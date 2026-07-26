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

pub use baseline::{
    Baseline, BaselineError, BenchmarkProjection, ProjectionBatch, ProjectionVisibility,
};
pub use gates::{BenchmarkRun, BenchmarkRunError, CorrectnessGates, GateCheck, ProjectionMetrics};
pub use generator::{GeneratorError, SyntheticTraceConfig, SyntheticTraceGenerator};
pub use scaling::{
    ScalingConfig, ScalingEnvironment, ScalingError, ScalingReport, ScalingRunner, ScalingSample,
};
pub use schema::{
    SHORT_MEMORY_TRACE_SCHEMA_VERSION, ShortMemoryTrace, ToolRelationOracle, TraceLineageOracle,
    TraceOracle, TraceOrigin, TraceValidationError,
};
