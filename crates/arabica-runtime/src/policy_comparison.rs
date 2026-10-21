use std::collections::{BTreeMap, BTreeSet};

use serde::{Deserialize, Serialize};

use crate::{EvaluationError, TaskScore, TaskScoreStatus};

/// Predeclared category weights used to aggregate multi-category trial scores.
#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct CategoryWeights {
    pub weights: BTreeMap<String, f64>,
}

impl CategoryWeights {
    pub fn new(weights: BTreeMap<String, f64>) -> Result<Self, EvaluationError> {
        let cw = Self { weights };
        cw.validate()?;
        Ok(cw)
    }

    /// Equal weighting across a set of categories.
    pub fn equal_split<'a>(
        categories: impl IntoIterator<Item = &'a str>,
    ) -> Result<Self, EvaluationError> {
        let cats: Vec<String> = categories.into_iter().map(String::from).collect();
        if cats.is_empty() {
            return Err(EvaluationError(
                "cannot create weights for empty categories".into(),
            ));
        }
        let w = 1.0 / (cats.len() as f64);
        let mut map = BTreeMap::new();
        for cat in cats {
            map.insert(cat, w);
        }
        Self::new(map)
    }

    pub fn validate(&self) -> Result<(), EvaluationError> {
        if self.weights.is_empty() {
            return Err(EvaluationError("category weights cannot be empty".into()));
        }
        let mut sum = 0.0;
        for (cat, &w) in &self.weights {
            if cat.trim().is_empty() {
                return Err(EvaluationError("category name cannot be empty".into()));
            }
            if w <= 0.0 || !w.is_finite() {
                return Err(EvaluationError(format!(
                    "category weight for '{cat}' must be positive and finite"
                )));
            }
            sum += w;
        }
        if (sum - 1.0).abs() > 1e-4 {
            return Err(EvaluationError(format!(
                "category weights must sum to 1.0; sum is {sum:.4}"
            )));
        }
        Ok(())
    }
}

/// A score record for one candidate on a specific task and repetition.
#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct TaskTrialScore {
    pub candidate: String,
    pub task_id: String,
    pub category: String,
    pub repetition: u32,
    pub score: Option<TaskScore>,
    pub cost: Option<f64>,
    pub wall_ms: Option<u64>,
    pub active_ms: Option<u64>,
    pub has_usage: bool,
}

impl TaskTrialScore {
    pub fn is_completed_and_valid(&self) -> bool {
        self.score
            .as_ref()
            .is_some_and(|s| s.status == TaskScoreStatus::Completed && s.total.is_some())
    }

    pub fn total_score_or_zero(&self) -> f64 {
        match &self.score {
            Some(s) if s.status == TaskScoreStatus::Completed => s.total.unwrap_or(0.0),
            _ => 0.0,
        }
    }

    pub fn required_passed(&self) -> bool {
        self.score
            .as_ref()
            .is_some_and(|s| s.required_passed == Some(true))
    }
}

fn percentile(values: &mut [u64], pct: usize) -> Option<u64> {
    values.sort_unstable();
    (!values.is_empty()).then(|| values[(values.len() * pct).div_ceil(100).saturating_sub(1)])
}

/// Comprehensive aggregate evaluation metrics for one policy candidate.
#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct CandidateAggregateReport {
    pub candidate: String,
    pub total_trials: usize,
    pub scored_trials: usize,
    pub incomplete_trials: usize,
    pub score_coverage: f64,
    pub usage_coverage: f64,
    /// Category-weighted overall strategy score [0, 100].
    /// StrategyScore = sum(category_weight * mean_score_within_category).
    pub strategy_score: Option<f64>,
    pub category_scores: BTreeMap<String, f64>,
    pub required_check_pass_rate: f64,
    pub total_spend: f64,
    /// Total spend divided by successful tasks (includes failed-task spend).
    /// Unavailable (None) when successes == 0, never zero.
    pub spend_per_successful_task: Option<f64>,
    pub wall_p50_ms: Option<u64>,
    pub wall_p95_ms: Option<u64>,
    pub active_p50_ms: Option<u64>,
    pub active_p95_ms: Option<u64>,
}

/// Paired comparison metrics between a candidate and baseline across matched tasks.
#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct PairedComparison {
    pub candidate: String,
    pub baseline: String,
    pub matched_tasks: usize,
    pub mean_difference: f64,
    pub standard_error: f64,
    pub confidence_interval_95: (f64, f64),
    pub statistically_significant_win: bool,
}

/// Predeclared gates required to recommend promotion of a candidate over baseline.
#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct RecommendationGatesConfig {
    /// Minimum score coverage ratio [0, 1], e.g. 1.0 (no missing evaluations tolerated).
    pub min_score_coverage: f64,
    /// Minimum usage accounting coverage ratio [0, 1], e.g. 1.0.
    pub min_usage_coverage: f64,
    /// Minimum overall required-check success rate, e.g. 0.80.
    pub overall_success_floor: f64,
    /// Minimum required-check success rate for critical categories, e.g. 0.75.
    pub critical_category_success_floor: f64,
    pub critical_categories: BTreeSet<String>,
    /// Minimum score improvement (StrategyScore delta) over baseline, e.g. +2.0.
    pub min_score_improvement: f64,
    /// Rejects if candidate has lower required-check pass rate than baseline.
    pub require_no_quality_regression: bool,
    /// Rejects if 95% confidence interval lower bound does not exceed 0.
    pub require_statistical_significance: bool,
}

impl Default for RecommendationGatesConfig {
    fn default() -> Self {
        Self {
            min_score_coverage: 1.0,
            min_usage_coverage: 1.0,
            overall_success_floor: 0.80,
            critical_category_success_floor: 0.75,
            critical_categories: BTreeSet::new(),
            min_score_improvement: 2.0,
            require_no_quality_regression: true,
            require_statistical_significance: true,
        }
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum RecommendationDecision {
    /// Candidate passed all quality, coverage, and improvement gates.
    Promote,
    /// Candidate failed quality, regression, or floor gates.
    Reject,
    /// Insufficient trials, missing data, or inconclusive difference.
    Inconclusive,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct GateCheckEvaluation {
    pub gate_name: String,
    pub passed: bool,
    pub detail: String,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct RecommendationReport {
    pub candidate: String,
    pub baseline: String,
    pub decision: RecommendationDecision,
    pub overall_passed: bool,
    pub gate_evaluations: Vec<GateCheckEvaluation>,
    pub summary_reasons: Vec<String>,
}

/// Aggregate candidate scores using predeclared category weights.
pub fn aggregate_candidate_scores(
    trials: &[TaskTrialScore],
    weights: &CategoryWeights,
) -> Result<BTreeMap<String, CandidateAggregateReport>, EvaluationError> {
    weights.validate()?;

    let mut by_candidate = BTreeMap::<String, Vec<&TaskTrialScore>>::new();
    for trial in trials {
        if trial.candidate.trim().is_empty() {
            return Err(EvaluationError(
                "candidate identifier cannot be empty".into(),
            ));
        }
        by_candidate
            .entry(trial.candidate.clone())
            .or_default()
            .push(trial);
    }

    let mut reports = BTreeMap::new();

    for (candidate, c_trials) in by_candidate {
        let total_trials = c_trials.len();
        if total_trials == 0 {
            continue;
        }

        let mut scored_count = 0_usize;
        let mut usage_count = 0_usize;
        let mut success_count = 0_usize;
        let mut total_spend = 0.0;
        let mut wall_times = Vec::new();
        let mut active_times = Vec::new();

        // Group scores by category
        let mut scores_by_cat = BTreeMap::<String, Vec<f64>>::new();

        for t in &c_trials {
            if t.has_usage {
                usage_count += 1;
            }
            if let Some(c) = t.cost {
                total_spend += c;
            }
            if let Some(w) = t.wall_ms {
                wall_times.push(w);
            }
            if let Some(a) = t.active_ms {
                active_times.push(a);
            }

            if t.is_completed_and_valid() {
                scored_count += 1;
                if t.required_passed() {
                    success_count += 1;
                }
                scores_by_cat
                    .entry(t.category.clone())
                    .or_default()
                    .push(t.total_score_or_zero());
            } else {
                // Scored failures count as 0.0
                scores_by_cat
                    .entry(t.category.clone())
                    .or_default()
                    .push(0.0);
            }
        }

        let score_coverage = scored_count as f64 / total_trials as f64;
        let usage_coverage = usage_count as f64 / total_trials as f64;
        let incomplete_trials = total_trials.saturating_sub(scored_count);
        let required_check_pass_rate = success_count as f64 / total_trials as f64;

        // Spend per successful task: total spend divided by successful tasks.
        // If zero successes, it is unavailable (None), never zero.
        let spend_per_successful_task = if success_count > 0 {
            Some(total_spend / success_count as f64)
        } else {
            None
        };

        // Compute category averages and weighted strategy score
        let mut category_scores = BTreeMap::new();
        let mut strategy_score_accum = 0.0;
        let mut all_weighted_categories_present = true;

        for (cat, &w) in &weights.weights {
            if let Some(cat_scores) = scores_by_cat.get(cat) {
                let cat_mean = if !cat_scores.is_empty() {
                    cat_scores.iter().sum::<f64>() / cat_scores.len() as f64
                } else {
                    0.0
                };
                category_scores.insert(cat.clone(), cat_mean);
                strategy_score_accum += w * cat_mean;
            } else {
                all_weighted_categories_present = false;
                category_scores.insert(cat.clone(), 0.0);
            }
        }

        let strategy_score = if all_weighted_categories_present && score_coverage > 0.0 {
            Some(strategy_score_accum)
        } else {
            None
        };

        reports.insert(
            candidate.clone(),
            CandidateAggregateReport {
                candidate,
                total_trials,
                scored_trials: scored_count,
                incomplete_trials,
                score_coverage,
                usage_coverage,
                strategy_score,
                category_scores,
                required_check_pass_rate,
                total_spend,
                spend_per_successful_task,
                wall_p50_ms: percentile(&mut wall_times, 50),
                wall_p95_ms: percentile(&mut wall_times, 95),
                active_p50_ms: percentile(&mut active_times, 50),
                active_p95_ms: percentile(&mut active_times, 95),
            },
        );
    }

    Ok(reports)
}

/// Compute matched paired comparisons and cluster-robust standard errors across repetitions.
pub fn compute_paired_comparison(
    candidate: &str,
    baseline: &str,
    trials: &[TaskTrialScore],
) -> Result<PairedComparison, EvaluationError> {
    if candidate == baseline {
        return Err(EvaluationError(
            "cannot pair-compare candidate against itself".into(),
        ));
    }

    // Map: (task_id, repetition) -> score
    let mut cand_scores = BTreeMap::<(String, u32), f64>::new();
    let mut base_scores = BTreeMap::<(String, u32), f64>::new();

    for t in trials {
        if t.candidate == candidate {
            cand_scores.insert((t.task_id.clone(), t.repetition), t.total_score_or_zero());
        } else if t.candidate == baseline {
            base_scores.insert((t.task_id.clone(), t.repetition), t.total_score_or_zero());
        }
    }

    // Cluster by task_id to account for correlation between repetitions of same task
    let mut diffs_by_task = BTreeMap::<String, Vec<f64>>::new();

    for ((task_id, rep), cand_val) in cand_scores {
        if let Some(&base_val) = base_scores.get(&(task_id.clone(), rep)) {
            diffs_by_task
                .entry(task_id)
                .or_default()
                .push(cand_val - base_val);
        }
    }

    let matched_tasks = diffs_by_task.len();
    if matched_tasks == 0 {
        return Err(EvaluationError(format!(
            "no matched task trials found between '{candidate}' and '{baseline}'"
        )));
    }

    // Task cluster means
    let task_means: Vec<f64> = diffs_by_task
        .values()
        .map(|diffs| diffs.iter().sum::<f64>() / diffs.len() as f64)
        .collect();

    let t_count = task_means.len() as f64;
    let mean_difference = task_means.iter().sum::<f64>() / t_count;

    let standard_error = if task_means.len() > 1 {
        let variance = task_means
            .iter()
            .map(|&m| (m - mean_difference).powi(2))
            .sum::<f64>()
            / (t_count - 1.0);
        (variance / t_count).sqrt()
    } else {
        0.0
    };

    // Critical value approx: t_crit ~ 2.0 for 95% confidence with cluster aggregation
    let t_crit = match matched_tasks {
        1 => 12.7,
        2 => 4.30,
        3 => 3.18,
        4 => 2.78,
        5 => 2.57,
        _ => 2.04,
    };
    let margin = t_crit * standard_error;
    let confidence_interval_95 = (mean_difference - margin, mean_difference + margin);
    let statistically_significant_win = confidence_interval_95.0 > 0.0;

    Ok(PairedComparison {
        candidate: candidate.to_string(),
        baseline: baseline.to_string(),
        matched_tasks,
        mean_difference,
        standard_error,
        confidence_interval_95,
        statistically_significant_win,
    })
}

/// Evaluate candidate against baseline across predeclared recommendation gates.
pub fn evaluate_recommendation(
    candidate: &CandidateAggregateReport,
    baseline: &CandidateAggregateReport,
    paired: &PairedComparison,
    config: &RecommendationGatesConfig,
) -> RecommendationReport {
    let mut gate_evaluations = Vec::new();
    let mut summary_reasons = Vec::new();
    let mut has_rejection = false;
    let mut is_inconclusive = false;

    // Gate 1: Adequate Evidence and Score Coverage
    let score_cov_ok = candidate.score_coverage >= config.min_score_coverage
        && baseline.score_coverage >= config.min_score_coverage;
    gate_evaluations.push(GateCheckEvaluation {
        gate_name: "score_coverage".into(),
        passed: score_cov_ok,
        detail: format!(
            "candidate: {:.1}%, baseline: {:.1}% (required: {:.1}%)",
            candidate.score_coverage * 100.0,
            baseline.score_coverage * 100.0,
            config.min_score_coverage * 100.0
        ),
    });
    if !score_cov_ok {
        is_inconclusive = true;
        summary_reasons.push(format!(
            "insufficient score coverage: candidate {:.1}%, baseline {:.1}%",
            candidate.score_coverage * 100.0,
            baseline.score_coverage * 100.0
        ));
    }

    // Gate 2: Usage Accounting Coverage
    let usage_cov_ok = candidate.usage_coverage >= config.min_usage_coverage
        && baseline.usage_coverage >= config.min_usage_coverage;
    gate_evaluations.push(GateCheckEvaluation {
        gate_name: "usage_coverage".into(),
        passed: usage_cov_ok,
        detail: format!(
            "candidate: {:.1}%, baseline: {:.1}% (required: {:.1}%)",
            candidate.usage_coverage * 100.0,
            baseline.usage_coverage * 100.0,
            config.min_usage_coverage * 100.0
        ),
    });
    if !usage_cov_ok {
        is_inconclusive = true;
        summary_reasons.push(format!(
            "insufficient usage coverage: candidate {:.1}%, baseline {:.1}%",
            candidate.usage_coverage * 100.0,
            baseline.usage_coverage * 100.0
        ));
    }

    // When evidence coverage is insufficient, return Inconclusive immediately
    // without evaluating downstream success floors on incomplete trials.
    if is_inconclusive {
        return RecommendationReport {
            candidate: candidate.candidate.clone(),
            baseline: baseline.candidate.clone(),
            decision: RecommendationDecision::Inconclusive,
            overall_passed: false,
            gate_evaluations,
            summary_reasons,
        };
    }

    // Gate 3: Overall Success Floor
    let overall_pass_ok = candidate.required_check_pass_rate >= config.overall_success_floor;
    gate_evaluations.push(GateCheckEvaluation {
        gate_name: "overall_success_floor".into(),
        passed: overall_pass_ok,
        detail: format!(
            "candidate pass rate: {:.1}% (floor: {:.1}%)",
            candidate.required_check_pass_rate * 100.0,
            config.overall_success_floor * 100.0
        ),
    });
    if !overall_pass_ok {
        has_rejection = true;
        summary_reasons.push(format!(
            "candidate failed overall success floor: {:.1}% < {:.1}%",
            candidate.required_check_pass_rate * 100.0,
            config.overall_success_floor * 100.0
        ));
    }

    // Gate 4: Critical Categories Success Floors
    for crit_cat in &config.critical_categories {
        let cat_score = candidate
            .category_scores
            .get(crit_cat)
            .copied()
            .unwrap_or(0.0);
        let crit_ok = (cat_score / 100.0) >= config.critical_category_success_floor;
        gate_evaluations.push(GateCheckEvaluation {
            gate_name: format!("critical_category_floor:{crit_cat}"),
            passed: crit_ok,
            detail: format!(
                "category '{crit_cat}' score: {cat_score:.1} (floor: {:.1})",
                config.critical_category_success_floor * 100.0
            ),
        });
        if !crit_ok {
            has_rejection = true;
            summary_reasons.push(format!(
                "critical category '{crit_cat}' fell below success floor ({cat_score:.1} < {:.1})",
                config.critical_category_success_floor * 100.0
            ));
        }
    }

    // Gate 5: No Quality Regression vs Baseline
    if config.require_no_quality_regression {
        let no_regression =
            candidate.required_check_pass_rate >= baseline.required_check_pass_rate - 1e-4;
        gate_evaluations.push(GateCheckEvaluation {
            gate_name: "no_quality_regression".into(),
            passed: no_regression,
            detail: format!(
                "candidate: {:.1}%, baseline: {:.1}%",
                candidate.required_check_pass_rate * 100.0,
                baseline.required_check_pass_rate * 100.0
            ),
        });
        if !no_regression {
            has_rejection = true;
            summary_reasons.push(format!(
                "quality regression: candidate pass rate ({:.1}%) is below baseline ({:.1}%)",
                candidate.required_check_pass_rate * 100.0,
                baseline.required_check_pass_rate * 100.0
            ));
        }
    }

    // Gate 6: Meaningful Score Improvement
    let cand_strat = candidate.strategy_score.unwrap_or(0.0);
    let base_strat = baseline.strategy_score.unwrap_or(0.0);
    let delta = cand_strat - base_strat;
    let delta_ok = delta >= config.min_score_improvement;

    gate_evaluations.push(GateCheckEvaluation {
        gate_name: "score_improvement".into(),
        passed: delta_ok,
        detail: format!(
            "candidate: {cand_strat:.2}, baseline: {base_strat:.2}, delta: {delta:+.2} (min: +{:.2})",
            config.min_score_improvement
        ),
    });
    if !delta_ok {
        has_rejection = true;
        summary_reasons.push(format!(
            "score improvement below threshold: {delta:+.2} < +{:.2}",
            config.min_score_improvement
        ));
    }

    // Gate 7: Statistical Significance
    if config.require_statistical_significance {
        let stat_ok = paired.statistically_significant_win;
        gate_evaluations.push(GateCheckEvaluation {
            gate_name: "statistical_significance".into(),
            passed: stat_ok,
            detail: format!(
                "95% CI: [{:.2}, {:.2}], SE: {:.2}",
                paired.confidence_interval_95.0,
                paired.confidence_interval_95.1,
                paired.standard_error
            ),
        });
        if !stat_ok {
            if paired.confidence_interval_95.1 <= 0.0 {
                has_rejection = true;
                summary_reasons
                    .push("paired difference is statistically significantly negative".into());
            } else {
                is_inconclusive = true;
                summary_reasons.push(format!(
                    "improvement not statistically significant (95% CI [{:.2}, {:.2}] overlaps zero)",
                    paired.confidence_interval_95.0, paired.confidence_interval_95.1
                ));
            }
        }
    }

    let decision = if has_rejection {
        RecommendationDecision::Reject
    } else if is_inconclusive {
        RecommendationDecision::Inconclusive
    } else {
        RecommendationDecision::Promote
    };

    let overall_passed = decision == RecommendationDecision::Promote;

    RecommendationReport {
        candidate: candidate.candidate.clone(),
        baseline: baseline.candidate.clone(),
        decision,
        overall_passed,
        gate_evaluations,
        summary_reasons,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{EvaluationIdentity, TaskScore, TaskScoreStatus};

    fn make_score(total: f64, passed: bool) -> TaskScore {
        TaskScore {
            status: TaskScoreStatus::Completed,
            quality: Some(if passed { 1.0 } else { 0.0 }),
            cost_efficiency: Some(0.8),
            time_efficiency: Some(0.8),
            total: Some(total),
            required_passed: Some(passed),
            budget_exceeded: false,
            evidence_ids: vec!["ev1".into()],
            evaluation_identity: EvaluationIdentity::new(1, "task", "prof"),
            failure_reason: None,
        }
    }

    fn sample_weights() -> CategoryWeights {
        CategoryWeights::new(BTreeMap::from([
            ("file_ops".into(), 0.5),
            ("code_fixes".into(), 0.5),
        ]))
        .unwrap()
    }

    #[test]
    fn weights_validation_enforces_positive_and_unit_sum() {
        assert!(CategoryWeights::equal_split(["a", "b"]).is_ok());
        assert!(CategoryWeights::equal_split([]).is_err());

        let bad_sum = CategoryWeights::new(BTreeMap::from([("a".into(), 0.8), ("b".into(), 0.8)]));
        assert!(bad_sum.is_err());

        let negative =
            CategoryWeights::new(BTreeMap::from([("a".into(), -0.2), ("b".into(), 1.2)]));
        assert!(negative.is_err());
    }

    #[test]
    fn aggregate_candidate_scores_handles_categories_and_failures() {
        let weights = sample_weights();
        let trials = vec![
            TaskTrialScore {
                candidate: "candA".into(),
                task_id: "t1".into(),
                category: "file_ops".into(),
                repetition: 0,
                score: Some(make_score(100.0, true)),
                cost: Some(0.10),
                wall_ms: Some(1000),
                active_ms: Some(900),
                has_usage: true,
            },
            TaskTrialScore {
                candidate: "candA".into(),
                task_id: "t2".into(),
                category: "code_fixes".into(),
                repetition: 0,
                score: Some(make_score(60.0, false)), // failed check
                cost: Some(0.20),
                wall_ms: Some(2000),
                active_ms: Some(1800),
                has_usage: true,
            },
        ];

        let reports = aggregate_candidate_scores(&trials, &weights).unwrap();
        let a = &reports["candA"];
        assert_eq!(a.total_trials, 2);
        assert_eq!(a.scored_trials, 2);
        assert_eq!(a.score_coverage, 1.0);
        assert_eq!(a.usage_coverage, 1.0);
        assert_eq!(a.required_check_pass_rate, 0.5);

        // file_ops = 100, code_fixes = 60 -> StrategyScore = 0.5*100 + 0.5*60 = 80
        assert_eq!(a.category_scores["file_ops"], 100.0);
        assert_eq!(a.category_scores["code_fixes"], 60.0);
        assert!((a.strategy_score.unwrap() - 80.0).abs() < 1e-9);

        // Total spend = 0.30, successes = 1 -> spend per success = 0.30
        assert!((a.total_spend - 0.30).abs() < 1e-9);
        assert!((a.spend_per_successful_task.unwrap() - 0.30).abs() < 1e-9);
    }

    #[test]
    fn zero_success_spend_is_none_never_zero() {
        let weights = sample_weights();
        let trials = vec![TaskTrialScore {
            candidate: "failCandidate".into(),
            task_id: "t1".into(),
            category: "file_ops".into(),
            repetition: 0,
            score: Some(make_score(0.0, false)),
            cost: Some(0.15),
            wall_ms: Some(500),
            active_ms: Some(400),
            has_usage: true,
        }];

        let reports = aggregate_candidate_scores(&trials, &weights).unwrap();
        let rep = &reports["failCandidate"];
        assert_eq!(rep.required_check_pass_rate, 0.0);
        assert_eq!(rep.total_spend, 0.15);
        assert_eq!(rep.spend_per_successful_task, None);
    }

    #[test]
    fn synthetic_gate_rejects_quality_regression() {
        let weights = sample_weights();
        let cand_trials = vec![
            TaskTrialScore {
                candidate: "candidate".into(),
                task_id: "t1".into(),
                category: "file_ops".into(),
                repetition: 0,
                score: Some(make_score(90.0, true)),
                cost: Some(0.01),
                wall_ms: Some(100),
                active_ms: Some(100),
                has_usage: true,
            },
            TaskTrialScore {
                candidate: "candidate".into(),
                task_id: "t2".into(),
                category: "code_fixes".into(),
                repetition: 0,
                score: Some(make_score(0.0, false)), // failed check
                cost: Some(0.01),
                wall_ms: Some(100),
                active_ms: Some(100),
                has_usage: true,
            },
        ];
        let base_trials = vec![
            TaskTrialScore {
                candidate: "baseline".into(),
                task_id: "t1".into(),
                category: "file_ops".into(),
                repetition: 0,
                score: Some(make_score(80.0, true)),
                cost: Some(0.10),
                wall_ms: Some(200),
                active_ms: Some(200),
                has_usage: true,
            },
            TaskTrialScore {
                candidate: "baseline".into(),
                task_id: "t2".into(),
                category: "code_fixes".into(),
                repetition: 0,
                score: Some(make_score(80.0, true)), // both passed
                cost: Some(0.10),
                wall_ms: Some(200),
                active_ms: Some(200),
                has_usage: true,
            },
        ];

        let mut all_trials = cand_trials;
        all_trials.extend(base_trials);

        let reports = aggregate_candidate_scores(&all_trials, &weights).unwrap();
        let paired = compute_paired_comparison("candidate", "baseline", &all_trials).unwrap();

        let config = RecommendationGatesConfig {
            overall_success_floor: 0.50,
            require_no_quality_regression: true,
            ..Default::default()
        };

        let report = evaluate_recommendation(
            &reports["candidate"],
            &reports["baseline"],
            &paired,
            &config,
        );
        assert_eq!(report.decision, RecommendationDecision::Reject);
        assert!(
            report
                .summary_reasons
                .iter()
                .any(|r| r.contains("quality regression"))
        );
    }

    #[test]
    fn synthetic_gate_rejects_easy_task_mix_skew() {
        let weights = sample_weights();
        // Candidate got 100 on file_ops, but 0 on critical code_fixes
        let cand_trials = vec![
            TaskTrialScore {
                candidate: "candidate".into(),
                task_id: "t1".into(),
                category: "file_ops".into(),
                repetition: 0,
                score: Some(make_score(100.0, true)),
                cost: Some(0.05),
                wall_ms: Some(100),
                active_ms: Some(100),
                has_usage: true,
            },
            TaskTrialScore {
                candidate: "candidate".into(),
                task_id: "t2".into(),
                category: "code_fixes".into(),
                repetition: 0,
                score: Some(make_score(20.0, false)),
                cost: Some(0.05),
                wall_ms: Some(100),
                active_ms: Some(100),
                has_usage: true,
            },
        ];
        let base_trials = vec![
            TaskTrialScore {
                candidate: "baseline".into(),
                task_id: "t1".into(),
                category: "file_ops".into(),
                repetition: 0,
                score: Some(make_score(70.0, true)),
                cost: Some(0.10),
                wall_ms: Some(200),
                active_ms: Some(200),
                has_usage: true,
            },
            TaskTrialScore {
                candidate: "baseline".into(),
                task_id: "t2".into(),
                category: "code_fixes".into(),
                repetition: 0,
                score: Some(make_score(70.0, true)),
                cost: Some(0.10),
                wall_ms: Some(200),
                active_ms: Some(200),
                has_usage: true,
            },
        ];

        let mut all_trials = cand_trials;
        all_trials.extend(base_trials);

        let reports = aggregate_candidate_scores(&all_trials, &weights).unwrap();
        let paired = compute_paired_comparison("candidate", "baseline", &all_trials).unwrap();

        let config = RecommendationGatesConfig {
            critical_categories: BTreeSet::from(["code_fixes".into()]),
            critical_category_success_floor: 0.70,
            ..Default::default()
        };

        let report = evaluate_recommendation(
            &reports["candidate"],
            &reports["baseline"],
            &paired,
            &config,
        );
        assert_eq!(report.decision, RecommendationDecision::Reject);
        assert!(
            report
                .summary_reasons
                .iter()
                .any(|r| r.contains("critical category 'code_fixes'"))
        );
    }

    #[test]
    fn synthetic_gate_returns_inconclusive_on_missing_evaluations() {
        let weights = sample_weights();
        // Candidate has missing usage and incomplete trial
        let cand_trials = vec![
            TaskTrialScore {
                candidate: "candidate".into(),
                task_id: "t1".into(),
                category: "file_ops".into(),
                repetition: 0,
                score: Some(make_score(95.0, true)),
                cost: Some(0.05),
                wall_ms: Some(100),
                active_ms: Some(100),
                has_usage: true,
            },
            TaskTrialScore {
                candidate: "candidate".into(),
                task_id: "t2".into(),
                category: "code_fixes".into(),
                repetition: 0,
                score: None, // Incomplete!
                cost: None,
                wall_ms: Some(100),
                active_ms: Some(100),
                has_usage: false, // Missing usage!
            },
        ];
        let base_trials = vec![
            TaskTrialScore {
                candidate: "baseline".into(),
                task_id: "t1".into(),
                category: "file_ops".into(),
                repetition: 0,
                score: Some(make_score(80.0, true)),
                cost: Some(0.10),
                wall_ms: Some(200),
                active_ms: Some(200),
                has_usage: true,
            },
            TaskTrialScore {
                candidate: "baseline".into(),
                task_id: "t2".into(),
                category: "code_fixes".into(),
                repetition: 0,
                score: Some(make_score(80.0, true)),
                cost: Some(0.10),
                wall_ms: Some(200),
                active_ms: Some(200),
                has_usage: true,
            },
        ];

        let mut all_trials = cand_trials;
        all_trials.extend(base_trials);

        let reports = aggregate_candidate_scores(&all_trials, &weights).unwrap();
        let paired = compute_paired_comparison("candidate", "baseline", &all_trials).unwrap();

        let config = RecommendationGatesConfig {
            min_score_coverage: 1.0,
            ..Default::default()
        };

        let report = evaluate_recommendation(
            &reports["candidate"],
            &reports["baseline"],
            &paired,
            &config,
        );
        assert_eq!(report.decision, RecommendationDecision::Inconclusive);
        assert!(
            report
                .summary_reasons
                .iter()
                .any(|r| r.contains("insufficient score coverage"))
        );
    }

    #[test]
    fn successful_candidate_is_promoted() {
        let weights = sample_weights();
        let mut cand_trials = Vec::new();
        let mut base_trials = Vec::new();

        for i in 0..6 {
            let task_id = format!("task_{i}");
            let cat = if i % 2 == 0 { "file_ops" } else { "code_fixes" };
            cand_trials.push(TaskTrialScore {
                candidate: "candidate".into(),
                task_id: task_id.clone(),
                category: cat.into(),
                repetition: 0,
                score: Some(make_score(95.0, true)),
                cost: Some(0.04),
                wall_ms: Some(150),
                active_ms: Some(140),
                has_usage: true,
            });
            base_trials.push(TaskTrialScore {
                candidate: "baseline".into(),
                task_id,
                category: cat.into(),
                repetition: 0,
                score: Some(make_score(80.0, true)),
                cost: Some(0.10),
                wall_ms: Some(300),
                active_ms: Some(280),
                has_usage: true,
            });
        }

        let mut all_trials = cand_trials;
        all_trials.extend(base_trials);

        let reports = aggregate_candidate_scores(&all_trials, &weights).unwrap();
        let paired = compute_paired_comparison("candidate", "baseline", &all_trials).unwrap();

        assert!(paired.mean_difference > 10.0);
        assert!(paired.statistically_significant_win);

        let config = RecommendationGatesConfig {
            min_score_improvement: 5.0,
            ..Default::default()
        };

        let report = evaluate_recommendation(
            &reports["candidate"],
            &reports["baseline"],
            &paired,
            &config,
        );
        assert_eq!(report.decision, RecommendationDecision::Promote);
        assert!(report.overall_passed);
        assert!(report.summary_reasons.is_empty());
    }
}
