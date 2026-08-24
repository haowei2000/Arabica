use structure_model::{
    FinishReason, MessageItem, RuntimeGenerationConfig, RuntimeItem, RuntimeRequest, RuntimeRole,
    RuntimeUsage, ToolChoice,
};
use structure_protocol::{
    AgentLoopTerminationReason, Command, ContextEntry, DisclosureLevel, ErrorCode, Event,
    ModelResponseRejectionReason, OutputStream, RunId, SessionId, ToolInteractionKind, WorkspaceId,
};

#[test]
fn every_command_has_a_stable_dotted_wire_name() {
    let commands = [
        (
            Command::SessionCreate {
                workspace_id: WorkspaceId::new("workspace-1"),
            },
            "session.create",
        ),
        (
            Command::SessionFork {
                source_session_id: SessionId::new("session-1"),
            },
            "session.fork",
        ),
        (Command::SessionResume, "session.resume"),
        (Command::SessionSuspend, "session.suspend"),
        (Command::SessionClose, "session.close"),
        (
            Command::MessageSend {
                content: "hello".to_owned(),
            },
            "message.send",
        ),
        (
            Command::RunCancel {
                run_id: RunId::new("run-1"),
            },
            "run.cancel",
        ),
        (
            Command::ContextRead {
                path: "memory/user".to_owned(),
            },
            "context.read",
        ),
        (
            Command::ContextSearch {
                query: "user".to_owned(),
            },
            "context.search",
        ),
        (
            Command::ContextUpdate {
                path: "memory/user".to_owned(),
                content: "concise".to_owned(),
            },
            "context.update",
        ),
        (
            Command::ContextDelete {
                path: "memory/user".to_owned(),
            },
            "context.delete",
        ),
        (
            Command::ContextSetDisclosure {
                level: DisclosureLevel::Detail,
            },
            "context.set_disclosure",
        ),
    ];

    for (command, expected_name) in commands {
        let value = serde_json::to_value(command).expect("command serializes");
        assert_eq!(value["type"], expected_name);
    }
}

#[test]
fn every_event_has_a_stable_dotted_wire_name() {
    let entry = ContextEntry {
        path: "memory/user".to_owned(),
        content: "concise".to_owned(),
    };
    let events = [
        (
            Event::SessionCreated {
                workspace_id: WorkspaceId::new("workspace-1"),
            },
            "session.created",
        ),
        (
            Event::SessionForked {
                source_session_id: SessionId::new("session-1"),
            },
            "session.forked",
        ),
        (Event::SessionResumed, "session.resumed"),
        (Event::SessionSuspended, "session.suspended"),
        (Event::SessionClosed, "session.closed"),
        (Event::RunScheduled, "run.scheduled"),
        (Event::RunStarted, "run.started"),
        (
            Event::MessageAccepted {
                content: "hello".to_owned(),
            },
            "message.accepted",
        ),
        (
            Event::ModelRequestPrepared {
                model_step: 0,
                request: RuntimeRequest {
                    model: "model-1".to_owned(),
                    items: vec![RuntimeItem::Message(MessageItem::text(
                        RuntimeRole::User,
                        "hello",
                    ))],
                    tools: Vec::new(),
                    tool_choice: ToolChoice::Auto,
                    generation: RuntimeGenerationConfig::default(),
                },
            },
            "model.request.prepared",
        ),
        (
            Event::ModelResponseItem {
                model_step: 0,
                item_index: 0,
                item: RuntimeItem::Message(MessageItem::text(RuntimeRole::Assistant, "hello")),
            },
            "model.response.item",
        ),
        (
            Event::ModelResponseCompleted {
                model_step: 0,
                finish_reason: Some(FinishReason::Stop),
                usage: RuntimeUsage::default(),
            },
            "model.response.completed",
        ),
        (
            Event::ModelResponseRejected {
                model_step: 0,
                reason: ModelResponseRejectionReason::ToolCallsWithoutItem,
                finish_reason: Some(FinishReason::ToolCalls),
                tool_call_count: 0,
                final_output_present: false,
            },
            "model.response.rejected",
        ),
        (
            Event::ToolCallRequested {
                call_id: "call-1".to_owned(),
                name: "write_file".to_owned(),
                arguments: serde_json::json!({"path": "note.txt"}),
            },
            "tool.call.requested",
        ),
        (
            Event::ToolCallCompleted {
                call_id: "call-1".to_owned(),
                name: "write_file".to_owned(),
                result: "written".to_owned(),
                is_error: false,
            },
            "tool.call.completed",
        ),
        (
            Event::ToolCallClassified {
                call_id: "call-1".to_owned(),
                kind: ToolInteractionKind::Mutation,
            },
            "tool.call.classified",
        ),
        (
            Event::ToolCallReused {
                call_id: "call-2".to_owned(),
                source_call_id: "call-1".to_owned(),
                fingerprint: "sha256:abcd".to_owned(),
                repeat_count: 1,
            },
            "tool.call.reused",
        ),
        (
            Event::ToolCallLoopBlocked {
                call_id: "call-3".to_owned(),
                fingerprint: "sha256:abcd".to_owned(),
                repeat_count: 2,
            },
            "tool.call.loop_blocked",
        ),
        (
            Event::AgentLoopTerminated {
                model_step: 8,
                reason: AgentLoopTerminationReason::NoStateProgress,
                consecutive_no_progress_steps: 8,
            },
            "agent.loop.terminated",
        ),
        (
            Event::CommandOutput {
                stream: OutputStream::Stdout,
                chunk: "hello".to_owned(),
            },
            "command.output",
        ),
        (
            Event::RunCompleted {
                output: Some("hello".to_owned()),
            },
            "run.completed",
        ),
        (
            Event::RunFailed {
                message: "failed".to_owned(),
            },
            "run.failed",
        ),
        (Event::RunCancelled, "run.cancelled"),
        (
            Event::ContextRead {
                entry: entry.clone(),
            },
            "context.read",
        ),
        (
            Event::ContextSearchResult {
                entries: vec![entry.clone()],
            },
            "context.search.result",
        ),
        (
            Event::ContextUpdated {
                entry: entry.clone(),
            },
            "context.updated",
        ),
        (
            Event::ContextDeleted {
                path: "memory/user".to_owned(),
            },
            "context.deleted",
        ),
        (
            Event::ContextDisclosureSet {
                level: DisclosureLevel::Overview,
            },
            "context.disclosure.set",
        ),
        (
            Event::Error {
                code: ErrorCode::RuntimeFailure,
                message: "failed".to_owned(),
            },
            "error",
        ),
    ];

    for (event, expected_name) in events {
        let value = serde_json::to_value(event).expect("event serializes");
        assert_eq!(value["type"], expected_name);
    }
}

#[test]
fn model_exchange_events_are_internal_to_the_canonical_log() {
    let event = Event::ModelResponseItem {
        model_step: 0,
        item_index: 0,
        item: RuntimeItem::Message(MessageItem::text(RuntimeRole::Assistant, "private")),
    };
    assert!(!event.is_client_visible());
    assert!(Event::RunCompleted { output: None }.is_client_visible());
}
