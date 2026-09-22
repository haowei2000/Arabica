//! `structure-cli`: the composition host that binds the canonical
//! Command/Event protocol to stdio.
//!
//! Per `docs/protocol.md` §1, a composition host wires the existing modules
//! in one process and exposes the protocol without adding vocabulary of its
//! own; it is not a UI surface and is not blocked by the protocol freeze
//! checklist. `structure-server` is the HTTP + SSE host; this crate is the
//! stdio one. `structure acp` (Agent Client Protocol v1) and `structure -p`
//! (one-shot execution) are its two bindings; see
//! `docs/runtime_core_architecture.md` Appendix B for the decision record.

pub mod acp;
pub mod host;
pub mod print;
