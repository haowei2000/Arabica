//! Backend-independent persistence ports and durable session metadata.
use crate::{EventVisibility, SessionEventObserver, SessionSnapshot};
use arabica_protocol::{EventEnvelope, SessionId, WorkspaceId};
use serde::{Deserialize, Serialize};
use std::error::Error;
use std::fmt::{Display, Formatter};
use std::path::Path;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum StoreErrorKind {
    /// `$ARABICA_HOME` could not be resolved (no `$ARABICA_HOME` and no `$HOME`).
    NoHome,
    /// A session or workspace id was not safe to use as a path component.
    InvalidId,
    /// The backend could not read, write, or acquire session ownership.
    Io,
}

#[derive(Debug)]
pub struct StoreError {
    kind: StoreErrorKind,
    message: String,
}

impl StoreError {
    pub fn new(kind: StoreErrorKind, message: impl Into<String>) -> Self {
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

/// Metadata supplied when creating a session.
pub struct NewSession<'a> {
    pub session_id: &'a SessionId,
    pub workspace_id: &'a WorkspaceId,
    pub cwd: &'a Path,
    /// The provider profile in effect, once `arabica-cli` has profiles
    /// (`P2-4`). `None` until then; a host without profiles yet has nothing
    /// honest to put here.
    pub profile: Option<&'a str>,
    /// A hash of the system instructions in effect at session creation:
    /// `arabica-cli` fills this from its `AGENTS.md` discovery; hosts
    /// without project instructions pass `None`. The full text is never
    /// stored here -- it is already exact in the `model.request.prepared`
    /// Event; this is a cheap "did the effective instructions change"
    /// signal for tooling, not a second copy of the prompt.
    pub instructions_sha256: Option<&'a str>,
}

/// Durable session metadata.
#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct SessionHeader {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub title: Option<String>,
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

#[derive(Debug)]
pub struct StoredSession {
    pub header: SessionHeader,
    pub events: Vec<EventEnvelope>,
}

impl StoredSession {
    /// Convert persisted events to the session restore input.
    pub fn into_snapshot(self) -> SessionSnapshot {
        SessionSnapshot {
            events: self.events,
        }
    }
}

/// Session metadata for listings, without reading the event history.
#[derive(Clone, Debug)]
pub struct SessionListing {
    pub header: SessionHeader,
    pub updated_at_ms: Option<u64>,
}

/// Backend-independent session repository. Hosts identify sessions by workspace
/// and session IDs; database paths and migration belong to the implementation.
/// Opening or creating a writer acquires exclusive ownership until its last
/// handle is dropped. Read and list operations do not return ownership handles;
/// adapters may perform migration before returning.
pub trait SessionStore: Send + Sync + std::fmt::Debug {
    fn create(
        &self,
        session: NewSession<'_>,
    ) -> Result<std::sync::Arc<dyn SessionWriter>, StoreError>;
    fn open(
        &self,
        workspace_id: &WorkspaceId,
        session_id: &SessionId,
    ) -> Result<std::sync::Arc<dyn SessionWriter>, StoreError>;
    fn read(
        &self,
        workspace_id: &WorkspaceId,
        session_id: &SessionId,
    ) -> Result<StoredSession, StoreError>;
    /// Most recently active first, optionally restricted to a workspace.
    fn list(&self, workspace_id: Option<&WorkspaceId>) -> Result<Vec<SessionListing>, StoreError>;
}

/// An exclusively owned session's write port. Adapters must atomically append
/// an event and its metadata update, and reject mismatched identities or gaps.
/// Hosts may upcast this handle to `SessionEventObserver` for dispatch/recovery.
pub trait SessionWriter: SessionEventObserver + std::fmt::Debug {
    fn append(
        &self,
        envelope: &EventEnvelope,
        visibility: EventVisibility,
    ) -> Result<(), StoreError>;
    fn set_title(&self, title: &str) -> Result<(), StoreError>;
}
