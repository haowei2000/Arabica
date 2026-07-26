use std::collections::{HashMap, HashSet};
use std::error::Error;
use std::fmt::{Display, Formatter};

use serde::{Deserialize, Serialize};
use structure_model::{MemoryLoadState, ShortMemoryItem};
use structure_protocol::RunId;

use crate::{Baseline, BenchmarkProjection, ShortMemoryTrace};

/// Machine-readable result of one deterministic Tier-A baseline run.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct BenchmarkRun {
    pub benchmark_schema_version: String,
    pub trace_schema_version: String,
    pub trace_id: String,
    pub trace_seed: u64,
    pub current_run_id: Option<RunId>,
    pub source_fingerprint: String,
    pub projection: BenchmarkProjection,
    pub correctness: CorrectnessGates,
    pub metrics: ProjectionMetrics,
}

impl BenchmarkRun {
    pub fn execute(
        trace: &ShortMemoryTrace,
        baseline: Baseline,
    ) -> Result<Self, BenchmarkRunError> {
        trace
            .validate()
            .map_err(|error| BenchmarkRunError::new(error.to_string()))?;
        let source_before = serde_json::to_vec(&trace.events)
            .map_err(|error| BenchmarkRunError::new(error.to_string()))?;
        let first = baseline
            .project(trace)
            .map_err(|error| BenchmarkRunError::new(error.to_string()))?;
        let second = baseline
            .project(trace)
            .map_err(|error| BenchmarkRunError::new(error.to_string()))?;
        let source_after = serde_json::to_vec(&trace.events)
            .map_err(|error| BenchmarkRunError::new(error.to_string()))?;
        let correctness =
            CorrectnessGates::evaluate(trace, &first, &second, source_before == source_after);
        let metrics = ProjectionMetrics::measure(trace, &first)
            .map_err(|error| BenchmarkRunError::new(error.to_string()))?;

        Ok(Self {
            benchmark_schema_version: "structure.short-memory.benchmark-run/v1".to_owned(),
            trace_schema_version: trace.schema_version.clone(),
            trace_id: trace.trace_id.clone(),
            trace_seed: trace.seed,
            current_run_id: trace.current_run_id.clone(),
            source_fingerprint: trace
                .source_fingerprint()
                .map_err(|error| BenchmarkRunError::new(error.to_string()))?,
            projection: first,
            correctness,
            metrics,
        })
    }
}

/// Hard gates that run before compression or performance comparisons.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct CorrectnessGates {
    pub source_immutability: GateCheck,
    pub determinism: GateCheck,
    pub provenance: GateCheck,
    pub order_preservation: GateCheck,
    pub fork_replay_equivalence: GateCheck,
    pub relation_integrity: GateCheck,
    pub pinned_anchor_retention: GateCheck,
    pub evidence_recall: GateCheck,
}

impl CorrectnessGates {
    fn evaluate(
        trace: &ShortMemoryTrace,
        first: &BenchmarkProjection,
        second: &BenchmarkProjection,
        source_is_immutable: bool,
    ) -> Self {
        let source_ids: HashSet<String> = trace
            .events
            .iter()
            .map(|event| event.event_id.to_string())
            .collect();
        let represented_ids: HashSet<String> = first
            .entries
            .iter()
            .flat_map(|entry| entry.source_event_ids.iter().cloned())
            .collect();

        let provenance_failures = first
            .entries
            .iter()
            .map(|entry| {
                let unique: HashSet<_> = entry.source_event_ids.iter().collect();
                usize::from(entry.source_event_ids.is_empty())
                    + entry
                        .source_event_ids
                        .iter()
                        .filter(|event_id| !source_ids.contains(event_id.as_str()))
                        .count()
                    + entry.source_event_ids.len().saturating_sub(unique.len())
            })
            .sum::<usize>();

        let source_positions: HashMap<String, usize> = trace
            .events
            .iter()
            .enumerate()
            .map(|(index, event)| (event.event_id.to_string(), index))
            .collect();
        let mut previous_entry_start = None;
        let mut order_failures = 0_usize;
        for entry in &first.entries {
            let positions: Vec<_> = entry
                .source_event_ids
                .iter()
                .filter_map(|event_id| source_positions.get(event_id).copied())
                .collect();
            if positions.windows(2).any(|pair| pair[0] >= pair[1]) {
                order_failures += 1;
            }
            if let Some(start) = positions.first().copied() {
                if previous_entry_start.is_some_and(|previous| previous >= start) {
                    order_failures += 1;
                }
                previous_entry_start = Some(start);
            }
        }

        let lineage_failures = trace.oracle.lineage.as_ref().map_or(0, |lineage| {
            let expected_ids: Vec<String> = lineage
                .inherited_event_ids
                .iter()
                .chain(&lineage.local_event_ids)
                .map(ToString::to_string)
                .collect();
            let actual_ids: Vec<String> = trace
                .events
                .iter()
                .map(|event| event.event_id.to_string())
                .collect();
            let order_failure = usize::from(expected_ids != actual_ids);
            let inherited_count = lineage.inherited_event_ids.len();
            let boundary = inherited_count.min(trace.events.len());
            let boundary_failure = usize::from(inherited_count > trace.events.len());
            let inherited_session_failures = trace.events[..boundary]
                .iter()
                .filter(|event| event.session_id != lineage.source_session_id)
                .count();
            let local_session_failures = trace.events[boundary..]
                .iter()
                .filter(|event| event.session_id != lineage.target_session_id)
                .count();
            order_failure + boundary_failure + inherited_session_failures + local_session_failures
        });

        let mut seen_full_calls = HashSet::new();
        let mut relation_failures = 0_usize;
        for entry in &first.entries {
            match &entry.item {
                ShortMemoryItem::ToolCall(call) => {
                    seen_full_calls.insert(call.call_id.clone());
                }
                ShortMemoryItem::ToolResult(result)
                    if !seen_full_calls.contains(&result.call_id) =>
                {
                    relation_failures += 1;
                }
                _ => {}
            }
        }
        for relation in &trace.oracle.tool_relations {
            let result_is_represented =
                represented_ids.contains(&relation.result_event_id.to_string());
            let call_is_represented = represented_ids.contains(&relation.call_event_id.to_string());
            if result_is_represented && !call_is_represented {
                relation_failures += 1;
            }
        }

        let retained_anchors = trace
            .oracle
            .required_anchor_event_ids
            .iter()
            .filter(|event_id| represented_ids.contains(&event_id.to_string()))
            .count();
        let retained_evidence = trace
            .oracle
            .gold_evidence_event_ids
            .iter()
            .filter(|event_id| represented_ids.contains(&event_id.to_string()))
            .count();
        let evidence_recall_bps = recall_bps(
            retained_evidence,
            trace.oracle.gold_evidence_event_ids.len(),
        );

        Self {
            source_immutability: GateCheck::boolean(
                source_is_immutable,
                "source EventEnvelope serialization is unchanged",
            ),
            determinism: GateCheck::boolean(
                first == second,
                "two projections of the same trace and baseline are identical",
            ),
            provenance: GateCheck::zero_failures(
                provenance_failures,
                "entries have non-empty, unique source ids present in the trace",
            ),
            order_preservation: GateCheck::zero_failures(
                order_failures,
                "entry and per-entry source order follows the supplied trace order",
            ),
            fork_replay_equivalence: GateCheck::zero_failures(
                lineage_failures,
                "fork history is inherited events followed by target Session local events",
            ),
            relation_integrity: GateCheck::zero_failures(
                relation_failures,
                "represented tool results retain an earlier matching tool call",
            ),
            pinned_anchor_retention: GateCheck {
                passed: retained_anchors == trace.oracle.required_anchor_event_ids.len(),
                observed: retained_anchors as u64,
                required: trace.oracle.required_anchor_event_ids.len() as u64,
                detail: "required anchor event ids represented by materialised entries".to_owned(),
            },
            evidence_recall: GateCheck {
                passed: evidence_recall_bps >= u64::from(trace.oracle.minimum_evidence_recall_bps),
                observed: evidence_recall_bps,
                required: u64::from(trace.oracle.minimum_evidence_recall_bps),
                detail: "gold evidence representation recall in basis points".to_owned(),
            },
        }
    }

    pub fn passed(&self) -> bool {
        self.source_immutability.passed
            && self.determinism.passed
            && self.provenance.passed
            && self.order_preservation.passed
            && self.fork_replay_equivalence.passed
            && self.relation_integrity.passed
            && self.pinned_anchor_retention.passed
            && self.evidence_recall.passed
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct GateCheck {
    pub passed: bool,
    pub observed: u64,
    pub required: u64,
    pub detail: String,
}

impl GateCheck {
    fn boolean(passed: bool, detail: &str) -> Self {
        Self {
            passed,
            observed: u64::from(passed),
            required: 1,
            detail: detail.to_owned(),
        }
    }

    fn zero_failures(failures: usize, detail: &str) -> Self {
        Self {
            passed: failures == 0,
            observed: failures as u64,
            required: 0,
            detail: detail.to_owned(),
        }
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct ProjectionMetrics {
    pub source_event_count: usize,
    pub source_bytes: usize,
    pub materialised_entry_count: usize,
    pub materialised_bytes: usize,
    pub estimated_materialised_tokens: usize,
    pub represented_source_event_count: usize,
    pub policy_visible_event_count: Option<usize>,
    pub batch_count: usize,
    pub load_all_batch_count: usize,
    pub load_key_batch_count: usize,
    pub no_load_batch_count: usize,
    pub load_all_estimated_tokens: u64,
    pub load_key_source_estimated_tokens: u64,
    pub no_load_source_estimated_tokens: u64,
}

impl ProjectionMetrics {
    fn measure(
        trace: &ShortMemoryTrace,
        projection: &BenchmarkProjection,
    ) -> Result<Self, serde_json::Error> {
        let source_bytes = serde_json::to_vec(&trace.events)?.len();
        // Measure provider-neutral memory items only. Sequence and provenance
        // metadata remain in the report but are not part of model context.
        let materialised_items: Vec<_> =
            projection.entries.iter().map(|entry| &entry.item).collect();
        let materialised_bytes = serde_json::to_vec(&materialised_items)?.len();
        let represented_source_event_count = projection
            .entries
            .iter()
            .flat_map(|entry| &entry.source_event_ids)
            .collect::<HashSet<_>>()
            .len();
        let policy_visible_event_count = (!projection.visibility.is_empty()).then(|| {
            projection
                .visibility
                .iter()
                .filter(|decision| decision.visible)
                .count()
        });
        let load_all_batch_count = projection
            .batches
            .iter()
            .filter(|batch| batch.load_state == MemoryLoadState::LoadAll)
            .count();
        let load_key_batch_count = projection
            .batches
            .iter()
            .filter(|batch| batch.load_state == MemoryLoadState::LoadKey)
            .count();
        let no_load_batch_count = projection
            .batches
            .iter()
            .filter(|batch| batch.load_state == MemoryLoadState::NoLoad)
            .count();
        let batch_tokens = |state| {
            projection
                .batches
                .iter()
                .filter(|batch| batch.load_state == state)
                .map(|batch| batch.estimated_tokens)
                .sum()
        };
        Ok(Self {
            source_event_count: trace.events.len(),
            source_bytes,
            materialised_entry_count: projection.entries.len(),
            materialised_bytes,
            estimated_materialised_tokens: materialised_bytes.div_ceil(4),
            represented_source_event_count,
            policy_visible_event_count,
            batch_count: projection.batches.len(),
            load_all_batch_count,
            load_key_batch_count,
            no_load_batch_count,
            load_all_estimated_tokens: batch_tokens(MemoryLoadState::LoadAll),
            load_key_source_estimated_tokens: batch_tokens(MemoryLoadState::LoadKey),
            no_load_source_estimated_tokens: batch_tokens(MemoryLoadState::NoLoad),
        })
    }
}

fn recall_bps(retained: usize, total: usize) -> u64 {
    if total == 0 {
        10_000
    } else {
        retained as u64 * 10_000 / total as u64
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct BenchmarkRunError {
    message: String,
}

impl BenchmarkRunError {
    fn new(message: impl Into<String>) -> Self {
        Self {
            message: message.into(),
        }
    }
}

impl Display for BenchmarkRunError {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        self.message.fmt(formatter)
    }
}

impl Error for BenchmarkRunError {}

#[cfg(test)]
mod tests {
    use structure_model::ShortMemoryItem;

    use super::*;
    use crate::{SyntheticTraceConfig, SyntheticTraceGenerator};

    fn two_turn_trace() -> ShortMemoryTrace {
        SyntheticTraceGenerator::generate(&SyntheticTraceConfig {
            turn_count: 2,
            tool_calls_per_turn: 1,
            command_output_chunks_per_tool: 1,
            failure_every: None,
            ..SyntheticTraceConfig::default()
        })
        .expect("trace generates")
    }

    #[test]
    fn b0_passes_all_correctness_gates() {
        let run =
            BenchmarkRun::execute(&two_turn_trace(), Baseline::FullReplay).expect("benchmark runs");

        assert!(run.correctness.passed());
        assert_eq!(run.correctness.evidence_recall.observed, 10_000);
        assert_eq!(run.projection.baseline.id(), "B0");
        assert_eq!(run.metrics.represented_source_event_count, 10);
    }

    #[test]
    fn narrow_b1_reports_fidelity_failure_without_breaking_relations() {
        let run = BenchmarkRun::execute(&two_turn_trace(), Baseline::TailK { entry_limit: 2 })
            .expect("benchmark runs");

        assert!(run.correctness.relation_integrity.passed);
        assert!(!run.correctness.pinned_anchor_retention.passed);
        assert!(!run.correctness.evidence_recall.passed);
        assert!(!run.correctness.passed());
    }

    #[test]
    fn benchmark_report_round_trips_as_json() {
        let run =
            BenchmarkRun::execute(&two_turn_trace(), Baseline::FullReplay).expect("benchmark runs");
        let encoded = serde_json::to_vec(&run).expect("run serializes");
        let decoded: BenchmarkRun = serde_json::from_slice(&encoded).expect("run deserializes");

        assert_eq!(decoded, run);
    }

    #[test]
    fn gates_detect_broken_provenance_order_and_tool_relations() {
        let trace = two_turn_trace();
        let canonical = Baseline::FullReplay.project(&trace).expect("B0 projects");

        let mut broken = canonical.clone();
        broken.entries[0].source_event_ids.clear();
        broken.entries.retain(|entry| {
            !matches!(
                &entry.item,
                ShortMemoryItem::ToolCall(call) if call.call_id == "call-0001-000"
            )
        });
        broken.entries.swap(1, 2);

        let gates = CorrectnessGates::evaluate(&trace, &broken, &broken, true);

        assert!(!gates.provenance.passed);
        assert!(!gates.order_preservation.passed);
        assert!(!gates.relation_integrity.passed);
    }

    #[test]
    fn fork_lineage_with_reset_sequences_passes_replay_gate() {
        let trace = SyntheticTraceGenerator::generate(&SyntheticTraceConfig {
            turn_count: 2,
            tool_calls_per_turn: 1,
            fork_after_turn: Some(1),
            ..SyntheticTraceConfig::default()
        })
        .expect("forked trace generates");

        let run =
            BenchmarkRun::execute(&trace, Baseline::FullReplay).expect("forked benchmark runs");

        assert!(run.correctness.fork_replay_equivalence.passed);
        assert!(run.correctness.order_preservation.passed);
        assert!(run.correctness.passed());
    }

    #[test]
    fn fork_gate_detects_an_incorrect_lineage_manifest() {
        let mut trace = SyntheticTraceGenerator::generate(&SyntheticTraceConfig {
            turn_count: 2,
            tool_calls_per_turn: 0,
            fork_after_turn: Some(1),
            ..SyntheticTraceConfig::default()
        })
        .expect("forked trace generates");
        let lineage = trace.oracle.lineage.as_mut().expect("lineage is recorded");
        lineage.local_event_ids.reverse();

        let run = BenchmarkRun::execute(&trace, Baseline::FullReplay)
            .expect("benchmark still produces diagnostics");

        assert!(!run.correctness.fork_replay_equivalence.passed);
        assert!(!run.correctness.passed());
    }
}
