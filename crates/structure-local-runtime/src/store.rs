use crate::types::{
    event_taxonomy_for_kind, ArtifactRecord, KnowledgeSource, LocalEvent, LocalTaskRecord,
    RunEventKind, RunStatus, RunSummary, WorkspaceSummary,
};
use rusqlite::{params, Connection};
use serde::Serialize;
use std::fs;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};
use structure_local_core::structure_core_manifest;

pub struct SqliteLocalStore {
    db_path: PathBuf,
}

impl SqliteLocalStore {
    pub fn open(runtime_dir: impl AsRef<Path>) -> Result<Self, String> {
        let runtime_dir = runtime_dir.as_ref();
        fs::create_dir_all(runtime_dir)
            .map_err(|err| format!("failed to create runtime directory: {err}"))?;
        let db_path = runtime_dir.join("structure.db");
        let store = Self { db_path };
        store.with_connection(|connection| {
            init_schema(connection)?;
            Ok(())
        })?;
        Ok(store)
    }

    pub fn db_path(&self) -> &Path {
        &self.db_path
    }

    pub(crate) fn ensure_workspace(
        &self,
        workspace_id: Option<String>,
        root_path: impl AsRef<Path>,
    ) -> Result<WorkspaceSummary, String> {
        let root_path = root_path.as_ref().display().to_string();
        let workspace_id = workspace_id.unwrap_or_else(|| "default".to_string());
        let name = if workspace_id == "default" {
            "Default Workspace".to_string()
        } else {
            workspace_id.clone()
        };
        let now = now_ms();

        self.with_connection(|connection| {
            connection
                .execute(
                    "INSERT INTO workspaces (workspace_id, name, root_path, created_at_ms, updated_at_ms)
                     VALUES (?1, ?2, ?3, ?4, ?4)
                     ON CONFLICT(workspace_id) DO UPDATE SET
                       root_path = excluded.root_path,
                       updated_at_ms = excluded.updated_at_ms",
                    params![workspace_id, name, root_path, now],
                )
                .map_err(sql_error)?;

            let mut statement = connection
                .prepare(
                    "SELECT workspace_id, name, root_path, created_at_ms, updated_at_ms
                     FROM workspaces WHERE workspace_id = ?1",
                )
                .map_err(sql_error)?;
            statement
                .query_row(params![workspace_id], workspace_from_row)
                .map_err(sql_error)
        })
    }

    pub fn list_workspaces(&self, limit: usize) -> Result<Vec<WorkspaceSummary>, String> {
        self.with_connection(|connection| {
            let mut statement = connection
                .prepare(
                    "SELECT workspace_id, name, root_path, created_at_ms, updated_at_ms
                     FROM workspaces
                     ORDER BY updated_at_ms DESC
                     LIMIT ?1",
                )
                .map_err(sql_error)?;
            let rows = statement
                .query_map(params![limit as i64], workspace_from_row)
                .map_err(sql_error)?;
            rows.collect::<Result<Vec<_>, _>>().map_err(sql_error)
        })
    }

    pub fn workspace_by_id(&self, workspace_id: &str) -> Result<WorkspaceSummary, String> {
        self.with_connection(|connection| {
            let mut statement = connection
                .prepare(
                    "SELECT workspace_id, name, root_path, created_at_ms, updated_at_ms
                     FROM workspaces WHERE workspace_id = ?1",
                )
                .map_err(sql_error)?;
            statement
                .query_row(params![workspace_id], workspace_from_row)
                .map_err(sql_error)
        })
    }

    pub(crate) fn create_run(
        &self,
        run_id: &str,
        workspace_id: &str,
        prompt: &str,
    ) -> Result<RunSummary, String> {
        let now = now_ms();
        self.with_connection(|connection| {
            connection
                .execute(
                    "INSERT INTO runs
                       (run_id, workspace_id, prompt, status, final_response, created_at_ms, updated_at_ms)
                     VALUES (?1, ?2, ?3, ?4, NULL, ?5, ?5)",
                    params![run_id, workspace_id, prompt, RunStatus::Pending.as_str(), now],
                )
                .map_err(sql_error)?;
            self.run_by_id_with(connection, run_id)
        })
    }

    pub(crate) fn update_run(
        &self,
        run_id: &str,
        status: RunStatus,
        final_response: Option<&str>,
    ) -> Result<RunSummary, String> {
        let now = now_ms();
        self.with_connection(|connection| {
            connection
                .execute(
                    "UPDATE runs
                     SET status = ?2, final_response = COALESCE(?3, final_response), updated_at_ms = ?4
                     WHERE run_id = ?1",
                    params![run_id, status.as_str(), final_response, now],
                )
                .map_err(sql_error)?;
            self.run_by_id_with(connection, run_id)
        })
    }

    pub(crate) fn append_event<T: Serialize>(
        &self,
        workspace_id: &str,
        run_id: Option<&str>,
        kind: RunEventKind,
        payload: &T,
    ) -> Result<LocalEvent, String> {
        let event_id = new_id("evt");
        let kind = kind.as_str();
        let (canonical_flow_id, primitive_id) = event_taxonomy_for_kind(kind);
        validate_core_taxonomy(kind, canonical_flow_id, primitive_id)?;
        let payload_json = serde_json::to_string(payload)
            .map_err(|err| format!("failed to serialize event payload: {err}"))?;
        let now = now_ms();
        self.with_connection(|connection| {
            connection
                .execute(
                    "INSERT INTO events
                       (event_id, run_id, workspace_id, kind, payload_json, created_at_ms)
                     VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
                    params![event_id, run_id, workspace_id, kind, payload_json, now],
                )
                .map_err(sql_error)?;
            let sequence = connection.last_insert_rowid();
            self.event_by_sequence_with(connection, sequence)
        })
    }

    pub fn add_knowledge_source(
        &self,
        workspace_id: &str,
        path: impl AsRef<Path>,
    ) -> Result<KnowledgeSource, String> {
        let path = path
            .as_ref()
            .canonicalize()
            .map_err(|err| format!("failed to canonicalize knowledge source: {err}"))?;
        let metadata = fs::metadata(&path)
            .map_err(|err| format!("failed to inspect knowledge source: {err}"))?;
        if !metadata.is_file() {
            return Err("knowledge source must be a file".to_string());
        }
        let source_id = new_id("src");
        let title = path
            .file_name()
            .and_then(|name| name.to_str())
            .unwrap_or("knowledge-source")
            .to_string();
        let path_string = path.display().to_string();
        let size_bytes = metadata.len();
        let now = now_ms();

        self.with_connection(|connection| {
            connection
                .execute(
                    "INSERT INTO knowledge_sources
                       (source_id, workspace_id, path, title, size_bytes, added_at_ms)
                     VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
                    params![source_id, workspace_id, path_string, title, size_bytes, now],
                )
                .map_err(sql_error)?;
            Ok(KnowledgeSource {
                source_id,
                workspace_id: workspace_id.to_string(),
                path: path_string,
                title,
                size_bytes,
                added_at_ms: now,
            })
        })
    }

    pub fn list_knowledge_sources(
        &self,
        workspace_id: &str,
        limit: usize,
    ) -> Result<Vec<KnowledgeSource>, String> {
        self.with_connection(|connection| {
            let mut statement = connection
                .prepare(
                    "SELECT source_id, workspace_id, path, title, size_bytes, added_at_ms
                     FROM knowledge_sources
                     WHERE workspace_id = ?1
                     ORDER BY added_at_ms DESC
                     LIMIT ?2",
                )
                .map_err(sql_error)?;
            let rows = statement
                .query_map(params![workspace_id, limit as i64], knowledge_from_row)
                .map_err(sql_error)?;
            rows.collect::<Result<Vec<_>, _>>().map_err(sql_error)
        })
    }

    pub fn knowledge_source_by_id(&self, source_id: &str) -> Result<KnowledgeSource, String> {
        self.with_connection(|connection| {
            let mut statement = connection
                .prepare(
                    "SELECT source_id, workspace_id, path, title, size_bytes, added_at_ms
                     FROM knowledge_sources
                     WHERE source_id = ?1",
                )
                .map_err(sql_error)?;
            statement
                .query_row(params![source_id], knowledge_from_row)
                .map_err(sql_error)
        })
    }

    pub fn delete_knowledge_source(&self, source_id: &str) -> Result<KnowledgeSource, String> {
        self.with_connection(|connection| {
            let source = {
                let mut statement = connection
                    .prepare(
                        "SELECT source_id, workspace_id, path, title, size_bytes, added_at_ms
                         FROM knowledge_sources
                         WHERE source_id = ?1",
                    )
                    .map_err(sql_error)?;
                statement
                    .query_row(params![source_id], knowledge_from_row)
                    .map_err(sql_error)?
            };
            connection
                .execute(
                    "DELETE FROM knowledge_sources WHERE source_id = ?1",
                    params![source_id],
                )
                .map_err(sql_error)?;
            Ok(source)
        })
    }

    pub fn create_task(
        &self,
        workspace_id: &str,
        run_id: Option<&str>,
        title: &str,
        priority: &str,
    ) -> Result<LocalTaskRecord, String> {
        let task_id = new_id("task");
        let now = now_ms();
        self.with_connection(|connection| {
            connection
                .execute(
                    "INSERT INTO local_tasks
                       (task_id, workspace_id, run_id, title, status, priority, created_at_ms, updated_at_ms)
                     VALUES (?1, ?2, ?3, ?4, 'todo', ?5, ?6, ?6)",
                    params![task_id, workspace_id, run_id, title, priority, now],
                )
                .map_err(sql_error)?;
            self.task_by_id_with(connection, &task_id)
        })
    }

    pub fn update_task_status(
        &self,
        task_id: &str,
        status: &str,
    ) -> Result<LocalTaskRecord, String> {
        let now = now_ms();
        self.with_connection(|connection| {
            connection
                .execute(
                    "UPDATE local_tasks
                     SET status = ?2, updated_at_ms = ?3
                     WHERE task_id = ?1",
                    params![task_id, status, now],
                )
                .map_err(sql_error)?;
            self.task_by_id_with(connection, task_id)
        })
    }

    pub fn list_tasks(
        &self,
        workspace_id: Option<&str>,
        status: Option<&str>,
        limit: usize,
    ) -> Result<Vec<LocalTaskRecord>, String> {
        self.with_connection(|connection| match (workspace_id, status) {
            (Some(workspace_id), Some(status)) => {
                let mut statement = connection
                    .prepare(
                        "SELECT task_id, workspace_id, run_id, title, status, priority, created_at_ms, updated_at_ms
                         FROM local_tasks
                         WHERE workspace_id = ?1 AND status = ?2
                         ORDER BY updated_at_ms DESC LIMIT ?3",
                    )
                    .map_err(sql_error)?;
                let rows = statement
                    .query_map(params![workspace_id, status, limit as i64], task_from_row)
                    .map_err(sql_error)?;
                rows.collect::<Result<Vec<_>, _>>().map_err(sql_error)
            }
            (Some(workspace_id), None) => {
                let mut statement = connection
                    .prepare(
                        "SELECT task_id, workspace_id, run_id, title, status, priority, created_at_ms, updated_at_ms
                         FROM local_tasks
                         WHERE workspace_id = ?1
                         ORDER BY updated_at_ms DESC LIMIT ?2",
                    )
                    .map_err(sql_error)?;
                let rows = statement
                    .query_map(params![workspace_id, limit as i64], task_from_row)
                    .map_err(sql_error)?;
                rows.collect::<Result<Vec<_>, _>>().map_err(sql_error)
            }
            (None, Some(status)) => {
                let mut statement = connection
                    .prepare(
                        "SELECT task_id, workspace_id, run_id, title, status, priority, created_at_ms, updated_at_ms
                         FROM local_tasks
                         WHERE status = ?1
                         ORDER BY updated_at_ms DESC LIMIT ?2",
                    )
                    .map_err(sql_error)?;
                let rows = statement
                    .query_map(params![status, limit as i64], task_from_row)
                    .map_err(sql_error)?;
                rows.collect::<Result<Vec<_>, _>>().map_err(sql_error)
            }
            (None, None) => {
                let mut statement = connection
                    .prepare(
                        "SELECT task_id, workspace_id, run_id, title, status, priority, created_at_ms, updated_at_ms
                         FROM local_tasks
                         ORDER BY updated_at_ms DESC LIMIT ?1",
                    )
                    .map_err(sql_error)?;
                let rows = statement
                    .query_map(params![limit as i64], task_from_row)
                    .map_err(sql_error)?;
                rows.collect::<Result<Vec<_>, _>>().map_err(sql_error)
            }
        })
    }

    pub fn task_by_id(&self, task_id: &str) -> Result<LocalTaskRecord, String> {
        self.with_connection(|connection| self.task_by_id_with(connection, task_id))
    }

    pub fn list_runs(
        &self,
        workspace_id: Option<&str>,
        limit: usize,
    ) -> Result<Vec<RunSummary>, String> {
        self.with_connection(|connection| {
            if let Some(workspace_id) = workspace_id {
                let mut statement = connection
                    .prepare(
                        "SELECT run_id, workspace_id, prompt, status, final_response, created_at_ms, updated_at_ms
                         FROM runs WHERE workspace_id = ?1
                         ORDER BY created_at_ms DESC LIMIT ?2",
                    )
                    .map_err(sql_error)?;
                let rows = statement
                    .query_map(params![workspace_id, limit as i64], run_from_row)
                    .map_err(sql_error)?;
                rows.collect::<Result<Vec<_>, _>>().map_err(sql_error)
            } else {
                let mut statement = connection
                    .prepare(
                        "SELECT run_id, workspace_id, prompt, status, final_response, created_at_ms, updated_at_ms
                         FROM runs ORDER BY created_at_ms DESC LIMIT ?1",
                    )
                    .map_err(sql_error)?;
                let rows = statement
                    .query_map(params![limit as i64], run_from_row)
                    .map_err(sql_error)?;
                rows.collect::<Result<Vec<_>, _>>().map_err(sql_error)
            }
        })
    }

    pub fn add_artifact(
        &self,
        run_id: &str,
        workspace_id: &str,
        kind: &str,
        path: impl AsRef<Path>,
    ) -> Result<ArtifactRecord, String> {
        let path = path.as_ref();
        let metadata =
            fs::metadata(path).map_err(|err| format!("failed to inspect artifact: {err}"))?;
        if !metadata.is_file() {
            return Err("artifact must be a file".to_string());
        }
        let artifact_id = new_id("art");
        let path_string = path.display().to_string();
        let size_bytes = metadata.len();
        let now = now_ms();

        self.with_connection(|connection| {
            connection
                .execute(
                    "INSERT INTO artifacts
                       (artifact_id, run_id, workspace_id, kind, path, size_bytes, created_at_ms)
                     VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
                    params![
                        artifact_id,
                        run_id,
                        workspace_id,
                        kind,
                        path_string,
                        size_bytes,
                        now
                    ],
                )
                .map_err(sql_error)?;
            Ok(ArtifactRecord {
                artifact_id,
                run_id: run_id.to_string(),
                workspace_id: workspace_id.to_string(),
                kind: kind.to_string(),
                path: path_string,
                size_bytes,
                created_at_ms: now,
            })
        })
    }

    pub fn list_artifacts(
        &self,
        workspace_id: Option<&str>,
        run_id: Option<&str>,
        limit: usize,
    ) -> Result<Vec<ArtifactRecord>, String> {
        self.with_connection(|connection| match (workspace_id, run_id) {
            (_, Some(run_id)) => {
                let mut statement = connection
                    .prepare(
                        "SELECT artifact_id, run_id, workspace_id, kind, path, size_bytes, created_at_ms
                         FROM artifacts WHERE run_id = ?1
                         ORDER BY created_at_ms DESC LIMIT ?2",
                    )
                    .map_err(sql_error)?;
                let rows = statement
                    .query_map(params![run_id, limit as i64], artifact_from_row)
                    .map_err(sql_error)?;
                rows.collect::<Result<Vec<_>, _>>().map_err(sql_error)
            }
            (Some(workspace_id), None) => {
                let mut statement = connection
                    .prepare(
                        "SELECT artifact_id, run_id, workspace_id, kind, path, size_bytes, created_at_ms
                         FROM artifacts WHERE workspace_id = ?1
                         ORDER BY created_at_ms DESC LIMIT ?2",
                    )
                    .map_err(sql_error)?;
                let rows = statement
                    .query_map(params![workspace_id, limit as i64], artifact_from_row)
                    .map_err(sql_error)?;
                rows.collect::<Result<Vec<_>, _>>().map_err(sql_error)
            }
            (None, None) => {
                let mut statement = connection
                    .prepare(
                        "SELECT artifact_id, run_id, workspace_id, kind, path, size_bytes, created_at_ms
                         FROM artifacts ORDER BY created_at_ms DESC LIMIT ?1",
                    )
                    .map_err(sql_error)?;
                let rows = statement
                    .query_map(params![limit as i64], artifact_from_row)
                    .map_err(sql_error)?;
                rows.collect::<Result<Vec<_>, _>>().map_err(sql_error)
            }
        })
    }

    pub fn artifact_by_id(&self, artifact_id: &str) -> Result<ArtifactRecord, String> {
        self.with_connection(|connection| {
            let mut statement = connection
                .prepare(
                    "SELECT artifact_id, run_id, workspace_id, kind, path, size_bytes, created_at_ms
                     FROM artifacts
                     WHERE artifact_id = ?1",
                )
                .map_err(sql_error)?;
            statement
                .query_row(params![artifact_id], artifact_from_row)
                .map_err(sql_error)
        })
    }

    pub fn run_by_id(&self, run_id: &str) -> Result<RunSummary, String> {
        self.with_connection(|connection| self.run_by_id_with(connection, run_id))
    }

    pub fn run_events(&self, run_id: &str) -> Result<Vec<LocalEvent>, String> {
        self.with_connection(|connection| {
            let mut statement = connection
                .prepare(
                    "SELECT sequence, event_id, run_id, workspace_id, kind, payload_json, created_at_ms
                     FROM events WHERE run_id = ?1 ORDER BY sequence ASC",
                )
                .map_err(sql_error)?;
            let rows = statement
                .query_map(params![run_id], event_from_row)
                .map_err(sql_error)?;
            rows.collect::<Result<Vec<_>, _>>().map_err(sql_error)
        })
    }

    pub fn workspace_events(
        &self,
        workspace_id: &str,
        limit: usize,
    ) -> Result<Vec<LocalEvent>, String> {
        self.with_connection(|connection| {
            let mut statement = connection
                .prepare(
                    "SELECT sequence, event_id, run_id, workspace_id, kind, payload_json, created_at_ms
                     FROM events WHERE workspace_id = ?1
                     ORDER BY sequence DESC LIMIT ?2",
                )
                .map_err(sql_error)?;
            let rows = statement
                .query_map(params![workspace_id, limit as i64], event_from_row)
                .map_err(sql_error)?;
            let mut events = rows.collect::<Result<Vec<_>, _>>().map_err(sql_error)?;
            events.reverse();
            Ok(events)
        })
    }

    pub fn workspace_events_after(
        &self,
        workspace_id: &str,
        after_sequence: i64,
        limit: usize,
    ) -> Result<Vec<LocalEvent>, String> {
        self.with_connection(|connection| {
            let mut statement = connection
                .prepare(
                    "SELECT sequence, event_id, run_id, workspace_id, kind, payload_json, created_at_ms
                     FROM events
                     WHERE workspace_id = ?1 AND sequence > ?2
                     ORDER BY sequence ASC LIMIT ?3",
                )
                .map_err(sql_error)?;
            let rows = statement
                .query_map(params![workspace_id, after_sequence, limit as i64], event_from_row)
                .map_err(sql_error)?;
            rows.collect::<Result<Vec<_>, _>>().map_err(sql_error)
        })
    }

    fn run_by_id_with(&self, connection: &Connection, run_id: &str) -> Result<RunSummary, String> {
        let mut statement = connection
            .prepare(
                "SELECT run_id, workspace_id, prompt, status, final_response, created_at_ms, updated_at_ms
                 FROM runs WHERE run_id = ?1",
            )
            .map_err(sql_error)?;
        statement
            .query_row(params![run_id], run_from_row)
            .map_err(sql_error)
    }

    fn task_by_id_with(
        &self,
        connection: &Connection,
        task_id: &str,
    ) -> Result<LocalTaskRecord, String> {
        let mut statement = connection
            .prepare(
                "SELECT task_id, workspace_id, run_id, title, status, priority, created_at_ms, updated_at_ms
                 FROM local_tasks WHERE task_id = ?1",
            )
            .map_err(sql_error)?;
        statement
            .query_row(params![task_id], task_from_row)
            .map_err(sql_error)
    }

    fn event_by_sequence_with(
        &self,
        connection: &Connection,
        sequence: i64,
    ) -> Result<LocalEvent, String> {
        let mut statement = connection
            .prepare(
                "SELECT sequence, event_id, run_id, workspace_id, kind, payload_json, created_at_ms
                 FROM events WHERE sequence = ?1",
            )
            .map_err(sql_error)?;
        statement
            .query_row(params![sequence], event_from_row)
            .map_err(sql_error)
    }

    fn with_connection<T>(
        &self,
        f: impl FnOnce(&Connection) -> Result<T, String>,
    ) -> Result<T, String> {
        let connection = Connection::open(&self.db_path)
            .map_err(|err| format!("failed to open local runtime database: {err}"))?;
        connection
            .execute_batch("PRAGMA foreign_keys = ON; PRAGMA journal_mode = WAL;")
            .map_err(sql_error)?;
        f(&connection)
    }
}

fn init_schema(connection: &Connection) -> Result<(), String> {
    connection
        .execute_batch(
            r#"
            CREATE TABLE IF NOT EXISTS workspaces (
              workspace_id TEXT PRIMARY KEY,
              name TEXT NOT NULL,
              root_path TEXT NOT NULL,
              created_at_ms INTEGER NOT NULL,
              updated_at_ms INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS runs (
              run_id TEXT PRIMARY KEY,
              workspace_id TEXT NOT NULL,
              prompt TEXT NOT NULL,
              status TEXT NOT NULL,
              final_response TEXT,
              created_at_ms INTEGER NOT NULL,
              updated_at_ms INTEGER NOT NULL,
              FOREIGN KEY(workspace_id) REFERENCES workspaces(workspace_id)
            );

            CREATE TABLE IF NOT EXISTS events (
              sequence INTEGER PRIMARY KEY AUTOINCREMENT,
              event_id TEXT NOT NULL UNIQUE,
              run_id TEXT,
              workspace_id TEXT NOT NULL,
              kind TEXT NOT NULL,
              payload_json TEXT NOT NULL,
              created_at_ms INTEGER NOT NULL,
              FOREIGN KEY(workspace_id) REFERENCES workspaces(workspace_id),
              FOREIGN KEY(run_id) REFERENCES runs(run_id)
            );

            CREATE TABLE IF NOT EXISTS knowledge_sources (
              source_id TEXT PRIMARY KEY,
              workspace_id TEXT NOT NULL,
              path TEXT NOT NULL,
              title TEXT NOT NULL,
              size_bytes INTEGER NOT NULL,
              added_at_ms INTEGER NOT NULL,
              FOREIGN KEY(workspace_id) REFERENCES workspaces(workspace_id)
            );

            CREATE TABLE IF NOT EXISTS local_tasks (
              task_id TEXT PRIMARY KEY,
              workspace_id TEXT NOT NULL,
              run_id TEXT,
              title TEXT NOT NULL,
              status TEXT NOT NULL,
              priority TEXT NOT NULL,
              created_at_ms INTEGER NOT NULL,
              updated_at_ms INTEGER NOT NULL,
              FOREIGN KEY(workspace_id) REFERENCES workspaces(workspace_id),
              FOREIGN KEY(run_id) REFERENCES runs(run_id)
            );

            CREATE TABLE IF NOT EXISTS artifacts (
              artifact_id TEXT PRIMARY KEY,
              run_id TEXT NOT NULL,
              workspace_id TEXT NOT NULL,
              kind TEXT NOT NULL,
              path TEXT NOT NULL,
              size_bytes INTEGER NOT NULL,
              created_at_ms INTEGER NOT NULL,
              FOREIGN KEY(workspace_id) REFERENCES workspaces(workspace_id),
              FOREIGN KEY(run_id) REFERENCES runs(run_id)
            );

            CREATE INDEX IF NOT EXISTS idx_events_run_sequence ON events(run_id, sequence);
            CREATE INDEX IF NOT EXISTS idx_events_workspace_sequence ON events(workspace_id, sequence);
            CREATE INDEX IF NOT EXISTS idx_runs_workspace_created ON runs(workspace_id, created_at_ms);
            CREATE INDEX IF NOT EXISTS idx_knowledge_workspace_added ON knowledge_sources(workspace_id, added_at_ms);
            CREATE INDEX IF NOT EXISTS idx_tasks_workspace_updated ON local_tasks(workspace_id, updated_at_ms);
            CREATE INDEX IF NOT EXISTS idx_tasks_run_updated ON local_tasks(run_id, updated_at_ms);
            CREATE INDEX IF NOT EXISTS idx_artifacts_workspace_created ON artifacts(workspace_id, created_at_ms);
            CREATE INDEX IF NOT EXISTS idx_artifacts_run_created ON artifacts(run_id, created_at_ms);
            "#,
        )
        .map_err(sql_error)
}

fn workspace_from_row(row: &rusqlite::Row<'_>) -> rusqlite::Result<WorkspaceSummary> {
    Ok(WorkspaceSummary {
        workspace_id: row.get(0)?,
        name: row.get(1)?,
        root_path: row.get(2)?,
        created_at_ms: row.get(3)?,
        updated_at_ms: row.get(4)?,
    })
}

fn knowledge_from_row(row: &rusqlite::Row<'_>) -> rusqlite::Result<KnowledgeSource> {
    let size_bytes: i64 = row.get(4)?;
    Ok(KnowledgeSource {
        source_id: row.get(0)?,
        workspace_id: row.get(1)?,
        path: row.get(2)?,
        title: row.get(3)?,
        size_bytes: size_bytes.max(0) as u64,
        added_at_ms: row.get(5)?,
    })
}

fn run_from_row(row: &rusqlite::Row<'_>) -> rusqlite::Result<RunSummary> {
    Ok(RunSummary {
        run_id: row.get(0)?,
        workspace_id: row.get(1)?,
        prompt: row.get(2)?,
        status: row.get(3)?,
        final_response: row.get(4)?,
        created_at_ms: row.get(5)?,
        updated_at_ms: row.get(6)?,
    })
}

fn artifact_from_row(row: &rusqlite::Row<'_>) -> rusqlite::Result<ArtifactRecord> {
    let size_bytes: i64 = row.get(5)?;
    Ok(ArtifactRecord {
        artifact_id: row.get(0)?,
        run_id: row.get(1)?,
        workspace_id: row.get(2)?,
        kind: row.get(3)?,
        path: row.get(4)?,
        size_bytes: size_bytes.max(0) as u64,
        created_at_ms: row.get(6)?,
    })
}

fn task_from_row(row: &rusqlite::Row<'_>) -> rusqlite::Result<LocalTaskRecord> {
    Ok(LocalTaskRecord {
        task_id: row.get(0)?,
        workspace_id: row.get(1)?,
        run_id: row.get(2)?,
        title: row.get(3)?,
        status: row.get(4)?,
        priority: row.get(5)?,
        created_at_ms: row.get(6)?,
        updated_at_ms: row.get(7)?,
    })
}

fn event_from_row(row: &rusqlite::Row<'_>) -> rusqlite::Result<LocalEvent> {
    let payload_json: String = row.get(5)?;
    let payload = serde_json::from_str(&payload_json).unwrap_or_else(|_| serde_json::json!({}));
    let kind: String = row.get(4)?;
    let (canonical_flow_id, primitive_id) = event_taxonomy_for_kind(&kind);
    Ok(LocalEvent {
        sequence: row.get(0)?,
        event_id: row.get(1)?,
        run_id: row.get(2)?,
        workspace_id: row.get(3)?,
        kind,
        canonical_flow_id: canonical_flow_id.to_string(),
        primitive_id: primitive_id.to_string(),
        payload,
        created_at_ms: row.get(6)?,
    })
}

pub(crate) fn now_ms() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_millis().min(i64::MAX as u128) as i64)
        .unwrap_or_default()
}

pub(crate) fn new_id(prefix: &str) -> String {
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_nanos())
        .unwrap_or_default();
    format!("{prefix}_{}_{}", std::process::id(), nanos)
}

fn sql_error(err: rusqlite::Error) -> String {
    format!("local runtime database error: {err}")
}

fn validate_core_taxonomy(
    kind: &str,
    canonical_flow_id: &str,
    primitive_id: &str,
) -> Result<(), String> {
    let manifest = structure_core_manifest()?;
    let flow_known = manifest
        .canonical_flow
        .iter()
        .any(|step| step.id == canonical_flow_id);
    let primitive_known = manifest
        .primitives
        .iter()
        .any(|primitive| primitive.id == primitive_id);
    if flow_known && primitive_known {
        return Ok(());
    }
    Err(format!(
        "event {kind} maps to flow {canonical_flow_id} / primitive {primitive_id}, which is outside the Structure core manifest"
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn store_persists_events_and_runs() {
        let root = unique_temp_dir("store");
        let store = SqliteLocalStore::open(&root).unwrap();
        let workspace = store.ensure_workspace(None, &root).unwrap();
        let run = store
            .create_run("run_test", &workspace.workspace_id, "hello")
            .unwrap();
        store
            .append_event(
                &workspace.workspace_id,
                Some(&run.run_id),
                RunEventKind::PromptReceived,
                &serde_json::json!({"prompt": "hello"}),
            )
            .unwrap();
        store
            .update_run(&run.run_id, RunStatus::Finished, Some("done"))
            .unwrap();

        let runs = store.list_runs(None, 10).unwrap();
        let loaded_run = store.run_by_id(&run.run_id).unwrap();
        let events = store.run_events(&run.run_id).unwrap();

        assert_eq!(runs[0].status, "finished");
        assert_eq!(loaded_run.run_id, run.run_id);
        assert_eq!(events.len(), 1);
        assert_eq!(events[0].kind, "prompt_received");
        assert_eq!(events[0].canonical_flow_id, "goal");
        assert_eq!(events[0].primitive_id, "event_audit");

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn store_persists_local_tasks() {
        let root = unique_temp_dir("tasks");
        let store = SqliteLocalStore::open(&root).unwrap();
        let workspace = store.ensure_workspace(None, &root).unwrap();
        let run = store
            .create_run("run_task", &workspace.workspace_id, "plan work")
            .unwrap();

        let task = store
            .create_task(
                &workspace.workspace_id,
                Some(&run.run_id),
                "Write local task tests",
                "high",
            )
            .unwrap();
        let updated = store.update_task_status(&task.task_id, "done").unwrap();
        let tasks = store
            .list_tasks(Some(&workspace.workspace_id), Some("done"), 10)
            .unwrap();

        assert_eq!(task.status, "todo");
        assert_eq!(updated.status, "done");
        assert_eq!(updated.run_id.as_deref(), Some("run_task"));
        assert_eq!(tasks.len(), 1);
        assert_eq!(tasks[0].task_id, task.task_id);

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn store_event_taxonomy_matches_structure_core_manifest() {
        let kinds = [
            RunEventKind::WorkspaceOpened,
            RunEventKind::RunCreated,
            RunEventKind::TaskCreated,
            RunEventKind::TaskUpdated,
            RunEventKind::ChatMessageRecorded,
            RunEventKind::PromptReceived,
            RunEventKind::AgentStepPlanned,
            RunEventKind::WorkspaceContextLoaded,
            RunEventKind::KnowledgeRetrieved,
            RunEventKind::ModelRequested,
            RunEventKind::ModelResponded,
            RunEventKind::ToolCallRequested,
            RunEventKind::ToolCallCompleted,
            RunEventKind::CodeChangeProposed,
            RunEventKind::CodeChangeReviewed,
            RunEventKind::CodeChangeApplied,
            RunEventKind::CodeChangeReverted,
            RunEventKind::ArtifactWritten,
            RunEventKind::RunFinished,
            RunEventKind::RunFailed,
        ];

        for kind in kinds {
            let kind_name = kind.as_str();
            let (flow, primitive) = event_taxonomy_for_kind(kind_name);
            validate_core_taxonomy(kind_name, flow, primitive).unwrap();
        }
    }

    #[test]
    fn store_rejects_event_taxonomy_outside_structure_core_manifest() {
        let error = validate_core_taxonomy(
            "bad_event",
            "not_a_structure_flow",
            "not_a_structure_primitive",
        )
        .unwrap_err();

        assert!(error.contains("outside the Structure core manifest"));
    }

    #[test]
    fn store_lists_and_loads_workspaces() {
        let root = unique_temp_dir("workspaces");
        let store = SqliteLocalStore::open(&root).unwrap();
        let alpha = store
            .ensure_workspace(Some("alpha".to_string()), &root)
            .unwrap();
        let beta = store
            .ensure_workspace(Some("beta".to_string()), &root)
            .unwrap();

        let workspaces = store.list_workspaces(10).unwrap();
        let loaded = store.workspace_by_id("alpha").unwrap();

        assert_eq!(loaded.workspace_id, alpha.workspace_id);
        assert!(workspaces
            .iter()
            .any(|workspace| workspace.workspace_id == alpha.workspace_id));
        assert!(workspaces
            .iter()
            .any(|workspace| workspace.workspace_id == beta.workspace_id));

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn store_indexes_artifacts_and_replays_workspace_events() {
        let root = unique_temp_dir("artifact");
        let store = SqliteLocalStore::open(&root).unwrap();
        let workspace = store.ensure_workspace(None, &root).unwrap();
        let run = store
            .create_run("run_artifact", &workspace.workspace_id, "hello")
            .unwrap();
        let artifact_path = root.join("response.md");
        fs::write(&artifact_path, "done").unwrap();
        store
            .append_event(
                &workspace.workspace_id,
                Some(&run.run_id),
                RunEventKind::PromptReceived,
                &serde_json::json!({"prompt": "hello"}),
            )
            .unwrap();
        let artifact = store
            .add_artifact(
                &run.run_id,
                &workspace.workspace_id,
                "assistant_response",
                &artifact_path,
            )
            .unwrap();

        let artifacts = store
            .list_artifacts(Some(&workspace.workspace_id), None, 10)
            .unwrap();
        let events = store.workspace_events(&workspace.workspace_id, 10).unwrap();

        assert_eq!(artifact.kind, "assistant_response");
        assert_eq!(artifacts.len(), 1);
        assert_eq!(artifacts[0].artifact_id, artifact.artifact_id);
        assert_eq!(events.len(), 1);
        assert_eq!(events[0].run_id.as_deref(), Some("run_artifact"));

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn store_reads_workspace_events_after_cursor() {
        let root = unique_temp_dir("event-feed");
        let store = SqliteLocalStore::open(&root).unwrap();
        let workspace = store.ensure_workspace(None, &root).unwrap();
        let run = store
            .create_run("run_feed", &workspace.workspace_id, "hello")
            .unwrap();
        let first = store
            .append_event(
                &workspace.workspace_id,
                Some(&run.run_id),
                RunEventKind::PromptReceived,
                &serde_json::json!({"step": 1}),
            )
            .unwrap();
        let second = store
            .append_event(
                &workspace.workspace_id,
                Some(&run.run_id),
                RunEventKind::RunFinished,
                &serde_json::json!({"step": 2}),
            )
            .unwrap();

        let events = store
            .workspace_events_after(&workspace.workspace_id, first.sequence, 10)
            .unwrap();

        assert_eq!(events.len(), 1);
        assert_eq!(events[0].sequence, second.sequence);

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn store_reads_and_deletes_knowledge_sources() {
        let root = unique_temp_dir("knowledge");
        let source_path = root.join("source.md");
        fs::write(&source_path, "local source").unwrap();
        let store = SqliteLocalStore::open(&root).unwrap();
        let workspace = store.ensure_workspace(None, &root).unwrap();
        let source = store
            .add_knowledge_source(&workspace.workspace_id, &source_path)
            .unwrap();

        let loaded = store.knowledge_source_by_id(&source.source_id).unwrap();
        let removed = store.delete_knowledge_source(&source.source_id).unwrap();
        let sources = store
            .list_knowledge_sources(&workspace.workspace_id, 10)
            .unwrap();

        assert_eq!(loaded.path, source.path);
        assert_eq!(removed.source_id, source.source_id);
        assert!(sources.is_empty());
        assert!(source_path.exists());

        fs::remove_dir_all(root).unwrap();
    }

    fn unique_temp_dir(label: &str) -> PathBuf {
        let path = std::env::temp_dir().join(format!(
            "structure-local-runtime-{label}-{}-{}",
            std::process::id(),
            now_ms()
        ));
        fs::create_dir_all(&path).unwrap();
        path
    }
}
