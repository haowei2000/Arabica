use structure_protocol::{Event, EventEnvelope};
use structure_provider::{ShortMemoryEntry, ShortMemoryItem};

/// Pure projection of a Session's immutable event history for the next run.
///
/// Short memory owns no storage and never mutates the event log. Deduplication,
/// compaction, and GC can evolve here while the Session event stream remains
/// the source of truth.
#[derive(Clone, Copy, Debug, Default)]
pub struct ShortMemoryProjector;

impl ShortMemoryProjector {
    pub fn project(events: &[EventEnvelope]) -> Vec<ShortMemoryEntry> {
        events
            .iter()
            .filter_map(|envelope| {
                let item = match &envelope.event {
                    Event::MessageAccepted { content } => ShortMemoryItem::UserMessage {
                        content: content.clone(),
                    },
                    Event::CommandOutput { stream, chunk } => ShortMemoryItem::CommandOutput {
                        stream: *stream,
                        chunk: chunk.clone(),
                    },
                    Event::RunCompleted {
                        output: Some(content),
                    } => ShortMemoryItem::AssistantMessage {
                        content: content.clone(),
                    },
                    Event::RunFailed { message } => ShortMemoryItem::RunFailure {
                        message: message.clone(),
                    },
                    Event::RunCancelled => ShortMemoryItem::RunCancelled,
                    _ => return None,
                };
                Some(ShortMemoryEntry {
                    session_id: envelope.session_id.clone(),
                    sequence: envelope.sequence,
                    run_id: envelope.run_id.clone(),
                    item,
                })
            })
            .collect()
    }
}

#[cfg(test)]
mod tests {
    use structure_protocol::{
        CommandId, EventId, EventMetadata, OutputStream, RunId, SessionId, WorkspaceId,
    };

    use super::*;

    fn envelope(sequence: u64, event: Event) -> EventEnvelope {
        EventEnvelope::new(
            EventMetadata {
                event_id: EventId::new(format!("event-{sequence}")),
                command_id: CommandId::new("command-1"),
                workspace_id: WorkspaceId::new("workspace-1"),
                session_id: SessionId::new("session-1"),
                run_id: Some(RunId::new("run-1")),
                sequence,
                occurred_at_ms: 0,
            },
            event,
        )
    }

    #[test]
    fn projection_keeps_conversation_facts_and_ignores_control_events() {
        let events = vec![
            envelope(1, Event::RunScheduled),
            envelope(
                2,
                Event::MessageAccepted {
                    content: "hello".to_owned(),
                },
            ),
            envelope(
                3,
                Event::CommandOutput {
                    stream: OutputStream::Stdout,
                    chunk: "working".to_owned(),
                },
            ),
            envelope(
                4,
                Event::RunCompleted {
                    output: Some("done".to_owned()),
                },
            ),
        ];

        let projected = ShortMemoryProjector::project(&events);
        assert_eq!(projected.len(), 3);
        assert!(matches!(
            projected[0].item,
            ShortMemoryItem::UserMessage { .. }
        ));
        assert!(matches!(
            projected[1].item,
            ShortMemoryItem::CommandOutput { .. }
        ));
        assert!(matches!(
            projected[2].item,
            ShortMemoryItem::AssistantMessage { .. }
        ));
    }

    #[test]
    fn projection_is_deterministic_and_does_not_modify_history() {
        let events = vec![envelope(
            1,
            Event::MessageAccepted {
                content: "hello".to_owned(),
            },
        )];
        let original = events.clone();

        assert_eq!(
            ShortMemoryProjector::project(&events),
            ShortMemoryProjector::project(&events)
        );
        assert_eq!(events, original);
    }
}
