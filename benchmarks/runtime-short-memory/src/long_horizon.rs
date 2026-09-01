//! Frozen, auditable schedule and analysis for long-horizon paired trials.
//!
//! This module deliberately never invokes a provider.  It produces the
//! manifest that an external serial Harbor runner consumes and turns its
//! verifier results into a reproducible ledger and paired analysis.

use std::collections::{BTreeMap, BTreeSet};

use serde::{Deserialize, Serialize};

pub const LONG_HORIZON_MANIFEST_SCHEMA: &str = "structure.long-horizon/2026-08-v4";
const PRIMARY_REPETITIONS: usize = 5;
const MIN_ELIGIBLE_PAIRS: usize = 5;
const QUALITY_NON_INFERIORITY_MARGIN_BPS: usize = 1_000;
const MIN_COST_REDUCTION_BPS: i64 = 1_000;
const BOOTSTRAP_RESAMPLES: usize = 10_000;
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
    pub tasks: Vec<String>,
    pub trials: Vec<ScheduledTrial>,
}

impl LongHorizonManifest {
    pub fn qualification(seed: u64, provider: String, model: String) -> Self {
        Self::paired_schedule(
            QUALIFICATION_CANDIDATES
                .iter()
                .map(ToString::to_string)
                .collect(),
            2,
            ExperimentPhase::Qualification,
            seed,
            provider,
            model,
        )
    }

    pub fn paired_primary(tasks: Vec<String>, seed: u64, provider: String, model: String) -> Self {
        Self::paired_schedule(tasks, 5, ExperimentPhase::Primary, seed, provider, model)
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
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct TrialLedgerEntry {
    pub trial_id: String,
    pub verifier_passed: bool,
    pub infrastructure_failure: bool,
    pub length_truncated: bool,
    #[serde(default)]
    pub input_tokens: u64,
    #[serde(default)]
    pub cached_input_tokens: u64,
    pub uncached_input_tokens: u64,
    #[serde(default)]
    pub official_half_price_input_tokens: u64,
    pub cache_creation_input_tokens: u64,
    pub gc_quality_gate_passed: Option<bool>,
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
    pub geometric_mean_official_cost_ratio_fbgc_over_b0: Option<f64>,
    pub official_cost_bootstrap_ci95_lower: Option<f64>,
    pub official_cost_bootstrap_ci95_upper: Option<f64>,
    pub official_cost_improvement: bool,
    pub geometric_mean_total_input_ratio_fbgc_over_b0: Option<f64>,
}

pub fn summarize_primary(
    manifest: &LongHorizonManifest,
    ledger: &[TrialLedgerEntry],
) -> Result<PairedPrimarySummary, String> {
    manifest.validate()?;
    let observed: BTreeMap<_, _> = ledger
        .iter()
        .map(|entry| (entry.trial_id.as_str(), entry))
        .collect();
    let mut b0_passes = 0usize;
    let mut fbgc_passes = 0usize;
    let mut deltas = Vec::new();
    let mut ratios = Vec::new();
    let mut official_cost_ratios = Vec::new();
    let mut total_input_ratios = Vec::new();
    let mut mechanism_gate_passed = true;
    for task in &manifest.tasks {
        for block in 1..=PRIMARY_REPETITIONS {
            let trials: Vec<_> = manifest
                .trials
                .iter()
                .filter(|trial| {
                    trial.phase == ExperimentPhase::Primary
                        && trial.task == *task
                        && trial.block == block
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
                if b0.official_half_price_input_tokens > 0
                    && fbgc.official_half_price_input_tokens > 0
                {
                    official_cost_ratios.push(
                        fbgc.official_half_price_input_tokens as f64
                            / b0.official_half_price_input_tokens as f64,
                    );
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
    let official_cost_ratio = geometric_mean(&official_cost_ratios);
    let (official_cost_ci_lower, official_cost_ci_upper) =
        bootstrap_geometric_mean_ci95(&official_cost_ratios, 0x5052_4943_4535_3030);
    let official_cost_improvement = quality_gate_passed
        && mechanism_gate_passed
        && official_cost_ratios.len() >= MIN_ELIGIBLE_PAIRS
        && official_cost_ratio.is_some_and(|ratio| ratio <= 0.9)
        && official_cost_ci_upper.is_some_and(|upper| upper < 1.0);
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
        geometric_mean_official_cost_ratio_fbgc_over_b0: official_cost_ratio,
        official_cost_bootstrap_ci95_lower: official_cost_ci_lower,
        official_cost_bootstrap_ci95_upper: official_cost_ci_upper,
        official_cost_improvement,
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
        LongHorizonArm::Fbgc => "fbgc",
        LongHorizonArm::Capc => "capc",
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
        let manifest =
            LongHorizonManifest::paired_primary(vec!["task".into()], 42, "p".into(), "m".into());
        let ledger: Vec<_> = manifest
            .trials
            .iter()
            .map(|trial| TrialLedgerEntry {
                trial_id: trial.trial_id.clone(),
                verifier_passed: true,
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
                    LongHorizonArm::Capc => unreachable!(),
                },
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
            summary.geometric_mean_official_cost_ratio_fbgc_over_b0,
            Some(0.75)
        );
        assert!(summary.official_cost_improvement);
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
        assert!(!summary.official_cost_improvement);
    }
}
