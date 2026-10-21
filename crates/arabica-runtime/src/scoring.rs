//! Versioned scoring contracts and pure calculations for evaluation tasks.
//!
//! Implements weighted criterion scoring, cost/time efficiency penalties,
//! budget enforcement, and explicit missing-data/evaluator-fault classification.

use std::collections::{BTreeSet, HashMap};

use arabica_protocol::{RunId, SessionId};
use serde::{Deserialize, Serialize};

use crate::{
    AcceptanceCriterion, AcceptanceSpec, AcceptanceStatus, ArtifactEvidence, EvaluationError,
    RunAcceptance, RunEvidenceSnapshot,
};

/// Resource budget limits for a task trial.
#[derive(Clone, Copy, Debug, Default, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct TaskResourceBudget {
    /// Maximum allowed financial cost in the evaluation currency.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub max_cost: Option<f64>,
    /// Maximum allowed active agent time in milliseconds.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub max_active_time_ms: Option<u64>,
    /// Maximum allowed model steps / turns.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub max_model_steps: Option<usize>,
}

impl TaskResourceBudget {
    pub fn validate(&self) -> Result<(), EvaluationError> {
        if self
            .max_cost
            .is_some_and(|cost| cost < 0.0 || !cost.is_finite())
        {
            return Err(EvaluationError(
                "max_cost budget must be non-negative and finite".into(),
            ));
        }
        Ok(())
    }
}

/// Versioned task specification with criteria, weights, and resource budgets.
#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct TaskSpec {
    pub task_id: String,
    pub version: u64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub environment_fingerprint: Option<String>,
    pub criteria: Vec<AcceptanceCriterion>,
    #[serde(default)]
    pub budgets: TaskResourceBudget,
}

impl TaskSpec {
    pub fn validate(&self) -> Result<(), EvaluationError> {
        if self.task_id.trim().is_empty() || self.version == 0 || self.criteria.len() > 128 {
            return Err(EvaluationError("invalid task specification".into()));
        }
        self.budgets.validate()?;
        let mut ids = BTreeSet::new();
        let mut total_weight = 0.0;
        for criterion in &self.criteria {
            if criterion.id.trim().is_empty() || !ids.insert(&criterion.id) {
                return Err(EvaluationError(
                    "acceptance criterion IDs must be unique and non-empty".into(),
                ));
            }
            if criterion.weight < 0.0 || !criterion.weight.is_finite() {
                return Err(EvaluationError(
                    "acceptance criterion weight must be non-negative and finite".into(),
                ));
            }
            total_weight += criterion.weight;
            criterion.check.validate()?;
        }
        if !self.criteria.is_empty() && total_weight <= 0.0 {
            return Err(EvaluationError(
                "total criterion weight must be strictly positive".into(),
            ));
        }
        Ok(())
    }

    pub fn fingerprint(&self) -> String {
        crate::context::fingerprint(self)
    }

    pub fn acceptance_spec(&self) -> AcceptanceSpec {
        AcceptanceSpec {
            task_id: self.task_id.clone(),
            version: self.version,
            criteria: self.criteria.clone(),
        }
    }
}

impl From<AcceptanceSpec> for TaskSpec {
    fn from(spec: AcceptanceSpec) -> Self {
        Self {
            task_id: spec.task_id,
            version: spec.version,
            environment_fingerprint: None,
            criteria: spec.criteria,
            budgets: TaskResourceBudget::default(),
        }
    }
}

/// Relative weights for scoring formula components summing to 1.0.
#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ScoringWeights {
    pub quality: f64,
    pub cost: f64,
    pub time: f64,
}

impl Default for ScoringWeights {
    fn default() -> Self {
        Self {
            quality: 0.70,
            cost: 0.20,
            time: 0.10,
        }
    }
}

impl ScoringWeights {
    pub fn validate(&self) -> Result<(), EvaluationError> {
        if self.quality < 0.0
            || !self.quality.is_finite()
            || self.cost < 0.0
            || !self.cost.is_finite()
            || self.time < 0.0
            || !self.time.is_finite()
        {
            return Err(EvaluationError(
                "scoring weights must be non-negative and finite".into(),
            ));
        }
        let sum = self.quality + self.cost + self.time;
        if (sum - 1.0).abs() > 1e-6 {
            return Err(EvaluationError(format!(
                "scoring weights must sum to 1.0 (got {sum:.6})"
            )));
        }
        Ok(())
    }
}

/// Frozen scoring profile with formula version, component weights, and references.
#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ScoringProfile {
    pub formula_version: u64,
    #[serde(default)]
    pub weights: ScoringWeights,
    pub reference_cost: f64,
    pub reference_time_ms: u64,
}

impl ScoringProfile {
    pub fn new(reference_cost: f64, reference_time_ms: u64) -> Result<Self, EvaluationError> {
        let profile = Self {
            formula_version: 1,
            weights: ScoringWeights::default(),
            reference_cost,
            reference_time_ms,
        };
        profile.validate()?;
        Ok(profile)
    }

    pub fn validate(&self) -> Result<(), EvaluationError> {
        if self.formula_version == 0 {
            return Err(EvaluationError("formula_version must be non-zero".into()));
        }
        if self.reference_cost <= 0.0 || !self.reference_cost.is_finite() {
            return Err(EvaluationError(
                "reference_cost must be positive and finite".into(),
            ));
        }
        if self.reference_time_ms == 0 {
            return Err(EvaluationError("reference_time_ms must be non-zero".into()));
        }
        self.weights.validate()?;
        Ok(())
    }

    pub fn fingerprint(&self) -> String {
        crate::context::fingerprint(self)
    }
}

/// Composite evaluation identity tying checker version, task, and scoring profile.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct EvaluationIdentity {
    pub checker_version: u64,
    pub task_fingerprint: String,
    pub scoring_fingerprint: String,
}

impl EvaluationIdentity {
    pub fn new(
        checker_version: u64,
        task_fingerprint: impl Into<String>,
        scoring_fingerprint: impl Into<String>,
    ) -> Self {
        Self {
            checker_version,
            task_fingerprint: task_fingerprint.into(),
            scoring_fingerprint: scoring_fingerprint.into(),
        }
    }

    pub fn fingerprint(&self) -> String {
        crate::context::fingerprint(self)
    }
}

/// Scoring outcome status distinguishing completed, pending, incomplete, and fault states.
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum TaskScoreStatus {
    PendingChecks,
    IncompleteEvidence,
    InvalidEvaluator,
    Completed,
}

/// Outcome of evaluating an individual criterion.
#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct CriterionEvaluation {
    pub criterion_id: String,
    pub status: AcceptanceStatus,
    /// Score in `[0, 1]`. Binary checks return 1.0 or 0.0. Missing/unknown evidence has None.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub score: Option<f64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub evidence_id: Option<String>,
}

impl CriterionEvaluation {
    pub fn from_status(
        criterion_id: impl Into<String>,
        status: AcceptanceStatus,
        evidence_id: Option<String>,
    ) -> Self {
        let score = match status {
            AcceptanceStatus::Pass => Some(1.0),
            AcceptanceStatus::Fail => Some(0.0),
            AcceptanceStatus::Unknown | AcceptanceStatus::NotApplicable => None,
        };
        Self {
            criterion_id: criterion_id.into(),
            status,
            score,
            evidence_id,
        }
    }

    pub fn with_score(
        criterion_id: impl Into<String>,
        status: AcceptanceStatus,
        score: Option<f64>,
        evidence_id: Option<String>,
    ) -> Self {
        Self {
            criterion_id: criterion_id.into(),
            status,
            score,
            evidence_id,
        }
    }
}

/// Observed runtime metrics and evaluator conditions for scoring a task run.
#[derive(Clone, Debug, Default, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ScoringObservation {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub actual_cost: Option<f64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub active_time_ms: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub model_steps: Option<usize>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub evaluator_fault: Option<String>,
    #[serde(default)]
    pub pending: bool,
}

/// Evaluated task score and components.
#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct TaskScore {
    pub status: TaskScoreStatus,
    pub quality: Option<f64>,
    pub cost_efficiency: Option<f64>,
    pub time_efficiency: Option<f64>,
    pub total: Option<f64>,
    pub required_passed: Option<bool>,
    pub budget_exceeded: bool,
    pub evidence_ids: Vec<String>,
    pub evaluation_identity: EvaluationIdentity,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub failure_reason: Option<String>,
}

/// Pure deterministic calculation of a versioned task score.
pub fn calculate_task_score(
    task_spec: &TaskSpec,
    profile: &ScoringProfile,
    checker_version: u64,
    criterion_results: &[CriterionEvaluation],
    observation: &ScoringObservation,
) -> Result<TaskScore, EvaluationError> {
    task_spec.validate()?;
    profile.validate()?;

    if observation
        .actual_cost
        .is_some_and(|cost| cost < 0.0 || !cost.is_finite())
    {
        return Err(EvaluationError(
            "actual_cost must be non-negative and finite".into(),
        ));
    }

    let evaluation_identity = EvaluationIdentity::new(
        checker_version,
        task_spec.fingerprint(),
        profile.fingerprint(),
    );

    // 1. Evaluator infrastructure failure is not a task failure.
    if let Some(fault) = &observation.evaluator_fault {
        return Ok(TaskScore {
            status: TaskScoreStatus::InvalidEvaluator,
            quality: None,
            cost_efficiency: None,
            time_efficiency: None,
            total: None,
            required_passed: None,
            budget_exceeded: false,
            evidence_ids: vec![],
            evaluation_identity,
            failure_reason: Some(format!("evaluator infrastructure fault: {fault}")),
        });
    }

    // 2. Pending checks
    if observation.pending {
        return Ok(TaskScore {
            status: TaskScoreStatus::PendingChecks,
            quality: None,
            cost_efficiency: None,
            time_efficiency: None,
            total: None,
            required_passed: None,
            budget_exceeded: false,
            evidence_ids: vec![],
            evaluation_identity,
            failure_reason: None,
        });
    }

    // 3. Collect evidence IDs
    let mut evidence_ids: Vec<String> = criterion_results
        .iter()
        .filter_map(|r| r.evidence_id.clone())
        .filter(|id| !id.trim().is_empty())
        .collect();
    evidence_ids.sort();
    evidence_ids.dedup();

    // 4. Weight normalization & Quality calculation
    // "Normalize criterion weights to sum to one."
    // "Unknown evidence never passes a check or causes weight renormalization."
    let total_declared_weight: f64 = task_spec.criteria.iter().map(|c| c.weight).sum();
    let norm_factor = if total_declared_weight > 0.0 {
        total_declared_weight
    } else {
        1.0
    };

    let results_by_id: HashMap<&str, &CriterionEvaluation> = criterion_results
        .iter()
        .map(|r| (r.criterion_id.as_str(), r))
        .collect();

    let mut quality_sum = 0.0;
    let mut required_failure = false;
    let mut required_unknown = false;
    let mut missing_criterion_evidence = false;
    let mut failure_reasons = Vec::new();

    for criterion in &task_spec.criteria {
        let norm_weight = criterion.weight / norm_factor;
        match results_by_id.get(criterion.id.as_str()) {
            Some(result) => match result.status {
                AcceptanceStatus::Pass => {
                    let score = result.score.unwrap_or(1.0).clamp(0.0, 1.0);
                    quality_sum += norm_weight * score;
                }
                AcceptanceStatus::Fail => {
                    if criterion.required {
                        required_failure = true;
                        failure_reasons
                            .push(format!("required criterion '{}' failed", criterion.id));
                    }
                }
                AcceptanceStatus::Unknown => {
                    missing_criterion_evidence = true;
                    if criterion.required {
                        required_unknown = true;
                    }
                }
                AcceptanceStatus::NotApplicable => {}
            },
            None => {
                missing_criterion_evidence = true;
                if criterion.required {
                    required_unknown = true;
                }
            }
        }
    }

    // 5. Budget checks
    let mut budget_exceeded = false;
    if task_spec
        .budgets
        .max_cost
        .is_some_and(|max| observation.actual_cost.is_some_and(|actual| actual > max))
    {
        let actual = observation.actual_cost.unwrap();
        let max = task_spec.budgets.max_cost.unwrap();
        budget_exceeded = true;
        failure_reasons.push(format!(
            "declared cost budget exceeded: {actual:.4} > {max:.4}"
        ));
    }
    if task_spec.budgets.max_active_time_ms.is_some_and(|max| {
        observation
            .active_time_ms
            .is_some_and(|actual| actual > max)
    }) {
        let actual = observation.active_time_ms.unwrap();
        let max = task_spec.budgets.max_active_time_ms.unwrap();
        budget_exceeded = true;
        failure_reasons.push(format!(
            "declared active time budget exceeded: {actual}ms > {max}ms"
        ));
    }
    if task_spec
        .budgets
        .max_model_steps
        .is_some_and(|max| observation.model_steps.is_some_and(|actual| actual > max))
    {
        let actual = observation.model_steps.unwrap();
        let max = task_spec.budgets.max_model_steps.unwrap();
        budget_exceeded = true;
        failure_reasons.push(format!(
            "declared model steps budget exceeded: {actual} > {max}"
        ));
    }

    // 6. Efficiency components
    // E_cost = 1 / (1 + actual_cost / reference_cost)
    let cost_efficiency = observation
        .actual_cost
        .map(|cost| 1.0 / (1.0 + cost / profile.reference_cost));
    // E_time = 1 / (1 + active_time / reference_time)
    let time_efficiency = observation
        .active_time_ms
        .map(|time| 1.0 / (1.0 + (time as f64) / (profile.reference_time_ms as f64)));

    let required_passed = if required_failure {
        Some(false)
    } else if required_unknown {
        None
    } else {
        Some(true)
    };

    // 7. Score determination
    // "If any required criterion fails, or the task exceeds its declared budget:
    //     Score = 0"
    // "A known required failure is sufficient for a zero score even if other evidence is missing."
    if required_failure || budget_exceeded {
        return Ok(TaskScore {
            status: TaskScoreStatus::Completed,
            quality: Some(quality_sum),
            cost_efficiency,
            time_efficiency,
            total: Some(0.0),
            required_passed,
            budget_exceeded,
            evidence_ids,
            evaluation_identity,
            failure_reason: Some(failure_reasons.join("; ")),
        });
    }

    // "Otherwise, when required criteria pass and all scoring evidence is available:
    //     Score = 100 * Q * (0.70 + 0.20 * E_cost + 0.10 * E_time)"
    // "Otherwise, missing evidence or accounting prevents a complete score; retain available quality and accounting components."
    let all_evidence_available = !missing_criterion_evidence
        && observation.actual_cost.is_some()
        && observation.active_time_ms.is_some();

    if !all_evidence_available {
        let mut missing_items = Vec::new();
        if missing_criterion_evidence {
            missing_items.push("criterion evidence missing or unknown");
        }
        if observation.actual_cost.is_none() {
            missing_items.push("actual cost accounting missing");
        }
        if observation.active_time_ms.is_none() {
            missing_items.push("active time accounting missing");
        }
        return Ok(TaskScore {
            status: TaskScoreStatus::IncompleteEvidence,
            quality: Some(quality_sum),
            cost_efficiency,
            time_efficiency,
            total: None,
            required_passed,
            budget_exceeded: false,
            evidence_ids,
            evaluation_identity,
            failure_reason: Some(format!(
                "incomplete scoring data: {}",
                missing_items.join("; ")
            )),
        });
    }

    let e_cost = cost_efficiency.expect("cost efficiency verified available");
    let e_time = time_efficiency.expect("time efficiency verified available");

    let efficiency_multiplier =
        profile.weights.quality + profile.weights.cost * e_cost + profile.weights.time * e_time;
    let score = 100.0 * quality_sum * efficiency_multiplier;

    Ok(TaskScore {
        status: TaskScoreStatus::Completed,
        quality: Some(quality_sum),
        cost_efficiency,
        time_efficiency,
        total: Some(score.clamp(0.0, 100.0)),
        required_passed: Some(true),
        budget_exceeded: false,
        evidence_ids,
        evaluation_identity,
        failure_reason: None,
    })
}

/// Score a completed run evaluation against a task specification and profile.
pub fn score_run_evaluation(
    task_spec: &TaskSpec,
    profile: &ScoringProfile,
    checker_version: u64,
    snapshot: &RunEvidenceSnapshot,
    acceptance: Option<&RunAcceptance>,
    actual_cost: Option<f64>,
) -> Result<TaskScore, EvaluationError> {
    let criterion_results = match acceptance {
        Some(acc) => acc
            .results
            .iter()
            .map(|r| CriterionEvaluation {
                criterion_id: r.criterion_id.clone(),
                status: r.status.clone(),
                score: match r.status {
                    AcceptanceStatus::Pass => Some(1.0),
                    AcceptanceStatus::Fail => Some(0.0),
                    _ => None,
                },
                evidence_id: (!r.evidence.is_empty()).then(|| r.evidence.clone()),
            })
            .collect(),
        None => Vec::new(),
    };

    let observation = ScoringObservation {
        actual_cost,
        active_time_ms: snapshot.timing.active_ms,
        model_steps: Some(snapshot.timing.model_calls),
        evaluator_fault: None,
        pending: false,
    };

    calculate_task_score(
        task_spec,
        profile,
        checker_version,
        &criterion_results,
        &observation,
    )
}

fn default_tool_health_weight() -> f64 {
    0.40
}

fn default_require_clean_terminal_tools() -> bool {
    true
}

fn default_gate_timeout_ms() -> u64 {
    30_000
}

fn default_gate_output_limit() -> usize {
    64 * 1024
}

/// Configuration for real live interactive session evaluation.
#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct LiveSessionConfig {
    pub session_id: SessionId,
    #[serde(default)]
    pub budgets: TaskResourceBudget,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub workspace_gate: Option<WorkspaceGateConfig>,
    /// Weight applied to tool health in quality score (0.0 .. 1.0, default 0.40).
    #[serde(default = "default_tool_health_weight")]
    pub tool_health_weight: f64,
    /// If true, a session ending on an unrecovered failed tool call forces zero quality.
    #[serde(default = "default_require_clean_terminal_tools")]
    pub require_clean_terminal_tools: bool,
}

impl LiveSessionConfig {
    pub fn new(session_id: SessionId) -> Self {
        Self {
            session_id,
            budgets: TaskResourceBudget::default(),
            workspace_gate: None,
            tool_health_weight: default_tool_health_weight(),
            require_clean_terminal_tools: default_require_clean_terminal_tools(),
        }
    }

    pub fn with_workspace_gate(mut self, gate: WorkspaceGateConfig) -> Self {
        self.workspace_gate = Some(gate);
        self
    }

    pub fn with_budgets(mut self, budgets: TaskResourceBudget) -> Self {
        self.budgets = budgets;
        self
    }
}

/// Configuration for workspace integrity and quality gates on real projects.
#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct WorkspaceGateConfig {
    /// Command to verify workspace compile/syntax (e.g. "cargo check").
    pub check_command: String,
    /// Optional command to run existing project tests for regressions (e.g. "cargo test").
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub test_command: Option<String>,
    /// Optional whitelist of permitted modified files.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub permitted_paths: Option<Vec<String>>,
    /// Command timeout in milliseconds (default 30_000).
    #[serde(default = "default_gate_timeout_ms")]
    pub timeout_ms: u64,
    /// Bounded stdout/stderr capture in bytes (default 64 KiB).
    #[serde(default = "default_gate_output_limit")]
    pub output_limit_bytes: usize,
}

impl WorkspaceGateConfig {
    pub fn new(check_command: impl Into<String>) -> Self {
        Self {
            check_command: check_command.into(),
            test_command: None,
            permitted_paths: None,
            timeout_ms: default_gate_timeout_ms(),
            output_limit_bytes: default_gate_output_limit(),
        }
    }
}

/// Result of evaluating real workspace integrity gates.
#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct WorkspaceGateResult {
    pub check_passed: bool,
    pub tests_passed: Option<bool>,
    pub permitted_files_passed: Option<bool>,
    pub overall_passed: bool,
    pub failure_reasons: Vec<String>,
}

/// Configuration for historical offline trace rescoring.
#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct HistoricalTraceConfig {
    pub session_id: SessionId,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub run_id: Option<RunId>,
}

/// Unified evaluation mode distinguishing live sessions, workspace gates, preset benchmarks, and historical audits.
#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(tag = "mode", rename_all = "snake_case")]
pub enum EvaluationMode {
    /// Mode 1: Live interactive session evaluated via trajectory health and efficiency.
    LiveSession(LiveSessionConfig),
    /// Mode 2: Real workspace integrity gate verification.
    WorkspaceGate(WorkspaceGateConfig),
    /// Mode 3: Preset benchmark task evaluation with explicit criteria.
    PresetTask(TaskSpec),
    /// Mode 4: Historical session trace offline rescoring and audit.
    HistoricalTrace(HistoricalTraceConfig),
}

/// Execute workspace integrity checks on a real project workspace.
pub fn execute_workspace_gate(
    root: &std::path::Path,
    config: &WorkspaceGateConfig,
    modified_paths: Option<&[String]>,
) -> WorkspaceGateResult {
    let mut failure_reasons = Vec::new();

    // 1. Check command (e.g. cargo check)
    let check_evidence = crate::execute_controlled_test(
        root,
        &config.check_command,
        config.timeout_ms,
        config.output_limit_bytes,
    );
    let check_passed = match &check_evidence {
        ArtifactEvidence::TestResult {
            exit_code,
            passed,
            error,
            ..
        } => {
            if let Some(err) = error {
                failure_reasons.push(format!("workspace check execution failed: {err}"));
                false
            } else if *passed && *exit_code == Some(0) {
                true
            } else {
                failure_reasons.push(format!("workspace check failed: exit code {:?}", exit_code));
                false
            }
        }
        _ => false,
    };

    // 2. Optional regression test command (e.g. cargo test)
    let mut tests_passed = None;
    if let Some(test_cmd) = &config.test_command {
        let test_evidence = crate::execute_controlled_test(
            root,
            test_cmd,
            config.timeout_ms,
            config.output_limit_bytes,
        );
        let passed = match &test_evidence {
            ArtifactEvidence::TestResult {
                exit_code,
                passed,
                error,
                ..
            } => {
                if let Some(err) = error {
                    failure_reasons
                        .push(format!("workspace regression test execution failed: {err}"));
                    false
                } else if *passed && *exit_code == Some(0) {
                    true
                } else {
                    failure_reasons.push(format!(
                        "workspace regression tests failed: exit code {:?}",
                        exit_code
                    ));
                    false
                }
            }
            _ => false,
        };
        tests_passed = Some(passed);
    }

    // 3. Optional permitted paths
    let mut permitted_passed = None;
    if let (Some(allowed), Some(modified)) = (&config.permitted_paths, modified_paths) {
        let allowed_set: std::collections::BTreeSet<&str> =
            allowed.iter().map(|s| s.as_str()).collect();
        let mut violations = Vec::new();
        for path in modified {
            if !allowed_set.contains(path.as_str()) {
                violations.push(path.clone());
            }
        }
        if !violations.is_empty() {
            failure_reasons.push(format!(
                "unpermitted files modified: {}",
                violations.join(", ")
            ));
            permitted_passed = Some(false);
        } else {
            permitted_passed = Some(true);
        }
    }

    let overall_passed =
        check_passed && tests_passed.unwrap_or(true) && permitted_passed.unwrap_or(true);

    WorkspaceGateResult {
        check_passed,
        tests_passed,
        permitted_files_passed: permitted_passed,
        overall_passed,
        failure_reasons,
    }
}

/// Evaluate a real running session from its evidence snapshot and live configuration.
pub fn evaluate_live_session(
    config: &LiveSessionConfig,
    profile: &ScoringProfile,
    checker_version: u64,
    snapshot: &RunEvidenceSnapshot,
    actual_cost: Option<f64>,
    workspace_gate_result: Option<&WorkspaceGateResult>,
) -> Result<TaskScore, EvaluationError> {
    profile.validate()?;
    config.budgets.validate()?;

    if snapshot.session_id != config.session_id {
        return Err(EvaluationError(format!(
            "session_id mismatch: expected '{}', got '{}'",
            config.session_id, snapshot.session_id
        )));
    }

    if actual_cost.is_some_and(|c| c < 0.0 || !c.is_finite()) {
        return Err(EvaluationError(
            "actual_cost must be non-negative and finite".into(),
        ));
    }

    let evaluation_identity = EvaluationIdentity::new(
        checker_version,
        format!("live_session:{}", config.session_id),
        profile.fingerprint(),
    );

    let mut failure_reasons = Vec::new();
    let evidence_ids = vec![snapshot.fingerprint.clone()];

    // 1. Terminal Outcome: Must be "completed"
    let terminal_completed = snapshot.terminal_status == "completed";
    if !terminal_completed {
        failure_reasons.push(format!(
            "session ended with non-completed terminal status: '{}'",
            snapshot.terminal_status
        ));
    }

    // 2. Tool Health Analysis
    let total_tools = snapshot.tools.len();
    let errored_tools = snapshot
        .tools
        .iter()
        .filter(|t| t.is_error == Some(true))
        .count();
    let incomplete_tools = snapshot.timing.incomplete_tool_calls;

    let tool_success_ratio = if total_tools > 0 {
        (total_tools.saturating_sub(errored_tools)) as f64 / total_tools as f64
    } else {
        1.0
    };

    let last_tool_failed = snapshot
        .tools
        .last()
        .and_then(|t| t.is_error)
        .unwrap_or(false);

    let mut tool_fatal_failure = false;
    if config.require_clean_terminal_tools && last_tool_failed {
        tool_fatal_failure = true;
        failure_reasons.push("session ended with an unrecovered tool error".into());
    }
    if incomplete_tools > 0 {
        failure_reasons.push(format!("{incomplete_tools} tool calls remained incomplete"));
    }

    // 3. Workspace Gate Analysis
    let mut gate_passed = true;
    if let Some(gate) = workspace_gate_result
        && !gate.overall_passed
    {
        gate_passed = false;
        failure_reasons.extend(gate.failure_reasons.clone());
    }

    // 4. Budget Checks
    let mut budget_exceeded = false;
    if config
        .budgets
        .max_cost
        .is_some_and(|max| actual_cost.is_some_and(|actual| actual > max))
    {
        let actual = actual_cost.unwrap();
        let max = config.budgets.max_cost.unwrap();
        budget_exceeded = true;
        failure_reasons.push(format!(
            "declared cost budget exceeded: {actual:.4} > {max:.4}"
        ));
    }
    if config
        .budgets
        .max_active_time_ms
        .is_some_and(|max| snapshot.timing.active_ms.is_some_and(|actual| actual > max))
    {
        let actual = snapshot.timing.active_ms.unwrap();
        let max = config.budgets.max_active_time_ms.unwrap();
        budget_exceeded = true;
        failure_reasons.push(format!(
            "declared active time budget exceeded: {actual}ms > {max}ms"
        ));
    }
    if config
        .budgets
        .max_model_steps
        .is_some_and(|max| max < snapshot.timing.model_calls)
    {
        budget_exceeded = true;
        failure_reasons.push(format!(
            "declared model steps budget exceeded: {} > {}",
            snapshot.timing.model_calls,
            config.budgets.max_model_steps.unwrap()
        ));
    }

    // 5. Efficiency Components
    let cost_efficiency = actual_cost.map(|cost| 1.0 / (1.0 + cost / profile.reference_cost));
    let time_efficiency = snapshot
        .timing
        .active_ms
        .map(|time| 1.0 / (1.0 + (time as f64) / (profile.reference_time_ms as f64)));

    // 6. Hard Failures Zero Score
    if !terminal_completed || tool_fatal_failure || !gate_passed || budget_exceeded {
        return Ok(TaskScore {
            status: TaskScoreStatus::Completed,
            quality: Some(0.0),
            cost_efficiency,
            time_efficiency,
            total: Some(0.0),
            required_passed: Some(false),
            budget_exceeded,
            evidence_ids,
            evaluation_identity,
            failure_reason: Some(failure_reasons.join("; ")),
        });
    }

    // 7. Quality Calculation for successful session:
    // Base completion = 1.0
    // Tool health penalty applied according to tool_health_weight
    let quality = 1.0 - (config.tool_health_weight * (1.0 - tool_success_ratio));
    let quality = quality.clamp(0.0, 1.0);

    // 8. Completeness of Accounting Data
    let all_evidence_available = actual_cost.is_some() && snapshot.timing.active_ms.is_some();

    if !all_evidence_available {
        let mut missing = Vec::new();
        if actual_cost.is_none() {
            missing.push("actual cost accounting missing");
        }
        if snapshot.timing.active_ms.is_none() {
            missing.push("active time accounting missing");
        }
        return Ok(TaskScore {
            status: TaskScoreStatus::IncompleteEvidence,
            quality: Some(quality),
            cost_efficiency,
            time_efficiency,
            total: None,
            required_passed: Some(true),
            budget_exceeded: false,
            evidence_ids,
            evaluation_identity,
            failure_reason: Some(format!("incomplete scoring data: {}", missing.join("; "))),
        });
    }

    let e_cost = cost_efficiency.expect("cost efficiency available");
    let e_time = time_efficiency.expect("time efficiency available");

    let efficiency_multiplier =
        profile.weights.quality + profile.weights.cost * e_cost + profile.weights.time * e_time;
    let score = 100.0 * quality * efficiency_multiplier;

    Ok(TaskScore {
        status: TaskScoreStatus::Completed,
        quality: Some(quality),
        cost_efficiency,
        time_efficiency,
        total: Some(score.clamp(0.0, 100.0)),
        required_passed: Some(true),
        budget_exceeded: false,
        evidence_ids,
        evaluation_identity,
        failure_reason: None,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::AcceptanceCheck;

    fn sample_spec() -> TaskSpec {
        TaskSpec {
            task_id: "test_task".into(),
            version: 1,
            environment_fingerprint: Some("env_123".into()),
            criteria: vec![
                AcceptanceCriterion {
                    id: "c1".into(),
                    check: AcceptanceCheck::OutputContains {
                        value: "pass".into(),
                    },
                    weight: 2.0,
                    required: true,
                },
                AcceptanceCriterion {
                    id: "c2".into(),
                    check: AcceptanceCheck::OutputContains {
                        value: "optional".into(),
                    },
                    weight: 2.0,
                    required: false,
                },
            ],
            budgets: TaskResourceBudget {
                max_cost: Some(1.0),
                max_active_time_ms: Some(10_000),
                max_model_steps: Some(5),
            },
        }
    }

    fn sample_profile() -> ScoringProfile {
        ScoringProfile::new(0.50, 2_000).expect("valid profile")
    }

    #[test]
    fn perfect_score_calculation() {
        let spec = sample_spec();
        let profile = sample_profile();

        let results = vec![
            CriterionEvaluation::from_status("c1", AcceptanceStatus::Pass, Some("ev1".into())),
            CriterionEvaluation::from_status("c2", AcceptanceStatus::Pass, Some("ev2".into())),
        ];
        // Zero cost and zero time -> E_cost = 1.0, E_time = 1.0
        let obs = ScoringObservation {
            actual_cost: Some(0.0),
            active_time_ms: Some(0),
            model_steps: Some(1),
            evaluator_fault: None,
            pending: false,
        };

        let score = calculate_task_score(&spec, &profile, 1, &results, &obs).unwrap();
        assert_eq!(score.status, TaskScoreStatus::Completed);
        assert_eq!(score.quality, Some(1.0));
        assert_eq!(score.cost_efficiency, Some(1.0));
        assert_eq!(score.time_efficiency, Some(1.0));
        assert!((score.total.unwrap() - 100.0).abs() < 1e-9);
        assert_eq!(score.required_passed, Some(true));
        assert!(!score.budget_exceeded);
        assert_eq!(score.evidence_ids, vec!["ev1", "ev2"]);
    }

    #[test]
    fn reference_baseline_calculation() {
        let spec = sample_spec();
        let profile = sample_profile();

        let results = vec![
            CriterionEvaluation::from_status("c1", AcceptanceStatus::Pass, Some("ev1".into())),
            CriterionEvaluation::from_status("c2", AcceptanceStatus::Pass, Some("ev2".into())),
        ];
        // actual == reference: E_cost = 0.5, E_time = 0.5
        // Multiplier = 0.70 + 0.20 * 0.5 + 0.10 * 0.5 = 0.85
        // Score = 100 * 1.0 * 0.85 = 85.0
        let obs = ScoringObservation {
            actual_cost: Some(0.50),
            active_time_ms: Some(2_000),
            model_steps: Some(2),
            evaluator_fault: None,
            pending: false,
        };

        let score = calculate_task_score(&spec, &profile, 1, &results, &obs).unwrap();
        assert_eq!(score.status, TaskScoreStatus::Completed);
        assert_eq!(score.quality, Some(1.0));
        assert_eq!(score.cost_efficiency, Some(0.5));
        assert_eq!(score.time_efficiency, Some(0.5));
        assert!((score.total.unwrap() - 85.0).abs() < 1e-9);
    }

    #[test]
    fn required_criterion_failure_forces_zero_score() {
        let spec = sample_spec();
        let profile = sample_profile();

        let results = vec![
            CriterionEvaluation::from_status("c1", AcceptanceStatus::Fail, Some("ev1".into())),
            CriterionEvaluation::from_status("c2", AcceptanceStatus::Pass, Some("ev2".into())),
        ];
        let obs = ScoringObservation {
            actual_cost: Some(0.10),
            active_time_ms: Some(500),
            model_steps: Some(1),
            evaluator_fault: None,
            pending: false,
        };

        let score = calculate_task_score(&spec, &profile, 1, &results, &obs).unwrap();
        assert_eq!(score.status, TaskScoreStatus::Completed);
        assert_eq!(score.total, Some(0.0));
        assert_eq!(score.required_passed, Some(false));
        assert!(
            score
                .failure_reason
                .unwrap()
                .contains("required criterion 'c1' failed")
        );
    }

    #[test]
    fn required_failure_sufficient_even_if_other_evidence_missing() {
        let spec = sample_spec();
        let profile = sample_profile();

        let results = vec![
            CriterionEvaluation::from_status("c1", AcceptanceStatus::Fail, None),
            CriterionEvaluation::from_status("c2", AcceptanceStatus::Unknown, None),
        ];
        // Cost and time missing
        let obs = ScoringObservation {
            actual_cost: None,
            active_time_ms: None,
            model_steps: None,
            evaluator_fault: None,
            pending: false,
        };

        let score = calculate_task_score(&spec, &profile, 1, &results, &obs).unwrap();
        assert_eq!(score.status, TaskScoreStatus::Completed);
        assert_eq!(score.total, Some(0.0));
        assert_eq!(score.required_passed, Some(false));
    }

    #[test]
    fn optional_criterion_failure_reduces_quality_proportionally() {
        let spec = sample_spec();
        let profile = sample_profile();

        // c1 (weight 2, required) passes, c2 (weight 2, optional) fails
        // Normalized weights are 0.5 each. Q = 0.5 * 1.0 + 0.5 * 0.0 = 0.5
        let results = vec![
            CriterionEvaluation::from_status("c1", AcceptanceStatus::Pass, None),
            CriterionEvaluation::from_status("c2", AcceptanceStatus::Fail, None),
        ];
        let obs = ScoringObservation {
            actual_cost: Some(0.0),
            active_time_ms: Some(0),
            model_steps: Some(1),
            evaluator_fault: None,
            pending: false,
        };

        let score = calculate_task_score(&spec, &profile, 1, &results, &obs).unwrap();
        assert_eq!(score.status, TaskScoreStatus::Completed);
        assert_eq!(score.quality, Some(0.5));
        assert!((score.total.unwrap() - 50.0).abs() < 1e-9);
        assert_eq!(score.required_passed, Some(true));
    }

    #[test]
    fn declared_cost_budget_exceeded_forces_zero_score() {
        let spec = sample_spec();
        let profile = sample_profile();

        let results = vec![
            CriterionEvaluation::from_status("c1", AcceptanceStatus::Pass, None),
            CriterionEvaluation::from_status("c2", AcceptanceStatus::Pass, None),
        ];
        // max_cost is 1.0, actual is 1.5
        let obs = ScoringObservation {
            actual_cost: Some(1.5),
            active_time_ms: Some(500),
            model_steps: Some(2),
            evaluator_fault: None,
            pending: false,
        };

        let score = calculate_task_score(&spec, &profile, 1, &results, &obs).unwrap();
        assert_eq!(score.status, TaskScoreStatus::Completed);
        assert_eq!(score.total, Some(0.0));
        assert!(score.budget_exceeded);
        assert!(
            score
                .failure_reason
                .unwrap()
                .contains("cost budget exceeded")
        );
    }

    #[test]
    fn declared_active_time_budget_exceeded_forces_zero_score() {
        let spec = sample_spec();
        let profile = sample_profile();

        let results = vec![
            CriterionEvaluation::from_status("c1", AcceptanceStatus::Pass, None),
            CriterionEvaluation::from_status("c2", AcceptanceStatus::Pass, None),
        ];
        // max_active_time_ms is 10_000, actual is 12_000
        let obs = ScoringObservation {
            actual_cost: Some(0.1),
            active_time_ms: Some(12_000),
            model_steps: Some(2),
            evaluator_fault: None,
            pending: false,
        };

        let score = calculate_task_score(&spec, &profile, 1, &results, &obs).unwrap();
        assert_eq!(score.status, TaskScoreStatus::Completed);
        assert_eq!(score.total, Some(0.0));
        assert!(score.budget_exceeded);
        assert!(
            score
                .failure_reason
                .unwrap()
                .contains("active time budget exceeded")
        );
    }

    #[test]
    fn declared_model_steps_budget_exceeded_forces_zero_score() {
        let spec = sample_spec();
        let profile = sample_profile();

        let results = vec![
            CriterionEvaluation::from_status("c1", AcceptanceStatus::Pass, None),
            CriterionEvaluation::from_status("c2", AcceptanceStatus::Pass, None),
        ];
        // max_model_steps is 5, actual is 6
        let obs = ScoringObservation {
            actual_cost: Some(0.1),
            active_time_ms: Some(500),
            model_steps: Some(6),
            evaluator_fault: None,
            pending: false,
        };

        let score = calculate_task_score(&spec, &profile, 1, &results, &obs).unwrap();
        assert_eq!(score.status, TaskScoreStatus::Completed);
        assert_eq!(score.total, Some(0.0));
        assert!(score.budget_exceeded);
        assert!(
            score
                .failure_reason
                .unwrap()
                .contains("model steps budget exceeded")
        );
    }

    #[test]
    fn missing_price_accounting_prevents_complete_score_retaining_quality() {
        let spec = sample_spec();
        let profile = sample_profile();

        let results = vec![
            CriterionEvaluation::from_status("c1", AcceptanceStatus::Pass, None),
            CriterionEvaluation::from_status("c2", AcceptanceStatus::Pass, None),
        ];
        // actual_cost is None
        let obs = ScoringObservation {
            actual_cost: None,
            active_time_ms: Some(1_000),
            model_steps: Some(1),
            evaluator_fault: None,
            pending: false,
        };

        let score = calculate_task_score(&spec, &profile, 1, &results, &obs).unwrap();
        assert_eq!(score.status, TaskScoreStatus::IncompleteEvidence);
        assert_eq!(score.total, None);
        assert_eq!(score.quality, Some(1.0));
        assert_eq!(score.cost_efficiency, None);
        assert!(score.time_efficiency.is_some());
    }

    #[test]
    fn unknown_check_does_not_renormalize_weights_and_yields_incomplete() {
        let spec = sample_spec();
        let profile = sample_profile();

        // c1 (weight 2, required) passes, c2 (weight 2, optional) is Unknown
        // Quality must be 0.5 * 1.0 = 0.5, NOT renormalized to 1.0!
        let results = vec![
            CriterionEvaluation::from_status("c1", AcceptanceStatus::Pass, None),
            CriterionEvaluation::from_status("c2", AcceptanceStatus::Unknown, None),
        ];
        let obs = ScoringObservation {
            actual_cost: Some(0.10),
            active_time_ms: Some(1_000),
            model_steps: Some(1),
            evaluator_fault: None,
            pending: false,
        };

        let score = calculate_task_score(&spec, &profile, 1, &results, &obs).unwrap();
        assert_eq!(score.status, TaskScoreStatus::IncompleteEvidence);
        assert_eq!(score.total, None);
        assert_eq!(score.quality, Some(0.5));
    }

    #[test]
    fn evaluator_infrastructure_fault_is_invalid_evaluator() {
        let spec = sample_spec();
        let profile = sample_profile();

        let results = vec![];
        let obs = ScoringObservation {
            actual_cost: None,
            active_time_ms: None,
            model_steps: None,
            evaluator_fault: Some("connection timed out to docker daemon".into()),
            pending: false,
        };

        let score = calculate_task_score(&spec, &profile, 1, &results, &obs).unwrap();
        assert_eq!(score.status, TaskScoreStatus::InvalidEvaluator);
        assert_eq!(score.total, None);
        assert!(
            score
                .failure_reason
                .unwrap()
                .contains("evaluator infrastructure fault")
        );
    }

    #[test]
    fn pending_checks_status() {
        let spec = sample_spec();
        let profile = sample_profile();

        let results = vec![];
        let obs = ScoringObservation {
            actual_cost: None,
            active_time_ms: None,
            model_steps: None,
            evaluator_fault: None,
            pending: true,
        };

        let score = calculate_task_score(&spec, &profile, 1, &results, &obs).unwrap();
        assert_eq!(score.status, TaskScoreStatus::PendingChecks);
        assert_eq!(score.total, None);
    }

    #[test]
    fn cost_and_time_penalties_are_monotonic() {
        let spec = sample_spec();
        let profile = sample_profile();

        let results = vec![
            CriterionEvaluation::from_status("c1", AcceptanceStatus::Pass, None),
            CriterionEvaluation::from_status("c2", AcceptanceStatus::Pass, None),
        ];

        let mut previous_score = 100.0;
        for cost in [0.0, 0.1, 0.25, 0.5, 0.75, 1.0] {
            let obs = ScoringObservation {
                actual_cost: Some(cost),
                active_time_ms: Some(100),
                model_steps: Some(1),
                evaluator_fault: None,
                pending: false,
            };
            let score = calculate_task_score(&spec, &profile, 1, &results, &obs)
                .unwrap()
                .total
                .unwrap();
            assert!(
                score <= previous_score,
                "score {score} must be <= previous {previous_score} as cost increases"
            );
            previous_score = score;
        }

        previous_score = 100.0;
        for time in [0, 500, 1_000, 2_000, 5_000, 10_000] {
            let obs = ScoringObservation {
                actual_cost: Some(0.1),
                active_time_ms: Some(time),
                model_steps: Some(1),
                evaluator_fault: None,
                pending: false,
            };
            let score = calculate_task_score(&spec, &profile, 1, &results, &obs)
                .unwrap()
                .total
                .unwrap();
            assert!(
                score <= previous_score,
                "score {score} must be <= previous {previous_score} as time increases"
            );
            previous_score = score;
        }
    }

    fn sample_snapshot(
        session_id: SessionId,
        terminal_status: &str,
        tools: Vec<crate::ObservedTool>,
        active_ms: Option<u64>,
    ) -> RunEvidenceSnapshot {
        use arabica_protocol::{
            CommandId, Event, EventEnvelope, EventId, EventMetadata, RunId, WorkspaceId,
        };

        let event = |sequence: u64, ms: u64, ev: Event| {
            EventEnvelope::new(
                EventMetadata {
                    event_id: EventId::new(format!("ev-{sequence}")),
                    command_id: CommandId::new("cmd"),
                    workspace_id: WorkspaceId::new("ws1"),
                    session_id: session_id.clone(),
                    run_id: Some(RunId::new("run1")),
                    sequence,
                    occurred_at_ms: ms,
                },
                ev,
            )
        };

        let mut snap = crate::run_evidence_snapshots(&[
            event(0, 0, Event::RunStarted),
            event(
                1,
                50,
                match terminal_status {
                    "completed" => Event::RunCompleted {
                        output: Some("done".into()),
                    },
                    "failed" => Event::RunFailed {
                        message: "fatal error".into(),
                    },
                    _ => Event::RunCancelled,
                },
            ),
        ])
        .pop()
        .unwrap();

        snap.terminal_status = terminal_status.into();
        snap.timing.active_ms = active_ms;
        snap.tools = tools;
        snap
    }

    #[test]
    fn live_session_clean_run_yields_full_quality() {
        use crate::ObservedTool;
        use arabica_protocol::SessionId;

        let session_id = SessionId::new("live_sess_1");
        let config = LiveSessionConfig::new(session_id.clone());
        let profile = sample_profile();

        let tools = vec![
            ObservedTool {
                call_id: "c1".into(),
                name: "read_file".into(),
                is_error: Some(false),
                runner_outcome: None,
            },
            ObservedTool {
                call_id: "c2".into(),
                name: "write_file".into(),
                is_error: Some(false),
                runner_outcome: None,
            },
        ];
        let snapshot = sample_snapshot(session_id, "completed", tools, Some(2000));

        // actual == reference (0.50 cost, 2000 active ms) -> E_cost = 0.5, E_time = 0.5
        // multiplier = 0.70 + 0.20*0.5 + 0.10*0.5 = 0.85
        // Quality = 1.0 -> Score = 85.0
        let score =
            evaluate_live_session(&config, &profile, 1, &snapshot, Some(0.50), None).unwrap();
        assert_eq!(score.status, TaskScoreStatus::Completed);
        assert_eq!(score.quality, Some(1.0));
        assert!((score.total.unwrap() - 85.0).abs() < 1e-9);
        assert_eq!(score.required_passed, Some(true));
        assert!(!score.budget_exceeded);
    }

    #[test]
    fn live_session_penalizes_errored_tools_proportionally() {
        use crate::ObservedTool;
        use arabica_protocol::SessionId;

        let session_id = SessionId::new("live_sess_2");
        let mut config = LiveSessionConfig::new(session_id.clone());
        config.require_clean_terminal_tools = false;
        config.tool_health_weight = 0.40;
        let profile = sample_profile();

        let tools = vec![
            ObservedTool {
                call_id: "c1".into(),
                name: "shell".into(),
                is_error: Some(true), // 1 of 2 tools errored
                runner_outcome: None,
            },
            ObservedTool {
                call_id: "c2".into(),
                name: "shell".into(),
                is_error: Some(false),
                runner_outcome: None,
            },
        ];
        let snapshot = sample_snapshot(session_id, "completed", tools, Some(0));

        // Tool success ratio: 1/2 = 0.5.
        // Quality = 1.0 - (0.40 * (1 - 0.5)) = 1.0 - 0.20 = 0.80
        // Cost = 0, Time = 0 -> multiplier = 1.0
        // Score = 100 * 0.80 * 1.0 = 80.0
        let score =
            evaluate_live_session(&config, &profile, 1, &snapshot, Some(0.0), None).unwrap();
        assert_eq!(score.status, TaskScoreStatus::Completed);
        assert!((score.quality.unwrap() - 0.80).abs() < 1e-9);
        assert!((score.total.unwrap() - 80.0).abs() < 1e-9);
    }

    #[test]
    fn live_session_fails_when_terminated_with_unrecovered_error() {
        use crate::ObservedTool;
        use arabica_protocol::SessionId;

        let session_id = SessionId::new("live_sess_3");
        let config = LiveSessionConfig::new(session_id.clone());
        let profile = sample_profile();

        let tools = vec![ObservedTool {
            call_id: "c1".into(),
            name: "shell".into(),
            is_error: Some(true), // Last tool failed and unrecovered!
            runner_outcome: None,
        }];
        let snapshot = sample_snapshot(session_id, "completed", tools, Some(100));

        let score =
            evaluate_live_session(&config, &profile, 1, &snapshot, Some(0.0), None).unwrap();
        assert_eq!(score.status, TaskScoreStatus::Completed);
        assert_eq!(score.quality, Some(0.0));
        assert_eq!(score.total, Some(0.0));
        assert!(
            score
                .failure_reason
                .unwrap()
                .contains("unrecovered tool error")
        );
    }

    #[test]
    fn live_session_fails_when_workspace_gate_fails() {
        use arabica_protocol::SessionId;

        let session_id = SessionId::new("live_sess_4");
        let config = LiveSessionConfig::new(session_id.clone());
        let profile = sample_profile();

        let snapshot = sample_snapshot(session_id, "completed", vec![], Some(100));

        let gate_res = WorkspaceGateResult {
            check_passed: false,
            tests_passed: None,
            permitted_files_passed: None,
            overall_passed: false,
            failure_reasons: vec!["cargo check compilation error: undefined variable foo".into()],
        };

        let score =
            evaluate_live_session(&config, &profile, 1, &snapshot, Some(0.01), Some(&gate_res))
                .unwrap();
        assert_eq!(score.status, TaskScoreStatus::Completed);
        assert_eq!(score.quality, Some(0.0));
        assert_eq!(score.total, Some(0.0));
        assert!(
            score
                .failure_reason
                .unwrap()
                .contains("cargo check compilation error")
        );
    }

    #[test]
    fn workspace_gate_execution_checks_and_catches_unpermitted_files() {
        let temp_dir = std::env::temp_dir();
        let config = WorkspaceGateConfig {
            check_command: "echo 'build ok'".into(),
            test_command: Some("exit 0".into()),
            permitted_paths: Some(vec!["src/lib.rs".into()]),
            timeout_ms: 5000,
            output_limit_bytes: 4096,
        };

        // Allowed path modified -> passes
        let res = execute_workspace_gate(&temp_dir, &config, Some(&["src/lib.rs".into()]));
        assert!(res.overall_passed);
        assert!(res.check_passed);
        assert_eq!(res.tests_passed, Some(true));
        assert_eq!(res.permitted_files_passed, Some(true));

        // Unpermitted path modified -> fails gate!
        let res = execute_workspace_gate(&temp_dir, &config, Some(&["tests/test.rs".into()]));
        assert!(!res.overall_passed);
        assert_eq!(res.permitted_files_passed, Some(false));
        assert!(
            res.failure_reasons
                .join(" ")
                .contains("unpermitted files modified")
        );
    }
}
