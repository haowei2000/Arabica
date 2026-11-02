//! The runner side of LRP: serves confined `LocalRunner` execution to one
//! authenticated loop per connection.
//!
//! The host's [`LocalRunnerPolicy`] is authoritative. A lease may narrow it
//! (read-only mode, a tool subset) and can never widen it. The policy may be
//! replaced while the host runs ([`RunnerHost::set_policy`]); every open
//! lease is re-derived from the new policy and loops that asked for it are
//! sent `tools.changed`.

use std::collections::{BTreeMap, HashMap, HashSet};
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use arabica_protocol::lrp::{
    CallCancel, CallCancelAck, CallCompleted, CallFailed, CallOutput, CallRef, CallStart, Capacity,
    FEATURE_TOOLS_CHANGED, Frame, Hello, LRP_SUBPROTOCOL, LRP_VERSION, LeaseMode, LeaseOpen,
    LeaseOpened, LrpError, LrpErrorCode, Message, OutputStream, RemoteTool, SHELL_CLASSIFIER,
    ToolClassification, ToolsChanged,
};
use arabica_protocol::{RunId, ToolInteractionKind, WorkspaceId};
use arabica_runner::{
    LocalRunner, LocalRunnerPolicy, LocalTool, RunnerEnvironment, RunnerError, RunnerOutput,
    ToolExecutionRequest,
};
use futures_util::{SinkExt, StreamExt};
use tokio::io::{AsyncRead, AsyncWrite};
use tokio::net::TcpListener;
use tokio::sync::{Semaphore, mpsc, watch};
use tokio_rustls::TlsAcceptor;
use tokio_tungstenite::tungstenite::handshake::server::{ErrorResponse, Request, Response};
use tokio_tungstenite::tungstenite::http::{HeaderValue, StatusCode, header};
use tokio_tungstenite::tungstenite::{self, Message as WsMessage};

use crate::{HANDSHAKE_TIMEOUT, decode, encode, offers_subprotocol, ws_config};

/// Longest deadline a loop may ask for one call.
const MAX_CALL_DEADLINE: Duration = Duration::from_secs(24 * 60 * 60);

/// Static configuration for one runner process.
#[derive(Clone, Debug)]
pub struct RunnerHostConfig {
    pub runner_id: String,
    pub labels: BTreeMap<String, String>,
    /// The initial policy; see [`RunnerHost::set_policy`].
    pub policy: LocalRunnerPolicy,
    /// Each workspace a loop may lease, mapped to its local root.
    pub workspaces: BTreeMap<WorkspaceId, PathBuf>,
    pub max_concurrent_calls: u32,
    pub heartbeat_interval: Duration,
}

/// A runner process: configuration plus the capacity and current policy
/// shared by every connection it serves.
#[derive(Debug)]
pub struct RunnerHost {
    config: RunnerHostConfig,
    capacity: Arc<Semaphore>,
    policy: watch::Sender<LocalRunnerPolicy>,
}

impl RunnerHost {
    pub fn new(config: RunnerHostConfig) -> Result<Self, RunnerError> {
        config.policy.validate()?;
        if config.runner_id.trim().is_empty() {
            return Err(RunnerError::new("runner_id must not be empty"));
        }
        if config.workspaces.is_empty() {
            return Err(RunnerError::new(
                "a runner must serve at least one workspace",
            ));
        }
        for (workspace, root) in &config.workspaces {
            if !root.is_dir() {
                return Err(RunnerError::new(format!(
                    "workspace {workspace} root is not a directory: {}",
                    root.display()
                )));
            }
        }
        if config.max_concurrent_calls == 0 {
            return Err(RunnerError::new("max_concurrent_calls must be positive"));
        }
        if config.heartbeat_interval.is_zero() {
            return Err(RunnerError::new("heartbeat_interval must be positive"));
        }
        let permits = usize::try_from(config.max_concurrent_calls).unwrap_or(usize::MAX);
        Ok(Self {
            capacity: Arc::new(Semaphore::new(permits)),
            policy: watch::Sender::new(config.policy.clone()),
            config,
        })
    }

    pub fn config(&self) -> &RunnerHostConfig {
        &self.config
    }

    /// The policy new leases and calls are checked against.
    pub fn policy(&self) -> LocalRunnerPolicy {
        self.policy.borrow().clone()
    }

    /// Replace the policy. Every open lease is re-derived from it with its
    /// original mode and tool request, and loops that listed
    /// `tools.changed` in their welcome are told. Calls already accepted
    /// finish under the policy they started with.
    pub fn set_policy(&self, policy: LocalRunnerPolicy) -> Result<(), RunnerError> {
        policy.validate()?;
        self.policy.send_replace(policy);
        Ok(())
    }

    /// The `hello` this runner sends on every connection.
    pub fn hello(&self) -> Hello {
        self.hello_for(&self.policy.borrow())
    }

    fn hello_for(&self, policy: &LocalRunnerPolicy) -> Hello {
        Hello {
            runner_id: self.config.runner_id.clone(),
            versions: vec![LRP_VERSION.to_owned()],
            runner_version: env!("CARGO_PKG_VERSION").to_owned(),
            labels: self.config.labels.clone(),
            capacity: Capacity {
                max_concurrent_calls: self.config.max_concurrent_calls,
            },
            workspaces: self.config.workspaces.keys().cloned().collect(),
            tools: advertised_tools(policy),
        }
    }

    /// Accept TLS connections forever, serving each on its own task.
    /// `report` receives every connection that ends with an error.
    pub async fn serve(
        self: Arc<Self>,
        listener: TcpListener,
        acceptor: TlsAcceptor,
        report: impl Fn(RunnerError) + Clone + Send + Sync + 'static,
    ) -> std::io::Result<()> {
        loop {
            let (tcp, peer) = listener.accept().await?;
            let _ = tcp.set_nodelay(true);
            let host = Arc::clone(&self);
            let acceptor = acceptor.clone();
            let report = report.clone();
            tokio::spawn(async move {
                let served =
                    match tokio::time::timeout(HANDSHAKE_TIMEOUT, acceptor.accept(tcp)).await {
                        Ok(Ok(tls)) => host.serve_connection(tls).await,
                        Ok(Err(error)) => Err(RunnerError::new(format!("tls handshake: {error}"))),
                        Err(_) => Err(RunnerError::new("tls handshake timed out")),
                    };
                if let Err(error) = served {
                    report(RunnerError::new(format!("{peer}: {error}")));
                }
            });
        }
    }

    /// Serve one already-authenticated stream until the loop disconnects.
    /// Every call still running when the connection ends is aborted.
    pub async fn serve_connection<S>(self: &Arc<Self>, stream: S) -> Result<(), RunnerError>
    where
        S: AsyncRead + AsyncWrite + Unpin + Send + 'static,
    {
        let ws = tokio_tungstenite::accept_hdr_async_with_config(
            stream,
            negotiate_subprotocol,
            Some(ws_config()),
        )
        .await
        .map_err(|error| RunnerError::new(format!("websocket handshake: {error}")))?;
        let (mut sink, mut stream) = ws.split();

        // Subscribed before hello is built, so any later change is seen.
        let mut policy = self.policy.subscribe();
        let hello = self.hello_for(&policy.borrow_and_update());
        // The handshake is strictly ordered, so it runs before the writer.
        sink.send(encode(&Frame::request(
            "hello",
            None,
            Message::Hello(hello),
        ))?)
        .await
        .map_err(ws_error)?;
        let welcome = match tokio::time::timeout(HANDSHAKE_TIMEOUT, next_frame(&mut stream)).await {
            Ok(Ok(Some(frame))) => frame,
            Ok(Ok(None)) => return Err(RunnerError::new("loop closed during handshake")),
            Ok(Err(error)) => return Err(error),
            Err(_) => return Err(RunnerError::new("loop sent no welcome")),
        };
        let (heartbeat_timeout, tools_changed) = match (&welcome.reply_to, welcome.message) {
            (Some(reply_to), Message::Welcome(welcome))
                if reply_to == "hello" && welcome.version == LRP_VERSION =>
            {
                (
                    Duration::from_millis(welcome.heartbeat_timeout_ms.max(1)),
                    welcome
                        .features
                        .iter()
                        .any(|feature| feature == FEATURE_TOOLS_CHANGED),
                )
            }
            _ => {
                let rejection = encode(&Frame::notification(Message::Error(LrpError::new(
                    LrpErrorCode::VersionUnsupported,
                    "expected a welcome for lrp.v1",
                ))))?;
                let _ = sink.send(rejection).await;
                return Err(RunnerError::new("loop did not welcome lrp.v1"));
            }
        };

        let (outbound, outbound_rx) = mpsc::unbounded_channel();
        let writer = tokio::spawn(write_frames(
            sink,
            outbound_rx,
            self.config.heartbeat_interval,
        ));
        let mut connection = Connection {
            host: Arc::clone(self),
            outbound,
            leases: HashMap::new(),
            active: Arc::new(Mutex::new(HashMap::new())),
            last_seq: 0,
            tools_changed,
        };
        let outcome = connection
            .run(&mut stream, &mut policy, heartbeat_timeout)
            .await;
        connection.abort_all();
        drop(connection);
        let _ = writer.await;
        outcome
    }
}

/// The tools a policy advertises, with how the loop must classify them.
pub fn advertised_tools(policy: &LocalRunnerPolicy) -> Vec<RemoteTool> {
    arabica_runner::tool_definitions(policy)
        .into_iter()
        .map(|definition| {
            let classification = match policy
                .tools
                .iter()
                .find(|tool| tool.name() == definition.name)
            {
                Some(LocalTool::Shell) => ToolClassification::Dynamic {
                    classifier: SHELL_CLASSIFIER.to_owned(),
                },
                Some(tool) => ToolClassification::Static {
                    interaction: tool.interaction(),
                },
                None => ToolClassification::Static {
                    interaction: ToolInteractionKind::Generic,
                },
            };
            RemoteTool {
                definition,
                classification,
            }
        })
        .collect()
}

#[allow(clippy::result_large_err)]
fn negotiate_subprotocol(
    request: &Request,
    mut response: Response,
) -> Result<Response, ErrorResponse> {
    if !offers_subprotocol(request.headers()) {
        let mut rejection = ErrorResponse::new(Some(format!(
            "the {LRP_SUBPROTOCOL} subprotocol is required"
        )));
        *rejection.status_mut() = StatusCode::BAD_REQUEST;
        return Err(rejection);
    }
    response.headers_mut().insert(
        header::SEC_WEBSOCKET_PROTOCOL,
        HeaderValue::from_static(LRP_SUBPROTOCOL),
    );
    Ok(response)
}

struct Lease {
    root: PathBuf,
    /// What the loop asked for, kept to re-derive `policy` when the host's
    /// policy changes.
    mode: LeaseMode,
    requested: Option<Vec<String>>,
    policy: LocalRunnerPolicy,
    seen_calls: HashSet<String>,
}

/// Narrow the host policy to what one lease asked for.
fn lease_policy(
    host: &LocalRunnerPolicy,
    mode: LeaseMode,
    requested: Option<&[String]>,
) -> LocalRunnerPolicy {
    let mut policy = host.clone();
    if mode == LeaseMode::ReadOnly {
        policy
            .tools
            .retain(|tool| tool.interaction() == ToolInteractionKind::Inspection);
    }
    if let Some(requested) = requested {
        policy
            .tools
            .retain(|tool| requested.iter().any(|name| name == tool.name()));
    }
    policy
}

fn tool_names(policy: &LocalRunnerPolicy) -> Vec<String> {
    policy
        .tools
        .iter()
        .map(|tool| tool.name().to_owned())
        .collect()
}

struct ActiveCall {
    lease_id: String,
    run_id: RunId,
    abort: tokio::task::AbortHandle,
}

/// Calls in flight, keyed by `call_id`. Whoever removes an entry sends that
/// call's single terminal message, so a call that finishes while it is being
/// cancelled still reports exactly once.
type ActiveCalls = Arc<Mutex<HashMap<String, ActiveCall>>>;

struct Connection {
    host: Arc<RunnerHost>,
    outbound: mpsc::UnboundedSender<Frame>,
    leases: HashMap<String, Lease>,
    active: ActiveCalls,
    last_seq: u64,
    /// Whether the loop asked for `tools.changed`.
    tools_changed: bool,
}

impl Connection {
    async fn run<St>(
        &mut self,
        stream: &mut St,
        policy: &mut watch::Receiver<LocalRunnerPolicy>,
        heartbeat_timeout: Duration,
    ) -> Result<(), RunnerError>
    where
        St: futures_util::Stream<Item = Result<WsMessage, tungstenite::Error>> + Unpin,
    {
        let mut silent_until = tokio::time::Instant::now() + heartbeat_timeout;
        loop {
            let next = tokio::select! {
                next = tokio::time::timeout_at(silent_until, next_frame(stream)) => next,
                Ok(()) = policy.changed() => {
                    let policy = policy.borrow_and_update().clone();
                    self.apply_policy(&policy);
                    continue;
                }
            };
            silent_until = tokio::time::Instant::now() + heartbeat_timeout;
            let frame = match next {
                Ok(Ok(Some(frame))) => frame,
                Ok(Ok(None)) => return Ok(()),
                Ok(Err(error)) => {
                    self.send_error(None, LrpErrorCode::ProtocolError, error.to_string());
                    return Err(error);
                }
                Err(_) => return Err(RunnerError::new("loop heartbeat timed out")),
            };
            if let Err(error) = self.handle(frame) {
                return Err(RunnerError::new(error.to_string()));
            }
        }
    }

    /// Handle one frame. An `Err` is a protocol violation that ends the
    /// connection after it has been reported to the loop.
    fn handle(&mut self, frame: Frame) -> Result<(), LrpError> {
        if frame.v != LRP_VERSION {
            return self.violation(frame.id, format!("unsupported frame version {}", frame.v));
        }
        let Some(id) = frame.id else {
            return match frame.message {
                Message::Heartbeat => Ok(()),
                // Replies to runner requests are not used after the handshake.
                Message::Error(_) if frame.reply_to.is_some() => Ok(()),
                other => self.violation(None, format!("unexpected notification {}", kind(&other))),
            };
        };
        match frame.seq {
            Some(seq) if seq > self.last_seq => self.last_seq = seq,
            _ => return self.violation(Some(id), "request seq must strictly increase"),
        }
        match frame.message {
            Message::LeaseOpen(open) => self.open_lease(id, open),
            Message::LeaseClose(close) => {
                self.close_lease(&close.lease_id);
                self.reply(id, Message::LeaseClosed(close));
            }
            Message::CallStart(start) => self.start_call(id, start),
            Message::CallCancel(cancel) => {
                let cancelled = self.cancel(&cancel);
                self.reply(id, Message::CallCancelAck(CallCancelAck { cancelled }));
            }
            other => {
                return self.violation(Some(id), format!("unexpected request {}", kind(&other)));
            }
        }
        Ok(())
    }

    fn open_lease(&mut self, id: String, open: LeaseOpen) {
        if self.leases.contains_key(&open.lease_id) {
            return self.send_error(
                Some(id),
                LrpErrorCode::ProtocolError,
                "lease_id already open",
            );
        }
        let Some(root) = self.host.config.workspaces.get(&open.workspace_id) else {
            return self.send_error(
                Some(id),
                LrpErrorCode::WorkspaceUnknown,
                format!("this runner does not serve workspace {}", open.workspace_id),
            );
        };
        let policy = lease_policy(&self.host.policy.borrow(), open.mode, open.tools.as_deref());
        let tools = tool_names(&policy);
        self.leases.insert(
            open.lease_id.clone(),
            Lease {
                root: root.clone(),
                mode: open.mode,
                requested: open.tools,
                policy,
                seen_calls: HashSet::new(),
            },
        );
        self.reply(
            id,
            Message::LeaseOpened(LeaseOpened {
                lease_id: open.lease_id,
                tools,
            }),
        );
    }

    /// Re-derive every lease from a new host policy and, if the loop asked,
    /// tell it. Accepted calls keep the runner they were admitted with.
    fn apply_policy(&mut self, policy: &LocalRunnerPolicy) {
        let mut leases = BTreeMap::new();
        for (lease_id, lease) in &mut self.leases {
            lease.policy = lease_policy(policy, lease.mode, lease.requested.as_deref());
            leases.insert(lease_id.clone(), tool_names(&lease.policy));
        }
        if self.tools_changed {
            let _ = self
                .outbound
                .send(Frame::notification(Message::ToolsChanged(ToolsChanged {
                    tools: advertised_tools(policy),
                    leases,
                })));
        }
    }

    fn close_lease(&mut self, lease_id: &str) {
        if self.leases.remove(lease_id).is_none() {
            return;
        }
        let closing: Vec<String> = self
            .active
            .lock()
            .expect("active calls lock poisoned")
            .iter()
            .filter(|(_, call)| call.lease_id == lease_id)
            .map(|(call_id, _)| call_id.clone())
            .collect();
        for call_id in closing {
            self.finish_cancelled(&call_id);
        }
    }

    fn start_call(&mut self, id: String, start: CallStart) {
        let call_id = start.call.call_id.clone();
        let admitted = admit(
            self.leases.get_mut(&start.lease_id),
            &self.host.capacity,
            &start,
        );
        let (runner, permit) = match admitted {
            Ok(admitted) => admitted,
            Err(error) => return self.send_error(Some(id), error.code, error.message),
        };
        let deadline = Duration::from_millis(start.deadline_ms);
        let active = Arc::clone(&self.active);
        let outbound = self.outbound.clone();
        let request = ToolExecutionRequest {
            run_id: start.run_id.clone(),
            call: start.call,
        };
        // Accepted strictly before any output of this call is queued.
        self.reply(
            id,
            Message::CallAccepted(CallRef {
                call_id: call_id.clone(),
            }),
        );
        let mut active_calls = self.active.lock().expect("active calls lock poisoned");
        let task = tokio::spawn({
            let call_id = call_id.clone();
            async move {
                let _permit = permit;
                let terminal = execute(runner, request, deadline).await;
                // Removing the entry claims the right to report.
                let mut active = active.lock().expect("active calls lock poisoned");
                if active.remove(&call_id).is_some() {
                    for frame in terminal {
                        let _ = outbound.send(frame);
                    }
                }
            }
        });
        // Inserted under the lock the task needs before it can finish, so
        // the task always finds its own entry.
        active_calls.insert(
            call_id,
            ActiveCall {
                lease_id: start.lease_id,
                run_id: start.run_id,
                abort: task.abort_handle(),
            },
        );
    }

    fn cancel(&mut self, cancel: &CallCancel) -> bool {
        let matching: Vec<String> = self
            .active
            .lock()
            .expect("active calls lock poisoned")
            .iter()
            .filter(|(call_id, call)| {
                call.run_id == cancel.run_id
                    && cancel
                        .call_id
                        .as_ref()
                        .is_none_or(|wanted| wanted == *call_id)
            })
            .map(|(call_id, _)| call_id.clone())
            .collect();
        let mut cancelled = false;
        for call_id in matching {
            cancelled |= self.finish_cancelled(&call_id);
        }
        cancelled
    }

    /// Abort one call and report it cancelled, unless it already finished.
    fn finish_cancelled(&self, call_id: &str) -> bool {
        let removed = self
            .active
            .lock()
            .expect("active calls lock poisoned")
            .remove(call_id);
        let Some(call) = removed else {
            return false;
        };
        // Dropping the call's future kills its process group.
        call.abort.abort();
        let _ = self
            .outbound
            .send(Frame::notification(Message::CallFailed(CallFailed {
                call_id: call_id.to_owned(),
                error: LrpError::new(LrpErrorCode::Cancelled, "call was cancelled"),
            })));
        true
    }

    fn abort_all(&self) {
        for (_, call) in self
            .active
            .lock()
            .expect("active calls lock poisoned")
            .drain()
        {
            call.abort.abort();
        }
    }

    fn reply(&self, id: String, message: Message) {
        let _ = self.outbound.send(Frame::reply(id, message));
    }

    fn send_error(&self, id: Option<String>, code: LrpErrorCode, message: impl Into<String>) {
        let message = Message::Error(LrpError::new(code, message));
        let frame = match id {
            Some(id) => Frame::reply(id, message),
            None => Frame::notification(message),
        };
        let _ = self.outbound.send(frame);
    }

    fn violation(&self, id: Option<String>, message: impl Into<String>) -> Result<(), LrpError> {
        let error = LrpError::new(LrpErrorCode::ProtocolError, message);
        self.send_error(id, error.code, error.message.clone());
        Err(error)
    }
}

/// Check one `call.start` against its lease and the runner's capacity, and
/// build the confined runner it executes on.
fn admit(
    lease: Option<&mut Lease>,
    capacity: &Arc<Semaphore>,
    start: &CallStart,
) -> Result<(LocalRunner, tokio::sync::OwnedSemaphorePermit), LrpError> {
    let Some(lease) = lease else {
        return Err(LrpError::new(
            LrpErrorCode::ProtocolError,
            "unknown lease_id",
        ));
    };
    if !lease
        .policy
        .tools
        .iter()
        .any(|tool| tool.name() == start.call.name)
    {
        return Err(LrpError::new(
            LrpErrorCode::ToolUnavailable,
            format!("tool {} is not offered on this lease", start.call.name),
        ));
    }
    if start.deadline_ms == 0 || Duration::from_millis(start.deadline_ms) > MAX_CALL_DEADLINE {
        return Err(LrpError::new(
            LrpErrorCode::ProtocolError,
            "deadline_ms out of range",
        ));
    }
    if lease.seen_calls.contains(&start.call.call_id) {
        return Err(LrpError::new(
            LrpErrorCode::ProtocolError,
            "duplicate call_id",
        ));
    }
    let permit = Arc::clone(capacity).try_acquire_owned().map_err(|_| {
        LrpError::new(
            LrpErrorCode::CapacityExhausted,
            "runner is at max_concurrent_calls",
        )
    })?;
    lease.seen_calls.insert(start.call.call_id.clone());
    // A fresh runner per call: calls on one lease run concurrently, and
    // aborting one cannot disturb another.
    let runner = LocalRunner::with_policy(lease.root.clone(), lease.policy.clone());
    Ok((runner, permit))
}

/// Run one call and build its output and terminal frames.
async fn execute(
    mut runner: LocalRunner,
    request: ToolExecutionRequest,
    deadline: Duration,
) -> Vec<Frame> {
    let call_id = request.call.call_id.clone();
    match tokio::time::timeout(deadline, runner.execute(request)).await {
        Ok(Ok(executed)) => {
            let mut frames: Vec<Frame> = executed
                .output
                .into_iter()
                .zip(0_u64..)
                .map(|(output, chunk)| {
                    let (stream, text) = match output {
                        RunnerOutput::Stdout(text) => (OutputStream::Stdout, text),
                        RunnerOutput::Stderr(text) => (OutputStream::Stderr, text),
                    };
                    Frame::notification(Message::CallOutput(CallOutput {
                        call_id: call_id.clone(),
                        chunk,
                        stream,
                        text,
                    }))
                })
                .collect();
            frames.push(Frame::notification(Message::CallCompleted(CallCompleted {
                call_id,
                result: executed.result,
            })));
            frames
        }
        Ok(Err(error)) => vec![failed(call_id, LrpErrorCode::Internal, error.to_string())],
        Err(_) => vec![failed(
            call_id,
            LrpErrorCode::DeadlineExceeded,
            format!("call exceeded its {} ms deadline", deadline.as_millis()),
        )],
    }
}

fn failed(call_id: String, code: LrpErrorCode, message: String) -> Frame {
    Frame::notification(Message::CallFailed(CallFailed {
        call_id,
        error: LrpError::new(code, message),
    }))
}

/// Serialize queued frames and keep the loop's heartbeat timer satisfied.
/// Ends with a close frame once every sender is gone.
async fn write_frames<Si>(
    mut sink: Si,
    mut outbound: mpsc::UnboundedReceiver<Frame>,
    heartbeat_interval: Duration,
) where
    Si: futures_util::Sink<WsMessage, Error = tungstenite::Error> + Unpin,
{
    let mut heartbeat = tokio::time::interval(heartbeat_interval);
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

async fn next_frame<St>(stream: &mut St) -> Result<Option<Frame>, RunnerError>
where
    St: futures_util::Stream<Item = Result<WsMessage, tungstenite::Error>> + Unpin,
{
    loop {
        match stream.next().await {
            None | Some(Ok(WsMessage::Close(_))) => return Ok(None),
            Some(Ok(WsMessage::Text(text))) => return decode(text.as_str()).map(Some),
            Some(Ok(WsMessage::Binary(_))) => {
                return Err(RunnerError::new("binary frames are not part of lrp.v1"));
            }
            Some(Ok(_)) => {}
            Some(Err(error)) => return Err(ws_error(error)),
        }
    }
}

fn ws_error(error: tungstenite::Error) -> RunnerError {
    RunnerError::new(format!("websocket: {error}"))
}

fn kind(message: &Message) -> String {
    serde_json::to_value(message)
        .ok()
        .and_then(|value| {
            value
                .get("type")
                .and_then(|kind| kind.as_str())
                .map(str::to_owned)
        })
        .unwrap_or_else(|| "message".to_owned())
}
