use serde::{Deserialize, Serialize};

const STRUCTURE_CORE_MANIFEST_JSON: &str = include_str!("../../../core/structure_core.json");

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct ProductSurface {
    pub id: String,
    pub name: String,
    pub entrypoint: String,
    pub audience: String,
    pub runtime_model: String,
    pub primary_jobs: Vec<String>,
    pub boundary: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StructureCoreManifest {
    pub schema_version: String,
    pub paper_anchor: PaperAnchor,
    pub canonical_flow: Vec<CoreFlowStep>,
    pub benchmark_report_schema: BenchmarkReportSchema,
    pub capabilities: Vec<CoreCapability>,
    pub primitives: Vec<CorePrimitive>,
    pub surfaces: Vec<ProductSurface>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PaperAnchor {
    pub system_name: String,
    pub thesis: String,
    pub sections: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CoreFlowStep {
    pub id: String,
    pub name: String,
    pub description: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BenchmarkReportSchema {
    pub id: String,
    pub family: String,
    pub description: String,
    pub required_fields: Vec<String>,
    pub cost_fields: Vec<String>,
    pub evidence_fields: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CoreCapability {
    pub id: String,
    pub name: String,
    pub primitive_id: String,
    pub description: String,
    pub surface_status: Vec<CapabilitySurfaceStatus>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CapabilitySurfaceStatus {
    pub surface_id: String,
    pub status: String,
    pub entrypoint: String,
    pub evidence: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CorePrimitive {
    pub id: String,
    pub name: String,
    pub paper_section: String,
    pub invariant: String,
    pub implementation_contract: String,
    pub surface_roles: Vec<SurfaceRole>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SurfaceRole {
    pub surface_id: String,
    pub role: String,
}

pub fn product_surfaces() -> Vec<ProductSurface> {
    structure_core_manifest()
        .map(|manifest| manifest.surfaces)
        .unwrap_or_else(|_| fallback_product_surfaces())
}

pub fn structure_core_manifest() -> Result<StructureCoreManifest, String> {
    serde_json::from_str(STRUCTURE_CORE_MANIFEST_JSON)
        .map_err(|err| format!("failed to parse Structure core manifest: {err}"))
}

pub fn structure_core_manifest_json() -> &'static str {
    STRUCTURE_CORE_MANIFEST_JSON
}

pub fn fallback_product_surfaces() -> Vec<ProductSurface> {
    vec![
        surface(
            "cli_tui",
            "CLI/TUI",
            "uv run structure ... / structure-local tui",
            "Developers and local agent operators",
            "Local Rust binary; no backend service required",
            &[
                "Codex-style chat and code-agent runs",
                "Workspace and knowledge inspection",
                "Proposal review and application from the terminal",
            ],
            "Operator tooling only; it should stay scriptable and should not become the product UI.",
        ),
        surface(
            "desktop_app",
            "Desktop App",
            "npm run desktop:dev / structure-desktop",
            "Local users reviewing Structure artifacts",
            "Tauri shell over local files and Rust commands",
            &[
                "Native local chat workspace",
                "Code-agent event inspection",
                "Proposal and artifact review flow",
            ],
            "User-facing local app; it should not expose terminal service controls as its main workflow.",
        ),
        surface(
            "web_app",
            "Web App",
            "cd frontend && npm run dev",
            "Browser users and collaborative service deployments",
            "React UI backed by Structure API services",
            &[
                "Auth and workspaces",
                "Run creation and streaming",
                "Service-backed collaboration",
            ],
            "Service product surface; it remains separate from the offline local app and CLI/TUI.",
        ),
    ]
}

fn surface(
    id: &str,
    name: &str,
    entrypoint: &str,
    audience: &str,
    runtime_model: &str,
    primary_jobs: &[&str],
    boundary: &str,
) -> ProductSurface {
    ProductSurface {
        id: id.to_string(),
        name: name.to_string(),
        entrypoint: entrypoint.to_string(),
        audience: audience.to_string(),
        runtime_model: runtime_model.to_string(),
        primary_jobs: primary_jobs.iter().map(|job| (*job).to_string()).collect(),
        boundary: boundary.to_string(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn product_surfaces_define_separate_entrypoints() {
        let surfaces = product_surfaces();

        assert_eq!(
            surfaces
                .iter()
                .map(|surface| surface.id.as_str())
                .collect::<Vec<_>>(),
            vec!["cli_tui", "desktop_app", "web_app"]
        );
        assert!(surfaces[0].entrypoint.contains("structure"));
        assert!(surfaces[1].entrypoint.contains("desktop"));
        assert!(surfaces[2].runtime_model.contains("API"));
    }

    #[test]
    fn structure_core_manifest_links_paper_primitives_to_surfaces() {
        let manifest = structure_core_manifest().unwrap();
        let surface_ids = manifest
            .surfaces
            .iter()
            .map(|surface| surface.id.as_str())
            .collect::<Vec<_>>();

        assert_eq!(manifest.paper_anchor.system_name, "Structure");
        assert!(manifest
            .paper_anchor
            .thesis
            .contains("structured address space"));
        assert_eq!(manifest.canonical_flow[0].id, "goal");
        assert_eq!(manifest.benchmark_report_schema.id, "benchmark-report-v1");
        assert!(manifest
            .capabilities
            .iter()
            .any(|capability| capability.id == "event_replay"));
        assert!(manifest
            .benchmark_report_schema
            .required_fields
            .contains(&"mean_cost".to_string()));
        assert!(manifest
            .primitives
            .iter()
            .any(|primitive| primitive.id == "multi_level_disclosure"));

        for primitive in &manifest.primitives {
            assert!(!primitive.paper_section.is_empty());
            for role in &primitive.surface_roles {
                assert!(surface_ids.contains(&role.surface_id.as_str()));
            }
        }

        let primitive_ids = manifest
            .primitives
            .iter()
            .map(|primitive| primitive.id.as_str())
            .collect::<Vec<_>>();
        for capability in &manifest.capabilities {
            assert!(primitive_ids.contains(&capability.primitive_id.as_str()));
            assert_eq!(capability.surface_status.len(), surface_ids.len());
            for status in &capability.surface_status {
                assert!(surface_ids.contains(&status.surface_id.as_str()));
                assert!(!status.entrypoint.is_empty());
                assert!(!status.evidence.is_empty());
            }
        }
    }
}
