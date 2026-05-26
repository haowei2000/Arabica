use structure_local_core::{collect_snapshot, default_repo_root, structure_core_manifest};
use structure_local_runtime::{
    BuiltinLocalToolRegistry, LocalAgentMode, LocalAgentRuntime, LocalToolCall, LocalToolRegistry,
    RunRequest,
};

#[tauri::command]
fn local_snapshot() -> Result<structure_local_core::LocalSnapshot, String> {
    let repo_root = default_repo_root()?;
    collect_snapshot(repo_root)
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
fn local_repo_entries(
    max_entries: Option<u64>,
) -> Result<structure_local_runtime::LocalToolResult, String> {
    execute_local_repo_tool(
        "list_workspace",
        serde_json::json!({ "max_entries": max_entries.unwrap_or(32) }),
    )
}

#[tauri::command]
fn local_repo_search(
    query: String,
    max_matches: Option<u64>,
) -> Result<structure_local_runtime::LocalToolResult, String> {
    execute_local_repo_tool(
        "search_repo",
        serde_json::json!({
            "query": query,
            "max_matches": max_matches.unwrap_or(20),
        }),
    )
}

#[tauri::command]
fn read_local_repo_file(
    path: String,
    max_bytes: Option<u64>,
) -> Result<structure_local_runtime::LocalToolResult, String> {
    execute_local_repo_tool(
        "read_repo_file",
        serde_json::json!({
            "path": path,
            "max_bytes": max_bytes.unwrap_or(64_000),
        }),
    )
}

#[tauri::command]
fn local_agent_run(
    prompt: String,
    workspace_id: Option<String>,
    mode: Option<String>,
) -> Result<structure_local_runtime::RunResult, String> {
    let runtime = LocalAgentRuntime::open_default()?;
    runtime.run_prompt(RunRequest {
        prompt,
        workspace_id,
        mode: Some(parse_local_agent_mode(mode)),
    })
}

#[tauri::command]
fn local_chat_turns(
    workspace_id: Option<String>,
    limit: Option<usize>,
) -> Result<Vec<structure_local_runtime::ChatTurn>, String> {
    let runtime = LocalAgentRuntime::open_default()?;
    runtime.chat_turns(workspace_id.as_deref(), limit.unwrap_or(20))
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
fn latest_local_proposal(
    workspace_id: Option<String>,
    run_id: Option<String>,
    max_bytes: Option<u64>,
) -> Result<structure_local_runtime::ArtifactPreview, String> {
    let runtime = LocalAgentRuntime::open_default()?;
    let proposal = runtime
        .list_artifacts(workspace_id.as_deref(), run_id.as_deref(), 32)?
        .into_iter()
        .find(|artifact| artifact.kind == "code_change_proposal")
        .ok_or_else(|| "No local code-change proposal found.".to_string())?;
    runtime.read_artifact(&proposal.artifact_id, max_bytes.unwrap_or(64_000))
}

#[tauri::command]
fn apply_local_proposal(
    artifact_id: String,
    dry_run: Option<bool>,
) -> Result<structure_local_runtime::ProposalApplyResult, String> {
    let runtime = LocalAgentRuntime::open_default()?;
    runtime.apply_code_change_proposal(&artifact_id, dry_run.unwrap_or(false))
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![
            local_snapshot,
            core_manifest,
            core_parity_report,
            local_repo_entries,
            local_repo_search,
            read_local_repo_file,
            local_agent_run,
            local_chat_turns,
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
            latest_local_proposal,
            apply_local_proposal,
        ])
        .run(tauri::generate_context!())
        .expect("failed to run Structure desktop app");
}

fn parse_local_agent_mode(mode: Option<String>) -> LocalAgentMode {
    match mode.as_deref() {
        Some("chat") => LocalAgentMode::Chat,
        _ => LocalAgentMode::CodeAgent,
    }
}

fn execute_local_repo_tool(
    name: &str,
    input: serde_json::Value,
) -> Result<structure_local_runtime::LocalToolResult, String> {
    let runtime = LocalAgentRuntime::open_default()?;
    let registry = BuiltinLocalToolRegistry::new(runtime.repo_root());
    Ok(registry.execute(&LocalToolCall {
        call_id: format!("desktop_{name}"),
        name: name.to_string(),
        input,
    }))
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
    fn desktop_commands_cover_workspace_run_and_artifact_evidence() {
        let _guard = ENV_LOCK.lock().unwrap();
        let root = unique_repo("desktop-commands");
        let runtime_dir = root.join(".runtime");
        let previous_repo_root = env::var("STRUCTURE_REPO_ROOT").ok();
        let previous_runtime_dir = env::var("STRUCTURE_LOCAL_RUNTIME_DIR").ok();
        let previous_openai = openai_env_keys()
            .iter()
            .map(|key| (*key, env::var(key).ok()))
            .collect::<Vec<_>>();
        env::set_var("STRUCTURE_REPO_ROOT", &root);
        env::set_var("STRUCTURE_LOCAL_RUNTIME_DIR", &runtime_dir);
        for key in openai_env_keys() {
            env::remove_var(key);
        }

        let result = (|| {
            fs::write(root.join("note.md"), "desktop local repo search").unwrap();
            fs::write(root.join(".env"), "LOCAL_SECRET=secret").unwrap();
            let workspace = create_local_workspace("desktop-test".to_string())?;
            let entries = local_repo_entries(Some(16))?;
            let search = local_repo_search("desktop local".to_string(), Some(8))?;
            let read_note = read_local_repo_file("note.md".to_string(), Some(1_000))?;
            let read_env = read_local_repo_file(".env".to_string(), Some(1_000))?;
            let run = local_agent_run(
                "Inspect this desktop command workspace.".to_string(),
                Some(workspace.workspace_id.clone()),
                Some("code_agent".to_string()),
            )?;
            let chat_run = local_agent_run(
                "Reply conversationally without code inspection.".to_string(),
                Some(workspace.workspace_id.clone()),
                Some("chat".to_string()),
            )?;
            let turns = local_chat_turns(Some(workspace.workspace_id.clone()), Some(5))?;
            let runs = local_runs(Some(workspace.workspace_id.clone()), Some(5))?;
            let replay = local_workspace_replay(Some(workspace.workspace_id.clone()), Some(50))?;
            let artifacts = local_artifacts(Some(workspace.workspace_id.clone()), None, Some(10))?;
            let proposal_preview = latest_local_proposal(
                Some(workspace.workspace_id.clone()),
                Some(run.run.run_id.clone()),
                Some(64_000),
            )?;
            let dry_apply =
                apply_local_proposal(proposal_preview.artifact.artifact_id.clone(), Some(true))?;
            let chat_artifacts = local_artifacts(
                Some(workspace.workspace_id.clone()),
                Some(chat_run.run.run_id.clone()),
                Some(10),
            )?;

            assert_eq!(workspace.workspace_id, "desktop-test");
            assert!(entries.success);
            let entries_array = entries.output["entries"].as_array().unwrap();
            assert!(!entries_array
                .iter()
                .any(
                    |entry| entry.get("name").and_then(serde_json::Value::as_str) == Some(".env")
                ));
            assert!(search.success);
            assert_eq!(search.output["match_count"], 1);
            assert!(read_note.success);
            assert!(read_note.output["preview"]
                .as_str()
                .unwrap_or_default()
                .contains("desktop local repo search"));
            assert!(!read_env.success);
            assert_eq!(
                read_env.error.as_deref(),
                Some("refusing to read sensitive local configuration file")
            );
            assert_eq!(run.run.workspace_id, "desktop-test");
            assert_eq!(run.run.status, "finished");
            assert_eq!(chat_run.run.status, "finished");
            assert!(turns.iter().any(|item| item.run_id == run.run.run_id));
            assert!(turns.iter().any(|item| item.run_id == chat_run.run.run_id));
            assert!(runs.iter().any(|item| item.run_id == run.run.run_id));
            assert!(!replay.events.is_empty());
            assert!(artifacts
                .iter()
                .any(|artifact| artifact.kind == "code_change_proposal"));
            assert_eq!(proposal_preview.artifact.kind, "code_change_proposal");
            assert!(proposal_preview.preview.contains("Patch Sketch"));
            assert!(!dry_apply.applied);
            assert!(dry_apply.dry_run);
            assert!(dry_apply
                .preview
                .contains("Proposed Structure local code-agent change"));
            assert!(chat_artifacts
                .iter()
                .all(|artifact| artifact.kind != "code_change_proposal"));
            Ok::<(), String>(())
        })();

        restore_env("STRUCTURE_REPO_ROOT", previous_repo_root);
        restore_env("STRUCTURE_LOCAL_RUNTIME_DIR", previous_runtime_dir);
        for (key, value) in previous_openai {
            restore_env(key, value);
        }
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

    fn openai_env_keys() -> [&'static str; 3] {
        ["OPENAI__API_KEY", "OPENAI__BASE_URL", "OPENAI__MODEL"]
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
