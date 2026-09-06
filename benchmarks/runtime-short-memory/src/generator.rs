use std::error::Error;
use std::fmt::{Display, Formatter};

use serde::{Deserialize, Serialize};
use structure_protocol::{
    CommandId, Event, EventEnvelope, EventId, EventMetadata, OutputStream, RunId, SessionId,
    WorkspaceId,
};

use crate::schema::{
    EvidenceUnitOracle, SHORT_MEMORY_TRACE_SCHEMA_VERSION, ShortMemoryTrace, ToolRelationOracle,
    TraceLineageOracle, TraceOracle,
};

/// Parameters that fully determine one synthetic trace.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct SyntheticTraceConfig {
    pub trace_id: String,
    pub seed: u64,
    pub turn_count: usize,
    pub tool_calls_per_turn: usize,
    pub command_output_chunks_per_tool: usize,
    pub payload_chars: usize,
    /// Every Nth tool result is an error. `None` produces no tool errors.
    pub failure_every: Option<usize>,
    /// Continue in a child Session after this completed turn.
    pub fork_after_turn: Option<usize>,
    /// Only tool results in the most recent N turns are gold evidence for the
    /// next model decision. Older results remain relation or compression data.
    pub evidence_horizon_turns: usize,
    /// Emit all logical turns as tool batches in one active run. This is used
    /// by the compaction scaling experiment because PGC and FBGC compact
    /// closed batches within the current run, not completed historical runs.
    #[serde(default)]
    pub single_run: bool,
}

impl Default for SyntheticTraceConfig {
    fn default() -> Self {
        Self {
            trace_id: "synthetic-smoke".to_owned(),
            seed: 20_260_726,
            turn_count: 4,
            tool_calls_per_turn: 2,
            command_output_chunks_per_tool: 1,
            payload_chars: 128,
            failure_every: Some(5),
            fork_after_turn: None,
            evidence_horizon_turns: 2,
            single_run: false,
        }
    }
}

/// Generates reproducible typed protocol traces without a Session or provider.
#[derive(Clone, Copy, Debug, Default)]
pub struct SyntheticTraceGenerator;

impl SyntheticTraceGenerator {
    pub fn generate(config: &SyntheticTraceConfig) -> Result<ShortMemoryTrace, GeneratorError> {
        validate_config(config)?;

        let workspace_id = WorkspaceId::new("workspace-benchmark");
        let session_id = SessionId::new("session-benchmark");
        let mut factory = EnvelopeFactory::new(
            workspace_id.clone(),
            session_id,
            DeterministicRng::new(config.seed),
        );
        let mut events = Vec::new();
        let mut required_anchor_event_ids = Vec::new();
        let mut evidence_candidates = Vec::new();
        let mut tool_relations = Vec::new();
        let mut tool_ordinal = 0_usize;
        let mut current_run_id = None;
        let mut fork_boundary = None;
        let mut lineage_sessions = None;

        events.push(factory.next(
            CommandId::new("command-create"),
            None,
            Event::SessionCreated { workspace_id },
        ));

        for turn in 0..config.turn_count {
            let turn_number = turn + 1;
            let run_id = if config.single_run {
                RunId::new("run-long")
            } else {
                RunId::new(format!("run-{turn_number:04}"))
            };
            let command_id = CommandId::new(format!("command-{turn_number:04}"));
            current_run_id = Some(run_id.clone());

            if !config.single_run || turn == 0 {
                events.push(factory.next(command_id.clone(), Some(&run_id), Event::RunScheduled));
                events.push(factory.next(command_id.clone(), Some(&run_id), Event::RunStarted));
                let message = factory.next(
                    command_id.clone(),
                    Some(&run_id),
                    Event::MessageAccepted {
                        content: if config.single_run {
                            "Inspect the requested workspace state across all tool batches and report the result."
                                .to_owned()
                        } else {
                            format!(
                                "Turn {turn_number}: inspect the requested workspace state and report the result."
                            )
                        },
                    },
                );
                if !config.single_run {
                    required_anchor_event_ids.push(message.event_id.clone());
                }
                events.push(message);
            }

            for tool_index in 0..config.tool_calls_per_turn {
                tool_ordinal += 1;
                let call_id = format!("call-{turn_number:04}-{tool_index:03}");
                let path = if config.single_run {
                    format!(
                        "src/generated_{tool_ordinal:06}_{:04}.rs",
                        factory.rng.next_u64() % (config.turn_count.max(1) as u64 * 2)
                    )
                } else {
                    format!(
                        "src/generated_{:04}.rs",
                        factory.rng.next_u64() % (config.turn_count.max(1) as u64 * 2)
                    )
                };
                let call = factory.next(
                    command_id.clone(),
                    Some(&run_id),
                    Event::ToolCallRequested {
                        call_id: call_id.clone(),
                        name: "read_file".to_owned(),
                        arguments: serde_json::json!({"path": path}),
                        provider_state: None,
                    },
                );
                let call_event_id = call.event_id.clone();
                events.push(call);

                for chunk_index in 0..config.command_output_chunks_per_tool {
                    let chunk = factory.rng.payload(
                        &format!("turn={turn_number} tool={tool_index} chunk={chunk_index} "),
                        config.payload_chars / 4,
                    );
                    events.push(factory.next(
                        command_id.clone(),
                        Some(&run_id),
                        Event::CommandOutput {
                            stream: OutputStream::Stdout,
                            chunk,
                        },
                    ));
                }

                let is_error = config
                    .failure_every
                    .is_some_and(|interval| tool_ordinal % interval == 0);
                let result_text = factory.rng.payload(
                    if is_error {
                        "read failed: "
                    } else {
                        "file content: "
                    },
                    config.payload_chars,
                );
                let required_key_fragments = vec![
                    format!("call_id={call_id}"),
                    format!("status={}", if is_error { "error" } else { "ok" }),
                    format!("result_hash={}", stable_fingerprint(&result_text)),
                ];
                let result = factory.next(
                    command_id.clone(),
                    Some(&run_id),
                    Event::ToolCallCompleted {
                        call_id: call_id.clone(),
                        name: "read_file".to_owned(),
                        result: result_text,
                        is_error,
                    },
                );
                let result_event_id = result.event_id.clone();
                evidence_candidates.push((
                    turn_number,
                    EvidenceUnitOracle {
                        event_id: result_event_id.clone(),
                        required_key_fragments,
                    },
                ));
                tool_relations.push(ToolRelationOracle {
                    call_id,
                    call_event_id,
                    result_event_id,
                });
                events.push(result);
            }

            if !config.single_run {
                let completion = factory.next(
                    command_id,
                    Some(&run_id),
                    Event::RunCompleted {
                        output: Some(format!("Completed synthetic turn {turn_number}.")),
                    },
                );
                required_anchor_event_ids.push(completion.event_id.clone());
                events.push(completion);
            }

            if config.fork_after_turn == Some(turn_number) {
                let source_session_id = factory.session_id.clone();
                let target_session_id = SessionId::new("session-benchmark-fork");
                fork_boundary = Some(events.len());
                lineage_sessions = Some((source_session_id.clone(), target_session_id.clone()));
                factory = factory.fork(target_session_id);
                events.push(factory.next(
                    CommandId::new(format!("command-fork-{turn_number:04}")),
                    None,
                    Event::SessionForked { source_session_id },
                ));
            }
        }

        let lineage = fork_boundary.map(|boundary| {
            let (source_session_id, target_session_id) =
                lineage_sessions.expect("fork boundary always records sessions");
            TraceLineageOracle {
                source_session_id,
                target_session_id,
                inherited_event_ids: events[..boundary]
                    .iter()
                    .map(|event| event.event_id.clone())
                    .collect(),
                local_event_ids: events[boundary..]
                    .iter()
                    .map(|event| event.event_id.clone())
                    .collect(),
            }
        });
        let evidence_start = config
            .turn_count
            .saturating_sub(config.evidence_horizon_turns)
            + 1;
        let evidence_units: Vec<_> = evidence_candidates
            .into_iter()
            .filter_map(|(turn_number, evidence)| {
                (turn_number >= evidence_start).then_some(evidence)
            })
            .collect();
        let gold_evidence_event_ids = evidence_units
            .iter()
            .map(|evidence| evidence.event_id.clone())
            .collect();

        let trace = ShortMemoryTrace {
            schema_version: SHORT_MEMORY_TRACE_SCHEMA_VERSION.to_owned(),
            trace_id: config.trace_id.clone(),
            seed: config.seed,
            origin: crate::TraceOrigin::Synthetic {
                generator_version: if config.single_run {
                    "lcg-v2-single-run".to_owned()
                } else {
                    "lcg-v1".to_owned()
                },
                turn_count: config.turn_count,
                tool_calls_per_turn: config.tool_calls_per_turn,
                command_output_chunks_per_tool: config.command_output_chunks_per_tool,
                payload_chars: config.payload_chars,
                failure_every: config.failure_every,
                fork_after_turn: config.fork_after_turn,
                evidence_horizon_turns: config.evidence_horizon_turns,
                single_run: config.single_run,
            },
            current_run_id,
            events,
            oracle: TraceOracle {
                required_anchor_event_ids,
                gold_evidence_event_ids,
                evidence_units,
                minimum_evidence_recall_bps: 10_000,
                tool_relations,
                lineage,
            },
        };
        trace
            .validate()
            .map_err(|error| GeneratorError::new(error.to_string()))?;
        Ok(trace)
    }
}

fn stable_fingerprint(value: &str) -> String {
    let hash = value
        .as_bytes()
        .iter()
        .fold(0xcbf2_9ce4_8422_2325_u64, |hash, byte| {
            (hash ^ u64::from(*byte)).wrapping_mul(0x0000_0100_0000_01b3)
        });
    format!("fnv1a64:{hash:016x}")
}

fn validate_config(config: &SyntheticTraceConfig) -> Result<(), GeneratorError> {
    if config.trace_id.trim().is_empty() {
        return Err(GeneratorError::new("trace_id must not be empty"));
    }
    if config.turn_count == 0 {
        return Err(GeneratorError::new("turn_count must be greater than zero"));
    }
    if config.failure_every == Some(0) {
        return Err(GeneratorError::new(
            "failure_every must be greater than zero when set",
        ));
    }
    if config
        .fork_after_turn
        .is_some_and(|turn| turn == 0 || turn >= config.turn_count)
    {
        return Err(GeneratorError::new(
            "fork_after_turn must be between 1 and turn_count - 1",
        ));
    }
    if config.single_run && config.fork_after_turn.is_some() {
        return Err(GeneratorError::new(
            "single_run traces do not support fork_after_turn",
        ));
    }
    if config.evidence_horizon_turns == 0 {
        return Err(GeneratorError::new(
            "evidence_horizon_turns must be greater than zero",
        ));
    }
    Ok(())
}

struct EnvelopeFactory {
    workspace_id: WorkspaceId,
    session_id: SessionId,
    sequence: u64,
    rng: DeterministicRng,
}

impl EnvelopeFactory {
    fn new(workspace_id: WorkspaceId, session_id: SessionId, rng: DeterministicRng) -> Self {
        Self {
            workspace_id,
            session_id,
            sequence: 0,
            rng,
        }
    }

    fn next(
        &mut self,
        command_id: CommandId,
        run_id: Option<&RunId>,
        event: Event,
    ) -> EventEnvelope {
        self.sequence += 1;
        EventEnvelope::new(
            EventMetadata {
                event_id: EventId::new(format!("event-{}-{:08}", self.session_id, self.sequence)),
                command_id,
                workspace_id: self.workspace_id.clone(),
                session_id: self.session_id.clone(),
                run_id: run_id.cloned(),
                sequence: self.sequence,
                occurred_at_ms: 1_700_000_000_000 + self.sequence,
            },
            event,
        )
    }

    fn fork(&self, target_session_id: SessionId) -> Self {
        Self::new(self.workspace_id.clone(), target_session_id, self.rng)
    }
}

#[derive(Clone, Copy, Debug)]
struct DeterministicRng {
    state: u64,
}

impl DeterministicRng {
    fn new(seed: u64) -> Self {
        Self {
            state: seed ^ 0x9e37_79b9_7f4a_7c15,
        }
    }

    fn next_u64(&mut self) -> u64 {
        self.state = self
            .state
            .wrapping_mul(6_364_136_223_846_793_005)
            .wrapping_add(1_442_695_040_888_963_407);
        self.state
    }

    fn payload(&mut self, prefix: &str, target_chars: usize) -> String {
        let mut output: String = prefix.chars().take(target_chars).collect();
        const ALPHABET: &[u8] = b"abcdefghijklmnopqrstuvwxyz0123456789";
        while output.len() < target_chars {
            let index = self.next_u64() as usize % ALPHABET.len();
            output.push(char::from(ALPHABET[index]));
        }
        output
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct GeneratorError {
    message: String,
}

impl GeneratorError {
    fn new(message: impl Into<String>) -> Self {
        Self {
            message: message.into(),
        }
    }
}

impl Display for GeneratorError {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        self.message.fmt(formatter)
    }
}

impl Error for GeneratorError {}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn same_configuration_produces_the_same_typed_trace() {
        let config = SyntheticTraceConfig::default();

        let first = SyntheticTraceGenerator::generate(&config).expect("first trace generates");
        let second = SyntheticTraceGenerator::generate(&config).expect("second trace generates");

        assert_eq!(first, second);
        assert_eq!(first.oracle.tool_relations.len(), 8);
        assert_eq!(first.oracle.required_anchor_event_ids.len(), 8);
        assert_eq!(first.oracle.gold_evidence_event_ids.len(), 4);
    }

    #[test]
    fn seed_changes_payload_and_trace_fingerprint() {
        let first = SyntheticTraceGenerator::generate(&SyntheticTraceConfig::default())
            .expect("first trace generates");
        let second = SyntheticTraceGenerator::generate(&SyntheticTraceConfig {
            seed: 7,
            ..SyntheticTraceConfig::default()
        })
        .expect("second trace generates");

        assert_ne!(first.events, second.events);
        assert_ne!(
            first.source_fingerprint().expect("fingerprint computes"),
            second.source_fingerprint().expect("fingerprint computes")
        );
    }

    #[test]
    fn invalid_failure_interval_is_rejected() {
        let error = SyntheticTraceGenerator::generate(&SyntheticTraceConfig {
            failure_every: Some(0),
            ..SyntheticTraceConfig::default()
        })
        .expect_err("zero interval is invalid");

        assert!(error.to_string().contains("failure_every"));
    }

    #[test]
    fn forked_trace_keeps_parent_history_before_reset_child_sequence() {
        let trace = SyntheticTraceGenerator::generate(&SyntheticTraceConfig {
            turn_count: 2,
            tool_calls_per_turn: 0,
            fork_after_turn: Some(1),
            ..SyntheticTraceConfig::default()
        })
        .expect("forked trace generates");
        let lineage = trace.oracle.lineage.as_ref().expect("lineage is recorded");

        assert!(!lineage.inherited_event_ids.is_empty());
        assert!(!lineage.local_event_ids.is_empty());
        assert!(
            trace
                .events
                .windows(2)
                .any(|events| events[0].sequence > events[1].sequence)
        );
        assert_eq!(
            trace.events[lineage.inherited_event_ids.len()].session_id,
            lineage.target_session_id
        );
    }
}
