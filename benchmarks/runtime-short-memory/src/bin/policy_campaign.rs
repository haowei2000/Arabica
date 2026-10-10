//! Real, isolated policy trials composed from the production Rust host.
use std::collections::{BTreeMap, BTreeSet};
use std::error::Error;
use std::io::Read;
use std::path::{Component, Path, PathBuf};
use std::sync::Arc;

use arabica_adapters::{NewSession, SqliteEvaluationStore, SqliteSessionRepository};
use arabica_cli::config::resolve_cli_runtime_config;
use arabica_cli::host::{
    HostConfigArgs, HostModelCatalog, UuidIds, build_host_runtime, process_environment,
    workspace_id_for,
};
use arabica_protocol::{Command, CommandEnvelope, CommandId};
use arabica_runtime::{
    AcceptanceCheck, AcceptanceSpec, ArtifactEvidence, BlendRoutingPolicy,
    EvidenceAcceptanceScorer, PolicyComparisonTrial, RunAcceptanceScorer, RunCancellation,
    RunControl, RunEvaluation, run_evidence_snapshots,
};
use arabica_session::{
    DispatchControl, EventVisibility, SessionEventObserver, SessionManager, SessionStore,
};
use serde::Deserialize;

type Result<T> = std::result::Result<T, Box<dyn Error + Send + Sync>>;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Campaign {
    candidates: Vec<Candidate>,
    tasks: Vec<Task>,
    repetitions: u32,
    max_model_steps: usize,
    timeout_seconds: u64,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Candidate {
    label: String,
    policy_id: String,
    #[serde(default)]
    version: Option<u64>,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Task {
    prompt: String,
    acceptance: AcceptanceSpec,
    #[serde(default)]
    initial_files: BTreeMap<String, String>,
}

fn relative_path(value: &str) -> Result<PathBuf> {
    let path = Path::new(value);
    if value.is_empty()
        || !path
            .components()
            .all(|component| matches!(component, Component::Normal(_)))
    {
        return Err("campaign paths must be non-empty confined relative paths".into());
    }
    Ok(path.to_path_buf())
}

impl Campaign {
    fn validate(&self) -> Result<()> {
        if self.candidates.len() < 2
            || self.candidates.len() > 16
            || self.tasks.is_empty()
            || self.tasks.len() > 100
            || !(1..=20).contains(&self.repetitions)
            || !(1..=100).contains(&self.max_model_steps)
            || !(1..=3600).contains(&self.timeout_seconds)
        {
            return Err("invalid campaign bounds".into());
        }
        let mut labels = BTreeSet::new();
        for candidate in &self.candidates {
            if candidate.label.trim().is_empty()
                || candidate.policy_id.trim().is_empty()
                || !labels.insert(&candidate.label)
            {
                return Err("candidate labels must be unique and policy IDs non-empty".into());
            }
        }
        let mut tasks = BTreeSet::new();
        for task in &self.tasks {
            task.acceptance.validate()?;
            if task.prompt.trim().is_empty()
                || task.acceptance.criteria.is_empty()
                || !tasks.insert(&task.acceptance.task_id)
            {
                return Err("campaign tasks need a prompt, criteria, and unique task ID".into());
            }
            let mut bytes = 0_usize;
            for (path, content) in &task.initial_files {
                relative_path(path)?;
                bytes = bytes.saturating_add(content.len());
            }
            if task.initial_files.len() > 128 || bytes > 1024 * 1024 {
                return Err("campaign fixture exceeds bounds".into());
            }
            for criterion in &task.acceptance.criteria {
                if let AcceptanceCheck::ArtifactContains { path, .. } = &criterion.check {
                    relative_path(path)?;
                }
            }
        }
        Ok(())
    }
}

// Capture only explicitly requested files. Reject symlinks at every component
// and bound reads; task failures and unavailable evidence remain distinct.
fn capture_artifacts(
    root: &Path,
    spec: &AcceptanceSpec,
) -> Result<BTreeMap<String, ArtifactEvidence>> {
    let mut artifacts = BTreeMap::new();
    let mut remaining = 64 * 1024_usize;
    for criterion in &spec.criteria {
        let AcceptanceCheck::ArtifactContains { path, .. } = &criterion.check else {
            continue;
        };
        if artifacts.contains_key(path) {
            continue;
        }
        let relative = relative_path(path)?;
        let capture = || -> std::io::Result<ArtifactEvidence> {
            let root = rustix::fs::open(
                root,
                rustix::fs::OFlags::RDONLY
                    | rustix::fs::OFlags::DIRECTORY
                    | rustix::fs::OFlags::NOFOLLOW,
                rustix::fs::Mode::empty(),
            )?;
            let components: Vec<_> = relative.components().collect();
            let mut directory = root;
            for component in &components[..components.len() - 1] {
                directory = rustix::fs::openat(
                    &directory,
                    component.as_os_str(),
                    rustix::fs::OFlags::RDONLY
                        | rustix::fs::OFlags::DIRECTORY
                        | rustix::fs::OFlags::NOFOLLOW,
                    rustix::fs::Mode::empty(),
                )?;
            }
            let descriptor = rustix::fs::openat(
                &directory,
                components.last().unwrap().as_os_str(),
                rustix::fs::OFlags::RDONLY
                    | rustix::fs::OFlags::NOFOLLOW
                    | rustix::fs::OFlags::NONBLOCK,
                rustix::fs::Mode::empty(),
            )?;
            let file = std::fs::File::from(descriptor);
            if !file.metadata()?.is_file() {
                return Ok(ArtifactEvidence::Unavailable);
            }
            let mut bytes = Vec::new();
            file.take(remaining.saturating_sub(path.len()) as u64 + 1)
                .read_to_end(&mut bytes)?;
            if path.len() + bytes.len() > remaining {
                return Ok(ArtifactEvidence::Unavailable);
            }
            Ok(match String::from_utf8(bytes) {
                Ok(content) => ArtifactEvidence::Captured { content },
                Err(_) => ArtifactEvidence::Unavailable,
            })
        };
        let evidence = match capture() {
            Ok(evidence) => evidence,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => ArtifactEvidence::Missing,
            Err(_) => ArtifactEvidence::Unavailable,
        };
        remaining = remaining.saturating_sub(
            path.len()
                + match &evidence {
                    ArtifactEvidence::Captured { content } => content.len(),
                    _ => 0,
                },
        );
        artifacts.insert(path.clone(), evidence);
    }
    Ok(artifacts)
}

async fn execute_trial(
    catalog: &HostModelCatalog,
    policy: BlendRoutingPolicy,
    task: &Task,
    root: &Path,
    storage_home: &Path,
    config: &Campaign,
    allow_shell: bool,
) -> Result<RunEvaluation> {
    std::fs::create_dir(root)?;
    for (path, content) in &task.initial_files {
        let target = root.join(relative_path(path)?);
        if let Some(parent) = target.parent() {
            std::fs::create_dir_all(parent)?;
        }
        std::fs::write(target, content)?;
    }
    let mut catalog = catalog.clone();
    catalog.blend_policy = Some(policy.clone());
    let model = catalog.build_model()?;
    let tool_policy = arabica_cli::print::tool_policy(allow_shell, false)?;
    let mut runtime = build_host_runtime(model, root, tool_policy, storage_home);
    runtime.set_blend_policy(Some(policy))?;
    runtime.set_max_model_steps_per_run(config.max_model_steps);
    let mut manager = SessionManager::with_ids(runtime, Box::new(UuidIds));
    let workspace = workspace_id_for(root);
    let created = manager
        .handle(CommandEnvelope::new(
            CommandId::new("create"),
            None,
            Command::SessionCreate {
                workspace_id: workspace.clone(),
            },
        ))
        .await?;
    let session = created
        .first()
        .ok_or("session creation returned no event")?
        .session_id
        .clone();
    let repository = SqliteSessionRepository::new(storage_home);
    let store = repository.create(NewSession {
        session_id: &session,
        workspace_id: &workspace,
        cwd: root,
        profile: None,
        instructions_sha256: None,
    })?;
    for event in &created {
        store.observe(event, EventVisibility::Client);
    }
    let cancellation = RunCancellation::new();
    let timer = tokio::spawn({
        let cancellation = cancellation.clone();
        let seconds = config.timeout_seconds;
        async move {
            tokio::time::sleep(std::time::Duration::from_secs(seconds)).await;
            cancellation.cancel();
        }
    });
    let observer: Arc<dyn SessionEventObserver> = store;
    let result = manager
        .dispatch(
            CommandEnvelope::new(
                CommandId::new("task"),
                Some(session.clone()),
                Command::MessageSend {
                    content: task.prompt.clone(),
                },
            ),
            DispatchControl {
                run: RunControl {
                    cancellation: Some(cancellation),
                    ..RunControl::default()
                },
                observer: Some(observer),
            },
        )
        .await;
    timer.abort();
    result?;
    let stored = repository.read(&workspace, &session)?;
    let mut snapshot = run_evidence_snapshots(&stored.events)
        .pop()
        .ok_or("trial produced no terminal evidence")?
        .with_artifacts(capture_artifacts(root, &task.acceptance)?)?;
    use sha2::{Digest, Sha256};
    snapshot.fixture_fingerprint = Some(hex::encode(Sha256::digest(serde_json::to_vec(&(
        &task.initial_files,
        config.max_model_steps,
        config.timeout_seconds,
        allow_shell,
    ))?)));
    let artifacts = snapshot.artifacts.clone();
    let snapshot = snapshot.with_artifacts(artifacts)?;
    let acceptance = EvidenceAcceptanceScorer.score(&snapshot, &task.acceptance)?;
    let mut db = SqliteEvaluationStore::open(&storage_home.join("evaluation.sqlite3"))?;
    db.save_acceptance(&snapshot, &task.acceptance, &acceptance)?;
    Ok(RunEvaluation {
        snapshot,
        acceptance: Some(acceptance),
    })
}

async fn run() -> Result<()> {
    let mut args = std::env::args().skip(1);
    let mut manifest = None;
    let mut output = None;
    let mut allow_shell = false;
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--manifest" => {
                manifest = Some(PathBuf::from(args.next().ok_or("--manifest needs a path")?))
            }
            "--output" => {
                output = Some(PathBuf::from(
                    args.next().ok_or("--output needs a new directory")?,
                ))
            }
            "--allow-shell" => allow_shell = true,
            "--help" | "-h" => {
                println!(
                    "policy_campaign --manifest campaign.json --output NEW_DIRECTORY [--allow-shell]\nRuns real provider calls in fresh trial directories. Writes a private SQLite evidence store and comparison report."
                );
                return Ok(());
            }
            _ => return Err("unknown policy campaign argument".into()),
        }
    }
    let manifest = manifest.ok_or("--manifest is required")?;
    let output = output.ok_or("--output is required")?;
    let mut bytes = Vec::new();
    std::fs::File::open(manifest)?
        .take(1024 * 1024 + 1)
        .read_to_end(&mut bytes)?;
    if bytes.len() > 1024 * 1024 {
        return Err("campaign manifest exceeds one MiB".into());
    }
    let campaign: Campaign = serde_json::from_slice(&bytes)?;
    campaign.validate()?;
    let home = arabica_adapters::default_arabica_home()?;
    let resolved = resolve_cli_runtime_config(
        &HostConfigArgs::default(),
        &home,
        &std::env::current_dir()?,
        process_environment,
    )
    .map_err(|error| error.to_string())?;
    let catalog = resolved.model_catalog();
    let mut policies = Vec::new();
    for candidate in &campaign.candidates {
        let policy = match candidate.version {
            Some(version) => {
                SqliteEvaluationStore::read_policy_versions(
                    &home.join("evaluation.sqlite3"),
                    Some(&candidate.policy_id),
                )?
                .into_iter()
                .find(|record| record.version == version)
                .ok_or("candidate policy version not found")?
                .policy
            }
            None => resolved
                .blend_policies
                .get(&candidate.policy_id)
                .cloned()
                .ok_or("configured candidate policy not found")?,
        };
        if policy.tool_call_capable_models.is_empty() {
            return Err("campaign policies require at least one certified tool-call model".into());
        }
        // Validate each policy/model combination before starting any paid work.
        let mut candidate_catalog = catalog.clone();
        candidate_catalog.blend_policy = Some(policy.clone());
        let mut validation_runtime = build_host_runtime(
            candidate_catalog.build_model()?,
            &std::env::current_dir()?,
            arabica_cli::print::tool_policy(allow_shell, false)?,
            &home,
        );
        validation_runtime.set_blend_policy(Some(policy.clone()))?;
        policies.push(policy);
    }
    std::fs::create_dir(&output)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&output, std::fs::Permissions::from_mode(0o700))?;
    }
    let output = std::fs::canonicalize(output)?;
    let storage_home = output.join("store");
    let mut trials = Vec::new();
    for (task_index, task) in campaign.tasks.iter().enumerate() {
        for repetition in 0..campaign.repetitions {
            for position in 0..campaign.candidates.len() {
                // Rotate order between blocks rather than always warming the same arm first.
                let index =
                    (position + task_index + repetition as usize) % campaign.candidates.len();
                let candidate = &campaign.candidates[index];
                eprintln!(
                    "policy campaign: task {} repetition {} candidate {}",
                    task_index + 1,
                    repetition + 1,
                    index + 1
                );
                let root = output.join(format!("trial-{task_index:03}-{repetition:02}-{index:02}"));
                let evaluation = execute_trial(
                    &catalog,
                    policies[index].clone(),
                    task,
                    &root,
                    &storage_home,
                    &campaign,
                    allow_shell,
                )
                .await?;
                trials.push(PolicyComparisonTrial {
                    candidate: candidate.label.clone(),
                    repetition,
                    evaluation,
                });
                std::fs::write(
                    output.join("trials.json"),
                    serde_json::to_vec_pretty(&trials)?,
                )?;
            }
        }
    }
    let mut db = SqliteEvaluationStore::open(&storage_home.join("evaluation.sqlite3"))?;
    let summary = db.save_policy_comparison(&trials)?;
    std::fs::write(
        output.join("comparison.json"),
        serde_json::to_vec_pretty(&summary)?,
    )?;
    println!("{}", serde_json::to_string_pretty(&summary)?);
    Ok(())
}

#[tokio::main]
async fn main() {
    if let Err(error) = run().await {
        eprintln!("policy campaign failed: {error}");
        std::process::exit(1);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn fixture_paths_cannot_escape_trial_roots() {
        for path in ["", "/etc/passwd", "../outside", "a/../../outside", "."] {
            assert!(relative_path(path).is_err());
        }
        assert!(relative_path("src/main.rs").is_ok());
    }
    #[test]
    fn capture_does_not_follow_symlinks_or_read_unbounded_files() {
        let root = std::env::temp_dir().join(format!("policy-capture-{}", std::process::id()));
        std::fs::create_dir(&root).unwrap();
        std::fs::write(root.join("large.txt"), "x".repeat(65537)).unwrap();
        #[cfg(unix)]
        std::os::unix::fs::symlink("/etc/passwd", root.join("link")).unwrap();
        let spec = AcceptanceSpec {
            task_id: "capture".into(),
            version: 1,
            criteria: ["large.txt", "missing.txt", "link"]
                .into_iter()
                .enumerate()
                .map(|(index, path)| arabica_runtime::AcceptanceCriterion {
                    id: index.to_string(),
                    check: AcceptanceCheck::ArtifactContains {
                        path: path.into(),
                        value: "x".into(),
                    },
                })
                .collect(),
        };
        let captured = capture_artifacts(&root, &spec).unwrap();
        assert_eq!(captured["large.txt"], ArtifactEvidence::Unavailable);
        assert_eq!(captured["missing.txt"], ArtifactEvidence::Missing);
        #[cfg(unix)]
        assert_eq!(captured["link"], ArtifactEvidence::Unavailable);
        std::fs::remove_dir_all(root).unwrap();
    }

    #[tokio::test]
    async fn real_host_trials_capture_artifacts_and_compare_without_paid_calls() {
        use arabica_provider::{ApiProviderConfig, ApiType};
        use axum::{Json, Router, routing::post};
        use std::sync::atomic::{AtomicUsize, Ordering};
        let calls = Arc::new(AtomicUsize::new(0));
        let router = Router::new().route("/v1/chat/completions", post({
            let calls = calls.clone();
            move || {
                let calls = calls.clone();
                async move {
                    let tool_turn = calls.fetch_add(1, Ordering::SeqCst).is_multiple_of(2);
                    let message = if tool_turn { serde_json::json!({
                        "role":"assistant", "content":null, "tool_calls":[{"id":"write", "type":"function", "function":{"name":"write_file", "arguments":"{\"path\":\"answer.txt\",\"content\":\"ready\"}"}}]
                    }) } else { serde_json::json!({"role":"assistant", "content":"ready"}) };
                    Json(serde_json::json!({"id":"response", "object":"chat.completion", "choices":[{"index":0,"message":message,"finish_reason":if tool_turn {"tool_calls"} else {"stop"}}],"usage":{"prompt_tokens":10,"completion_tokens":5}}))
                }
            }
        }));
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let server = tokio::spawn(async move {
            axum::serve(listener, router).await.unwrap();
        });
        let provider = ApiProviderConfig::new(
            ApiType::OpenAiChatCompletions,
            "test-key",
            format!("http://{address}/v1"),
            "fake-model",
        );
        let catalog = HostModelCatalog {
            provider,
            models: BTreeMap::from([("default".into(), "fake-model".into())]),
            blend_policy: None,
            model_configs: BTreeMap::new(),
        };
        let policy: BlendRoutingPolicy = serde_json::from_value(serde_json::json!({
            "policy_id":"baseline", "version":1, "default_model":"default", "recovery_after_no_progress_steps":2, "minimum_model_dwell_steps":1, "tool_call_capable_models":["default"]
        })).unwrap();
        let task = Task {
            prompt: "Write ready to answer.txt and finish.".into(),
            initial_files: BTreeMap::from([("answer.txt".into(), "pending".into())]),
            acceptance: AcceptanceSpec {
                task_id: "write".into(),
                version: 1,
                criteria: vec![arabica_runtime::AcceptanceCriterion {
                    id: "file".into(),
                    check: AcceptanceCheck::ArtifactContains {
                        path: "answer.txt".into(),
                        value: "ready".into(),
                    },
                }],
            },
        };
        let config = Campaign {
            candidates: Vec::new(),
            tasks: Vec::new(),
            repetitions: 1,
            max_model_steps: 5,
            timeout_seconds: 10,
        };
        let root = std::env::temp_dir().join(format!(
            "policy-host-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        std::fs::create_dir(&root).unwrap();
        let storage = root.join("store");
        let first = execute_trial(
            &catalog,
            policy.clone(),
            &task,
            &root.join("first"),
            &storage,
            &config,
            false,
        )
        .await
        .unwrap();
        assert_eq!(
            first.acceptance.as_ref().unwrap().verified_success,
            Some(true)
        );
        assert_eq!(first.snapshot.timing.runner_calls, 1);
        assert_eq!(first.snapshot.timing.model_calls, 2);
        assert_eq!(first.snapshot.timing.input_tokens, 20);
        let mut candidate = policy;
        candidate.policy_id = "candidate".into();
        let second = execute_trial(
            &catalog,
            candidate,
            &task,
            &root.join("second"),
            &storage,
            &config,
            false,
        )
        .await
        .unwrap();
        assert_ne!(first.snapshot.workspace_id, second.snapshot.workspace_id);
        let mut db = SqliteEvaluationStore::open(&storage.join("evaluation.sqlite3")).unwrap();
        let summary = db
            .save_policy_comparison(&[
                PolicyComparisonTrial {
                    candidate: "baseline".into(),
                    repetition: 0,
                    evaluation: first.clone(),
                },
                PolicyComparisonTrial {
                    candidate: "candidate".into(),
                    repetition: 0,
                    evaluation: second,
                },
            ])
            .unwrap();
        assert!(summary.iter().all(|row| row.success_rate == Some(1.0)));
        // A subsequent offline refresh retains the terminal artifact snapshot.
        let stored = SqliteSessionRepository::new(&storage)
            .read(&first.snapshot.workspace_id, &first.snapshot.session_id)
            .unwrap();
        assert_eq!(
            db.resolve_run_snapshot(run_evidence_snapshots(&stored.events).pop().unwrap())
                .unwrap(),
            first.snapshot
        );
        assert_eq!(calls.load(Ordering::SeqCst), 4);
        server.abort();
        std::fs::remove_dir_all(root).unwrap();
    }
}
