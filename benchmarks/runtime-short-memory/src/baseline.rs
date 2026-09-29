use std::cell::Cell;
use std::collections::{BTreeSet, HashSet};
use std::error::Error;
use std::fmt::{Display, Formatter};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::Instant;

use arabica_model::{MemoryBatchKind, MemoryLoadState, ShortMemoryEntry, ShortMemoryItem};
use arabica_runtime::{
    ArchivedMemory, EventVisibilityDecision, KeyAdmissionDecision, KeyAdmissionPolicy,
    LongMemoryError, LongMemoryManager, LongMemoryStore, MemoryClass,
    PointerGcAdmissionObservation, RuntimeCompactionStrategy, ShortMemoryMaterialization,
    ShortMemoryPolicy, ShortMemoryProjector, project_compaction_for_benchmark,
};
use serde::{Deserialize, Serialize};

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
    BatchOnly {
        recent_turns_load_all: usize,
        key_admission: KeyAdmissionPolicy,
    },
    /// S: production TTL, relation decay, and batch disclosure together.
    Structure { policy: ShortMemoryPolicy },
    /// PGC: recoverable pointer substitution without durable file semantics.
    PointerGc { checkpoint_batches: usize },
    /// FBGC: exact durable file-backed archive plus substitutive pointers.
    FileBackedGc { checkpoint_batches: usize },
}

impl Baseline {
    pub fn id(&self) -> &'static str {
        match self {
            Self::FullReplay => "B0",
            Self::TailK { .. } => "B1",
            Self::TtlOnly { .. } => "B2",
            Self::BatchOnly { .. } => "B3",
            Self::Structure { .. } => "S",
            Self::PointerGc { .. } => "PGC",
            Self::FileBackedGc { .. } => "FBGC",
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
                key_admission: KeyAdmissionPolicy::default(),
            },
            Self::Structure {
                policy: ShortMemoryPolicy::default(),
            },
            Self::PointerGc {
                checkpoint_batches: 2,
            },
            Self::PointerGc {
                checkpoint_batches: 4,
            },
            Self::PointerGc {
                checkpoint_batches: 8,
            },
            Self::FileBackedGc {
                checkpoint_batches: 2,
            },
            Self::FileBackedGc {
                checkpoint_batches: 4,
            },
            Self::FileBackedGc {
                checkpoint_batches: 8,
            },
        ])
    }

    pub fn with_key_admission(mut self, key_admission: KeyAdmissionPolicy) -> Self {
        match &mut self {
            Self::BatchOnly {
                key_admission: configured,
                ..
            } => *configured = key_admission,
            Self::Structure { policy } => policy.key_admission = key_admission,
            Self::FullReplay
            | Self::TailK { .. }
            | Self::TtlOnly { .. }
            | Self::PointerGc { .. }
            | Self::FileBackedGc { .. } => {}
        }
        self
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
        let mut compaction = None;
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
                key_admission,
            } => {
                let mut policy = ShortMemoryPolicy::batch_only(*recent_turns_load_all);
                policy.key_admission = *key_admission;
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
            Self::PointerGc { checkpoint_batches } => {
                let run_id = trace.current_run_id.as_ref().ok_or_else(|| {
                    BaselineError::new("PGC requires a trace with current_run_id")
                })?;
                let mut memory = LongMemoryManager::default();
                let projection = project_compaction_for_benchmark(
                    &trace.events,
                    run_id,
                    &ShortMemoryPolicy::ttl_only(),
                    RuntimeCompactionStrategy::PointerGc,
                    *checkpoint_batches,
                    &mut memory,
                )
                .map_err(|error| BaselineError::new(error.to_string()))?;
                let metadata = projection_metadata(&projection.visibility, &projection.batches);
                compaction = Some(CompactionProjectionMetrics::from_projection(
                    RuntimeCompactionStrategy::PointerGc,
                    *checkpoint_batches,
                    &projection,
                ));
                (projection.entries, metadata.0, metadata.1)
            }
            Self::FileBackedGc { checkpoint_batches } => {
                let run_id = trace.current_run_id.as_ref().ok_or_else(|| {
                    BaselineError::new("FBGC requires a trace with current_run_id")
                })?;
                let root = deterministic_archive_root();
                let archive_open_started = Instant::now();
                let mut memory = LongMemoryManager::with_file_archive(&root)
                    .map_err(|error| BaselineError::new(error.to_string()))?;
                let archive_open_ns = elapsed_ns(archive_open_started);
                let mut timed_memory = TimedArchiveStore::new(&mut memory);
                let mut projection = project_compaction_for_benchmark(
                    &trace.events,
                    run_id,
                    &ShortMemoryPolicy::ttl_only(),
                    RuntimeCompactionStrategy::FileBackedGc,
                    *checkpoint_batches,
                    &mut timed_memory,
                )
                .map_err(|error| BaselineError::new(error.to_string()))?;
                let archive_io = timed_memory.snapshot();
                projection.timing.archive_put_ns = archive_io.put_ns;
                projection.timing.archive_get_ns = archive_io.get_ns;
                projection.timing.archive_count_ns = archive_io.count_ns;
                projection.timing.archive_put_calls = archive_io.put_calls;
                projection.timing.archive_get_calls = archive_io.get_calls;
                let runtime_timing = projection.timing;
                let metadata_started = Instant::now();
                let metadata = projection_metadata(&projection.visibility, &projection.batches);
                let mut metrics = CompactionProjectionMetrics::from_projection(
                    RuntimeCompactionStrategy::FileBackedGc,
                    *checkpoint_batches,
                    &projection,
                );
                metrics.timing = Some(BenchmarkCompactionTimings {
                    archive_open_ns,
                    first_model_step_ns: runtime_timing.first_model_step_ns,
                    idempotence_model_step_ns: runtime_timing.idempotence_model_step_ns,
                    diagnostic_materialization_ns: runtime_timing.diagnostic_materialization_ns,
                    metadata_assembly_ns: 0,
                    archive_put_ns: runtime_timing.archive_put_ns,
                    archive_get_ns: runtime_timing.archive_get_ns,
                    archive_count_ns: runtime_timing.archive_count_ns,
                    archive_put_calls: runtime_timing.archive_put_calls,
                    archive_get_calls: runtime_timing.archive_get_calls,
                    archive_reopen_count_ns: 0,
                    cleanup_ns: 0,
                });
                if let Some(timing) = &mut metrics.timing {
                    timing.metadata_assembly_ns = elapsed_ns(metadata_started);
                }
                compaction = Some(metrics);
                drop(memory);
                let verification_started = Instant::now();
                let reopened = LongMemoryManager::with_file_archive(&root)
                    .map_err(|error| BaselineError::new(error.to_string()))?;
                let reopened_count = reopened
                    .archived_count()
                    .map_err(|error| BaselineError::new(error.to_string()))?;
                if let Some(compaction) = &mut compaction {
                    compaction.archive_reopen_verified = reopened_count == compaction.archive_count;
                    if let Some(timing) = &mut compaction.timing {
                        timing.archive_reopen_count_ns = elapsed_ns(verification_started);
                    }
                }
                drop(reopened);
                let cleanup_started = Instant::now();
                std::fs::remove_dir_all(&root).map_err(|error| {
                    BaselineError::new(format!(
                        "failed to remove benchmark archive {}: {error}",
                        root.display()
                    ))
                })?;
                if let Some(compaction) = &mut compaction
                    && let Some(timing) = &mut compaction.timing
                {
                    timing.cleanup_ns = elapsed_ns(cleanup_started);
                }
                (projection.entries, metadata.0, metadata.1)
            }
        };
        Ok(BenchmarkProjection {
            baseline: self.clone(),
            entries,
            visibility,
            batches,
            compaction,
        })
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct BenchmarkProjection {
    pub baseline: Baseline,
    pub entries: Vec<ShortMemoryEntry>,
    pub visibility: Vec<ProjectionVisibility>,
    pub batches: Vec<ProjectionBatch>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub compaction: Option<CompactionProjectionMetrics>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct CompactionProjectionMetrics {
    pub strategy: RuntimeCompactionStrategy,
    pub checkpoint_batches: usize,
    pub admission: Option<PointerGcAdmissionObservation>,
    pub archive_count: usize,
    pub archive_idempotent: bool,
    pub archive_reopen_verified: bool,
    pub exact_continuation_bytes: usize,
    pub projected_continuation_bytes: usize,
    pub substitutive_transition: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub timing: Option<BenchmarkCompactionTimings>,
}

#[derive(Clone, Copy, Debug, Default, Deserialize, Eq, PartialEq, Serialize)]
pub struct BenchmarkCompactionTimings {
    pub archive_open_ns: u64,
    /// First policy projection, including newly admitted archive writes.
    pub first_model_step_ns: u64,
    /// Idempotence projection, including reads of already archived batches.
    pub idempotence_model_step_ns: u64,
    pub diagnostic_materialization_ns: u64,
    pub metadata_assembly_ns: u64,
    /// Sum of durable put_archive calls, including fsync and link publication.
    pub archive_put_ns: u64,
    /// Sum of archive probes and reads performed by the two model-step passes.
    pub archive_get_ns: u64,
    pub archive_count_ns: u64,
    pub archive_put_calls: u64,
    pub archive_get_calls: u64,
    pub archive_reopen_count_ns: u64,
    pub cleanup_ns: u64,
}

impl CompactionProjectionMetrics {
    fn from_projection(
        strategy: RuntimeCompactionStrategy,
        checkpoint_batches: usize,
        projection: &arabica_runtime::DeterministicCompactionProjection,
    ) -> Self {
        Self {
            strategy,
            checkpoint_batches,
            admission: projection.admission.clone(),
            archive_count: projection.archive_count,
            archive_idempotent: projection.archive_idempotent,
            archive_reopen_verified: strategy != RuntimeCompactionStrategy::FileBackedGc,
            exact_continuation_bytes: projection.exact_continuation_bytes,
            projected_continuation_bytes: projection.projected_continuation_bytes,
            substitutive_transition: projection.archive_count > 0
                && (projection.projected_continuation_bytes < projection.exact_continuation_bytes
                    || projection
                        .entries
                        .iter()
                        .any(|entry| matches!(entry.item, ShortMemoryItem::MemoryPointer(_)))),
            timing: Some(BenchmarkCompactionTimings {
                first_model_step_ns: projection.timing.first_model_step_ns,
                idempotence_model_step_ns: projection.timing.idempotence_model_step_ns,
                diagnostic_materialization_ns: projection.timing.diagnostic_materialization_ns,
                archive_put_ns: projection.timing.archive_put_ns,
                archive_get_ns: projection.timing.archive_get_ns,
                archive_count_ns: projection.timing.archive_count_ns,
                archive_put_calls: projection.timing.archive_put_calls,
                archive_get_calls: projection.timing.archive_get_calls,
                ..BenchmarkCompactionTimings::default()
            }),
        }
    }
}

fn elapsed_ns(started: Instant) -> u64 {
    started.elapsed().as_nanos().min(u64::MAX as u128) as u64
}

#[derive(Clone, Copy, Debug, Default)]
struct ArchiveIoSnapshot {
    put_ns: u64,
    get_ns: u64,
    count_ns: u64,
    put_calls: u64,
    get_calls: u64,
}

struct TimedArchiveStore<'a> {
    inner: &'a mut LongMemoryManager,
    put_ns: Cell<u64>,
    get_ns: Cell<u64>,
    count_ns: Cell<u64>,
    put_calls: Cell<u64>,
    get_calls: Cell<u64>,
}

impl<'a> TimedArchiveStore<'a> {
    fn new(inner: &'a mut LongMemoryManager) -> Self {
        Self {
            inner,
            put_ns: Cell::new(0),
            get_ns: Cell::new(0),
            count_ns: Cell::new(0),
            put_calls: Cell::new(0),
            get_calls: Cell::new(0),
        }
    }

    fn snapshot(&self) -> ArchiveIoSnapshot {
        ArchiveIoSnapshot {
            put_ns: self.put_ns.get(),
            get_ns: self.get_ns.get(),
            count_ns: self.count_ns.get(),
            put_calls: self.put_calls.get(),
            get_calls: self.get_calls.get(),
        }
    }
}

impl LongMemoryStore for TimedArchiveStore<'_> {
    fn put_archive(
        &mut self,
        memory_id: &str,
        content: String,
        content_hash: String,
    ) -> Result<(), LongMemoryError> {
        let started = Instant::now();
        let result = self.inner.put_archive(memory_id, content, content_hash);
        self.put_ns
            .set(self.put_ns.get().saturating_add(elapsed_ns(started)));
        self.put_calls.set(self.put_calls.get().saturating_add(1));
        result
    }

    fn get_archive(&self, memory_id: &str) -> Result<Option<ArchivedMemory>, LongMemoryError> {
        let started = Instant::now();
        let result = self.inner.get_archive(memory_id);
        self.get_ns
            .set(self.get_ns.get().saturating_add(elapsed_ns(started)));
        self.get_calls.set(self.get_calls.get().saturating_add(1));
        result
    }

    fn search_archives(
        &self,
        query: &str,
        limit: usize,
    ) -> Result<Vec<ArchivedMemory>, LongMemoryError> {
        self.inner.search_archives(query, limit)
    }

    fn archive_count(&self) -> Result<usize, LongMemoryError> {
        let started = Instant::now();
        let result = self.inner.archive_count();
        self.count_ns
            .set(self.count_ns.get().saturating_add(elapsed_ns(started)));
        result
    }
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
    pub raw_item_bytes: usize,
    pub key_content_budget_bytes: usize,
    pub key_content_bytes: usize,
    pub materialized_key_bytes: usize,
    pub key_admission_rank: Option<usize>,
    pub key_admission: KeyAdmissionDecision,
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
            raw_item_bytes: batch.raw_item_bytes,
            key_content_budget_bytes: batch.key_content_budget_bytes,
            key_content_bytes: batch.key_content_bytes,
            materialized_key_bytes: batch.materialized_key_bytes,
            key_admission_rank: batch.key_admission_rank,
            key_admission: batch.key_admission,
            load_state: batch.load_state,
        })
        .collect();
    (materialization.entries, visibility, batches)
}

fn projection_metadata(
    visibility: &[EventVisibilityDecision],
    batches: &[arabica_runtime::EventBatch],
) -> (Vec<ProjectionVisibility>, Vec<ProjectionBatch>) {
    let visibility = visibility.iter().map(ProjectionVisibility::from).collect();
    let batches = batches
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
            raw_item_bytes: batch.raw_item_bytes,
            key_content_budget_bytes: batch.key_content_budget_bytes,
            key_content_bytes: batch.key_content_bytes,
            materialized_key_bytes: batch.materialized_key_bytes,
            key_admission_rank: batch.key_admission_rank,
            key_admission: batch.key_admission,
            load_state: batch.load_state,
        })
        .collect();
    (visibility, batches)
}

fn deterministic_archive_root() -> std::path::PathBuf {
    static NEXT_ARCHIVE: AtomicU64 = AtomicU64::new(1);
    std::env::temp_dir().join(format!(
        "structure-fbgc-tier-a-{}-{}",
        std::process::id(),
        NEXT_ARCHIVE.fetch_add(1, Ordering::Relaxed)
    ))
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
            key_admission: KeyAdmissionPolicy::default(),
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
    fn default_suite_contains_all_preregistered_methods() {
        let suite = Baseline::default_suite(128).expect("suite is valid");

        assert_eq!(
            suite.iter().map(Baseline::id).collect::<Vec<_>>(),
            [
                "B0", "B1", "B2", "B3", "S", "PGC", "PGC", "PGC", "FBGC", "FBGC", "FBGC"
            ]
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

    #[test]
    fn pgc_and_fbgc_activate_and_replay_idempotently_on_a_long_run() {
        let trace = SyntheticTraceGenerator::generate(&SyntheticTraceConfig {
            single_run: true,
            turn_count: 40,
            tool_calls_per_turn: 2,
            payload_chars: 64,
            failure_every: None,
            ..SyntheticTraceConfig::default()
        })
        .expect("long-run trace generates");

        for baseline in [
            Baseline::PointerGc {
                checkpoint_batches: 8,
            },
            Baseline::FileBackedGc {
                checkpoint_batches: 8,
            },
        ] {
            let run = BenchmarkRun::execute(&trace, baseline).expect("compaction benchmark runs");
            let compaction = run
                .projection
                .compaction
                .as_ref()
                .expect("compaction diagnostics exist");
            assert!(run.correctness.passed());
            assert!(
                compaction
                    .admission
                    .as_ref()
                    .is_some_and(|item| item.admitted)
            );
            assert!(compaction.archive_count > 0);
            assert!(compaction.archive_idempotent);
            assert!(compaction.substitutive_transition);
            assert!(compaction.archive_reopen_verified);
        }
    }
}
