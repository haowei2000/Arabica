use structure_local_core::{
    collect_snapshot, default_repo_root, read_repo_text_file, structure_core_manifest,
};
use structure_local_runtime::{LocalAgentRuntime, LocalBenchmarkRequest, RunRequest};

#[tauri::command]
fn local_snapshot() -> Result<structure_local_core::LocalSnapshot, String> {
    let repo_root = default_repo_root()?;
    collect_snapshot(repo_root)
}

#[tauri::command]
fn read_local_report(path: String) -> Result<String, String> {
    let repo_root = default_repo_root()?;
    read_repo_text_file(repo_root, path, 1_000_000)
}

#[tauri::command]
fn local_benchmark_evidence(
    path: String,
) -> Result<structure_local_runtime::LocalBenchmarkEvidence, String> {
    let runtime = LocalAgentRuntime::open_default()?;
    runtime.local_benchmark_evidence(path)
}

#[tauri::command]
fn core_manifest() -> Result<structure_local_core::StructureCoreManifest, String> {
    structure_core_manifest()
}

#[tauri::command]
fn core_parity_report() -> Result<structure_local_core::SurfaceParityReport, String> {
    let repo_root = default_repo_root()?;
    structure_local_core::verify_structure_core_parity_for_repo(repo_root)
}

#[tauri::command]
fn local_agent_run(
    prompt: String,
    workspace_id: Option<String>,
) -> Result<structure_local_runtime::RunResult, String> {
    let runtime = LocalAgentRuntime::open_default()?;
    runtime.run_prompt(RunRequest {
        prompt,
        workspace_id,
    })
}

#[tauri::command]
fn local_runs(
    workspace_id: Option<String>,
    limit: Option<usize>,
) -> Result<Vec<structure_local_runtime::RunSummary>, String> {
    let runtime = LocalAgentRuntime::open_default()?;
    runtime.list_runs(workspace_id.as_deref(), limit.unwrap_or(10))
}

#[tauri::command]
fn create_local_workspace(
    workspace_id: String,
) -> Result<structure_local_runtime::WorkspaceSummary, String> {
    let runtime = LocalAgentRuntime::open_default()?;
    runtime.ensure_workspace(Some(workspace_id))
}

#[tauri::command]
fn local_workspaces(
    limit: Option<usize>,
) -> Result<Vec<structure_local_runtime::WorkspaceSummary>, String> {
    let runtime = LocalAgentRuntime::open_default()?;
    runtime.list_workspaces(limit.unwrap_or(20))
}

#[tauri::command]
fn local_workspace(
    workspace_id: String,
) -> Result<structure_local_runtime::WorkspaceSummary, String> {
    let runtime = LocalAgentRuntime::open_default()?;
    runtime.workspace(&workspace_id)
}

#[tauri::command]
fn local_run(run_id: String) -> Result<structure_local_runtime::RunSummary, String> {
    let runtime = LocalAgentRuntime::open_default()?;
    runtime.run_by_id(&run_id)
}

#[tauri::command]
fn local_run_events(run_id: String) -> Result<Vec<structure_local_runtime::LocalEvent>, String> {
    let runtime = LocalAgentRuntime::open_default()?;
    runtime.run_events(&run_id)
}

#[tauri::command]
fn local_run_evidence(
    run_id: String,
) -> Result<structure_local_runtime::RunEvidenceSummary, String> {
    let runtime = LocalAgentRuntime::open_default()?;
    runtime.run_evidence_summary(&run_id)
}

#[tauri::command]
fn local_workspace_replay(
    workspace_id: Option<String>,
    limit: Option<usize>,
) -> Result<structure_local_runtime::WorkspaceReplay, String> {
    let runtime = LocalAgentRuntime::open_default()?;
    runtime.workspace_replay(workspace_id.as_deref(), limit.unwrap_or(50))
}

#[tauri::command]
fn local_evidence_bundle(
    workspace_id: Option<String>,
    limit: Option<usize>,
) -> Result<structure_local_runtime::LocalEvidenceBundle, String> {
    let runtime = LocalAgentRuntime::open_default()?;
    runtime.local_evidence_bundle(workspace_id.as_deref(), limit.unwrap_or(50))
}

#[tauri::command]
fn add_local_knowledge(
    path: String,
    workspace_id: Option<String>,
) -> Result<structure_local_runtime::KnowledgeSource, String> {
    let runtime = LocalAgentRuntime::open_default()?;
    runtime.add_knowledge_source(workspace_id, path)
}

#[tauri::command]
fn local_knowledge(
    workspace_id: Option<String>,
    limit: Option<usize>,
) -> Result<Vec<structure_local_runtime::KnowledgeSource>, String> {
    let runtime = LocalAgentRuntime::open_default()?;
    runtime.knowledge_sources(workspace_id.as_deref(), limit.unwrap_or(20))
}

#[tauri::command]
fn local_knowledge_source(
    source_id: String,
) -> Result<structure_local_runtime::KnowledgeSource, String> {
    let runtime = LocalAgentRuntime::open_default()?;
    runtime.knowledge_source(&source_id)
}

#[tauri::command]
fn read_local_knowledge_source(
    source_id: String,
    max_bytes: Option<u64>,
) -> Result<structure_local_runtime::KnowledgeSourcePreview, String> {
    let runtime = LocalAgentRuntime::open_default()?;
    runtime.read_knowledge_source(&source_id, max_bytes.unwrap_or(64_000))
}

#[tauri::command]
fn remove_local_knowledge(
    source_id: String,
) -> Result<structure_local_runtime::KnowledgeSource, String> {
    let runtime = LocalAgentRuntime::open_default()?;
    runtime.remove_knowledge_source(&source_id)
}

#[tauri::command]
fn local_artifacts(
    workspace_id: Option<String>,
    run_id: Option<String>,
    limit: Option<usize>,
) -> Result<Vec<structure_local_runtime::ArtifactRecord>, String> {
    let runtime = LocalAgentRuntime::open_default()?;
    runtime.list_artifacts(
        workspace_id.as_deref(),
        run_id.as_deref(),
        limit.unwrap_or(20),
    )
}

#[tauri::command]
fn read_local_artifact(
    artifact_id: String,
    max_bytes: Option<u64>,
) -> Result<structure_local_runtime::ArtifactPreview, String> {
    let runtime = LocalAgentRuntime::open_default()?;
    runtime.read_artifact(&artifact_id, max_bytes.unwrap_or(64_000))
}

#[tauri::command]
fn local_benchmark_run(
    benchmark: Option<String>,
    max_cases: Option<usize>,
    workspace_id: Option<String>,
) -> Result<structure_local_runtime::LocalBenchmarkRunResult, String> {
    let runtime = LocalAgentRuntime::open_default()?;
    runtime.run_local_benchmark(LocalBenchmarkRequest {
        benchmark,
        max_cases,
        output_dir: None,
        workspace_id,
    })
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![
            local_snapshot,
            read_local_report,
            local_benchmark_evidence,
            core_manifest,
            core_parity_report,
            local_agent_run,
            local_runs,
            create_local_workspace,
            local_workspaces,
            local_workspace,
            local_run,
            local_run_events,
            local_run_evidence,
            local_workspace_replay,
            local_evidence_bundle,
            add_local_knowledge,
            local_knowledge,
            local_knowledge_source,
            read_local_knowledge_source,
            remove_local_knowledge,
            local_artifacts,
            read_local_artifact,
            local_benchmark_run
        ])
        .run(tauri::generate_context!())
        .expect("failed to run Structure desktop app");
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::env;
    use std::fs;
    use std::path::PathBuf;
    use std::sync::Mutex;
    use std::time::{SystemTime, UNIX_EPOCH};

    static ENV_LOCK: Mutex<()> = Mutex::new(());

    #[test]
    fn desktop_commands_cover_workspace_run_and_benchmark_evidence() {
        let _guard = ENV_LOCK.lock().unwrap();
        let root = unique_repo("desktop-commands");
        let runtime_dir = root.join(".runtime");
        let previous_repo_root = env::var("STRUCTURE_REPO_ROOT").ok();
        let previous_runtime_dir = env::var("STRUCTURE_LOCAL_RUNTIME_DIR").ok();
        env::set_var("STRUCTURE_REPO_ROOT", &root);
        env::set_var("STRUCTURE_LOCAL_RUNTIME_DIR", &runtime_dir);

        let result = (|| {
            let workspace = create_local_workspace("desktop-test".to_string())?;
            let run = local_agent_run(
                "Inspect this desktop command workspace.".to_string(),
                Some(workspace.workspace_id.clone()),
            )?;
            let runs = local_runs(Some(workspace.workspace_id.clone()), Some(5))?;
            let replay = local_workspace_replay(Some(workspace.workspace_id.clone()), Some(50))?;
            let benchmark =
                local_benchmark_run(None, Some(1), Some(workspace.workspace_id.clone()))?;
            let evidence = local_benchmark_evidence(benchmark.json_path.clone())?;

            assert_eq!(workspace.workspace_id, "desktop-test");
            assert_eq!(run.run.workspace_id, "desktop-test");
            assert_eq!(run.run.status, "finished");
            assert!(runs.iter().any(|item| item.run_id == run.run.run_id));
            assert!(!replay.events.is_empty());
            assert_eq!(benchmark.report.n_cases, 1);
            assert_eq!(evidence.schema_version, "local-benchmark-evidence-v1");
            assert_eq!(evidence.run_evidence.len(), 1);
            Ok::<(), String>(())
        })();

        restore_env("STRUCTURE_REPO_ROOT", previous_repo_root);
        restore_env("STRUCTURE_LOCAL_RUNTIME_DIR", previous_runtime_dir);
        fs::remove_dir_all(root).ok();
        result.unwrap();
    }

    fn restore_env(key: &str, value: Option<String>) {
        if let Some(value) = value {
            env::set_var(key, value);
        } else {
            env::remove_var(key);
        }
    }

    fn unique_repo(label: &str) -> PathBuf {
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let root = env::temp_dir().join(format!(
            "structure-desktop-{label}-{}-{nanos}",
            std::process::id()
        ));
        fs::create_dir_all(root.join("src/structure")).unwrap();
        fs::create_dir_all(root.join("frontend")).unwrap();
        fs::write(root.join("pyproject.toml"), "").unwrap();
        root.canonicalize().unwrap()
    }
}
