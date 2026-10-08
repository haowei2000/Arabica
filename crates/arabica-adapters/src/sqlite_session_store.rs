//! Transactional session metadata and ordered events in one database per home.
//! Session paths are locators, not physical databases. JSONL files are imported
//! once and retained for recovery; SQLite is authoritative after import.

use std::fs::{File, OpenOptions};
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use arabica_protocol::{Event, EventEnvelope, PROTOCOL_VERSION, SessionId, WorkspaceId};
use arabica_session::{EventVisibility, SessionEventObserver};
use rusqlite::{Connection, OptionalExtension, params};

use crate::file_session_store::{create_private_dir, lock_exclusive, validate_path_component};
use crate::{
    FileSessionListing, FileSessionStore, NewSession, SessionHeader, StoreError, StoreErrorKind,
    StoredSession,
};

#[derive(Debug)]
pub struct SqliteSessionStore {
    connection: Mutex<Connection>,
    path: PathBuf,
    workspace_id: WorkspaceId,
    session_id: SessionId,
    // Locks must live for the entire writer lifetime, including legacy files.
    _lock: File,
    _legacy_lock: Option<File>,
}

fn error(error: impl std::fmt::Display) -> StoreError {
    StoreError::new(StoreErrorKind::Io, error.to_string())
}

fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|time| time.as_millis() as u64)
        .unwrap_or(0)
}

fn connect(home: &Path) -> Result<Connection, StoreError> {
    std::fs::create_dir_all(home).map_err(error)?;
    let directory = home.join("sessions");
    if !directory.exists() {
        create_private_dir(&directory)?;
    }
    let path = home.join("sessions.sqlite3");
    let mut options = OpenOptions::new();
    options.create(true).append(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    options.open(&path).map_err(error)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600)).map_err(error)?;
    }
    let connection = Connection::open(&path).map_err(error)?;
    connection
        .busy_timeout(Duration::from_secs(5))
        .map_err(error)?;
    let version: u32 = connection
        .query_row("PRAGMA user_version", [], |row| row.get(0))
        .map_err(error)?;
    if version > 1 {
        return Err(error(format!(
            "unsupported session database version {version}"
        )));
    }
    connection
        .execute_batch(
            "PRAGMA journal_mode=WAL;
         PRAGMA synchronous=FULL;
         PRAGMA foreign_keys=ON;
         CREATE TABLE IF NOT EXISTS sessions (
           workspace_id TEXT NOT NULL, session_id TEXT NOT NULL,
           header TEXT NOT NULL, title TEXT, updated_at_ms INTEGER NOT NULL,
           PRIMARY KEY(workspace_id, session_id));
         CREATE INDEX IF NOT EXISTS sessions_activity ON sessions(updated_at_ms DESC);
         CREATE TABLE IF NOT EXISTS events (
           workspace_id TEXT NOT NULL, session_id TEXT NOT NULL,
           sequence INTEGER NOT NULL, visibility TEXT NOT NULL, envelope TEXT NOT NULL,
           PRIMARY KEY(workspace_id, session_id, sequence),
           FOREIGN KEY(workspace_id, session_id) REFERENCES sessions(workspace_id, session_id));
         PRAGMA user_version=1;",
        )
        .map_err(error)?;
    Ok(connection)
}

fn title_for(event: &Event) -> Option<String> {
    match event {
        Event::MessageAccepted { content } => {
            let title = content
                .split_whitespace()
                .collect::<Vec<_>>()
                .join(" ")
                .chars()
                .take(80)
                .collect::<String>();
            (!title.is_empty()).then_some(title)
        }
        _ => None,
    }
}

fn import_legacy(home: &Path, connection: &mut Connection) -> Result<(), StoreError> {
    for listing in FileSessionStore::list_sessions(home, None)? {
        let workspace = listing.header.workspace_id.to_string();
        let session = listing.header.id.to_string();
        let exists: bool = connection
            .query_row(
                "SELECT EXISTS(SELECT 1 FROM sessions WHERE workspace_id=?1 AND session_id=?2)",
                params![workspace, session],
                |row| row.get(0),
            )
            .map_err(error)?;
        if exists {
            continue;
        }
        // Do not take a snapshot while a legacy writer is still active.
        let file = OpenOptions::new()
            .read(true)
            .write(true)
            .open(&listing.path)
            .map_err(error)?;
        lock_exclusive(&file, &listing.path)?;
        let stored = FileSessionStore::read(&listing.path)?;
        validate_path_component(&workspace, "workspace_id")?;
        validate_path_component(&session, "session_id")?;
        if FileSessionStore::session_path(home, &stored.header.workspace_id, &stored.header.id)
            != listing.path
        {
            return Err(error("legacy header does not match session file location"));
        }
        for (index, envelope) in stored.events.iter().enumerate() {
            if envelope.workspace_id != stored.header.workspace_id
                || envelope.session_id != stored.header.id
                || envelope.sequence != index as u64 + 1
            {
                return Err(error("legacy event identity or sequence is invalid"));
            }
        }
        let title = stored.header.title.clone().or_else(|| {
            stored
                .events
                .iter()
                .find_map(|event| title_for(&event.event))
        });
        let transaction = connection.transaction().map_err(error)?;
        let inserted = transaction
            .execute(
                "INSERT OR IGNORE INTO sessions VALUES (?1, ?2, ?3, ?4, ?5)",
                params![
                    workspace,
                    session,
                    serde_json::to_string(&stored.header).map_err(error)?,
                    title,
                    listing
                        .modified_at_ms
                        .unwrap_or(stored.header.created_at_ms)
                ],
            )
            .map_err(error)?;
        if inserted != 0 {
            for envelope in stored.events {
                transaction
                    .execute(
                        "INSERT INTO events VALUES (?1, ?2, ?3, ?4, ?5)",
                        params![
                            workspace,
                            session,
                            envelope.sequence,
                            serde_json::to_string(&if envelope.event.is_client_visible() {
                                EventVisibility::Client
                            } else {
                                EventVisibility::Internal
                            })
                            .map_err(error)?,
                            serde_json::to_string(&envelope).map_err(error)?
                        ],
                    )
                    .map_err(error)?;
            }
        }
        transaction.commit().map_err(error)?;
    }
    Ok(())
}

impl SqliteSessionStore {
    pub fn session_path(
        home: &Path,
        workspace_id: &WorkspaceId,
        session_id: &SessionId,
    ) -> PathBuf {
        home.join("sessions")
            .join(workspace_id.to_string())
            .join(format!("{session_id}.sqlite"))
    }

    pub fn create(home: &Path, new: NewSession<'_>) -> Result<Self, StoreError> {
        validate_path_component(&new.workspace_id.to_string(), "workspace_id")?;
        validate_path_component(&new.session_id.to_string(), "session_id")?;
        let mut connection = connect(home)?;
        import_legacy(home, &mut connection)?;
        let path = Self::session_path(home, new.workspace_id, new.session_id);
        let (lock, legacy_lock) = Self::locks(&path)?;
        let header = SessionHeader {
            title: None,
            schema: "structure.session/v1".to_owned(),
            id: new.session_id.clone(),
            workspace_id: new.workspace_id.clone(),
            cwd: new.cwd.display().to_string(),
            created_at_ms: now_ms(),
            protocol_version: PROTOCOL_VERSION.to_owned(),
            profile: new.profile.map(str::to_owned),
            instructions_sha256: new.instructions_sha256.map(str::to_owned),
        };
        connection
            .execute(
                "INSERT INTO sessions VALUES (?1, ?2, ?3, NULL, ?4)",
                params![
                    new.workspace_id.to_string(),
                    new.session_id.to_string(),
                    serde_json::to_string(&header).map_err(error)?,
                    header.created_at_ms
                ],
            )
            .map_err(error)?;
        Ok(Self {
            connection: Mutex::new(connection),
            path,
            workspace_id: new.workspace_id.clone(),
            session_id: new.session_id.clone(),
            _lock: lock,
            _legacy_lock: legacy_lock,
        })
    }

    fn location(path: &Path) -> Result<(&Path, WorkspaceId, SessionId), StoreError> {
        let directory = path
            .parent()
            .ok_or_else(|| error("invalid session locator"))?;
        let home = directory
            .parent()
            .and_then(Path::parent)
            .ok_or_else(|| error("invalid session locator"))?;
        let workspace = directory
            .file_name()
            .and_then(|value| value.to_str())
            .ok_or_else(|| error("invalid workspace locator"))?;
        let session = path
            .file_stem()
            .and_then(|value| value.to_str())
            .ok_or_else(|| error("invalid session locator"))?;
        validate_path_component(workspace, "workspace_id")?;
        validate_path_component(session, "session_id")?;
        Ok((home, WorkspaceId::new(workspace), SessionId::new(session)))
    }

    fn locks(path: &Path) -> Result<(File, Option<File>), StoreError> {
        let directory = path
            .parent()
            .ok_or_else(|| error("invalid session locator"))?;
        if !directory.exists() {
            create_private_dir(directory)?;
        }
        let lock_path = path.with_extension("lock");
        let mut options = OpenOptions::new();
        options.create(true).append(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        let file = options.open(&lock_path).map_err(error)?;
        lock_exclusive(&file, &lock_path)?;
        let legacy = path.with_extension("jsonl");
        let legacy_lock = if legacy.exists() {
            let file = OpenOptions::new()
                .read(true)
                .write(true)
                .open(&legacy)
                .map_err(error)?;
            lock_exclusive(&file, &legacy)?;
            Some(file)
        } else {
            None
        };
        Ok((file, legacy_lock))
    }

    pub fn open_existing(path: &Path) -> Result<Self, StoreError> {
        let (home, workspace_id, session_id) = Self::location(path)?;
        let mut connection = connect(home)?;
        import_legacy(home, &mut connection)?;
        Self::read_from(&connection, &workspace_id, &session_id)?;
        let (lock, legacy_lock) = Self::locks(path)?;
        Ok(Self {
            connection: Mutex::new(connection),
            path: path.to_owned(),
            workspace_id,
            session_id,
            _lock: lock,
            _legacy_lock: legacy_lock,
        })
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    pub fn read(path: &Path) -> Result<StoredSession, StoreError> {
        let (home, workspace_id, session_id) = Self::location(path)?;
        Self::read_session(home, &workspace_id, &session_id)
    }

    pub fn read_session(
        home: &Path,
        workspace_id: &WorkspaceId,
        session_id: &SessionId,
    ) -> Result<StoredSession, StoreError> {
        let mut connection = connect(home)?;
        import_legacy(home, &mut connection)?;
        Self::read_from(&connection, workspace_id, session_id)
    }

    fn read_from(
        connection: &Connection,
        workspace_id: &WorkspaceId,
        session_id: &SessionId,
    ) -> Result<StoredSession, StoreError> {
        let transaction = connection.unchecked_transaction().map_err(error)?;
        let (header, title): (String, Option<String>) = transaction
            .query_row(
                "SELECT header, title FROM sessions WHERE workspace_id=?1 AND session_id=?2",
                params![workspace_id.to_string(), session_id.to_string()],
                |row| Ok((row.get(0)?, row.get(1)?)),
            )
            .optional()
            .map_err(error)?
            .ok_or_else(|| error(format!("unknown session {session_id}")))?;
        let mut header: SessionHeader = serde_json::from_str(&header).map_err(error)?;
        header.title = title;
        let mut statement = transaction.prepare("SELECT envelope FROM events WHERE workspace_id=?1 AND session_id=?2 ORDER BY sequence").map_err(error)?;
        let rows = statement
            .query_map(
                params![workspace_id.to_string(), session_id.to_string()],
                |row| row.get::<_, String>(0),
            )
            .map_err(error)?;
        let events = rows
            .map(|row| serde_json::from_str(&row.map_err(error)?).map_err(error))
            .collect::<Result<Vec<_>, _>>()?;
        drop(statement);
        transaction.commit().map_err(error)?;
        Ok(StoredSession { header, events })
    }

    pub fn list_sessions(
        home: &Path,
        workspace_id: Option<&WorkspaceId>,
    ) -> Result<Vec<FileSessionListing>, StoreError> {
        let mut connection = connect(home)?;
        import_legacy(home, &mut connection)?;
        let mut statement = connection.prepare("SELECT header, title, updated_at_ms FROM sessions WHERE (?1 IS NULL OR workspace_id=?1) ORDER BY updated_at_ms DESC, session_id DESC").map_err(error)?;
        let rows = statement
            .query_map([workspace_id.map(ToString::to_string)], |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, Option<String>>(1)?,
                    row.get::<_, u64>(2)?,
                ))
            })
            .map_err(error)?;
        rows.map(|row| {
            let (header, title, modified_at_ms) = row.map_err(error)?;
            let mut header: SessionHeader = serde_json::from_str(&header).map_err(error)?;
            header.title = title;
            Ok(FileSessionListing {
                path: Self::session_path(home, &header.workspace_id, &header.id),
                header,
                modified_at_ms: Some(modified_at_ms),
            })
        })
        .collect()
    }

    pub fn set_title(&self, title: &str) -> Result<(), StoreError> {
        let title = title.trim();
        if title.is_empty() {
            return Err(error("session title must not be empty"));
        }
        self.connection.lock().map_err(error)?.execute("UPDATE sessions SET title=?3, updated_at_ms=?4 WHERE workspace_id=?1 AND session_id=?2", params![self.workspace_id.to_string(), self.session_id.to_string(), title, now_ms()]).map_err(error)?;
        Ok(())
    }

    pub fn append(
        &self,
        envelope: &EventEnvelope,
        visibility: EventVisibility,
    ) -> Result<(), StoreError> {
        if envelope.workspace_id != self.workspace_id || envelope.session_id != self.session_id {
            return Err(error("event does not belong to this session"));
        }
        let mut connection = self.connection.lock().map_err(error)?;
        // Reserve the writer before checking sequence. A deferred transaction
        // can lose its read snapshot when a background reader initializes
        // another connection, making the subsequent write fail immediately.
        let transaction = connection
            .transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)
            .map_err(error)?;
        let last_sequence: u64 = transaction.query_row(
            "SELECT COALESCE(MAX(sequence), 0) FROM events WHERE workspace_id=?1 AND session_id=?2",
            params![self.workspace_id.to_string(), self.session_id.to_string()], |row| row.get(0),
        ).map_err(error)?;
        if last_sequence.checked_add(1) != Some(envelope.sequence) {
            return Err(error("event sequence must continue the persisted history"));
        }
        transaction
            .execute(
                "INSERT INTO events VALUES (?1, ?2, ?3, ?4, ?5)",
                params![
                    self.workspace_id.to_string(),
                    self.session_id.to_string(),
                    envelope.sequence,
                    serde_json::to_string(&visibility).map_err(error)?,
                    serde_json::to_string(envelope).map_err(error)?
                ],
            )
            .map_err(error)?;
        transaction.execute("UPDATE sessions SET updated_at_ms=?3, title=COALESCE(title, ?4) WHERE workspace_id=?1 AND session_id=?2", params![self.workspace_id.to_string(), self.session_id.to_string(), now_ms(), title_for(&envelope.event)]).map_err(error)?;
        transaction.commit().map_err(error)
    }
}

impl SessionEventObserver for SqliteSessionStore {
    fn observe(&self, envelope: &EventEnvelope, visibility: EventVisibility) {
        if let Err(error) = self.append(envelope, visibility) {
            eprintln!(
                "structure: failed to persist session {}: {error}",
                self.session_id
            );
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use arabica_protocol::{CommandId, EventId};

    fn home(label: &str) -> PathBuf {
        std::env::temp_dir().join(format!(
            "arabica-sqlite-{label}-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ))
    }
    fn new_session() -> NewSession<'static> {
        // Stable fixture IDs without borrowing temporary values.
        static WORKSPACE: std::sync::OnceLock<WorkspaceId> = std::sync::OnceLock::new();
        static SESSION: std::sync::OnceLock<SessionId> = std::sync::OnceLock::new();
        NewSession {
            workspace_id: WORKSPACE.get_or_init(|| WorkspaceId::new("ws-1")),
            session_id: SESSION.get_or_init(|| SessionId::new("session-1")),
            cwd: Path::new("/workspace"),
            profile: None,
            instructions_sha256: None,
        }
    }
    fn event(sequence: u64, event: Event) -> EventEnvelope {
        EventEnvelope {
            protocol_version: PROTOCOL_VERSION.to_owned(),
            event_id: EventId::new(format!("event-{sequence}")),
            command_id: CommandId::new("command-1"),
            workspace_id: WorkspaceId::new("ws-1"),
            session_id: SessionId::new("session-1"),
            run_id: None,
            sequence,
            occurred_at_ms: 0,
            event,
        }
    }

    #[test]
    fn appends_remain_contiguous_during_background_repository_reads() {
        use arabica_session::SessionStore;
        let home = home("background-reads");
        let store = SqliteSessionStore::create(&home, new_session()).unwrap();
        store
            .append(
                &event(
                    1,
                    Event::SessionCreated {
                        workspace_id: WorkspaceId::new("ws-1"),
                    },
                ),
                EventVisibility::Client,
            )
            .unwrap();
        let barrier = std::sync::Arc::new(std::sync::Barrier::new(2));
        let reader = std::thread::spawn({
            let home = home.clone();
            let barrier = barrier.clone();
            move || {
                let repository = crate::SqliteSessionRepository::new(home);
                barrier.wait();
                for _ in 0..50 {
                    let stored = repository
                        .read(&WorkspaceId::new("ws-1"), &SessionId::new("session-1"))
                        .unwrap();
                    for (index, envelope) in stored.events.iter().enumerate() {
                        assert_eq!(envelope.sequence, index as u64 + 1);
                    }
                }
            }
        });
        barrier.wait();
        for sequence in 2..=51 {
            store
                .append(
                    &event(sequence, Event::SessionResumed),
                    EventVisibility::Client,
                )
                .unwrap();
        }
        reader.join().unwrap();
        assert_eq!(
            SqliteSessionStore::read(store.path()).unwrap().events.len(),
            51
        );
        drop(store);
        std::fs::remove_dir_all(home).unwrap();
    }

    #[test]
    fn title_and_internal_events_survive_reopen_and_manual_title_wins() {
        let home = home("reopen");
        let store = SqliteSessionStore::create(&home, new_session()).unwrap();
        let first = event(
            1,
            Event::SessionCreated {
                workspace_id: WorkspaceId::new("ws-1"),
            },
        );
        store.append(&first, EventVisibility::Client).unwrap();
        let second = event(
            2,
            Event::MessageAccepted {
                content: "  hello\nworld  ".to_owned(),
            },
        );
        store.append(&second, EventVisibility::Internal).unwrap();
        assert_eq!(
            SqliteSessionStore::read(store.path())
                .unwrap()
                .header
                .title
                .as_deref(),
            Some("hello world")
        );
        store.set_title("My title").unwrap();
        let path = store.path().to_owned();
        drop(store);
        let store = SqliteSessionStore::open_existing(&path).unwrap();
        store
            .append(
                &event(
                    3,
                    Event::MessageAccepted {
                        content: "later message".to_owned(),
                    },
                ),
                EventVisibility::Client,
            )
            .unwrap();
        let stored = SqliteSessionStore::read(&path).unwrap();
        assert_eq!(stored.header.title.as_deref(), Some("My title"));
        assert_eq!(stored.events.len(), 3);
        assert_eq!(stored.events[0], first);
        assert_eq!(stored.events[1], second);
        drop(store);
        std::fs::remove_dir_all(home).unwrap();
    }

    #[test]
    fn import_is_idempotent_and_preserves_legacy_bytes_and_latest_title() {
        let home = home("import");
        let legacy = FileSessionStore::create(&home, new_session()).unwrap();
        legacy.observe(
            &event(
                1,
                Event::SessionCreated {
                    workspace_id: WorkspaceId::new("ws-1"),
                },
            ),
            EventVisibility::Client,
        );
        legacy.observe(
            &event(
                2,
                Event::MessageAccepted {
                    content: "Legacy title".to_owned(),
                },
            ),
            EventVisibility::Client,
        );
        let path = legacy.path().to_owned();
        drop(legacy);
        let bytes = std::fs::read(&path).unwrap();
        let listing = SqliteSessionStore::list_sessions(&home, None)
            .unwrap()
            .remove(0);
        let store = SqliteSessionStore::open_existing(&listing.path).unwrap();
        store.set_title("Renamed").unwrap();
        assert_eq!(
            SqliteSessionStore::list_sessions(&home, None).unwrap()[0]
                .header
                .title
                .as_deref(),
            Some("Renamed")
        );
        assert_eq!(
            SqliteSessionStore::read(&listing.path)
                .unwrap()
                .events
                .len(),
            2
        );
        assert_eq!(std::fs::read(&path).unwrap(), bytes);
        assert!(FileSessionStore::open_existing(&path).is_err());
        drop(store);
        std::fs::remove_dir_all(home).unwrap();
    }

    #[test]
    fn duplicate_sequence_rolls_back_metadata_and_session_lock_is_exclusive() {
        let home = home("locks");
        let store = SqliteSessionStore::create(&home, new_session()).unwrap();
        assert!(SqliteSessionStore::open_existing(store.path()).is_err());
        assert!(SqliteSessionStore::create(&home, new_session()).is_err());
        let first = event(
            1,
            Event::SessionCreated {
                workspace_id: WorkspaceId::new("ws-1"),
            },
        );
        store.append(&first, EventVisibility::Client).unwrap();
        assert!(
            store
                .append(
                    &event(
                        1,
                        Event::MessageAccepted {
                            content: "Should roll back".to_owned()
                        }
                    ),
                    EventVisibility::Client
                )
                .is_err()
        );
        assert!(
            SqliteSessionStore::read(store.path())
                .unwrap()
                .header
                .title
                .is_none()
        );
        let mut foreign = event(2, Event::SessionResumed);
        foreign.workspace_id = WorkspaceId::new("other");
        assert!(store.append(&foreign, EventVisibility::Client).is_err());
        assert!(
            SqliteSessionStore::list_sessions(&home, Some(&WorkspaceId::new("other")))
                .unwrap()
                .is_empty()
        );
        drop(store);
        std::fs::remove_dir_all(home).unwrap();
    }
}
