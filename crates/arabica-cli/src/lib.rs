//! `arabica-cli`: the composition host that binds the canonical
//! Command/Event protocol to stdio.
//!
//! Per `docs/protocol.md` §1, a composition host wires the existing modules
//! in one process and exposes the protocol without adding vocabulary of its
//! own; it is not a UI surface and is not blocked by the protocol freeze
//! checklist. `arabica-server` is the HTTP + SSE host; this crate is the
//! stdio one. Interactive terminal chat, `structure acp` (Agent Client
//! Protocol v1), and `structure -p` (one-shot execution) share the same
//! runtime composition; see
//! `docs/runtime_core_architecture.md` Appendix B for the decision record.

pub mod acp;
pub mod auth;
mod checkpoint;
pub mod config;
mod context;
pub mod host;
mod instructions;
pub mod interactive;
pub mod mcp;
pub mod print;
pub mod sessions;
pub mod tui;
