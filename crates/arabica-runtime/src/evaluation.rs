//! Read-only evaluation plugins and deterministic behavioral clustering.
//!
//! Plugins receive sanitized canonical evidence. Scores and grouping never
//! grant capabilities or change model routing.

use std::collections::{BTreeMap, BTreeSet};
use std::fmt::{Display, Formatter};

use arabica_protocol::{Event, EventEnvelope, RunId};
use serde::{Deserialize, Serialize};

use crate::{
    BlendEvaluationReport, BlendRoutingPolicy, ContextReport, context_report,
    evaluate_blend_history,
};

const OBSERVATIONAL: &str = "observational";
const BEHAVIOR_CLUSTERS: &str = "behavior-clusters";

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(default, deny_unknown_fields)]
pub struct EvaluationConfig {
    pub context_strategy: String,
    pub model_strategy: String,
    pub max_clusters: usize,
    pub min_cluster_runs: usize,
}

impl Default for EvaluationConfig {
    fn default() -> Self {
        Self {
            context_strategy: OBSERVATIONAL.into(),
            model_strategy: OBSERVATIONAL.into(),
            max_clusters: 3,
            min_cluster_runs: 12,
        }
    }
}

impl EvaluationConfig {
    pub fn validate(&self) -> Result<(), EvaluationError> {
        if !(1..=16).contains(&self.max_clusters) || self.min_cluster_runs < self.max_clusters {
            return Err(EvaluationError(
                "invalid evaluation clustering bounds".into(),
            ));
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct EvaluationError(pub String);
impl Display for EvaluationError {
    fn fmt(&self, f: &mut Formatter<'_>) -> std::fmt::Result {
        self.0.fmt(f)
    }
}
impl std::error::Error for EvaluationError {}

/// No prompts, arguments, provider envelopes, results, or failure messages.
/// Run IDs are namespaced by workspace/session to avoid cross-session joins.
#[derive(Clone, Debug, Serialize)]
pub struct EvaluationEvidence {
    events: Vec<EventEnvelope>,
    pub schema_version: u64,
    pub fingerprint: String,
}

impl EvaluationEvidence {
    pub fn from_history(history: &[EventEnvelope]) -> Self {
        let events: Vec<_> = history
            .iter()
            .filter_map(|envelope| {
                let run_id = envelope.run_id.as_ref()?;
                let event = match &envelope.event {
                    Event::ContextRunResolved { .. }
                    | Event::ContextRequestExposed { .. }
                    | Event::ContextItemUnfolded { .. }
                    | Event::ContextCallStarted { .. }
                    | Event::ContextCallRejected { .. }
                    | Event::ToolCallClassified { .. }
                    | Event::ToolCallPermissionResolved { .. }
                    | Event::ToolCallPermissionRequested { .. }
                    | Event::ToolCallReused { .. }
                    | Event::ModelCallObserved { .. }
                    | Event::RunScheduled
                    | Event::RunStarted
                    | Event::RunCancelled => envelope.event.clone(),
                    Event::ToolCallRequested { call_id, name, .. } => Event::ToolCallRequested {
                        call_id: call_id.clone(),
                        name: name.clone(),
                        arguments: serde_json::json!({}),
                        provider_state: None,
                    },
                    Event::ToolCallCompleted {
                        call_id,
                        name,
                        is_error,
                        ..
                    } => Event::ToolCallCompleted {
                        call_id: call_id.clone(),
                        name: name.clone(),
                        result: String::new(),
                        is_error: *is_error,
                    },
                    Event::ModelRouteSelected {
                        model_step,
                        decision_id,
                        policy_id,
                        policy_version,
                        policy_fingerprint,
                        model_registry_snapshot,
                        model_alias,
                        ..
                    } => Event::ModelRouteSelected {
                        model_step: *model_step,
                        decision_id: decision_id.clone(),
                        policy_id: policy_id.clone(),
                        policy_version: *policy_version,
                        policy_fingerprint: policy_fingerprint.clone(),
                        model_registry_snapshot: model_registry_snapshot.clone(),
                        model_alias: model_alias.clone(),
                        reason: String::new(),
                    },
                    Event::RunCompleted { .. } => Event::RunCompleted { output: None },
                    Event::RunFailed { .. } => Event::RunFailed {
                        message: String::new(),
                    },
                    _ => return None,
                };
                let mut sanitized = envelope.clone();
                sanitized.event = event;
                sanitized.run_id = Some(RunId::new(
                    serde_json::to_string(&(&envelope.workspace_id, &envelope.session_id, run_id))
                        .expect("run identity"),
                ));
                Some(sanitized)
            })
            .collect();
        let fingerprint = crate::context::fingerprint(&(1_u64, &events));
        Self {
            events,
            schema_version: 1,
            fingerprint,
        }
    }

    pub fn events(&self) -> &[EventEnvelope] {
        &self.events
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct EvaluationPluginMetadata {
    pub id: String,
    pub version: u64,
    pub configuration_fingerprint: String,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum EvaluationDiagnostic {
    InsufficientSamples { found: usize, required: usize },
    MissingBehaviorEvidence { runs: usize },
    MissingOrChangingModelRegistry { runs: usize },
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct ContextEvaluationGroup {
    pub group_id: String,
    pub sample_runs: usize,
    pub report: ContextReport,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct ModelEvaluationGroup {
    pub group_id: String,
    pub sample_runs: usize,
    /// Separate model aliases when their registry or effective policy changes.
    pub routing_policy_fingerprint: String,
    pub model_registry_fingerprint: String,
    pub report: BlendEvaluationReport,
}

#[derive(Clone, Debug, Default, Deserialize, PartialEq, Serialize)]
pub struct ContextEvaluationData {
    pub groups: Vec<ContextEvaluationGroup>,
    pub diagnostics: Vec<EvaluationDiagnostic>,
}
#[derive(Clone, Debug, Default, Deserialize, PartialEq, Serialize)]
pub struct ModelEvaluationData {
    pub groups: Vec<ModelEvaluationGroup>,
    pub diagnostics: Vec<EvaluationDiagnostic>,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct ContextEvaluationResult {
    pub plugin: EvaluationPluginMetadata,
    pub evidence_fingerprint: String,
    pub evidence_schema_version: u64,
    pub data: ContextEvaluationData,
}
#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct ModelEvaluationResult {
    pub plugin: EvaluationPluginMetadata,
    pub evidence_fingerprint: String,
    pub evidence_schema_version: u64,
    pub data: ModelEvaluationData,
}

pub trait ContextEvaluationStrategy: std::fmt::Debug + Send + Sync {
    fn metadata(&self) -> EvaluationPluginMetadata;
    fn evaluate(
        &self,
        evidence: &EvaluationEvidence,
    ) -> Result<ContextEvaluationData, EvaluationError>;
}
pub trait ModelEvaluationStrategy: std::fmt::Debug + Send + Sync {
    fn metadata(&self) -> EvaluationPluginMetadata;
    fn evaluate(
        &self,
        evidence: &EvaluationEvidence,
    ) -> Result<ModelEvaluationData, EvaluationError>;
}

#[derive(Debug, Default)]
pub struct EvaluationRegistry {
    context: BTreeMap<String, Box<dyn ContextEvaluationStrategy>>,
    models: BTreeMap<String, Box<dyn ModelEvaluationStrategy>>,
}

impl EvaluationRegistry {
    pub fn builtins(config: &EvaluationConfig) -> Result<Self, EvaluationError> {
        config.validate()?;
        let mut registry = Self::default();
        for clustered in [false, true] {
            registry.register_context(Box::new(BuiltinEvaluator {
                config: config.clone(),
                clustered,
            }))?;
            registry.register_model(Box::new(BuiltinEvaluator {
                config: config.clone(),
                clustered,
            }))?;
        }
        Ok(registry)
    }
    pub fn register_context(
        &mut self,
        strategy: Box<dyn ContextEvaluationStrategy>,
    ) -> Result<(), EvaluationError> {
        let metadata = strategy.metadata();
        validate_metadata(&metadata)?;
        if self.context.contains_key(&metadata.id) {
            return Err(EvaluationError(
                "duplicate context evaluation strategy".into(),
            ));
        }
        self.context.insert(metadata.id, strategy);
        Ok(())
    }
    pub fn register_model(
        &mut self,
        strategy: Box<dyn ModelEvaluationStrategy>,
    ) -> Result<(), EvaluationError> {
        let metadata = strategy.metadata();
        validate_metadata(&metadata)?;
        if self.models.contains_key(&metadata.id) {
            return Err(EvaluationError(
                "duplicate model evaluation strategy".into(),
            ));
        }
        self.models.insert(metadata.id, strategy);
        Ok(())
    }
    pub fn context(
        &self,
        id: &str,
        evidence: &EvaluationEvidence,
    ) -> Result<ContextEvaluationResult, EvaluationError> {
        let strategy = self
            .context
            .get(id)
            .ok_or_else(|| EvaluationError("unknown context evaluation strategy".into()))?;
        Ok(ContextEvaluationResult {
            plugin: strategy.metadata(),
            evidence_fingerprint: evidence.fingerprint.clone(),
            evidence_schema_version: evidence.schema_version,
            data: strategy.evaluate(evidence)?,
        })
    }
    pub fn model(
        &self,
        id: &str,
        evidence: &EvaluationEvidence,
    ) -> Result<ModelEvaluationResult, EvaluationError> {
        let strategy = self
            .models
            .get(id)
            .ok_or_else(|| EvaluationError("unknown model evaluation strategy".into()))?;
        Ok(ModelEvaluationResult {
            plugin: strategy.metadata(),
            evidence_fingerprint: evidence.fingerprint.clone(),
            evidence_schema_version: evidence.schema_version,
            data: strategy.evaluate(evidence)?,
        })
    }
}

fn validate_metadata(metadata: &EvaluationPluginMetadata) -> Result<(), EvaluationError> {
    if metadata.id.trim().is_empty()
        || metadata.version == 0
        || metadata.configuration_fingerprint.is_empty()
    {
        return Err(EvaluationError(
            "invalid evaluation strategy metadata".into(),
        ));
    }
    Ok(())
}

#[derive(Debug)]
struct BuiltinEvaluator {
    config: EvaluationConfig,
    clustered: bool,
}
impl BuiltinEvaluator {
    fn metadata(&self) -> EvaluationPluginMetadata {
        let id = if self.clustered {
            BEHAVIOR_CLUSTERS
        } else {
            OBSERVATIONAL
        };
        EvaluationPluginMetadata {
            id: id.into(),
            version: 1,
            configuration_fingerprint: crate::context::fingerprint(&(
                id,
                self.clustered
                    .then_some((self.config.max_clusters, self.config.min_cluster_runs)),
            )),
        }
    }
}

type EventGroups = BTreeMap<String, Vec<EventEnvelope>>;

impl ContextEvaluationStrategy for BuiltinEvaluator {
    fn metadata(&self) -> EvaluationPluginMetadata {
        self.metadata()
    }
    fn evaluate(
        &self,
        evidence: &EvaluationEvidence,
    ) -> Result<ContextEvaluationData, EvaluationError> {
        let (groups, diagnostics) = group_evidence(evidence, self.clustered, &self.config);
        Ok(ContextEvaluationData {
            groups: groups
                .into_iter()
                .map(|(group_id, events)| ContextEvaluationGroup {
                    group_id,
                    sample_runs: run_count(&events),
                    report: context_report(&events),
                })
                .collect(),
            diagnostics,
        })
    }
}
impl ModelEvaluationStrategy for BuiltinEvaluator {
    fn metadata(&self) -> EvaluationPluginMetadata {
        self.metadata()
    }
    fn evaluate(
        &self,
        evidence: &EvaluationEvidence,
    ) -> Result<ModelEvaluationData, EvaluationError> {
        let (groups, mut diagnostics) = group_evidence(evidence, self.clustered, &self.config);
        let mut results = Vec::new();
        let mut excluded = 0;
        for (group_id, events) in groups {
            let mut cohorts: BTreeMap<(String, String), Vec<EventEnvelope>> = BTreeMap::new();
            for run in split_runs(&events).into_values() {
                let registries: BTreeSet<_> = run
                    .iter()
                    .filter_map(|envelope| match &envelope.event {
                        Event::ModelRouteSelected {
                            policy_fingerprint,
                            model_registry_snapshot,
                            ..
                        } => Some((
                            policy_fingerprint.clone(),
                            crate::context::fingerprint(model_registry_snapshot),
                        )),
                        _ => None,
                    })
                    .collect();
                let incomplete = run.iter().any(|event| {
                    matches!(&event.event,
                    Event::ModelRouteSelected { policy_fingerprint, model_registry_snapshot, .. }
                    if policy_fingerprint.is_empty() || model_registry_snapshot.is_empty())
                });
                if incomplete || registries.len() != 1 {
                    excluded += 1;
                    continue;
                }
                let key = registries.into_iter().next().expect("one registry");
                cohorts.entry(key).or_default().extend(run);
            }
            for ((routing_policy_fingerprint, model_registry_fingerprint), events) in cohorts {
                results.push(ModelEvaluationGroup {
                    group_id: group_id.clone(),
                    sample_runs: run_count(&events),
                    routing_policy_fingerprint,
                    model_registry_fingerprint,
                    report: evaluate_blend_history(&events),
                });
            }
        }
        if excluded > 0 {
            diagnostics
                .push(EvaluationDiagnostic::MissingOrChangingModelRegistry { runs: excluded });
        }
        Ok(ModelEvaluationData {
            groups: results,
            diagnostics,
        })
    }
}

fn split_runs(events: &[EventEnvelope]) -> BTreeMap<RunId, Vec<EventEnvelope>> {
    let mut runs = BTreeMap::<RunId, Vec<EventEnvelope>>::new();
    for envelope in events {
        if let Some(run) = &envelope.run_id {
            runs.entry(run.clone()).or_default().push(envelope.clone());
        }
    }
    runs
}
fn run_count(events: &[EventEnvelope]) -> usize {
    split_runs(events).len()
}

fn group_evidence(
    evidence: &EvaluationEvidence,
    clustered: bool,
    config: &EvaluationConfig,
) -> (EventGroups, Vec<EvaluationDiagnostic>) {
    if evidence.events.is_empty() {
        return (BTreeMap::new(), Vec::new());
    }
    if !clustered {
        return (
            BTreeMap::from([("all".into(), evidence.events.clone())]),
            Vec::new(),
        );
    }
    let runs = split_runs(&evidence.events);
    let mut points = Vec::new();
    let mut usable = Vec::new();
    let mut missing = Vec::new();
    for events in runs.into_values() {
        let mut calls = BTreeSet::new();
        let mut tools = BTreeSet::new();
        let mut unfolds = BTreeSet::new();
        for envelope in &events {
            match &envelope.event {
                Event::ModelRouteSelected { model_step, .. } => {
                    calls.insert(*model_step);
                }
                Event::ToolCallRequested { call_id, name, .. }
                    if !matches!(
                        name.as_str(),
                        "context_unfold" | "context_read" | "runtime_complete"
                    ) =>
                {
                    tools.insert(call_id);
                }
                Event::ContextItemUnfolded { context_id, .. } => {
                    unfolds.insert(context_id);
                }
                _ => {}
            }
        }
        if calls.is_empty() {
            missing.extend(events);
            continue;
        }
        points.push([
            (calls.len() as f64).ln_1p(),
            (tools.len() as f64).ln_1p(),
            (unfolds.len() as f64).ln_1p(),
        ]);
        usable.push(events);
    }
    let mut groups = BTreeMap::new();
    let mut diagnostics = Vec::new();
    if !missing.is_empty() {
        diagnostics.push(EvaluationDiagnostic::MissingBehaviorEvidence {
            runs: run_count(&missing),
        });
        groups.insert("unknown-behavior".into(), missing);
    }
    if usable.len() < config.min_cluster_runs {
        diagnostics.push(EvaluationDiagnostic::InsufficientSamples {
            found: usable.len(),
            required: config.min_cluster_runs,
        });
        let events: Vec<_> = usable.into_iter().flatten().collect();
        if !events.is_empty() {
            groups.insert("unclustered".into(), events);
        }
        return (groups, diagnostics);
    }
    standardize(&mut points);
    let assignments = cluster(&points, config.max_clusters);
    for (events, assignment) in usable.into_iter().zip(assignments) {
        groups
            .entry(format!("cluster-{assignment}"))
            .or_default()
            .extend(events);
    }
    (groups, diagnostics)
}

fn standardize(points: &mut [[f64; 3]]) {
    for dimension in 0..3 {
        let mean = points.iter().map(|point| point[dimension]).sum::<f64>() / points.len() as f64;
        let deviation = (points
            .iter()
            .map(|point| (point[dimension] - mean).powi(2))
            .sum::<f64>()
            / points.len() as f64)
            .sqrt();
        for point in &mut *points {
            point[dimension] = if deviation > f64::EPSILON {
                (point[dimension] - mean) / deviation
            } else {
                0.0
            };
        }
    }
}
fn distance(a: &[f64; 3], b: &[f64; 3]) -> f64 {
    a.iter().zip(b).map(|(a, b)| (a - b).powi(2)).sum()
}

/// Stable run order, farthest-point seeds, deterministic ties and bounded Lloyd
/// iterations. No outcome, latency, model identity, or prompt enters features.
fn cluster(points: &[[f64; 3]], max_clusters: usize) -> Vec<usize> {
    let mut centers = vec![points[0]];
    while centers.len() < max_clusters.min(points.len()) {
        let mut farthest = (0, 0.0);
        for (index, point) in points.iter().enumerate() {
            let nearest = centers
                .iter()
                .map(|center| distance(point, center))
                .fold(f64::INFINITY, f64::min);
            if nearest > farthest.1 {
                farthest = (index, nearest);
            }
        }
        if farthest.1 <= f64::EPSILON {
            break;
        }
        centers.push(points[farthest.0]);
    }
    let mut assignments = vec![usize::MAX; points.len()];
    for _ in 0..32 {
        let next: Vec<_> = points
            .iter()
            .map(|point| {
                centers
                    .iter()
                    .enumerate()
                    .min_by(|(_, a), (_, b)| distance(point, a).total_cmp(&distance(point, b)))
                    .expect("non-empty centers")
                    .0
            })
            .collect();
        if next == assignments {
            break;
        }
        assignments = next;
        let mut sums = vec![[0.0; 3]; centers.len()];
        let mut counts = vec![0; centers.len()];
        for (point, &assignment) in points.iter().zip(&assignments) {
            counts[assignment] += 1;
            for (sum, value) in sums[assignment].iter_mut().zip(point) {
                *sum += value;
            }
        }
        for index in 0..centers.len() {
            if counts[index] > 0 {
                centers[index] = sums[index].map(|sum| sum / counts[index] as f64);
            }
        }
    }
    assignments
}

/// A policy version candidate or active definition generated by background or offline evaluation.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct GeneratedPolicyRecord {
    pub policy_id: String,
    pub version: u64,
    pub status: String,
    pub policy: BlendRoutingPolicy,
    pub basis_fingerprint: Option<String>,
    pub reason: String,
}

/// Propose an evolved routing policy candidate given historical evaluation evidence and reports.
/// This derives an updated candidate version without mutating currently active or running session policies.
pub fn evolve_blend_policy(
    base: &BlendRoutingPolicy,
    report: &BlendEvaluationReport,
    basis_fingerprint: Option<String>,
) -> Option<GeneratedPolicyRecord> {
    let total_tool_errors: u64 = report
        .models
        .values()
        .map(|m| m.downstream_tool_errors)
        .sum();
    let total_runs_failed: u64 = report.policies.values().map(|p| p.runs_failed).sum();

    if total_tool_errors > 0 || total_runs_failed > 0 {
        let mut evolved = base.clone();
        evolved.version = base.version.saturating_add(1);

        let mut reason_parts = Vec::new();
        if evolved.after_tool_error.is_none() {
            let candidate = evolved
                .recovery_model
                .clone()
                .unwrap_or_else(|| evolved.default_model.clone());
            evolved.after_tool_error = Some(candidate);
            reason_parts.push(format!(
                "configured after_tool_error fallback after {} downstream tool error(s)",
                total_tool_errors
            ));
        }
        if total_runs_failed > 0 && evolved.recovery_after_no_progress_steps > 1 {
            evolved.recovery_after_no_progress_steps = 1;
            reason_parts.push(
                "tightened recovery_after_no_progress_steps to 1 due to failed run(s)".to_string(),
            );
        }
        if reason_parts.is_empty() {
            reason_parts.push(format!(
                "policy increment to version {} reflecting evaluated error trends",
                evolved.version
            ));
        }

        Some(GeneratedPolicyRecord {
            policy_id: evolved.policy_id.clone(),
            version: evolved.version,
            status: "candidate".to_string(),
            policy: evolved,
            basis_fingerprint,
            reason: reason_parts.join("; "),
        })
    } else {
        None
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use arabica_protocol::{CommandId, EventId, EventMetadata, SessionId, WorkspaceId};

    fn event(session: &str, run: &str, sequence: u64, event: Event) -> EventEnvelope {
        EventEnvelope::new(
            EventMetadata {
                event_id: EventId::new(format!("event-{sequence}")),
                command_id: CommandId::new("command"),
                workspace_id: WorkspaceId::new("workspace"),
                session_id: SessionId::new(session),
                run_id: Some(RunId::new(run)),
                sequence,
                occurred_at_ms: sequence,
            },
            event,
        )
    }
    fn route(step: usize, registry: &str, policy: &str) -> Event {
        Event::ModelRouteSelected {
            model_step: step,
            decision_id: format!("decision-{step}"),
            policy_id: "policy".into(),
            policy_version: 1,
            policy_fingerprint: policy.into(),
            model_registry_snapshot: registry.into(),
            model_alias: Some("default".into()),
            reason: "SECRET".into(),
        }
    }
    fn behavior(run: &str, steps: usize) -> Vec<EventEnvelope> {
        (0..steps)
            .map(|step| {
                event(
                    "session",
                    run,
                    step as u64,
                    route(step, "registry", "policy"),
                )
            })
            .collect()
    }
    #[test]
    fn evidence_removes_payloads_and_namespaces_runs() {
        let events = vec![
            event("a", "same", 1, route(0, "registry", "policy")),
            event(
                "b",
                "same",
                2,
                Event::ToolCallRequested {
                    call_id: "call".into(),
                    name: "tool".into(),
                    arguments: serde_json::json!({"value":"SECRET"}),
                    provider_state: None,
                },
            ),
            event(
                "b",
                "same",
                3,
                Event::ToolCallCompleted {
                    call_id: "call".into(),
                    name: "tool".into(),
                    result: "SECRET".into(),
                    is_error: false,
                },
            ),
            event(
                "a",
                "same",
                4,
                Event::RunFailed {
                    message: "SECRET".into(),
                },
            ),
        ];
        let evidence = EvaluationEvidence::from_history(&events);
        assert!(!serde_json::to_string(&evidence).unwrap().contains("SECRET"));
        assert_eq!(run_count(evidence.events()), 2);
        assert_eq!(
            evidence.fingerprint,
            EvaluationEvidence::from_history(&events).fingerprint
        );
    }
    #[test]
    fn clustering_is_deterministic_and_does_not_use_outcomes() {
        let mut events = Vec::new();
        for (run, steps) in [("a", 1), ("b", 1), ("c", 9), ("d", 9)] {
            events.extend(behavior(run, steps));
        }
        let config = EvaluationConfig {
            max_clusters: 2,
            min_cluster_runs: 4,
            ..EvaluationConfig::default()
        };
        let (groups, diagnostics) =
            group_evidence(&EvaluationEvidence::from_history(&events), true, &config);
        assert!(diagnostics.is_empty());
        assert_eq!(groups.len(), 2);
        assert!(groups.values().all(|events| run_count(events) == 2));
        let memberships = |groups: EventGroups| {
            groups
                .into_iter()
                .map(|(id, events)| (id, split_runs(&events).into_keys().collect::<Vec<_>>()))
                .collect::<BTreeMap<_, _>>()
        };
        let expected = memberships(groups);
        events.reverse();
        events.push(event(
            "session",
            "a",
            99,
            Event::RunFailed {
                message: "failure".into(),
            },
        ));
        let (groups, _) = group_evidence(&EvaluationEvidence::from_history(&events), true, &config);
        assert_eq!(expected, memberships(groups));
    }
    #[test]
    fn sparse_samples_and_identical_features_do_not_invent_clusters() {
        let config = EvaluationConfig {
            max_clusters: 2,
            min_cluster_runs: 2,
            ..EvaluationConfig::default()
        };
        let evidence = EvaluationEvidence::from_history(&behavior("a", 1));
        let (groups, diagnostics) = group_evidence(&evidence, true, &config);
        assert!(groups.contains_key("unclustered"));
        assert_eq!(
            diagnostics,
            vec![EvaluationDiagnostic::InsufficientSamples {
                found: 1,
                required: 2
            }]
        );
        assert_eq!(cluster(&[[0.0; 3]; 4], 3), vec![0; 4]);
    }
    #[test]
    fn model_aliases_are_separated_by_registry_and_policy() {
        let events = vec![
            event("session", "a", 1, route(0, "registry-a", "policy-a")),
            event("session", "b", 2, route(0, "registry-b", "policy-a")),
            event("session", "c", 3, route(0, "registry-a", "policy-b")),
            event("session", "legacy", 4, route(0, "", "")),
            event("session", "changed", 5, route(0, "registry-a", "policy-a")),
            event("session", "changed", 6, route(1, "registry-b", "policy-a")),
        ];
        let registry = EvaluationRegistry::builtins(&EvaluationConfig::default()).unwrap();
        let result = registry
            .model(OBSERVATIONAL, &EvaluationEvidence::from_history(&events))
            .unwrap();
        assert_eq!(result.data.groups.len(), 3);
        assert!(
            result.data.groups.iter().all(|group| group.sample_runs == 1
                && group.report.models["default"].selected_calls == 1)
        );
        assert_eq!(
            result.data.diagnostics,
            vec![EvaluationDiagnostic::MissingOrChangingModelRegistry { runs: 2 }]
        );
    }
    #[derive(Debug)]
    struct CustomStrategy;
    impl ContextEvaluationStrategy for CustomStrategy {
        fn metadata(&self) -> EvaluationPluginMetadata {
            EvaluationPluginMetadata {
                id: "custom".into(),
                version: 2,
                configuration_fingerprint: "configuration".into(),
            }
        }
        fn evaluate(
            &self,
            evidence: &EvaluationEvidence,
        ) -> Result<ContextEvaluationData, EvaluationError> {
            Ok(ContextEvaluationData {
                groups: vec![ContextEvaluationGroup {
                    group_id: "custom-group".into(),
                    sample_runs: run_count(evidence.events()),
                    report: context_report(evidence.events()),
                }],
                diagnostics: Vec::new(),
            })
        }
    }
    #[test]
    fn custom_plugins_dispatch_and_duplicate_or_unknown_ids_fail() {
        let mut registry = EvaluationRegistry::default();
        registry.register_context(Box::new(CustomStrategy)).unwrap();
        assert!(registry.register_context(Box::new(CustomStrategy)).is_err());
        let evidence = EvaluationEvidence::from_history(&behavior("a", 1));
        let result = registry.context("custom", &evidence).unwrap();
        assert_eq!(result.plugin.version, 2);
        assert_eq!(result.evidence_fingerprint, evidence.fingerprint);
        assert_eq!(result.data.groups[0].sample_runs, 1);
        assert!(registry.context("missing", &evidence).is_err());
        assert!(registry.model("custom", &evidence).is_err());
    }

    #[test]
    fn policy_evolution_proposes_candidate_version_on_observed_tool_errors() {
        let base = BlendRoutingPolicy {
            policy_id: "test-blend".to_string(),
            version: 1,
            default_model: "fast".to_string(),
            after_tool_success: None,
            after_tool_error: None,
            recovery_model: Some("strong".to_string()),
            planning_model: None,
            tool_routes: vec![],
            recovery_after_no_progress_steps: 3,
            minimum_model_dwell_steps: 1,
            tool_call_capable_models: BTreeSet::new(),
            typed_completion_capable_models: BTreeSet::new(),
        };

        let mut report = BlendEvaluationReport::default();
        // Zero errors -> no evolution warranted
        assert!(evolve_blend_policy(&base, &report, None).is_none());

        // Introduce a tool error in the evaluation report
        report
            .models
            .entry("fast".to_string())
            .or_default()
            .downstream_tool_errors = 2;

        let evolved = evolve_blend_policy(&base, &report, Some("evidence-fp".to_string()))
            .expect("should propose evolved policy");

        assert_eq!(evolved.policy_id, "test-blend");
        assert_eq!(evolved.version, 2);
        assert_eq!(evolved.status, "candidate");
        assert_eq!(evolved.policy.after_tool_error, Some("strong".to_string()));
        assert_eq!(evolved.basis_fingerprint, Some("evidence-fp".to_string()));
        assert!(
            evolved
                .reason
                .contains("configured after_tool_error fallback")
        );
    }
}
