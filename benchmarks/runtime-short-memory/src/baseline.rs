use std::collections::{BTreeSet, HashSet};
use std::error::Error;
use std::fmt::{Display, Formatter};

use serde::{Deserialize, Serialize};
use structure_model::{MemoryBatchKind, MemoryLoadState, ShortMemoryEntry, ShortMemoryItem};
use structure_runtime::{
    EventVisibilityDecision, MemoryClass, ShortMemoryMaterialization, ShortMemoryPolicy,
    ShortMemoryProjector,
};

use crate::ShortMemoryTrace;

/// Reference selectors and Runtime ablations evaluated over the same trace.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(tag = "type", content = "parameters", rename_all = "snake_case")]
pub enum Baseline {
    /// B0: every provider-relevant event is materialised in full.
    FullReplay,
    /// B1: keep the last K provider-relevant entries, then add any earlier
    /// matching tool call required to encode a selected tool result.
    TailK { entry_limit: usize },
    /// B2: apply event TTL and relation decay without batch disclosure.
    TtlOnly { policy: ShortMemoryPolicy },
    /// B3: apply batch disclosure while keeping every event visible to the
    /// batch layer.
    BatchOnly { recent_turns_load_all: usize },
    /// S: production TTL, relation decay, and batch disclosure together.
    Structure { policy: ShortMemoryPolicy },
}

impl Baseline {
    pub fn id(&self) -> &'static str {
        match self {
            Self::FullReplay => "B0",
            Self::TailK { .. } => "B1",
            Self::TtlOnly { .. } => "B2",
            Self::BatchOnly { .. } => "B3",
            Self::Structure { .. } => "S",
        }
    }

    pub fn default_suite(tail_k: usize) -> Result<Vec<Self>, BaselineError> {
        if tail_k == 0 {
            return Err(BaselineError::new(
                "Tail-K entry_limit must be greater than zero",
            ));
        }
        Ok(vec![
            Self::FullReplay,
            Self::TailK {
                entry_limit: tail_k,
            },
            Self::TtlOnly {
                policy: ShortMemoryPolicy::default(),
            },
            Self::BatchOnly {
                recent_turns_load_all: 2,
            },
            Self::Structure {
                policy: ShortMemoryPolicy::default(),
            },
        ])
    }

    pub fn project(&self, trace: &ShortMemoryTrace) -> Result<BenchmarkProjection, BaselineError> {
        trace
            .validate()
            .map_err(|error| BaselineError::new(error.to_string()))?;
        self.project_prevalidated(trace)
    }

    /// Project a trace whose schema and oracle have already been validated.
    /// Scaling uses this entry point so schema validation stays outside the
    /// timed region.
    pub(crate) fn project_prevalidated(
        &self,
        trace: &ShortMemoryTrace,
    ) -> Result<BenchmarkProjection, BaselineError> {
        let (entries, visibility, batches) = match self {
            Self::FullReplay => (
                ShortMemoryProjector::project_full(&trace.events),
                Vec::new(),
                Vec::new(),
            ),
            Self::TailK { entry_limit } => {
                let full_entries = ShortMemoryProjector::project_full(&trace.events);
                (
                    structurally_closed_tail(&full_entries, *entry_limit)?,
                    Vec::new(),
                    Vec::new(),
                )
            }
            Self::TtlOnly { policy } => {
                let visibility = ShortMemoryProjector::visibility(&trace.events, policy);
                let visible_ids: HashSet<String> = visibility
                    .iter()
                    .filter(|decision| decision.visible)
                    .map(|decision| decision.event_id.to_string())
                    .collect();
                let full_entries = ShortMemoryProjector::project_full(&trace.events);
                let selected = full_entries
                    .iter()
                    .enumerate()
                    .filter(|(_, entry)| {
                        entry
                            .source_event_ids
                            .iter()
                            .any(|event_id| visible_ids.contains(event_id))
                    })
                    .map(|(index, _)| index)
                    .collect();
                (
                    structurally_close_selection(&full_entries, selected)?,
                    visibility.iter().map(ProjectionVisibility::from).collect(),
                    Vec::new(),
                )
            }
            Self::BatchOnly {
                recent_turns_load_all,
            } => {
                let policy = ShortMemoryPolicy::batch_only(*recent_turns_load_all);
                projection_parts(ShortMemoryProjector::materialize(
                    &trace.events,
                    trace.current_run_id.as_ref(),
                    &policy,
                ))
            }
            Self::Structure { policy } => projection_parts(ShortMemoryProjector::materialize(
                &trace.events,
                trace.current_run_id.as_ref(),
                policy,
            )),
        };
        Ok(BenchmarkProjection {
            baseline: self.clone(),
            entries,
            visibility,
            batches,
        })
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct BenchmarkProjection {
    pub baseline: Baseline,
    pub entries: Vec<ShortMemoryEntry>,
    pub visibility: Vec<ProjectionVisibility>,
    pub batches: Vec<ProjectionBatch>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct ProjectionVisibility {
    pub event_id: String,
    pub memory_class: MemoryClass,
    pub accumulated_decay: u64,
    pub pinned: bool,
    pub protected_by_recency_floor: bool,
    pub visible: bool,
}

impl From<&EventVisibilityDecision> for ProjectionVisibility {
    fn from(decision: &EventVisibilityDecision) -> Self {
        Self {
            event_id: decision.event_id.to_string(),
            memory_class: decision.memory_class,
            accumulated_decay: decision.accumulated_decay,
            pinned: decision.pinned,
            protected_by_recency_floor: decision.protected_by_recency_floor,
            visible: decision.visible,
        }
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct ProjectionBatch {
    pub context_key: String,
    pub context_kind: MemoryBatchKind,
    pub source_event_ids: Vec<String>,
    pub estimated_tokens: u64,
    pub load_state: MemoryLoadState,
}

fn projection_parts(
    materialization: ShortMemoryMaterialization,
) -> (
    Vec<ShortMemoryEntry>,
    Vec<ProjectionVisibility>,
    Vec<ProjectionBatch>,
) {
    let visibility = materialization
        .visibility
        .iter()
        .map(ProjectionVisibility::from)
        .collect();
    let batches = materialization
        .batches
        .iter()
        .map(|batch| ProjectionBatch {
            context_key: batch.context_key.clone(),
            context_kind: batch.context_kind,
            source_event_ids: batch
                .events
                .iter()
                .map(|event| event.event_id.to_string())
                .collect(),
            estimated_tokens: batch.estimated_tokens,
            load_state: batch.load_state,
        })
        .collect();
    (materialization.entries, visibility, batches)
}

fn structurally_closed_tail(
    entries: &[ShortMemoryEntry],
    entry_limit: usize,
) -> Result<Vec<ShortMemoryEntry>, BaselineError> {
    if entry_limit == 0 {
        return Err(BaselineError::new(
            "Tail-K entry_limit must be greater than zero",
        ));
    }
    let start = entries.len().saturating_sub(entry_limit);
    structurally_close_selection(entries, (start..entries.len()).collect())
}

fn structurally_close_selection(
    entries: &[ShortMemoryEntry],
    mut selected: BTreeSet<usize>,
) -> Result<Vec<ShortMemoryEntry>, BaselineError> {
    let selected_results: Vec<_> = selected
        .iter()
        .copied()
        .filter(|index| matches!(entries[*index].item, ShortMemoryItem::ToolResult(_)))
        .collect();
    for result_index in selected_results {
        let ShortMemoryItem::ToolResult(result) = &entries[result_index].item else {
            unreachable!("selected result indexes are filtered above");
        };
        let matching_call = entries[..result_index]
            .iter()
            .enumerate()
            .rev()
            .find(|(_, entry)| {
                matches!(
                    &entry.item,
                    ShortMemoryItem::ToolCall(call) if call.call_id == result.call_id
                )
            })
            .map(|(index, _)| index)
            .ok_or_else(|| {
                BaselineError::new(format!(
                    "selected tool result {} has no earlier matching call",
                    result.call_id
                ))
            })?;
        selected.insert(matching_call);
    }

    Ok(selected
        .into_iter()
        .map(|index| entries[index].clone())
        .collect())
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct BaselineError {
    message: String,
}

impl BaselineError {
    fn new(message: impl Into<String>) -> Self {
        Self {
            message: message.into(),
        }
    }
}

impl Display for BaselineError {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        self.message.fmt(formatter)
    }
}

impl Error for BaselineError {}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{BenchmarkRun, SyntheticTraceConfig, SyntheticTraceGenerator};

    fn trace(turn_count: usize) -> ShortMemoryTrace {
        SyntheticTraceGenerator::generate(&SyntheticTraceConfig {
            turn_count,
            tool_calls_per_turn: 1,
            command_output_chunks_per_tool: 1,
            failure_every: None,
            ..SyntheticTraceConfig::default()
        })
        .expect("trace generates")
    }

    #[test]
    fn b0_materialises_every_provider_relevant_event() {
        let projection = Baseline::FullReplay
            .project(&trace(1))
            .expect("B0 projects");

        assert_eq!(projection.entries.len(), 5);
        assert_eq!(projection.baseline.id(), "B0");
    }

    #[test]
    fn b1_adds_the_matching_call_when_the_tail_starts_at_a_result() {
        let projection = Baseline::TailK { entry_limit: 2 }
            .project(&trace(1))
            .expect("B1 projects");

        assert_eq!(projection.entries.len(), 3);
        assert!(matches!(
            projection.entries[0].item,
            ShortMemoryItem::ToolCall(_)
        ));
        assert!(matches!(
            projection.entries[1].item,
            ShortMemoryItem::ToolResult(_)
        ));
    }

    #[test]
    fn b2_applies_ttl_without_creating_batch_keys() {
        let projection = Baseline::TtlOnly {
            policy: ShortMemoryPolicy::default(),
        }
        .project(&trace(30))
        .expect("B2 projects");

        assert!(projection.visibility.iter().any(|item| !item.visible));
        assert!(projection.batches.is_empty());
        assert!(
            projection
                .entries
                .iter()
                .all(|entry| !matches!(entry.item, ShortMemoryItem::BatchKey(_)))
        );
    }

    #[test]
    fn b3_keeps_ttl_visible_and_applies_batch_disclosure() {
        let projection = Baseline::BatchOnly {
            recent_turns_load_all: 2,
        }
        .project(&trace(30))
        .expect("B3 projects");

        assert!(projection.visibility.iter().all(|item| item.visible));
        assert!(!projection.batches.is_empty());
        assert!(
            projection
                .batches
                .iter()
                .any(|batch| batch.load_state == MemoryLoadState::LoadKey)
        );
    }

    #[test]
    fn full_structure_passes_correctness_with_bounded_gold_evidence() {
        let run = BenchmarkRun::execute(
            &trace(30),
            Baseline::Structure {
                policy: ShortMemoryPolicy::default(),
            },
        )
        .expect("Structure benchmark runs");

        assert!(run.correctness.passed());
        assert!(run.projection.visibility.iter().any(|item| !item.visible));
        assert!(!run.projection.batches.is_empty());
    }

    #[test]
    fn default_suite_contains_all_five_methods() {
        let suite = Baseline::default_suite(128).expect("suite is valid");

        assert_eq!(
            suite.iter().map(Baseline::id).collect::<Vec<_>>(),
            ["B0", "B1", "B2", "B3", "S"]
        );
    }

    #[test]
    fn structure_compacts_long_trace_below_full_replay_item_bytes() {
        let trace = trace(100);
        let full = BenchmarkRun::execute(&trace, Baseline::FullReplay).expect("B0 benchmark runs");
        let structure = BenchmarkRun::execute(
            &trace,
            Baseline::Structure {
                policy: ShortMemoryPolicy::default(),
            },
        )
        .expect("Structure benchmark runs");

        assert!(structure.correctness.passed());
        assert!(structure.metrics.materialised_bytes < full.metrics.materialised_bytes);
    }
}
