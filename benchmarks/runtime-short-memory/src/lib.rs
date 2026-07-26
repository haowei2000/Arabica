//! Deterministic Tier-A benchmark harness for Runtime short memory.
//!
//! This crate owns benchmark schemas, synthetic traces, reference baselines,
//! and correctness gates. Production event-to-memory semantics remain owned by
//! `structure-runtime`.

mod baseline;
mod gates;
mod generator;
mod scaling;
mod schema;
mod tier_b;

pub use baseline::{
    Baseline, BaselineError, BenchmarkProjection, ProjectionBatch, ProjectionVisibility,
};
pub use gates::{
    BenchmarkRun, BenchmarkRunError, CorrectnessGates, EvidenceRecallGate, GateCheck,
    ProjectionMetrics,
};
pub use generator::{GeneratorError, SyntheticTraceConfig, SyntheticTraceGenerator};
pub use scaling::{
    ScalingConfig, ScalingEnvironment, ScalingError, ScalingReport, ScalingRunner, ScalingSample,
};
pub use schema::{
    EvidenceUnitOracle, SHORT_MEMORY_TRACE_SCHEMA_VERSION, ShortMemoryTrace, ToolRelationOracle,
    TraceLineageOracle, TraceOracle, TraceOrigin, TraceValidationError,
};
pub use tier_b::{
    ExpectedFile, FileOracleResult, FixtureFileProvider, ProviderCallObservation,
    TIER_B_REPORT_SCHEMA_VERSION, TierBAggregate, TierBEnvironment, TierBError, TierBEvidenceLevel,
    TierBProviderMetadata, TierBReport, TierBRun, TierBSuiteConfig, TierBTask, TierBTaskChecks,
    run_tier_b_suite,
};
