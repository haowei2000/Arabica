//! The loop side of LRP: a [`RunnerEnvironment`] whose tools execute on a
//! remote runner over one long-lived, mutually authenticated WebSocket.
//!
//! The leased tool list can change while connected: the runner sends
//! `tools.changed` and [`RemoteRunner::tools_snapshot`] reflects it, with a
//! generation the host compares to know when to re-advertise tools.

use std::collections::HashMap;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use arabica_model::{ContentBlock, ToolCallItem, ToolDefinition, ToolResultItem};
use arabica_protocol::lrp::{
    CallCancel, CallStart, FEATURE_TOOLS_CHANGED, Frame, Hello, LRP_SUBPROTOCOL, LRP_VERSION,
    LeaseMode, LeaseOpen, LrpError, LrpErrorCode, Message, OutputStream, RemoteTool,
    SHELL_CLASSIFIER, ToolClassification, Welcome,
};
use arabica_protocol::{RunId, SessionId, ToolInteractionKind, WorkspaceId};
use arabica_runner::{
    RunnerEnvironment, RunnerError, RunnerOutput, ToolExecutionRequest, ToolExecutionResult,
    classify_shell_interaction,
};
use futures_util::{SinkExt, StreamExt};
use rustls::ClientConfig;
use rustls_pki_types::ServerName;
use tokio::sync::{mpsc, oneshot};
use tokio_rustls::TlsConnector;
use tokio_tungstenite::tungstenite::client::IntoClientRequest;
use tokio_tungstenite::tungstenite::http::{HeaderValue, header};
use tokio_tungstenite::tungstenite::{self, Message as WsMessage};

use crate::{HANDSHAKE_TIMEOUT, decode, encode, offers_subprotocol, ws_config};

const REQUEST_TIMEOUT: Duration = Duration::from_secs(30);
const HEARTBEAT_INTERVAL: Duration = Duration::from_secs(15);
const HEARTBEAT_TIMEOUT: Duration = Duration::from_secs(45);

/// Everything the loop needs to reach and lease one remote runner.
#[derive(Clone, Debug)]
pub struct RemoteRunnerConfig {
    /// `wss://host:port[/path]`. Plaintext `ws://` is refused.
    pub url: url::Url,
    /// TLS name to verify instead of the URL host.
    pub server_name: Option<String>,
    pub tls: Arc<ClientConfig>,
    /// When set, the runner's `hello` must name exactly this runner.
    pub expected_runner_id: Option<String>,
    pub workspace_id: WorkspaceId,
    pub session_id: Option<SessionId>,
    pub mode: LeaseMode,
    /// Tools to lease; `None` takes every tool the runner offers in `mode`.
    pub tools: Option<Vec<String>>,
    /// Deadline sent with every call.
    pub call_deadline: Duration,
}

/// A leased workspace on one remote runner.
pub struct RemoteRunner {
    link: Arc<Link>,
    runner_id: String,
    lease_id: String,
    call_deadline: Duration,
}

impl std::fmt::Debug for RemoteRunner {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("RemoteRunner")
            .field("runner_id", &self.runner_id)
            .field("lease_id", &self.lease_id)
            .field("tools", &self.link.routes().tools.definitions.len())
            .finish_non_exhaustive()
    }
}

/// The tools this lease currently offers.
#[derive(Default)]
struct LeaseTools {
    /// Bumped on every `tools.changed`; 0 is the list from `lease.opened`.
    generation: u64,
    classifications: HashMap<String, ToolClassification>,
    definitions: Vec<ToolDefinition>,
}

impl LeaseTools {
    /// The tools of `offered` that `leased` names, in the runner's order.
    fn new(generation: u64, offered: Vec<RemoteTool>, leased: &[String]) -> Self {
        let leased: Vec<RemoteTool> = offered
            .into_iter()
            .filter(|tool| leased.contains(&tool.definition.name))
            .collect();
        Self {
            generation,
            definitions: leased.iter().map(|tool| tool.definition.clone()).collect(),
            classifications: leased
                .into_iter()
                .map(|tool| (tool.definition.name, tool.classification))
                .collect(),
        }
    }
}

impl RemoteRunner {
    /// Connect, authenticate, complete the handshake, and open one lease.
    pub async fn connect(config: RemoteRunnerConfig) -> Result<Self, RunnerError> {
        if config.url.scheme() != "wss" {
            return Err(RunnerError::new("remote runner url must use wss://"));
        }
        if config.call_deadline.is_zero() {
            return Err(RunnerError::new("call_deadline must be positive"));
        }
        let host = config
            .url
            .host_str()
            .ok_or_else(|| RunnerError::new("remote runner url has no host"))?
            .trim_start_matches('[')
            .trim_end_matches(']')
            .to_owned();
        let port = config.url.port().unwrap_or(443);
        let server_name = ServerName::try_from(config.server_name.clone().unwrap_or(host.clone()))
            .map_err(|_| RunnerError::new("invalid remote runner server name"))?;

        let ws = tokio::time::timeout(HANDSHAKE_TIMEOUT, async {
            let tcp = tokio::net::TcpStream::connect((host.as_str(), port))
                .await
                .map_err(|error| RunnerError::new(format!("connect {host}:{port}: {error}")))?;
            let _ = tcp.set_nodelay(true);
            let tls = TlsConnector::from(Arc::clone(&config.tls))
                .connect(server_name, tcp)
                .await
                .map_err(|error| RunnerError::new(format!("tls handshake: {error}")))?;
            let mut request = config
                .url
                .as_str()
                .into_client_request()
                .map_err(ws_error)?;
            request.headers_mut().insert(
                header::SEC_WEBSOCKET_PROTOCOL,
                HeaderValue::from_static(LRP_SUBPROTOCOL),
            );
            let (ws, response) =
                tokio_tungstenite::client_async_with_config(request, tls, Some(ws_config()))
                    .await
                    .map_err(ws_error)?;
            if !offers_subprotocol(response.headers()) {
                return Err(RunnerError::new(format!(
                    "runner did not accept the {LRP_SUBPROTOCOL} subprotocol"
                )));
            }
            Ok(ws)
        })
        .await
        .map_err(|_| RunnerError::new("remote runner handshake timed out"))??;
        let (mut sink, mut stream) = ws.split();

        let hello = tokio::time::timeout(HANDSHAKE_TIMEOUT, read_hello(&mut stream))
            .await
            .map_err(|_| RunnerError::new("runner sent no hello"))??;
        if let Err(error) = check_hello(&hello.1, &config) {
            let _ = sink
                .send(encode(&Frame::reply(
                    hello.0,
                    Message::Error(error.clone()),
                ))?)
                .await;
            return Err(RunnerError::new(format!("remote runner rejected: {error}")));
        }
        let (hello_id, hello) = hello;
        sink.send(encode(&Frame::reply(
            hello_id,
            Message::Welcome(Welcome {
                version: LRP_VERSION.to_owned(),
                connection_id: uuid::Uuid::now_v7().to_string(),
                heartbeat_interval_ms: duration_ms(HEARTBEAT_INTERVAL),
                heartbeat_timeout_ms: duration_ms(HEARTBEAT_TIMEOUT),
                features: vec![FEATURE_TOOLS_CHANGED.to_owned()],
            }),
        ))?)
        .await
        .map_err(ws_error)?;

        let lease_id = uuid::Uuid::now_v7().to_string();
        let (outbound, outbound_rx) = mpsc::unbounded_channel();
        let routes = Arc::new(Mutex::new(Routes {
            lease_id: lease_id.clone(),
            ..Routes::default()
        }));
        tokio::spawn(write_frames(sink, outbound_rx));
        tokio::spawn(read_frames(stream, Arc::clone(&routes)));
        let link = Arc::new(Link {
            outbound,
            next_seq: AtomicU64::new(1),
            routes,
        });

        let opened = link
            .request(Message::LeaseOpen(LeaseOpen {
                lease_id: lease_id.clone(),
                session_id: config.session_id.clone(),
                workspace_id: config.workspace_id.clone(),
                mode: config.mode,
                tools: config.tools.clone(),
            }))
            .await?;
        let Message::LeaseOpened(opened) = opened else {
            return Err(RunnerError::new("runner answered lease.open unexpectedly"));
        };
        {
            let mut routes = link.routes();
            // A tools.changed read right after lease.opened is newer.
            if routes.tools.generation == 0 {
                routes.tools = LeaseTools::new(0, hello.tools, &opened.tools);
            }
        }
        Ok(Self {
            link,
            runner_id: hello.runner_id,
            lease_id,
            call_deadline: config.call_deadline,
        })
    }

    pub fn runner_id(&self) -> &str {
        &self.runner_id
    }

    /// The tools this lease offers, to advertise to the model.
    pub fn definitions(&self) -> Vec<ToolDefinition> {
        self.tools_snapshot().1
    }

    /// The current tool list with its generation, which increases every
    /// time the runner changes the list.
    pub fn tools_snapshot(&self) -> (u64, Vec<ToolDefinition>) {
        let routes = self.link.routes();
        (routes.tools.generation, routes.tools.definitions.clone())
    }

    pub fn contains(&self, name: &str) -> bool {
        self.link.routes().tools.classifications.contains_key(name)
    }

    /// Whether the connection to the runner is still up.
    pub fn is_connected(&self) -> bool {
        !self.link.routes().closed
    }
}

impl RunnerEnvironment for RemoteRunner {
    fn classify(&self, call: &ToolCallItem) -> ToolInteractionKind {
        let classification = self
            .link
            .routes()
            .tools
            .classifications
            .get(&call.name)
            .cloned();
        match &classification {
            Some(ToolClassification::Static { interaction }) => *interaction,
            Some(ToolClassification::Dynamic { classifier }) if classifier == SHELL_CLASSIFIER => {
                call.arguments
                    .get("command")
                    .and_then(serde_json::Value::as_str)
                    .map_or(ToolInteractionKind::Generic, classify_shell_interaction)
            }
            _ => ToolInteractionKind::Generic,
        }
    }

    async fn execute(
        &mut self,
        request: ToolExecutionRequest,
    ) -> Result<ToolExecutionResult, RunnerError> {
        let ToolExecutionRequest { run_id, mut call } = request;
        let call_id = call.call_id.clone();
        let name = call.name.clone();
        // Provider continuation state is the loop's business, not the runner's.
        call.provider_state = None;
        let mut events = self.link.register_call(&call_id)?;
        let mut guard = CallGuard {
            link: Arc::clone(&self.link),
            run_id: run_id.clone(),
            call_id: call_id.clone(),
            finished: false,
        };

        // A full runner rejects before recording the call, so the same
        // call_id may be offered again until a slot frees or the deadline
        // passes: the loop queues instead of failing the run.
        let queued_until = tokio::time::Instant::now() + self.call_deadline;
        let mut backoff = Duration::from_millis(25);
        let accepted = loop {
            let accepted = self
                .link
                .request(Message::CallStart(CallStart {
                    lease_id: self.lease_id.clone(),
                    run_id: run_id.clone(),
                    call: call.clone(),
                    deadline_ms: duration_ms(self.call_deadline),
                }))
                .await;
            match accepted {
                Err(RequestError::Rejected(error))
                    if error.code == LrpErrorCode::CapacityExhausted
                        && tokio::time::Instant::now() + backoff < queued_until =>
                {
                    tokio::time::sleep(backoff).await;
                    backoff = (backoff * 2).min(Duration::from_secs(1));
                }
                accepted => break accepted,
            }
        };
        match accepted {
            Ok(Message::CallAccepted(_)) => {}
            Ok(_) => {
                guard.finished = true;
                return Err(RunnerError::new("runner answered call.start unexpectedly"));
            }
            Err(error) => {
                // Rejected calls never started, so there is nothing to cancel.
                guard.finished = true;
                return match error {
                    RequestError::Rejected(error) => tool_failure(call_id, name, error),
                    RequestError::Transport(error) => Err(error),
                };
            }
        }

        let mut output = Vec::new();
        loop {
            match events.recv().await {
                Some(Message::CallOutput(chunk)) => {
                    if chunk.chunk != output.len() as u64 {
                        return Err(RunnerError::new("runner sent call output out of order"));
                    }
                    output.push(match chunk.stream {
                        OutputStream::Stdout => RunnerOutput::Stdout(chunk.text),
                        OutputStream::Stderr => RunnerOutput::Stderr(chunk.text),
                    });
                }
                Some(Message::CallCompleted(completed)) => {
                    guard.finished = true;
                    return Ok(ToolExecutionResult {
                        result: completed.result,
                        output,
                    });
                }
                Some(Message::CallFailed(failed)) => {
                    guard.finished = true;
                    return tool_failure(call_id, name, failed.error);
                }
                Some(_) => {}
                None => {
                    guard.finished = true;
                    return Err(RunnerError::new(
                        "runner_lost: connection to the remote runner ended during the call",
                    ));
                }
            }
        }
    }

    async fn cancel(&mut self, run_id: &RunId) -> Result<bool, RunnerError> {
        if !self.is_connected() {
            return Ok(false);
        }
        match self
            .link
            .request(Message::CallCancel(CallCancel {
                run_id: run_id.clone(),
                call_id: None,
            }))
            .await
        {
            Ok(Message::CallCancelAck(ack)) => Ok(ack.cancelled),
            Ok(_) => Err(RunnerError::new("runner answered call.cancel unexpectedly")),
            Err(RequestError::Rejected(error)) => Err(RunnerError::new(error.to_string())),
            Err(RequestError::Transport(error)) => Err(error),
        }
    }
}

/// Failures the model can act on become error results; the rest end the run.
fn tool_failure(
    call_id: String,
    name: String,
    error: LrpError,
) -> Result<ToolExecutionResult, RunnerError> {
    match error.code {
        LrpErrorCode::DeadlineExceeded
        | LrpErrorCode::ToolUnavailable
        | LrpErrorCode::CapacityExhausted => Ok(ToolExecutionResult {
            result: ToolResultItem {
                id: None,
                call_id,
                name: Some(name),
                content: vec![ContentBlock::text(format!(
                    "remote runner: {}",
                    error.message
                ))],
                is_error: true,
            },
            output: Vec::new(),
        }),
        _ => Err(RunnerError::new(format!("remote runner: {error}"))),
    }
}

/// Cancels a call on the runner when the runtime drops its `execute` future
/// before the call finished, which is how the runtime cancels a tool call.
struct CallGuard {
    link: Arc<Link>,
    run_id: RunId,
    call_id: String,
    finished: bool,
}

impl Drop for CallGuard {
    fn drop(&mut self) {
        self.link
            .routes
            .lock()
            .expect("routes lock poisoned")
            .calls
            .remove(&self.call_id);
        if !self.finished {
            // The ack is not awaited; its reply is dropped as unroutable.
            let _ = self.link.send(Message::CallCancel(CallCancel {
                run_id: self.run_id.clone(),
                call_id: Some(self.call_id.clone()),
            }));
        }
    }
}

#[derive(Default)]
struct Routes {
    pending: HashMap<String, oneshot::Sender<Message>>,
    calls: HashMap<String, mpsc::UnboundedSender<Message>>,
    closed: bool,
    /// This connection's one lease and its tools, which `tools.changed`
    /// replaces.
    lease_id: String,
    tools: LeaseTools,
}

struct Link {
    outbound: mpsc::UnboundedSender<Frame>,
    next_seq: AtomicU64,
    routes: Arc<Mutex<Routes>>,
}

enum RequestError {
    /// The runner answered with an error.
    Rejected(LrpError),
    /// No answer: the connection failed or timed out.
    Transport(RunnerError),
}

impl From<RequestError> for RunnerError {
    fn from(error: RequestError) -> Self {
        match error {
            RequestError::Rejected(error) => RunnerError::new(format!("remote runner: {error}")),
            RequestError::Transport(error) => error,
        }
    }
}

impl Link {
    fn routes(&self) -> std::sync::MutexGuard<'_, Routes> {
        self.routes.lock().expect("routes lock poisoned")
    }

    /// Queue a request without waiting for its reply.
    fn send(&self, message: Message) -> Result<String, RunnerError> {
        let seq = self.next_seq.fetch_add(1, Ordering::Relaxed);
        let id = format!("m-{seq}");
        self.outbound
            .send(Frame::request(id.clone(), Some(seq), message))
            .map_err(|_| lost())?;
        Ok(id)
    }

    async fn request(&self, message: Message) -> Result<Message, RequestError> {
        let (reply_tx, reply_rx) = oneshot::channel();
        {
            // Registered under the same lock the reader uses, before the
            // request is queued, so a fast reply cannot be missed.
            let mut routes = self.routes.lock().expect("routes lock poisoned");
            if routes.closed {
                return Err(RequestError::Transport(lost()));
            }
            let id = self.send(message).map_err(RequestError::Transport)?;
            routes.pending.insert(id, reply_tx);
        }
        match tokio::time::timeout(REQUEST_TIMEOUT, reply_rx).await {
            Ok(Ok(Message::Error(error))) => Err(RequestError::Rejected(error)),
            Ok(Ok(message)) => Ok(message),
            Ok(Err(_)) => Err(RequestError::Transport(lost())),
            Err(_) => Err(RequestError::Transport(RunnerError::new(
                "remote runner did not answer in time",
            ))),
        }
    }

    fn register_call(
        &self,
        call_id: &str,
    ) -> Result<mpsc::UnboundedReceiver<Message>, RunnerError> {
        let mut routes = self.routes.lock().expect("routes lock poisoned");
        if routes.closed {
            return Err(lost());
        }
        if routes.calls.contains_key(call_id) {
            return Err(RunnerError::new(format!(
                "call {call_id} is already running"
            )));
        }
        let (tx, rx) = mpsc::unbounded_channel();
        routes.calls.insert(call_id.to_owned(), tx);
        Ok(rx)
    }
}

fn lost() -> RunnerError {
    RunnerError::new("runner_lost: the remote runner connection is closed")
}

fn check_hello(hello: &Hello, config: &RemoteRunnerConfig) -> Result<(), LrpError> {
    if !hello.versions.iter().any(|version| version == LRP_VERSION) {
        return Err(LrpError::new(
            LrpErrorCode::VersionUnsupported,
            format!("this loop speaks only {LRP_VERSION}"),
        ));
    }
    if let Some(expected) = &config.expected_runner_id
        && &hello.runner_id != expected
    {
        return Err(LrpError::new(
            LrpErrorCode::Unauthenticated,
            format!("expected runner {expected}, got {}", hello.runner_id),
        ));
    }
    if !hello.workspaces.contains(&config.workspace_id) {
        return Err(LrpError::new(
            LrpErrorCode::WorkspaceUnknown,
            format!("runner does not serve workspace {}", config.workspace_id),
        ));
    }
    Ok(())
}

async fn read_hello<St>(stream: &mut St) -> Result<(String, Hello), RunnerError>
where
    St: futures_util::Stream<Item = Result<WsMessage, tungstenite::Error>> + Unpin,
{
    loop {
        match stream.next().await {
            Some(Ok(WsMessage::Text(text))) => {
                let frame = decode(text.as_str())?;
                return match (frame.id, frame.message) {
                    (Some(id), Message::Hello(hello)) if frame.v == LRP_VERSION => Ok((id, hello)),
                    _ => Err(RunnerError::new("runner did not open with hello")),
                };
            }
            Some(Ok(WsMessage::Ping(_) | WsMessage::Pong(_))) => {}
            Some(Ok(_)) | None => return Err(RunnerError::new("runner closed before hello")),
            Some(Err(error)) => return Err(ws_error(error)),
        }
    }
}

async fn write_frames<Si>(mut sink: Si, mut outbound: mpsc::UnboundedReceiver<Frame>)
where
    Si: futures_util::Sink<WsMessage, Error = tungstenite::Error> + Unpin,
{
    let mut heartbeat = tokio::time::interval(HEARTBEAT_INTERVAL);
    heartbeat.tick().await;
    loop {
        let frame = tokio::select! {
            frame = outbound.recv() => match frame {
                Some(frame) => frame,
                None => break,
            },
            _ = heartbeat.tick() => Frame::notification(Message::Heartbeat),
        };
        let Ok(message) = encode(&frame) else {
            continue;
        };
        if sink.send(message).await.is_err() {
            return;
        }
    }
    let _ = sink.close().await;
}

/// Route replies and call events until the connection ends, then fail
/// everything still waiting on it.
async fn read_frames<St>(mut stream: St, routes: Arc<Mutex<Routes>>)
where
    St: futures_util::Stream<Item = Result<WsMessage, tungstenite::Error>> + Unpin,
{
    loop {
        let message = match tokio::time::timeout(HEARTBEAT_TIMEOUT, stream.next()).await {
            Ok(Some(Ok(message))) => message,
            Ok(Some(Err(_)) | None) | Err(_) => break,
        };
        let text = match message {
            WsMessage::Text(text) => text,
            WsMessage::Close(_) | WsMessage::Binary(_) => break,
            _ => continue,
        };
        let Ok(frame) = decode(text.as_str()) else {
            break;
        };
        let mut routes = routes.lock().expect("routes lock poisoned");
        if let Some(reply_to) = frame.reply_to {
            if let Some(waiter) = routes.pending.remove(&reply_to) {
                let _ = waiter.send(frame.message);
            }
            continue;
        }
        let call_id = match frame.message {
            Message::ToolsChanged(changed) => {
                // A runner lists every open lease; one without ours leaves
                // this lease's tools alone.
                if let Some(leased) = changed.leases.get(&routes.lease_id) {
                    let generation = routes.tools.generation + 1;
                    routes.tools = LeaseTools::new(generation, changed.tools, leased);
                }
                continue;
            }
            Message::CallOutput(ref output) => output.call_id.clone(),
            Message::CallCompleted(ref completed) => completed.call_id.clone(),
            Message::CallFailed(ref failed) => failed.call_id.clone(),
            _ => continue,
        };
        let terminal = !matches!(frame.message, Message::CallOutput(_));
        if let Some(events) = routes.calls.get(&call_id) {
            let _ = events.send(frame.message);
        }
        if terminal {
            routes.calls.remove(&call_id);
        }
    }
    let mut routes = routes.lock().expect("routes lock poisoned");
    routes.closed = true;
    routes.pending.clear();
    routes.calls.clear();
}

fn ws_error(error: tungstenite::Error) -> RunnerError {
    RunnerError::new(format!("websocket: {error}"))
}

fn duration_ms(duration: Duration) -> u64 {
    u64::try_from(duration.as_millis()).unwrap_or(u64::MAX)
}
