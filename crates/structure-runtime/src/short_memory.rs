use std::cmp::Reverse;
use std::collections::{BTreeMap, HashMap, HashSet};

use serde::{Deserialize, Serialize};
use structure_model::{
    ContentBlock, MemoryBatchKey, MemoryBatchKind, MemoryLoadState, ShortMemoryEntry,
    ShortMemoryItem, ToolCallItem, ToolResultItem,
};
use structure_protocol::{Event, EventEnvelope, EventId, RunId, ToolInteractionKind};

const BATCH_FIELD_EXCERPT_LIMIT: usize = 64;
const BATCH_USER_MESSAGE_EXCERPT_LIMIT: usize = 256;
const DEFAULT_BATCH_KEY_CONTENT_LIMIT_BYTES: usize = 384;
const DEFAULT_BATCH_TARGET_COMPRESSION_BPS: u16 = 6_000;
const MISC_BUCKET_SIZE: u64 = 50;

const fn default_true() -> bool {
    true
}

const fn default_batch_key_content_limit_bytes() -> usize {
    DEFAULT_BATCH_KEY_CONTENT_LIMIT_BYTES
}

const fn default_batch_target_compression_bps() -> u16 {
    DEFAULT_BATCH_TARGET_COMPRESSION_BPS
}

/// Small retention taxonomy used by Runtime. Protocol events remain detailed
/// audit facts and are projected into one of these policy classes.
#[derive(Clone, Copy, Debug, Deserialize, Eq, Hash, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum MemoryClass {
    Anchor,
    Working,
    Recovery,
    ToolInspection,
    ToolMutation,
    ToolBuild,
    ToolDependency,
    ToolValidation,
    Transient,
    Control,
}

/// Relationship metadata used for precise decay. A completion affects only an
/// older event with the same relation key, such as one matching tool call.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct EventMemoryTraits {
    pub class: MemoryClass,
    pub relation_key: Option<String>,
    pub completes_relation: bool,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct EventTtl {
    /// Maximum accumulated event-count decay. None means unbounded.
    pub ttl_events: Option<u64>,
    /// Pinned events remain visible regardless of accumulated decay.
    pub pin: bool,
}

impl EventTtl {
    pub const fn ttl(ttl_events: u64) -> Self {
        Self {
            ttl_events: Some(ttl_events),
            pin: false,
        }
    }

    pub const fn pinned() -> Self {
        Self {
            ttl_events: None,
            pin: true,
        }
    }

    pub const fn unbounded() -> Self {
        Self {
            ttl_events: None,
            pin: false,
        }
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum DecayMatch {
    Any,
    CompletesRelation,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct DecayRule {
    pub older: MemoryClass,
    pub newer: MemoryClass,
    pub match_kind: DecayMatch,
    pub cost: u64,
}

/// Declarative, replayable policy for event visibility and batch loading.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct ShortMemoryPolicy {
    pub default_ttl_events: u64,
    pub recency_floor: usize,
    pub recent_turns_load_all: usize,
    #[serde(default = "default_true")]
    pub batch_compaction_enabled: bool,
    #[serde(default)]
    pub key_admission: KeyAdmissionPolicy,
    #[serde(default = "default_batch_key_content_limit_bytes")]
    pub batch_key_content_limit_bytes: usize,
    #[serde(default = "default_batch_target_compression_bps")]
    pub batch_target_compression_bps: u16,
    pub ttl_overrides: BTreeMap<MemoryClass, EventTtl>,
    pub decay_rules: Vec<DecayRule>,
}

/// Hard limits for historical `LOAD_KEY` materialisation. `None` is unbounded.
/// Limits apply to UTF-8 key-content bytes, excluding full `LOAD_ALL` items.
#[derive(Clone, Copy, Debug, Default, Deserialize, Eq, PartialEq, Serialize)]
pub struct KeyAdmissionPolicy {
    pub max_key_batches: Option<usize>,
    pub max_key_content_bytes: Option<usize>,
}

impl Default for ShortMemoryPolicy {
    fn default() -> Self {
        let ttl_overrides = BTreeMap::from([
            (MemoryClass::Anchor, EventTtl::pinned()),
            (MemoryClass::Working, EventTtl::ttl(3)),
            (MemoryClass::Recovery, EventTtl::ttl(12)),
            (MemoryClass::ToolInspection, EventTtl::ttl(1)),
            (MemoryClass::ToolMutation, EventTtl::ttl(8)),
            (MemoryClass::ToolBuild, EventTtl::ttl(8)),
            (MemoryClass::ToolDependency, EventTtl::ttl(8)),
            (MemoryClass::ToolValidation, EventTtl::ttl(6)),
            (MemoryClass::Transient, EventTtl::ttl(0)),
            (MemoryClass::Control, EventTtl::ttl(0)),
        ]);
        let decay_rules = vec![
            DecayRule {
                older: MemoryClass::Working,
                newer: MemoryClass::Working,
                match_kind: DecayMatch::CompletesRelation,
                cost: 3,
            },
            DecayRule {
                older: MemoryClass::Working,
                newer: MemoryClass::Recovery,
                match_kind: DecayMatch::CompletesRelation,
                cost: 3,
            },
        ];
        Self {
            default_ttl_events: 6,
            recency_floor: 20,
            recent_turns_load_all: 2,
            batch_compaction_enabled: true,
            key_admission: KeyAdmissionPolicy::default(),
            batch_key_content_limit_bytes: DEFAULT_BATCH_KEY_CONTENT_LIMIT_BYTES,
            batch_target_compression_bps: DEFAULT_BATCH_TARGET_COMPRESSION_BPS,
            ttl_overrides,
            decay_rules,
        }
    }
}

impl ShortMemoryPolicy {
    /// Keep every visible event as a full typed item. This is the B0
    /// counterfactual used to measure TTL and BatchKey savings.
    pub fn full_replay() -> Self {
        Self {
            batch_compaction_enabled: false,
            ..Self::batch_only(usize::MAX)
        }
    }

    /// Apply production TTL and relation decay without BatchKey compaction.
    /// This is the B2 ablation policy.
    pub fn ttl_only() -> Self {
        Self {
            batch_compaction_enabled: false,
            ..Self::default()
        }
    }

    /// Disable event TTL while retaining batch disclosure. This is the B3
    /// ablation policy and is also useful for diagnostics.
    pub fn batch_only(recent_turns_load_all: usize) -> Self {
        let ttl_overrides = BTreeMap::from([
            (MemoryClass::Anchor, EventTtl::unbounded()),
            (MemoryClass::Working, EventTtl::unbounded()),
            (MemoryClass::Recovery, EventTtl::unbounded()),
            (MemoryClass::ToolInspection, EventTtl::unbounded()),
            (MemoryClass::ToolMutation, EventTtl::unbounded()),
            (MemoryClass::ToolBuild, EventTtl::unbounded()),
            (MemoryClass::ToolDependency, EventTtl::unbounded()),
            (MemoryClass::ToolValidation, EventTtl::unbounded()),
            (MemoryClass::Transient, EventTtl::unbounded()),
            (MemoryClass::Control, EventTtl::unbounded()),
        ]);
        Self {
            default_ttl_events: u64::MAX,
            recency_floor: 0,
            recent_turns_load_all,
            batch_compaction_enabled: true,
            key_admission: KeyAdmissionPolicy::default(),
            batch_key_content_limit_bytes: DEFAULT_BATCH_KEY_CONTENT_LIMIT_BYTES,
            batch_target_compression_bps: DEFAULT_BATCH_TARGET_COMPRESSION_BPS,
            ttl_overrides,
            decay_rules: Vec::new(),
        }
    }

    fn ttl_for(&self, class: MemoryClass) -> EventTtl {
        self.ttl_overrides
            .get(&class)
            .copied()
            .unwrap_or_else(|| EventTtl::ttl(self.default_ttl_events))
    }

    fn decay_cost(
        &self,
        older: MemoryClass,
        newer: MemoryClass,
        match_kind: DecayMatch,
    ) -> Option<u64> {
        self.decay_rules
            .iter()
            .rev()
            .find(|rule| {
                rule.older == older && rule.newer == newer && rule.match_kind == match_kind
            })
            .map(|rule| rule.cost)
    }
}

/// Explainable decision for one immutable source event.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct EventVisibilityDecision {
    pub event_id: EventId,
    pub sequence: u64,
    pub memory_class: MemoryClass,
    pub relation_key: Option<String>,
    pub accumulated_decay: u64,
    pub ttl_events: Option<u64>,
    pub pinned: bool,
    pub protected_by_recency_floor: bool,
    pub visible: bool,
}

/// A stable runtime grouping over immutable events.
#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct EventBatch {
    pub context_key: String,
    pub context_kind: MemoryBatchKind,
    pub run_id: Option<RunId>,
    pub sequence_start: u64,
    pub sequence_end: u64,
    pub event_count: usize,
    pub estimated_tokens: u64,
    pub raw_item_bytes: usize,
    pub key_content_budget_bytes: usize,
    pub key_content: String,
    pub key_content_bytes: usize,
    pub materialized_key_bytes: usize,
    pub key_admission_rank: Option<usize>,
    pub key_admission: KeyAdmissionDecision,
    pub load_state: MemoryLoadState,
    pub events: Vec<EventEnvelope>,
}

/// Explainable outcome of applying the key budget to one batch.
#[derive(Clone, Copy, Debug, Default, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum KeyAdmissionDecision {
    #[default]
    NotCandidate,
    Admitted,
    KeptFullNoBenefit,
    RejectedBatchLimit,
    RejectedByteLimit,
}

#[derive(Clone, Copy, Debug, Default, Deserialize, Eq, PartialEq, Serialize)]
pub struct KeyAdmissionSummary {
    pub policy: KeyAdmissionPolicy,
    pub candidate_batches: usize,
    pub admitted_batches: usize,
    pub rejected_batches: usize,
    pub kept_full_no_benefit_batches: usize,
    pub admitted_key_content_bytes: usize,
}

/// Complete output of short-memory materialisation. The source event slice is
/// never modified; decisions and batches are returned for replay diagnostics.
#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct ShortMemoryMaterialization {
    pub entries: Vec<ShortMemoryEntry>,
    pub batches: Vec<EventBatch>,
    pub visibility: Vec<EventVisibilityDecision>,
    pub key_admission: KeyAdmissionSummary,
}

/// Pure projection of a Session's immutable event history for the next model
/// turn. TTL and batch policies alter prompt visibility, never the audit log.
#[derive(Clone, Copy, Debug, Default)]
pub struct ShortMemoryProjector;

impl ShortMemoryProjector {
    pub fn classify_event(event: &Event) -> EventMemoryTraits {
        event_memory_traits(event)
    }

    /// Project every provider-relevant event without TTL or batch compaction.
    ///
    /// This is the canonical event-to-item mapping used by full-replay
    /// diagnostics and benchmark baselines. Lifecycle-only events remain in
    /// the audit log but do not become model input items.
    pub fn project_full(events: &[EventEnvelope]) -> Vec<ShortMemoryEntry> {
        events
            .iter()
            .flat_map(event_to_short_memory_entries)
            .collect()
    }

    /// Return event-level TTL decisions without applying batch disclosure.
    pub fn visibility(
        events: &[EventEnvelope],
        policy: &ShortMemoryPolicy,
    ) -> Vec<EventVisibilityDecision> {
        visibility_decisions(events, policy)
    }

    pub fn project(events: &[EventEnvelope]) -> Vec<ShortMemoryEntry> {
        Self::materialize(events, None, &ShortMemoryPolicy::default()).entries
    }

    pub fn materialize(
        events: &[EventEnvelope],
        current_run_id: Option<&RunId>,
        policy: &ShortMemoryPolicy,
    ) -> ShortMemoryMaterialization {
        Self::materialize_with_protection(events, current_run_id, policy, true, &HashSet::new())
    }

    /// Project context before a model step inside an active run.
    ///
    /// Only the explicitly protected event tail is forced to `LOAD_ALL`.
    /// Older completed batches in the same run remain eligible for TTL and
    /// BatchKey disclosure. The protected tail is returned as canonical
    /// projected entries; provider continuation is not a second history.
    pub fn materialize_for_model_step(
        events: &[EventEnvelope],
        current_run_id: &RunId,
        protected_event_ids: &HashSet<EventId>,
        policy: &ShortMemoryPolicy,
    ) -> ShortMemoryMaterialization {
        Self::materialize_with_protection(
            events,
            Some(current_run_id),
            policy,
            false,
            protected_event_ids,
        )
    }

    fn materialize_with_protection(
        events: &[EventEnvelope],
        current_run_id: Option<&RunId>,
        policy: &ShortMemoryPolicy,
        protect_all_current_run_tools: bool,
        protected_event_ids: &HashSet<EventId>,
    ) -> ShortMemoryMaterialization {
        // Session Management supplies inherited history before local history.
        // Sequence values are only monotonic within one Session, so sorting a
        // forked history by sequence would interleave unrelated timelines.
        let visibility = Self::visibility(events, policy);
        let visible_by_event_id: HashMap<EventId, bool> = visibility
            .iter()
            .map(|decision| (decision.event_id.clone(), decision.visible))
            .collect();
        let mut batches = build_batches(events);
        assign_load_states(
            &mut batches,
            &visible_by_event_id,
            current_run_id,
            policy.recent_turns_load_all,
            policy.batch_compaction_enabled,
            protect_all_current_run_tools,
            protected_event_ids,
        );
        if !protect_all_current_run_tools {
            if let Some(current_run_id) = current_run_id {
                for batch in &mut batches {
                    if batch.run_id.as_ref() == Some(current_run_id) {
                        batch.raw_item_bytes = model_step_full_batch_item_bytes(&batch.events);
                    }
                }
            }
        }
        let key_admission = populate_key_content(&mut batches, policy);
        let entries = materialize_entries(events, &batches);

        ShortMemoryMaterialization {
            entries,
            batches,
            visibility,
            key_admission,
        }
    }
}

fn visibility_decisions(
    events: &[EventEnvelope],
    policy: &ShortMemoryPolicy,
) -> Vec<EventVisibilityDecision> {
    let floor_start = events.len().saturating_sub(policy.recency_floor);
    let mut newer_class_counts = BTreeMap::<MemoryClass, u64>::new();
    let mut newer_completion_counts = BTreeMap::<(String, MemoryClass), u64>::new();
    let mut decisions = Vec::with_capacity(events.len());

    for (index, event) in events.iter().enumerate().rev() {
        let traits = event_memory_traits(&event.event);
        let ttl = policy.ttl_for(traits.class);
        let mut accumulated_decay: i128 = newer_class_counts
            .iter()
            .map(|(newer, count)| {
                i128::from(*count)
                    * i128::from(
                        policy
                            .decay_cost(traits.class, *newer, DecayMatch::Any)
                            .unwrap_or(1),
                    )
            })
            .sum();
        if let Some(relation_key) = &traits.relation_key {
            for newer_class in newer_class_counts.keys() {
                let completion_count = newer_completion_counts
                    .get(&(relation_key.clone(), *newer_class))
                    .copied()
                    .unwrap_or_default();
                if completion_count == 0 {
                    continue;
                }
                let general_cost = policy
                    .decay_cost(traits.class, *newer_class, DecayMatch::Any)
                    .unwrap_or(1);
                let completion_cost = policy
                    .decay_cost(traits.class, *newer_class, DecayMatch::CompletesRelation)
                    .unwrap_or(general_cost);
                accumulated_decay += i128::from(completion_count)
                    * (i128::from(completion_cost) - i128::from(general_cost));
            }
        }
        let accumulated_decay = accumulated_decay.max(0) as u64;
        let protected_by_recency_floor = index >= floor_start;
        let within_ttl = ttl
            .ttl_events
            .is_none_or(|limit| accumulated_decay <= limit);
        decisions.push(EventVisibilityDecision {
            event_id: event.event_id.clone(),
            sequence: event.sequence,
            memory_class: traits.class,
            relation_key: traits.relation_key.clone(),
            accumulated_decay,
            ttl_events: ttl.ttl_events,
            pinned: ttl.pin,
            protected_by_recency_floor,
            visible: ttl.pin || protected_by_recency_floor || within_ttl,
        });
        *newer_class_counts.entry(traits.class).or_default() += 1;
        if traits.completes_relation {
            if let Some(relation_key) = traits.relation_key {
                *newer_completion_counts
                    .entry((relation_key, traits.class))
                    .or_default() += 1;
            }
        }
    }

    decisions.reverse();
    decisions
}

fn build_batches(events: &[EventEnvelope]) -> Vec<EventBatch> {
    let mut batches = Vec::<EventBatch>::new();
    let mut batch_indexes = HashMap::<String, usize>::new();
    let mut active_tool_by_run = HashMap::<String, String>::new();

    for event in events {
        let (context_key, context_kind) = batch_identity(event, &mut active_tool_by_run);
        let index = if let Some(index) = batch_indexes.get(&context_key) {
            *index
        } else {
            let index = batches.len();
            batch_indexes.insert(context_key.clone(), index);
            batches.push(EventBatch {
                context_key,
                context_kind,
                run_id: event.run_id.clone(),
                sequence_start: event.sequence,
                sequence_end: event.sequence,
                event_count: 0,
                estimated_tokens: 0,
                raw_item_bytes: 0,
                key_content_budget_bytes: 0,
                key_content: String::new(),
                key_content_bytes: 0,
                materialized_key_bytes: 0,
                key_admission_rank: None,
                key_admission: KeyAdmissionDecision::NotCandidate,
                load_state: MemoryLoadState::NoLoad,
                events: Vec::new(),
            });
            index
        };
        let batch = &mut batches[index];
        batch.sequence_start = batch.sequence_start.min(event.sequence);
        batch.sequence_end = batch.sequence_end.max(event.sequence);
        batch.events.push(event.clone());
    }

    for batch in &mut batches {
        batch.event_count = batch.events.len();
        batch.estimated_tokens = estimate_tokens(&batch.events);
        batch.raw_item_bytes = full_batch_item_bytes(&batch.events);
    }
    batches
}

fn populate_key_content(
    batches: &mut [EventBatch],
    policy: &ShortMemoryPolicy,
) -> KeyAdmissionSummary {
    let mut candidates = Vec::new();
    let mut kept_full_no_benefit_batches = 0_usize;
    for (index, batch) in batches.iter_mut().enumerate() {
        if batch.load_state != MemoryLoadState::LoadKey {
            continue;
        }
        batch.key_content_budget_bytes = policy.batch_key_content_limit_bytes.min(
            batch
                .raw_item_bytes
                .saturating_mul(usize::from(policy.batch_target_compression_bps.min(10_000)))
                / 10_000,
        );
        batch.key_content = build_key_content(batch, batch.key_content_budget_bytes);
        batch.key_content_bytes = batch.key_content.len();
        batch.materialized_key_bytes = materialized_batch_key_bytes(batch);
        if batch.materialized_key_bytes >= batch.raw_item_bytes {
            batch.key_admission = KeyAdmissionDecision::KeptFullNoBenefit;
            batch.load_state = MemoryLoadState::LoadAll;
            batch.key_content.clear();
            kept_full_no_benefit_batches += 1;
            continue;
        }
        candidates.push(index);
    }
    if policy.key_admission == KeyAdmissionPolicy::default() {
        let admitted_key_content_bytes = candidates
            .iter()
            .copied()
            .enumerate()
            .map(|(rank, index)| {
                let batch = &mut batches[index];
                batch.key_admission_rank = Some(rank + 1);
                batch.key_admission = KeyAdmissionDecision::Admitted;
                batch.key_content_bytes
            })
            .sum();
        return KeyAdmissionSummary {
            policy: policy.key_admission,
            candidate_batches: candidates.len(),
            admitted_batches: candidates.len(),
            rejected_batches: 0,
            kept_full_no_benefit_batches,
            admitted_key_content_bytes,
        };
    }
    candidates.sort_by_key(|index| {
        let batch = &batches[*index];
        (
            batch_evidence_priority(batch),
            batch_kind_priority(batch.context_kind),
            Reverse(*index),
        )
    });

    let mut admitted_batches = 0_usize;
    let mut admitted_key_content_bytes = 0_usize;
    for (rank, index) in candidates.iter().copied().enumerate() {
        let batch = &mut batches[index];
        batch.key_admission_rank = Some(rank + 1);
        let exceeds_batch_limit = policy
            .key_admission
            .max_key_batches
            .is_some_and(|limit| admitted_batches >= limit);
        let exceeds_byte_limit = policy
            .key_admission
            .max_key_content_bytes
            .is_some_and(|limit| {
                admitted_key_content_bytes.saturating_add(batch.key_content_bytes) > limit
            });
        batch.key_admission = if exceeds_batch_limit {
            KeyAdmissionDecision::RejectedBatchLimit
        } else if exceeds_byte_limit {
            KeyAdmissionDecision::RejectedByteLimit
        } else {
            admitted_batches += 1;
            admitted_key_content_bytes += batch.key_content_bytes;
            KeyAdmissionDecision::Admitted
        };
        if batch.key_admission != KeyAdmissionDecision::Admitted {
            batch.load_state = MemoryLoadState::NoLoad;
            batch.key_content.clear();
        }
    }

    KeyAdmissionSummary {
        policy: policy.key_admission,
        candidate_batches: candidates.len(),
        admitted_batches,
        rejected_batches: candidates.len().saturating_sub(admitted_batches),
        kept_full_no_benefit_batches,
        admitted_key_content_bytes,
    }
}

fn batch_evidence_priority(batch: &EventBatch) -> u8 {
    batch
        .events
        .iter()
        .map(|event| match event_memory_traits(&event.event).class {
            MemoryClass::Anchor => 0,
            MemoryClass::Recovery
            | MemoryClass::ToolMutation
            | MemoryClass::ToolBuild
            | MemoryClass::ToolDependency => 1,
            MemoryClass::ToolValidation => 2,
            MemoryClass::Working => 3,
            MemoryClass::ToolInspection => 4,
            MemoryClass::Control => 5,
            MemoryClass::Transient => 6,
        })
        .min()
        .unwrap_or(7)
}

fn batch_kind_priority(kind: MemoryBatchKind) -> u8 {
    match kind {
        MemoryBatchKind::Turn => 0,
        MemoryBatchKind::Reasoning => 1,
        MemoryBatchKind::Context => 2,
        MemoryBatchKind::Tool => 3,
        MemoryBatchKind::Task => 4,
        MemoryBatchKind::Artifact => 5,
        MemoryBatchKind::Transient => 6,
        MemoryBatchKind::Misc => 7,
    }
}

fn batch_identity(
    envelope: &EventEnvelope,
    active_tool_by_run: &mut HashMap<String, String>,
) -> (String, MemoryBatchKind) {
    let run = envelope
        .run_id
        .as_ref()
        .map_or_else(|| "none".to_owned(), ToString::to_string);
    match &envelope.event {
        Event::MessageAccepted { .. }
        | Event::RunScheduled
        | Event::RunStarted
        | Event::RunCompleted { .. }
        | Event::RunFailed { .. }
        | Event::AgentProgressAdvisory { .. }
        | Event::AgentLoopTerminated { .. }
        | Event::TerminalControlTransition { .. }
        | Event::RunCancelled => (format!("run:{run}:turn:1"), MemoryBatchKind::Turn),
        Event::ModelResponseItem {
            model_step,
            item_index,
            item: structure_model::RuntimeItem::Reasoning(_),
        } => (
            format!("run:{run}:reasoning:{model_step}:{item_index}"),
            MemoryBatchKind::Reasoning,
        ),
        Event::ModelResponseItem {
            model_step,
            item_index,
            ..
        } => (
            format!("run:{run}:model-response:{model_step}:{item_index}"),
            MemoryBatchKind::Transient,
        ),
        Event::ModelResponseCompleted { model_step, .. } => (
            format!("run:{run}:model-response:{model_step}:completed"),
            MemoryBatchKind::Transient,
        ),
        Event::ModelResponseRejected { model_step, .. } => (
            format!("run:{run}:model-response:{model_step}:rejected"),
            MemoryBatchKind::Transient,
        ),
        Event::ModelResponseNormalized { model_step, .. } => (
            format!("run:{run}:model-response:{model_step}:normalized"),
            MemoryBatchKind::Transient,
        ),
        Event::ModelRequestPrepared { model_step, .. } => (
            format!("run:{run}:model-request:{model_step}"),
            MemoryBatchKind::Transient,
        ),
        Event::ToolCallRequested { call_id, .. } => {
            active_tool_by_run.insert(run.clone(), call_id.clone());
            (format!("run:{run}:tool:{call_id}"), MemoryBatchKind::Tool)
        }
        Event::ToolCallClassified { call_id, .. }
        | Event::ToolCallReused { call_id, .. }
        | Event::ToolCallLoopBlocked { call_id, .. } => {
            (format!("run:{run}:tool:{call_id}"), MemoryBatchKind::Tool)
        }
        Event::ToolCallCompleted { call_id, .. } => {
            if active_tool_by_run.get(&run) == Some(call_id) {
                active_tool_by_run.remove(&run);
            }
            (format!("run:{run}:tool:{call_id}"), MemoryBatchKind::Tool)
        }
        Event::CommandOutput { .. } => active_tool_by_run.get(&run).map_or_else(
            || {
                (
                    format!(
                        "run:{run}:transient:{}",
                        envelope.sequence / MISC_BUCKET_SIZE
                    ),
                    MemoryBatchKind::Transient,
                )
            },
            |call_id| (format!("run:{run}:tool:{call_id}"), MemoryBatchKind::Tool),
        ),
        Event::ContextRead { entry } | Event::ContextUpdated { entry } => (
            format!("context:{}:{}", envelope.workspace_id, entry.path),
            MemoryBatchKind::Context,
        ),
        Event::ContextDeleted { path } => (
            format!("context:{}:{path}", envelope.workspace_id),
            MemoryBatchKind::Context,
        ),
        Event::ContextSearchResult { .. } => (
            format!(
                "context:{}:search:{}",
                envelope.workspace_id, envelope.sequence
            ),
            MemoryBatchKind::Context,
        ),
        Event::ContextDisclosureSet { .. } => (
            format!("session:{}:control", envelope.session_id),
            MemoryBatchKind::Misc,
        ),
        Event::SessionCreated { .. }
        | Event::SessionForked { .. }
        | Event::SessionResumed
        | Event::SessionSuspended
        | Event::SessionClosed => (
            format!("session:{}:lifecycle", envelope.session_id),
            MemoryBatchKind::Misc,
        ),
        Event::Error { .. } if envelope.run_id.is_some() => {
            (format!("run:{run}:turn:1"), MemoryBatchKind::Turn)
        }
        Event::Error { .. } => (
            format!(
                "session:{}:error:{}",
                envelope.session_id, envelope.sequence
            ),
            MemoryBatchKind::Misc,
        ),
    }
}

fn assign_load_states(
    batches: &mut [EventBatch],
    visible_by_event_id: &HashMap<EventId, bool>,
    current_run_id: Option<&RunId>,
    recent_turn_count: usize,
    batch_compaction_enabled: bool,
    protect_all_current_run_tools: bool,
    protected_event_ids: &HashSet<EventId>,
) {
    let turn_indexes: Vec<_> = batches
        .iter()
        .enumerate()
        .filter(|(_, batch)| batch.context_kind == MemoryBatchKind::Turn)
        .map(|(index, _)| index)
        .collect();
    let recent_start = turn_indexes.len().saturating_sub(recent_turn_count);
    let recent_turn_indexes = &turn_indexes[recent_start..];

    for (index, batch) in batches.iter_mut().enumerate() {
        let has_visible_event = batch.events.iter().any(|event| {
            visible_by_event_id
                .get(&event.event_id)
                .copied()
                .unwrap_or(false)
        });
        let explicitly_protected = batch
            .events
            .iter()
            .any(|event| protected_event_ids.contains(&event.event_id));
        batch.load_state = if explicitly_protected {
            MemoryLoadState::LoadAll
        } else if !has_visible_event {
            MemoryLoadState::NoLoad
        } else if !batch_compaction_enabled {
            MemoryLoadState::LoadAll
        } else if matches!(
            batch.context_kind,
            MemoryBatchKind::Transient | MemoryBatchKind::Misc
        ) {
            MemoryLoadState::NoLoad
        } else if recent_turn_indexes.contains(&index)
            || (protect_all_current_run_tools
                && batch.context_kind == MemoryBatchKind::Tool
                && current_run_id.is_some_and(|run_id| batch.run_id.as_ref() == Some(run_id)))
        {
            MemoryLoadState::LoadAll
        } else {
            MemoryLoadState::LoadKey
        };
    }
}

fn materialize_entries(events: &[EventEnvelope], batches: &[EventBatch]) -> Vec<ShortMemoryEntry> {
    let mut state_by_event_id = HashMap::<EventId, (&EventBatch, bool)>::new();
    for batch in batches {
        for (index, event) in batch.events.iter().enumerate() {
            state_by_event_id.insert(event.event_id.clone(), (batch, index == 0));
        }
    }

    let mut entries = Vec::new();
    for event in events {
        let Some((batch, first_in_batch)) = state_by_event_id.get(&event.event_id) else {
            continue;
        };
        match batch.load_state {
            MemoryLoadState::LoadAll => {
                entries.extend(event_to_short_memory_entries(event));
            }
            MemoryLoadState::LoadKey if *first_in_batch => {
                entries.push(ShortMemoryEntry {
                    source_event_ids: batch
                        .events
                        .iter()
                        .map(|event| event.event_id.to_string())
                        .collect(),
                    sequence: batch.sequence_start,
                    item: batch_key_item(batch),
                });
            }
            MemoryLoadState::LoadKey | MemoryLoadState::NoLoad => {}
        }
    }
    entries
}

fn event_to_short_memory_entries(envelope: &EventEnvelope) -> Vec<ShortMemoryEntry> {
    if let Event::ModelResponseItem { item, .. } = &envelope.event {
        let item = match item {
            structure_model::RuntimeItem::Reasoning(reasoning) => {
                Some(ShortMemoryItem::Reasoning(reasoning.clone()))
            }
            structure_model::RuntimeItem::Message(message) => {
                Some(ShortMemoryItem::ProviderMessage(message.clone()))
            }
            // Tool calls are projected from tool.call.requested so their
            // execution relation remains one stable batch. That event carries
            // the exact same provider state.
            structure_model::RuntimeItem::ToolCall(_)
            | structure_model::RuntimeItem::ToolResult(_) => None,
        };
        if let Some(item) = item {
            return vec![ShortMemoryEntry {
                source_event_ids: vec![envelope.event_id.to_string()],
                sequence: envelope.sequence,
                item,
            }];
        }
    }
    event_to_short_memory(envelope).into_iter().collect()
}

fn event_to_short_memory(envelope: &EventEnvelope) -> Option<ShortMemoryEntry> {
    let item = match &envelope.event {
        Event::MessageAccepted { content } => ShortMemoryItem::UserMessage {
            content: content.clone(),
        },
        Event::RunCompleted {
            output: Some(content),
        } => ShortMemoryItem::AssistantMessage {
            content: content.clone(),
        },
        Event::ToolCallRequested {
            call_id,
            name,
            arguments,
            provider_state,
        } => ShortMemoryItem::ToolCall(ToolCallItem {
            id: Some(envelope.event_id.to_string()),
            call_id: call_id.clone(),
            name: name.clone(),
            arguments: arguments.clone(),
            provider_state: provider_state.clone(),
        }),
        Event::ToolCallCompleted {
            call_id,
            name,
            result,
            is_error,
        } => ShortMemoryItem::ToolResult(ToolResultItem {
            id: Some(envelope.event_id.to_string()),
            call_id: call_id.clone(),
            name: Some(name.clone()),
            content: vec![ContentBlock::text(result.clone())],
            is_error: *is_error,
        }),
        Event::CommandOutput { stream, chunk } => ShortMemoryItem::Observation {
            content: format!("stream={stream:?}\n{chunk}"),
        },
        Event::RunFailed { message } | Event::Error { message, .. } => {
            ShortMemoryItem::RunFailure {
                message: message.clone(),
            }
        }
        Event::AgentProgressAdvisory { message, .. } => ShortMemoryItem::Observation {
            content: message.clone(),
        },
        Event::RunCancelled => ShortMemoryItem::RunCancelled,
        Event::ContextRead { entry } | Event::ContextUpdated { entry } => {
            ShortMemoryItem::Observation {
                content: format!("context_path={}\n{}", entry.path, entry.content),
            }
        }
        Event::ContextSearchResult { entries } => ShortMemoryItem::Observation {
            content: entries
                .iter()
                .map(|entry| format!("[{}]\n{}", entry.path, entry.content))
                .collect::<Vec<_>>()
                .join("\n\n"),
        },
        Event::ContextDeleted { path } => ShortMemoryItem::Observation {
            content: format!("context_deleted={path}"),
        },
        Event::ContextDisclosureSet { level } => ShortMemoryItem::Observation {
            content: format!("context_disclosure={level:?}"),
        },
        Event::SessionCreated { .. }
        | Event::SessionForked { .. }
        | Event::SessionResumed
        | Event::SessionSuspended
        | Event::SessionClosed
        | Event::RunScheduled
        | Event::RunStarted
        | Event::ModelRequestPrepared { .. }
        | Event::ModelResponseItem { .. }
        | Event::ModelResponseCompleted { .. }
        | Event::ModelResponseRejected { .. }
        | Event::ModelResponseNormalized { .. }
        | Event::ToolCallClassified { .. }
        | Event::ToolCallReused { .. }
        | Event::ToolCallLoopBlocked { .. }
        | Event::AgentLoopTerminated { .. }
        | Event::TerminalControlTransition { .. }
        | Event::RunCompleted { output: None } => return None,
    };
    Some(ShortMemoryEntry {
        source_event_ids: vec![envelope.event_id.to_string()],
        sequence: envelope.sequence,
        item,
    })
}

fn tool_interaction_memory_class(kind: ToolInteractionKind) -> MemoryClass {
    match kind {
        ToolInteractionKind::Inspection => MemoryClass::ToolInspection,
        ToolInteractionKind::Mutation | ToolInteractionKind::MutationWithValidation => {
            MemoryClass::ToolMutation
        }
        ToolInteractionKind::Build => MemoryClass::ToolBuild,
        ToolInteractionKind::Dependency => MemoryClass::ToolDependency,
        ToolInteractionKind::Validation => MemoryClass::ToolValidation,
        ToolInteractionKind::Generic => MemoryClass::Working,
    }
}

fn event_memory_traits(event: &Event) -> EventMemoryTraits {
    let (class, relation_key, completes_relation) = match event {
        Event::SessionCreated { .. }
        | Event::SessionForked { .. }
        | Event::SessionResumed
        | Event::SessionSuspended
        | Event::SessionClosed => (MemoryClass::Control, None, false),
        Event::RunScheduled | Event::RunStarted | Event::RunCancelled => {
            (MemoryClass::Control, None, false)
        }
        Event::MessageAccepted { .. } => (MemoryClass::Anchor, None, false),
        Event::ModelResponseItem {
            item: structure_model::RuntimeItem::Reasoning(_),
            ..
        } => (MemoryClass::Working, None, false),
        Event::ModelResponseItem { .. }
        | Event::ModelResponseCompleted { .. }
        | Event::ModelResponseRejected { .. }
        | Event::ModelResponseNormalized { .. } => (MemoryClass::Control, None, false),
        Event::ModelRequestPrepared { .. } => (MemoryClass::Control, None, false),
        Event::ToolCallRequested { call_id, .. } => {
            (MemoryClass::Working, Some(format!("tool:{call_id}")), false)
        }
        Event::ToolCallClassified { call_id, kind } => (
            tool_interaction_memory_class(*kind),
            Some(format!("tool:{call_id}")),
            false,
        ),
        Event::ToolCallReused { call_id, .. } | Event::ToolCallLoopBlocked { call_id, .. } => {
            (MemoryClass::Working, Some(format!("tool:{call_id}")), false)
        }
        Event::ToolCallCompleted {
            call_id,
            is_error: true,
            ..
        } => (MemoryClass::Recovery, Some(format!("tool:{call_id}")), true),
        Event::ToolCallCompleted {
            call_id,
            is_error: false,
            ..
        } => (MemoryClass::Working, Some(format!("tool:{call_id}")), true),
        Event::CommandOutput { .. } => (MemoryClass::Transient, None, false),
        Event::RunCompleted { output: Some(_) } => (MemoryClass::Anchor, None, false),
        Event::RunCompleted { output: None } => (MemoryClass::Control, None, false),
        Event::AgentProgressAdvisory { .. } => (MemoryClass::Control, None, false),
        Event::TerminalControlTransition { .. } => (MemoryClass::Control, None, false),
        Event::RunFailed { .. } | Event::AgentLoopTerminated { .. } | Event::Error { .. } => {
            (MemoryClass::Recovery, None, false)
        }
        Event::ContextRead { entry } => (
            MemoryClass::Working,
            Some(format!("context:{}", entry.path)),
            false,
        ),
        Event::ContextSearchResult { .. } => (MemoryClass::Working, None, false),
        Event::ContextUpdated { entry } => (
            MemoryClass::Anchor,
            Some(format!("context:{}", entry.path)),
            false,
        ),
        Event::ContextDeleted { path } => {
            (MemoryClass::Anchor, Some(format!("context:{path}")), false)
        }
        Event::ContextDisclosureSet { .. } => (MemoryClass::Control, None, false),
    };
    EventMemoryTraits {
        class,
        relation_key,
        completes_relation,
    }
}

fn event_type_name(event: &Event) -> &'static str {
    match event {
        Event::SessionCreated { .. } => "session.created",
        Event::SessionForked { .. } => "session.forked",
        Event::SessionResumed => "session.resumed",
        Event::SessionSuspended => "session.suspended",
        Event::SessionClosed => "session.closed",
        Event::RunScheduled => "run.scheduled",
        Event::RunStarted => "run.started",
        Event::MessageAccepted { .. } => "message.accepted",
        Event::ModelRequestPrepared { .. } => "model.request.prepared",
        Event::ModelResponseItem { .. } => "model.response.item",
        Event::ModelResponseCompleted { .. } => "model.response.completed",
        Event::ModelResponseRejected { .. } => "model.response.rejected",
        Event::ModelResponseNormalized { .. } => "model.response.normalized",
        Event::ToolCallRequested { .. } => "tool.call.requested",
        Event::ToolCallClassified { .. } => "tool.call.classified",
        Event::ToolCallReused { .. } => "tool.call.reused",
        Event::ToolCallLoopBlocked { .. } => "tool.call.loop_blocked",
        Event::ToolCallCompleted { is_error: true, .. } => "tool.call.error",
        Event::ToolCallCompleted {
            is_error: false, ..
        } => "tool.call.completed",
        Event::AgentProgressAdvisory { .. } => "agent.progress.advisory",
        Event::AgentLoopTerminated { .. } => "agent.loop.terminated",
        Event::TerminalControlTransition { .. } => "terminal.control.transition",
        Event::CommandOutput { .. } => "command.output",
        Event::RunCompleted { .. } => "run.completed",
        Event::RunFailed { .. } => "run.failed",
        Event::RunCancelled => "run.cancelled",
        Event::ContextRead { .. } => "context.read",
        Event::ContextSearchResult { .. } => "context.search.result",
        Event::ContextUpdated { .. } => "context.updated",
        Event::ContextDeleted { .. } => "context.deleted",
        Event::ContextDisclosureSet { .. } => "context.disclosure.set",
        Event::Error { .. } => "error",
    }
}

fn estimate_tokens(events: &[EventEnvelope]) -> u64 {
    let characters: usize = events
        .iter()
        .map(|event| serde_json::to_string(&event.event).map_or(0, |text| text.chars().count()))
        .sum();
    characters.div_ceil(4) as u64
}

fn full_batch_item_bytes(events: &[EventEnvelope]) -> usize {
    let items: Vec<_> = events
        .iter()
        .flat_map(event_to_short_memory_entries)
        .map(|entry| entry.item)
        .collect();
    serde_json::to_vec(&items).map_or(usize::MAX, |encoded| encoded.len())
}

fn model_step_full_batch_item_bytes(events: &[EventEnvelope]) -> usize {
    let items: Vec<_> = events
        .iter()
        .flat_map(event_to_short_memory_entries)
        .map(|entry| entry.item)
        .filter(|item| {
            !matches!(
                item,
                ShortMemoryItem::UserMessage { .. } | ShortMemoryItem::Observation { .. }
            )
        })
        .collect();
    serde_json::to_vec(&items).map_or(usize::MAX, |encoded| encoded.len())
}

fn materialized_batch_key_bytes(batch: &EventBatch) -> usize {
    serde_json::to_vec(&batch_key_item(batch)).map_or(usize::MAX, |encoded| encoded.len())
}

fn batch_key_item(batch: &EventBatch) -> ShortMemoryItem {
    ShortMemoryItem::BatchKey(MemoryBatchKey {
        context_key: batch.context_key.clone(),
        context_kind: batch.context_kind,
        sequence_start: batch.sequence_start,
        sequence_end: batch.sequence_end,
        event_count: batch.event_count,
        estimated_tokens: batch.estimated_tokens,
        key_content: batch.key_content.clone(),
    })
}

fn build_key_content(batch: &EventBatch, content_budget_bytes: usize) -> String {
    let mut type_counts = BTreeMap::<&str, usize>::new();
    for event in &batch.events {
        *type_counts
            .entry(event_type_name(&event.event))
            .or_default() += 1;
    }
    let mut lines = vec![
        format!(
            "batch={} kind={:?} seq={}-{} events={} tokens={}",
            truncate_chars(&batch.context_key, BATCH_FIELD_EXCERPT_LIMIT),
            batch.context_kind,
            batch.sequence_start,
            batch.sequence_end,
            batch.events.len(),
            batch.estimated_tokens,
        )
        .to_lowercase(),
        format!(
            "types={}",
            type_counts
                .iter()
                .map(|(event_type, count)| format!("{event_type}:{count}"))
                .collect::<Vec<_>>()
                .join(",")
        ),
    ];
    let mut semantic_events: Vec<_> = batch.events.iter().collect();
    if batch.context_kind == MemoryBatchKind::Tool {
        semantic_events
            .sort_by_key(|event| !matches!(event.event, Event::ToolCallCompleted { .. }));
    }
    for event in semantic_events {
        if let Some(line) = event_semantic_key(&event.event) {
            lines.push(line);
        }
    }
    truncate_utf8_bytes(&lines.join("\n"), content_budget_bytes)
}

fn event_semantic_key(event: &Event) -> Option<String> {
    match event {
        Event::MessageAccepted { content } => Some(compact_user_message(content)),
        Event::ModelResponseItem {
            model_step,
            item_index,
            item,
        } => Some(format!(
            "model_response_item step={model_step} index={item_index} kind={}",
            match item {
                structure_model::RuntimeItem::Message(_) => "message",
                structure_model::RuntimeItem::ToolCall(_) => "tool_call",
                structure_model::RuntimeItem::ToolResult(_) => "tool_result",
                structure_model::RuntimeItem::Reasoning(_) => "reasoning",
            },
        )),
        Event::ModelResponseCompleted {
            model_step,
            finish_reason,
            usage,
            ..
        } => Some(format!(
            "model_response_completed step={model_step} finish={finish_reason:?} input_tokens={} output_tokens={} cached_input_tokens={}",
            usage.input_tokens, usage.output_tokens, usage.cached_input_tokens,
        )),
        Event::ModelResponseRejected {
            model_step,
            reason,
            finish_reason,
            tool_call_count,
            final_output_present,
        } => Some(
            format!(
                "model_response_rejected step={model_step} reason={reason:?} finish={finish_reason:?} tool_calls={tool_call_count} final_output_present={final_output_present}"
            )
            .to_lowercase(),
        ),
        Event::ModelResponseNormalized {
            model_step,
            policy,
            ignored_assistant_text,
        } => Some(
            format!(
                "model_response_normalized step={model_step} policy={policy:?} ignored_assistant_text={ignored_assistant_text}"
            )
            .to_lowercase(),
        ),
        Event::ModelRequestPrepared {
            model_step,
            request,
        } => Some(format!(
            "model_request step={model_step} model={} items={} tools={} tool_choice={:?}",
            request.model,
            request.items.len(),
            request.tools.len(),
            request.tool_choice,
        )),
        Event::RunCompleted {
            output: Some(content),
        } => Some(compact_text("assistant", content)),
        Event::ToolCallRequested {
            call_id,
            name,
            arguments,
            ..
        } => {
            let arguments = serde_json::to_string(arguments).unwrap_or_default();
            Some(format!(
                "tool_call name={name} call_id={call_id} {}",
                compact_text("arguments", &arguments)
            ))
        }
        Event::ToolCallClassified { call_id, kind } => {
            Some(format!("tool_class call_id={call_id} kind={kind:?}").to_lowercase())
        }
        Event::ToolCallReused {
            call_id,
            source_call_id,
            fingerprint,
            repeat_count,
        } => Some(format!(
            "tool_reused call_id={call_id} source_call_id={source_call_id} fingerprint={fingerprint} repeat_count={repeat_count}"
        )),
        Event::ToolCallLoopBlocked {
            call_id,
            fingerprint,
            repeat_count,
        } => Some(format!(
            "tool_loop_blocked call_id={call_id} fingerprint={fingerprint} repeat_count={repeat_count}"
        )),
        Event::ToolCallCompleted {
            call_id,
            name,
            result,
            is_error,
        } => Some(format!(
            "tool_result name={name} call_id={call_id} status={} {}",
            if *is_error { "error" } else { "ok" },
            compact_text("result", result)
        )),
        Event::RunFailed { message } => Some(compact_text("run_failure", message)),
        Event::AgentProgressAdvisory {
            model_step,
            consecutive_no_progress_steps,
            message,
        } => Some(format!(
            "agent_progress_advisory step={model_step} consecutive_no_progress_steps={consecutive_no_progress_steps} {}",
            compact_text("message", message)
        )),
        Event::AgentLoopTerminated {
            model_step,
            reason,
            consecutive_no_progress_steps,
        } => Some(
            format!(
                "agent_loop_terminated step={model_step} reason={reason:?} consecutive_no_progress_steps={consecutive_no_progress_steps}"
            )
            .to_lowercase(),
        ),
        Event::TerminalControlTransition {
            model_step,
            policy,
            from,
            to,
            reason,
        } => Some(
            format!(
                "terminal_control_transition step={model_step} policy={policy:?} from={from:?} to={to:?} reason={reason:?}"
            )
            .to_lowercase(),
        ),
        Event::RunCancelled => Some("run_status=cancelled".to_owned()),
        Event::ContextRead { entry } => Some(format!(
            "context_read path={} {}",
            entry.path,
            compact_text("content", &entry.content)
        )),
        Event::ContextSearchResult { entries } => Some(format!(
            "context_search paths={}",
            truncate_chars(
                &entries
                    .iter()
                    .map(|entry| entry.path.as_str())
                    .collect::<Vec<_>>()
                    .join(","),
                BATCH_FIELD_EXCERPT_LIMIT,
            )
        )),
        Event::ContextUpdated { entry } => Some(format!(
            "context_updated path={} {}",
            entry.path,
            compact_text("content", &entry.content)
        )),
        Event::ContextDeleted { path } => Some(format!("context_deleted path={path}")),
        Event::ContextDisclosureSet { level } => {
            Some(format!("context_disclosure={level:?}").to_lowercase())
        }
        Event::Error { code, message } => Some(format!(
            "error code={code:?} {}",
            compact_text("message", message)
        )),
        Event::SessionCreated { .. }
        | Event::SessionForked { .. }
        | Event::SessionResumed
        | Event::SessionSuspended
        | Event::SessionClosed
        | Event::RunScheduled
        | Event::RunStarted
        | Event::RunCompleted { output: None }
        | Event::CommandOutput { .. } => None,
    }
}

fn compact_text(label: &str, value: &str) -> String {
    compact_text_with_limit(label, value, BATCH_FIELD_EXCERPT_LIMIT)
}

fn compact_user_message(value: &str) -> String {
    format!(
        "user_hash={} user_chars={} user_content={}",
        stable_fingerprint(value),
        value.chars().count(),
        truncate_chars(value, BATCH_USER_MESSAGE_EXCERPT_LIMIT)
    )
}

fn compact_text_with_limit(label: &str, value: &str, excerpt_limit: usize) -> String {
    format!(
        "{label}_hash={} {label}_chars={} {label}_excerpt={:?}",
        stable_fingerprint(value),
        value.chars().count(),
        truncate_chars(value, excerpt_limit)
    )
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

fn truncate_chars(value: &str, limit: usize) -> String {
    if value.chars().count() <= limit {
        return value.to_owned();
    }
    let mut truncated: String = value.chars().take(limit.saturating_sub(3)).collect();
    truncated.push_str("...");
    truncated
}

fn truncate_utf8_bytes(value: &str, limit: usize) -> String {
    if value.len() <= limit {
        return value.to_owned();
    }
    let mut end = limit.min(value.len());
    while !value.is_char_boundary(end) {
        end = end.saturating_sub(1);
    }
    value[..end].to_owned()
}

#[cfg(test)]
mod tests {
    use structure_model::{
        MemoryLoadState, ProviderState, ReasoningItem, RuntimeItem, ShortMemoryItem,
    };
    use structure_protocol::{
        CommandId, EventId, EventMetadata, OutputStream, SessionId, WorkspaceId,
    };

    use super::*;

    fn envelope(sequence: u64, event: Event) -> EventEnvelope {
        envelope_for_run(sequence, "run-1", event)
    }

    fn envelope_for_run(sequence: u64, run_id: &str, event: Event) -> EventEnvelope {
        envelope_for_session(sequence, "session-1", run_id, event)
    }

    fn envelope_for_session(
        sequence: u64,
        session_id: &str,
        run_id: &str,
        event: Event,
    ) -> EventEnvelope {
        EventEnvelope::new(
            EventMetadata {
                event_id: EventId::new(format!("event-{session_id}-{sequence}")),
                command_id: CommandId::new("command-1"),
                workspace_id: WorkspaceId::new("workspace-1"),
                session_id: SessionId::new(session_id),
                run_id: Some(RunId::new(run_id)),
                sequence,
                occurred_at_ms: 0,
            },
            event,
        )
    }

    #[test]
    fn reasoning_response_is_canonical_and_projects_as_its_own_batch() {
        let reasoning = ReasoningItem {
            id: Some("reasoning-1".to_owned()),
            summary: vec!["inspect before changing state".to_owned()],
            provider_state: Some(ProviderState::OpenAiChatCompletions {
                reasoning_content: "inspect before changing state".to_owned(),
            }),
        };
        let history = vec![envelope(
            1,
            Event::ModelResponseItem {
                model_step: 0,
                item_index: 0,
                item: RuntimeItem::Reasoning(reasoning.clone()),
            },
        )];

        let materialization = ShortMemoryProjector::materialize(
            &history,
            Some(&RunId::new("run-1")),
            &ShortMemoryPolicy::full_replay(),
        );

        assert_eq!(materialization.batches.len(), 1);
        assert_eq!(
            materialization.batches[0].context_kind,
            MemoryBatchKind::Reasoning
        );
        assert!(matches!(
            materialization.entries.as_slice(),
            [ShortMemoryEntry {
                item: ShortMemoryItem::Reasoning(projected),
                ..
            }] if projected == &reasoning
        ));
    }

    #[test]
    fn responses_message_and_tool_state_survive_active_run_projection() {
        let raw_message = serde_json::json!({
            "id": "msg-1",
            "type": "message",
            "role": "assistant",
            "phase": "commentary",
            "content": [{"type": "output_text", "text": "I will inspect it."}]
        });
        let raw_call = serde_json::json!({
            "id": "fc-1",
            "type": "function_call",
            "call_id": "call-1",
            "name": "read_file",
            "arguments": "{\"path\":\"src/lib.rs\"}"
        });
        let message = structure_model::MessageItem {
            id: Some("msg-1".to_owned()),
            role: structure_model::RuntimeRole::Assistant,
            content: vec![ContentBlock::text("I will inspect it.")],
            provider_state: Some(ProviderState::OpenAi {
                item_id: Some("msg-1".to_owned()),
                encrypted_content: None,
                raw_item: Some(raw_message),
            }),
        };
        let call_state = ProviderState::OpenAi {
            item_id: Some("fc-1".to_owned()),
            encrypted_content: None,
            raw_item: Some(raw_call),
        };
        let history = vec![
            envelope(
                1,
                Event::ModelResponseItem {
                    model_step: 0,
                    item_index: 0,
                    item: RuntimeItem::Message(message.clone()),
                },
            ),
            envelope(
                2,
                Event::ToolCallRequested {
                    call_id: "call-1".to_owned(),
                    name: "read_file".to_owned(),
                    arguments: serde_json::json!({"path": "src/lib.rs"}),
                    provider_state: Some(call_state.clone()),
                },
            ),
        ];

        let materialization = ShortMemoryProjector::materialize(
            &history,
            Some(&RunId::new("run-1")),
            &ShortMemoryPolicy::full_replay(),
        );

        assert!(materialization.entries.iter().any(|entry| matches!(
            &entry.item,
            ShortMemoryItem::ProviderMessage(projected) if projected == &message
        )));
        assert!(materialization.entries.iter().any(|entry| matches!(
            &entry.item,
            ShortMemoryItem::ToolCall(projected)
                if projected.provider_state.as_ref() == Some(&call_state)
        )));
    }

    #[test]
    fn detailed_protocol_events_map_to_retention_classes() {
        assert_eq!(
            ShortMemoryProjector::classify_event(&Event::MessageAccepted {
                content: "remember this".to_owned(),
            })
            .class,
            MemoryClass::Anchor
        );
        let tool_error = ShortMemoryProjector::classify_event(&Event::ToolCallCompleted {
            call_id: "call-1".to_owned(),
            name: "read".to_owned(),
            result: "failed".to_owned(),
            is_error: true,
        });
        assert_eq!(tool_error.class, MemoryClass::Recovery);
        assert_eq!(tool_error.relation_key.as_deref(), Some("tool:call-1"));
        assert!(tool_error.completes_relation);
        for (kind, expected_class) in [
            (ToolInteractionKind::Inspection, MemoryClass::ToolInspection),
            (ToolInteractionKind::Mutation, MemoryClass::ToolMutation),
            (ToolInteractionKind::Build, MemoryClass::ToolBuild),
            (ToolInteractionKind::Dependency, MemoryClass::ToolDependency),
            (ToolInteractionKind::Validation, MemoryClass::ToolValidation),
            (ToolInteractionKind::Generic, MemoryClass::Working),
        ] {
            let classified = ShortMemoryProjector::classify_event(&Event::ToolCallClassified {
                call_id: "call-1".to_owned(),
                kind,
            });
            assert_eq!(classified.class, expected_class);
            assert_eq!(classified.relation_key.as_deref(), Some("tool:call-1"));
            assert!(!classified.completes_relation);
        }
        assert_eq!(
            ShortMemoryProjector::classify_event(&Event::CommandOutput {
                stream: OutputStream::Stdout,
                chunk: "noise".to_owned(),
            })
            .class,
            MemoryClass::Transient
        );
        assert_eq!(
            ShortMemoryProjector::classify_event(&Event::SessionClosed).class,
            MemoryClass::Control
        );
    }

    #[test]
    fn tool_interaction_classes_have_independent_default_ttls() {
        let policy = ShortMemoryPolicy::default();

        assert_eq!(
            policy.ttl_for(MemoryClass::ToolInspection),
            EventTtl::ttl(1)
        );
        assert_eq!(policy.ttl_for(MemoryClass::ToolMutation), EventTtl::ttl(8));
        assert_eq!(policy.ttl_for(MemoryClass::ToolBuild), EventTtl::ttl(8));
        assert_eq!(
            policy.ttl_for(MemoryClass::ToolDependency),
            EventTtl::ttl(8)
        );
        assert_eq!(
            policy.ttl_for(MemoryClass::ToolValidation),
            EventTtl::ttl(6)
        );
    }

    #[test]
    fn ttl_uses_newer_event_decay_and_specialized_rules() {
        let events = vec![
            envelope(
                1,
                Event::ToolCallRequested {
                    call_id: "call-1".to_owned(),
                    name: "read".to_owned(),
                    arguments: serde_json::json!({}),
                    provider_state: None,
                },
            ),
            envelope(
                2,
                Event::ToolCallCompleted {
                    call_id: "call-1".to_owned(),
                    name: "read".to_owned(),
                    result: "done".to_owned(),
                    is_error: false,
                },
            ),
            envelope(
                3,
                Event::RunCompleted {
                    output: Some("answer".to_owned()),
                },
            ),
        ];
        let mut policy = ShortMemoryPolicy {
            recency_floor: 0,
            recent_turns_load_all: 0,
            ..ShortMemoryPolicy::default()
        };
        policy
            .ttl_overrides
            .insert(MemoryClass::Working, EventTtl::ttl(2));

        let result = ShortMemoryProjector::materialize(&events, None, &policy);

        assert_eq!(result.visibility[0].accumulated_decay, 4);
        assert!(!result.visibility[0].visible);
        assert!(result.visibility[2].visible);
    }

    #[test]
    fn tool_completion_only_accelerates_its_matching_call() {
        let events = vec![
            envelope(
                1,
                Event::ToolCallRequested {
                    call_id: "call-1".to_owned(),
                    name: "read".to_owned(),
                    arguments: serde_json::json!({}),
                    provider_state: None,
                },
            ),
            envelope(
                2,
                Event::ToolCallCompleted {
                    call_id: "call-2".to_owned(),
                    name: "search".to_owned(),
                    result: "unrelated".to_owned(),
                    is_error: false,
                },
            ),
            envelope(
                3,
                Event::RunCompleted {
                    output: Some("answer".to_owned()),
                },
            ),
        ];
        let mut policy = ShortMemoryPolicy {
            recency_floor: 0,
            recent_turns_load_all: 0,
            ..ShortMemoryPolicy::default()
        };
        policy
            .ttl_overrides
            .insert(MemoryClass::Working, EventTtl::ttl(2));

        let result = ShortMemoryProjector::materialize(&events, None, &policy);

        assert_eq!(result.visibility[0].accumulated_decay, 2);
        assert!(result.visibility[0].visible);
    }

    #[test]
    fn recency_floor_protects_an_expired_event_without_mutating_history() {
        let events = vec![
            envelope(
                1,
                Event::CommandOutput {
                    stream: OutputStream::Stdout,
                    chunk: "old".to_owned(),
                },
            ),
            envelope(
                2,
                Event::CommandOutput {
                    stream: OutputStream::Stdout,
                    chunk: "recent".to_owned(),
                },
            ),
        ];
        let original = events.clone();
        let mut policy = ShortMemoryPolicy {
            recency_floor: 1,
            recent_turns_load_all: 0,
            ..ShortMemoryPolicy::default()
        };
        policy
            .ttl_overrides
            .insert(MemoryClass::Transient, EventTtl::ttl(0));

        let first = ShortMemoryProjector::materialize(&events, None, &policy);
        let second = ShortMemoryProjector::materialize(&events, None, &policy);

        assert!(!first.visibility[0].visible);
        assert!(first.visibility[1].visible);
        assert!(first.visibility[1].protected_by_recency_floor);
        assert_eq!(first, second);
        assert_eq!(events, original);
    }

    #[test]
    fn tool_call_output_and_result_share_one_stable_batch() {
        let events = vec![
            envelope(
                1,
                Event::ToolCallRequested {
                    call_id: "call-7".to_owned(),
                    name: "write_file".to_owned(),
                    arguments: serde_json::json!({"path": "note.txt"}),
                    provider_state: None,
                },
            ),
            envelope(
                2,
                Event::CommandOutput {
                    stream: OutputStream::Stdout,
                    chunk: "writing".to_owned(),
                },
            ),
            envelope(
                3,
                Event::ToolCallCompleted {
                    call_id: "call-7".to_owned(),
                    name: "write_file".to_owned(),
                    result: "ok".to_owned(),
                    is_error: false,
                },
            ),
        ];

        let result = ShortMemoryProjector::materialize(
            &events,
            Some(&RunId::new("run-1")),
            &ShortMemoryPolicy::default(),
        );

        assert_eq!(result.batches.len(), 1);
        assert_eq!(result.batches[0].context_key, "run:run-1:tool:call-7");
        assert_eq!(result.batches[0].event_count, 3);
        assert_eq!(result.batches[0].load_state, MemoryLoadState::LoadAll);
        assert!(matches!(
            result.entries[0].item,
            ShortMemoryItem::ToolCall(_)
        ));
        assert!(matches!(
            result.entries[2].item,
            ShortMemoryItem::ToolResult(_)
        ));
    }

    #[test]
    fn model_step_compacts_older_closed_tools_but_protects_the_active_tail() {
        let events = vec![
            envelope(
                1,
                Event::ToolCallRequested {
                    call_id: "call-old".to_owned(),
                    name: "read_file".to_owned(),
                    arguments: serde_json::json!({"path": "old.txt"}),
                    provider_state: None,
                },
            ),
            envelope(
                2,
                Event::ToolCallCompleted {
                    call_id: "call-old".to_owned(),
                    name: "read_file".to_owned(),
                    result: "old result ".repeat(500),
                    is_error: false,
                },
            ),
            envelope(
                3,
                Event::ToolCallRequested {
                    call_id: "call-active".to_owned(),
                    name: "read_file".to_owned(),
                    arguments: serde_json::json!({"path": "active.txt"}),
                    provider_state: None,
                },
            ),
            envelope(
                4,
                Event::ToolCallCompleted {
                    call_id: "call-active".to_owned(),
                    name: "read_file".to_owned(),
                    result: "active result ".repeat(500),
                    is_error: false,
                },
            ),
        ];
        let protected = HashSet::from([
            EventId::new("event-session-1-3"),
            EventId::new("event-session-1-4"),
        ]);

        let result = ShortMemoryProjector::materialize_for_model_step(
            &events,
            &RunId::new("run-1"),
            &protected,
            &ShortMemoryPolicy::batch_only(0),
        );

        assert_eq!(result.batches[0].load_state, MemoryLoadState::LoadKey);
        assert_eq!(result.batches[1].load_state, MemoryLoadState::LoadAll);
        assert!(matches!(
            result.entries[0].item,
            ShortMemoryItem::BatchKey(_)
        ));
        assert!(result.entries.iter().any(|entry| {
            matches!(
                &entry.item,
                ShortMemoryItem::ToolCall(call) if call.call_id == "call-active"
            )
        }));
        assert!(result.entries.iter().any(|entry| {
            matches!(
                &entry.item,
                ShortMemoryItem::ToolResult(result) if result.call_id == "call-active"
            )
        }));
    }

    #[test]
    fn explicit_model_step_protection_overrides_expired_ttl() {
        let events = vec![
            envelope(
                1,
                Event::ToolCallRequested {
                    call_id: "call-error".to_owned(),
                    name: "shell".to_owned(),
                    arguments: serde_json::json!({"command": "pytest"}),
                    provider_state: None,
                },
            ),
            envelope(
                2,
                Event::ToolCallCompleted {
                    call_id: "call-error".to_owned(),
                    name: "shell".to_owned(),
                    result: "one test failed".to_owned(),
                    is_error: true,
                },
            ),
            envelope(
                3,
                Event::ToolCallRequested {
                    call_id: "call-new".to_owned(),
                    name: "shell".to_owned(),
                    arguments: serde_json::json!({"command": "pwd"}),
                    provider_state: None,
                },
            ),
            envelope(
                4,
                Event::ToolCallCompleted {
                    call_id: "call-new".to_owned(),
                    name: "shell".to_owned(),
                    result: "/workspace".to_owned(),
                    is_error: false,
                },
            ),
        ];
        let protected = HashSet::from([
            EventId::new("event-session-1-1"),
            EventId::new("event-session-1-2"),
        ]);
        let policy = ShortMemoryPolicy {
            recency_floor: 0,
            ttl_overrides: BTreeMap::from([
                (MemoryClass::Anchor, EventTtl::ttl(0)),
                (MemoryClass::Working, EventTtl::ttl(0)),
                (MemoryClass::Recovery, EventTtl::ttl(0)),
                (MemoryClass::Transient, EventTtl::ttl(0)),
                (MemoryClass::Control, EventTtl::ttl(0)),
            ]),
            ..ShortMemoryPolicy::default()
        };

        let result = ShortMemoryProjector::materialize_for_model_step(
            &events,
            &RunId::new("run-1"),
            &protected,
            &policy,
        );

        let error_batch = result
            .batches
            .iter()
            .find(|batch| batch.context_key.ends_with("call-error"))
            .expect("error batch exists");
        assert_eq!(error_batch.load_state, MemoryLoadState::LoadAll);
        assert!(result.entries.iter().any(|entry| {
            matches!(
                &entry.item,
                ShortMemoryItem::ToolResult(result)
                    if result.call_id == "call-error" && result.is_error
            )
        }));
    }

    #[test]
    fn model_step_gate_uses_only_items_that_reach_the_provider() {
        let events = vec![
            envelope(
                1,
                Event::ToolCallRequested {
                    call_id: "call-old".to_owned(),
                    name: "write_file".to_owned(),
                    arguments: serde_json::json!({"path": "old.txt", "content": "ok"}),
                    provider_state: None,
                },
            ),
            envelope(
                2,
                Event::CommandOutput {
                    stream: OutputStream::Stdout,
                    chunk: "duplicate runner output ".repeat(500),
                },
            ),
            envelope(
                3,
                Event::ToolCallCompleted {
                    call_id: "call-old".to_owned(),
                    name: "write_file".to_owned(),
                    result: "ok".to_owned(),
                    is_error: false,
                },
            ),
        ];

        let result = ShortMemoryProjector::materialize_for_model_step(
            &events,
            &RunId::new("run-1"),
            &HashSet::new(),
            &ShortMemoryPolicy::batch_only(0),
        );

        assert_eq!(result.batches[0].load_state, MemoryLoadState::LoadAll);
        assert_eq!(
            result.batches[0].key_admission,
            KeyAdmissionDecision::KeptFullNoBenefit
        );
        assert!(result.batches[0].materialized_key_bytes >= result.batches[0].raw_item_bytes);
    }

    #[test]
    fn full_replay_keeps_older_active_run_tool_batches_uncompressed() {
        let events = vec![
            envelope(
                1,
                Event::ToolCallRequested {
                    call_id: "call-old".to_owned(),
                    name: "read_file".to_owned(),
                    arguments: serde_json::json!({"path": "old.txt"}),
                    provider_state: None,
                },
            ),
            envelope(
                2,
                Event::ToolCallCompleted {
                    call_id: "call-old".to_owned(),
                    name: "read_file".to_owned(),
                    result: "old result ".repeat(500),
                    is_error: false,
                },
            ),
            envelope(
                3,
                Event::ToolCallRequested {
                    call_id: "call-active".to_owned(),
                    name: "read_file".to_owned(),
                    arguments: serde_json::json!({"path": "active.txt"}),
                    provider_state: None,
                },
            ),
            envelope(
                4,
                Event::ToolCallCompleted {
                    call_id: "call-active".to_owned(),
                    name: "read_file".to_owned(),
                    result: "active result ".repeat(500),
                    is_error: false,
                },
            ),
        ];
        let protected = HashSet::from([
            EventId::new("event-session-1-3"),
            EventId::new("event-session-1-4"),
        ]);

        let result = ShortMemoryProjector::materialize_for_model_step(
            &events,
            &RunId::new("run-1"),
            &protected,
            &ShortMemoryPolicy::full_replay(),
        );

        assert!(
            result
                .batches
                .iter()
                .all(|batch| batch.load_state == MemoryLoadState::LoadAll)
        );
        assert!(
            result
                .entries
                .iter()
                .all(|entry| !matches!(entry.item, ShortMemoryItem::BatchKey(_)))
        );
    }

    #[test]
    fn recent_turns_load_all_and_older_turns_load_only_the_key() {
        let events = vec![
            envelope_for_run(
                1,
                "old",
                Event::MessageAccepted {
                    content: "old question ".repeat(200),
                },
            ),
            envelope_for_run(
                2,
                "old",
                Event::RunCompleted {
                    output: Some("old answer ".repeat(200)),
                },
            ),
            envelope_for_run(
                3,
                "recent",
                Event::MessageAccepted {
                    content: "recent question".to_owned(),
                },
            ),
            envelope_for_run(
                4,
                "recent",
                Event::RunCompleted {
                    output: Some("recent answer".to_owned()),
                },
            ),
        ];
        let policy = ShortMemoryPolicy {
            recency_floor: 0,
            recent_turns_load_all: 1,
            ..ShortMemoryPolicy::default()
        };

        let result = ShortMemoryProjector::materialize(&events, None, &policy);

        assert_eq!(result.batches[0].load_state, MemoryLoadState::LoadKey);
        assert_eq!(result.batches[1].load_state, MemoryLoadState::LoadAll);
        assert!(!result.batches[0].key_content.is_empty());
        assert!(result.batches[1].key_content.is_empty());
        assert!(matches!(
            result.entries[0].item,
            ShortMemoryItem::BatchKey(_)
        ));
        assert!(matches!(
            result.entries[1].item,
            ShortMemoryItem::UserMessage { .. }
        ));
        assert!(matches!(
            result.entries[2].item,
            ShortMemoryItem::AssistantMessage { .. }
        ));
    }

    #[test]
    fn expired_batches_are_not_loaded_but_remain_in_the_input_log() {
        let events = vec![
            envelope(
                1,
                Event::ContextRead {
                    entry: structure_protocol::ContextEntry {
                        path: "knowledge/old".to_owned(),
                        content: "old payload".to_owned(),
                    },
                },
            ),
            envelope(
                2,
                Event::RunCompleted {
                    output: Some("answer".to_owned()),
                },
            ),
        ];
        let mut policy = ShortMemoryPolicy {
            recency_floor: 0,
            recent_turns_load_all: 0,
            ..ShortMemoryPolicy::default()
        };
        policy
            .ttl_overrides
            .insert(MemoryClass::Working, EventTtl::ttl(0));

        let result = ShortMemoryProjector::materialize(&events, None, &policy);

        assert_eq!(result.batches[0].load_state, MemoryLoadState::NoLoad);
        assert_eq!(events.len(), 2);
        assert_eq!(events[0].sequence, 1);
    }

    #[test]
    fn forked_histories_use_event_identity_and_preserve_supplied_order() {
        let events = vec![
            envelope_for_session(
                1,
                "parent",
                "parent-run",
                Event::MessageAccepted {
                    content: "parent fact".to_owned(),
                },
            ),
            envelope_for_session(
                1,
                "child",
                "child-run",
                Event::MessageAccepted {
                    content: "child fact".to_owned(),
                },
            ),
        ];

        let result =
            ShortMemoryProjector::materialize(&events, None, &ShortMemoryPolicy::default());

        assert_eq!(result.entries.len(), 2);
        assert_eq!(
            result.entries[0].source_event_ids,
            vec!["event-parent-1".to_owned()]
        );
        assert_eq!(
            result.entries[1].source_event_ids,
            vec!["event-child-1".to_owned()]
        );
    }

    #[test]
    fn full_projection_reuses_runtime_mapping_without_gc_or_batch_keys() {
        let events = vec![
            envelope(1, Event::RunStarted),
            envelope(
                2,
                Event::MessageAccepted {
                    content: "question".to_owned(),
                },
            ),
            envelope(
                3,
                Event::RunCompleted {
                    output: Some("answer".to_owned()),
                },
            ),
        ];

        let entries = ShortMemoryProjector::project_full(&events);

        assert_eq!(entries.len(), 2);
        assert_eq!(entries[0].source_event_ids, vec!["event-session-1-2"]);
        assert_eq!(entries[1].source_event_ids, vec!["event-session-1-3"]);
        assert!(
            entries
                .iter()
                .all(|entry| !matches!(entry.item, ShortMemoryItem::BatchKey(_)))
        );
    }

    #[test]
    fn key_admission_prefers_recent_batches_with_equal_evidence_value() {
        let events = vec![
            envelope_for_run(
                1,
                "old",
                Event::MessageAccepted {
                    content: "old question ".repeat(200),
                },
            ),
            envelope_for_run(
                2,
                "old",
                Event::RunCompleted {
                    output: Some("old answer ".repeat(200)),
                },
            ),
            envelope_for_run(
                3,
                "new",
                Event::MessageAccepted {
                    content: "new question ".repeat(200),
                },
            ),
            envelope_for_run(
                4,
                "new",
                Event::RunCompleted {
                    output: Some("new answer ".repeat(200)),
                },
            ),
        ];
        let policy = ShortMemoryPolicy {
            recency_floor: 0,
            recent_turns_load_all: 0,
            key_admission: KeyAdmissionPolicy {
                max_key_batches: Some(1),
                max_key_content_bytes: None,
            },
            ..ShortMemoryPolicy::default()
        };

        let result = ShortMemoryProjector::materialize(&events, None, &policy);

        assert_eq!(result.key_admission.candidate_batches, 2);
        assert_eq!(result.key_admission.admitted_batches, 1);
        assert_eq!(result.key_admission.rejected_batches, 1);
        assert_eq!(result.batches[0].load_state, MemoryLoadState::NoLoad);
        assert_eq!(
            result.batches[0].key_admission,
            KeyAdmissionDecision::RejectedBatchLimit
        );
        assert_eq!(result.batches[1].load_state, MemoryLoadState::LoadKey);
        assert_eq!(
            result.batches[1].key_admission,
            KeyAdmissionDecision::Admitted
        );
        assert_eq!(result.batches[1].key_admission_rank, Some(1));
    }

    #[test]
    fn zero_byte_budget_rejects_keys_with_an_explainable_decision() {
        let events = vec![envelope(
            1,
            Event::MessageAccepted {
                content: "required anchor ".repeat(200),
            },
        )];
        let policy = ShortMemoryPolicy {
            recency_floor: 0,
            recent_turns_load_all: 0,
            key_admission: KeyAdmissionPolicy {
                max_key_batches: None,
                max_key_content_bytes: Some(0),
            },
            ..ShortMemoryPolicy::default()
        };

        let result = ShortMemoryProjector::materialize(&events, None, &policy);

        assert!(result.entries.is_empty());
        assert_eq!(result.key_admission.candidate_batches, 1);
        assert_eq!(result.key_admission.admitted_key_content_bytes, 0);
        assert_eq!(
            result.batches[0].key_admission,
            KeyAdmissionDecision::RejectedByteLimit
        );
        assert_eq!(result.batches[0].load_state, MemoryLoadState::NoLoad);
        assert!(result.batches[0].key_content.is_empty());
        assert!(result.batches[0].key_content_bytes > 0);
    }

    #[test]
    fn batch_key_is_a_bounded_semantic_index_not_raw_event_replay() {
        let large_result = "x".repeat(5_000);
        let events = vec![
            envelope(
                1,
                Event::ToolCallRequested {
                    call_id: "call-large".to_owned(),
                    name: "read_file".to_owned(),
                    arguments: serde_json::json!({"path": "src/large.rs"}),
                    provider_state: None,
                },
            ),
            envelope(
                2,
                Event::ToolCallCompleted {
                    call_id: "call-large".to_owned(),
                    name: "read_file".to_owned(),
                    result: large_result.clone(),
                    is_error: false,
                },
            ),
        ];
        let policy = ShortMemoryPolicy {
            recent_turns_load_all: 0,
            ..ShortMemoryPolicy::default()
        };

        let result = ShortMemoryProjector::materialize(&events, None, &policy);
        let key = &result.batches[0].key_content;

        assert_eq!(result.batches[0].load_state, MemoryLoadState::LoadKey);
        assert!(key.len() <= DEFAULT_BATCH_KEY_CONTENT_LIMIT_BYTES);
        assert_eq!(
            result.batches[0].key_content_budget_bytes,
            DEFAULT_BATCH_KEY_CONTENT_LIMIT_BYTES.min(
                result.batches[0].raw_item_bytes
                    * usize::from(DEFAULT_BATCH_TARGET_COMPRESSION_BPS)
                    / 10_000
            )
        );
        assert!(result.batches[0].materialized_key_bytes < result.batches[0].raw_item_bytes);
        assert!(key.contains("types=tool.call.completed:1,tool.call.requested:1"));
        assert!(key.contains("tool_call name=read_file"));
        assert!(key.contains("result_hash=fnv1a64:"));
        assert!(!key.contains(&large_result[..1_000]));
    }

    #[test]
    fn short_structured_user_evidence_stays_full_when_key_would_expand_input() {
        let evidence = "Remember this evidence for the next task and reply with MEMORY_STORED.\n<memory_evidence_json>{\"key\":\"tier-b-evidence-0001\",\"value\":\"STRUCTURE_TIER_B_OK\"}</memory_evidence_json>";
        let events = vec![envelope(
            1,
            Event::MessageAccepted {
                content: evidence.to_owned(),
            },
        )];
        let policy = ShortMemoryPolicy {
            recent_turns_load_all: 0,
            ..ShortMemoryPolicy::default()
        };

        let result = ShortMemoryProjector::materialize(&events, None, &policy);
        assert_eq!(result.batches[0].load_state, MemoryLoadState::LoadAll);
        assert_eq!(
            result.batches[0].key_admission,
            KeyAdmissionDecision::KeptFullNoBenefit
        );
        assert_eq!(result.key_admission.kept_full_no_benefit_batches, 1);
        assert!(result.batches[0].materialized_key_bytes >= result.batches[0].raw_item_bytes);
        assert!(matches!(
            result.entries[0].item,
            ShortMemoryItem::UserMessage { .. }
        ));
    }
}
