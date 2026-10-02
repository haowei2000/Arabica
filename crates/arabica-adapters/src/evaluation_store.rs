//! SQLite projections on a dedicated worker. Runtime observers only try_send.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex, mpsc};
use std::time::Duration;

use arabica_protocol::{Event, EventEnvelope, SessionId, WorkspaceId};
use arabica_runtime::{
    ContextEvaluationResult, EvaluationConfig, EvaluationEvidence, EvaluationRegistry,
    ModelEvaluationResult,
};
use arabica_session::{EventVisibility, SessionEventObserver};
use rusqlite::{Connection, params};
use serde::{Deserialize, Serialize};

use crate::FileSessionStore;

type StoreResult<T> = Result<T, Box<dyn std::error::Error + Send + Sync>>;

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct EvaluationCheckpoint {
    pub workspace_id: WorkspaceId,
    pub session_id: SessionId,
    pub sequence: u64,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct SavedEvaluation {
    pub checkpoint: EvaluationCheckpoint,
    pub context: ContextEvaluationResult,
    pub model: ModelEvaluationResult,
}

/// Owned and used exclusively by evaluation workers, never runtime observers.
pub struct SqliteEvaluationStore(Connection);
impl SqliteEvaluationStore {
    pub fn open(path: &Path) -> StoreResult<Self> {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            std::fs::OpenOptions::new()
                .create(true)
                .truncate(false)
                .read(true)
                .write(true)
                .mode(0o600)
                .open(path)?;
        }
        let connection = Connection::open(path)?;
        connection.busy_timeout(Duration::from_millis(250))?;
        let version: i64 = connection.query_row("PRAGMA user_version", [], |row| row.get(0))?;
        if !matches!(version, 0 | 1) {
            return Err("unsupported evaluation database schema".into());
        }
        connection.execute_batch("PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON; BEGIN IMMEDIATE;
            CREATE TABLE IF NOT EXISTS evaluation_schema(version INTEGER PRIMARY KEY);
            INSERT OR IGNORE INTO evaluation_schema VALUES(1);
            CREATE TABLE IF NOT EXISTS evaluation_strategies(
                kind TEXT NOT NULL, id TEXT NOT NULL, version INTEGER NOT NULL,
                config_fingerprint TEXT NOT NULL, config_json TEXT NOT NULL,
                PRIMARY KEY(kind,id,version,config_fingerprint,config_json));
            CREATE TABLE IF NOT EXISTS evaluation_policies(
                kind TEXT NOT NULL, fingerprint TEXT NOT NULL, snapshot_json TEXT NOT NULL,
                PRIMARY KEY(kind,fingerprint,snapshot_json));
            CREATE TABLE IF NOT EXISTS evaluation_results(
                workspace_id TEXT NOT NULL, session_id TEXT NOT NULL, sequence INTEGER NOT NULL,
                evidence_fingerprint TEXT NOT NULL, context_id TEXT NOT NULL, context_version INTEGER NOT NULL,
                context_config TEXT NOT NULL, model_id TEXT NOT NULL, model_version INTEGER NOT NULL,
                model_config TEXT NOT NULL, result_json TEXT NOT NULL, evaluation_config_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(workspace_id,session_id,sequence,evidence_fingerprint,context_id,context_version,context_config,model_id,model_version,model_config));
            CREATE INDEX IF NOT EXISTS evaluation_latest ON evaluation_results(workspace_id,session_id,sequence DESC); PRAGMA user_version=1; COMMIT;")?;
        Ok(Self(connection))
    }
    pub fn save(
        &mut self,
        saved: &SavedEvaluation,
        config: &EvaluationConfig,
        evidence: &EvaluationEvidence,
    ) -> StoreResult<()> {
        let tx = self.0.transaction()?;
        let config_json = serde_json::to_string(config)?;
        for (kind, plugin) in [
            ("context", &saved.context.plugin),
            ("model", &saved.model.plugin),
        ] {
            tx.execute(
                "INSERT OR IGNORE INTO evaluation_strategies VALUES(?1,?2,?3,?4,?5)",
                params![
                    kind,
                    plugin.id,
                    plugin.version,
                    plugin.configuration_fingerprint,
                    config_json
                ],
            )?;
        }
        for envelope in evidence.events() {
            let policy = match &envelope.event {
                Event::ContextRunResolved { snapshot } => Some((
                    "context",
                    snapshot.policy_fingerprint.clone(),
                    serde_json::to_string(snapshot)?,
                )),
                Event::ModelRouteSelected {
                    policy_fingerprint,
                    model_registry_snapshot,
                    policy_id,
                    policy_version,
                    ..
                } => Some((
                    "model",
                    policy_fingerprint.clone(),
                    serde_json::to_string(&(policy_id, policy_version, model_registry_snapshot))?,
                )),
                _ => None,
            };
            if let Some((kind, fingerprint, snapshot)) = policy {
                tx.execute(
                    "INSERT OR IGNORE INTO evaluation_policies VALUES(?1,?2,?3)",
                    params![kind, fingerprint, snapshot],
                )?;
            }
        }
        tx.execute("INSERT OR IGNORE INTO evaluation_results(workspace_id,session_id,sequence,evidence_fingerprint,context_id,context_version,context_config,model_id,model_version,model_config,result_json,evaluation_config_json) VALUES(?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11,?12)", params![saved.checkpoint.workspace_id.0.as_str(), saved.checkpoint.session_id.0.as_str(), saved.checkpoint.sequence, saved.context.evidence_fingerprint, saved.context.plugin.id, saved.context.plugin.version, saved.context.plugin.configuration_fingerprint, saved.model.plugin.id, saved.model.plugin.version, saved.model.plugin.configuration_fingerprint, serde_json::to_string(saved)?, config_json])?;
        tx.commit()?;
        Ok(())
    }
    pub fn latest(
        &self,
        workspace: &WorkspaceId,
        session: &SessionId,
    ) -> StoreResult<Option<SavedEvaluation>> {
        let mut statement = self.0.prepare("SELECT result_json FROM evaluation_results WHERE workspace_id=?1 AND session_id=?2 ORDER BY sequence DESC,rowid DESC LIMIT 1")?;
        let mut rows = statement.query(params![workspace.0.as_str(), session.0.as_str()])?;
        rows.next()?
            .map(|row| -> StoreResult<SavedEvaluation> {
                Ok(serde_json::from_str(&row.get::<_, String>(0)?)?)
            })
            .transpose()
    }
}

#[derive(Clone, Debug, Default)]
pub struct EvaluationWorkerStatus {
    pub submitted: u64,
    pub dropped: u64,
    pub completed: u64,
    pub failed: u64,
}
#[derive(Default)]
struct WorkerState {
    submitted: AtomicU64,
    dropped: AtomicU64,
    completed: AtomicU64,
    failed: AtomicU64,
    latest: Mutex<BTreeMap<(WorkspaceId, SessionId), SavedEvaluation>>,
}

impl WorkerState {
    fn publish(&self, saved: SavedEvaluation) -> StoreResult<()> {
        let mut latest = self
            .latest
            .lock()
            .map_err(|_| "evaluation cache poisoned")?;
        let key = (
            saved.checkpoint.workspace_id.clone(),
            saved.checkpoint.session_id.clone(),
        );
        if latest
            .get(&key)
            .is_none_or(|previous| previous.checkpoint.sequence <= saved.checkpoint.sequence)
        {
            latest.insert(key, saved);
        }
        Ok(())
    }
}

pub struct EvaluationWorker {
    sender: mpsc::SyncSender<EvaluationCheckpoint>,
    state: Arc<WorkerState>,
}
impl EvaluationWorker {
    /// Startup, file reads, config loading, evaluation and SQLite all run on
    /// this OS thread. Dropping the handle never joins or waits for its work.
    pub fn start(
        home: PathBuf,
        capacity: usize,
        load_config: impl Fn() -> Result<EvaluationConfig, String> + Send + 'static,
    ) -> std::io::Result<Self> {
        Self::start_with_registry(home, capacity, load_config, |config| {
            EvaluationRegistry::builtins(config).map_err(|error| error.to_string())
        })
    }

    pub fn start_with_registry(
        home: PathBuf,
        capacity: usize,
        load_config: impl Fn() -> Result<EvaluationConfig, String> + Send + 'static,
        build_registry: impl Fn(&EvaluationConfig) -> Result<EvaluationRegistry, String>
        + Send
        + 'static,
    ) -> std::io::Result<Self> {
        let (sender, receiver) = mpsc::sync_channel(capacity.max(1));
        let state = Arc::new(WorkerState::default());
        let worker_state = state.clone();
        std::thread::Builder::new()
            .name("arabica-evaluation".into())
            .spawn(move || {
                let process = |checkpoint: EvaluationCheckpoint| -> StoreResult<()> {
                    let mut db = SqliteEvaluationStore::open(&home.join("evaluation.sqlite3"))?;
                    if let Some(saved) =
                        db.latest(&checkpoint.workspace_id, &checkpoint.session_id)?
                    {
                        worker_state.publish(saved)?;
                    }
                    let mut stored = FileSessionStore::read_session(
                        &home,
                        &checkpoint.workspace_id,
                        &checkpoint.session_id,
                    )?;
                    stored
                        .events
                        .retain(|event| event.sequence <= checkpoint.sequence);
                    if stored.header.id != checkpoint.session_id
                        || stored.header.workspace_id != checkpoint.workspace_id
                        || !stored.events.iter().any(|event| {
                            event.sequence == checkpoint.sequence && terminal(&event.event)
                        })
                    {
                        return Err("evaluation checkpoint is not durably recorded".into());
                    }
                    let config = load_config().map_err(std::io::Error::other)?;
                    let evidence = EvaluationEvidence::from_history(&stored.events);
                    let registry = build_registry(&config).map_err(std::io::Error::other)?;
                    let saved = SavedEvaluation {
                        checkpoint: checkpoint.clone(),
                        context: registry.context(&config.context_strategy, &evidence)?,
                        model: registry.model(&config.model_strategy, &evidence)?,
                    };
                    db.save(&saved, &config, &evidence)?;
                    worker_state.publish(saved)?;
                    Ok(())
                };
                let evaluate = |checkpoint| {
                    let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                        process(checkpoint)
                    }));
                    if matches!(result, Ok(Ok(()))) {
                        worker_state.completed.fetch_add(1, Ordering::Relaxed);
                    } else {
                        worker_state.failed.fetch_add(1, Ordering::Relaxed);
                    }
                };
                // JSONL is the durable source: recover missed checkpoints after
                // queue overflow or process exit without a synchronous outbox write.
                if let Ok(listings) = FileSessionStore::list_sessions(&home, None) {
                    for listing in listings {
                        if let Ok(stored) = FileSessionStore::read(&listing.path)
                            && let Some(event) = stored
                                .events
                                .iter()
                                .rev()
                                .find(|event| terminal(&event.event))
                        {
                            evaluate(EvaluationCheckpoint {
                                workspace_id: event.workspace_id.clone(),
                                session_id: event.session_id.clone(),
                                sequence: event.sequence,
                            });
                        }
                    }
                }
                for checkpoint in receiver {
                    evaluate(checkpoint);
                }
            })?;
        Ok(Self { sender, state })
    }
    pub fn submit(&self, checkpoint: EvaluationCheckpoint) -> bool {
        match self.sender.try_send(checkpoint) {
            Ok(()) => {
                self.state.submitted.fetch_add(1, Ordering::Relaxed);
                true
            }
            Err(_) => {
                self.state.dropped.fetch_add(1, Ordering::Relaxed);
                false
            }
        }
    }
    /// Cache reads never wait behind evaluator work or SQLite locks.
    pub fn latest(&self, workspace: &WorkspaceId, session: &SessionId) -> Option<SavedEvaluation> {
        self.state
            .latest
            .try_lock()
            .ok()?
            .get(&(workspace.clone(), session.clone()))
            .cloned()
    }
    pub fn status(&self) -> EvaluationWorkerStatus {
        EvaluationWorkerStatus {
            submitted: self.state.submitted.load(Ordering::Relaxed),
            dropped: self.state.dropped.load(Ordering::Relaxed),
            completed: self.state.completed.load(Ordering::Relaxed),
            failed: self.state.failed.load(Ordering::Relaxed),
        }
    }
}
fn terminal(event: &Event) -> bool {
    matches!(
        event,
        Event::RunCompleted { .. } | Event::RunFailed { .. } | Event::RunCancelled
    )
}
impl SessionEventObserver for EvaluationWorker {
    fn observe(&self, envelope: &EventEnvelope, _visibility: EventVisibility) {
        if envelope.run_id.is_some() && terminal(&envelope.event) {
            self.submit(EvaluationCheckpoint {
                workspace_id: envelope.workspace_id.clone(),
                session_id: envelope.session_id.clone(),
                sequence: envelope.sequence,
            });
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::NewSession;
    use arabica_protocol::{CommandId, EventId, EventMetadata, RunId};
    use std::sync::atomic::AtomicBool;
    use std::time::{Instant, SystemTime, UNIX_EPOCH};

    fn home() -> PathBuf {
        static NEXT: AtomicU64 = AtomicU64::new(0);
        std::env::temp_dir().join(format!(
            "structure-evaluation-{}-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ))
    }
    fn event(sequence: u64, event: Event) -> EventEnvelope {
        EventEnvelope::new(
            EventMetadata {
                event_id: EventId::new(format!("event-{sequence}")),
                command_id: CommandId::new("command"),
                workspace_id: WorkspaceId::new("workspace"),
                session_id: SessionId::new("session"),
                run_id: Some(RunId::new("run")),
                sequence,
                occurred_at_ms: sequence,
            },
            event,
        )
    }
    fn events() -> Vec<EventEnvelope> {
        vec![
            event(
                1,
                Event::ModelRouteSelected {
                    model_step: 0,
                    decision_id: "decision".into(),
                    policy_id: "policy".into(),
                    policy_version: 1,
                    policy_fingerprint: "policy-fingerprint".into(),
                    model_registry_snapshot: "registry".into(),
                    model_alias: Some("default".into()),
                    reason: "SECRET".into(),
                },
            ),
            event(
                2,
                Event::RunCompleted {
                    output: Some("SECRET".into()),
                },
            ),
        ]
    }
    fn checkpoint() -> EvaluationCheckpoint {
        EvaluationCheckpoint {
            workspace_id: WorkspaceId::new("workspace"),
            session_id: SessionId::new("session"),
            sequence: 2,
        }
    }
    fn write_history(home: &Path) {
        let store = FileSessionStore::create(
            home,
            NewSession {
                session_id: &SessionId::new("session"),
                workspace_id: &WorkspaceId::new("workspace"),
                cwd: home,
                profile: None,
                instructions_sha256: None,
            },
        )
        .unwrap();
        for event in events() {
            store.observe(&event, EventVisibility::Internal);
        }
    }
    fn wait_for(mut predicate: impl FnMut() -> bool) {
        let until = Instant::now() + Duration::from_secs(5);
        while !predicate() {
            assert!(
                Instant::now() < until,
                "background evaluation did not finish"
            );
            std::thread::sleep(Duration::from_millis(10));
        }
    }
    #[test]
    fn sqlite_results_are_atomic_idempotent_and_survive_reopen() {
        let home = home();
        let path = home.join("evaluation.sqlite3");
        let config = EvaluationConfig::default();
        let evidence = EvaluationEvidence::from_history(&events());
        let registry = EvaluationRegistry::builtins(&config).unwrap();
        let saved = SavedEvaluation {
            checkpoint: checkpoint(),
            context: registry
                .context(&config.context_strategy, &evidence)
                .unwrap(),
            model: registry.model(&config.model_strategy, &evidence).unwrap(),
        };
        let mut db = SqliteEvaluationStore::open(&path).unwrap();
        db.save(&saved, &config, &evidence).unwrap();
        db.save(&saved, &config, &evidence).unwrap();
        let count: usize =
            db.0.query_row("SELECT count(*) FROM evaluation_results", [], |row| {
                row.get(0)
            })
            .unwrap();
        assert_eq!(count, 1);
        let strategies: usize =
            db.0.query_row("SELECT count(*) FROM evaluation_strategies", [], |row| {
                row.get(0)
            })
            .unwrap();
        assert_eq!(strategies, 2);
        let policies: usize =
            db.0.query_row("SELECT count(*) FROM evaluation_policies", [], |row| {
                row.get(0)
            })
            .unwrap();
        assert_eq!(policies, 1);
        assert!(!serde_json::to_string(&saved).unwrap().contains("SECRET"));
        drop(db);
        let db = SqliteEvaluationStore::open(&path).unwrap();
        assert_eq!(
            db.latest(&checkpoint().workspace_id, &checkpoint().session_id)
                .unwrap(),
            Some(saved)
        );
        drop(db);
        std::fs::remove_dir_all(home).unwrap();
    }
    #[test]
    fn unsupported_schema_is_rejected_without_modification() {
        let home = home();
        std::fs::create_dir_all(&home).unwrap();
        let path = home.join("evaluation.sqlite3");
        let connection = Connection::open(&path).unwrap();
        connection.execute_batch("PRAGMA user_version=2;").unwrap();
        assert!(SqliteEvaluationStore::open(&path).is_err());
        let count: usize = connection
            .query_row(
                "SELECT count(*) FROM sqlite_master WHERE type='table'",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(count, 0);
        drop(connection);
        std::fs::remove_dir_all(home).unwrap();
    }
    #[test]
    fn blocked_evaluator_and_full_queue_never_block_observer() {
        let home = home();
        write_history(&home);
        let (ready_tx, ready_rx) = mpsc::channel();
        let (release_tx, release_rx) = mpsc::channel();
        let first = AtomicBool::new(true);
        let worker = EvaluationWorker::start(home.clone(), 1, move || {
            if first.swap(false, Ordering::Relaxed) {
                ready_tx.send(()).unwrap();
                release_rx
                    .recv_timeout(Duration::from_secs(5))
                    .map_err(|_| "test evaluator timeout".to_string())?;
            }
            Ok(EvaluationConfig::default())
        })
        .unwrap();
        // Startup replay is already blocked inside the evaluator.
        ready_rx.recv_timeout(Duration::from_secs(5)).unwrap();
        worker.observe(&events()[1], EventVisibility::Internal);
        worker.observe(&events()[1], EventVisibility::Internal);
        assert_eq!(worker.status().submitted, 1);
        assert_eq!(worker.status().dropped, 1);
        assert!(
            worker
                .latest(&checkpoint().workspace_id, &checkpoint().session_id)
                .is_none()
        );
        release_tx.send(()).unwrap();
        wait_for(|| worker.status().completed == 2);
        assert_eq!(
            worker
                .latest(&checkpoint().workspace_id, &checkpoint().session_id)
                .unwrap()
                .checkpoint
                .sequence,
            2
        );
        drop(worker);
        std::fs::remove_dir_all(home).unwrap();
    }
    #[test]
    fn database_failure_is_confined_to_worker_and_replay_recovers() {
        let home = home();
        write_history(&home);
        std::fs::create_dir(home.join("evaluation.sqlite3")).unwrap();
        let worker =
            EvaluationWorker::start(home.clone(), 1, || Ok(EvaluationConfig::default())).unwrap();
        wait_for(|| worker.status().failed == 1);
        assert_eq!(worker.status().completed, 0);
        drop(worker);
        std::fs::remove_dir(home.join("evaluation.sqlite3")).unwrap();
        // No new runtime event is needed: startup replay recovers the job.
        let worker =
            EvaluationWorker::start(home.clone(), 1, || Ok(EvaluationConfig::default())).unwrap();
        wait_for(|| worker.status().completed == 1);
        assert!(
            worker
                .latest(&checkpoint().workspace_id, &checkpoint().session_id)
                .is_some()
        );
        drop(worker);
        std::fs::remove_dir_all(home).unwrap();
    }
}
