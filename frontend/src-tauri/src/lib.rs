use serde::Serialize;
use structure_local_core::{collect_snapshot, default_repo_root, structure_core_manifest};
use structure_local_runtime::{
    ContinuationRequest, LocalAgentMode, LocalAgentRuntime, LocalToolCall, RunRequest,
};

#[derive(Debug, Serialize)]
struct DesktopSessionStatus {
    workspace_id: String,
    selected_run_id: Option<String>,
    mode: String,
    repo_root: String,
    runtime_dir: String,
    runtime_db: String,
    llm_config: structure_local_core::LocalLlmConfigStatus,
    run_count: usize,
    artifact_count: usize,
    knowledge_source_count: usize,
    recent_event_count: usize,
    last_event_sequence: Option<i64>,
}

#[tauri::command]
fn local_snapshot() -> Result<structure_local_core::LocalSnapshot, String> {
    let repo_root = default_repo_root()?;
    collect_snapshot(repo_root)
}

#[tauri::command]
fn local_session_status(
    workspace_id: Option<String>,
    selected_run_id: Option<String>,
    mode: Option<String>,
) -> Result<DesktopSessionStatus, String> {
    let runtime = LocalAgentRuntime::open_default()?;
    let snapshot = collect_snapshot(runtime.repo_root())?;
    let workspace_id = workspace_id.unwrap_or_else(|| "default".to_string());
    let mode = parse_local_agent_mode(mode).as_str().to_string();
    let runs = runtime.list_runs(Some(&workspace_id), 1_000)?;
    let artifacts = runtime.list_artifacts(Some(&workspace_id), None, 1_000)?;
    let knowledge_sources = runtime.knowledge_sources(Some(&workspace_id), 1_000)?;
    let replay = runtime.workspace_replay(Some(&workspace_id), 500)?;

    Ok(DesktopSessionStatus {
        workspace_id,
        selected_run_id,
        mode,
        repo_root: snapshot.repo_root.clone(),
        runtime_dir: snapshot.runtime_dir.clone(),
        runtime_db: runtime.db_path().display().to_string(),
        llm_config: snapshot.llm_config,
        run_count: runs.len(),
        artifact_count: artifacts.len(),
        knowledge_source_count: knowledge_sources.len(),
        recent_event_count: replay.events.len(),
        last_event_sequence: replay.last_sequence,
    })
}

#[tauri::command]
fn local_llm_diagnostic() -> Result<structure_local_runtime::LocalLlmDiagnostic, String> {
    let runtime = LocalAgentRuntime::open_default()?;
    Ok(runtime.llm_diagnostic())
}

#[tauri::command]
fn local_worktree_snapshot() -> Result<structure_local_runtime::WorktreeSnapshot, String> {
    let runtime = LocalAgentRuntime::open_default()?;
    Ok(runtime.worktree_snapshot())
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
    workspace_id: Option<String>,
    max_entries: Option<u64>,
) -> Result<structure_local_runtime::LocalToolResult, String> {
    execute_local_repo_tool(
        workspace_id,
        "list_workspace",
        serde_json::json!({ "max_entries": max_entries.unwrap_or(32) }),
    )
}

#[tauri::command]
fn local_repo_search(
    workspace_id: Option<String>,
    query: String,
    max_matches: Option<u64>,
) -> Result<structure_local_runtime::LocalToolResult, String> {
    execute_local_repo_tool(
        workspace_id,
        "search_repo",
        serde_json::json!({
            "query": query,
            "max_matches": max_matches.unwrap_or(20),
        }),
    )
}

#[tauri::command]
fn read_local_repo_file(
    workspace_id: Option<String>,
    path: String,
    max_bytes: Option<u64>,
) -> Result<structure_local_runtime::LocalToolResult, String> {
    execute_local_repo_tool(
        workspace_id,
        "read_repo_file",
        serde_json::json!({
            "path": path,
            "max_bytes": max_bytes.unwrap_or(64_000),
        }),
    )
}

#[tauri::command]
fn run_local_command(
    workspace_id: Option<String>,
    argv: Vec<String>,
    cwd: Option<String>,
    timeout_ms: Option<u64>,
    max_output_chars: Option<u64>,
) -> Result<structure_local_runtime::LocalToolResult, String> {
    execute_local_repo_tool(
        workspace_id,
        "run_local_command",
        serde_json::json!({
            "argv": argv,
            "cwd": cwd.unwrap_or_else(|| ".".to_string()),
            "timeout_ms": timeout_ms.unwrap_or(30_000),
            "max_output_chars": max_output_chars.unwrap_or(12_000),
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
fn local_agent_run_attempt(
    prompt: String,
    workspace_id: Option<String>,
    mode: Option<String>,
) -> Result<structure_local_runtime::RunAttempt, String> {
    let runtime = LocalAgentRuntime::open_default()?;
    runtime.run_prompt_attempt(RunRequest {
        prompt,
        workspace_id,
        mode: Some(parse_local_agent_mode(mode)),
    })
}

#[tauri::command]
fn local_agent_continue_attempt(
    run_id: String,
    extra_instruction: Option<String>,
    mode: Option<String>,
) -> Result<structure_local_runtime::RunAttempt, String> {
    let runtime = LocalAgentRuntime::open_default()?;
    runtime.run_continuation_attempt(ContinuationRequest {
        run_id,
        extra_instruction,
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
fn local_run_transcript(run_id: String) -> Result<structure_local_runtime::RunTranscript, String> {
    let runtime = LocalAgentRuntime::open_default()?;
    runtime.run_transcript(&run_id)
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
fn local_workspace_event_feed(
    workspace_id: Option<String>,
    after_sequence: Option<i64>,
    limit: Option<usize>,
) -> Result<structure_local_runtime::WorkspaceEventFeed, String> {
    let runtime = LocalAgentRuntime::open_default()?;
    runtime.workspace_event_feed(workspace_id.as_deref(), after_sequence, limit.unwrap_or(50))
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
            local_session_status,
            local_llm_diagnostic,
            local_worktree_snapshot,
            core_manifest,
            core_parity_report,
            local_repo_entries,
            local_repo_search,
            read_local_repo_file,
            run_local_command,
            local_agent_run,
            local_agent_run_attempt,
            local_agent_continue_attempt,
            local_chat_turns,
            local_runs,
            create_local_workspace,
            local_workspaces,
            local_workspace,
            local_run,
            local_run_events,
            local_run_evidence,
            local_run_transcript,
            local_workspace_replay,
            local_workspace_event_feed,
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
    workspace_id: Option<String>,
    name: &str,
    input: serde_json::Value,
) -> Result<structure_local_runtime::LocalToolResult, String> {
    let runtime = LocalAgentRuntime::open_default()?;
    runtime.execute_workspace_tool(
        workspace_id,
        "desktop_app",
        LocalToolCall {
            call_id: format!("desktop_{name}"),
            name: name.to_string(),
            input,
        },
    )
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
            let entries = local_repo_entries(Some(workspace.workspace_id.clone()), Some(16))?;
            let search = local_repo_search(
                Some(workspace.workspace_id.clone()),
                "desktop local".to_string(),
                Some(8),
            )?;
            let read_note = read_local_repo_file(
                Some(workspace.workspace_id.clone()),
                "note.md".to_string(),
                Some(1_000),
            )?;
            let read_env = read_local_repo_file(
                Some(workspace.workspace_id.clone()),
                ".env".to_string(),
                Some(1_000),
            )?;
            let pwd = run_local_command(
                Some(workspace.workspace_id.clone()),
                vec!["pwd".to_string()],
                None,
                Some(5_000),
                Some(2_000),
            )?;
            let run = local_agent_run(
                "Inspect this desktop command workspace.".to_string(),
                Some(workspace.workspace_id.clone()),
                Some("code_agent".to_string()),
            )?;
            let attempt = local_agent_run_attempt(
                "Inspect this desktop command workspace through attempt.".to_string(),
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
            let transcript = local_run_transcript(run.run.run_id.clone())?;
            let replay = local_workspace_replay(Some(workspace.workspace_id.clone()), Some(200))?;
            let feed =
                local_workspace_event_feed(Some(workspace.workspace_id.clone()), Some(0), Some(5))?;
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
            let session_status = local_session_status(
                Some(workspace.workspace_id.clone()),
                Some(run.run.run_id.clone()),
                Some("code_agent".to_string()),
            )?;
            let llm_diagnostic = local_llm_diagnostic()?;
            let worktree = local_worktree_snapshot()?;

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
            assert!(pwd.success);
            assert_eq!(pwd.output["success"], true);
            assert!(pwd.output["stdout"]
                .as_str()
                .unwrap_or_default()
                .contains(root.to_str().unwrap()));
            assert_eq!(run.run.workspace_id, "desktop-test");
            assert_eq!(run.run.status, "finished");
            assert_eq!(attempt.run.status, "finished");
            assert!(attempt.result.is_some());
            assert!(attempt.error.is_none());
            assert!(attempt
                .events
                .iter()
                .any(|event| event.kind == "run_finished"));
            assert_eq!(chat_run.run.status, "finished");
            assert!(turns.iter().any(|item| item.run_id == run.run.run_id));
            assert!(turns.iter().any(|item| item.run_id == chat_run.run.run_id));
            assert!(runs.iter().any(|item| item.run_id == run.run.run_id));
            assert_eq!(transcript.run.run_id, run.run.run_id);
            assert!(transcript.chat_turn.is_some());
            assert_eq!(transcript.evidence.event_count, transcript.events.len());
            assert!(transcript
                .evidence
                .primitive_ids
                .contains(&"reproducible_evidence".to_string()));
            assert!(transcript.evidence.core_trace.core_aligned);
            assert_eq!(
                transcript.evidence.core_trace.manifest_schema_version,
                "2026.05"
            );
            assert!(transcript
                .events
                .iter()
                .any(|event| event.canonical_flow_id == "evidence"));
            assert!(!replay.events.is_empty());
            assert!(!feed.events.is_empty());
            assert!(replay.events.iter().any(|event| {
                event.run_id.is_none()
                    && event.kind == "tool_call_requested"
                    && event.payload["surface"] == "desktop_app"
            }));
            assert!(replay.events.iter().any(|event| {
                event.run_id.is_none()
                    && event.kind == "tool_call_completed"
                    && event.payload["name"] == "run_local_command"
                    && event.payload["surface"] == "desktop_app"
            }));
            assert_eq!(
                feed.next_after_sequence,
                feed.events.last().unwrap().sequence
            );
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
            assert_eq!(session_status.workspace_id, "desktop-test");
            assert_eq!(
                session_status.selected_run_id.as_deref(),
                Some(run.run.run_id.as_str())
            );
            assert_eq!(session_status.mode, "code_agent");
            assert!(session_status.repo_root.contains(root.to_str().unwrap()));
            assert!(session_status.runtime_db.ends_with("structure.db"));
            assert!(session_status.run_count >= 3);
            assert!(session_status.artifact_count >= 3);
            assert!(session_status.recent_event_count > 0);
            assert!(session_status.last_event_sequence.is_some());
            assert!(!session_status.llm_config.configured);
            assert!(!llm_diagnostic.configured);
            assert!(!llm_diagnostic.ok);
            assert!(llm_diagnostic.error.is_some());
            assert!(!worktree.available || worktree.error.is_some() || worktree.branch.is_some());
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

    #[test]
    fn desktop_local_ui_keeps_benchmarks_out_of_agent_surface() {
        let local_ui = include_str!("../local-ui/index.html").to_lowercase();

        assert!(!local_ui.contains("benchmark"));
        assert!(!local_ui.contains("bench"));
    }

    #[test]
    fn desktop_local_ui_evidence_command_renders_agent_context() {
        let local_ui = include_str!("../local-ui/index.html");

        assert!(local_ui.contains("function renderEvidenceText(evidence)"));
        assert!(local_ui.contains("Prompt References"));
        assert!(local_ui.contains("Agent Instructions"));
        assert!(local_ui.contains("Worktree Changes"));
        assert!(local_ui.contains("eventLogEl.textContent = renderEvidenceText(evidence);"));
    }

    #[test]
    fn desktop_local_ui_exposes_segmented_agent_mode_control() {
        let local_ui = include_str!("../local-ui/index.html");

        assert!(local_ui.contains("role=\"radiogroup\" aria-label=\"Agent mode\""));
        assert!(local_ui.contains("data-agent-mode=\"code_agent\""));
        assert!(local_ui.contains("data-agent-mode=\"chat\""));
        assert!(local_ui.contains("function setAgentMode(mode, announce = true)"));
        assert!(local_ui.contains("mode: state.agentMode"));
    }

    #[test]
    fn desktop_local_ui_supports_run_continuation_command() {
        let local_ui = include_str!("../local-ui/index.html");

        assert!(local_ui.contains("invoke(\"local_agent_continue_attempt\""));
        assert!(local_ui.contains("case \"/continue\":"));
        assert!(local_ui.contains("case \"/resume\":"));
        assert!(local_ui.contains("continueLocalAgentAttempt"));
        assert!(local_ui.contains("function performContinuationRun"));
        assert!(local_ui.contains("continueRun.textContent = \"Continue\""));
    }

    #[test]
    fn desktop_local_ui_links_chat_turns_to_run_evidence() {
        let local_ui = include_str!("../local-ui/index.html");

        assert!(local_ui.contains("className = \"chat-actions\""));
        assert!(local_ui.contains("inspect.addEventListener(\"click\", () => selectRun(turn));"));
        assert!(local_ui.contains("await previewProposalForRun(turn.run_id);"));
    }

    #[test]
    fn desktop_local_ui_reviews_proposals_from_preview_surface() {
        let local_ui = include_str!("../local-ui/index.html");

        assert!(local_ui.contains("id=\"preview-dry-run\""));
        assert!(local_ui.contains("id=\"preview-apply\""));
        assert!(local_ui.contains("function reviewPreviewProposal"));
        assert!(local_ui.contains("await applyProposal(artifactId, true)"));
        assert!(local_ui.contains("await applyProposal(artifactId, false)"));
        assert!(local_ui.contains("state.previewArtifact"));
    }

    #[test]
    fn desktop_local_ui_attaches_repo_previews_to_prompt() {
        let local_ui = include_str!("../local-ui/index.html");

        assert!(local_ui.contains("id=\"preview-attach\""));
        assert!(local_ui.contains("function attachPreviewFileToPrompt"));
        assert!(local_ui.contains("state.previewRepoPath"));
        assert!(local_ui.contains("state.previewRepoPath = result.output.path ?? null"));
        assert!(local_ui.contains("const reference = `@${state.previewRepoPath}`"));
    }

    #[test]
    fn desktop_local_ui_surfaces_current_worktree_snapshot() {
        let local_ui = include_str!("../local-ui/index.html");

        assert!(local_ui.contains("id=\"worktree-status\""));
        assert!(local_ui.contains("id=\"refresh-worktree\""));
        assert!(local_ui.contains("invoke(\"local_worktree_snapshot\""));
        assert!(local_ui.contains("function renderWorktree(worktree)"));
        assert!(local_ui.contains("case \"/worktree\":"));
    }

    #[test]
    fn desktop_app_uses_local_ui_and_runtime_entrypoints() {
        let config: serde_json::Value =
            serde_json::from_str(include_str!("../tauri.conf.json")).unwrap();
        let package: serde_json::Value =
            serde_json::from_str(include_str!("../../package.json")).unwrap();
        let local_ui = include_str!("../local-ui/index.html");

        assert_eq!(config["build"]["frontendDist"], "local-ui");
        assert!(config["build"].get("devUrl").is_none());
        assert_eq!(
            package["scripts"]["desktop:dev"],
            "cargo run --manifest-path src-tauri/Cargo.toml"
        );
        assert_eq!(
            package["scripts"]["desktop:build"],
            "cargo build --release --manifest-path src-tauri/Cargo.toml"
        );
        assert!(local_ui.contains("invoke(\"local_agent_run_attempt\""));
        assert!(local_ui.contains("invoke(\"local_agent_continue_attempt\""));
        assert!(local_ui.contains("invoke(\"local_session_status\""));
        assert!(local_ui.contains("invoke(\"local_worktree_snapshot\""));
        assert!(local_ui.contains("invoke(\"local_run_transcript\""));
        assert!(!local_ui.contains("fetch("));
        assert!(!local_ui.contains("localhost"));
        assert!(!local_ui.contains("127.0.0.1"));
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
