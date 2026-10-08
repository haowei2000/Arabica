//! SQLite session persistence and a legacy JSONL import adapter.

mod file_session_store;

pub use arabica_session::{
    NewSession, SessionHeader, SessionListing, StoreError, StoreErrorKind, StoredSession,
};
pub use file_session_store::{FileSessionListing, FileSessionStore, default_arabica_home};

mod sqlite_session_store;
pub use sqlite_session_store::SqliteSessionStore;

mod sqlite_session_repository;
pub use sqlite_session_repository::SqliteSessionRepository;
