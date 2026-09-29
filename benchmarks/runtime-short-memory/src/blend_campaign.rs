//! Frozen paired schedule and summary schema for model-routing experiments.

use std::collections::{BTreeMap, BTreeSet};

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

pub const BLEND_CAMPAIGN_SCHEMA: &str = "structure.blend-campaign/2026-09-v1";

#[derive(Clone, Copy, Debug, Deserialize, Eq, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum BlendArm {
    FixedStrong,
    FixedInexpensive,
    FixedPhase,
    StateAdaptive,
}

impl BlendArm {
    const ALL: [Self; 4] = [
        Self::FixedStrong,
        Self::FixedInexpensive,
        Self::FixedPhase,
        Self::StateAdaptive,
    ];
    fn name(self) -> &'static str {
        match self {
            Self::FixedStrong => "fixed_strong",
            Self::FixedInexpensive => "fixed_inexpensive",
            Self::FixedPhase => "fixed_phase",
            Self::StateAdaptive => "state_adaptive",
        }
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct BlendTask {
    pub id: String,
    pub stratum: String,
    pub continuity_challenge: bool,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct BlendTrial {
    pub trial_id: String,
    pub task_id: String,
    pub stratum: String,
    pub block: usize,
    pub sequence_in_block: usize,
    pub arm: BlendArm,
    pub seed: u64,
    pub continuity_challenge: bool,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct BlendCampaignManifest {
    pub schema: String,
    pub seed: u64,
    pub strong_model: String,
    pub inexpensive_model: String,
    pub fixed_phase_policy: String,
    pub adaptive_policy: String,
    pub pricing_version: String,
    pub success_oracle_version: String,
    pub repetitions: usize,
    pub tasks: Vec<BlendTask>,
    pub trials: Vec<BlendTrial>,
}

impl BlendCampaignManifest {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        seed: u64,
        strong_model: String,
        inexpensive_model: String,
        fixed_phase_policy: String,
        adaptive_policy: String,
        pricing_version: String,
        success_oracle_version: String,
        repetitions: usize,
        tasks: Vec<BlendTask>,
    ) -> Result<Self, String> {
        if repetitions == 0 || tasks.is_empty() {
            return Err("Blend campaign needs tasks and at least one repetition".to_owned());
        }
        for value in [
            &strong_model,
            &inexpensive_model,
            &fixed_phase_policy,
            &adaptive_policy,
            &pricing_version,
            &success_oracle_version,
        ] {
            if value.trim().is_empty() {
                return Err("Blend campaign provenance fields must not be empty".to_owned());
            }
        }
        let mut ids = BTreeSet::new();
        if tasks.iter().any(|task| {
            task.id.trim().is_empty() || task.stratum.trim().is_empty() || !ids.insert(&task.id)
        }) {
            return Err("Blend task IDs must be unique and task strata non-empty".to_owned());
        }
        let mut trials = Vec::new();
        for task in &tasks {
            for block in 0..repetitions {
                let block_seed = stable_seed(seed, &task.id, block);
                let mut arms = BlendArm::ALL;
                deterministic_shuffle(&mut arms, block_seed);
                for (position, arm) in arms.into_iter().enumerate() {
                    trials.push(BlendTrial {
                        trial_id: format!("{}-b{:03}-{}", task.id, block + 1, arm.name()),
                        task_id: task.id.clone(),
                        stratum: task.stratum.clone(),
                        block,
                        sequence_in_block: position,
                        arm,
                        seed: block_seed,
                        continuity_challenge: task.continuity_challenge,
                    });
                }
            }
        }
        Ok(Self {
            schema: BLEND_CAMPAIGN_SCHEMA.to_owned(),
            seed,
            strong_model,
            inexpensive_model,
            fixed_phase_policy,
            adaptive_policy,
            pricing_version,
            success_oracle_version,
            repetitions,
            tasks,
            trials,
        })
    }

    pub fn validate(&self) -> Result<(), String> {
        if self.schema != BLEND_CAMPAIGN_SCHEMA || self.repetitions == 0 || self.tasks.is_empty() {
            return Err("invalid Blend campaign schema or empty schedule".to_owned());
        }
        let expected = self.tasks.len() * self.repetitions * BlendArm::ALL.len();
        if self.trials.len() != expected {
            return Err(format!(
                "expected {expected} Blend trials, got {}",
                self.trials.len()
            ));
        }
        let mut ids = BTreeSet::new();
        if self.trials.iter().any(|trial| !ids.insert(&trial.trial_id)) {
            return Err("duplicate Blend trial ID".to_owned());
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct BlendTrialResult {
    pub trial_id: String,
    pub task_success: Option<bool>,
    pub billed_cost_micros: Option<u64>,
    pub latency_ms: Option<u64>,
    pub recovery_count: u32,
    pub safety_incidents: u32,
}

#[derive(Clone, Debug, Default, Deserialize, PartialEq, Serialize)]
pub struct BlendArmSummary {
    pub scheduled: usize,
    pub observed: usize,
    pub scored: usize,
    pub successes: usize,
    pub success_rate: Option<f64>,
    pub success_rate_95_wilson: Option<[f64; 2]>,
    pub cost_per_success_micros: Option<f64>,
    pub mean_latency_ms: Option<f64>,
    pub p95_latency_ms: Option<u64>,
    pub recovery_count: u64,
    pub safety_incidents: u64,
}

#[derive(Clone, Debug, Default, Deserialize, PartialEq, Serialize)]
pub struct BlendCampaignSummary {
    pub arms: BTreeMap<BlendArm, BlendArmSummary>,
    pub by_stratum: BTreeMap<String, BTreeMap<BlendArm, BlendArmSummary>>,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum MemoryEvidenceCondition {
    BaselineProjection,
    EvidenceEnhancedProjection,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct MemoryEvidenceTrial {
    pub trial_id: String,
    pub pair_id: String,
    pub task_id: String,
    pub stratum: String,
    pub condition: MemoryEvidenceCondition,
    pub sequence_in_pair: usize,
    pub seed: u64,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct MemoryEvidenceManifest {
    pub schema: String,
    pub seed: u64,
    pub fixed_model: String,
    pub fixed_routing_policy: String,
    pub baseline_memory_policy: String,
    pub enhanced_memory_policy: String,
    pub success_oracle_version: String,
    pub evidence_oracle_version: String,
    pub repetitions: usize,
    pub tasks: Vec<BlendTask>,
    pub trials: Vec<MemoryEvidenceTrial>,
}

impl MemoryEvidenceManifest {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        seed: u64,
        fixed_model: String,
        fixed_routing_policy: String,
        baseline_memory_policy: String,
        enhanced_memory_policy: String,
        success_oracle_version: String,
        evidence_oracle_version: String,
        repetitions: usize,
        tasks: Vec<BlendTask>,
    ) -> Result<Self, String> {
        if repetitions == 0 || tasks.is_empty() {
            return Err("memory evidence campaign needs tasks and repetitions".to_owned());
        }
        for value in [
            &fixed_model,
            &fixed_routing_policy,
            &baseline_memory_policy,
            &enhanced_memory_policy,
            &success_oracle_version,
            &evidence_oracle_version,
        ] {
            if value.trim().is_empty() {
                return Err("memory evidence provenance fields must not be empty".to_owned());
            }
        }
        let mut ids = BTreeSet::new();
        if tasks.iter().any(|task| {
            task.id.trim().is_empty() || task.stratum.trim().is_empty() || !ids.insert(&task.id)
        }) {
            return Err("memory evidence task IDs must be unique with non-empty strata".to_owned());
        }
        let conditions = [
            MemoryEvidenceCondition::BaselineProjection,
            MemoryEvidenceCondition::EvidenceEnhancedProjection,
        ];
        let mut trials = Vec::new();
        for task in &tasks {
            for block in 0..repetitions {
                let pair_id = format!("{}-b{:03}", task.id, block + 1);
                let block_seed = stable_seed(seed, &task.id, block);
                let mut ordered = conditions;
                deterministic_shuffle(&mut ordered, block_seed);
                for (sequence, condition) in ordered.into_iter().enumerate() {
                    let condition_name = match condition {
                        MemoryEvidenceCondition::BaselineProjection => "baseline",
                        MemoryEvidenceCondition::EvidenceEnhancedProjection => "evidence-enhanced",
                    };
                    trials.push(MemoryEvidenceTrial {
                        trial_id: format!("{pair_id}-{condition_name}"),
                        pair_id: pair_id.clone(),
                        task_id: task.id.clone(),
                        stratum: task.stratum.clone(),
                        condition,
                        sequence_in_pair: sequence,
                        seed: block_seed,
                    });
                }
            }
        }
        Ok(Self {
            schema: "structure.memory-evidence/2026-09-v1".to_owned(),
            seed,
            fixed_model,
            fixed_routing_policy,
            baseline_memory_policy,
            enhanced_memory_policy,
            success_oracle_version,
            evidence_oracle_version,
            repetitions,
            tasks,
            trials,
        })
    }

    pub fn validate(&self) -> Result<(), String> {
        let expected = self.tasks.len() * self.repetitions * 2;
        if self.schema != "structure.memory-evidence/2026-09-v1"
            || self.repetitions == 0
            || self.trials.len() != expected
        {
            return Err("invalid memory evidence manifest or trial count".to_owned());
        }
        let mut ids = BTreeSet::new();
        if self.trials.iter().any(|trial| !ids.insert(&trial.trial_id)) {
            return Err("duplicate memory evidence trial ID".to_owned());
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct MemoryEvidenceTrialResult {
    pub trial_id: String,
    pub task_success: Option<bool>,
    pub evidence_recall_bps: Option<u32>,
    pub input_tokens: Option<u64>,
}

#[derive(Clone, Debug, Default, Deserialize, PartialEq, Serialize)]
pub struct MemoryEvidenceConditionSummary {
    pub observed: usize,
    pub scored: usize,
    pub successes: usize,
    pub success_rate: Option<f64>,
    pub success_rate_95_wilson: Option<[f64; 2]>,
    pub mean_evidence_recall_bps: Option<f64>,
    pub mean_input_tokens: Option<f64>,
}

#[derive(Clone, Debug, Default, Deserialize, PartialEq, Serialize)]
pub struct MemoryEvidenceSummary {
    pub conditions: BTreeMap<MemoryEvidenceCondition, MemoryEvidenceConditionSummary>,
}

pub fn summarize_memory_evidence(
    manifest: &MemoryEvidenceManifest,
    results: &[MemoryEvidenceTrialResult],
) -> Result<MemoryEvidenceSummary, String> {
    manifest.validate()?;
    let scheduled = manifest
        .trials
        .iter()
        .map(|trial| (trial.trial_id.as_str(), trial.condition))
        .collect::<BTreeMap<_, _>>();
    let mut groups = BTreeMap::<MemoryEvidenceCondition, Vec<&MemoryEvidenceTrialResult>>::new();
    let mut seen = BTreeSet::new();
    for result in results {
        if !seen.insert(result.trial_id.as_str()) {
            return Err(format!(
                "duplicate memory evidence result {}",
                result.trial_id
            ));
        }
        let condition = scheduled
            .get(result.trial_id.as_str())
            .ok_or_else(|| format!("unknown memory evidence result {}", result.trial_id))?;
        groups.entry(*condition).or_default().push(result);
    }
    let mut summary = MemoryEvidenceSummary::default();
    for condition in [
        MemoryEvidenceCondition::BaselineProjection,
        MemoryEvidenceCondition::EvidenceEnhancedProjection,
    ] {
        let rows = groups
            .get(&condition)
            .map(Vec::as_slice)
            .unwrap_or_default();
        let scores = rows
            .iter()
            .filter_map(|row| row.task_success)
            .collect::<Vec<_>>();
        let successes = scores.iter().filter(|success| **success).count();
        let recall = rows
            .iter()
            .filter_map(|row| row.evidence_recall_bps)
            .collect::<Vec<_>>();
        let tokens = rows
            .iter()
            .filter_map(|row| row.input_tokens)
            .collect::<Vec<_>>();
        summary.conditions.insert(
            condition,
            MemoryEvidenceConditionSummary {
                observed: rows.len(),
                scored: scores.len(),
                successes,
                success_rate: (!scores.is_empty()).then(|| successes as f64 / scores.len() as f64),
                success_rate_95_wilson: (!scores.is_empty())
                    .then(|| wilson_interval(successes, scores.len())),
                mean_evidence_recall_bps: mean_u32(&recall),
                mean_input_tokens: mean_u64(&tokens),
            },
        );
    }
    Ok(summary)
}

fn mean_u32(values: &[u32]) -> Option<f64> {
    (!values.is_empty()).then(|| {
        values.iter().map(|value| u64::from(*value)).sum::<u64>() as f64 / values.len() as f64
    })
}

fn mean_u64(values: &[u64]) -> Option<f64> {
    (!values.is_empty()).then(|| values.iter().sum::<u64>() as f64 / values.len() as f64)
}

pub fn summarize_blend_campaign(
    manifest: &BlendCampaignManifest,
    results: &[BlendTrialResult],
) -> Result<BlendCampaignSummary, String> {
    manifest.validate()?;
    let scheduled = manifest
        .trials
        .iter()
        .map(|trial| (trial.trial_id.as_str(), trial))
        .collect::<BTreeMap<_, _>>();
    let mut seen = BTreeSet::new();
    let mut groups = BTreeMap::<(Option<String>, BlendArm), Vec<&BlendTrialResult>>::new();
    for result in results {
        if !seen.insert(result.trial_id.as_str()) {
            return Err(format!("duplicate Blend result {}", result.trial_id));
        }
        let trial = scheduled
            .get(result.trial_id.as_str())
            .ok_or_else(|| format!("unknown Blend result {}", result.trial_id))?;
        groups.entry((None, trial.arm)).or_default().push(result);
        groups
            .entry((Some(trial.stratum.clone()), trial.arm))
            .or_default()
            .push(result);
    }
    let summarize = |arm: BlendArm, stratum: Option<&str>| {
        let scheduled_count = manifest
            .trials
            .iter()
            .filter(|trial| trial.arm == arm && stratum.is_none_or(|s| trial.stratum == s))
            .count();
        let rows = groups
            .get(&(stratum.map(str::to_owned), arm))
            .map(Vec::as_slice)
            .unwrap_or_default();
        let scored = rows
            .iter()
            .filter_map(|row| row.task_success)
            .collect::<Vec<_>>();
        let successes = scored.iter().filter(|success| **success).count();
        let costs = rows
            .iter()
            .filter_map(|row| row.billed_cost_micros)
            .sum::<u64>();
        let cost_count = rows
            .iter()
            .filter(|row| row.billed_cost_micros.is_some())
            .count();
        let latencies = rows
            .iter()
            .filter_map(|row| row.latency_ms)
            .collect::<Vec<_>>();
        let mut sorted_latencies = latencies.clone();
        sorted_latencies.sort_unstable();
        BlendArmSummary {
            scheduled: scheduled_count,
            observed: rows.len(),
            scored: scored.len(),
            successes,
            success_rate: (!scored.is_empty()).then(|| successes as f64 / scored.len() as f64),
            success_rate_95_wilson: (!scored.is_empty())
                .then(|| wilson_interval(successes, scored.len())),
            cost_per_success_micros: (successes > 0
                && cost_count == rows.len()
                && !rows.is_empty())
            .then(|| costs as f64 / successes as f64),
            mean_latency_ms: (!latencies.is_empty())
                .then(|| latencies.iter().sum::<u64>() as f64 / latencies.len() as f64),
            p95_latency_ms: (!sorted_latencies.is_empty()).then(|| {
                let index = (sorted_latencies.len() * 95)
                    .div_ceil(100)
                    .saturating_sub(1);
                sorted_latencies[index]
            }),
            recovery_count: rows.iter().map(|row| u64::from(row.recovery_count)).sum(),
            safety_incidents: rows.iter().map(|row| u64::from(row.safety_incidents)).sum(),
        }
    };
    let mut summary = BlendCampaignSummary::default();
    for arm in BlendArm::ALL {
        summary.arms.insert(arm, summarize(arm, None));
    }
    for stratum in manifest
        .tasks
        .iter()
        .map(|task| task.stratum.as_str())
        .collect::<BTreeSet<_>>()
    {
        summary.by_stratum.insert(
            stratum.to_owned(),
            BlendArm::ALL
                .into_iter()
                .map(|arm| (arm, summarize(arm, Some(stratum))))
                .collect(),
        );
    }
    Ok(summary)
}

fn wilson_interval(successes: usize, trials: usize) -> [f64; 2] {
    let n = trials as f64;
    let p = successes as f64 / n;
    let z = 1.959_963_984_540_054;
    let denominator = 1.0 + z * z / n;
    let center = (p + z * z / (2.0 * n)) / denominator;
    let margin = z * ((p * (1.0 - p) / n + z * z / (4.0 * n * n)).sqrt()) / denominator;
    [(center - margin).max(0.0), (center + margin).min(1.0)]
}

fn stable_seed(seed: u64, task: &str, block: usize) -> u64 {
    let mut hash = Sha256::new();
    hash.update(seed.to_le_bytes());
    hash.update(task.as_bytes());
    hash.update(block.to_le_bytes());
    u64::from_le_bytes(
        hash.finalize()[..8]
            .try_into()
            .expect("fixed digest length"),
    )
}

fn deterministic_shuffle<T>(values: &mut [T], mut state: u64) {
    for index in (1..values.len()).rev() {
        state ^= state << 13;
        state ^= state >> 7;
        state ^= state << 17;
        values.swap(index, (state as usize) % (index + 1));
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn campaign() -> BlendCampaignManifest {
        BlendCampaignManifest::new(
            42,
            "strong-v1".into(),
            "cheap-v1".into(),
            "phase-v1".into(),
            "adaptive-v1".into(),
            "price-v1".into(),
            "oracle-v1".into(),
            2,
            vec![
                BlendTask {
                    id: "coding-small".into(),
                    stratum: "coding".into(),
                    continuity_challenge: false,
                },
                BlendTask {
                    id: "coding-switch".into(),
                    stratum: "context-switch".into(),
                    continuity_challenge: true,
                },
            ],
        )
        .unwrap()
    }
    #[test]
    fn campaign_schedule_is_reproducible_and_paired() {
        let first = campaign();
        assert_eq!(first, campaign());
        first.validate().unwrap();
        assert_eq!(
            first.trials.len(),
            first.tasks.len() * 2 * BlendArm::ALL.len()
        );
        for task in &first.tasks {
            for block in 0..first.repetitions {
                let arms = first
                    .trials
                    .iter()
                    .filter(|trial| trial.task_id == task.id && trial.block == block)
                    .map(|trial| trial.arm)
                    .collect::<BTreeSet<_>>();
                assert_eq!(arms.len(), BlendArm::ALL.len());
            }
        }
    }
    #[test]
    fn summary_reports_oracle_uncertainty_and_context_strata() {
        let manifest = campaign();
        let results = manifest
            .trials
            .iter()
            .map(|trial| BlendTrialResult {
                trial_id: trial.trial_id.clone(),
                task_success: Some(trial.arm == BlendArm::FixedStrong),
                billed_cost_micros: Some(100),
                latency_ms: Some(1_000),
                recovery_count: 0,
                safety_incidents: 0,
            })
            .collect::<Vec<_>>();
        let summary = summarize_blend_campaign(&manifest, &results).unwrap();
        let strong = &summary.arms[&BlendArm::FixedStrong];
        assert_eq!(strong.successes, 4);
        assert_eq!(strong.cost_per_success_micros, Some(100.0));
        assert!(strong.success_rate_95_wilson.is_some());
        assert!(summary.by_stratum.contains_key("context-switch"));
    }

    #[test]
    fn memory_evidence_campaign_holds_model_and_router_fixed() {
        let manifest = MemoryEvidenceManifest::new(
            17,
            "model-v1".into(),
            "router-v1".into(),
            "projection-v1".into(),
            "evidence-v1".into(),
            "success-oracle-v1".into(),
            "recall-oracle-v1".into(),
            2,
            vec![BlendTask {
                id: "retrieval-task".into(),
                stratum: "long-context".into(),
                continuity_challenge: true,
            }],
        )
        .unwrap();
        manifest.validate().unwrap();
        assert_eq!(manifest.trials.len(), 4);
        assert_eq!(manifest.trials[0].seed, manifest.trials[1].seed);
        assert_eq!(manifest.fixed_routing_policy, "router-v1");

        let results = manifest
            .trials
            .iter()
            .map(|trial| MemoryEvidenceTrialResult {
                trial_id: trial.trial_id.clone(),
                task_success: Some(
                    trial.condition == MemoryEvidenceCondition::EvidenceEnhancedProjection,
                ),
                evidence_recall_bps: Some(match trial.condition {
                    MemoryEvidenceCondition::BaselineProjection => 6_000,
                    MemoryEvidenceCondition::EvidenceEnhancedProjection => 9_000,
                }),
                input_tokens: Some(2_000),
            })
            .collect::<Vec<_>>();
        let summary = summarize_memory_evidence(&manifest, &results).unwrap();
        assert_eq!(
            summary.conditions[&MemoryEvidenceCondition::EvidenceEnhancedProjection].successes,
            2
        );
        assert_eq!(
            summary.conditions[&MemoryEvidenceCondition::BaselineProjection]
                .mean_evidence_recall_bps,
            Some(6_000.0)
        );
    }
}
