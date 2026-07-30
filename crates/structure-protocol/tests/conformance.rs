use structure_protocol::{
    Command, ContextEntry, DisclosureLevel, ErrorCode, Event, OutputStream, RunId, SessionId,
    ToolInteractionKind, WorkspaceId,
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
