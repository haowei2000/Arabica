//! Persistence adapters implementing `structure-session`'s ports.
//!
//! `structure-session` defines only one persistence-shaped port,
//! [`structure_session::SessionEventObserver`]: a sink notified of every
//! Event as it is appended, including internal ones
//! (`crates/structure-session/src/lib.rs`). This crate's
//! [`FileSessionStore`] is the first adapter for it: one append-only JSONL
//! file per session under `$STRUCTURE_HOME`. No host wires it in yet --
//! that is `structure-cli`'s job, once session resume (`P2-2`/`P2-3` in the
//! CLI/ACP extension plan) needs something to read it back.

mod file_session_store;

pub use file_session_store::{
    FileSessionStore, NewSession, StoreError, StoreErrorKind, default_structure_home,
};
