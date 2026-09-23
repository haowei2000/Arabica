use std::collections::HashMap;
use std::error::Error;
use std::fmt::{Display, Formatter};

use serde::{Deserialize, Serialize};
use structure_protocol::{Event, EventEnvelope, EventId, RunId, SessionId};

pub const SHORT_MEMORY_TRACE_SCHEMA_VERSION: &str = "structure.short-memory.trace/v3";

/// Versioned, provider-independent input to the Tier-A benchmark.
#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct ShortMemoryTrace {
    pub schema_version: String,
    pub trace_id: String,
    pub seed: u64,
    pub origin: TraceOrigin,
    pub current_run_id: Option<RunId>,
    pub events: Vec<EventEnvelope>,
    pub oracle: TraceOracle,
}

/// Reproduction metadata for generated or captured traces.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(tag = "type", content = "parameters", rename_all = "snake_case")]
pub enum TraceOrigin {
    Synthetic {
        generator_version: String,
        turn_count: usize,
        tool_calls_per_turn: usize,
        command_output_chunks_per_tool: usize,
        payload_chars: usize,
        failure_every: Option<usize>,
        fork_after_turn: Option<usize>,
        evidence_horizon_turns: usize,
    },
    Captured {
        source_schema_version: String,
        sanitized: bool,
    },
}

impl ShortMemoryTrace {
    pub fn validate(&self) -> Result<(), TraceValidationError> {
        if self.schema_version != SHORT_MEMORY_TRACE_SCHEMA_VERSION {
            return Err(TraceValidationError::new(format!(
                "unsupported trace schema {}; expected {SHORT_MEMORY_TRACE_SCHEMA_VERSION}",
                self.schema_version
            )));
        }
        if self.trace_id.trim().is_empty() {
            return Err(TraceValidationError::new("trace_id must not be empty"));
        }
        if self.events.is_empty() {
            return Err(TraceValidationError::new(
                "a benchmark trace must contain at least one event",
            ));
        }
        if self.oracle.minimum_evidence_recall_bps > 10_000 {
            return Err(TraceValidationError::new(
                "minimum_evidence_recall_bps must be between 0 and 10000",
            ));
        }

        let mut source_events = HashMap::with_capacity(self.events.len());
        for (position, event) in self.events.iter().enumerate() {
            if source_events
                .insert(event.event_id.clone(), (position, event))
                .is_some()
            {
                return Err(TraceValidationError::new(format!(
                    "duplicate event id {}",
                    event.event_id
                )));
            }
        }

        let oracle_ids = self
            .oracle
            .required_anchor_event_ids
            .iter()
            .chain(&self.oracle.gold_evidence_event_ids)
            .chain(self.oracle.evidence_units.iter().map(|unit| &unit.event_id))
            .chain(
                self.oracle
                    .tool_relations
                    .iter()
                    .flat_map(|relation| [&relation.call_event_id, &relation.result_event_id]),
            )
            .chain(self.oracle.lineage.iter().flat_map(|lineage| {
                lineage
                    .inherited_event_ids
                    .iter()
                    .chain(&lineage.local_event_ids)
            }));
        for event_id in oracle_ids {
            if !source_events.contains_key(event_id) {
                return Err(TraceValidationError::new(format!(
                    "oracle references missing event id {event_id}"
                )));
            }
        }
        let gold_evidence_ids: std::collections::HashSet<_> =
            self.oracle.gold_evidence_event_ids.iter().collect();
        let evidence_unit_ids: std::collections::HashSet<_> = self
            .oracle
            .evidence_units
            .iter()
            .map(|unit| &unit.event_id)
            .collect();
        if gold_evidence_ids.len() != self.oracle.gold_evidence_event_ids.len()
            || evidence_unit_ids.len() != self.oracle.evidence_units.len()
            || gold_evidence_ids != evidence_unit_ids
        {
            return Err(TraceValidationError::new(
                "evidence_units must contain exactly one oracle for each gold evidence event",
            ));
        }
        if self.oracle.evidence_units.iter().any(|unit| {
            unit.required_key_fragments.is_empty()
                || unit
                    .required_key_fragments
                    .iter()
                    .any(|fragment| fragment.is_empty())
        }) {
            return Err(TraceValidationError::new(
                "each evidence unit must declare non-empty required_key_fragments",
            ));
        }
        for relation in &self.oracle.tool_relations {
            let (call_position, call) = source_events
                .get(&relation.call_event_id)
                .expect("oracle ids were validated above");
            let (result_position, result) = source_events
                .get(&relation.result_event_id)
                .expect("oracle ids were validated above");
            if !matches!(
                &call.event,
                Event::ToolCallRequested { call_id, .. } if call_id == &relation.call_id
            ) {
                return Err(TraceValidationError::new(format!(
                    "oracle call event {} does not match call_id {}",
                    relation.call_event_id, relation.call_id
                )));
            }
            if !matches!(
                &result.event,
                Event::ToolCallCompleted { call_id, .. } if call_id == &relation.call_id
            ) {
                return Err(TraceValidationError::new(format!(
                    "oracle result event {} does not match call_id {}",
                    relation.result_event_id, relation.call_id
                )));
            }
            if call_position >= result_position {
                return Err(TraceValidationError::new(format!(
                    "oracle relation {} is not ordered call before result",
                    relation.call_id
                )));
            }
        }
        if self.current_run_id.as_ref().is_some_and(|current_run_id| {
            !self
                .events
                .iter()
                .any(|event| event.run_id.as_ref() == Some(current_run_id))
        }) {
            return Err(TraceValidationError::new(
                "current_run_id is not present in the trace",
            ));
        }
        Ok(())
    }

    /// Stable non-cryptographic fingerprint for detecting trace drift.
    pub fn source_fingerprint(&self) -> Result<String, serde_json::Error> {
        let bytes = serde_json::to_vec(&self.events)?;
        let hash = bytes.iter().fold(0xcbf2_9ce4_8422_2325_u64, |hash, byte| {
            (hash ^ u64::from(*byte)).wrapping_mul(0x0000_0100_0000_01b3)
        });
        Ok(format!("fnv1a64:{hash:016x}"))
    }
}

#[derive(Clone, Debug, Default, Deserialize, Eq, PartialEq, Serialize)]
pub struct TraceOracle {
    /// Events that a policy is required to represent in materialised entries.
    pub required_anchor_event_ids: Vec<EventId>,
    /// Evidence events used to compute representation recall.
    pub gold_evidence_event_ids: Vec<EventId>,
    /// Oracle-authored semantic fragments required when evidence is represented
    /// by a compact key instead of its full Runtime item.
    pub evidence_units: Vec<EvidenceUnitOracle>,
    /// Minimum evidence recall in basis points, where 10_000 means 100%.
    pub minimum_evidence_recall_bps: u16,
    /// Known call/result relations used by structural checks and diagnostics.
    pub tool_relations: Vec<ToolRelationOracle>,
    /// Expected inherited/local ordering for a forked Session history.
    pub lineage: Option<TraceLineageOracle>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct EvidenceUnitOracle {
    pub event_id: EventId,
    pub required_key_fragments: Vec<String>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct TraceLineageOracle {
    pub source_session_id: SessionId,
    pub target_session_id: SessionId,
    pub inherited_event_ids: Vec<EventId>,
    pub local_event_ids: Vec<EventId>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct ToolRelationOracle {
    pub call_id: String,
    pub call_event_id: EventId,
    pub result_event_id: EventId,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct TraceValidationError {
    message: String,
}

impl TraceValidationError {
    pub fn new(message: impl Into<String>) -> Self {
        Self {
            message: message.into(),
        }
    }
}

impl Display for TraceValidationError {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        self.message.fmt(formatter)
    }
}

impl Error for TraceValidationError {}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{SyntheticTraceConfig, SyntheticTraceGenerator};

    #[test]
    fn trace_round_trips_and_has_a_stable_fingerprint() {
        let trace = SyntheticTraceGenerator::generate(&SyntheticTraceConfig::default())
            .expect("trace generates");
        let encoded = serde_json::to_vec(&trace).expect("trace serializes");
        let decoded: ShortMemoryTrace =
            serde_json::from_slice(&encoded).expect("trace deserializes");

        assert_eq!(decoded, trace);
        assert_eq!(
            decoded.source_fingerprint().expect("fingerprint computes"),
            trace.source_fingerprint().expect("fingerprint computes")
        );
        trace.validate().expect("trace validates");
    }

    #[test]
    fn every_gold_evidence_event_requires_an_independent_key_oracle() {
        let mut trace = SyntheticTraceGenerator::generate(&SyntheticTraceConfig::default())
            .expect("trace generates");
        trace.oracle.evidence_units.pop();

        let error = trace
            .validate()
            .expect_err("missing evidence oracle is invalid");

        assert!(error.to_string().contains("evidence_units"));
    }
}
