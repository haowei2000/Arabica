//! Persistence adapters implementing `arabica-session`'s ports.
//!
//! `arabica-session` defines only one persistence-shaped port,
//! [`arabica_session::SessionEventObserver`]: a sink notified of every
//! Event as it is appended, including internal ones
//! (`crates/arabica-session/src/lib.rs`). This crate's
//! [`FileSessionStore`] is the first adapter for it: one append-only JSONL
//! file per session under `$ARABICA_HOME`, writable as that observer and
//! readable back into a `arabica_session::SessionSnapshot` for
//! `SessionManager::restore_session`. No host wires it in yet -- that is
//! `arabica-cli`'s job (`P2-3` in the CLI/ACP extension plan).

mod evaluation_store;
mod file_session_store;
pub use evaluation_store::{
    EvaluationCheckpoint, EvaluationView, EvaluationWorker, EvaluationWorkerStatus,
    SavedEvaluation, SqliteEvaluationStore,
};

pub use file_session_store::{
    FileSessionStore, NewSession, SessionHeader, SessionListing, StoreError, StoreErrorKind,
    StoredSession, default_arabica_home,
};
