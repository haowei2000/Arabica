use std::error::Error;
use std::fmt::{Display, Formatter};
use std::hint::black_box;
use std::process::Command;
use std::time::Instant;

use serde::{Deserialize, Serialize};

use crate::{
    Baseline, BenchmarkRun, CompactionProjectionMetrics, ProjectionMetrics, SyntheticTraceConfig,
    SyntheticTraceGenerator,
};

/// Configuration for projector-only scaling measurements.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct ScalingConfig {
    pub target_event_counts: Vec<usize>,
    pub warmup_iterations: usize,
    pub measured_iterations: usize,
    pub synthetic: SyntheticTraceConfig,
    pub baselines: Vec<Baseline>,
    /// Freeze a target-dependent trace shape that keeps at least several
    /// closed tool batches at small N without causing an inode storm at 100k.
    #[serde(default = "default_adaptive_chunking")]
    pub adaptive_chunking: bool,
}

impl Default for ScalingConfig {
    fn default() -> Self {
        Self {
            target_event_counts: vec![100, 1_000, 10_000, 100_000],
            warmup_iterations: 5,
            measured_iterations: 20,
            synthetic: SyntheticTraceConfig {
                single_run: true,
                ..SyntheticTraceConfig::default()
            },
            baselines: Baseline::default_suite(128).expect("default Tail-K is valid"),
            adaptive_chunking: true,
        }
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct ScalingReport {
    pub schema_version: String,
    pub environment: ScalingEnvironment,
    pub warmup_iterations: usize,
    pub measured_iterations: usize,
    pub samples: Vec<ScalingSample>,
}

impl ScalingReport {
    pub fn all_correctness_gates_passed(&self) -> bool {
        self.samples
            .iter()
            .all(|sample| sample.correctness_gates_passed)
    }

    /// B1 is an intentionally lossy control. Its window loss is reported but
    /// does not fail the preregistered deterministic campaign gate.
    pub fn required_correctness_gates_passed(&self) -> bool {
        self.samples
            .iter()
            .filter(|sample| sample.baseline.id() != "B1")
            .all(|sample| sample.correctness_gates_passed && sample.mechanism_gate_passed)
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct ScalingEnvironment {
    pub release_mode: bool,
    pub target_arch: String,
    pub target_os: String,
    pub rustc_version: Option<String>,
    pub git_revision: Option<String>,
    pub git_worktree_dirty: Option<bool>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct ScalingSample {
    pub baseline: Baseline,
    pub target_event_count: usize,
    pub actual_event_count: usize,
    pub generated_turn_count: usize,
    pub generated_command_output_chunks_per_tool: usize,
    pub correctness_gates_passed: bool,
    pub mechanism_gate_passed: bool,
    pub metrics: ProjectionMetrics,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub compaction: Option<CompactionProjectionMetrics>,
    pub median_projection_ns: u64,
    pub p95_projection_ns: u64,
    pub p99_projection_ns: u64,
    pub median_ns_per_source_event: u64,
    pub peak_serialized_projection_bytes: usize,
    /// Maximum resident-set observation while a measured projection was
    /// alive. This is process-wide and intentionally reported separately
    /// from the exact serialized projection size.
    pub peak_observed_process_rss_bytes: Option<u64>,
}

#[derive(Clone, Copy, Debug, Default)]
pub struct ScalingRunner;

impl ScalingRunner {
    pub fn run(config: &ScalingConfig) -> Result<ScalingReport, ScalingError> {
        validate(config)?;
        let mut samples =
            Vec::with_capacity(config.target_event_counts.len() * config.baselines.len());

        for target_event_count in &config.target_event_counts {
            let mut trace_config = config.synthetic.clone();
            if config.baselines.iter().any(|baseline| {
                matches!(
                    baseline,
                    Baseline::PointerGc { .. } | Baseline::FileBackedGc { .. }
                )
            }) {
                trace_config.single_run = true;
            }
            if config.adaptive_chunking {
                trace_config.command_output_chunks_per_tool = adaptive_chunks(*target_event_count);
            }
            trace_config.turn_count = turns_for_target(*target_event_count, &trace_config);
            trace_config.trace_id = format!(
                "{}-target-{}",
                config.synthetic.trace_id, target_event_count
            );
            let trace = SyntheticTraceGenerator::generate(&trace_config)
                .map_err(|error| ScalingError::new(error.to_string()))?;
            trace
                .validate()
                .map_err(|error| ScalingError::new(error.to_string()))?;

            for baseline in &config.baselines {
                let gate_run = BenchmarkRun::execute(&trace, baseline.clone())
                    .map_err(|error| ScalingError::new(error.to_string()))?;
                let mechanism_gate_passed =
                    gate_run
                        .projection
                        .compaction
                        .as_ref()
                        .is_none_or(|compaction| {
                            compaction
                                .admission
                                .as_ref()
                                .is_some_and(|item| item.admitted)
                                && compaction.archive_count > 0
                                && compaction.archive_idempotent
                                && compaction.archive_reopen_verified
                                && compaction.substitutive_transition
                        });
                for _ in 0..config.warmup_iterations {
                    black_box(
                        baseline
                            .project_prevalidated(black_box(&trace))
                            .map_err(|error| ScalingError::new(error.to_string()))?,
                    );
                }

                let mut elapsed_ns = Vec::with_capacity(config.measured_iterations);
                let mut peak_serialized_projection_bytes = 0usize;
                let mut peak_observed_process_rss_bytes = None;
                for _ in 0..config.measured_iterations {
                    let started = Instant::now();
                    let projection = baseline
                        .project_prevalidated(black_box(&trace))
                        .map_err(|error| ScalingError::new(error.to_string()))?;
                    let elapsed = started.elapsed().as_nanos();
                    peak_serialized_projection_bytes = peak_serialized_projection_bytes
                        .max(serde_json::to_vec(&projection).map_or(0, |bytes| bytes.len()));
                    if let Some(rss) = current_process_rss_bytes() {
                        peak_observed_process_rss_bytes = Some(
                            peak_observed_process_rss_bytes.map_or(rss, |peak: u64| peak.max(rss)),
                        );
                    }
                    black_box(projection);
                    elapsed_ns.push(saturating_u64(elapsed));
                }
                elapsed_ns.sort_unstable();
                let median_projection_ns = percentile(&elapsed_ns, 50);

                samples.push(ScalingSample {
                    baseline: baseline.clone(),
                    target_event_count: *target_event_count,
                    actual_event_count: trace.events.len(),
                    generated_turn_count: trace_config.turn_count,
                    generated_command_output_chunks_per_tool: trace_config
                        .command_output_chunks_per_tool,
                    correctness_gates_passed: gate_run.correctness.passed(),
                    mechanism_gate_passed,
                    metrics: gate_run.metrics,
                    compaction: gate_run.projection.compaction,
                    median_projection_ns,
                    p95_projection_ns: percentile(&elapsed_ns, 95),
                    p99_projection_ns: percentile(&elapsed_ns, 99),
                    median_ns_per_source_event: median_projection_ns
                        / trace.events.len().max(1) as u64,
                    peak_serialized_projection_bytes,
                    peak_observed_process_rss_bytes,
                });
            }
        }

        Ok(ScalingReport {
            schema_version: "structure.short-memory.scaling/v4".to_owned(),
            environment: ScalingEnvironment {
                release_mode: !cfg!(debug_assertions),
                target_arch: std::env::consts::ARCH.to_owned(),
                target_os: std::env::consts::OS.to_owned(),
                rustc_version: command_output("rustc", &["--version"]),
                git_revision: command_output("git", &["rev-parse", "HEAD"]),
                git_worktree_dirty: command_output("git", &["status", "--porcelain"])
                    .map(|output| !output.is_empty()),
            },
            warmup_iterations: config.warmup_iterations,
            measured_iterations: config.measured_iterations,
            samples,
        })
    }
}

const fn default_adaptive_chunking() -> bool {
    true
}

const fn adaptive_chunks(target_event_count: usize) -> usize {
    match target_event_count {
        0..=999 => 1,
        1_000..=9_999 => 8,
        10_000..=99_999 => 32,
        _ => 128,
    }
}

fn validate(config: &ScalingConfig) -> Result<(), ScalingError> {
    if config.target_event_counts.is_empty() || config.target_event_counts.contains(&0) {
        return Err(ScalingError::new(
            "target_event_counts must contain only positive values",
        ));
    }
    if config.measured_iterations == 0 {
        return Err(ScalingError::new(
            "measured_iterations must be greater than zero",
        ));
    }
    if config.baselines.is_empty() {
        return Err(ScalingError::new("at least one baseline is required"));
    }
    if config.synthetic.fork_after_turn.is_some() {
        return Err(ScalingError::new(
            "scaling traces do not support fork_after_turn; use fork fixtures for correctness",
        ));
    }
    Ok(())
}

fn turns_for_target(target_event_count: usize, config: &SyntheticTraceConfig) -> usize {
    let tool_events =
        (config.tool_calls_per_turn * (2 + config.command_output_chunks_per_tool)).max(1);
    if config.single_run {
        target_event_count
            .saturating_sub(4)
            .div_ceil(tool_events)
            .max(1)
    } else {
        target_event_count
            .saturating_sub(1)
            .div_ceil(4 + tool_events)
            .max(1)
    }
}

fn percentile(sorted: &[u64], percentile: usize) -> u64 {
    let rank = (sorted.len() * percentile)
        .div_ceil(100)
        .saturating_sub(1)
        .min(sorted.len() - 1);
    sorted[rank]
}

fn saturating_u64(value: u128) -> u64 {
    u64::try_from(value).unwrap_or(u64::MAX)
}

fn command_output(program: &str, arguments: &[&str]) -> Option<String> {
    let output = Command::new(program).args(arguments).output().ok()?;
    output
        .status
        .success()
        .then(|| String::from_utf8_lossy(&output.stdout).trim().to_owned())
}

fn current_process_rss_bytes() -> Option<u64> {
    let process_id = std::process::id().to_string();
    let output = Command::new("ps")
        .args(["-o", "rss=", "-p", &process_id])
        .output()
        .ok()?;
    output.status.success().then_some(())?;
    String::from_utf8_lossy(&output.stdout)
        .trim()
        .parse::<u64>()
        .ok()
        .map(|kibibytes| kibibytes.saturating_mul(1_024))
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ScalingError {
    message: String,
}

impl ScalingError {
    fn new(message: impl Into<String>) -> Self {
        Self {
            message: message.into(),
        }
    }
}

impl Display for ScalingError {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        self.message.fmt(formatter)
    }
}

impl Error for ScalingError {}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn scaling_reports_actual_counts_and_all_methods() {
        let report = ScalingRunner::run(&ScalingConfig {
            target_event_counts: vec![20, 50],
            warmup_iterations: 0,
            measured_iterations: 2,
            synthetic: SyntheticTraceConfig {
                tool_calls_per_turn: 1,
                command_output_chunks_per_tool: 0,
                payload_chars: 16,
                ..SyntheticTraceConfig::default()
            },
            baselines: Baseline::default_suite(8).expect("suite is valid"),
            adaptive_chunking: false,
        })
        .expect("scaling runs");

        assert_eq!(report.samples.len(), 22);
        assert!(
            report
                .samples
                .iter()
                .all(|sample| sample.actual_event_count >= sample.target_event_count)
        );
        assert!(
            report
                .samples
                .iter()
                .all(|sample| sample.median_projection_ns > 0)
        );
        assert!(
            report
                .samples
                .iter()
                .filter(|sample| sample.baseline.id() == "S")
                .all(|sample| sample.correctness_gates_passed)
        );
        assert_eq!(
            report
                .samples
                .iter()
                .filter(|sample| sample.baseline.id() == "B1")
                .count(),
            2
        );
    }

    #[test]
    fn scaling_rejects_zero_iterations() {
        let error = ScalingRunner::run(&ScalingConfig {
            measured_iterations: 0,
            ..ScalingConfig::default()
        })
        .expect_err("zero iterations are invalid");

        assert!(error.to_string().contains("measured_iterations"));
    }
}
