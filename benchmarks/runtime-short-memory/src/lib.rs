//! Deterministic Tier-A benchmark harness for Runtime short memory.
//!
//! This crate owns benchmark schemas, synthetic traces, reference baselines,
//! and correctness gates. Production event-to-memory semantics remain owned by
//! `structure-runtime`.

mod baseline;
mod cli_comparison;
mod gates;
mod generator;
mod long_horizon;
mod scaling;
mod schema;
mod tier_b;

pub use baseline::{
    Baseline, BaselineError, BenchmarkProjection, CompactionProjectionMetrics, ProjectionBatch,
    ProjectionVisibility,
};
pub use cli_comparison::{
    CLI_COMPARISON_MANIFEST_SCHEMA, CLI_COMPARISON_REPORT_SCHEMA, CLI_COMPARISON_SUITE_SCHEMA,
    CliComparisonManifest, CliComparisonMode, CliComparisonPlan, CliComparisonSummary,
    CliMultiAgentSummary, CliPreflightReport, CliProgressDiagnostics, CliScenario, CliSuite,
    CliSurface, CliTrial, CliTrialReport, CliUsage, CliVerifierResult, StructureOpponentSummary,
    create_cli_comparison_manifest, preflight_cli_comparison, run_cli_trial,
    summarize_cli_comparison, summarize_cli_multi_agent,
};
pub use gates::{
    BenchmarkRun, BenchmarkRunError, CorrectnessGates, EvidenceRecallGate, GateCheck,
    ProjectionMetrics,
};
pub use generator::{GeneratorError, SyntheticTraceConfig, SyntheticTraceGenerator};
pub use long_horizon::{
    ArmComparisonSummary, CoreAblationSummary, ExperimentPhase, LONG_HORIZON_MANIFEST_SCHEMA,
    LongHorizonArm, LongHorizonManifest, PairedPrimarySummary, QUALIFICATION_CANDIDATES,
    QualificationSummary, ScheduledTrial, TrialLedgerEntry, summarize_core_ablation,
    summarize_primary, summarize_qualification,
};
pub use scaling::{
    ScalingConfig, ScalingEnvironment, ScalingError, ScalingReport, ScalingRunner, ScalingSample,
};
pub use schema::{
    EvidenceUnitOracle, SHORT_MEMORY_TRACE_SCHEMA_VERSION, ShortMemoryTrace, ToolRelationOracle,
    TraceLineageOracle, TraceOracle, TraceOrigin, TraceValidationError,
};
pub use tier_b::{
    ExpectedFile, FileOracleResult, FixtureFileProvider, ProviderCallObservation, ProviderRecorder,
    RecordingProvider, TIER_B_REPORT_SCHEMA_VERSION, TierBAggregate, TierBEnvironment, TierBError,
    TierBEvidenceLevel, TierBProviderMetadata, TierBReport, TierBRun, TierBSuiteConfig, TierBTask,
    TierBTaskChecks, run_tier_b_suite,
};
