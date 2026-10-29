//! End-to-end LRP conformance: a real `RunnerHost` on loopback, reached by
//! `RemoteRunner` over mutually authenticated TLS with a throwaway PKI.

use std::collections::BTreeMap;
use std::net::SocketAddr;
use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;

use arabica_model::{ContentBlock, ToolCallItem};
use arabica_protocol::lrp::LeaseMode;
use arabica_protocol::{RunId, ToolInteractionKind, WorkspaceId};
use arabica_runner::{
    LocalRunnerPolicy, LocalTool, RunnerEnvironment, RunnerOutput, ToolExecutionRequest,
};
use arabica_runner_remote::{
    RemoteRunner, RemoteRunnerConfig, RunnerHost, RunnerHostConfig, TlsIdentity,
};
use rcgen::{
    BasicConstraints, CertificateParams, CertifiedIssuer, ExtendedKeyUsagePurpose, IsCa, KeyPair,
    KeyUsagePurpose,
};
use serde_json::json;
use tokio::net::TcpListener;
use tokio_rustls::TlsAcceptor;

struct Pki {
    ca: CertifiedIssuer<'static, KeyPair>,
}

impl Pki {
    fn new() -> Self {
        let mut params = CertificateParams::new(Vec::<String>::new()).unwrap();
        params.is_ca = IsCa::Ca(BasicConstraints::Unconstrained);
        params.key_usages = vec![KeyUsagePurpose::KeyCertSign, KeyUsagePurpose::CrlSign];
        let ca = CertifiedIssuer::self_signed(params, KeyPair::generate().unwrap()).unwrap();
        Self { ca }
    }

    fn ca_pem(&self) -> Vec<u8> {
        self.ca.pem().into_bytes()
    }

    /// A leaf certificate for `names`, trusting `peer_ca_pem` for the peer.
    fn identity(&self, names: &[&str], peer_ca_pem: Vec<u8>) -> TlsIdentity {
        let mut params = CertificateParams::new(
            names
                .iter()
                .map(|name| (*name).to_owned())
                .collect::<Vec<_>>(),
        )
        .unwrap();
        params.extended_key_usages = vec![
            ExtendedKeyUsagePurpose::ServerAuth,
            ExtendedKeyUsagePurpose::ClientAuth,
        ];
        let key = KeyPair::generate().unwrap();
        let cert = params.signed_by(&key, &self.ca).unwrap();
        TlsIdentity {
            cert_chain_pem: cert.pem().into_bytes(),
            private_key_pem: key.serialize_pem().into_bytes(),
            peer_ca_pem,
        }
    }
}

fn workspace() -> PathBuf {
    let root = std::env::temp_dir().join(format!("arabica-lrp-{}", uuid::Uuid::now_v7()));
    std::fs::create_dir_all(&root).unwrap();
    std::fs::write(root.join("hello.txt"), "hello from the runner\n").unwrap();
    root
}

fn shell_policy() -> LocalRunnerPolicy {
    LocalRunnerPolicy::coding().with_tool(LocalTool::Shell)
}

async fn start_runner(
    pki: &Pki,
    root: PathBuf,
    policy: LocalRunnerPolicy,
    max_concurrent_calls: u32,
) -> SocketAddr {
    start_runner_host(pki, root, policy, max_concurrent_calls)
        .await
        .0
}

async fn start_runner_host(
    pki: &Pki,
    root: PathBuf,
    policy: LocalRunnerPolicy,
    max_concurrent_calls: u32,
) -> (SocketAddr, Arc<RunnerHost>) {
    let identity = pki.identity(&["127.0.0.1", "localhost"], pki.ca_pem());
    let host = Arc::new(
        RunnerHost::new(RunnerHostConfig {
            runner_id: "runner-a".to_owned(),
            labels: BTreeMap::from([("os".to_owned(), std::env::consts::OS.to_owned())]),
            policy,
            workspaces: BTreeMap::from([(WorkspaceId::new("ws-1"), root)]),
            max_concurrent_calls,
            heartbeat_interval: Duration::from_secs(5),
        })
        .unwrap(),
    );
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let acceptor = TlsAcceptor::from(identity.server_config().unwrap());
    tokio::spawn(Arc::clone(&host).serve(listener, acceptor, |_| {}));
    (address, host)
}

fn loop_config(pki: &Pki, address: SocketAddr) -> RemoteRunnerConfig {
    let identity = pki.identity(&["loop.test"], pki.ca_pem());
    RemoteRunnerConfig {
        url: format!("wss://{address}/lrp").parse().unwrap(),
        server_name: None,
        tls: identity.client_config().unwrap(),
        expected_runner_id: Some("runner-a".to_owned()),
        workspace_id: WorkspaceId::new("ws-1"),
        session_id: None,
        mode: LeaseMode::Coding,
        tools: None,
        call_deadline: Duration::from_secs(30),
    }
}

fn call(call_id: &str, name: &str, arguments: serde_json::Value) -> ToolExecutionRequest {
    ToolExecutionRequest {
        run_id: RunId::new("run-1"),
        call: ToolCallItem {
            id: None,
            call_id: call_id.to_owned(),
            name: name.to_owned(),
            arguments,
            provider_state: None,
        },
    }
}

fn text(result: &arabica_model::ToolResultItem) -> String {
    result
        .content
        .iter()
        .filter_map(|block| match block {
            ContentBlock::Text { text } => Some(text.as_str()),
            _ => None,
        })
        .collect()
}

#[tokio::test]
async fn a_leased_runner_advertises_classifies_and_executes_its_tools() {
    let pki = Pki::new();
    let root = workspace();
    let address = start_runner(&pki, root.clone(), shell_policy(), 4).await;
    let mut runner = RemoteRunner::connect(loop_config(&pki, address))
        .await
        .expect("loop connects");

    assert_eq!(runner.runner_id(), "runner-a");
    assert_eq!(
        names(&runner),
        names_of(&arabica_runner::tool_definitions(&shell_policy()))
    );

    let read = call("c-1", "read_file", json!({ "path": "hello.txt" }));
    assert_eq!(runner.classify(&read.call), ToolInteractionKind::Inspection);
    let result = runner.execute(read).await.expect("read executes");
    assert!(!result.result.is_error);
    assert!(text(&result.result).contains("hello from the runner"));

    let write = call(
        "c-2",
        "write_file",
        json!({ "path": "out.txt", "content": "written remotely\n" }),
    );
    assert_eq!(runner.classify(&write.call), ToolInteractionKind::Mutation);
    let result = runner.execute(write).await.expect("write executes");
    assert!(!result.result.is_error, "{}", text(&result.result));
    assert_eq!(
        std::fs::read_to_string(root.join("out.txt")).unwrap(),
        "written remotely\n"
    );

    // Shell is classified on the loop from the command text.
    let shell = call(
        "c-3",
        "shell",
        json!({ "command": "echo out; echo err 1>&2" }),
    );
    let local = arabica_runner::LocalRunner::with_policy(&root, shell_policy());
    assert_eq!(runner.classify(&shell.call), local.classify(&shell.call));
    let result = runner.execute(shell).await.expect("shell executes");
    assert!(!result.result.is_error, "{}", text(&result.result));
    let stdout: String = result
        .output
        .iter()
        .filter_map(|output| match output {
            RunnerOutput::Stdout(text) => Some(text.as_str()),
            RunnerOutput::Stderr(_) => None,
        })
        .collect();
    let stderr: String = result
        .output
        .iter()
        .filter_map(|output| match output {
            RunnerOutput::Stderr(text) => Some(text.as_str()),
            RunnerOutput::Stdout(_) => None,
        })
        .collect();
    assert!(stdout.contains("out"), "stdout: {stdout:?}");
    assert!(stderr.contains("err"), "stderr: {stderr:?}");
}

#[tokio::test]
async fn a_read_only_lease_cannot_change_the_workspace() {
    let pki = Pki::new();
    let root = workspace();
    let address = start_runner(&pki, root.clone(), shell_policy(), 4).await;
    let mut config = loop_config(&pki, address);
    config.mode = LeaseMode::ReadOnly;
    let mut runner = RemoteRunner::connect(config).await.expect("loop connects");

    assert!(runner.contains("read_file"));
    assert!(!runner.contains("write_file"));
    assert!(!runner.contains("shell"));
    let result = runner
        .execute(call(
            "c-1",
            "write_file",
            json!({ "path": "out.txt", "content": "nope" }),
        ))
        .await
        .expect("a refused tool is a tool error, not a run failure");
    assert!(result.result.is_error);
    assert!(!root.join("out.txt").exists());
}

#[tokio::test]
async fn a_loop_without_a_trusted_certificate_is_refused() {
    let pki = Pki::new();
    let address = start_runner(&pki, workspace(), shell_policy(), 4).await;
    // Trusts the runner, but its own certificate comes from another CA.
    let stranger = Pki::new().identity(&["loop.test"], pki.ca_pem());
    let mut config = loop_config(&pki, address);
    config.tls = stranger.client_config().unwrap();
    assert!(RemoteRunner::connect(config).await.is_err());
}

#[tokio::test]
async fn a_runner_without_a_trusted_certificate_is_refused() {
    let pki = Pki::new();
    let address = start_runner(&pki, workspace(), shell_policy(), 4).await;
    // The loop pins a different CA than the one that signed the runner.
    let other = Pki::new();
    let mut identity = pki.identity(&["loop.test"], other.ca_pem());
    identity.peer_ca_pem = other.ca_pem();
    let mut config = loop_config(&pki, address);
    config.tls = identity.client_config().unwrap();
    let error = RemoteRunner::connect(config).await.unwrap_err();
    assert!(error.to_string().contains("tls"), "{error}");
}

#[tokio::test]
async fn the_handshake_checks_runner_identity_and_workspace() {
    let pki = Pki::new();
    let address = start_runner(&pki, workspace(), shell_policy(), 4).await;

    let mut config = loop_config(&pki, address);
    config.expected_runner_id = Some("runner-b".to_owned());
    let error = RemoteRunner::connect(config).await.unwrap_err();
    assert!(error.to_string().contains("unauthenticated"), "{error}");

    let mut config = loop_config(&pki, address);
    config.workspace_id = WorkspaceId::new("ws-elsewhere");
    let error = RemoteRunner::connect(config).await.unwrap_err();
    assert!(error.to_string().contains("workspace_unknown"), "{error}");
}

#[tokio::test]
async fn plaintext_urls_are_refused() {
    let pki = Pki::new();
    let mut config = loop_config(&pki, "127.0.0.1:9".parse().unwrap());
    config.url = "ws://127.0.0.1:9/lrp".parse().unwrap();
    let error = RemoteRunner::connect(config).await.unwrap_err();
    assert!(error.to_string().contains("wss://"), "{error}");
}

#[tokio::test]
async fn a_call_past_its_deadline_is_a_tool_error() {
    let pki = Pki::new();
    let address = start_runner(&pki, workspace(), shell_policy(), 4).await;
    let mut config = loop_config(&pki, address);
    config.call_deadline = Duration::from_millis(300);
    let mut runner = RemoteRunner::connect(config).await.expect("loop connects");
    let result = runner
        .execute(call("c-1", "shell", json!({ "command": "sleep 5" })))
        .await
        .expect("a deadline is reported to the model");
    assert!(result.result.is_error);
    assert!(text(&result.result).contains("deadline"));
}

#[tokio::test]
async fn dropping_a_call_cancels_it_on_the_runner_and_frees_capacity() {
    let pki = Pki::new();
    let root = workspace();
    // One slot: a call left running on the runner would block the next one.
    let address = start_runner(&pki, root.clone(), shell_policy(), 1).await;
    let mut runner = RemoteRunner::connect(loop_config(&pki, address))
        .await
        .expect("loop connects");

    let marker = root.join("finished.txt");
    let slow = call(
        "c-1",
        "shell",
        json!({ "command": "sleep 2 && touch finished.txt" }),
    );
    // How the runtime cancels: it drops the execute future.
    assert!(
        tokio::time::timeout(Duration::from_millis(300), runner.execute(slow))
            .await
            .is_err()
    );

    // Queued behind the cancellation, then admitted once the slot frees.
    let result = runner
        .execute(call("c-2", "read_file", json!({ "path": "hello.txt" })))
        .await
        .expect("runner stays connected");
    assert!(!result.result.is_error, "{}", text(&result.result));

    tokio::time::sleep(Duration::from_millis(2_500)).await;
    assert!(!marker.exists(), "the cancelled command kept running");
}

#[tokio::test]
async fn a_full_runner_queues_the_call_until_its_deadline() {
    let pki = Pki::new();
    let address = start_runner(&pki, workspace(), shell_policy(), 1).await;
    let mut busy = RemoteRunner::connect(loop_config(&pki, address))
        .await
        .expect("first loop connects");
    let mut config = loop_config(&pki, address);
    config.call_deadline = Duration::from_millis(400);
    let mut waiting = RemoteRunner::connect(config)
        .await
        .expect("second loop connects");

    let hold = tokio::spawn(async move {
        busy.execute(call("c-1", "shell", json!({ "command": "sleep 2" })))
            .await
    });
    tokio::time::sleep(Duration::from_millis(200)).await;
    let result = waiting
        .execute(call("c-2", "read_file", json!({ "path": "hello.txt" })))
        .await
        .expect("a full runner is a tool error, not a run failure");
    assert!(result.result.is_error);
    assert!(text(&result.result).contains("max_concurrent_calls"));
    hold.abort();
}

#[tokio::test]
async fn cancel_reports_whether_anything_was_running() {
    let pki = Pki::new();
    let address = start_runner(&pki, workspace(), shell_policy(), 4).await;
    let mut runner = RemoteRunner::connect(loop_config(&pki, address))
        .await
        .expect("loop connects");
    assert!(!runner.cancel(&RunId::new("run-1")).await.unwrap());
}

fn names_of(tools: &[arabica_model::ToolDefinition]) -> Vec<String> {
    tools.iter().map(|tool| tool.name.clone()).collect()
}

fn names(runner: &RemoteRunner) -> Vec<String> {
    names_of(&runner.definitions())
}

/// Wait for the runner's `tools.changed` to reach `generation`.
async fn wait_for_generation(runner: &RemoteRunner, generation: u64) {
    tokio::time::timeout(Duration::from_secs(5), async {
        while runner.tools_snapshot().0 < generation {
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
    })
    .await
    .expect("tools.changed arrives");
}

#[tokio::test]
async fn a_policy_change_reaches_every_lease_as_tools_changed() {
    let pki = Pki::new();
    let root = workspace();
    let (address, host) = start_runner_host(&pki, root.clone(), shell_policy(), 4).await;
    let mut everything = RemoteRunner::connect(loop_config(&pki, address))
        .await
        .expect("loop connects");
    let subset = RemoteRunner::connect(RemoteRunnerConfig {
        tools: Some(vec!["read_file".to_owned(), "shell".to_owned()]),
        ..loop_config(&pki, address)
    })
    .await
    .expect("loop connects");
    assert_eq!(everything.tools_snapshot().0, 0);
    assert_eq!(names(&subset), ["read_file", "shell"]);

    // The operator withdraws shell.
    host.set_policy(LocalRunnerPolicy::coding()).unwrap();
    wait_for_generation(&everything, 1).await;
    wait_for_generation(&subset, 1).await;
    assert_eq!(
        names(&everything),
        names_of(&arabica_runner::tool_definitions(
            &LocalRunnerPolicy::coding()
        ))
    );
    assert_eq!(names(&subset), ["read_file"]);
    assert!(!everything.contains("shell"));

    // A call the loop sends anyway is refused by the runner's lease.
    let shell = call("c-1", "shell", json!({ "command": "touch withdrawn" }));
    assert_eq!(
        everything.classify(&shell.call),
        ToolInteractionKind::Generic
    );
    let refused = everything
        .execute(shell)
        .await
        .expect("refusal is a tool error");
    assert!(refused.result.is_error);
    assert!(text(&refused.result).contains("not offered"));
    assert!(!root.join("withdrawn").exists());

    // Restoring it brings shell back only to leases that asked for it.
    host.set_policy(shell_policy()).unwrap();
    wait_for_generation(&everything, 2).await;
    wait_for_generation(&subset, 2).await;
    assert_eq!(names(&subset), ["read_file", "shell"]);
    let shell = call("c-2", "shell", json!({ "command": "echo back" }));
    let result = everything.execute(shell).await.expect("shell executes");
    assert!(!result.result.is_error, "{}", text(&result.result));

    // A new connection opens with the current policy.
    host.set_policy(LocalRunnerPolicy::read_only()).unwrap();
    let late = RemoteRunner::connect(loop_config(&pki, address))
        .await
        .expect("loop connects");
    assert_eq!(
        names(&late),
        names_of(&arabica_runner::tool_definitions(
            &LocalRunnerPolicy::read_only()
        ))
    );
    assert!(host.set_policy(invalid_policy()).is_err());
    std::fs::remove_dir_all(root).unwrap();
}

fn invalid_policy() -> LocalRunnerPolicy {
    let mut policy = LocalRunnerPolicy::coding();
    policy.max_read_bytes = 0;
    policy
}
