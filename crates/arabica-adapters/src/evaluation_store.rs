//! SQLite projections on a dedicated worker. Runtime observers only try_send.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex, mpsc};
use std::time::Duration;

use arabica_protocol::{Event, EventEnvelope, SessionId, WorkspaceId};
use arabica_runtime::{
    AcceptanceSpec, BlendRoutingPolicy, ContextEvaluationResult, EvaluationConfig,
    EvaluationEvidence, EvaluationRegistry, GeneratedPolicyRecord, ModelEvaluationResult,
    RunAcceptance, RunEvaluation, RunEvidenceSnapshot, evolve_blend_policy, run_evidence_snapshots,
};
use arabica_session::{EventVisibility, SessionEventObserver, SessionStore};
use rusqlite::{Connection, params};
use serde::{Deserialize, Serialize};

use crate::SqliteSessionRepository;

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
    #[serde(default)]
    pub runs: Vec<RunEvaluation>,
}

/// Owned and used exclusively by evaluation workers, never runtime observers.
pub struct SqliteEvaluationStore(Connection);
impl SqliteEvaluationStore {
    /// Pure query: never creates a database, migrates it, or runs evaluation.
    pub fn read_latest(
        path: &Path,
        workspace: &WorkspaceId,
        session: &SessionId,
    ) -> StoreResult<Option<SavedEvaluation>> {
        if !path.exists() {
            return Ok(None);
        }
        let connection =
            Connection::open_with_flags(path, rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY)?;
        connection.busy_timeout(Duration::from_millis(50))?;
        let version: i64 = connection.query_row("PRAGMA user_version", [], |row| row.get(0))?;
        if !matches!(version, 1 | 2) {
            return Err("unsupported evaluation database schema".into());
        }
        Self(connection).latest(workspace, session)
    }
    /// Pure query for persisted policy versions without modifying database or taking write locks.
    pub fn read_policy_versions(
        path: &Path,
        policy_id: Option<&str>,
    ) -> StoreResult<Vec<GeneratedPolicyRecord>> {
        if !path.exists() {
            return Ok(Vec::new());
        }
        let connection =
            Connection::open_with_flags(path, rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY)?;
        connection.busy_timeout(Duration::from_millis(50))?;
        let version: i64 = connection.query_row("PRAGMA user_version", [], |row| row.get(0))?;
        if !matches!(version, 1 | 2) {
            return Err("unsupported evaluation database schema".into());
        }
        Self(connection).list_policy_versions(policy_id)
    }
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
        if !matches!(version, 0..=2) {
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
            CREATE TABLE IF NOT EXISTS policy_versions(
                policy_id TEXT NOT NULL, version INTEGER NOT NULL, status TEXT NOT NULL,
                policy_json TEXT NOT NULL, basis_evidence_fingerprint TEXT, reason TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(policy_id, version));
            CREATE TABLE IF NOT EXISTS run_evidence_snapshots(
                workspace_id TEXT NOT NULL, session_id TEXT NOT NULL, run_id TEXT NOT NULL,
                fingerprint TEXT NOT NULL, snapshot_json TEXT NOT NULL,
                PRIMARY KEY(workspace_id,session_id,run_id));
            CREATE TABLE IF NOT EXISTS policy_comparisons(
                fingerprint TEXT PRIMARY KEY, trials_json TEXT NOT NULL, report_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS run_acceptance_results(
                workspace_id TEXT NOT NULL, session_id TEXT NOT NULL, run_id TEXT NOT NULL,
                snapshot_fingerprint TEXT NOT NULL, spec_fingerprint TEXT NOT NULL,
                scorer_id TEXT NOT NULL, scorer_version INTEGER NOT NULL, scorer_config TEXT NOT NULL,
                spec_json TEXT NOT NULL, result_json TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(workspace_id,session_id,run_id,snapshot_fingerprint,spec_fingerprint,scorer_id,scorer_version,scorer_config));
            CREATE INDEX IF NOT EXISTS evaluation_latest ON evaluation_results(workspace_id,session_id,sequence DESC); PRAGMA user_version=2; COMMIT;")?;
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
        tx.execute("INSERT OR IGNORE INTO evaluation_results(workspace_id,session_id,sequence,evidence_fingerprint,context_id,context_version,context_config,model_id,model_version,model_config,result_json,evaluation_config_json) VALUES(?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11,?12) ON CONFLICT DO UPDATE SET result_json=excluded.result_json", params![saved.checkpoint.workspace_id.0.as_str(), saved.checkpoint.session_id.0.as_str(), saved.checkpoint.sequence, saved.context.evidence_fingerprint, saved.context.plugin.id, saved.context.plugin.version, saved.context.plugin.configuration_fingerprint, saved.model.plugin.id, saved.model.plugin.version, saved.model.plugin.configuration_fingerprint, serde_json::to_string(saved)?, config_json])?;
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
                let mut saved: SavedEvaluation = serde_json::from_str(&row.get::<_, String>(0)?)?;
                for run in &mut saved.runs {
                    run.acceptance = self.latest_acceptance(&run.snapshot)?;
                }
                Ok(saved)
            })
            .transpose()
    }
    pub fn save_policy_version(&mut self, record: &GeneratedPolicyRecord) -> StoreResult<()> {
        let policy_json = serde_json::to_string(&record.policy)?;
        self.0.execute(
            "INSERT INTO policy_versions(policy_id, version, status, policy_json, basis_evidence_fingerprint, reason)
             VALUES(?1, ?2, ?3, ?4, ?5, ?6)
             ON CONFLICT(policy_id, version) DO UPDATE SET
             status = excluded.status,
             policy_json = excluded.policy_json,
             basis_evidence_fingerprint = excluded.basis_evidence_fingerprint,
             reason = excluded.reason",
            params![
                record.policy_id,
                record.version as i64,
                record.status,
                policy_json,
                record.basis_fingerprint,
                record.reason,
            ],
        )?;
        Ok(())
    }
    /// Freeze the first bounded snapshot for a run; refresh never overwrites it.
    pub fn save_run_snapshot(&mut self, snapshot: &RunEvidenceSnapshot) -> StoreResult<()> {
        let encoded = serde_json::to_string(snapshot)?;
        let tx = self.0.transaction()?;
        tx.execute(
            "INSERT OR IGNORE INTO run_evidence_snapshots VALUES(?1,?2,?3,?4,?5)",
            params![
                snapshot.workspace_id.0,
                snapshot.session_id.0,
                snapshot.run_id.0,
                snapshot.fingerprint,
                encoded
            ],
        )?;
        let stored: String = tx.query_row("SELECT snapshot_json FROM run_evidence_snapshots WHERE workspace_id=?1 AND session_id=?2 AND run_id=?3",
            params![snapshot.workspace_id.0, snapshot.session_id.0, snapshot.run_id.0], |row| row.get(0))?;
        if stored != encoded {
            return Err("immutable run evidence mismatch".into());
        }
        tx.commit()?;
        Ok(())
    }

    /// Reuse artifact evidence captured synchronously by an experiment host.
    pub fn resolve_run_snapshot(
        &mut self,
        mut snapshot: RunEvidenceSnapshot,
    ) -> StoreResult<RunEvidenceSnapshot> {
        let snapshot = if let Some(existing) = self.run_snapshot(
            &snapshot.workspace_id,
            &snapshot.session_id,
            &snapshot.run_id,
        )? {
            snapshot.fixture_fingerprint = existing.fixture_fingerprint;
            snapshot.with_artifacts(existing.artifacts)?
        } else {
            snapshot
        };
        self.save_run_snapshot(&snapshot)?;
        Ok(snapshot)
    }

    pub fn save_policy_comparison(
        &mut self,
        trials: &[arabica_runtime::PolicyComparisonTrial],
    ) -> StoreResult<Vec<arabica_runtime::PolicyComparisonSummary>> {
        use sha2::{Digest, Sha256};
        let report = arabica_runtime::compare_policy_trials(trials)?;
        let encoded = serde_json::to_string(trials)?;
        let fingerprint = hex::encode(Sha256::digest(encoded.as_bytes()));
        self.0.execute("INSERT OR IGNORE INTO policy_comparisons(fingerprint,trials_json,report_json) VALUES(?1,?2,?3)", params![fingerprint, encoded, serde_json::to_string(&report)?])?;
        Ok(report)
    }

    pub fn run_snapshot(
        &self,
        workspace: &WorkspaceId,
        session: &SessionId,
        run: &arabica_protocol::RunId,
    ) -> StoreResult<Option<RunEvidenceSnapshot>> {
        let mut statement = self.0.prepare("SELECT snapshot_json FROM run_evidence_snapshots WHERE workspace_id=?1 AND session_id=?2 AND run_id=?3")?;
        let mut rows = statement.query(params![workspace.0, session.0, run.0])?;
        rows.next()?
            .map(|row| -> StoreResult<_> { Ok(serde_json::from_str(&row.get::<_, String>(0)?)?) })
            .transpose()
    }

    /// Append a versioned score bound to its exact specification and snapshot.
    pub fn save_acceptance(
        &mut self,
        snapshot: &RunEvidenceSnapshot,
        specification: &AcceptanceSpec,
        acceptance: &RunAcceptance,
    ) -> StoreResult<()> {
        specification.validate()?;
        if acceptance.snapshot_fingerprint != snapshot.fingerprint
            || acceptance.specification_fingerprint != specification.fingerprint()
            || acceptance.task_id != specification.task_id
        {
            return Err("acceptance provenance mismatch".into());
        }
        self.save_run_snapshot(snapshot)?;
        self.0.execute("INSERT OR IGNORE INTO run_acceptance_results(workspace_id,session_id,run_id,snapshot_fingerprint,spec_fingerprint,scorer_id,scorer_version,scorer_config,spec_json,result_json) VALUES(?1,?2,?3,?4,?5,?6,?7,?8,?9,?10)",
            params![snapshot.workspace_id.0, snapshot.session_id.0, snapshot.run_id.0, snapshot.fingerprint, acceptance.specification_fingerprint, acceptance.scorer.id, acceptance.scorer.version, acceptance.scorer.configuration_fingerprint, serde_json::to_string(specification)?, serde_json::to_string(acceptance)?])?;
        Ok(())
    }

    pub fn latest_acceptance(
        &self,
        snapshot: &RunEvidenceSnapshot,
    ) -> StoreResult<Option<RunAcceptance>> {
        let mut statement = self.0.prepare("SELECT result_json FROM run_acceptance_results WHERE workspace_id=?1 AND session_id=?2 AND run_id=?3 AND snapshot_fingerprint=?4 ORDER BY rowid DESC LIMIT 1")?;
        let mut rows = statement.query(params![
            snapshot.workspace_id.0,
            snapshot.session_id.0,
            snapshot.run_id.0,
            snapshot.fingerprint
        ])?;
        rows.next()?
            .map(|row| -> StoreResult<_> { Ok(serde_json::from_str(&row.get::<_, String>(0)?)?) })
            .transpose()
    }
    pub fn list_policy_versions(
        &self,
        policy_id: Option<&str>,
    ) -> StoreResult<Vec<GeneratedPolicyRecord>> {
        let mut sql = "SELECT policy_id, version, status, policy_json, basis_evidence_fingerprint, reason FROM policy_versions".to_string();
        if policy_id.is_some() {
            sql.push_str(" WHERE policy_id = ?1");
        }
        sql.push_str(" ORDER BY policy_id ASC, version DESC");
        let mut stmt = self.0.prepare(&sql)?;
        let rows = if let Some(id) = policy_id {
            stmt.query(params![id])?
        } else {
            stmt.query([])?
        };
        let mapped = rows.and_then(|row| {
            let policy_id: String = row.get(0)?;
            let version: i64 = row.get(1)?;
            let status: String = row.get(2)?;
            let policy_json: String = row.get(3)?;
            let basis_evidence_fingerprint: Option<String> = row.get(4)?;
            let reason: String = row.get(5)?;
            let policy: BlendRoutingPolicy = serde_json::from_str(&policy_json).map_err(|e| {
                rusqlite::Error::FromSqlConversionFailure(
                    0,
                    rusqlite::types::Type::Text,
                    Box::new(e),
                )
            })?;
            Ok(GeneratedPolicyRecord {
                policy_id,
                version: version as u64,
                status,
                policy,
                basis_fingerprint: basis_evidence_fingerprint,
                reason,
            })
        });
        let results: Result<Vec<_>, rusqlite::Error> = mapped.collect();
        Ok(results?)
    }
    pub fn get_policy_version(
        &self,
        policy_id: &str,
        version: u64,
    ) -> StoreResult<Option<GeneratedPolicyRecord>> {
        let mut stmt = self.0.prepare(
            "SELECT policy_id, version, status, policy_json, basis_evidence_fingerprint, reason FROM policy_versions WHERE policy_id = ?1 AND version = ?2 LIMIT 1",
        )?;
        let mut rows = stmt.query(params![policy_id, version as i64])?;
        if let Some(row) = rows.next()? {
            let policy_id: String = row.get(0)?;
            let version: i64 = row.get(1)?;
            let status: String = row.get(2)?;
            let policy_json: String = row.get(3)?;
            let basis_evidence_fingerprint: Option<String> = row.get(4)?;
            let reason: String = row.get(5)?;
            let policy: BlendRoutingPolicy = serde_json::from_str(&policy_json).map_err(|e| {
                rusqlite::Error::FromSqlConversionFailure(
                    0,
                    rusqlite::types::Type::Text,
                    Box::new(e),
                )
            })?;
            Ok(Some(GeneratedPolicyRecord {
                policy_id,
                version: version as u64,
                status,
                policy,
                basis_fingerprint: basis_evidence_fingerprint,
                reason,
            }))
        } else {
            Ok(None)
        }
    }
}

#[derive(Clone, Debug, Default, Serialize, Deserialize, Eq, PartialEq)]
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
    sender: mpsc::SyncSender<EvaluationJob>,
    state: Arc<WorkerState>,
}

enum EvaluationJob {
    Checkpoint(EvaluationCheckpoint),
    Refresh {
        workspace: WorkspaceId,
        session: SessionId,
    },
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct EvaluationView {
    pub schema_version: u64,
    pub workspace_id: WorkspaceId,
    pub session_id: SessionId,
    pub worker_status: Option<EvaluationWorkerStatus>,
    pub refresh_accepted: Option<bool>,
    pub report: Option<SavedEvaluation>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub policies: Vec<GeneratedPolicyRecord>,
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
                let sessions: Arc<dyn SessionStore> = Arc::new(SqliteSessionRepository::new(&home));
                let process = |checkpoint: EvaluationCheckpoint| -> StoreResult<()> {
                    let mut db = SqliteEvaluationStore::open(&home.join("evaluation.sqlite3"))?;
                    if let Some(saved) =
                        db.latest(&checkpoint.workspace_id, &checkpoint.session_id)?
                    {
                        worker_state.publish(saved)?;
                    }
                    let mut stored =
                        sessions.read(&checkpoint.workspace_id, &checkpoint.session_id)?;
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
                    let mut runs = Vec::new();
                    for snapshot in run_evidence_snapshots(&stored.events) {
                        let snapshot = db.resolve_run_snapshot(snapshot)?;
                        let acceptance = db.latest_acceptance(&snapshot)?;
                        runs.push(RunEvaluation {
                            snapshot,
                            acceptance,
                        });
                    }
                    let saved = SavedEvaluation {
                        runs,
                        checkpoint: checkpoint.clone(),
                        context: registry.context(&config.context_strategy, &evidence)?,
                        model: registry.model(&config.model_strategy, &evidence)?,
                    };
                    db.save(&saved, &config, &evidence)?;
                    if let Ok(records) = db.list_policy_versions(None) {
                        for group in &saved.model.data.groups {
                            for record in &records {
                                if let Some(evolved) = evolve_blend_policy(
                                    &record.policy,
                                    &group.report,
                                    Some(evidence.fingerprint.clone()),
                                ) {
                                    let _ = db.save_policy_version(&evolved);
                                }
                            }
                        }
                    }
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
                // The session repository is the durable source: recover missed checkpoints after
                // queue overflow or process exit without a synchronous outbox write.
                if let Ok(listings) = sessions.list(None) {
                    for listing in listings {
                        if let Ok(stored) =
                            sessions.read(&listing.header.workspace_id, &listing.header.id)
                            && let Some(event) = stored
                                .events
                                .iter()
                                .rev()
                                .find(|event| terminal(&event.event))
                        {
                            let already_evaluated = SqliteEvaluationStore::read_latest(
                                &home.join("evaluation.sqlite3"),
                                &event.workspace_id,
                                &event.session_id,
                            )
                            .ok()
                            .flatten()
                            .is_some_and(|latest| {
                                latest.checkpoint.sequence >= event.sequence
                                    && !latest.runs.is_empty()
                            });

                            if !already_evaluated {
                                evaluate(EvaluationCheckpoint {
                                    workspace_id: event.workspace_id.clone(),
                                    session_id: event.session_id.clone(),
                                    sequence: event.sequence,
                                });
                            }
                        }
                    }
                }
                for job in receiver {
                    match job {
                        EvaluationJob::Checkpoint(checkpoint) => evaluate(checkpoint),
                        EvaluationJob::Refresh { workspace, session } => {
                            match sessions.read(&workspace, &session) {
                                Ok(stored) => {
                                    if let Some(event) = stored
                                        .events
                                        .iter()
                                        .rev()
                                        .find(|event| terminal(&event.event))
                                    {
                                        evaluate(EvaluationCheckpoint {
                                            workspace_id: workspace,
                                            session_id: session,
                                            sequence: event.sequence,
                                        });
                                    }
                                }
                                Err(_) => {
                                    worker_state.failed.fetch_add(1, Ordering::Relaxed);
                                }
                            }
                        }
                    }
                }
            })?;
        Ok(Self { sender, state })
    }
    pub fn submit(&self, checkpoint: EvaluationCheckpoint) -> bool {
        self.enqueue(EvaluationJob::Checkpoint(checkpoint))
    }
    pub fn refresh(&self, workspace: WorkspaceId, session: SessionId) -> bool {
        self.enqueue(EvaluationJob::Refresh { workspace, session })
    }
    fn enqueue(&self, job: EvaluationJob) -> bool {
        match self.sender.try_send(job) {
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
        let store = SqliteSessionRepository::new(home)
            .create(NewSession {
                session_id: &SessionId::new("session"),
                workspace_id: &WorkspaceId::new("workspace"),
                cwd: home,
                profile: None,
                instructions_sha256: None,
            })
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
            runs: Vec::new(),
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
            SqliteEvaluationStore::read_latest(
                &path,
                &checkpoint().workspace_id,
                &checkpoint().session_id
            )
            .unwrap(),
            Some(saved.clone())
        );
        assert_eq!(
            db.latest(&checkpoint().workspace_id, &checkpoint().session_id)
                .unwrap(),
            Some(saved)
        );
        drop(db);
        std::fs::remove_dir_all(home).unwrap();
    }

    #[test]
    fn run_snapshots_are_immutable_and_acceptance_survives_reopen() {
        use arabica_runtime::{AcceptanceSpec, EvidenceAcceptanceScorer, RunAcceptanceScorer};
        let home = home();
        let path = home.join("evaluation.sqlite3");
        let snapshot = run_evidence_snapshots(&events()).pop().unwrap();
        let spec = AcceptanceSpec {
            task_id: "task".into(),
            version: 1,
            criteria: Vec::new(),
        };
        let score = EvidenceAcceptanceScorer.score(&snapshot, &spec).unwrap();
        let mut db = SqliteEvaluationStore::open(&path).unwrap();
        db.save_run_snapshot(&snapshot).unwrap();
        db.save_acceptance(&snapshot, &spec, &score).unwrap();
        db.save_acceptance(&snapshot, &spec, &score).unwrap();
        let count: usize =
            db.0.query_row("SELECT count(*) FROM run_acceptance_results", [], |row| {
                row.get(0)
            })
            .unwrap();
        assert_eq!(count, 1);
        let mut changed = snapshot.clone();
        changed.final_output = Some("changed later".into());
        assert!(db.save_run_snapshot(&changed).is_err());
        let mut wrong_score = score.clone();
        wrong_score.snapshot_fingerprint = "wrong".into();
        assert!(db.save_acceptance(&snapshot, &spec, &wrong_score).is_err());
        drop(db);
        let db = SqliteEvaluationStore::open(&path).unwrap();
        assert_eq!(
            db.run_snapshot(
                &snapshot.workspace_id,
                &snapshot.session_id,
                &snapshot.run_id
            )
            .unwrap(),
            Some(snapshot.clone())
        );
        assert_eq!(db.latest_acceptance(&snapshot).unwrap(), Some(score));
        std::fs::remove_dir_all(home).unwrap();
    }

    #[test]
    fn schema_one_migrates_without_losing_existing_reports() {
        let home = home();
        let path = home.join("evaluation.sqlite3");
        let mut db = SqliteEvaluationStore::open(&path).unwrap();
        let config = EvaluationConfig::default();
        let evidence = EvaluationEvidence::from_history(&events());
        let registry = EvaluationRegistry::builtins(&config).unwrap();
        let saved = SavedEvaluation {
            checkpoint: checkpoint(),
            runs: Vec::new(),
            context: registry
                .context(&config.context_strategy, &evidence)
                .unwrap(),
            model: registry.model(&config.model_strategy, &evidence).unwrap(),
        };
        db.save(&saved, &config, &evidence).unwrap();
        db.0.execute_batch("DROP TABLE run_evidence_snapshots; DROP TABLE run_acceptance_results; DROP TABLE policy_comparisons; PRAGMA user_version=1;").unwrap();
        drop(db);
        assert_eq!(
            SqliteEvaluationStore::read_latest(
                &path,
                &checkpoint().workspace_id,
                &checkpoint().session_id
            )
            .unwrap(),
            Some(saved.clone())
        );
        let db = SqliteEvaluationStore::open(&path).unwrap();
        assert_eq!(
            db.latest(&checkpoint().workspace_id, &checkpoint().session_id)
                .unwrap(),
            Some(saved)
        );
        let version: u64 =
            db.0.query_row("PRAGMA user_version", [], |row| row.get(0))
                .unwrap();
        assert_eq!(version, 2);
        std::fs::remove_dir_all(home).unwrap();
    }
    #[test]
    fn refresh_uses_latest_persisted_checkpoint_without_runtime_access() {
        let home = home();
        write_history(&home);
        let worker =
            EvaluationWorker::start(home.clone(), 2, || Ok(EvaluationConfig::default())).unwrap();
        wait_for(|| worker.status().completed == 1);
        let initial = worker
            .latest(&checkpoint().workspace_id, &checkpoint().session_id)
            .unwrap();
        assert_eq!(initial.runs.len(), 1);
        assert!(initial.runs[0].acceptance.is_none());
        let store = SqliteSessionRepository::new(&home)
            .open(&checkpoint().workspace_id, &checkpoint().session_id)
            .unwrap();
        store.observe(
            &event(3, Event::RunCompleted { output: None }),
            EventVisibility::Internal,
        );
        assert!(worker.refresh(checkpoint().workspace_id, checkpoint().session_id));
        wait_for(|| worker.status().completed == 2);
        assert_eq!(
            worker
                .latest(&checkpoint().workspace_id, &checkpoint().session_id)
                .unwrap()
                .checkpoint
                .sequence,
            3
        );
        drop(store);
        drop(worker);
        std::fs::remove_dir_all(home).unwrap();
    }

    #[test]
    fn unsupported_schema_is_rejected_without_modification() {
        let home = home();
        std::fs::create_dir_all(&home).unwrap();
        let path = home.join("evaluation.sqlite3");
        let connection = Connection::open(&path).unwrap();
        connection.execute_batch("PRAGMA user_version=99;").unwrap();
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
    #[test]
    fn policy_versions_crud_and_query_survive_database_reopen() {
        use std::collections::BTreeSet;
        let home = home();
        let path = home.join("evaluation.sqlite3");
        let mut db = SqliteEvaluationStore::open(&path).unwrap();

        let policy = BlendRoutingPolicy {
            policy_id: "test-policy".to_string(),
            version: 1,
            default_model: "fast".to_string(),
            after_tool_success: None,
            after_tool_error: None,
            recovery_model: Some("strong".to_string()),
            planning_model: None,
            tool_routes: vec![],
            recovery_after_no_progress_steps: 2,
            minimum_model_dwell_steps: 1,
            tool_call_capable_models: BTreeSet::new(),
            typed_completion_capable_models: BTreeSet::new(),
        };

        let record = GeneratedPolicyRecord {
            policy_id: "test-policy".to_string(),
            version: 1,
            status: "active".to_string(),
            policy,
            basis_fingerprint: Some("fp-1".to_string()),
            reason: "initial active policy".to_string(),
        };

        db.save_policy_version(&record).unwrap();
        let list = db.list_policy_versions(Some("test-policy")).unwrap();
        assert_eq!(list.len(), 1);
        assert_eq!(list[0].status, "active");

        let mut candidate = record.clone();
        candidate.version = 2;
        candidate.status = "candidate".to_string();
        candidate.reason = "evolved candidate".to_string();
        db.save_policy_version(&candidate).unwrap();

        let all = db.list_policy_versions(None).unwrap();
        assert_eq!(all.len(), 2);
        assert_eq!(all[0].version, 2); // Ordered DESC

        drop(db);

        // Read-only query without write lock
        let read_only_list =
            SqliteEvaluationStore::read_policy_versions(&path, Some("test-policy")).unwrap();
        assert_eq!(read_only_list.len(), 2);

        let v2 = SqliteEvaluationStore::open(&path)
            .unwrap()
            .get_policy_version("test-policy", 2)
            .unwrap();
        assert!(v2.is_some());
        assert_eq!(v2.unwrap().status, "candidate");

        std::fs::remove_dir_all(home).unwrap();
    }
}
