use std::error::Error;
use std::fmt::{Display, Formatter};
use std::hint::black_box;
use std::process::Command;
use std::time::Instant;

use serde::{Deserialize, Serialize};

use crate::{
    Baseline, BenchmarkRun, ProjectionMetrics, SyntheticTraceConfig, SyntheticTraceGenerator,
};

/// Configuration for projector-only scaling measurements.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct ScalingConfig {
    pub target_event_counts: Vec<usize>,
    pub warmup_iterations: usize,
    pub measured_iterations: usize,
    pub synthetic: SyntheticTraceConfig,
    pub baselines: Vec<Baseline>,
}

impl Default for ScalingConfig {
    fn default() -> Self {
        Self {
            target_event_counts: vec![100, 1_000, 10_000, 100_000],
            warmup_iterations: 5,
            measured_iterations: 20,
            synthetic: SyntheticTraceConfig::default(),
            baselines: Baseline::default_suite(128).expect("default Tail-K is valid"),
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
    pub correctness_gates_passed: bool,
    pub metrics: ProjectionMetrics,
    pub median_projection_ns: u64,
    pub p95_projection_ns: u64,
    pub p99_projection_ns: u64,
    pub median_ns_per_source_event: u64,
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
                for _ in 0..config.warmup_iterations {
                    black_box(
                        baseline
                            .project_prevalidated(black_box(&trace))
                            .map_err(|error| ScalingError::new(error.to_string()))?,
                    );
                }

                let mut elapsed_ns = Vec::with_capacity(config.measured_iterations);
                for _ in 0..config.measured_iterations {
                    let started = Instant::now();
                    let projection = baseline
                        .project_prevalidated(black_box(&trace))
                        .map_err(|error| ScalingError::new(error.to_string()))?;
                    let elapsed = started.elapsed().as_nanos();
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
                    correctness_gates_passed: gate_run.correctness.passed(),
                    metrics: gate_run.metrics,
                    median_projection_ns,
                    p95_projection_ns: percentile(&elapsed_ns, 95),
                    p99_projection_ns: percentile(&elapsed_ns, 99),
                    median_ns_per_source_event: median_projection_ns
                        / trace.events.len().max(1) as u64,
                });
            }
        }

        Ok(ScalingReport {
            schema_version: "structure.short-memory.scaling/v1".to_owned(),
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
    let events_per_turn =
        4 + config.tool_calls_per_turn * (2 + config.command_output_chunks_per_tool);
    target_event_count
        .saturating_sub(1)
        .div_ceil(events_per_turn)
        .max(1)
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
        })
        .expect("scaling runs");

        assert_eq!(report.samples.len(), 10);
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
        assert!(
            report
                .samples
                .iter()
                .filter(|sample| sample.baseline.id() == "B1")
                .any(|sample| !sample.correctness_gates_passed)
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
