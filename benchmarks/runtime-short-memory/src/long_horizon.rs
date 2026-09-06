//! Frozen, auditable schedule and analysis for long-horizon paired trials.
//!
//! This module deliberately never invokes a provider.  It produces the
//! manifest that an external serial Harbor runner consumes and turns its
//! verifier results into a reproducible ledger and paired analysis.

use std::collections::{BTreeMap, BTreeSet};

use serde::{Deserialize, Serialize};
use structure_protocol::TerminalControllerPolicy;
use structure_runtime::PointerGcAdmissionPolicy;

pub const LONG_HORIZON_MANIFEST_SCHEMA: &str = "structure.long-horizon/2026-09-v6";
const PRIMARY_REPETITIONS: usize = 5;
const MIN_ELIGIBLE_PAIRS: usize = 5;
const QUALITY_NON_INFERIORITY_MARGIN_BPS: usize = 1_000;
const MIN_COST_REDUCTION_BPS: i64 = 1_000;
const BOOTSTRAP_RESAMPLES: usize = 10_000;
pub const QUALIFICATION_TASKS: [&str; 2] = ["build-cython-ext", "db-wal-recovery"];
pub const QUALIFICATION_CANDIDATES: [&str; 5] = [
    "db-wal-recovery",
    "build-cython-ext",
    "fix-code-vulnerability",
    "llm-inference-batching-scheduler",
    "custom-memory-heap-crash",
];

#[derive(Clone, Copy, Debug, Deserialize, Eq, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(rename_all = "UPPERCASE")]
pub enum LongHorizonArm {
    B0,
    B2,
    Pgc,
    Fbgc,
    Capc,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ExperimentPhase {
    Qualification,
    Primary,
    Replication,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct ScheduledTrial {
    pub trial_id: String,
    pub task: String,
    pub phase: ExperimentPhase,
    pub block: usize,
    pub sequence_in_block: usize,
    pub arm: LongHorizonArm,
    #[serde(default)]
    pub terminal_controller_policy: TerminalControllerPolicy,
    pub seed: u64,
    pub report_path: String,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct LongHorizonManifest {
    pub schema_version: String,
    pub provider: String,
    pub model: String,
    pub thinking_enabled: bool,
    pub max_output_tokens: u32,
    pub max_model_steps: usize,
    pub timeout_seconds: u64,
    pub checkpoint_batches: usize,
    pub compaction_effort: usize,
    pub continuation_probability_bps: u32,
    pub cached_input_cost_bps: u32,
    #[serde(default)]
    pub pointer_gc_admission_policy: PointerGcAdmissionPolicy,
    /// True only when the campaign freeze identifies a provider-authoritative
    /// conversion (for example Coding Plan points) for every trial.
    #[serde(default)]
    pub price_weighting_auditable: bool,
    #[serde(default)]
    pub price_weighting_source: Option<String>,
    pub tasks: Vec<String>,
    pub trials: Vec<ScheduledTrial>,
}

impl LongHorizonManifest {
    pub fn qualification(seed: u64, provider: String, model: String) -> Self {
        const CONDITIONS: [(LongHorizonArm, TerminalControllerPolicy); 4] = [
            (LongHorizonArm::B0, TerminalControllerPolicy::AdvisoryV18),
            (LongHorizonArm::Fbgc, TerminalControllerPolicy::AdvisoryV18),
            (
                LongHorizonArm::B0,
                TerminalControllerPolicy::TypedCompletionAutoV2,
            ),
            (
                LongHorizonArm::Fbgc,
                TerminalControllerPolicy::TypedCompletionAutoV2,
            ),
        ];
        const WILLIAMS: [[usize; 4]; 4] = [[0, 1, 3, 2], [1, 2, 0, 3], [2, 3, 1, 0], [3, 0, 2, 1]];
        let tasks: Vec<_> = QUALIFICATION_TASKS
            .iter()
            .map(ToString::to_string)
            .collect();
        let mut trials = Vec::new();
        for (task_index, task) in tasks.iter().enumerate() {
            for block in 0..2 {
                let block_seed = stable_seed(seed, task, block);
                for (position, condition_index) in
                    WILLIAMS[task_index * 2 + block].into_iter().enumerate()
                {
                    let (arm, terminal_controller_policy) = CONDITIONS[condition_index];
                    let controller = terminal_policy_name(terminal_controller_policy);
                    trials.push(ScheduledTrial {
                        trial_id: format!(
                            "qualification-{task}-b{:02}-{}-{controller}",
                            block + 1,
                            arm_name(arm)
                        ),
                        task: task.clone(),
                        phase: ExperimentPhase::Qualification,
                        block: block + 1,
                        sequence_in_block: position + 1,
                        arm,
                        terminal_controller_policy,
                        seed: block_seed,
                        report_path: format!(
                            "trials/qualification/{task}/block-{:02}/{}-{controller}.json",
                            block + 1,
                            arm_name(arm)
                        ),
                    });
                }
            }
        }
        let mut manifest = Self::base(provider, model, tasks, trials);
        manifest.pointer_gc_admission_policy = PointerGcAdmissionPolicy::MechanismQualification;
        manifest
    }

    pub fn paired_primary(tasks: Vec<String>, seed: u64, provider: String, model: String) -> Self {
        Self::paired_schedule(tasks, 5, ExperimentPhase::Primary, seed, provider, model)
    }

    /// Four-arm B0/B2/PGC/FBGC Williams schedule used by the formal GLM core
    /// campaign. Every task/block receives one shared declared seed.
    pub fn core_primary(tasks: Vec<String>, seed: u64, provider: String, model: String) -> Self {
        const ARMS: [LongHorizonArm; 4] = [
            LongHorizonArm::B0,
            LongHorizonArm::B2,
            LongHorizonArm::Pgc,
            LongHorizonArm::Fbgc,
        ];
        const WILLIAMS: [[usize; 4]; 4] = [[0, 1, 3, 2], [1, 2, 0, 3], [2, 3, 1, 0], [3, 0, 2, 1]];
        let mut trials = Vec::new();
        for (task_index, task) in tasks.iter().enumerate() {
            for block in 0..PRIMARY_REPETITIONS {
                let block_seed = stable_seed(seed, task, block);
                let order = WILLIAMS[(task_index * PRIMARY_REPETITIONS + block) % 4];
                for (position, arm_index) in order.into_iter().enumerate() {
                    let arm = ARMS[arm_index];
                    trials.push(ScheduledTrial {
                        trial_id: format!("primary-{task}-b{:02}-{}", block + 1, arm_name(arm)),
                        task: task.clone(),
                        phase: ExperimentPhase::Primary,
                        block: block + 1,
                        sequence_in_block: position + 1,
                        arm,
                        terminal_controller_policy: TerminalControllerPolicy::TypedCompletionAutoV2,
                        seed: block_seed,
                        report_path: format!(
                            "trials/primary/{task}/block-{:02}/{}.json",
                            block + 1,
                            arm_name(arm)
                        ),
                    });
                }
            }
        }
        Self::base(provider, model, tasks, trials)
    }

    pub fn cross_model_confirmation(
        tasks: Vec<String>,
        seed: u64,
        provider: String,
        model: String,
    ) -> Self {
        let mut manifest = Self::paired_schedule(
            tasks,
            5,
            ExperimentPhase::Replication,
            seed,
            provider,
            model,
        );
        for trial in &mut manifest.trials {
            trial.terminal_controller_policy = TerminalControllerPolicy::TypedCompletionAutoV2;
        }
        manifest
    }

    pub fn anthropic_replication(tasks: Vec<String>, seed: u64, model: String) -> Self {
        let mut manifest = Self::paired_schedule(
            tasks,
            2,
            ExperimentPhase::Replication,
            seed,
            "anthropic_messages".to_owned(),
            model,
        );
        for task in &manifest.tasks {
            for block in 1..=2 {
                manifest.trials.push(ScheduledTrial {
                    trial_id: format!("replication-{task}-b{block:02}-capc"),
                    task: task.clone(),
                    phase: ExperimentPhase::Replication,
                    block,
                    sequence_in_block: 3,
                    arm: LongHorizonArm::Capc,
                    terminal_controller_policy: TerminalControllerPolicy::AdvisoryV18,
                    seed: stable_seed(seed, task, 100 + block),
                    report_path: format!("trials/replication/{task}/block-{block:02}/capc.json"),
                });
            }
        }
        manifest
    }

    fn paired_schedule(
        tasks: Vec<String>,
        repetitions: usize,
        phase: ExperimentPhase,
        seed: u64,
        provider: String,
        model: String,
    ) -> Self {
        let mut trials = Vec::new();
        for task in &tasks {
            for block in 0..repetitions {
                let pair_seed = stable_seed(seed, task, block);
                let reversed = pair_seed & 1 == 1;
                for (sequence_in_block, arm) in (if reversed {
                    [LongHorizonArm::Fbgc, LongHorizonArm::B0]
                } else {
                    [LongHorizonArm::B0, LongHorizonArm::Fbgc]
                })
                .into_iter()
                .enumerate()
                {
                    trials.push(ScheduledTrial {
                        trial_id: format!("{}-{task}-b{:02}-{arm:?}", phase_name(phase), block + 1)
                            .to_ascii_lowercase(),
                        task: task.clone(),
                        phase,
                        block: block + 1,
                        sequence_in_block: sequence_in_block + 1,
                        arm,
                        terminal_controller_policy: TerminalControllerPolicy::AdvisoryV18,
                        // Both treatments receive the same sampling seed. The
                        // seed may be ignored by a provider, but it must never
                        // be a hidden between-arm treatment when supported.
                        seed: pair_seed,
                        report_path: format!(
                            "trials/{}/{task}/block-{:02}/{}.json",
                            phase_name(phase),
                            block + 1,
                            arm_name(arm)
                        ),
                    });
                }
            }
        }
        Self::base(provider, model, tasks, trials)
    }

    fn base(
        provider: String,
        model: String,
        tasks: Vec<String>,
        trials: Vec<ScheduledTrial>,
    ) -> Self {
        Self {
            schema_version: LONG_HORIZON_MANIFEST_SCHEMA.to_owned(),
            provider,
            model,
            thinking_enabled: false,
            max_output_tokens: 8_192,
            max_model_steps: 128,
            timeout_seconds: 900,
            checkpoint_batches: 8,
            compaction_effort: 1,
            continuation_probability_bps: 7_500,
            cached_input_cost_bps: 0,
            pointer_gc_admission_policy: PointerGcAdmissionPolicy::Profitability,
            price_weighting_auditable: false,
            price_weighting_source: None,
            tasks,
            trials,
        }
    }

    pub fn validate(&self) -> Result<(), String> {
        if self.schema_version != LONG_HORIZON_MANIFEST_SCHEMA {
            return Err(format!(
                "unsupported long-horizon manifest schema {}",
                self.schema_version
            ));
        }
        if self.provider.is_empty() || self.model.is_empty() || self.tasks.is_empty() {
            return Err("provider, model, and at least one task are required".to_owned());
        }
        if self.max_output_tokens == 0
            || self.max_model_steps == 0
            || self.timeout_seconds == 0
            || self.checkpoint_batches == 0
            || self.compaction_effort == 0
            || self.continuation_probability_bps > 10_000
            || self.cached_input_cost_bps > 10_000
        {
            return Err("long-horizon execution limits are invalid".to_owned());
        }
        let unique_tasks: BTreeSet<_> = self.tasks.iter().collect();
        if unique_tasks.len() != self.tasks.len() {
            return Err("duplicate tasks are forbidden".to_owned());
        }
        let mut ids = BTreeSet::new();
        let mut paths = BTreeSet::new();
        for trial in &self.trials {
            if !ids.insert(&trial.trial_id) {
                return Err(format!("duplicate trial id {}", trial.trial_id));
            }
            if !paths.insert(&trial.report_path) {
                return Err(format!("duplicate report path {}", trial.report_path));
            }
        }
        for trial in self
            .trials
            .iter()
            .filter(|trial| trial.arm == LongHorizonArm::B0)
        {
            let counterpart = self.trials.iter().find(|candidate| {
                candidate.phase == trial.phase
                    && candidate.task == trial.task
                    && candidate.block == trial.block
                    && candidate.arm == LongHorizonArm::Fbgc
                    && candidate.terminal_controller_policy == trial.terminal_controller_policy
            });
            let Some(counterpart) = counterpart else {
                return Err(format!(
                    "missing FBGC counterpart for {} block {}",
                    trial.task, trial.block
                ));
            };
            if counterpart.seed != trial.seed {
                return Err(format!(
                    "paired seeds differ for {} block {}",
                    trial.task, trial.block
                ));
            }
        }
        Ok(())
    }

    /// Paid primary campaigns are not allowed to substitute public standard
    /// API token prices for an unaudited plan-credit conversion.
    pub fn validate_paid_stage(&self) -> Result<(), String> {
        self.validate()?;
        let is_formal_primary = self
            .trials
            .iter()
            .any(|trial| trial.phase == ExperimentPhase::Primary);
        if is_formal_primary
            && (!self.price_weighting_auditable
                || self
                    .price_weighting_source
                    .as_deref()
                    .is_none_or(str::is_empty))
        {
            return Err(
                "formal paid primary campaign requires an auditable price-weighting source"
                    .to_owned(),
            );
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct TrialLedgerEntry {
    pub trial_id: String,
    pub verifier_passed: bool,
    #[serde(default)]
    pub external_verifier_passed: bool,
    #[serde(default)]
    pub agent_terminal_success: bool,
    pub infrastructure_failure: bool,
    pub length_truncated: bool,
    #[serde(default)]
    pub input_tokens: u64,
    #[serde(default)]
    pub cached_input_tokens: u64,
    pub uncached_input_tokens: u64,
    /// Provider-authoritative economic units. `None` means the preregistered
    /// price-weighted primary endpoint is blocked, not estimated.
    #[serde(default)]
    pub price_weighted_input_units: Option<u64>,
    #[serde(default)]
    pub output_tokens: u64,
    #[serde(default)]
    pub reasoning_tokens: u64,
    #[serde(default)]
    pub peak_rss_bytes: u64,
    #[serde(default)]
    pub terminal_event_valid: Option<bool>,
    /// Retained only to read v4 ledgers. New campaigns never populate or use
    /// this standard-API half-cache approximation.
    #[serde(default)]
    pub official_half_price_input_tokens: u64,
    pub cache_creation_input_tokens: u64,
    pub gc_quality_gate_passed: Option<bool>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct QualificationSummary {
    pub scheduled_trials: usize,
    pub observed_trials: usize,
    pub advisory_b0_passes: usize,
    pub typed_b0_passes: usize,
    pub advisory_fbgc_passes: usize,
    pub typed_fbgc_passes: usize,
    pub candidate_non_inferior_in_both_memory_arms: bool,
    pub all_candidate_successes_have_valid_terminal_event: bool,
    pub differential_terminal_only_failure_absent: bool,
    pub fbgc_mechanism_gate_activated: bool,
    pub qualification_passed: bool,
}

pub fn summarize_qualification(
    manifest: &LongHorizonManifest,
    ledger: &[TrialLedgerEntry],
) -> Result<QualificationSummary, String> {
    manifest.validate()?;
    let scheduled = manifest
        .trials
        .iter()
        .filter(|trial| trial.phase == ExperimentPhase::Qualification)
        .collect::<Vec<_>>();
    let observed = ledger
        .iter()
        .map(|entry| (entry.trial_id.as_str(), entry))
        .collect::<BTreeMap<_, _>>();
    let pass_count = |arm, policy| {
        scheduled
            .iter()
            .filter(|trial| trial.arm == arm && trial.terminal_controller_policy == policy)
            .filter_map(|trial| observed.get(trial.trial_id.as_str()))
            // Qualification non-inferiority is defined on the hidden external
            // verifier. Terminal-control failures are evaluated separately.
            .filter(|entry| entry.external_verifier_passed && !entry.infrastructure_failure)
            .count()
    };
    let advisory_b0_passes = pass_count(LongHorizonArm::B0, TerminalControllerPolicy::AdvisoryV18);
    let typed_b0_passes = pass_count(
        LongHorizonArm::B0,
        TerminalControllerPolicy::TypedCompletionAutoV2,
    );
    let advisory_fbgc_passes =
        pass_count(LongHorizonArm::Fbgc, TerminalControllerPolicy::AdvisoryV18);
    let typed_fbgc_passes = pass_count(
        LongHorizonArm::Fbgc,
        TerminalControllerPolicy::TypedCompletionAutoV2,
    );
    let candidate_entries = scheduled
        .iter()
        .filter(|trial| {
            trial.terminal_controller_policy == TerminalControllerPolicy::TypedCompletionAutoV2
        })
        .filter_map(|trial| {
            observed
                .get(trial.trial_id.as_str())
                .map(|entry| (*trial, *entry))
        })
        .collect::<Vec<_>>();
    let all_candidate_successes_have_valid_terminal_event = candidate_entries
        .iter()
        .filter(|(_, entry)| entry.external_verifier_passed)
        .all(|(_, entry)| entry.terminal_event_valid == Some(true));
    let terminal_only_failures = |arm| {
        candidate_entries
            .iter()
            .filter(|(trial, _)| trial.arm == arm)
            .filter(|(_, entry)| {
                entry.external_verifier_passed && entry.terminal_event_valid != Some(true)
            })
            .count()
    };
    let differential_terminal_only_failure_absent =
        terminal_only_failures(LongHorizonArm::B0) == terminal_only_failures(LongHorizonArm::Fbgc);
    let fbgc_mechanism_gate_activated = candidate_entries
        .iter()
        .filter(|(trial, _)| trial.arm == LongHorizonArm::Fbgc)
        .all(|(_, entry)| entry.gc_quality_gate_passed == Some(true));
    let candidate_non_inferior_in_both_memory_arms =
        typed_b0_passes >= advisory_b0_passes && typed_fbgc_passes >= advisory_fbgc_passes;
    let observed_trials = scheduled
        .iter()
        .filter(|trial| observed.contains_key(trial.trial_id.as_str()))
        .count();
    let qualification_passed = observed_trials == scheduled.len()
        && candidate_non_inferior_in_both_memory_arms
        && all_candidate_successes_have_valid_terminal_event
        && differential_terminal_only_failure_absent
        && fbgc_mechanism_gate_activated;
    Ok(QualificationSummary {
        scheduled_trials: scheduled.len(),
        observed_trials,
        advisory_b0_passes,
        typed_b0_passes,
        advisory_fbgc_passes,
        typed_fbgc_passes,
        candidate_non_inferior_in_both_memory_arms,
        all_candidate_successes_have_valid_terminal_event,
        differential_terminal_only_failure_absent,
        fbgc_mechanism_gate_activated,
        qualification_passed,
    })
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct PairedPrimarySummary {
    pub eligible_pairs: usize,
    pub b0_passes: usize,
    pub fbgc_passes: usize,
    pub quality_gate_passed: bool,
    pub mechanism_gate_passed: bool,
    pub mean_uncached_delta: Option<f64>,
    pub mean_uncached_reduction_bps: Option<i64>,
    pub geometric_mean_uncached_ratio_fbgc_over_b0: Option<f64>,
    pub bootstrap_ci95_lower: Option<f64>,
    pub bootstrap_ci95_upper: Option<f64>,
    pub cost_improvement: bool,
    pub geometric_mean_price_weighted_ratio_fbgc_over_b0: Option<f64>,
    pub price_weighted_bootstrap_ci95_lower: Option<f64>,
    pub price_weighted_bootstrap_ci95_upper: Option<f64>,
    pub price_weighted_improvement: bool,
    pub geometric_mean_total_input_ratio_fbgc_over_b0: Option<f64>,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct ArmComparisonSummary {
    pub numerator_arm: LongHorizonArm,
    pub denominator_arm: LongHorizonArm,
    pub eligible_double_success_pairs: usize,
    pub numerator_passes: usize,
    pub denominator_passes: usize,
    pub quality_non_inferior: bool,
    pub geometric_mean_uncached_ratio: Option<f64>,
    pub bootstrap_ci95_lower: Option<f64>,
    pub bootstrap_ci95_upper: Option<f64>,
    pub raw_one_sided_bootstrap_p: Option<f64>,
    pub holm_adjusted_p: Option<f64>,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct CoreAblationSummary {
    pub fbgc_over_b0_primary: PairedPrimarySummary,
    pub holm_family: Vec<ArmComparisonSummary>,
    pub arm_descriptives: Vec<ArmDescriptiveSummary>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct ArmDescriptiveSummary {
    pub arm: LongHorizonArm,
    pub observed_trials: usize,
    pub verifier_passes: usize,
    pub external_verifier_passes: usize,
    pub infrastructure_failures: usize,
    pub length_truncations: usize,
    pub invalid_terminal_events: usize,
    pub fresh_input_tokens: u64,
    pub total_input_tokens: u64,
    pub cached_input_tokens: u64,
    pub price_weighted_input_units: Option<u64>,
    pub output_tokens: u64,
    pub reasoning_tokens: u64,
    pub peak_rss_bytes: u64,
}

pub fn summarize_core_ablation(
    manifest: &LongHorizonManifest,
    ledger: &[TrialLedgerEntry],
) -> Result<CoreAblationSummary, String> {
    manifest.validate()?;
    let observed = ledger
        .iter()
        .map(|entry| (entry.trial_id.as_str(), entry))
        .collect::<BTreeMap<_, _>>();
    let mut comparisons = vec![
        summarize_arm_pair(
            manifest,
            &observed,
            LongHorizonArm::B2,
            LongHorizonArm::B0,
            0x4232,
        )?,
        summarize_arm_pair(
            manifest,
            &observed,
            LongHorizonArm::Pgc,
            LongHorizonArm::B2,
            0x5047,
        )?,
        summarize_arm_pair(
            manifest,
            &observed,
            LongHorizonArm::Fbgc,
            LongHorizonArm::Pgc,
            0x4642,
        )?,
    ];
    let mut order = (0..comparisons.len()).collect::<Vec<_>>();
    order.sort_by(|left, right| {
        comparisons[*left]
            .raw_one_sided_bootstrap_p
            .unwrap_or(1.0)
            .total_cmp(&comparisons[*right].raw_one_sided_bootstrap_p.unwrap_or(1.0))
    });
    let family_size = order.len();
    let mut prior = 0.0_f64;
    for (rank, index) in order.into_iter().enumerate() {
        let adjusted = comparisons[index]
            .raw_one_sided_bootstrap_p
            .map(|raw| (raw * (family_size - rank) as f64).min(1.0).max(prior));
        if let Some(value) = adjusted {
            prior = value;
        }
        comparisons[index].holm_adjusted_p = adjusted;
    }
    Ok(CoreAblationSummary {
        fbgc_over_b0_primary: summarize_primary(manifest, ledger)?,
        holm_family: comparisons,
        arm_descriptives: [
            LongHorizonArm::B0,
            LongHorizonArm::B2,
            LongHorizonArm::Pgc,
            LongHorizonArm::Fbgc,
        ]
        .into_iter()
        .map(|arm| summarize_arm_descriptives(manifest, &observed, arm))
        .collect(),
    })
}

fn summarize_arm_descriptives(
    manifest: &LongHorizonManifest,
    observed: &BTreeMap<&str, &TrialLedgerEntry>,
    arm: LongHorizonArm,
) -> ArmDescriptiveSummary {
    let entries = manifest
        .trials
        .iter()
        .filter(|trial| trial.phase == ExperimentPhase::Primary && trial.arm == arm)
        .filter_map(|trial| observed.get(trial.trial_id.as_str()).copied())
        .collect::<Vec<_>>();
    let auditable_price_units = entries
        .iter()
        .map(|entry| entry.price_weighted_input_units)
        .collect::<Option<Vec<_>>>()
        .map(|values| values.into_iter().sum());
    ArmDescriptiveSummary {
        arm,
        observed_trials: entries.len(),
        verifier_passes: entries.iter().filter(|entry| entry.verifier_passed).count(),
        external_verifier_passes: entries
            .iter()
            .filter(|entry| entry.external_verifier_passed)
            .count(),
        infrastructure_failures: entries
            .iter()
            .filter(|entry| entry.infrastructure_failure)
            .count(),
        length_truncations: entries
            .iter()
            .filter(|entry| entry.length_truncated)
            .count(),
        invalid_terminal_events: entries
            .iter()
            .filter(|entry| entry.terminal_event_valid == Some(false))
            .count(),
        fresh_input_tokens: entries
            .iter()
            .map(|entry| entry.uncached_input_tokens)
            .sum(),
        total_input_tokens: entries.iter().map(|entry| entry.input_tokens).sum(),
        cached_input_tokens: entries.iter().map(|entry| entry.cached_input_tokens).sum(),
        price_weighted_input_units: auditable_price_units,
        output_tokens: entries.iter().map(|entry| entry.output_tokens).sum(),
        reasoning_tokens: entries.iter().map(|entry| entry.reasoning_tokens).sum(),
        peak_rss_bytes: entries
            .iter()
            .map(|entry| entry.peak_rss_bytes)
            .max()
            .unwrap_or(0),
    }
}

fn summarize_arm_pair(
    manifest: &LongHorizonManifest,
    observed: &BTreeMap<&str, &TrialLedgerEntry>,
    numerator_arm: LongHorizonArm,
    denominator_arm: LongHorizonArm,
    bootstrap_seed: u64,
) -> Result<ArmComparisonSummary, String> {
    let mut numerator_passes = 0usize;
    let mut denominator_passes = 0usize;
    let mut ratios = Vec::new();
    for task in &manifest.tasks {
        for block in 1..=PRIMARY_REPETITIONS {
            let find = |arm| {
                manifest
                    .trials
                    .iter()
                    .find(|trial| {
                        trial.phase == ExperimentPhase::Primary
                            && trial.task == *task
                            && trial.block == block
                            && trial.arm == arm
                    })
                    .and_then(|trial| observed.get(trial.trial_id.as_str()).copied())
            };
            let numerator = find(numerator_arm).ok_or_else(|| {
                format!("missing {numerator_arm:?} trial for {task} block {block}")
            })?;
            let denominator = find(denominator_arm).ok_or_else(|| {
                format!("missing {denominator_arm:?} trial for {task} block {block}")
            })?;
            numerator_passes += usize::from(numerator.verifier_passed);
            denominator_passes += usize::from(denominator.verifier_passed);
            if numerator.verifier_passed
                && denominator.verifier_passed
                && !numerator.infrastructure_failure
                && !denominator.infrastructure_failure
                && numerator.uncached_input_tokens > 0
                && denominator.uncached_input_tokens > 0
            {
                ratios.push(
                    numerator.uncached_input_tokens as f64
                        / denominator.uncached_input_tokens as f64,
                );
            }
        }
    }
    let attempts = manifest.tasks.len() * PRIMARY_REPETITIONS;
    let quality_non_inferior = numerator_passes * 10_000
        + QUALITY_NON_INFERIORITY_MARGIN_BPS * attempts
        >= denominator_passes * 10_000;
    let (bootstrap_ci95_lower, bootstrap_ci95_upper) =
        bootstrap_geometric_mean_ci95(&ratios, bootstrap_seed);
    Ok(ArmComparisonSummary {
        numerator_arm,
        denominator_arm,
        eligible_double_success_pairs: ratios.len(),
        numerator_passes,
        denominator_passes,
        quality_non_inferior,
        geometric_mean_uncached_ratio: geometric_mean(&ratios),
        bootstrap_ci95_lower,
        bootstrap_ci95_upper,
        raw_one_sided_bootstrap_p: bootstrap_one_sided_p(&ratios, bootstrap_seed ^ 0x5056),
        holm_adjusted_p: None,
    })
}

fn bootstrap_one_sided_p(values: &[f64], mut state: u64) -> Option<f64> {
    if values.is_empty() {
        return None;
    }
    let mut null_or_worse = 0usize;
    for _ in 0..BOOTSTRAP_RESAMPLES {
        let mut log_sum = 0.0;
        for _ in 0..values.len() {
            state ^= state << 13;
            state ^= state >> 7;
            state ^= state << 17;
            log_sum += values[(state as usize) % values.len()].ln();
        }
        null_or_worse += usize::from((log_sum / values.len() as f64).exp() >= 1.0);
    }
    Some((null_or_worse + 1) as f64 / (BOOTSTRAP_RESAMPLES + 1) as f64)
}

pub fn summarize_primary(
    manifest: &LongHorizonManifest,
    ledger: &[TrialLedgerEntry],
) -> Result<PairedPrimarySummary, String> {
    manifest.validate()?;
    let comparison_phase = if manifest
        .trials
        .iter()
        .any(|trial| trial.phase == ExperimentPhase::Primary)
    {
        ExperimentPhase::Primary
    } else {
        ExperimentPhase::Replication
    };
    let observed: BTreeMap<_, _> = ledger
        .iter()
        .map(|entry| (entry.trial_id.as_str(), entry))
        .collect();
    let mut b0_passes = 0usize;
    let mut fbgc_passes = 0usize;
    let mut deltas = Vec::new();
    let mut ratios = Vec::new();
    let mut price_weighted_ratios = Vec::new();
    let mut total_input_ratios = Vec::new();
    let mut mechanism_gate_passed = true;
    for task in &manifest.tasks {
        for block in 1..=PRIMARY_REPETITIONS {
            let trials: Vec<_> = manifest
                .trials
                .iter()
                .filter(|trial| {
                    trial.phase == comparison_phase && trial.task == *task && trial.block == block
                })
                .collect();
            let b0 = trials
                .iter()
                .find(|trial| trial.arm == LongHorizonArm::B0)
                .and_then(|trial| observed.get(trial.trial_id.as_str()))
                .ok_or_else(|| format!("missing B0 trial for {task} block {block}"))?;
            let fbgc = trials
                .iter()
                .find(|trial| trial.arm == LongHorizonArm::Fbgc)
                .and_then(|trial| observed.get(trial.trial_id.as_str()))
                .ok_or_else(|| format!("missing FBGC trial for {task} block {block}"))?;
            b0_passes += usize::from(b0.verifier_passed);
            fbgc_passes += usize::from(fbgc.verifier_passed);
            if b0.verifier_passed
                && fbgc.verifier_passed
                && !b0.infrastructure_failure
                && !fbgc.infrastructure_failure
            {
                deltas.push(fbgc.uncached_input_tokens as f64 - b0.uncached_input_tokens as f64);
                if b0.uncached_input_tokens > 0 && fbgc.uncached_input_tokens > 0 {
                    ratios
                        .push(fbgc.uncached_input_tokens as f64 / b0.uncached_input_tokens as f64);
                }
                if let (Some(b0_cost), Some(fbgc_cost)) = (
                    b0.price_weighted_input_units.filter(|value| *value > 0),
                    fbgc.price_weighted_input_units.filter(|value| *value > 0),
                ) {
                    price_weighted_ratios.push(fbgc_cost as f64 / b0_cost as f64);
                }
                if b0.input_tokens > 0 && fbgc.input_tokens > 0 {
                    total_input_ratios.push(fbgc.input_tokens as f64 / b0.input_tokens as f64);
                }
                mechanism_gate_passed &= fbgc.gc_quality_gate_passed == Some(true);
            }
        }
    }
    let scheduled_attempts = manifest.tasks.len() * PRIMARY_REPETITIONS;
    let quality_gate_passed = fbgc_passes * 10_000
        + QUALITY_NON_INFERIORITY_MARGIN_BPS * scheduled_attempts
        >= b0_passes * 10_000;
    let mean_uncached_delta =
        (!deltas.is_empty()).then(|| deltas.iter().sum::<f64>() / deltas.len() as f64);
    mechanism_gate_passed &= !deltas.is_empty();
    let geometric_mean_ratio = geometric_mean(&ratios);
    let (bootstrap_ci95_lower, bootstrap_ci95_upper) =
        bootstrap_geometric_mean_ci95(&ratios, 0x4642_4743_3230_3236);
    let reduction = geometric_mean_ratio.map(|ratio| ((1.0 - ratio) * 10_000.0).round() as i64);
    let cost_improvement = quality_gate_passed
        && mechanism_gate_passed
        && ratios.len() >= MIN_ELIGIBLE_PAIRS
        && reduction.is_some_and(|bps| bps >= MIN_COST_REDUCTION_BPS)
        && bootstrap_ci95_upper.is_some_and(|upper| upper < 1.0);
    let price_weighted_ratio = geometric_mean(&price_weighted_ratios);
    let (price_weighted_ci_lower, price_weighted_ci_upper) =
        bootstrap_geometric_mean_ci95(&price_weighted_ratios, 0x5052_4943_4535_3030);
    let price_weighted_improvement = manifest.price_weighting_auditable
        && quality_gate_passed
        && mechanism_gate_passed
        && price_weighted_ratios.len() >= MIN_ELIGIBLE_PAIRS
        && price_weighted_ratio.is_some_and(|ratio| ratio <= 0.9)
        && price_weighted_ci_upper.is_some_and(|upper| upper < 1.0);
    Ok(PairedPrimarySummary {
        eligible_pairs: deltas.len(),
        b0_passes,
        fbgc_passes,
        quality_gate_passed,
        mechanism_gate_passed,
        mean_uncached_delta,
        mean_uncached_reduction_bps: reduction,
        geometric_mean_uncached_ratio_fbgc_over_b0: geometric_mean_ratio,
        bootstrap_ci95_lower,
        bootstrap_ci95_upper,
        cost_improvement,
        geometric_mean_price_weighted_ratio_fbgc_over_b0: price_weighted_ratio,
        price_weighted_bootstrap_ci95_lower: price_weighted_ci_lower,
        price_weighted_bootstrap_ci95_upper: price_weighted_ci_upper,
        price_weighted_improvement,
        geometric_mean_total_input_ratio_fbgc_over_b0: geometric_mean(&total_input_ratios),
    })
}

fn geometric_mean(values: &[f64]) -> Option<f64> {
    (!values.is_empty())
        .then(|| (values.iter().map(|value| value.ln()).sum::<f64>() / values.len() as f64).exp())
}

fn bootstrap_geometric_mean_ci95(values: &[f64], mut state: u64) -> (Option<f64>, Option<f64>) {
    if values.is_empty() {
        return (None, None);
    }
    let mut estimates = Vec::with_capacity(BOOTSTRAP_RESAMPLES);
    for _ in 0..BOOTSTRAP_RESAMPLES {
        let mut log_sum = 0.0;
        for _ in 0..values.len() {
            state ^= state << 13;
            state ^= state >> 7;
            state ^= state << 17;
            log_sum += values[(state as usize) % values.len()].ln();
        }
        estimates.push((log_sum / values.len() as f64).exp());
    }
    estimates.sort_by(f64::total_cmp);
    let lower = estimates[BOOTSTRAP_RESAMPLES * 25 / 1_000];
    let upper = estimates[BOOTSTRAP_RESAMPLES * 975 / 1_000];
    (Some(lower), Some(upper))
}

fn arm_name(arm: LongHorizonArm) -> &'static str {
    match arm {
        LongHorizonArm::B0 => "b0",
        LongHorizonArm::B2 => "b2",
        LongHorizonArm::Pgc => "pgc",
        LongHorizonArm::Fbgc => "fbgc",
        LongHorizonArm::Capc => "capc",
    }
}

fn terminal_policy_name(policy: TerminalControllerPolicy) -> &'static str {
    match policy {
        TerminalControllerPolicy::AdvisoryV18 => "advisory-v18",
        TerminalControllerPolicy::TypedCompletionV1 => "typed-completion-v1",
        TerminalControllerPolicy::TypedCompletionAutoV1 => "typed-completion-auto-v1",
        TerminalControllerPolicy::TypedCompletionAutoV2 => "typed-completion-auto-v2",
    }
}
fn phase_name(phase: ExperimentPhase) -> &'static str {
    match phase {
        ExperimentPhase::Qualification => "qualification",
        ExperimentPhase::Primary => "primary",
        ExperimentPhase::Replication => "replication",
    }
}
fn stable_seed(seed: u64, task: &str, index: usize) -> u64 {
    task.bytes().fold(seed ^ index as u64, |state, byte| {
        state
            .wrapping_mul(1_099_511_628_211)
            .wrapping_add(byte as u64)
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn primary_schedule_is_stable_balanced_and_unique() {
        let manifest =
            LongHorizonManifest::paired_primary(vec!["task".into()], 42, "p".into(), "m".into());
        manifest.validate().expect("valid");
        assert_eq!(manifest.trials.len(), 10);
        assert_eq!(
            manifest
                .trials
                .iter()
                .filter(|trial| trial.arm == LongHorizonArm::B0)
                .count(),
            5
        );
        assert_eq!(
            manifest,
            LongHorizonManifest::paired_primary(vec!["task".into()], 42, "p".into(), "m".into())
        );
        for block in 1..=PRIMARY_REPETITIONS {
            let pair: Vec<_> = manifest
                .trials
                .iter()
                .filter(|trial| trial.block == block)
                .collect();
            assert_eq!(pair.len(), 2);
            assert_eq!(pair[0].seed, pair[1].seed);
        }
    }

    #[test]
    fn primary_summary_requires_significant_paired_mechanism_active_savings() {
        let mut manifest =
            LongHorizonManifest::paired_primary(vec!["task".into()], 42, "p".into(), "m".into());
        manifest.price_weighting_auditable = true;
        manifest.price_weighting_source = Some("test-authoritative-units".to_owned());
        let ledger: Vec<_> = manifest
            .trials
            .iter()
            .map(|trial| TrialLedgerEntry {
                trial_id: trial.trial_id.clone(),
                verifier_passed: true,
                external_verifier_passed: true,
                agent_terminal_success: true,
                infrastructure_failure: false,
                length_truncated: false,
                input_tokens: if trial.arm == LongHorizonArm::B0 {
                    300
                } else {
                    220
                },
                cached_input_tokens: if trial.arm == LongHorizonArm::B0 {
                    200
                } else {
                    140
                },
                uncached_input_tokens: match trial.arm {
                    LongHorizonArm::B0 => 100,
                    LongHorizonArm::Fbgc => 80,
                    LongHorizonArm::B2 | LongHorizonArm::Pgc | LongHorizonArm::Capc => {
                        unreachable!()
                    }
                },
                price_weighted_input_units: Some(if trial.arm == LongHorizonArm::B0 {
                    200
                } else {
                    150
                }),
                output_tokens: 0,
                reasoning_tokens: 0,
                peak_rss_bytes: 0,
                terminal_event_valid: Some(true),
                official_half_price_input_tokens: if trial.arm == LongHorizonArm::B0 {
                    200
                } else {
                    150
                },
                cache_creation_input_tokens: 0,
                gc_quality_gate_passed: (trial.arm == LongHorizonArm::Fbgc).then_some(true),
            })
            .collect();
        let summary = summarize_primary(&manifest, &ledger).expect("summary");
        assert_eq!(summary.eligible_pairs, PRIMARY_REPETITIONS);
        assert_eq!(
            summary.geometric_mean_uncached_ratio_fbgc_over_b0,
            Some(0.8)
        );
        assert_eq!(summary.bootstrap_ci95_lower, Some(0.8));
        assert_eq!(summary.bootstrap_ci95_upper, Some(0.8));
        assert!(summary.quality_gate_passed);
        assert!(summary.mechanism_gate_passed);
        assert!(summary.cost_improvement);
        assert_eq!(
            summary.geometric_mean_price_weighted_ratio_fbgc_over_b0,
            Some(0.75)
        );
        assert!(summary.price_weighted_improvement);
    }

    #[test]
    fn primary_summary_rejects_savings_without_mechanism_gate() {
        let manifest =
            LongHorizonManifest::paired_primary(vec!["task".into()], 42, "p".into(), "m".into());
        let ledger: Vec<_> = manifest
            .trials
            .iter()
            .map(|trial| TrialLedgerEntry {
                trial_id: trial.trial_id.clone(),
                verifier_passed: true,
                external_verifier_passed: true,
                agent_terminal_success: true,
                infrastructure_failure: false,
                length_truncated: false,
                input_tokens: if trial.arm == LongHorizonArm::B0 {
                    300
                } else {
                    220
                },
                cached_input_tokens: if trial.arm == LongHorizonArm::B0 {
                    200
                } else {
                    140
                },
                uncached_input_tokens: if trial.arm == LongHorizonArm::B0 {
                    100
                } else {
                    80
                },
                price_weighted_input_units: None,
                output_tokens: 0,
                reasoning_tokens: 0,
                peak_rss_bytes: 0,
                terminal_event_valid: Some(true),
                official_half_price_input_tokens: if trial.arm == LongHorizonArm::B0 {
                    200
                } else {
                    150
                },
                cache_creation_input_tokens: 0,
                gc_quality_gate_passed: (trial.arm == LongHorizonArm::Fbgc).then_some(false),
            })
            .collect();
        let summary = summarize_primary(&manifest, &ledger).expect("summary");
        assert!(!summary.mechanism_gate_passed);
        assert!(!summary.cost_improvement);
        assert!(!summary.price_weighted_improvement);
    }

    #[test]
    fn formal_primary_paid_gate_requires_auditable_weighting() {
        let mut manifest = LongHorizonManifest::core_primary(
            QUALIFICATION_CANDIDATES
                .iter()
                .map(ToString::to_string)
                .collect(),
            42,
            "glm_coding_plan".into(),
            "glm-5.3-flash".into(),
        );
        assert_eq!(manifest.trials.len(), 100);
        assert!(manifest.validate_paid_stage().is_err());
        manifest.price_weighting_auditable = true;
        manifest.price_weighting_source = Some("provider points ledger".to_owned());
        manifest
            .validate_paid_stage()
            .expect("auditable primary starts");
    }

    #[test]
    fn qualification_and_cross_model_schedules_have_frozen_sizes() {
        let qualification = LongHorizonManifest::qualification(
            42,
            "glm_coding_plan".into(),
            "glm-5.3-flash".into(),
        );
        assert_eq!(qualification.trials.len(), 16);
        assert!(qualification.trials.iter().any(|trial| {
            trial.terminal_controller_policy == TerminalControllerPolicy::TypedCompletionAutoV2
        }));
        qualification.validate().expect("qualification is valid");

        let replication = LongHorizonManifest::cross_model_confirmation(
            QUALIFICATION_CANDIDATES
                .iter()
                .map(ToString::to_string)
                .collect(),
            42,
            "deepseek_responses".into(),
            "deepseek-v4-flash".into(),
        );
        assert_eq!(replication.trials.len(), 50);
        assert!(replication.trials.iter().all(|trial| {
            trial.terminal_controller_policy == TerminalControllerPolicy::TypedCompletionAutoV2
        }));
        replication.validate().expect("replication is valid");
    }
}
