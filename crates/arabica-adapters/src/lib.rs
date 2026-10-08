//! SQLite session persistence and a legacy JSONL import adapter.

mod evaluation_store;
mod file_session_store;
pub use evaluation_store::{
    EvaluationCheckpoint, EvaluationView, EvaluationWorker, EvaluationWorkerStatus,
    SavedEvaluation, SqliteEvaluationStore,
};

pub use arabica_session::{
    NewSession, SessionHeader, SessionListing, StoreError, StoreErrorKind, StoredSession,
};
pub use file_session_store::{FileSessionListing, FileSessionStore, default_arabica_home};

mod sqlite_session_store;
pub use sqlite_session_store::SqliteSessionStore;

mod sqlite_session_repository;
pub use sqlite_session_repository::SqliteSessionRepository;
