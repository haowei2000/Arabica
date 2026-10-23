use std::collections::BTreeMap;
use std::fmt::{self, Display, Formatter};
use std::str::FromStr;

use serde::{Deserialize, Serialize};

use crate::{
    BlendEvaluationReport, BlendRoutingPolicy, BlendToolMatcher, BlendToolRoute, EvaluationError,
    TaskTrialScore,
};

/// Lifecycle state for an immutable routing policy definition.
#[derive(Clone, Copy, Debug, Eq, Hash, Ord, PartialEq, PartialOrd, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum PolicyLifecycleStatus {
    /// Bounded proposal generated from evaluation evidence.
    Candidate,
    /// Scored through experimental trials or campaigns.
    Evaluated,
    /// Passed recommendation gates and eligible for activation.
    Approved,
    /// Currently active for new agent sessions/runs.
    Active,
    /// Failed gates, discarded, or manually rejected.
    Rejected,
    /// Superseded by a newer active policy or rolled back.
    Retired,
}

impl Display for PolicyLifecycleStatus {
    fn fmt(&self, f: &mut Formatter<'_>) -> fmt::Result {
        let s = match self {
            Self::Candidate => "candidate",
            Self::Evaluated => "evaluated",
            Self::Approved => "approved",
            Self::Active => "active",
            Self::Rejected => "rejected",
            Self::Retired => "retired",
        };
        write!(f, "{s}")
    }
}

impl FromStr for PolicyLifecycleStatus {
    type Err = EvaluationError;

    fn from_str(s: &str) -> Result<Self, Self::Err> {
        match s.trim().to_ascii_lowercase().as_str() {
            "candidate" => Ok(Self::Candidate),
            "evaluated" => Ok(Self::Evaluated),
            "approved" => Ok(Self::Approved),
            "active" => Ok(Self::Active),
            "rejected" => Ok(Self::Rejected),
            "retired" => Ok(Self::Retired),
            other => Err(EvaluationError(format!(
                "invalid policy lifecycle status: '{other}'"
            ))),
        }
    }
}

impl PolicyLifecycleStatus {
    /// Check whether a transition from `self` to `target` is valid.
    pub fn can_transition_to(&self, target: Self) -> bool {
        if *self == target {
            return true;
        }
        match (self, target) {
            // Candidates can be evaluated, approved, or rejected
            (Self::Candidate, Self::Evaluated | Self::Approved | Self::Rejected) => true,
            // Evaluated policies can be approved, rejected, or re-evaluated
            (Self::Evaluated, Self::Approved | Self::Rejected | Self::Candidate) => true,
            // Approved policies can become active (activation) or be rejected
            (Self::Approved, Self::Active | Self::Rejected) => true,
            // Active policies transition to retired when superseded or rolled back
            (Self::Active, Self::Retired) => true,
            // Retired policies can be reactivated on rollback or re-queued
            (Self::Retired, Self::Active) => true,
            // Rejected policies can be re-opened as candidates
            (Self::Rejected, Self::Candidate) => true,
            _ => false,
        }
    }
}

/// A specific parameter modification within a proposed policy candidate.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(tag = "parameter", rename_all = "snake_case")]
pub enum PolicyParameterDelta {
    DefaultModel {
        from: String,
        to: String,
    },
    AfterToolSuccess {
        from: Option<String>,
        to: Option<String>,
    },
    AfterToolError {
        from: Option<String>,
        to: Option<String>,
    },
    RecoveryModel {
        from: Option<String>,
        to: Option<String>,
    },
    RecoveryAfterNoProgressSteps {
        from: usize,
        to: usize,
    },
    MinimumModelDwellSteps {
        from: usize,
        to: usize,
    },
    AddToolRoute {
        route: BlendToolRoute,
    },
    RemoveToolRoute {
        route_id: String,
    },
}

/// Provenance and metrics that justified generating this candidate.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ProposalEvidenceBasis {
    pub metric: String,
    pub observed_value: String,
    pub threshold: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub category: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub source_fingerprint: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub tool_name: Option<String>,
}

/// A bounded candidate proposal derived from evaluated evidence.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct PolicyProposal {
    pub proposal_id: String,
    pub base_policy_id: String,
    pub base_version: u64,
    pub candidate_version: u64,
    pub proposed_policy: BlendRoutingPolicy,
    pub parameter_deltas: Vec<PolicyParameterDelta>,
    pub evidence: ProposalEvidenceBasis,
    pub rationale: String,
}

/// Catalog of available models for bounded proposal exploration.
#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
pub struct AvailableModelCatalog {
    pub fast_models: Vec<String>,
    pub strong_models: Vec<String>,
    pub recovery_models: Vec<String>,
}

impl AvailableModelCatalog {
    pub fn new(
        fast_models: Vec<String>,
        strong_models: Vec<String>,
        recovery_models: Vec<String>,
    ) -> Self {
        Self {
            fast_models,
            strong_models,
            recovery_models,
        }
    }
}

/// Generate bounded candidate policy proposals informed by evaluation evidence.
///
/// Bounded exploration covers five dimensions:
/// 1. Default model: Upgrading default when failure rate in complex tasks is high,
///    or testing fast default when simple task pass rate is 100%.
/// 2. Post-tool model (after_tool_success, after_tool_error): Fallback model after
///    downstream tool errors or cost-optimized model after clean tool execution.
/// 3. Recovery models & thresholds: Tightening recovery steps and configuring dedicated
///    recovery models when stalling/no-progress failures occur.
/// 4. Minimum dwell: Increasing dwell steps when oscillation or rapid dropback occurs.
/// 5. Explicit tool routes: Routing tools with observed failures to a capable model.
pub fn generate_bounded_proposals(
    base: &BlendRoutingPolicy,
    catalog: &AvailableModelCatalog,
    trial_scores: &[TaskTrialScore],
    report: Option<&BlendEvaluationReport>,
    tool_errors: &[(String, u64)],
    source_fingerprint: Option<String>,
) -> Vec<PolicyProposal> {
    let mut proposals = Vec::new();
    let next_version = base.version.saturating_add(1);

    // 1. Tool-level error evidence -> Explicit tool routes & AfterToolError
    let mut tool_error_counts = BTreeMap::<String, u64>::new();
    let mut total_tool_errors = 0_u64;

    for (tool, count) in tool_errors {
        *tool_error_counts.entry(tool.clone()).or_default() += count;
        total_tool_errors += count;
    }

    if let Some(rep) = report {
        let rep_tool_errors: u64 = rep.models.values().map(|m| m.downstream_tool_errors).sum();
        total_tool_errors = total_tool_errors.max(rep_tool_errors);
    }

    let strong_model = catalog
        .strong_models
        .first()
        .cloned()
        .or_else(|| base.recovery_model.clone())
        .unwrap_or_else(|| base.default_model.clone());

    let recovery_model = catalog
        .recovery_models
        .first()
        .cloned()
        .or_else(|| catalog.strong_models.first().cloned())
        .or_else(|| base.recovery_model.clone())
        .unwrap_or_else(|| base.default_model.clone());

    let fast_model = catalog
        .fast_models
        .first()
        .cloned()
        .unwrap_or_else(|| base.default_model.clone());

    // Dimension 1: Default model upgrade on complex category failures
    if !trial_scores.is_empty() {
        let mut complex_fails = 0_usize;
        let mut complex_total = 0_usize;
        for t in trial_scores {
            if t.category == "Multistep work and recovery" || t.category == "Small code fixes" {
                complex_total += 1;
                if !t.required_passed() {
                    complex_fails += 1;
                }
            }
        }
        if complex_total > 0
            && (complex_fails as f64 / complex_total as f64) > 0.4
            && base.default_model != strong_model
        {
            let mut policy = base.clone();
            policy.version = next_version;
            policy.default_model = strong_model.clone();

            proposals.push(PolicyProposal {
                proposal_id: format!("{}-v{}-default-model", base.policy_id, next_version),
                base_policy_id: base.policy_id.clone(),
                base_version: base.version,
                candidate_version: next_version,
                proposed_policy: policy,
                parameter_deltas: vec![PolicyParameterDelta::DefaultModel {
                    from: base.default_model.clone(),
                    to: strong_model.clone(),
                }],
                evidence: ProposalEvidenceBasis {
                    metric: "complex_category_failure_rate".into(),
                    observed_value: format!("{:.2}", complex_fails as f64 / complex_total as f64),
                    threshold: "0.40".into(),
                    category: Some("Multistep work and recovery".into()),
                    source_fingerprint: source_fingerprint.clone(),
                    tool_name: None,
                },
                rationale: format!(
                    "High failure rate ({complex_fails}/{complex_total}) on complex tasks under default model '{}'; propose upgrading default model to '{strong_model}'",
                    base.default_model
                ),
            });
        }
    }

    // Dimension 2: AfterToolError fallback when tool errors observed
    if total_tool_errors > 0 && base.after_tool_error.as_ref() != Some(&recovery_model) {
        let mut policy = base.clone();
        policy.version = next_version;
        policy.after_tool_error = Some(recovery_model.clone());

        proposals.push(PolicyProposal {
            proposal_id: format!("{}-v{}-after-tool-error", base.policy_id, next_version),
            base_policy_id: base.policy_id.clone(),
            base_version: base.version,
            candidate_version: next_version,
            proposed_policy: policy,
            parameter_deltas: vec![PolicyParameterDelta::AfterToolError {
                from: base.after_tool_error.clone(),
                to: Some(recovery_model.clone()),
            }],
            evidence: ProposalEvidenceBasis {
                metric: "total_tool_errors".into(),
                observed_value: total_tool_errors.to_string(),
                threshold: "1".into(),
                category: None,
                source_fingerprint: source_fingerprint.clone(),
                tool_name: None,
            },
            rationale: format!(
                "Observed {total_tool_errors} downstream tool error(s); propose routing to recovery model '{recovery_model}' on tool error"
            ),
        });
    }

    // Post-tool success cost optimization when tools succeed without error
    if total_tool_errors == 0
        && base.after_tool_success.is_none()
        && base.default_model != fast_model
    {
        let mut policy = base.clone();
        policy.version = next_version;
        policy.after_tool_success = Some(fast_model.clone());

        proposals.push(PolicyProposal {
            proposal_id: format!("{}-v{}-after-tool-success", base.policy_id, next_version),
            base_policy_id: base.policy_id.clone(),
            base_version: base.version,
            candidate_version: next_version,
            proposed_policy: policy,
            parameter_deltas: vec![PolicyParameterDelta::AfterToolSuccess {
                from: None,
                to: Some(fast_model.clone()),
            }],
            evidence: ProposalEvidenceBasis {
                metric: "clean_tool_execution".into(),
                observed_value: "0 tool errors".into(),
                threshold: "0".into(),
                category: None,
                source_fingerprint: source_fingerprint.clone(),
                tool_name: None,
            },
            rationale: format!(
                "Tools executed without errors; propose routing to fast model '{fast_model}' after tool success for cost savings"
            ),
        });
    }

    // Dimension 3: Recovery model & thresholds tightening on failed runs
    let runs_failed = report
        .map(|r| r.policies.values().map(|p| p.runs_failed).sum::<u64>())
        .unwrap_or(0);

    if (runs_failed > 0
        || trial_scores
            .iter()
            .any(|t| !t.required_passed() && t.category.contains("recovery")))
        && base.recovery_after_no_progress_steps > 1
    {
        let mut policy = base.clone();
        policy.version = next_version;
        let new_steps = 1;
        policy.recovery_after_no_progress_steps = new_steps;
        if policy.recovery_model.is_none() {
            policy.recovery_model = Some(recovery_model.clone());
        }

        let mut deltas = vec![PolicyParameterDelta::RecoveryAfterNoProgressSteps {
            from: base.recovery_after_no_progress_steps,
            to: new_steps,
        }];
        if base.recovery_model != policy.recovery_model {
            deltas.push(PolicyParameterDelta::RecoveryModel {
                from: base.recovery_model.clone(),
                to: policy.recovery_model.clone(),
            });
        }

        proposals.push(PolicyProposal {
            proposal_id: format!("{}-v{}-tighten-recovery", base.policy_id, next_version),
            base_policy_id: base.policy_id.clone(),
            base_version: base.version,
            candidate_version: next_version,
            proposed_policy: policy,
            parameter_deltas: deltas,
            evidence: ProposalEvidenceBasis {
                metric: "runs_failed_or_stalled".into(),
                observed_value: runs_failed.to_string(),
                threshold: "1".into(),
                category: Some("Multistep work and recovery".into()),
                source_fingerprint: source_fingerprint.clone(),
                tool_name: None,
            },
            rationale: format!(
                "Failed or stalled runs detected; propose tightening recovery_after_no_progress_steps from {} to {new_steps} with recovery model '{recovery_model}'",
                base.recovery_after_no_progress_steps
            ),
        });
    }

    // Dimension 4: Minimum dwell extension
    if base.minimum_model_dwell_steps == 1 && (total_tool_errors > 0 || runs_failed > 0) {
        let mut policy = base.clone();
        policy.version = next_version;
        policy.minimum_model_dwell_steps = 2;

        proposals.push(PolicyProposal {
            proposal_id: format!("{}-v{}-increase-dwell", base.policy_id, next_version),
            base_policy_id: base.policy_id.clone(),
            base_version: base.version,
            candidate_version: next_version,
            proposed_policy: policy,
            parameter_deltas: vec![PolicyParameterDelta::MinimumModelDwellSteps {
                from: 1,
                to: 2,
            }],
            evidence: ProposalEvidenceBasis {
                metric: "model_flapping_and_errors".into(),
                observed_value: format!("errors={total_tool_errors}, failed_runs={runs_failed}"),
                threshold: "errors > 0".into(),
                category: None,
                source_fingerprint: source_fingerprint.clone(),
                tool_name: None,
            },
            rationale:
                "Propose increasing minimum_model_dwell_steps from 1 to 2 to prevent premature model drop-backs during error recovery"
                    .into(),
        });
    }

    // Dimension 5: Explicit tool route for error-prone tools
    for (tool_name, &err_count) in &tool_error_counts {
        if err_count > 0
            && !base.tool_routes.iter().any(
                |r| matches!(&r.matcher, BlendToolMatcher::ExactName(name) if name == tool_name),
            )
        {
            let route_id = format!("route-to-strong-{}", tool_name);
            let route = BlendToolRoute {
                id: route_id.clone(),
                matcher: BlendToolMatcher::ExactName(tool_name.clone()),
                model: strong_model.clone(),
                priority: 10,
            };

            let mut policy = base.clone();
            policy.version = next_version;
            policy.tool_routes.push(route.clone());

            proposals.push(PolicyProposal {
                proposal_id: format!(
                    "{}-v{}-tool-route-{}",
                    base.policy_id, next_version, tool_name
                ),
                base_policy_id: base.policy_id.clone(),
                base_version: base.version,
                candidate_version: next_version,
                proposed_policy: policy,
                parameter_deltas: vec![PolicyParameterDelta::AddToolRoute { route }],
                evidence: ProposalEvidenceBasis {
                    metric: "tool_error_count".into(),
                    observed_value: err_count.to_string(),
                    threshold: "1".into(),
                    category: None,
                    source_fingerprint: source_fingerprint.clone(),
                    tool_name: Some(tool_name.clone()),
                },
                rationale: format!(
                    "Tool '{tool_name}' produced {err_count} downstream error(s); propose explicit route mapping '{tool_name}' directly to '{strong_model}'"
                ),
            });
        }
    }

    proposals
}

/// Trajectory-level feedback associating run scores with routing decisions.
///
/// Treats scores as whole-trajectory outcomes without claiming each individual
/// routing step was isolatedly correct or incorrect.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct TrajectoryRoutingFeedback {
    pub run_id: String,
    pub policy_id: String,
    pub policy_version: u64,
    pub trajectory_passed: bool,
    pub final_score: f64,
    pub decisions: Vec<TrajectoryDecisionObservation>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct TrajectoryDecisionObservation {
    pub step: usize,
    pub model_alias: String,
    pub reason: String,
    pub tool_calls_count: usize,
    pub tool_errors_count: usize,
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::BlendPolicyEvaluation;
    use std::collections::BTreeSet;

    fn dummy_policy() -> BlendRoutingPolicy {
        BlendRoutingPolicy {
            policy_id: "test-policy".into(),
            version: 1,
            default_model: "fast".into(),
            after_tool_success: None,
            after_tool_error: None,
            recovery_model: Some("strong".into()),
            planning_model: None,
            tool_routes: vec![],
            recovery_after_no_progress_steps: 3,
            minimum_model_dwell_steps: 1,
            tool_call_capable_models: BTreeSet::new(),
            typed_completion_capable_models: BTreeSet::new(),
        }
    }

    #[test]
    fn lifecycle_valid_and_invalid_transitions() {
        assert!(
            PolicyLifecycleStatus::Candidate.can_transition_to(PolicyLifecycleStatus::Evaluated)
        );
        assert!(
            PolicyLifecycleStatus::Evaluated.can_transition_to(PolicyLifecycleStatus::Approved)
        );
        assert!(PolicyLifecycleStatus::Approved.can_transition_to(PolicyLifecycleStatus::Active));
        assert!(PolicyLifecycleStatus::Active.can_transition_to(PolicyLifecycleStatus::Retired));
        // Rollback reactivation
        assert!(PolicyLifecycleStatus::Retired.can_transition_to(PolicyLifecycleStatus::Active));
        // Reject transitions
        assert!(
            PolicyLifecycleStatus::Candidate.can_transition_to(PolicyLifecycleStatus::Rejected)
        );
        assert!(
            PolicyLifecycleStatus::Evaluated.can_transition_to(PolicyLifecycleStatus::Rejected)
        );
        assert!(PolicyLifecycleStatus::Approved.can_transition_to(PolicyLifecycleStatus::Rejected));

        // Invalid direct transitions
        assert!(!PolicyLifecycleStatus::Candidate.can_transition_to(PolicyLifecycleStatus::Active));
        assert!(
            !PolicyLifecycleStatus::Candidate.can_transition_to(PolicyLifecycleStatus::Retired)
        );
        assert!(!PolicyLifecycleStatus::Active.can_transition_to(PolicyLifecycleStatus::Candidate));
    }

    #[test]
    fn bounded_proposals_vary_dimensions_with_evidence() {
        let base = dummy_policy();
        let catalog = AvailableModelCatalog::new(
            vec!["fast".into()],
            vec!["strong".into()],
            vec!["strong".into()],
        );

        let mut report = BlendEvaluationReport::default();
        let policy_eval = BlendPolicyEvaluation {
            runs_failed: 1,
            ..Default::default()
        };
        report.policies.insert("test-policy".into(), policy_eval);

        let tool_errors = vec![("patch".to_string(), 3_u64)];

        let proposals = generate_bounded_proposals(
            &base,
            &catalog,
            &[],
            Some(&report),
            &tool_errors,
            Some("fp-evidence".into()),
        );

        assert!(!proposals.is_empty());
        // Verify proposals cover AfterToolError, ToolRoute for patch, tighten recovery, and increase dwell
        let has_after_tool_error = proposals.iter().any(|p| {
            p.parameter_deltas
                .iter()
                .any(|d| matches!(d, PolicyParameterDelta::AfterToolError { .. }))
        });
        let has_patch_route = proposals
            .iter()
            .any(|p| p.parameter_deltas.iter().any(|d| matches!(d, PolicyParameterDelta::AddToolRoute { route } if route.matcher == BlendToolMatcher::ExactName("patch".into()))));
        let has_tighten_recovery = proposals.iter().any(|p| {
            p.parameter_deltas.iter().any(|d| {
                matches!(
                    d,
                    PolicyParameterDelta::RecoveryAfterNoProgressSteps { from: 3, to: 1 }
                )
            })
        });
        let has_dwell_increase = proposals.iter().any(|p| {
            p.parameter_deltas.iter().any(|d| {
                matches!(
                    d,
                    PolicyParameterDelta::MinimumModelDwellSteps { from: 1, to: 2 }
                )
            })
        });

        assert!(
            has_after_tool_error,
            "Should propose AfterToolError fallback"
        );
        assert!(
            has_patch_route,
            "Should propose explicit route for failing 'patch' tool"
        );
        assert!(
            has_tighten_recovery,
            "Should propose tightening recovery threshold"
        );
        assert!(has_dwell_increase, "Should propose increasing dwell steps");

        for p in &proposals {
            assert_eq!(p.base_version, 1);
            assert_eq!(p.candidate_version, 2);
            assert_eq!(
                p.evidence.source_fingerprint.as_deref(),
                Some("fp-evidence")
            );
            assert!(!p.rationale.is_empty());
        }
    }
}
