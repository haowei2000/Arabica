//! One session's canonical Events as an append-only JSONL file.
//!
//! Layout: `$STRUCTURE_HOME/sessions/<workspace_id>/<session_id>.jsonl`. The
//! first line is a header object; every following line is
//! `{"record":"event","visibility":...,"envelope":...}` for one
//! [`EventEnvelope`], written in the order `observe` receives them.
//! Internal Events are included -- a client-visible-only log cannot
//! reconstruct exact Runtime history, which restoring a session needs
//! (`docs/runtime_core_architecture.md`'s CLI/ACP extension plan, P2-2).

use std::error::Error;
use std::fmt::{Display, Formatter};
use std::fs::{File, OpenOptions};
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::time::{SystemTime, UNIX_EPOCH};

use serde::{Deserialize, Serialize};
use structure_protocol::{Event, EventEnvelope, PROTOCOL_VERSION, SessionId, WorkspaceId};
use structure_session::{EventVisibility, SessionEventObserver, SessionSnapshot};

/// `$STRUCTURE_HOME`'s default name inside the user's home directory.
const HOME_DIR_NAME: &str = ".structure";
const SESSIONS_DIR_NAME: &str = "sessions";
const SCHEMA: &str = "structure.session/v1";

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum StoreErrorKind {
    /// `$STRUCTURE_HOME` could not be resolved (no `$STRUCTURE_HOME` and no `$HOME`).
    NoHome,
    /// A session or workspace id was not safe to use as a path component.
    InvalidId,
    /// The session file, or a directory on its path, could not be created,
    /// opened, or locked.
    Io,
}

#[derive(Debug)]
pub struct StoreError {
    kind: StoreErrorKind,
    message: String,
}

impl StoreError {
    fn new(kind: StoreErrorKind, message: impl Into<String>) -> Self {
        Self {
            kind,
            message: message.into(),
        }
    }

    pub fn kind(&self) -> StoreErrorKind {
        self.kind
    }
}

impl Display for StoreError {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        self.message.fmt(formatter)
    }
}

impl Error for StoreError {}

/// The default `$STRUCTURE_HOME`: the `STRUCTURE_HOME` environment variable
/// if set, otherwise `$HOME/.structure`.
pub fn default_structure_home() -> Result<PathBuf, StoreError> {
    structure_home_from(|name| std::env::var(name).ok())
}

/// `default_structure_home`'s logic, reading through an injected `lookup`
/// rather than `std::env::var` directly so it is testable without mutating
/// the real process environment (`std::env::set_var` is `unsafe` as of Rust
/// 2024, and this workspace forbids `unsafe` outright) -- the same seam
/// `structure-cli`'s `resolve_provider_config` uses for the same reason.
fn structure_home_from(lookup: impl Fn(&str) -> Option<String>) -> Result<PathBuf, StoreError> {
    if let Some(value) = lookup("STRUCTURE_HOME") {
        return Ok(PathBuf::from(value));
    }
    let home = lookup("HOME").ok_or_else(|| {
        StoreError::new(
            StoreErrorKind::NoHome,
            "cannot resolve a session store location: neither STRUCTURE_HOME nor HOME is set",
        )
    })?;
    Ok(PathBuf::from(home).join(HOME_DIR_NAME))
}

/// Rejects an id that is not safe to use as one path component: empty, or
/// containing a path separator or a `.`/`..` traversal segment. Every
/// current caller mints ids internally (`IdAllocator`), so this is
/// defense in depth against a future caller that does not, not a response
/// to an observed problem.
fn validate_path_component(value: &str, field: &str) -> Result<(), StoreError> {
    if value.is_empty() || value == "." || value == ".." || value.contains(['/', '\\']) {
        return Err(StoreError::new(
            StoreErrorKind::InvalidId,
            format!("{field} is not safe to use as a path component: {value:?}"),
        ));
    }
    Ok(())
}

/// The header fields a caller supplies when starting a new session's file.
/// `schema`, `created_at_ms`, and `protocol_version` are the store's own to
/// set, not the caller's, since a caller could otherwise write an
/// inconsistent value.
pub struct NewSession<'a> {
    pub session_id: &'a SessionId,
    pub workspace_id: &'a WorkspaceId,
    pub cwd: &'a Path,
    /// The provider profile in effect, once `structure-cli` has profiles
    /// (`P2-4`). `None` until then; a host without profiles yet has nothing
    /// honest to put here.
    pub profile: Option<&'a str>,
    /// A hash of the system instructions in effect at session creation:
    /// `structure-cli` fills this from its `AGENTS.md` discovery; hosts
    /// without project instructions pass `None`. The full text is never
    /// stored here -- it is already exact in the `model.request.prepared`
    /// Event; this is a cheap "did the effective instructions change"
    /// signal for tooling, not a second copy of the prompt.
    pub instructions_sha256: Option<&'a str>,
}

/// A session file's header line, parsed or about to be written. Owned (not
/// borrowed like the rest of this module's write path) because it is also
/// this module's read-side return value, where there is no caller-owned
/// data to borrow from.
#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct SessionHeader {
    pub schema: String,
    pub id: SessionId,
    pub workspace_id: WorkspaceId,
    pub cwd: String,
    pub created_at_ms: u64,
    pub protocol_version: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub profile: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub instructions_sha256: Option<String>,
}

#[derive(Serialize)]
struct EventRecord<'a> {
    record: &'static str,
    visibility: EventVisibility,
    envelope: &'a EventEnvelope,
}

#[derive(Deserialize)]
struct StoredEventRecord {
    #[expect(
        dead_code,
        reason = "present in every line for humans/tools reading the file directly; nothing here needs to branch on it, since a session file has no other record kind yet"
    )]
    record: String,
    #[expect(
        dead_code,
        reason = "read back for completeness; nothing here re-derives visibility from it today, since Event::is_client_visible is already the source of truth"
    )]
    visibility: EventVisibility,
    envelope: EventEnvelope,
}

/// One session file's header and however much of its Event history could be
/// read. `events` may be shorter than what was truly written if the file's
/// last line was left mid-write by a crash -- see [`FileSessionStore::read`].
#[derive(Debug)]
pub struct StoredSession {
    pub header: SessionHeader,
    pub events: Vec<EventEnvelope>,
}

impl StoredSession {
    /// This session's Events as a [`SessionSnapshot`], ready for
    /// `structure_session::SessionManager::restore_session`.
    pub fn into_snapshot(self) -> SessionSnapshot {
        SessionSnapshot {
            events: self.events,
        }
    }
}

/// One session's header plus where its file lives and when it was last
/// touched, for listing sessions without reading each one's full history.
pub struct SessionListing {
    pub header: SessionHeader,
    pub path: PathBuf,
    /// The file's own modification time, read from filesystem metadata as a
    /// cheap proxy for "last activity" -- every Event this session ever
    /// recorded touched the file, so this is accurate without reading the
    /// file's body at all. `None` only if the platform or filesystem
    /// cannot report it.
    pub modified_at_ms: Option<u64>,
}

fn is_run_boundary(event: &Event) -> bool {
    matches!(
        event,
        Event::RunCompleted { .. } | Event::RunFailed { .. } | Event::RunCancelled
    )
}

/// A [`structure_session::SessionEventObserver`] that appends every Event it
/// is shown to one session's JSONL file. Constructed once per session and
/// reused across every `dispatch` call for that session's whole lifetime
/// (every `message.send`, not just the first) -- a persistent log needs the
/// same sink across calls, unlike `structure-cli`'s ACP/print observers,
/// which are built fresh per call because they only ever forward one call's
/// Events live.
#[derive(Debug)]
pub struct FileSessionStore {
    file: Mutex<File>,
    path: PathBuf,
}

impl FileSessionStore {
    /// Where `create` would put this session's file, without creating
    /// anything. Used by a restore path (`P2-2`) to find an existing file
    /// from a session id alone.
    pub fn session_path(
        structure_home: &Path,
        workspace_id: &WorkspaceId,
        session_id: &SessionId,
    ) -> PathBuf {
        structure_home
            .join(SESSIONS_DIR_NAME)
            .join(workspace_id.to_string())
            .join(format!("{session_id}.jsonl"))
    }

    /// Creates a brand-new session file and writes its header.
    ///
    /// Fails if the file already exists rather than silently reusing or
    /// overwriting it: a session id collision (a sequential-id host
    /// restarting, say) must never splice a new session's history onto an
    /// old one's bytes. Directories and the file are created with the
    /// intended permissions as part of the same syscall that creates them
    /// (`DirBuilder`/`OpenOptions` `.mode(...)`), so there is no window
    /// where they exist with looser, default permissions.
    pub fn create(structure_home: &Path, header: NewSession<'_>) -> Result<Self, StoreError> {
        validate_path_component(&header.session_id.to_string(), "session_id")?;
        validate_path_component(&header.workspace_id.to_string(), "workspace_id")?;

        let sessions_dir = structure_home.join(SESSIONS_DIR_NAME);
        std::fs::create_dir_all(&sessions_dir)
            .map_err(|error| io_error(&sessions_dir, "create", error))?;

        let workspace_dir = sessions_dir.join(header.workspace_id.to_string());
        create_private_dir(&workspace_dir)?;

        let path = workspace_dir.join(format!("{}.jsonl", header.session_id));
        let file = open_private_new_file(&path)?;
        lock_exclusive(&file, &path)?;

        let store = Self {
            file: Mutex::new(file),
            path,
        };
        store.write_header(&header)?;
        Ok(store)
    }

    /// Reopens an existing session file for append -- the write-side
    /// counterpart of restoring one with [`Self::read`]: a header already
    /// exists and is not rewritten, and every Event `observe` is shown from
    /// here on is appended after whatever the file already held, continuing
    /// its history rather than starting a new one.
    ///
    /// Takes the same exclusive lock [`Self::create`] does, so a session
    /// cannot be resumed by two processes (or twice by one) at once, and
    /// fails if `path` does not already exist: this is deliberately not a
    /// "create if missing" convenience, since a caller reopening a path it
    /// just got from [`Self::list_sessions`] or [`Self::read`] should never
    /// be surprised by a silently created empty file when its assumption
    /// that the session exists was wrong.
    pub fn open_existing(path: &Path) -> Result<Self, StoreError> {
        let file = OpenOptions::new()
            .append(true)
            .open(path)
            .map_err(|error| io_error(path, "reopen", error))?;
        lock_exclusive(&file, path)?;
        Ok(Self {
            file: Mutex::new(file),
            path: path.to_path_buf(),
        })
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    fn write_header(&self, header: &NewSession<'_>) -> Result<(), StoreError> {
        let created_at_ms = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|duration| duration.as_millis() as u64)
            .unwrap_or(0);
        let record = SessionHeader {
            schema: SCHEMA.to_owned(),
            id: header.session_id.clone(),
            workspace_id: header.workspace_id.clone(),
            cwd: header.cwd.display().to_string(),
            created_at_ms,
            protocol_version: PROTOCOL_VERSION.to_owned(),
            profile: header.profile.map(str::to_owned),
            instructions_sha256: header.instructions_sha256.map(str::to_owned),
        };
        self.write_line(&record, false)
    }

    fn write_line(&self, record: &impl Serialize, sync: bool) -> Result<(), StoreError> {
        let line = serde_json::to_string(record).map_err(|error| {
            StoreError::new(
                StoreErrorKind::Io,
                format!("failed to serialize a record: {error}"),
            )
        })?;
        let mut file = self.file.lock().expect("session file mutex poisoned");
        writeln!(file, "{line}").map_err(|error| io_error(&self.path, "write to", error))?;
        file.flush()
            .map_err(|error| io_error(&self.path, "flush", error))?;
        if sync {
            // Best-effort: a run boundary that fails to reach disk is a
            // durability problem to surface (below), never a reason to
            // fail the run itself -- the in-memory Event Log is still
            // authoritative for the caller's own turn.
            let _ = file.sync_data();
        }
        Ok(())
    }

    /// Reads a session file back into its header and Events.
    ///
    /// Tolerates the last line being incomplete: `observe` flushes after
    /// every Event, but a crash can still land mid-`write_all` and leave a
    /// truncated final line. If only the last line fails to parse, this
    /// returns everything before it rather than erroring -- that Event
    /// never durably finished writing, so treating it as never having
    /// happened is correct, not lossy. A failure anywhere *else* in the
    /// file is not tolerated: that is corruption, not an interrupted
    /// write, and a caller trusting the result would silently restore from
    /// a history with an unexplained hole in the middle.
    pub fn read(path: &Path) -> Result<StoredSession, StoreError> {
        let content =
            std::fs::read_to_string(path).map_err(|error| io_error(path, "read", error))?;
        let mut lines = content.lines();
        let header_line = lines.next().ok_or_else(|| {
            StoreError::new(
                StoreErrorKind::Io,
                format!("{} has no header line", path.display()),
            )
        })?;
        let header: SessionHeader = serde_json::from_str(header_line).map_err(|error| {
            StoreError::new(
                StoreErrorKind::Io,
                format!("{} has an unreadable header: {error}", path.display()),
            )
        })?;

        let remaining: Vec<&str> = lines.collect();
        let mut events = Vec::with_capacity(remaining.len());
        for (index, line) in remaining.iter().enumerate() {
            match serde_json::from_str::<StoredEventRecord>(line) {
                Ok(record) => events.push(record.envelope),
                Err(error) => {
                    if index + 1 == remaining.len() {
                        break;
                    }
                    return Err(StoreError::new(
                        StoreErrorKind::Io,
                        format!(
                            "{} line {} is corrupt (not the file's last line, so not tolerated as a truncated write): {error}",
                            path.display(),
                            index + 2
                        ),
                    ));
                }
            }
        }
        Ok(StoredSession { header, events })
    }

    /// [`Self::read`] at the path [`Self::session_path`] would compute for
    /// this workspace/session id pair.
    pub fn read_session(
        structure_home: &Path,
        workspace_id: &WorkspaceId,
        session_id: &SessionId,
    ) -> Result<StoredSession, StoreError> {
        Self::read(&Self::session_path(
            structure_home,
            workspace_id,
            session_id,
        ))
    }

    /// Every session file under `$STRUCTURE_HOME/sessions/`, or just one
    /// workspace's, newest first by file modification time.
    ///
    /// Reads only each file's header line, not its full history, so this
    /// stays fast no matter how long any one session's log has grown. A
    /// file that cannot be read as a session (an unrelated file dropped in
    /// that directory, say, or one this process cannot open) is skipped
    /// rather than failing the whole listing.
    pub fn list_sessions(
        structure_home: &Path,
        workspace_id: Option<&WorkspaceId>,
    ) -> Result<Vec<SessionListing>, StoreError> {
        let sessions_dir = structure_home.join(SESSIONS_DIR_NAME);
        if !sessions_dir.is_dir() {
            return Ok(Vec::new());
        }
        let workspace_dirs: Vec<PathBuf> = match workspace_id {
            Some(workspace_id) => vec![sessions_dir.join(workspace_id.to_string())],
            None => std::fs::read_dir(&sessions_dir)
                .map_err(|error| io_error(&sessions_dir, "read", error))?
                .filter_map(|entry| entry.ok())
                .filter(|entry| entry.file_type().is_ok_and(|kind| kind.is_dir()))
                .map(|entry| entry.path())
                .collect(),
        };

        let mut listings = Vec::new();
        for workspace_dir in workspace_dirs {
            let Ok(files) = std::fs::read_dir(&workspace_dir) else {
                continue;
            };
            for file_entry in files.filter_map(|entry| entry.ok()) {
                let path = file_entry.path();
                if path.extension().and_then(|extension| extension.to_str()) != Some("jsonl") {
                    continue;
                }
                let Some(header) = read_header_line(&path) else {
                    continue;
                };
                let modified_at_ms = file_entry
                    .metadata()
                    .ok()
                    .and_then(|metadata| metadata.modified().ok())
                    .and_then(|modified| modified.duration_since(UNIX_EPOCH).ok())
                    .map(|duration| duration.as_millis() as u64);
                listings.push(SessionListing {
                    header,
                    path,
                    modified_at_ms,
                });
            }
        }
        listings.sort_by_key(|listing| std::cmp::Reverse(listing.modified_at_ms));
        Ok(listings)
    }
}

fn read_header_line(path: &Path) -> Option<SessionHeader> {
    let file = File::open(path).ok()?;
    let first_line = BufReader::new(file).lines().next()?.ok()?;
    serde_json::from_str(&first_line).ok()
}

impl SessionEventObserver for FileSessionStore {
    fn observe(&self, envelope: &EventEnvelope, visibility: EventVisibility) {
        let record = EventRecord {
            record: "event",
            visibility,
            envelope,
        };
        if let Err(error) = self.write_line(&record, is_run_boundary(&envelope.event)) {
            eprintln!(
                "structure: failed to persist an Event to {}: {error}",
                self.path.display()
            );
        }
    }
}

fn io_error(path: &Path, action: &str, error: std::io::Error) -> StoreError {
    StoreError::new(
        StoreErrorKind::Io,
        format!("failed to {action} {}: {error}", path.display()),
    )
}

/// Creates `path` as a directory with `0700` if it does not already exist;
/// if it does, brings its permissions to `0700` regardless of how it got
/// there, since every other decision in this module assumes the directory
/// only a Structure session's own files, readable only by their owner.
fn create_private_dir(path: &Path) -> Result<(), StoreError> {
    if path.is_dir() {
        return set_permissions(path, 0o700);
    }
    let mut builder = std::fs::DirBuilder::new();
    #[cfg(unix)]
    {
        use std::os::unix::fs::DirBuilderExt;
        builder.mode(0o700);
    }
    builder
        .create(path)
        .map_err(|error| io_error(path, "create", error))
}

fn open_private_new_file(path: &Path) -> Result<File, StoreError> {
    let mut options = OpenOptions::new();
    options.create_new(true).append(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    options
        .open(path)
        .map_err(|error| io_error(path, "create", error))
}

#[cfg(unix)]
fn set_permissions(path: &Path, mode: u32) -> Result<(), StoreError> {
    use std::os::unix::fs::PermissionsExt;
    std::fs::set_permissions(path, std::fs::Permissions::from_mode(mode))
        .map_err(|error| io_error(path, "set permissions on", error))
}

#[cfg(not(unix))]
fn set_permissions(_path: &Path, _mode: u32) -> Result<(), StoreError> {
    Ok(())
}

/// Takes a non-blocking exclusive advisory lock on `file`, so two processes
/// (or two accidental opens in one process) never interleave writes to the
/// same session file. Fails fast rather than waiting: a session file being
/// locked means another live process already owns this session, and a
/// second writer queueing behind it would still corrupt turn-by-turn
/// ordering once both eventually write.
#[cfg(unix)]
fn lock_exclusive(file: &File, path: &Path) -> Result<(), StoreError> {
    rustix::fs::flock(file, rustix::fs::FlockOperation::NonBlockingLockExclusive).map_err(|error| {
        StoreError::new(
            StoreErrorKind::Io,
            format!(
                "could not lock {} (already open elsewhere?): {error}",
                path.display()
            ),
        )
    })
}

#[cfg(not(unix))]
fn lock_exclusive(_file: &File, _path: &Path) -> Result<(), StoreError> {
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use structure_protocol::{CommandId, EventId};

    fn temp_home(label: &str) -> PathBuf {
        let unique = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .expect("clock is valid")
            .as_nanos();
        std::env::temp_dir().join(format!("structure-adapters-{label}-{unique}"))
    }

    fn envelope(sequence: u64, event: Event) -> EventEnvelope {
        EventEnvelope {
            protocol_version: PROTOCOL_VERSION.to_owned(),
            event_id: EventId::new(format!("event-{sequence}")),
            command_id: CommandId::new("command-1"),
            workspace_id: WorkspaceId::new("ws-1"),
            session_id: SessionId::new("session-1"),
            run_id: Some(structure_protocol::RunId::new("run-1")),
            sequence,
            occurred_at_ms: 0,
            event,
        }
    }

    fn read_lines(path: &Path) -> Vec<serde_json::Value> {
        std::fs::read_to_string(path)
            .expect("file readable")
            .lines()
            .map(|line| serde_json::from_str(line).expect("line is JSON"))
            .collect()
    }

    #[test]
    fn create_writes_a_header_line_with_the_documented_schema() {
        let home = temp_home("header");
        let session_id = SessionId::new("session-1");
        let workspace_id = WorkspaceId::new("ws-1");
        let store = FileSessionStore::create(
            &home,
            NewSession {
                session_id: &session_id,
                workspace_id: &workspace_id,
                cwd: Path::new("/repo"),
                profile: None,
                instructions_sha256: None,
            },
        )
        .expect("store creates");

        let lines = read_lines(store.path());
        assert_eq!(lines.len(), 1);
        assert_eq!(lines[0]["schema"], "structure.session/v1");
        assert_eq!(lines[0]["id"], "session-1");
        assert_eq!(lines[0]["workspace_id"], "ws-1");
        assert_eq!(lines[0]["cwd"], "/repo");
        assert_eq!(lines[0]["protocol_version"], PROTOCOL_VERSION);
        assert!(lines[0]["created_at_ms"].as_u64().unwrap() > 0);
        assert!(
            lines[0].get("profile").is_none(),
            "None must be omitted, not null"
        );

        std::fs::remove_dir_all(&home).ok();
    }

    #[test]
    fn observe_appends_one_line_per_event_including_internal_ones() {
        let home = temp_home("append");
        let session_id = SessionId::new("session-1");
        let workspace_id = WorkspaceId::new("ws-1");
        let store = FileSessionStore::create(
            &home,
            NewSession {
                session_id: &session_id,
                workspace_id: &workspace_id,
                cwd: Path::new("/repo"),
                profile: None,
                instructions_sha256: None,
            },
        )
        .expect("store creates");

        store.observe(&envelope(1, Event::RunScheduled), EventVisibility::Client);
        store.observe(
            &envelope(
                2,
                Event::ModelRequestPrepared {
                    model_step: 1,
                    request: structure_model::RuntimeRequest {
                        model: "test-model".to_owned(),
                        items: vec![],
                        tools: vec![],
                        tool_choice: structure_model::ToolChoice::Auto,
                        generation: structure_model::RuntimeGenerationConfig::default(),
                    },
                },
            ),
            EventVisibility::Internal,
        );

        let lines = read_lines(store.path());
        assert_eq!(lines.len(), 3, "header + two events");
        assert_eq!(lines[1]["record"], "event");
        assert_eq!(lines[1]["visibility"], "client");
        assert_eq!(lines[1]["envelope"]["sequence"], 1);
        assert_eq!(lines[1]["envelope"]["event"]["type"], "run.scheduled");
        assert_eq!(lines[2]["visibility"], "internal");
        assert_eq!(
            lines[2]["envelope"]["event"]["type"], "model.request.prepared",
            "internal Events must still be persisted: they are needed to restore exact Runtime history"
        );

        std::fs::remove_dir_all(&home).ok();
    }

    #[test]
    fn a_second_create_with_the_same_ids_fails_instead_of_overwriting() {
        let home = temp_home("collision");
        let session_id = SessionId::new("session-1");
        let workspace_id = WorkspaceId::new("ws-1");
        let header = || NewSession {
            session_id: &session_id,
            workspace_id: &workspace_id,
            cwd: Path::new("/repo"),
            profile: None,
            instructions_sha256: None,
        };
        let first = FileSessionStore::create(&home, header()).expect("first store creates");
        let second = FileSessionStore::create(&home, header());
        assert!(
            second.is_err(),
            "a colliding session id must not silently overwrite the first session's file"
        );
        drop(first);

        std::fs::remove_dir_all(&home).ok();
    }

    #[test]
    fn an_invalid_id_is_rejected_before_touching_the_filesystem() {
        let home = temp_home("invalid-id");
        let session_id = SessionId::new("../escape");
        let workspace_id = WorkspaceId::new("ws-1");
        let result = FileSessionStore::create(
            &home,
            NewSession {
                session_id: &session_id,
                workspace_id: &workspace_id,
                cwd: Path::new("/repo"),
                profile: None,
                instructions_sha256: None,
            },
        );
        assert!(result.is_err());
        assert_eq!(result.unwrap_err().kind(), StoreErrorKind::InvalidId);
        assert!(
            !home.exists(),
            "no directory should be created for a rejected id"
        );
    }

    #[cfg(unix)]
    #[test]
    fn the_session_directory_and_file_are_private_to_their_owner() {
        use std::os::unix::fs::PermissionsExt;

        let home = temp_home("perms");
        let session_id = SessionId::new("session-1");
        let workspace_id = WorkspaceId::new("ws-1");
        let store = FileSessionStore::create(
            &home,
            NewSession {
                session_id: &session_id,
                workspace_id: &workspace_id,
                cwd: Path::new("/repo"),
                profile: None,
                instructions_sha256: None,
            },
        )
        .expect("store creates");

        let dir_mode = std::fs::metadata(store.path().parent().unwrap())
            .unwrap()
            .permissions()
            .mode()
            & 0o777;
        let file_mode = std::fs::metadata(store.path())
            .unwrap()
            .permissions()
            .mode()
            & 0o777;
        assert_eq!(dir_mode, 0o700, "session directory must be 0700");
        assert_eq!(file_mode, 0o600, "session file must be 0600");

        std::fs::remove_dir_all(&home).ok();
    }

    #[cfg(unix)]
    #[test]
    fn a_second_open_of_the_same_file_while_the_first_is_live_is_rejected() {
        // Exercises the lock directly (not through `create`, which already
        // refuses a second `create_new` on its own): opening the same path
        // for append while the first store's lock is still held must fail,
        // proving the lock -- not just `create_new` -- is the actual guard
        // once a restore path (P2-2) starts re-opening existing files.
        let home = temp_home("lock");
        let session_id = SessionId::new("session-1");
        let workspace_id = WorkspaceId::new("ws-1");
        let store = FileSessionStore::create(
            &home,
            NewSession {
                session_id: &session_id,
                workspace_id: &workspace_id,
                cwd: Path::new("/repo"),
                profile: None,
                instructions_sha256: None,
            },
        )
        .expect("store creates");

        let reopened = OpenOptions::new()
            .append(true)
            .open(store.path())
            .expect("path reopens for the OS");
        let lock_result = rustix::fs::flock(
            &reopened,
            rustix::fs::FlockOperation::NonBlockingLockExclusive,
        );
        assert!(
            lock_result.is_err(),
            "a second exclusive lock attempt must fail while the first store still holds the file open"
        );

        std::fs::remove_dir_all(&home).ok();
    }

    fn env_map(vars: &[(&str, &str)]) -> impl Fn(&str) -> Option<String> + use<> {
        let vars: std::collections::HashMap<String, String> = vars
            .iter()
            .map(|(name, value)| ((*name).to_owned(), (*value).to_owned()))
            .collect();
        move |name: &str| vars.get(name).cloned()
    }

    #[test]
    fn structure_home_prefers_the_override_variable_over_home() {
        let lookup = env_map(&[("STRUCTURE_HOME", "/custom/home"), ("HOME", "/home/user")]);
        assert_eq!(
            structure_home_from(lookup).unwrap(),
            PathBuf::from("/custom/home")
        );
    }

    #[test]
    fn structure_home_falls_back_to_home_dot_structure() {
        let lookup = env_map(&[("HOME", "/home/user")]);
        assert_eq!(
            structure_home_from(lookup).unwrap(),
            PathBuf::from("/home/user/.structure")
        );
    }

    #[test]
    fn structure_home_errors_when_neither_variable_is_set() {
        let lookup = env_map(&[]);
        let error = structure_home_from(lookup).unwrap_err();
        assert_eq!(error.kind(), StoreErrorKind::NoHome);
    }

    #[test]
    fn read_recovers_exactly_what_create_and_observe_wrote() {
        let home = temp_home("read-roundtrip");
        let session_id = SessionId::new("session-1");
        let workspace_id = WorkspaceId::new("ws-1");
        let store = FileSessionStore::create(
            &home,
            NewSession {
                session_id: &session_id,
                workspace_id: &workspace_id,
                cwd: Path::new("/repo"),
                profile: Some("default"),
                instructions_sha256: Some("abc123"),
            },
        )
        .expect("store creates");
        store.observe(&envelope(1, Event::RunScheduled), EventVisibility::Client);
        store.observe(&envelope(2, Event::RunStarted), EventVisibility::Client);

        let stored = FileSessionStore::read(store.path()).expect("file reads back");
        assert_eq!(stored.header.id, session_id);
        assert_eq!(stored.header.workspace_id, workspace_id);
        assert_eq!(stored.header.cwd, "/repo");
        assert_eq!(stored.header.profile.as_deref(), Some("default"));
        assert_eq!(stored.header.instructions_sha256.as_deref(), Some("abc123"));
        assert_eq!(stored.events.len(), 2);
        assert!(matches!(stored.events[0].event, Event::RunScheduled));
        assert!(matches!(stored.events[1].event, Event::RunStarted));

        let snapshot = stored.into_snapshot();
        assert_eq!(snapshot.events.len(), 2);

        std::fs::remove_dir_all(&home).ok();
    }

    #[test]
    fn read_session_finds_the_same_file_create_would_have_written() {
        let home = temp_home("read-by-id");
        let session_id = SessionId::new("session-1");
        let workspace_id = WorkspaceId::new("ws-1");
        FileSessionStore::create(
            &home,
            NewSession {
                session_id: &session_id,
                workspace_id: &workspace_id,
                cwd: Path::new("/repo"),
                profile: None,
                instructions_sha256: None,
            },
        )
        .expect("store creates");

        let stored =
            FileSessionStore::read_session(&home, &workspace_id, &session_id).expect("reads by id");
        assert_eq!(stored.header.id, session_id);

        std::fs::remove_dir_all(&home).ok();
    }

    #[test]
    fn open_existing_appends_after_the_original_history_without_rewriting_the_header() {
        let home = temp_home("reopen");
        let session_id = SessionId::new("session-1");
        let workspace_id = WorkspaceId::new("ws-1");
        let path = {
            let store = FileSessionStore::create(
                &home,
                NewSession {
                    session_id: &session_id,
                    workspace_id: &workspace_id,
                    cwd: Path::new("/repo"),
                    profile: None,
                    instructions_sha256: None,
                },
            )
            .expect("store creates");
            store.observe(&envelope(1, Event::RunScheduled), EventVisibility::Client);
            store.path().to_path_buf()
            // `store` drops here, releasing its lock before reopening.
        };

        let reopened = FileSessionStore::open_existing(&path).expect("reopens for append");
        reopened.observe(&envelope(2, Event::RunStarted), EventVisibility::Client);
        drop(reopened);

        let stored = FileSessionStore::read(&path).expect("reads back");
        assert_eq!(
            stored.header.id, session_id,
            "the original header is untouched"
        );
        assert_eq!(
            stored.events.len(),
            2,
            "the new Event is appended after the original one"
        );
        assert!(matches!(stored.events[0].event, Event::RunScheduled));
        assert!(matches!(stored.events[1].event, Event::RunStarted));

        std::fs::remove_dir_all(&home).ok();
    }

    #[test]
    fn open_existing_fails_on_a_path_that_does_not_exist() {
        let home = temp_home("reopen-missing");
        let missing = home
            .join("sessions")
            .join("ws-1")
            .join("no-such-session.jsonl");
        let error = FileSessionStore::open_existing(&missing)
            .expect_err("there is nothing to reopen at this path");
        assert_eq!(error.kind(), StoreErrorKind::Io);
    }

    #[cfg(unix)]
    #[test]
    fn open_existing_is_rejected_while_the_original_store_still_holds_the_lock() {
        let home = temp_home("reopen-locked");
        let session_id = SessionId::new("session-1");
        let workspace_id = WorkspaceId::new("ws-1");
        let store = FileSessionStore::create(
            &home,
            NewSession {
                session_id: &session_id,
                workspace_id: &workspace_id,
                cwd: Path::new("/repo"),
                profile: None,
                instructions_sha256: None,
            },
        )
        .expect("store creates");

        let error = FileSessionStore::open_existing(store.path())
            .expect_err("the original store's lock must still be held");
        assert_eq!(error.kind(), StoreErrorKind::Io);

        std::fs::remove_dir_all(&home).ok();
    }

    #[test]
    fn read_tolerates_a_truncated_final_line_but_keeps_everything_before_it() {
        let home = temp_home("read-truncated");
        let session_id = SessionId::new("session-1");
        let workspace_id = WorkspaceId::new("ws-1");
        let store = FileSessionStore::create(
            &home,
            NewSession {
                session_id: &session_id,
                workspace_id: &workspace_id,
                cwd: Path::new("/repo"),
                profile: None,
                instructions_sha256: None,
            },
        )
        .expect("store creates");
        store.observe(&envelope(1, Event::RunScheduled), EventVisibility::Client);
        drop(store); // release the lock so this test can reopen the file directly

        // Simulate a crash mid-write: a well-formed Event, then a line cut
        // off partway through, exactly what an interrupted `write_all`
        // would leave behind.
        let mut file = OpenOptions::new()
            .append(true)
            .open(FileSessionStore::session_path(
                &home,
                &workspace_id,
                &session_id,
            ))
            .expect("file reopens");
        // No leading newline here: `observe`'s `writeln!` for event 1
        // already terminated the previous line, so this continues directly
        // as the file's next (here, final and incomplete) line.
        write!(
            file,
            "{{\"record\":\"event\",\"visibility\":\"client\",\"envelope\":{{\"sequence\":2,\"event\":{{\"typ"
        )
        .expect("partial line writes");
        file.flush().expect("flush succeeds");
        drop(file);

        let stored = FileSessionStore::read(&FileSessionStore::session_path(
            &home,
            &workspace_id,
            &session_id,
        ))
        .expect("a truncated last line must not fail the whole read");
        assert_eq!(
            stored.events.len(),
            1,
            "only the one fully-written Event survives; the truncated one is dropped, not guessed at"
        );

        std::fs::remove_dir_all(&home).ok();
    }

    #[test]
    fn read_does_not_tolerate_corruption_in_the_middle_of_the_file() {
        let home = temp_home("read-corrupt-middle");
        let session_id = SessionId::new("session-1");
        let workspace_id = WorkspaceId::new("ws-1");
        let store = FileSessionStore::create(
            &home,
            NewSession {
                session_id: &session_id,
                workspace_id: &workspace_id,
                cwd: Path::new("/repo"),
                profile: None,
                instructions_sha256: None,
            },
        )
        .expect("store creates");
        store.observe(&envelope(1, Event::RunScheduled), EventVisibility::Client);
        drop(store);

        let path = FileSessionStore::session_path(&home, &workspace_id, &session_id);
        let mut file = OpenOptions::new()
            .append(true)
            .open(&path)
            .expect("file reopens");
        // A corrupt line in the middle, followed by a well-formed one: the
        // well-formed line at the end must not make this look like a
        // "truncated last line" and get tolerated.
        writeln!(file, "not json at all").expect("corrupt line writes");
        writeln!(
            file,
            "{{\"record\":\"event\",\"visibility\":\"client\",\"envelope\":{{\"protocol_version\":\"1.0\",\"event_id\":\"event-3\",\"command_id\":\"command-1\",\"workspace_id\":\"ws-1\",\"session_id\":\"session-1\",\"sequence\":3,\"occurred_at_ms\":0,\"event\":{{\"type\":\"run.started\"}}}}}}"
        )
        .expect("well-formed line writes");
        file.flush().expect("flush succeeds");
        drop(file);

        let error = FileSessionStore::read(&path)
            .expect_err("corruption before the last line must not be silently tolerated");
        assert_eq!(error.kind(), StoreErrorKind::Io);

        std::fs::remove_dir_all(&home).ok();
    }

    #[test]
    fn list_sessions_finds_headers_without_needing_the_full_file() {
        let home = temp_home("list");
        let workspace_id = WorkspaceId::new("ws-1");
        for label in ["session-1", "session-2"] {
            FileSessionStore::create(
                &home,
                NewSession {
                    session_id: &SessionId::new(label),
                    workspace_id: &workspace_id,
                    cwd: Path::new("/repo"),
                    profile: None,
                    instructions_sha256: None,
                },
            )
            .expect("store creates");
        }
        let other_workspace = WorkspaceId::new("ws-2");
        FileSessionStore::create(
            &home,
            NewSession {
                session_id: &SessionId::new("session-3"),
                workspace_id: &other_workspace,
                cwd: Path::new("/other"),
                profile: None,
                instructions_sha256: None,
            },
        )
        .expect("store creates");

        let all = FileSessionStore::list_sessions(&home, None).expect("lists");
        assert_eq!(all.len(), 3);

        let scoped = FileSessionStore::list_sessions(&home, Some(&workspace_id)).expect("lists");
        assert_eq!(scoped.len(), 2);
        assert!(
            scoped
                .iter()
                .all(|listing| listing.header.workspace_id == workspace_id)
        );

        std::fs::remove_dir_all(&home).ok();
    }

    #[test]
    fn list_sessions_on_a_structure_home_with_no_sessions_yet_is_empty_not_an_error() {
        let home = temp_home("list-empty");
        let listings = FileSessionStore::list_sessions(&home, None)
            .expect("a missing sessions directory is not an error");
        assert!(listings.is_empty());
    }
}
