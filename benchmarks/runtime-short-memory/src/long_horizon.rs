//! Frozen, auditable schedule and analysis for long-horizon paired trials.
//!
//! This module deliberately never invokes a provider.  It produces the
//! manifest that an external serial Harbor runner consumes and turns its
//! verifier results into a reproducible ledger and paired analysis.

use std::collections::{BTreeMap, BTreeSet};

use serde::{Deserialize, Serialize};

pub const LONG_HORIZON_MANIFEST_SCHEMA: &str = "structure.long-horizon/2026-08";
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
                let reversed = stable_seed(seed, task, block) & 1 == 1;
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
                        seed: stable_seed(seed, task, block * 2 + sequence_in_block),
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
            tasks,
            trials,
        }
    }

    pub fn validate(&self) -> Result<(), String> {
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
        Ok(())
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct TrialLedgerEntry {
    pub trial_id: String,
    pub verifier_passed: bool,
    pub infrastructure_failure: bool,
    pub length_truncated: bool,
    pub uncached_input_tokens: u64,
    pub cache_creation_input_tokens: u64,
    pub gc_quality_gate_passed: Option<bool>,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct PairedPrimarySummary {
    pub eligible_pairs: usize,
    pub b0_passes: usize,
    pub fbgc_passes: usize,
    pub quality_gate_passed: bool,
    pub mean_uncached_delta: Option<f64>,
    pub mean_uncached_reduction_bps: Option<i64>,
    pub cost_improvement: bool,
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
    for task in &manifest.tasks {
        for block in 1..=5 {
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
            }
        }
    }
    let quality_gate_passed = fbgc_passes + manifest.tasks.len() >= b0_passes;
    let mean_uncached_delta =
        (!deltas.is_empty()).then(|| deltas.iter().sum::<f64>() / deltas.len() as f64);
    let mean_b0 = (!deltas.is_empty()).then(|| {
        let mut total = 0u64;
        let mut count = 0u64;
        for trial in &manifest.trials {
            if trial.arm == LongHorizonArm::B0 {
                if let Some(entry) = observed.get(trial.trial_id.as_str()) {
                    if entry.verifier_passed && !entry.infrastructure_failure {
                        total += entry.uncached_input_tokens;
                        count += 1;
                    }
                }
            }
        }
        if count == 0 {
            0.0
        } else {
            total as f64 / count as f64
        }
    });
    let reduction = match (mean_uncached_delta, mean_b0) {
        (Some(delta), Some(base)) if base > 0.0 => Some((-delta / base * 10_000.0).round() as i64),
        _ => None,
    };
    let cost_improvement = quality_gate_passed
        && reduction.is_some_and(|bps| bps >= 1_000)
        && mean_uncached_delta.is_some_and(|delta| delta < 0.0);
    Ok(PairedPrimarySummary {
        eligible_pairs: deltas.len(),
        b0_passes,
        fbgc_passes,
        quality_gate_passed,
        mean_uncached_delta,
        mean_uncached_reduction_bps: reduction,
        cost_improvement,
    })
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
    }
}
