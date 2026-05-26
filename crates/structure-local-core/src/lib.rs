mod manifest;
mod parity;
mod repo;
mod reports;
mod snapshot;

pub use manifest::{
    fallback_product_surfaces, product_surfaces, structure_core_manifest,
    structure_core_manifest_json, BenchmarkReportSchema, CapabilitySurfaceStatus, CoreCapability,
    CoreFlowStep, CorePrimitive, PaperAnchor, ProductSurface, StructureCoreManifest, SurfaceRole,
};
pub use parity::{
    verify_manifest_parity, verify_structure_core_parity, verify_structure_core_parity_for_repo,
    SurfaceParityCheck, SurfaceParityReport,
};
pub use repo::{default_repo_root, find_repo_root};
pub use reports::{read_repo_text_file, recent_benchmark_reports, LocalFile};
pub use snapshot::{
    collect_snapshot, snapshot_json, LocalEnvVarStatus, LocalLlmConfigStatus, LocalSnapshot,
};
