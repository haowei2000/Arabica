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
    /// Read the most recently persisted report without starting an Agent.
    Show {
        session_id: String,
        /// Workspace defaults to the current directory's workspace.
        #[arg(long)]
        workspace_id: Option<String>,
        #[arg(long)]
        json: bool,
    },
}

pub fn run(action: EvaluationAction) -> i32 {
    let EvaluationAction::Show {
        session_id,
        workspace_id,
        json,
    } = action;
    let result = (|| -> Result<arabica_adapters::EvaluationView, Box<dyn std::error::Error + Send + Sync>> {
        let home = arabica_adapters::default_arabica_home()?;
        let workspace = match workspace_id {
            Some(id) => arabica_protocol::WorkspaceId::new(id),
            None => crate::host::workspace_id_for(&std::env::current_dir()?),
        };
        let session = arabica_protocol::SessionId::new(session_id);
        let report = arabica_adapters::SqliteEvaluationStore::read_latest(&home.join("evaluation.sqlite3"), &workspace, &session)?;
        Ok(arabica_adapters::EvaluationView { schema_version: 1, workspace_id: workspace, session_id: session, worker_status: None, refresh_accepted: None, report })
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
    arabica_adapters::EvaluationView {
        schema_version: 1,
        workspace_id: workspace,
        session_id: session,
        worker_status,
        refresh_accepted,
        report,
    }
}
