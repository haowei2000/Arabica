use crate::manifest::{structure_core_manifest, StructureCoreManifest};
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;
use std::fs;
use std::path::Path;

const REQUIRED_SURFACES: [&str; 3] = ["cli_tui", "desktop_app", "web_app"];
const ALLOWED_STATUSES: [&str; 3] = ["native", "service_native", "read_only"];

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SurfaceParityReport {
    pub schema_version: String,
    pub passed: bool,
    pub surface_count: usize,
    pub primitive_count: usize,
    pub capability_count: usize,
    pub checks: Vec<SurfaceParityCheck>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SurfaceParityCheck {
    pub id: String,
    pub passed: bool,
    pub summary: String,
    pub details: Vec<String>,
}

pub fn verify_structure_core_parity() -> Result<SurfaceParityReport, String> {
    let manifest = structure_core_manifest()?;
    Ok(verify_manifest_parity(&manifest))
}

pub fn verify_structure_core_parity_for_repo(
    repo_root: impl AsRef<Path>,
) -> Result<SurfaceParityReport, String> {
    let manifest = structure_core_manifest()?;
    let mut report = verify_manifest_parity(&manifest);
    report.checks.push(check_entrypoint_source_markers(
        &manifest,
        repo_root.as_ref(),
    ));
    report.passed = report.checks.iter().all(|check| check.passed);
    Ok(report)
}

pub fn verify_manifest_parity(manifest: &StructureCoreManifest) -> SurfaceParityReport {
    let checks = vec![
        check_required_surfaces(manifest),
        check_unique_capability_ids(manifest),
        check_capability_primitive_refs(manifest),
        check_capability_surface_coverage(manifest),
        check_capability_status_values(manifest),
        check_capability_entrypoints_and_evidence(manifest),
    ];
    let passed = checks.iter().all(|check| check.passed);

    SurfaceParityReport {
        schema_version: manifest.schema_version.clone(),
        passed,
        surface_count: manifest.surfaces.len(),
        primitive_count: manifest.primitives.len(),
        capability_count: manifest.capabilities.len(),
        checks,
    }
}

fn check_required_surfaces(manifest: &StructureCoreManifest) -> SurfaceParityCheck {
    let surface_ids = manifest
        .surfaces
        .iter()
        .map(|surface| surface.id.as_str())
        .collect::<BTreeSet<_>>();
    let missing = REQUIRED_SURFACES
        .iter()
        .filter(|surface_id| !surface_ids.contains(**surface_id))
        .map(|surface_id| (*surface_id).to_string())
        .collect::<Vec<_>>();
    let extra = surface_ids
        .iter()
        .filter(|surface_id| !REQUIRED_SURFACES.contains(surface_id))
        .map(|surface_id| (*surface_id).to_string())
        .collect::<Vec<_>>();

    check(
        "required_surfaces",
        missing.is_empty() && extra.is_empty(),
        format!("expected surfaces: {}", REQUIRED_SURFACES.join(", ")),
        missing
            .into_iter()
            .map(|surface_id| format!("missing surface: {surface_id}"))
            .chain(
                extra
                    .into_iter()
                    .map(|surface_id| format!("unexpected surface: {surface_id}")),
            )
            .collect(),
    )
}

fn check_unique_capability_ids(manifest: &StructureCoreManifest) -> SurfaceParityCheck {
    let mut seen = BTreeSet::new();
    let mut duplicates = Vec::new();
    for capability in &manifest.capabilities {
        if !seen.insert(capability.id.as_str()) {
            duplicates.push(format!("duplicate capability id: {}", capability.id));
        }
    }

    check(
        "unique_capability_ids",
        duplicates.is_empty(),
        "capability ids must be unique".to_string(),
        duplicates,
    )
}

fn check_capability_primitive_refs(manifest: &StructureCoreManifest) -> SurfaceParityCheck {
    let primitive_ids = manifest
        .primitives
        .iter()
        .map(|primitive| primitive.id.as_str())
        .collect::<BTreeSet<_>>();
    let missing = manifest
        .capabilities
        .iter()
        .filter(|capability| !primitive_ids.contains(capability.primitive_id.as_str()))
        .map(|capability| {
            format!(
                "{} references unknown primitive {}",
                capability.id, capability.primitive_id
            )
        })
        .collect::<Vec<_>>();

    check(
        "capability_primitive_refs",
        missing.is_empty(),
        "every capability must reference a paper primitive".to_string(),
        missing,
    )
}

fn check_capability_surface_coverage(manifest: &StructureCoreManifest) -> SurfaceParityCheck {
    let mut details = Vec::new();
    for capability in &manifest.capabilities {
        let surface_ids = capability
            .surface_status
            .iter()
            .map(|status| status.surface_id.as_str())
            .collect::<BTreeSet<_>>();
        for required_surface in REQUIRED_SURFACES {
            if !surface_ids.contains(required_surface) {
                details.push(format!(
                    "{} missing surface status for {}",
                    capability.id, required_surface
                ));
            }
        }
        for surface_id in surface_ids {
            if !REQUIRED_SURFACES.contains(&surface_id) {
                details.push(format!(
                    "{} has unknown surface status for {}",
                    capability.id, surface_id
                ));
            }
        }
    }

    check(
        "capability_surface_coverage",
        details.is_empty(),
        "every capability must cover CLI/TUI, desktop, and web".to_string(),
        details,
    )
}

fn check_capability_status_values(manifest: &StructureCoreManifest) -> SurfaceParityCheck {
    let details = manifest
        .capabilities
        .iter()
        .flat_map(|capability| {
            capability
                .surface_status
                .iter()
                .filter(|status| !ALLOWED_STATUSES.contains(&status.status.as_str()))
                .map(|status| {
                    format!(
                        "{} / {} has unsupported status {}",
                        capability.id, status.surface_id, status.status
                    )
                })
        })
        .collect::<Vec<_>>();

    check(
        "capability_status_values",
        details.is_empty(),
        format!("allowed statuses: {}", ALLOWED_STATUSES.join(", ")),
        details,
    )
}

fn check_capability_entrypoints_and_evidence(
    manifest: &StructureCoreManifest,
) -> SurfaceParityCheck {
    let mut details = Vec::new();
    for capability in &manifest.capabilities {
        if capability.description.trim().is_empty() {
            details.push(format!("{} has an empty description", capability.id));
        }
        for status in &capability.surface_status {
            if status.entrypoint.trim().is_empty() {
                details.push(format!(
                    "{} / {} has an empty entrypoint",
                    capability.id, status.surface_id
                ));
            }
            if status.evidence.trim().is_empty() {
                details.push(format!(
                    "{} / {} has empty evidence",
                    capability.id, status.surface_id
                ));
            }
        }
    }

    check(
        "capability_entrypoints_and_evidence",
        details.is_empty(),
        "each capability status must include an entrypoint and evidence".to_string(),
        details,
    )
}

fn check_entrypoint_source_markers(
    manifest: &StructureCoreManifest,
    repo_root: &Path,
) -> SurfaceParityCheck {
    let mut details = Vec::new();
    for capability in &manifest.capabilities {
        for status in &capability.surface_status {
            let markers = expected_source_markers(&capability.id, &status.surface_id);
            if markers.is_empty() {
                details.push(format!(
                    "{} / {} has no source marker rule",
                    capability.id, status.surface_id
                ));
                continue;
            }
            for marker in markers {
                let path = repo_root.join(marker.path);
                let text = match fs::read_to_string(&path) {
                    Ok(text) => text,
                    Err(err) => {
                        details.push(format!(
                            "{} / {} cannot read {}: {}",
                            capability.id,
                            status.surface_id,
                            marker.path.display(),
                            err
                        ));
                        continue;
                    }
                };
                for expected in marker.contains {
                    if !text.contains(expected) {
                        details.push(format!(
                            "{} / {} missing `{}` in {}",
                            capability.id,
                            status.surface_id,
                            expected,
                            marker.path.display()
                        ));
                    }
                }
            }
        }
    }

    check(
        "entrypoint_source_markers",
        details.is_empty(),
        "manifest entrypoints must be backed by source markers in this repository".to_string(),
        details,
    )
}

#[derive(Debug, Clone, Copy)]
struct SourceMarker<'a> {
    path: &'a Path,
    contains: &'a [&'a str],
}

fn marker<'a>(path: &'a str, contains: &'a [&'a str]) -> SourceMarker<'a> {
    SourceMarker {
        path: Path::new(path),
        contains,
    }
}

fn expected_source_markers(capability_id: &str, surface_id: &str) -> Vec<SourceMarker<'static>> {
    match (capability_id, surface_id) {
        ("core_manifest", "cli_tui") => vec![marker(
            "crates/structure-local/src/cli.rs",
            &[
                "Command::Core",
                "Command::Surfaces",
                "run_core",
                "run_surfaces",
            ],
        )],
        ("core_manifest", "desktop_app") => vec![marker(
            "frontend/src-tauri/src/lib.rs",
            &["fn local_snapshot", "fn core_manifest", "core_manifest,"],
        )],
        ("core_manifest", "web_app") => vec![marker(
            "frontend/src/core/structureCore.ts",
            &[
                "STRUCTURE_CORE_MANIFEST",
                "BenchmarkReportSchema",
                "CoreCapability",
            ],
        )],
        ("local_agent_run_loop", "cli_tui") => vec![marker(
            "crates/structure-local/src/cli.rs",
            &[
                "Command::Chat",
                "Command::Run",
                "Command::Continue",
                "Command::Tasks",
                "Command::Proposals",
                "Command::Tui",
                "run_chat_agent",
                "run_continue_agent",
                "run_continuation_from_session",
                "run_workspace_continuation_from_session",
                "WorkspaceCommand::Usage",
                "RunsCommand::Status",
                "ProposalsCommand::Apply",
                "ProposalsCommand::Rollback",
            ],
        )],
        ("local_agent_run_loop", "desktop_app") => vec![marker(
            "frontend/src-tauri/src/lib.rs",
            &[
                "fn local_agent_run",
                "fn local_agent_continue_attempt",
                "fn local_workspace_continue_attempt",
                "fn local_workspace_usage",
                "fn local_run_status",
                "fn local_tasks",
                "fn create_local_task",
                "fn update_local_task_status",
                "fn local_chat_turns",
                "fn local_command_turns",
                "fn record_local_command_turn",
                "fn apply_local_proposal",
                "fn rollback_local_proposal",
                "local_chat_turns,",
            ],
        )],
        ("local_agent_run_loop", "web_app") => vec![
            marker(
                "src/structure/routers/runs/runs.py",
                &["async def create_run"],
            ),
            marker(
                "src/structure/services/runs/run_application_service.py",
                &["async def start_user_run"],
            ),
        ],
        ("workspace_context", "cli_tui") => vec![marker(
            "crates/structure-local/src/cli.rs",
            &[
                "KnowledgeCommand::Add",
                "KnowledgeCommand::Show",
                "KnowledgeCommand::Remove",
                "WorkspaceCommand::Create",
                "WorkspaceCommand::List",
                "WorkspaceCommand::Show",
            ],
        )],
        ("workspace_context", "desktop_app") => vec![marker(
            "frontend/src-tauri/src/lib.rs",
            &[
                "fn create_local_workspace",
                "fn local_workspaces",
                "fn local_repo_entries",
                "fn local_repo_search",
                "fn read_local_repo_file",
                "fn add_local_knowledge",
                "fn read_local_knowledge_source",
                "fn remove_local_knowledge",
                "remove_local_knowledge,",
            ],
        )],
        ("workspace_context", "web_app") => vec![
            marker(
                "src/structure/services/workspace_context/workspace_context_service.py",
                &[
                    "class WorkspaceContextService",
                    "async def get",
                    "async def list",
                ],
            ),
            marker(
                "src/structure/routers/workspaces/workspace.py",
                &[
                    "async def list_workspace_contexts",
                    "async def remove_workspace_context",
                ],
            ),
        ],
        ("event_replay", "cli_tui") => vec![marker(
            "crates/structure-local/src/cli.rs",
            &[
                "RunsCommand::Events",
                "RunsCommand::Transcript",
                "RunsCommand::Status",
                "WorkspaceCommand::Replay",
                "WorkspaceCommand::Continue",
                "WorkspaceCommand::Usage",
            ],
        )],
        ("event_replay", "desktop_app") => vec![marker(
            "frontend/src-tauri/src/lib.rs",
            &[
                "fn local_run_transcript",
                "fn local_run_status",
                "fn local_run_events",
                "fn local_command_turns",
                "fn record_local_command_turn",
                "fn local_workspace_replay",
                "fn local_workspace_continue_attempt",
                "fn local_workspace_usage",
                "local_workspace_replay,",
            ],
        )],
        ("event_replay", "web_app") => vec![
            marker(
                "frontend/src/services/eventService.ts",
                &["LIST_BY_WORKSPACE", "LIST_BY_RUN"],
            ),
            marker(
                "src/structure/services/events/event_crud.py",
                &["class EventCRUD"],
            ),
        ],
        ("artifact_evidence", "cli_tui") => vec![marker(
            "crates/structure-local/src/cli.rs",
            &[
                "ArtifactsCommand::List",
                "ArtifactsCommand::Show",
                "ProposalsCommand::List",
                "ProposalsCommand::Show",
                "ProposalsCommand::Apply",
                "ProposalsCommand::Rollback",
            ],
        )],
        ("artifact_evidence", "desktop_app") => vec![marker(
            "frontend/src-tauri/src/lib.rs",
            &[
                "fn local_artifacts",
                "fn read_local_artifact",
                "fn apply_local_proposal",
                "fn rollback_local_proposal",
                "read_local_artifact,",
            ],
        )],
        ("artifact_evidence", "web_app") => vec![
            marker(
                "src/structure/routers/runs/artifacts.py",
                &["async def list_artifacts", "async def get_artifact"],
            ),
            marker(
                "src/structure/services/runs/artifact_crud.py",
                &["class ArtifactCRUD"],
            ),
        ],
        _ => Vec::new(),
    }
}

fn check(id: &str, passed: bool, summary: String, details: Vec<String>) -> SurfaceParityCheck {
    SurfaceParityCheck {
        id: id.to_string(),
        passed,
        summary,
        details,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn canonical_manifest_passes_surface_parity() {
        let report = verify_structure_core_parity().unwrap();

        assert!(report.passed);
        assert_eq!(report.surface_count, 3);
        assert!(report.capability_count >= 5);
        assert!(report.checks.iter().all(|check| check.passed));
    }

    #[test]
    fn canonical_manifest_entrypoints_are_backed_by_source_markers() {
        let repo_root = Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../..")
            .canonicalize()
            .unwrap();
        let report = verify_structure_core_parity_for_repo(repo_root).unwrap();

        assert!(report.passed, "{report:#?}");
        assert!(report
            .checks
            .iter()
            .any(|check| check.id == "entrypoint_source_markers"));
    }
}
