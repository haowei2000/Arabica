//! Turns the Events one `message.send` produced into the ACP outcome for
//! that `session/prompt` call.
//!
//! This is a pure function over the client-visible Events Session Management
//! already returned from `dispatch`: no I/O, no protocol types beyond the
//! return value, so a mapping mistake shows up in a unit test instead of a
//! live ACP session.

use agent_client_protocol::schema::v1::StopReason;
use arabica_protocol::{
    AgentLoopTerminationReason, Event, EventEnvelope, ModelResponseRejectionReason,
};

/// What `session/prompt` should tell the client.
pub enum PromptOutcome {
    /// A normal `PromptResponse` with this `stop_reason`.
    Stopped(StopReason),
    /// No stop reason ACP defines fits; the prompt must fail as a JSON-RPC
    /// error instead of a `PromptResponse`, carrying this message.
    Failed(String),
}

/// Classify one `message.send` call's Events into a [`PromptOutcome`].
///
/// `run.cancelled` always wins regardless of position: a client that sends
/// `session/cancel` must see `Cancelled` even if the run happened to also
/// record a rejection on its way down. Otherwise this looks for the run's
/// terminal Event (`run.completed` or `run.failed`) and, for a failure,
/// the specific typed evidence ACP has a stop reason for. A failure with no
/// such evidence (a malformed provider response, a runner error) is real
/// evidence of a bug or outage, not a turn outcome a client should render as
/// though the agent just stopped talking, so it becomes a JSON-RPC error.
pub fn classify(events: &[EventEnvelope]) -> PromptOutcome {
    if events
        .iter()
        .any(|envelope| matches!(envelope.event, Event::RunCancelled))
    {
        return PromptOutcome::Stopped(StopReason::Cancelled);
    }
    for envelope in events {
        match &envelope.event {
            Event::RunCompleted { .. } => return PromptOutcome::Stopped(StopReason::EndTurn),
            Event::RunFailed { message } => return failure_outcome(events, message),
            _ => {}
        }
    }
    PromptOutcome::Failed("run ended without a terminal event".to_owned())
}

fn failure_outcome(events: &[EventEnvelope], message: &str) -> PromptOutcome {
    for envelope in events {
        match &envelope.event {
            Event::ModelResponseRejected {
                reason: ModelResponseRejectionReason::OutputLength,
                ..
            } => return PromptOutcome::Stopped(StopReason::MaxTokens),
            Event::ModelResponseRejected {
                reason: ModelResponseRejectionReason::ContentFilter,
                ..
            } => return PromptOutcome::Stopped(StopReason::Refusal),
            Event::AgentLoopTerminated { reason, .. } => {
                let _: AgentLoopTerminationReason = *reason;
                return PromptOutcome::Stopped(StopReason::MaxTurnRequests);
            }
            _ => {}
        }
    }
    PromptOutcome::Failed(message.to_owned())
}

#[cfg(test)]
mod tests {
    use super::*;
    use arabica_protocol::{
        CommandId, EventId, RunId, SessionId, ToolPermissionOutcome, ToolPermissionScope,
        ToolPermissionSource, WorkspaceId,
    };

    fn envelope(event: Event) -> EventEnvelope {
        EventEnvelope {
            protocol_version: arabica_protocol::PROTOCOL_VERSION.to_owned(),
            event_id: EventId::new("event-1"),
            command_id: CommandId::new("command-1"),
            workspace_id: WorkspaceId::new("ws-1"),
            session_id: SessionId::new("session-1"),
            run_id: Some(RunId::new("run-1")),
            sequence: 1,
            occurred_at_ms: 0,
            event,
        }
    }

    fn is_stopped(outcome: &PromptOutcome, expected: StopReason) -> bool {
        matches!(outcome, PromptOutcome::Stopped(reason) if *reason == expected)
    }

    #[test]
    fn a_completed_run_ends_the_turn() {
        let events = vec![
            envelope(Event::RunStarted),
            envelope(Event::RunCompleted { output: None }),
        ];
        assert!(is_stopped(&classify(&events), StopReason::EndTurn));
    }

    #[test]
    fn a_cancelled_run_wins_even_after_a_rejection() {
        let events = vec![
            envelope(Event::ModelResponseRejected {
                model_step: 1,
                reason: ModelResponseRejectionReason::OutputLength,
                finish_reason: None,
                tool_call_count: 0,
                final_output_present: false,
            }),
            envelope(Event::RunCancelled),
        ];
        assert!(is_stopped(&classify(&events), StopReason::Cancelled));
    }

    #[test]
    fn an_output_length_rejection_before_the_failure_maps_to_max_tokens() {
        let events = vec![
            envelope(Event::ModelResponseRejected {
                model_step: 1,
                reason: ModelResponseRejectionReason::OutputLength,
                finish_reason: None,
                tool_call_count: 0,
                final_output_present: false,
            }),
            envelope(Event::RunFailed {
                message: "provider truncated the response".to_owned(),
            }),
        ];
        assert!(is_stopped(&classify(&events), StopReason::MaxTokens));
    }

    #[test]
    fn a_content_filter_rejection_maps_to_refusal() {
        let events = vec![
            envelope(Event::ModelResponseRejected {
                model_step: 1,
                reason: ModelResponseRejectionReason::ContentFilter,
                finish_reason: None,
                tool_call_count: 0,
                final_output_present: false,
            }),
            envelope(Event::RunFailed {
                message: "content filter".to_owned(),
            }),
        ];
        assert!(is_stopped(&classify(&events), StopReason::Refusal));
    }

    #[test]
    fn a_terminated_loop_maps_to_max_turn_requests() {
        let events = vec![
            envelope(Event::AgentLoopTerminated {
                model_step: 5,
                reason: AgentLoopTerminationReason::ModelStepLimit,
                consecutive_no_progress_steps: 0,
            }),
            envelope(Event::RunFailed {
                message: "step limit".to_owned(),
            }),
        ];
        assert!(is_stopped(&classify(&events), StopReason::MaxTurnRequests));

        let events = vec![
            envelope(Event::AgentLoopTerminated {
                model_step: 5,
                reason: AgentLoopTerminationReason::NoStateProgress,
                consecutive_no_progress_steps: 8,
            }),
            envelope(Event::RunFailed {
                message: "no progress".to_owned(),
            }),
        ];
        assert!(is_stopped(&classify(&events), StopReason::MaxTurnRequests));
    }

    #[test]
    fn a_failure_with_no_typed_evidence_is_a_protocol_error_not_a_stop_reason() {
        let events = vec![envelope(Event::RunFailed {
            message: "provider connection reset".to_owned(),
        })];
        match classify(&events) {
            PromptOutcome::Failed(message) => assert_eq!(message, "provider connection reset"),
            PromptOutcome::Stopped(_) => panic!("an untyped failure must not become a stop reason"),
        }
    }

    #[test]
    fn a_permission_denial_alone_is_not_a_terminal_event() {
        // Sanity check that unrelated Events in the batch (a denied tool
        // call, say) do not confuse the scan: the run keeps going and only
        // its actual terminal Event decides the outcome.
        let events = vec![
            envelope(Event::ToolCallPermissionResolved {
                call_id: "call-1".to_owned(),
                outcome: ToolPermissionOutcome::Denied,
                scope: ToolPermissionScope::Once,
                source: ToolPermissionSource::Policy,
            }),
            envelope(Event::RunCompleted {
                output: Some("done".to_owned()),
            }),
        ];
        assert!(is_stopped(&classify(&events), StopReason::EndTurn));
    }

    #[test]
    fn no_terminal_event_at_all_is_a_protocol_error() {
        let events = vec![envelope(Event::RunStarted)];
        match classify(&events) {
            PromptOutcome::Failed(_) => {}
            PromptOutcome::Stopped(_) => panic!("a run with no terminal event has no stop reason"),
        }
    }
}
