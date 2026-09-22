//! Translates Structure's canonical Events into ACP `session/update`
//! notifications, live, as they occur.
//!
//! [`AcpObserver`] is installed as the [`SessionEventObserver`] for exactly
//! one `dispatch` call (one `session/prompt`), so every Event it ever sees
//! belongs to that one call: no `run_id` bookkeeping is needed to tell one
//! prompt's Events apart from another's. It is handed *every* Event,
//! including the internal ones Structure never puts on an ordinary client
//! transport (`docs/protocol.md` §1) -- that boundary is about the generic
//! event stream, not this mapping, and `model.response.item` (internal) is
//! exactly where the assistant's text and reasoning live.

use std::path::{Path, PathBuf};

use agent_client_protocol::schema::v1::{
    ContentBlock, ContentChunk, Diff, SessionId as AcpSessionId, SessionNotification,
    SessionUpdate, ToolCall, ToolCallContent, ToolCallId, ToolCallLocation, ToolCallStatus,
    ToolCallUpdate, ToolCallUpdateFields, ToolKind,
};
use agent_client_protocol::{Client, ConnectionTo};
use serde_json::Value;
use structure_model::{RuntimeItem, RuntimeRole};
use structure_protocol::{Event, EventEnvelope};
use structure_session::{EventVisibility, SessionEventObserver};

/// Forwards one `dispatch` call's Events to the ACP client as live
/// `session/update` notifications.
pub struct AcpObserver {
    connection: ConnectionTo<Client>,
    session_id: AcpSessionId,
    workspace_root: PathBuf,
}

impl AcpObserver {
    pub fn new(
        connection: ConnectionTo<Client>,
        session_id: AcpSessionId,
        workspace_root: PathBuf,
    ) -> Self {
        Self {
            connection,
            session_id,
            workspace_root,
        }
    }
}

impl SessionEventObserver for AcpObserver {
    fn observe(&self, envelope: &EventEnvelope, _visibility: EventVisibility) {
        for update in updates_for(&envelope.event, &envelope.run_id, &self.workspace_root) {
            if let Err(error) = self
                .connection
                .send_notification(SessionNotification::new(self.session_id.clone(), update))
            {
                eprintln!("structure acp: dropped a session/update: {error}");
            }
        }
    }
}

/// The ACP tool call id for one Structure call, stable across every update
/// for that call within this run: `run_id` and `call_id` alone are already
/// unique within one run, since Structure's own tool-loop guard assigns
/// `call_id` per call, so no extra counter is needed. `pub(super)`: also used
/// by `permission.rs` so the permission dialog names the same tool call the
/// observer already announced.
pub(super) fn acp_tool_call_id(
    run_id: &Option<structure_protocol::RunId>,
    call_id: &str,
) -> ToolCallId {
    match run_id {
        Some(run_id) => ToolCallId::new(format!("{run_id}:{call_id}")),
        None => ToolCallId::new(call_id.to_owned()),
    }
}

pub fn tool_kind(name: &str) -> ToolKind {
    match name {
        "read_file" | "list_dir" => ToolKind::Read,
        "grep" | "find_files" => ToolKind::Search,
        "write_file" | "edit_files" => ToolKind::Edit,
        "delete_file" => ToolKind::Delete,
        "shell" => ToolKind::Execute,
        _ => ToolKind::Other,
    }
}

pub fn tool_call_title(name: &str, arguments: &Value) -> String {
    let path = arguments.get("path").and_then(Value::as_str);
    match name {
        "read_file" => path.map_or_else(|| "Read file".to_owned(), |path| format!("Read {path}")),
        "list_dir" => path.map_or_else(
            || "List directory".to_owned(),
            |path| format!("List {path}"),
        ),
        "write_file" => {
            path.map_or_else(|| "Write file".to_owned(), |path| format!("Write {path}"))
        }
        "delete_file" => {
            path.map_or_else(|| "Delete file".to_owned(), |path| format!("Delete {path}"))
        }
        "edit_files" => {
            let count = arguments
                .get("edits")
                .and_then(Value::as_array)
                .map_or(0, Vec::len);
            match count {
                1 => "Edit 1 file".to_owned(),
                count => format!("Edit {count} files"),
            }
        }
        "grep" => arguments
            .get("pattern")
            .and_then(Value::as_str)
            .map_or_else(
                || "Search".to_owned(),
                |pattern| format!("Search for {pattern:?}"),
            ),
        "find_files" => arguments.get("glob").and_then(Value::as_str).map_or_else(
            || "Find files".to_owned(),
            |glob| format!("Find files matching {glob:?}"),
        ),
        "shell" => arguments
            .get("command")
            .and_then(Value::as_str)
            .map_or_else(
                || "Run command".to_owned(),
                |command| format!("Run `{command}`"),
            ),
        other => other.to_owned(),
    }
}

fn resolve_path(workspace_root: &Path, arguments: &Value) -> Option<PathBuf> {
    arguments
        .get("path")
        .and_then(Value::as_str)
        .map(|path| workspace_root.join(path))
}

fn tool_call_locations(workspace_root: &Path, arguments: &Value) -> Vec<ToolCallLocation> {
    resolve_path(workspace_root, arguments)
        .map(|path| vec![ToolCallLocation::new(path)])
        .unwrap_or_default()
}

/// Best-effort diff content for the two edit tools, built from the call's
/// own arguments rather than reading the file. For `write_file`, the tool's
/// own contract is "replace with this complete content", so a diff with no
/// `old_text` is accurate regardless of what was there before. For
/// `edit_files`, only the matched `old_string`/`new_string` pair is known --
/// not the whole file -- so the diff covers exactly that changed span, not
/// full before/after file contents. `pub(super)`: `permission.rs` shows the
/// same diff in the permission dialog as the observer shows in the
/// `tool_call` announcement.
pub(super) fn diff_content(
    workspace_root: &Path,
    name: &str,
    arguments: &Value,
) -> Vec<ToolCallContent> {
    match name {
        "write_file" => {
            let Some(path) = resolve_path(workspace_root, arguments) else {
                return Vec::new();
            };
            let Some(content) = arguments.get("content").and_then(Value::as_str) else {
                return Vec::new();
            };
            vec![ToolCallContent::Diff(Diff::new(path, content))]
        }
        "edit_files" => arguments
            .get("edits")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
            .filter_map(|edit| {
                let path = workspace_root.join(edit.get("path")?.as_str()?);
                let old_string = edit.get("old_string")?.as_str()?;
                let new_string = edit.get("new_string")?.as_str()?;
                Some(ToolCallContent::Diff(
                    Diff::new(path, new_string).old_text(old_string),
                ))
            })
            .collect(),
        _ => Vec::new(),
    }
}

fn message_chunks(content: &[structure_model::ContentBlock]) -> Vec<ContentBlock> {
    content
        .iter()
        .filter_map(|block| match block {
            structure_model::ContentBlock::Text { text } if !text.is_empty() => {
                Some(ContentBlock::from(text.as_str()))
            }
            _ => None,
        })
        .collect()
}

fn updates_for(
    event: &Event,
    run_id: &Option<structure_protocol::RunId>,
    workspace_root: &Path,
) -> Vec<SessionUpdate> {
    match event {
        Event::ModelResponseItem { item, .. } => match item {
            RuntimeItem::Message(message) if message.role == RuntimeRole::Assistant => {
                message_chunks(&message.content)
                    .into_iter()
                    .map(|block| {
                        let mut chunk = ContentChunk::new(block);
                        if let Some(id) = &message.id {
                            chunk = chunk.message_id(id.as_str());
                        }
                        SessionUpdate::AgentMessageChunk(chunk)
                    })
                    .collect()
            }
            RuntimeItem::Reasoning(reasoning) if !reasoning.summary.is_empty() => {
                let mut chunk = ContentChunk::new(ContentBlock::from(reasoning.summary.join("\n\n")));
                if let Some(id) = &reasoning.id {
                    chunk = chunk.message_id(id.as_str());
                }
                vec![SessionUpdate::AgentThoughtChunk(chunk)]
            }
            // Tool calls and their results also arrive as their own
            // dedicated `tool.call.*` Events below, which carry the
            // canonical call_id/name/result; reporting them again from the
            // raw model-response item would double the client's view of the
            // same call.
            RuntimeItem::Message(_) | RuntimeItem::Reasoning(_) | RuntimeItem::ToolCall(_)
            | RuntimeItem::ToolResult(_) => Vec::new(),
        },
        Event::ToolCallRequested {
            call_id,
            name,
            arguments,
            ..
        } => {
            let mut call = ToolCall::new(acp_tool_call_id(run_id, call_id), tool_call_title(name, arguments))
                .name(name.clone())
                .kind(tool_kind(name))
                .status(ToolCallStatus::Pending)
                .raw_input(arguments.clone())
                .locations(tool_call_locations(workspace_root, arguments));
            let diff = diff_content(workspace_root, name, arguments);
            if !diff.is_empty() {
                call = call.content(diff);
            }
            vec![SessionUpdate::ToolCall(call)]
        }
        // Either signal means the call cleared its gate and is now actually
        // running: a call with no permission gate at all only ever produces
        // `classified`, and a gated call may produce both, in which case the
        // second, identical `in_progress` update is a harmless duplicate.
        Event::ToolCallClassified { call_id, .. } => vec![in_progress(run_id, call_id)],
        Event::ToolCallPermissionResolved {
            call_id,
            outcome: structure_protocol::ToolPermissionOutcome::Allowed,
            ..
        } => vec![in_progress(run_id, call_id)],
        Event::ToolCallCompleted {
            call_id,
            result,
            is_error,
            ..
        } => {
            let status = if *is_error {
                ToolCallStatus::Failed
            } else {
                ToolCallStatus::Completed
            };
            vec![SessionUpdate::ToolCallUpdate(ToolCallUpdate::new(
                acp_tool_call_id(run_id, call_id),
                ToolCallUpdateFields::new()
                    .status(status)
                    .content(vec![ToolCallContent::from(result.as_str())]),
            ))]
        }
        // A denied or cancelled decision is always followed by its own
        // `tool.call.completed{is_error: true}` (the permission gate and
        // cooperative cancellation both guarantee it), which the arm above
        // already turns into a `failed` update, so no separate update is
        // needed for the resolution itself.
        Event::ToolCallPermissionResolved { .. }
        // A person is asked through the separate `session/request_permission`
        // request (`permission.rs`), not a session/update.
        | Event::ToolCallPermissionRequested { .. }
        | Event::SessionCreated { .. }
        | Event::SessionForked { .. }
        | Event::SessionResumed
        | Event::SessionSuspended
        | Event::SessionClosed
        | Event::RunScheduled
        | Event::RunStarted
        | Event::MessageAccepted { .. }
        | Event::ModelRequestPrepared { .. }
        | Event::ModelResponseCompleted { .. }
        | Event::ModelResponseRejected { .. }
        | Event::ModelResponseNormalized { .. }
        | Event::ToolCallReused { .. }
        | Event::ToolCallLoopBlocked { .. }
        | Event::AgentProgressAdvisory { .. }
        | Event::AgentLoopTerminated { .. }
        | Event::TerminalControlTransition { .. }
        | Event::CommandOutput { .. }
        | Event::RunCompleted { .. }
        | Event::RunFailed { .. }
        | Event::RunCancelled
        | Event::ContextRead { .. }
        | Event::ContextSearchResult { .. }
        | Event::ContextUpdated { .. }
        | Event::ContextDeleted { .. }
        | Event::ContextDisclosureSet { .. }
        | Event::Error { .. } => Vec::new(),
    }
}

fn in_progress(run_id: &Option<structure_protocol::RunId>, call_id: &str) -> SessionUpdate {
    SessionUpdate::ToolCallUpdate(ToolCallUpdate::new(
        acp_tool_call_id(run_id, call_id),
        ToolCallUpdateFields::new().status(ToolCallStatus::InProgress),
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tool_kind_matches_the_plans_categories() {
        assert_eq!(tool_kind("read_file"), ToolKind::Read);
        assert_eq!(tool_kind("list_dir"), ToolKind::Read);
        assert_eq!(tool_kind("grep"), ToolKind::Search);
        assert_eq!(tool_kind("find_files"), ToolKind::Search);
        assert_eq!(tool_kind("write_file"), ToolKind::Edit);
        assert_eq!(tool_kind("edit_files"), ToolKind::Edit);
        assert_eq!(tool_kind("delete_file"), ToolKind::Delete);
        assert_eq!(tool_kind("shell"), ToolKind::Execute);
        assert_eq!(tool_kind("unknown_future_tool"), ToolKind::Other);
    }

    #[test]
    fn a_tool_call_requested_event_becomes_a_pending_tool_call() {
        let run_id = Some(structure_protocol::RunId::new("run-1"));
        let updates = updates_for(
            &Event::ToolCallRequested {
                call_id: "call-1".to_owned(),
                name: "read_file".to_owned(),
                arguments: serde_json::json!({"path": "src/main.rs"}),
                provider_state: None,
            },
            &run_id,
            Path::new("/workspace"),
        );
        assert_eq!(updates.len(), 1);
        let SessionUpdate::ToolCall(call) = &updates[0] else {
            panic!("expected a ToolCall update");
        };
        assert_eq!(call.tool_call_id, ToolCallId::new("run-1:call-1"));
        assert_eq!(call.title, "Read src/main.rs");
        assert_eq!(call.kind, ToolKind::Read);
        assert_eq!(call.status, ToolCallStatus::Pending);
        assert_eq!(
            call.locations,
            vec![ToolCallLocation::new("/workspace/src/main.rs")]
        );
    }

    #[test]
    fn a_write_file_request_carries_a_diff_with_no_old_text() {
        let updates = updates_for(
            &Event::ToolCallRequested {
                call_id: "call-1".to_owned(),
                name: "write_file".to_owned(),
                arguments: serde_json::json!({"path": "a.txt", "content": "hello"}),
                provider_state: None,
            },
            &Some(structure_protocol::RunId::new("run-1")),
            Path::new("/ws"),
        );
        let SessionUpdate::ToolCall(call) = &updates[0] else {
            panic!("expected a ToolCall update");
        };
        assert_eq!(call.content.len(), 1);
        let ToolCallContent::Diff(diff) = &call.content[0] else {
            panic!("expected a Diff content block");
        };
        assert_eq!(diff.path, PathBuf::from("/ws/a.txt"));
        assert_eq!(diff.old_text, None);
        assert_eq!(diff.new_text, "hello");
    }

    #[test]
    fn an_edit_files_request_carries_one_diff_per_edit() {
        let updates = updates_for(
            &Event::ToolCallRequested {
                call_id: "call-1".to_owned(),
                name: "edit_files".to_owned(),
                arguments: serde_json::json!({"edits": [
                    {"path": "a.txt", "old_string": "old", "new_string": "new"},
                    {"path": "b.txt", "old_string": "x", "new_string": "y"},
                ]}),
                provider_state: None,
            },
            &Some(structure_protocol::RunId::new("run-1")),
            Path::new("/ws"),
        );
        let SessionUpdate::ToolCall(call) = &updates[0] else {
            panic!("expected a ToolCall update");
        };
        assert_eq!(call.content.len(), 2);
    }

    #[test]
    fn classified_and_allowed_both_report_in_progress() {
        let run_id = Some(structure_protocol::RunId::new("run-1"));
        for event in [
            Event::ToolCallClassified {
                call_id: "call-1".to_owned(),
                kind: structure_protocol::ToolInteractionKind::Inspection,
            },
            Event::ToolCallPermissionResolved {
                call_id: "call-1".to_owned(),
                outcome: structure_protocol::ToolPermissionOutcome::Allowed,
                scope: structure_protocol::ToolPermissionScope::Once,
                source: structure_protocol::ToolPermissionSource::Policy,
            },
        ] {
            let updates = updates_for(&event, &run_id, Path::new("/ws"));
            assert_eq!(updates.len(), 1);
            let SessionUpdate::ToolCallUpdate(update) = &updates[0] else {
                panic!("expected a ToolCallUpdate");
            };
            assert_eq!(update.tool_call_id, ToolCallId::new("run-1:call-1"));
            assert_eq!(update.fields.status, Some(ToolCallStatus::InProgress));
        }
    }

    #[test]
    fn a_denied_permission_resolution_alone_produces_no_update() {
        // The completion event that always follows carries the failure.
        let updates = updates_for(
            &Event::ToolCallPermissionResolved {
                call_id: "call-1".to_owned(),
                outcome: structure_protocol::ToolPermissionOutcome::Denied,
                scope: structure_protocol::ToolPermissionScope::Once,
                source: structure_protocol::ToolPermissionSource::Policy,
            },
            &Some(structure_protocol::RunId::new("run-1")),
            Path::new("/ws"),
        );
        assert!(updates.is_empty());
    }

    #[test]
    fn a_completed_call_reports_success_and_an_errored_one_reports_failure() {
        let run_id = Some(structure_protocol::RunId::new("run-1"));
        let ok = updates_for(
            &Event::ToolCallCompleted {
                call_id: "call-1".to_owned(),
                name: "read_file".to_owned(),
                result: "file contents".to_owned(),
                is_error: false,
            },
            &run_id,
            Path::new("/ws"),
        );
        let SessionUpdate::ToolCallUpdate(update) = &ok[0] else {
            panic!("expected a ToolCallUpdate");
        };
        assert_eq!(update.fields.status, Some(ToolCallStatus::Completed));

        let failed = updates_for(
            &Event::ToolCallCompleted {
                call_id: "call-2".to_owned(),
                name: "shell".to_owned(),
                result: "permission denied".to_owned(),
                is_error: true,
            },
            &run_id,
            Path::new("/ws"),
        );
        let SessionUpdate::ToolCallUpdate(update) = &failed[0] else {
            panic!("expected a ToolCallUpdate");
        };
        assert_eq!(update.fields.status, Some(ToolCallStatus::Failed));
    }

    #[test]
    fn assistant_text_becomes_an_agent_message_chunk_but_a_tool_result_item_is_ignored() {
        let assistant_message = updates_for(
            &Event::ModelResponseItem {
                model_step: 1,
                item_index: 0,
                item: RuntimeItem::Message(structure_model::MessageItem {
                    id: Some("msg-1".to_owned()),
                    role: RuntimeRole::Assistant,
                    content: vec![structure_model::ContentBlock::Text {
                        text: "hello".to_owned(),
                    }],
                    provider_state: None,
                }),
            },
            &Some(structure_protocol::RunId::new("run-1")),
            Path::new("/ws"),
        );
        assert_eq!(assistant_message.len(), 1);
        assert!(matches!(
            assistant_message[0],
            SessionUpdate::AgentMessageChunk(_)
        ));

        let tool_result_item = updates_for(
            &Event::ModelResponseItem {
                model_step: 1,
                item_index: 1,
                item: RuntimeItem::ToolResult(structure_model::ToolResultItem {
                    id: None,
                    call_id: "call-1".to_owned(),
                    name: Some("read_file".to_owned()),
                    content: vec![structure_model::ContentBlock::Text {
                        text: "dup".to_owned(),
                    }],
                    is_error: false,
                }),
            },
            &Some(structure_protocol::RunId::new("run-1")),
            Path::new("/ws"),
        );
        assert!(tool_result_item.is_empty());
    }
}
