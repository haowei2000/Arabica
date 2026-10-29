//! Remote runners over the Loop–Runner Protocol: `arabica runner serve` on
//! the machine that owns a workspace, and the `--runner <name>` connection
//! the agent loop opens to it.

use std::collections::BTreeMap;
use std::net::SocketAddr;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Duration;

use arabica_protocol::WorkspaceId;
use arabica_protocol::lrp::LeaseMode;
use arabica_runner::{LocalRunnerPolicy, LocalTool};
use arabica_runner_remote::{
    RemoteRunner, RemoteRunnerConfig, RunnerHost, RunnerHostConfig, TlsIdentity,
};
use clap::{Args, Subcommand};

#[derive(Subcommand, Debug)]
pub enum RunnerAction {
    /// Serve this machine's workspaces to a remote agent loop over LRP
    /// (WebSocket on mutually authenticated TLS 1.3).
    Serve(ServeArgs),
}

#[derive(Args, Debug)]
pub struct ServeArgs {
    /// Address to listen on.
    #[arg(long, default_value = "127.0.0.1:7443")]
    listen: SocketAddr,
    /// A workspace to serve, as ID=PATH. Repeat for several workspaces.
    #[arg(long = "workspace", value_name = "ID=PATH", required = true, value_parser = parse_pair)]
    workspaces: Vec<(String, String)>,
    /// This runner's identity, announced in its hello.
    #[arg(long)]
    runner_id: String,
    /// The runner's TLS certificate chain (PEM).
    #[arg(long)]
    cert: PathBuf,
    /// The runner's private key (PEM); must be readable only by its owner.
    #[arg(long)]
    key: PathBuf,
    /// CA bundle that agent loop client certificates must chain to (PEM).
    #[arg(long)]
    client_ca: PathBuf,
    /// Offer the shell tool. Off by default: shell cannot be confined to
    /// the workspace root.
    #[arg(long, conflicts_with = "read_only")]
    allow_shell: bool,
    /// Offer read-only tools only.
    #[arg(long)]
    read_only: bool,
    /// A label for routing, as KEY=VALUE. Repeatable.
    #[arg(long = "label", value_name = "KEY=VALUE", value_parser = parse_pair)]
    labels: Vec<(String, String)>,
    /// Calls this runner executes at once, across every connection.
    #[arg(long, default_value_t = 8)]
    max_concurrent_calls: u32,
}

fn parse_pair(value: &str) -> Result<(String, String), String> {
    match value.split_once('=') {
        Some((key, rest)) if !key.trim().is_empty() && !rest.trim().is_empty() => {
            Ok((key.trim().to_owned(), rest.trim().to_owned()))
        }
        _ => Err(format!("expected KEY=VALUE, got {value:?}")),
    }
}

pub async fn run(action: RunnerAction) -> i32 {
    match action {
        RunnerAction::Serve(args) => match serve(args).await {
            Ok(()) => 0,
            Err(error) => {
                eprintln!("error: {error}");
                1
            }
        },
    }
}

async fn serve(args: ServeArgs) -> Result<(), String> {
    require_private_key(&args.key)?;
    let identity = TlsIdentity::from_files(&args.cert, &args.key, &args.client_ca)
        .map_err(|error| error.to_string())?;
    let mut policy = if args.read_only {
        LocalRunnerPolicy::read_only()
    } else {
        LocalRunnerPolicy::coding()
    };
    if args.allow_shell {
        policy = policy.with_tool(LocalTool::Shell);
    }
    let mut workspaces = BTreeMap::new();
    for (id, path) in args.workspaces {
        let root = std::fs::canonicalize(&path)
            .map_err(|error| format!("workspace {id}: cannot open {path}: {error}"))?;
        if workspaces
            .insert(WorkspaceId::new(id.clone()), root)
            .is_some()
        {
            return Err(format!("workspace {id} is listed twice"));
        }
    }
    let host = Arc::new(
        RunnerHost::new(RunnerHostConfig {
            runner_id: args.runner_id,
            labels: args.labels.into_iter().collect(),
            policy,
            workspaces,
            max_concurrent_calls: args.max_concurrent_calls,
            heartbeat_interval: Duration::from_secs(15),
        })
        .map_err(|error| error.to_string())?,
    );
    let acceptor = identity.acceptor().map_err(|error| error.to_string())?;
    let listener = tokio::net::TcpListener::bind(args.listen)
        .await
        .map_err(|error| format!("cannot listen on {}: {error}", args.listen))?;

    let config = host.config();
    eprintln!(
        "arabica runner {} listening on wss://{}",
        config.runner_id,
        listener.local_addr().map_err(|error| error.to_string())?
    );
    for (id, root) in &config.workspaces {
        eprintln!("  workspace {id}: {}", root.display());
    }
    let tools: Vec<&str> = config.policy.tools.iter().map(|tool| tool.name()).collect();
    eprintln!("  tools: {}", tools.join(", "));

    tokio::select! {
        served = Arc::clone(&host).serve(listener, acceptor, |error| {
            eprintln!("arabica runner: connection ended: {error}");
        }) => served.map_err(|error| format!("listener failed: {error}")),
        _ = tokio::signal::ctrl_c() => Ok(()),
    }
}

/// Connect to the runner configured as `[runners.<name>]` and lease its
/// workspace with no more tools than `policy` allows locally.
pub(crate) async fn connect_configured(
    arabica_home: &Path,
    name: &str,
    policy: &LocalRunnerPolicy,
) -> Result<RemoteRunner, String> {
    let settings = crate::config::user_config_runner(arabica_home, name)
        .map_err(|error| format!("runner configuration: {error}"))?;
    require_private_key(&settings.client_key)?;
    let tls = TlsIdentity::from_files(
        &settings.client_cert,
        &settings.client_key,
        &settings.ca_cert,
    )
    .and_then(|identity| identity.client_config())
    .map_err(|error| format!("runner {name}: {error}"))?;
    let read_only = policy
        .tools
        .iter()
        .all(|tool| tool.interaction() == arabica_protocol::ToolInteractionKind::Inspection);
    RemoteRunner::connect(RemoteRunnerConfig {
        url: settings.url,
        server_name: settings.server_name,
        tls,
        expected_runner_id: settings.runner_id,
        workspace_id: WorkspaceId::new(settings.workspace),
        session_id: None,
        mode: if read_only {
            LeaseMode::ReadOnly
        } else {
            LeaseMode::Coding
        },
        // The lease never exceeds what this host would allow locally, so
        // --read-only and --allow-shell keep their meaning.
        tools: Some(
            policy
                .tools
                .iter()
                .map(|tool| tool.name().to_owned())
                .collect(),
        ),
        call_deadline: Duration::from_secs(settings.call_timeout_secs),
    })
    .await
    .map_err(|error| format!("runner {name}: {error}"))
}

/// A private key file must be a regular file only its owner can read.
fn require_private_key(path: &Path) -> Result<(), String> {
    let metadata = std::fs::metadata(path)
        .map_err(|error| format!("cannot read private key {}: {error}", path.display()))?;
    if !metadata.is_file() {
        return Err(format!(
            "private key {} is not a regular file",
            path.display()
        ));
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        if metadata.permissions().mode() & 0o077 != 0 {
            return Err(format!(
                "private key {} must be readable only by its owner (chmod 600)",
                path.display()
            ));
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pairs_need_a_key_and_a_value() {
        assert_eq!(
            parse_pair("ws-1=/srv/repo").unwrap(),
            ("ws-1".to_owned(), "/srv/repo".to_owned())
        );
        assert_eq!(parse_pair("url=wss://a/b?c=d").unwrap().1, "wss://a/b?c=d");
        assert!(parse_pair("ws-1").is_err());
        assert!(parse_pair("=/srv").is_err());
        assert!(parse_pair("ws-1=").is_err());
    }

    fn temp_dir(label: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("arabica-{label}-{}", uuid::Uuid::now_v7()));
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[cfg(unix)]
    fn write_private(path: &Path, contents: &str) {
        use std::os::unix::fs::PermissionsExt;
        std::fs::write(path, contents).unwrap();
        std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o600)).unwrap();
    }

    /// A throwaway CA plus a runner identity and loop certificate files in
    /// `home`, laid out the way `[runners.<name>]` refers to them.
    #[cfg(unix)]
    fn pki(home: &Path) -> TlsIdentity {
        use rcgen::{
            BasicConstraints, CertificateParams, CertifiedIssuer, ExtendedKeyUsagePurpose, IsCa,
            KeyPair, KeyUsagePurpose,
        };
        let mut params = CertificateParams::new(Vec::<String>::new()).unwrap();
        params.is_ca = IsCa::Ca(BasicConstraints::Unconstrained);
        params.key_usages = vec![KeyUsagePurpose::KeyCertSign];
        let ca = CertifiedIssuer::self_signed(params, KeyPair::generate().unwrap()).unwrap();
        let leaf = |names: Vec<String>| {
            let mut params = CertificateParams::new(names).unwrap();
            params.extended_key_usages = vec![
                ExtendedKeyUsagePurpose::ServerAuth,
                ExtendedKeyUsagePurpose::ClientAuth,
            ];
            let key = KeyPair::generate().unwrap();
            let cert = params.signed_by(&key, &ca).unwrap();
            (cert.pem(), key.serialize_pem())
        };
        let (loop_cert, loop_key) = leaf(vec!["loop.test".to_owned()]);
        std::fs::create_dir_all(home.join("certs")).unwrap();
        std::fs::write(home.join("certs/ca.pem"), ca.pem()).unwrap();
        std::fs::write(home.join("certs/loop.pem"), loop_cert).unwrap();
        write_private(&home.join("certs/loop.key"), &loop_key);
        let (runner_cert, runner_key) = leaf(vec!["127.0.0.1".to_owned()]);
        TlsIdentity {
            cert_chain_pem: runner_cert.into_bytes(),
            private_key_pem: runner_key.into_bytes(),
            peer_ca_pem: ca.pem().into_bytes(),
        }
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn a_configured_runner_replaces_the_local_tools_within_the_local_policy() {
        use arabica_model::ToolCallItem;
        use arabica_protocol::RunId;
        use arabica_runner::{RunnerEnvironment, ToolExecutionRequest};

        let home = temp_dir("runner-home");
        let remote_root = temp_dir("runner-remote");
        let local_root = temp_dir("runner-local");
        let identity = pki(&home);

        // The runner would allow shell; the loop's local policy does not.
        let host = Arc::new(
            RunnerHost::new(RunnerHostConfig {
                runner_id: "box".to_owned(),
                labels: BTreeMap::new(),
                policy: LocalRunnerPolicy::coding().with_tool(LocalTool::Shell),
                workspaces: BTreeMap::from([(WorkspaceId::new("repo"), remote_root.clone())]),
                max_concurrent_calls: 2,
                heartbeat_interval: Duration::from_secs(5),
            })
            .unwrap(),
        );
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        tokio::spawn(Arc::clone(&host).serve(listener, identity.acceptor().unwrap(), |_| {}));

        write_private(
            &home.join("config.toml"),
            &format!(
                "[runners.box]\n\
                 url = \"wss://{address}/lrp\"\n\
                 workspace = \"repo\"\n\
                 runner_id = \"box\"\n\
                 ca_cert = \"certs/ca.pem\"\n\
                 client_cert = \"certs/loop.pem\"\n\
                 client_key = \"certs/loop.key\"\n"
            ),
        );

        let policy = LocalRunnerPolicy::coding();
        let remote = connect_configured(&home, "box", &policy)
            .await
            .expect("configured runner connects");
        let mut runtime = crate::host::build_host_runtime_with_runner(
            crate::host::HostModel::Scripted(Default::default()),
            &local_root,
            policy,
            &home.join("state"),
            crate::mcp::McpTools::default(),
            Some(remote),
        );
        let names: Vec<&str> = runtime
            .tools()
            .iter()
            .map(|tool| tool.name.as_str())
            .collect();
        assert!(names.contains(&"write_file"));
        assert!(
            !names.contains(&"shell"),
            "the lease must not exceed the local policy"
        );

        let result = runtime
            .runner_mut()
            .execute(ToolExecutionRequest {
                run_id: RunId::new("run-1"),
                call: ToolCallItem {
                    id: None,
                    call_id: "c-1".to_owned(),
                    name: "write_file".to_owned(),
                    arguments: serde_json::json!({ "path": "note.txt", "content": "remote\n" }),
                    provider_state: None,
                },
            })
            .await
            .expect("write runs remotely");
        assert!(!result.result.is_error);
        assert_eq!(
            std::fs::read_to_string(remote_root.join("note.txt")).unwrap(),
            "remote\n"
        );
        assert!(!local_root.join("note.txt").exists());

        // Narrowing the runner's policy swaps only the remote tools.
        let host_tools: Vec<String> = runtime
            .tools()
            .iter()
            .filter(|tool| !runtime.runner().remote_tool_names().contains(&tool.name))
            .map(|tool| tool.name.clone())
            .collect();
        assert!(!crate::host::sync_remote_tools(&mut runtime));
        host.set_policy(LocalRunnerPolicy::read_only()).unwrap();
        tokio::time::timeout(Duration::from_secs(5), async {
            while !crate::host::sync_remote_tools(&mut runtime) {
                tokio::time::sleep(Duration::from_millis(10)).await;
            }
        })
        .await
        .expect("tools.changed arrives");
        let names: Vec<String> = runtime
            .tools()
            .iter()
            .map(|tool| tool.name.clone())
            .collect();
        assert!(names.contains(&"read_file".to_owned()));
        assert!(!names.contains(&"write_file".to_owned()));
        assert!(host_tools.iter().all(|tool| names.contains(tool)));
        assert_eq!(
            names.len(),
            host_tools.len() + runtime.runner().remote_tool_names().len()
        );
    }

    #[test]
    fn runner_tables_are_validated_and_paths_resolve_against_home() {
        let home = temp_dir("runner-config");
        let config = home.join("config.toml");
        std::fs::write(
            &config,
            "[runners.box]\n\
             url = \"wss://box.internal:7443/lrp\"\n\
             workspace = \"repo\"\n\
             ca_cert = \"certs/ca.pem\"\n\
             client_cert = \"/etc/arabica/loop.pem\"\n\
             client_key = \"certs/loop.key\"\n",
        )
        .unwrap();
        let settings = crate::config::user_config_runner(&home, "box").unwrap();
        assert_eq!(settings.ca_cert, home.join("certs/ca.pem"));
        assert_eq!(settings.client_cert, PathBuf::from("/etc/arabica/loop.pem"));
        assert_eq!(
            settings.call_timeout_secs,
            crate::config::DEFAULT_REMOTE_CALL_TIMEOUT_SECS
        );
        let unknown = crate::config::user_config_runner(&home, "other").unwrap_err();
        assert!(unknown.to_string().contains("configured: box"), "{unknown}");

        std::fs::write(
            &config,
            "[runners.box]\n\
             url = \"ws://box.internal:7443/lrp\"\n\
             workspace = \"repo\"\n\
             ca_cert = \"ca.pem\"\n\
             client_cert = \"loop.pem\"\n\
             client_key = \"loop.key\"\n",
        )
        .unwrap();
        let plaintext = crate::config::user_config_runner(&home, "box").unwrap_err();
        assert!(plaintext.to_string().contains("wss://"), "{plaintext}");
    }

    #[cfg(unix)]
    #[test]
    fn a_shared_private_key_is_refused() {
        use std::os::unix::fs::PermissionsExt;
        let dir = std::env::temp_dir().join(format!("arabica-key-{}", uuid::Uuid::now_v7()));
        std::fs::create_dir_all(&dir).unwrap();
        let key = dir.join("loop.key");
        std::fs::write(&key, "not a real key").unwrap();
        std::fs::set_permissions(&key, std::fs::Permissions::from_mode(0o644)).unwrap();
        assert!(require_private_key(&key).unwrap_err().contains("chmod 600"));
        std::fs::set_permissions(&key, std::fs::Permissions::from_mode(0o600)).unwrap();
        assert!(require_private_key(&key).is_ok());
    }
}
