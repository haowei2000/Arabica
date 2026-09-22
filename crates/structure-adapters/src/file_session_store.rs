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
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::time::{SystemTime, UNIX_EPOCH};

use serde::Serialize;
use structure_protocol::{Event, EventEnvelope, PROTOCOL_VERSION, SessionId, WorkspaceId};
use structure_session::{EventVisibility, SessionEventObserver};

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
    /// A hash of the system instructions in effect, once project
    /// instructions (`P2-5`, `AGENTS.md` discovery) exist to hash. `None`
    /// until then. The full text is never stored here -- it is already
    /// exact in the `model.request.prepared` Event; this is a cheap
    /// "did the effective instructions change" signal for tooling, not a
    /// second copy of the prompt.
    pub instructions_sha256: Option<&'a str>,
}

#[derive(Serialize)]
struct HeaderRecord<'a> {
    schema: &'a str,
    id: &'a SessionId,
    workspace_id: &'a WorkspaceId,
    cwd: String,
    created_at_ms: u64,
    protocol_version: &'a str,
    #[serde(skip_serializing_if = "Option::is_none")]
    profile: Option<&'a str>,
    #[serde(skip_serializing_if = "Option::is_none")]
    instructions_sha256: Option<&'a str>,
}

#[derive(Serialize)]
struct EventRecord<'a> {
    record: &'static str,
    visibility: EventVisibility,
    envelope: &'a EventEnvelope,
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

    pub fn path(&self) -> &Path {
        &self.path
    }

    fn write_header(&self, header: &NewSession<'_>) -> Result<(), StoreError> {
        let created_at_ms = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|duration| duration.as_millis() as u64)
            .unwrap_or(0);
        let record = HeaderRecord {
            schema: SCHEMA,
            id: header.session_id,
            workspace_id: header.workspace_id,
            cwd: header.cwd.display().to_string(),
            created_at_ms,
            protocol_version: PROTOCOL_VERSION,
            profile: header.profile,
            instructions_sha256: header.instructions_sha256,
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
}
