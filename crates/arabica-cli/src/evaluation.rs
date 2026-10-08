//! Host composition for detached evaluation workers, shared per ARABICA_HOME.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex, OnceLock};

use arabica_adapters::EvaluationWorker;
use arabica_session::SessionEventObserver;

static WORKERS: OnceLock<Mutex<BTreeMap<PathBuf, Arc<EvaluationWorker>>>> = OnceLock::new();

pub(crate) fn worker(home: &Path) -> Option<Arc<EvaluationWorker>> {
    let mut workers = WORKERS.get_or_init(Default::default).lock().ok()?;
    if let Some(worker) = workers.get(home) {
        return Some(worker.clone());
    }
    let config_home = home.to_path_buf();
    let worker = Arc::new(
        EvaluationWorker::start(home.to_path_buf(), 64, move || {
            crate::config::user_config_evaluation(&config_home)
                .map_err(|_| "invalid evaluation configuration".into())
        })
        .ok()?,
    );
    workers.insert(home.to_path_buf(), worker.clone());
    Some(worker)
}

pub(crate) fn attach(observers: &mut Vec<Arc<dyn SessionEventObserver>>, home: &Path) {
    if let Some(worker) = worker(home) {
        observers.push(worker);
    }
}

#[derive(clap::Subcommand, Debug)]
pub enum EvaluationAction {
    /// Freeze and inspect per-run timing and acceptance evidence for a session.
    Runs {
        session_id: String,
        #[arg(long)]
        workspace_id: Option<String>,
        #[arg(long)]
        json: bool,
    },
    /// Score immutable terminal evidence using an explicit JSON acceptance spec.
    Score {
        session_id: String,
        run_id: String,
        #[arg(long)]
        specification: PathBuf,
        #[arg(long)]
        workspace_id: Option<String>,
        #[arg(long, default_value = "evidence-checks")]
        scorer: String,
        #[arg(long)]
        json: bool,
    },
    /// Compare actual scored trials listed in a JSON manifest; never replays
    /// one policy's history as if a different policy had executed it.
    Compare {
        #[arg(long)]
        manifest: PathBuf,
        #[arg(long)]
        json: bool,
    },
    /// Read the most recently persisted report without starting an Agent.
    Show {
        session_id: String,
        /// Workspace defaults to the current directory's workspace.
        #[arg(long)]
        workspace_id: Option<String>,
        #[arg(long)]
        json: bool,
    },
    /// Inspect persisted policy versions and generated candidates.
    Policies {
        #[arg(long)]
        policy_id: Option<String>,
        #[arg(long)]
        json: bool,
    },
    /// Evaluate a session offline and generate an evolved candidate policy version if warranted.
    Evolve {
        session_id: String,
        #[arg(long)]
        workspace_id: Option<String>,
        #[arg(long)]
        json: bool,
    },
    /// Run the decoupled evaluation worker as an independent background daemon process.
    Daemon {
        #[arg(long, default_value_t = 64)]
        capacity: usize,
    },
}

pub async fn run(action: EvaluationAction) -> i32 {
    match action {
        EvaluationAction::Runs {
            session_id,
            workspace_id,
            json,
        } => run_quality(session_id, workspace_id, None, json),
        EvaluationAction::Score {
            session_id,
            run_id,
            specification,
            workspace_id,
            scorer,
            json,
        } => run_quality(
            session_id,
            workspace_id,
            Some((run_id, specification, scorer)),
            json,
        ),
        EvaluationAction::Compare { manifest, json } => run_compare(manifest, json),
        EvaluationAction::Show {
            session_id,
            workspace_id,
            json,
        } => run_show(session_id, workspace_id, json),
        EvaluationAction::Policies { policy_id, json } => run_policies(policy_id, json),
        EvaluationAction::Evolve {
            session_id,
            workspace_id,
            json,
        } => run_evolve(session_id, workspace_id, json),
        EvaluationAction::Daemon { capacity } => run_daemon(capacity).await,
    }
}

type QualityResult<T> = Result<T, Box<dyn std::error::Error + Send + Sync>>;

fn bounded_json<T: serde::de::DeserializeOwned>(path: &Path) -> QualityResult<T> {
    use std::io::Read;
    let file = std::fs::File::open(path)?;
    let mut bytes = Vec::new();
    file.take(1024 * 1024 + 1).read_to_end(&mut bytes)?;
    if bytes.len() > 1024 * 1024 {
        return Err("evaluation input exceeds one MiB".into());
    }
    Ok(serde_json::from_slice(&bytes)?)
}

fn workspace_identity(id: Option<String>) -> QualityResult<arabica_protocol::WorkspaceId> {
    Ok(match id {
        Some(id) => arabica_protocol::WorkspaceId::new(id),
        None => crate::host::workspace_id_for(&std::env::current_dir()?),
    })
}

fn session_runs(
    home: &Path,
    workspace: &arabica_protocol::WorkspaceId,
    session: &arabica_protocol::SessionId,
    db: &mut arabica_adapters::SqliteEvaluationStore,
) -> QualityResult<Vec<arabica_runtime::RunEvaluation>> {
    use arabica_session::SessionStore;
    let stored = arabica_adapters::SqliteSessionRepository::new(home).read(workspace, session)?;
    let mut runs = Vec::new();
    for snapshot in arabica_runtime::run_evidence_snapshots(&stored.events) {
        let snapshot = db.resolve_run_snapshot(snapshot)?;
        let acceptance = db.latest_acceptance(&snapshot)?;
        runs.push(arabica_runtime::RunEvaluation {
            snapshot,
            acceptance,
        });
    }
    Ok(runs)
}

fn print_quality<T: serde::Serialize>(result: QualityResult<T>, json: bool) -> i32 {
    match result {
        Ok(value) => {
            let encoded = if json {
                serde_json::to_string(&value)
            } else {
                serde_json::to_string_pretty(&value)
            };
            match encoded {
                Ok(encoded) => {
                    println!("{encoded}");
                    0
                }
                Err(_) => {
                    eprintln!("error: could not serialize evaluation report");
                    1
                }
            }
        }
        Err(error) => {
            eprintln!("error: run evaluation failed: {error}");
            1
        }
    }
}

fn run_quality(
    session: String,
    workspace: Option<String>,
    score: Option<(String, PathBuf, String)>,
    json: bool,
) -> i32 {
    let result = (|| -> QualityResult<Vec<arabica_runtime::RunEvaluation>> {
        let home = arabica_adapters::default_arabica_home()?;
        let workspace = workspace_identity(workspace)?;
        let session = arabica_protocol::SessionId::new(session);
        let mut db =
            arabica_adapters::SqliteEvaluationStore::open(&home.join("evaluation.sqlite3"))?;
        let mut runs = session_runs(&home, &workspace, &session, &mut db)?;
        if let Some((run_id, path, scorer)) = score {
            let specification: arabica_runtime::AcceptanceSpec = bounded_json(&path)?;
            let run = runs
                .iter_mut()
                .find(|run| run.snapshot.run_id.0 == run_id)
                .ok_or("terminal run not found in this session")?;
            let registry = arabica_runtime::EvaluationRegistry::builtins(
                &arabica_runtime::EvaluationConfig::default(),
            )?;
            let acceptance = registry.acceptance(&scorer, &run.snapshot, &specification)?;
            db.save_acceptance(&run.snapshot, &specification, &acceptance)?;
            run.acceptance = Some(acceptance);
            runs.retain(|run| run.snapshot.run_id.0 == run_id);
        }
        Ok(runs)
    })();
    print_quality(result, json)
}

#[derive(serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct ComparisonManifest {
    trials: Vec<ComparisonReference>,
}

#[derive(serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct ComparisonReference {
    candidate: String,
    repetition: u32,
    workspace_id: String,
    session_id: String,
    run_id: String,
}

fn run_compare(manifest: PathBuf, json: bool) -> i32 {
    let result = (|| -> QualityResult<Vec<arabica_runtime::PolicyComparisonSummary>> {
        let manifest: ComparisonManifest = bounded_json(&manifest)?;
        let home = arabica_adapters::default_arabica_home()?;
        let mut db =
            arabica_adapters::SqliteEvaluationStore::open(&home.join("evaluation.sqlite3"))?;
        let mut trials = Vec::new();
        for reference in manifest.trials {
            let workspace = arabica_protocol::WorkspaceId::new(reference.workspace_id);
            let session = arabica_protocol::SessionId::new(reference.session_id);
            let evaluation = session_runs(&home, &workspace, &session, &mut db)?
                .into_iter()
                .find(|run| run.snapshot.run_id.0 == reference.run_id)
                .ok_or("comparison terminal run not found")?;
            trials.push(arabica_runtime::PolicyComparisonTrial {
                candidate: reference.candidate,
                repetition: reference.repetition,
                evaluation,
            });
        }
        db.save_policy_comparison(&trials)
    })();
    print_quality(result, json)
}

fn run_show(session_id: String, workspace_id: Option<String>, json: bool) -> i32 {
    let result = (|| -> Result<arabica_adapters::EvaluationView, Box<dyn std::error::Error + Send + Sync>> {
        let home = arabica_adapters::default_arabica_home()?;
        let workspace = match workspace_id {
            Some(id) => arabica_protocol::WorkspaceId::new(id),
            None => crate::host::workspace_id_for(&std::env::current_dir()?),
        };
        let session = arabica_protocol::SessionId::new(session_id);
        let report = arabica_adapters::SqliteEvaluationStore::read_latest(&home.join("evaluation.sqlite3"), &workspace, &session)?;
        let policies = arabica_adapters::SqliteEvaluationStore::read_policy_versions(&home.join("evaluation.sqlite3"), None).unwrap_or_default();
        Ok(arabica_adapters::EvaluationView { schema_version: 1, workspace_id: workspace, session_id: session, worker_status: None, refresh_accepted: None, report, policies })
    })();
    match result {
        Ok(view) => {
            if json {
                println!("{}", serde_json::to_string(&view).expect("evaluation view"));
            } else if let Some(report) = view.report {
                println!(
                    "Evaluation checkpoint: {} · context: {} v{} · model: {} v{}",
                    report.checkpoint.sequence,
                    report.context.plugin.id,
                    report.context.plugin.version,
                    report.model.plugin.id,
                    report.model.plugin.version
                );
                println!(
                    "{}",
                    serde_json::to_string_pretty(&report).expect("evaluation report")
                );
            } else {
                println!(
                    "No persisted evaluation for session {}. Agent execution is unaffected.",
                    view.session_id
                );
            }
            0
        }
        Err(_) => {
            eprintln!("error: evaluation database unavailable or incompatible");
            1
        }
    }
}

fn run_policies(policy_id: Option<String>, json: bool) -> i32 {
    let result = (|| -> Result<Vec<arabica_runtime::GeneratedPolicyRecord>, Box<dyn std::error::Error + Send + Sync>> {
        let home = arabica_adapters::default_arabica_home()?;
        let path = home.join("evaluation.sqlite3");
        arabica_adapters::SqliteEvaluationStore::read_policy_versions(&path, policy_id.as_deref())
    })();
    match result {
        Ok(policies) => {
            if json {
                println!(
                    "{}",
                    serde_json::to_string(&policies).expect("policies json")
                );
            } else if policies.is_empty() {
                println!("No policy versions recorded.");
            } else {
                for p in policies {
                    println!(
                        "Policy: {} v{} [{}] (default model: {})\n  Reason: {}\n",
                        p.policy_id, p.version, p.status, p.policy.default_model, p.reason
                    );
                }
            }
            0
        }
        Err(_) => {
            eprintln!("error: evaluation database unavailable or incompatible");
            1
        }
    }
}

fn run_evolve(session_id: String, workspace_id: Option<String>, json: bool) -> i32 {
    let result = (|| -> Result<Vec<arabica_runtime::GeneratedPolicyRecord>, Box<dyn std::error::Error + Send + Sync>> {
        let home = arabica_adapters::default_arabica_home()?;
        let workspace = match workspace_id {
            Some(id) => arabica_protocol::WorkspaceId::new(id),
            None => crate::host::workspace_id_for(&std::env::current_dir()?),
        };
        let session = arabica_protocol::SessionId::new(session_id.clone());
        let sessions = arabica_adapters::SqliteSessionRepository::new(&home);
        use arabica_session::SessionStore;
        let stored = sessions.read(&workspace, &session)?;
        let config = crate::config::user_config_evaluation(&home).unwrap_or_default();
        let evidence = arabica_runtime::EvaluationEvidence::from_history(&stored.events);
        let registry = arabica_runtime::EvaluationRegistry::builtins(&config)?;
        let model_eval = registry.model(&config.model_strategy, &evidence)?;

        let mut db = arabica_adapters::SqliteEvaluationStore::open(&home.join("evaluation.sqlite3"))?;
        let mut candidate_bases = db.list_policy_versions(None)?;
        if candidate_bases.is_empty()
            && let Ok(policies) =
                crate::config::user_config_blend_policies(&home, &std::env::current_dir()?)
        {
            for (_id, policy) in policies {
                let seed = arabica_runtime::GeneratedPolicyRecord {
                    policy_id: policy.policy_id.clone(),
                    version: policy.version,
                    status: "active".into(),
                    policy: policy.clone(),
                    basis_fingerprint: None,
                    reason: "configured base policy".into(),
                };
                let _ = db.save_policy_version(&seed);
                candidate_bases.push(seed);
            }
        }

        let mut evolved_records = Vec::new();
        for group in &model_eval.data.groups {
            for base in &candidate_bases {
                if let Some(evolved) = arabica_runtime::evolve_blend_policy(
                    &base.policy,
                    &group.report,
                    Some(evidence.fingerprint.clone()),
                ) {
                    db.save_policy_version(&evolved)?;
                    evolved_records.push(evolved);
                }
            }
        }
        Ok(evolved_records)
    })();
    match result {
        Ok(evolved_records) => {
            if json {
                println!(
                    "{}",
                    serde_json::to_string(&evolved_records).expect("evolved json")
                );
            } else if evolved_records.is_empty() {
                println!("Session evaluation complete. No policy evolution candidate generated.");
            } else {
                for rec in evolved_records {
                    println!(
                        "Generated policy candidate: {} v{} [{}]",
                        rec.policy_id, rec.version, rec.status
                    );
                    println!("  Reason: {}", rec.reason);
                }
            }
            0
        }
        Err(e) => {
            eprintln!("error: offline policy evolution failed: {e}");
            1
        }
    }
}

async fn run_daemon(capacity: usize) -> i32 {
    let home = match arabica_adapters::default_arabica_home() {
        Ok(h) => h,
        Err(_) => {
            eprintln!("error: could not resolve ARABICA_HOME");
            return 1;
        }
    };
    println!(
        "Arabica evaluation daemon running (capacity: {}, home: {})...",
        capacity,
        home.display()
    );
    if worker(&home).is_none() {
        eprintln!("error: failed to start background evaluation worker");
        return 1;
    }
    println!("Evaluation daemon active. Press Ctrl+C to stop.");
    let _ = tokio::signal::ctrl_c().await;
    println!("Evaluation daemon stopped.");
    0
}

pub(crate) fn view(
    home: &Path,
    workspace: arabica_protocol::WorkspaceId,
    session: arabica_protocol::SessionId,
    refresh: bool,
) -> arabica_adapters::EvaluationView {
    let worker = worker(home);
    let refresh_accepted = refresh.then(|| {
        worker
            .as_ref()
            .is_some_and(|worker| worker.refresh(workspace.clone(), session.clone()))
    });
    let report = worker
        .as_ref()
        .and_then(|worker| worker.latest(&workspace, &session));
    let worker_status = worker.as_ref().map(|worker| worker.status());
    let policies = arabica_adapters::SqliteEvaluationStore::read_policy_versions(
        &home.join("evaluation.sqlite3"),
        None,
    )
    .unwrap_or_default();
    arabica_adapters::EvaluationView {
        schema_version: 1,
        workspace_id: workspace,
        session_id: session,
        worker_status,
        refresh_accepted,
        report,
        policies,
    }
}
