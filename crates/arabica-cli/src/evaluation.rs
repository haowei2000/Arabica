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
