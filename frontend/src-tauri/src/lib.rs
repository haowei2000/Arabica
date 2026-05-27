use serde::Serialize;
use structure_local_core::{collect_snapshot, default_repo_root, structure_core_manifest};
use structure_local_runtime::{
    ContinuationRequest, LocalAgentMode, LocalAgentRuntime, LocalToolCall, RunRequest,
    WorkspaceContinuationRequest,
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
    task_count: usize,
    active_task_count: usize,
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
    let tasks = runtime.list_tasks(Some(&workspace_id), None, 1_000)?;
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
        task_count: tasks.len(),
        active_task_count: tasks
            .iter()
            .filter(|task| matches!(task.status.as_str(), "todo" | "in_progress"))
            .count(),
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
fn local_agent_context(
    workspace_id: Option<String>,
    mode: Option<String>,
) -> Result<structure_local_runtime::LocalAgentContext, String> {
    let runtime = LocalAgentRuntime::open_default()?;
    runtime.agent_context(workspace_id.as_deref(), Some(parse_local_agent_mode(mode)))
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
fn local_workspace_continue_attempt(
    workspace_id: Option<String>,
    extra_instruction: Option<String>,
    mode: Option<String>,
    limit: Option<usize>,
) -> Result<structure_local_runtime::RunAttempt, String> {
    let runtime = LocalAgentRuntime::open_default()?;
    runtime.workspace_continuation_attempt(WorkspaceContinuationRequest {
        workspace_id,
        extra_instruction,
        mode: Some(parse_local_agent_mode(mode)),
        limit: limit.unwrap_or(12),
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
fn local_command_turns(
    workspace_id: Option<String>,
    limit: Option<usize>,
) -> Result<Vec<structure_local_runtime::CommandTurn>, String> {
    let runtime = LocalAgentRuntime::open_default()?;
    runtime.command_turns(workspace_id.as_deref(), limit.unwrap_or(20))
}

#[tauri::command]
fn record_local_command_turn(
    workspace_id: Option<String>,
    input: String,
    output: String,
    status: String,
) -> Result<structure_local_runtime::CommandTurn, String> {
    let runtime = LocalAgentRuntime::open_default()?;
    runtime.record_command_turn(workspace_id.as_deref(), &input, &output, &status, "desktop")
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
fn local_run_event_gc(
    run_id: String,
    retain_last: Option<usize>,
) -> Result<structure_local_runtime::EventGcPreview, String> {
    let runtime = LocalAgentRuntime::open_default()?;
    runtime.run_event_gc_preview(&run_id, retain_last)
}

#[tauri::command]
fn local_run_tool_trace(
    run_id: String,
) -> Result<Vec<structure_local_runtime::LocalToolTraceEntry>, String> {
    let runtime = LocalAgentRuntime::open_default()?;
    runtime.run_tool_trace(&run_id)
}

#[tauri::command]
fn local_run_core_trace(
    run_id: String,
) -> Result<structure_local_runtime::LocalRunCoreTrace, String> {
    let runtime = LocalAgentRuntime::open_default()?;
    runtime.run_core_trace(&run_id)
}

#[tauri::command]
fn local_run_plan(run_id: String) -> Result<structure_local_runtime::LocalRunPlan, String> {
    let runtime = LocalAgentRuntime::open_default()?;
    runtime.run_plan(&run_id)
}

#[tauri::command]
fn local_run_compact(run_id: String) -> Result<structure_local_runtime::LocalRunCompact, String> {
    let runtime = LocalAgentRuntime::open_default()?;
    runtime.run_compact(&run_id)
}

#[tauri::command]
fn local_run_review(run_id: String) -> Result<structure_local_runtime::LocalRunReview, String> {
    let runtime = LocalAgentRuntime::open_default()?;
    runtime.run_review(&run_id)
}

#[tauri::command]
fn record_local_run_checkpoint(
    run_id: String,
    note: String,
) -> Result<structure_local_runtime::RunCheckpoint, String> {
    let runtime = LocalAgentRuntime::open_default()?;
    runtime.record_run_checkpoint(&run_id, &note)
}

#[tauri::command]
fn local_run_status(
    run_id: String,
) -> Result<structure_local_runtime::LocalRunStatusSnapshot, String> {
    let runtime = LocalAgentRuntime::open_default()?;
    runtime.run_status_snapshot(&run_id)
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
fn local_workspace_compact(
    workspace_id: Option<String>,
    limit: Option<usize>,
) -> Result<structure_local_runtime::WorkspaceCompact, String> {
    let runtime = LocalAgentRuntime::open_default()?;
    runtime.workspace_compact(workspace_id.as_deref(), limit.unwrap_or(12))
}

#[tauri::command]
fn local_workspace_usage(
    workspace_id: Option<String>,
    limit: Option<usize>,
) -> Result<structure_local_runtime::WorkspaceUsageSummary, String> {
    let runtime = LocalAgentRuntime::open_default()?;
    runtime.workspace_usage(workspace_id.as_deref(), limit.unwrap_or(20))
}

#[tauri::command]
fn local_tasks(
    workspace_id: Option<String>,
    status: Option<String>,
    limit: Option<usize>,
) -> Result<Vec<structure_local_runtime::LocalTaskRecord>, String> {
    let runtime = LocalAgentRuntime::open_default()?;
    runtime.list_tasks(
        workspace_id.as_deref(),
        status.as_deref(),
        limit.unwrap_or(20),
    )
}

#[tauri::command]
fn create_local_task(
    workspace_id: Option<String>,
    run_id: Option<String>,
    title: String,
    priority: Option<String>,
) -> Result<structure_local_runtime::LocalTaskRecord, String> {
    let runtime = LocalAgentRuntime::open_default()?;
    runtime.create_task(workspace_id, run_id.as_deref(), &title, priority.as_deref())
}

#[tauri::command]
fn update_local_task_status(
    task_id: String,
    status: String,
    note: Option<String>,
) -> Result<structure_local_runtime::LocalTaskRecord, String> {
    let runtime = LocalAgentRuntime::open_default()?;
    runtime.update_task_status(&task_id, &status, note.as_deref())
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
fn remember_local_knowledge(
    text: String,
    workspace_id: Option<String>,
) -> Result<structure_local_runtime::KnowledgeSource, String> {
    let runtime = LocalAgentRuntime::open_default()?;
    runtime.add_text_knowledge_source(workspace_id, &text)
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
fn rate_local_knowledge_source(
    source_id: String,
    run_id: Option<String>,
    rating: u8,
    note: Option<String>,
) -> Result<structure_local_runtime::SourceRating, String> {
    let runtime = LocalAgentRuntime::open_default()?;
    runtime.rate_knowledge_source(
        &source_id,
        run_id.as_deref(),
        rating,
        note.as_deref().unwrap_or(""),
    )
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
fn review_local_proposal(
    artifact_id: String,
) -> Result<structure_local_runtime::ProposalReview, String> {
    let runtime = LocalAgentRuntime::open_default()?;
    runtime.review_code_change_proposal(&artifact_id)
}

#[tauri::command]
fn apply_local_proposal(
    artifact_id: String,
    dry_run: Option<bool>,
) -> Result<structure_local_runtime::ProposalApplyResult, String> {
    let runtime = LocalAgentRuntime::open_default()?;
    runtime.apply_code_change_proposal(&artifact_id, dry_run.unwrap_or(false))
}

#[tauri::command]
fn rollback_local_proposal(
    artifact_id: String,
) -> Result<structure_local_runtime::ProposalRollbackResult, String> {
    let runtime = LocalAgentRuntime::open_default()?;
    runtime.rollback_code_change_proposal(&artifact_id)
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![
            local_snapshot,
            local_session_status,
            local_llm_diagnostic,
            local_worktree_snapshot,
            local_agent_context,
            core_manifest,
            core_parity_report,
            local_repo_entries,
            local_repo_search,
            read_local_repo_file,
            run_local_command,
            local_agent_run,
            local_agent_run_attempt,
            local_agent_continue_attempt,
            local_workspace_continue_attempt,
            local_chat_turns,
            local_command_turns,
            record_local_command_turn,
            local_runs,
            create_local_workspace,
            local_workspaces,
            local_workspace,
            local_run,
            local_run_events,
            local_run_evidence,
            local_run_event_gc,
            local_run_tool_trace,
            local_run_core_trace,
            local_run_plan,
            local_run_compact,
            local_run_review,
            record_local_run_checkpoint,
            local_run_status,
            local_run_transcript,
            local_workspace_replay,
            local_workspace_compact,
            local_workspace_usage,
            local_tasks,
            create_local_task,
            update_local_task_status,
            local_workspace_event_feed,
            local_evidence_bundle,
            add_local_knowledge,
            remember_local_knowledge,
            local_knowledge,
            local_knowledge_source,
            read_local_knowledge_source,
            remove_local_knowledge,
            rate_local_knowledge_source,
            local_artifacts,
            read_local_artifact,
            latest_local_proposal,
            review_local_proposal,
            apply_local_proposal,
            rollback_local_proposal,
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
            let source = add_local_knowledge(
                root.join("note.md").display().to_string(),
                Some(workspace.workspace_id.clone()),
            )?;
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
            let command_turn = record_local_command_turn(
                Some(workspace.workspace_id.clone()),
                "/context".to_string(),
                "Agent Context\nWorkspace: desktop-test".to_string(),
                "ok".to_string(),
            )?;
            let command_turns = local_command_turns(Some(workspace.workspace_id.clone()), Some(5))?;
            let source_rating = rate_local_knowledge_source(
                source.source_id.clone(),
                Some(run.run.run_id.clone()),
                4,
                Some("desktop grounding".to_string()),
            )?;
            let checkpoint = record_local_run_checkpoint(
                run.run.run_id.clone(),
                "Desktop human checkpoint: keep Structure Core as the event loop.".to_string(),
            )?;
            let event_gc = local_run_event_gc(run.run.run_id.clone(), Some(4))?;
            let tool_trace = local_run_tool_trace(run.run.run_id.clone())?;
            let transcript = local_run_transcript(run.run.run_id.clone())?;
            let review = local_run_review(run.run.run_id.clone())?;
            let run_status = local_run_status(run.run.run_id.clone())?;
            let task = create_local_task(
                Some(workspace.workspace_id.clone()),
                Some(run.run.run_id.clone()),
                "Review desktop local task evidence".to_string(),
                Some("high".to_string()),
            )?;
            let updated_task = update_local_task_status(
                task.task_id.clone(),
                "done".to_string(),
                Some("desktop verified".to_string()),
            )?;
            let tasks = local_tasks(
                Some(workspace.workspace_id.clone()),
                Some("done".to_string()),
                Some(20),
            )?;
            let replay = local_workspace_replay(Some(workspace.workspace_id.clone()), Some(200))?;
            let feed =
                local_workspace_event_feed(Some(workspace.workspace_id.clone()), Some(0), Some(5))?;
            let artifacts = local_artifacts(Some(workspace.workspace_id.clone()), None, Some(10))?;
            let proposal_preview = latest_local_proposal(
                Some(workspace.workspace_id.clone()),
                Some(run.run.run_id.clone()),
                Some(64_000),
            )?;
            let proposal_review =
                review_local_proposal(proposal_preview.artifact.artifact_id.clone())?;
            let dry_apply =
                apply_local_proposal(proposal_preview.artifact.artifact_id.clone(), Some(true))?;
            let real_apply =
                apply_local_proposal(proposal_preview.artifact.artifact_id.clone(), Some(false))?;
            let rollback = rollback_local_proposal(proposal_preview.artifact.artifact_id.clone())?;
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
            assert_eq!(source_rating.rating, 4);
            assert_eq!(
                source_rating.run_id.as_deref(),
                Some(run.run.run_id.as_str())
            );
            assert_eq!(checkpoint.run_id, run.run.run_id);
            assert!(checkpoint.note.contains("Structure Core"));
            assert_eq!(event_gc.summary.policy_id, "retain_last_n_events");
            assert_eq!(event_gc.summary.retained_event_count, 4);
            assert_eq!(
                event_gc.summary.retained_event_count + event_gc.summary.filtered_event_count,
                event_gc.retained_events.len() + event_gc.filtered_events.len()
            );
            assert!(!tool_trace.is_empty());
            assert!(tool_trace
                .iter()
                .any(|entry| entry.completed_sequence.is_some()));
            let core_trace = local_run_core_trace(run.run.run_id.clone())?;
            assert_eq!(core_trace.run.run_id, run.run.run_id);
            assert!(core_trace.core_aligned);
            assert_eq!(core_trace.manifest_schema_version, "2026.05");
            assert!(core_trace.flow_path.contains(&"goal".to_string()));
            assert!(core_trace
                .steps
                .iter()
                .any(|step| step.primitive_id == "reproducible_evidence"));
            let plan = local_run_plan(run.run.run_id.clone())?;
            assert_eq!(plan.run.run_id, run.run.run_id);
            assert!(plan.step_count >= 1);
            assert_eq!(plan.completed_step_count, plan.step_count);
            assert!(plan
                .steps
                .iter()
                .any(|step| step.title.contains("Workspace Context Replay")));
            let compact = local_run_compact(run.run.run_id.clone())?;
            assert_eq!(compact.run.run_id, run.run.run_id);
            assert!(compact.core_aligned);
            assert!(compact.summary.contains(&run.run.run_id));
            assert!(compact
                .continuation_context
                .contains("Compact Structure context"));
            let workspace_compact =
                local_workspace_compact(Some(workspace.workspace_id.clone()), Some(12))?;
            assert_eq!(workspace_compact.workspace_id, workspace.workspace_id);
            assert!(workspace_compact.core_aligned);
            assert!(workspace_compact
                .continuation_context
                .contains("Compact Structure workspace context"));
            let workspace_usage =
                local_workspace_usage(Some(workspace.workspace_id.clone()), Some(20))?;
            assert_eq!(workspace_usage.workspace_id, workspace.workspace_id);
            assert!(workspace_usage.core_aligned);
            assert!(workspace_usage.run_count >= 1);
            assert!(workspace_usage.event_count >= run.events.len());
            assert!(workspace_usage.summary.contains("total tokens"));
            let workspace_continuation = local_workspace_continue_attempt(
                Some(workspace.workspace_id.clone()),
                Some("Continue from the desktop workspace compact.".to_string()),
                Some("chat".to_string()),
                Some(12),
            )?;
            assert_eq!(
                workspace_continuation.run.workspace_id,
                workspace.workspace_id
            );
            assert!(workspace_continuation
                .run
                .prompt
                .contains("Continue Structure workspace/session"));
            assert!(workspace_continuation
                .run
                .prompt
                .contains("Compact Structure workspace context"));
            assert_eq!(review.run.run_id, run.run.run_id);
            assert!(review.core_aligned);
            assert!(review.proposal_artifact.is_some());
            assert!(review
                .next_actions
                .iter()
                .any(|action| action.contains("dry-run")));
            assert_eq!(run_status.run.run_id, run.run.run_id);
            assert!(run_status.terminal);
            assert!(run_status.core_aligned);
            assert!(run_status.latest_event.is_some());
            assert!(run_status.proposal_artifact.is_some());
            assert!(run_status
                .next_actions
                .iter()
                .any(|action| action.contains("proposal")));
            assert_eq!(task.status, "todo");
            assert_eq!(task.priority, "high");
            assert_eq!(task.run_id.as_deref(), Some(run.run.run_id.as_str()));
            assert_eq!(updated_task.status, "done");
            assert!(tasks.iter().any(|item| item.task_id == task.task_id));
            assert!(replay.tasks.iter().any(|item| item.task_id == task.task_id));
            assert!(workspace_compact.task_count >= 1);
            assert!(workspace_compact.continuation_context.contains("Tasks:"));
            assert!(workspace_usage.task_count >= 1);
            assert_eq!(workspace_usage.active_task_count, 0);
            assert!(workspace_usage.summary.contains("tasks"));
            assert_eq!(
                proposal_review.artifact.artifact_id,
                proposal_preview.artifact.artifact_id
            );
            assert!(proposal_review.can_apply);
            assert!(proposal_review.dry_run_required);
            assert!(proposal_review
                .checks
                .iter()
                .any(|check| check.id == "patch_context" && check.status == "ok"));
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
            assert_eq!(command_turn.input, "/context");
            assert_eq!(command_turn.surface, "desktop");
            assert!(command_turns
                .iter()
                .any(|item| item.input == "/context" && item.status == "ok"));
            assert_eq!(transcript.run.run_id, run.run.run_id);
            assert!(transcript.chat_turn.is_some());
            assert_eq!(transcript.evidence.event_count, transcript.events.len());
            assert!(transcript
                .evidence
                .primitive_ids
                .contains(&"reproducible_evidence".to_string()));
            assert_eq!(transcript.evidence.source_ratings.len(), 1);
            assert_eq!(transcript.evidence.checkpoints.len(), 1);
            assert!(transcript.evidence.checkpoints[0]
                .note
                .contains("Structure Core"));
            assert_eq!(
                transcript.evidence.event_gc.policy_id,
                "retain_last_n_events"
            );
            assert!(transcript
                .evidence
                .primitive_ids
                .contains(&"source_evaluation".to_string()));
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
                    && event.kind == "command_turn_recorded"
                    && event.payload["input"] == "/context"
            }));
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
            assert!(real_apply.applied);
            assert!(real_apply.backup_artifact.is_some());
            assert!(rollback.restored);
            assert_eq!(
                rollback.artifact.artifact_id,
                proposal_preview.artifact.artifact_id
            );
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
            assert!(session_status.task_count >= 1);
            assert_eq!(session_status.active_task_count, 0);
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
        assert!(local_ui.contains("function renderAgentContextText(context)"));
        assert!(local_ui.contains("Workspace Tasks"));
        assert!(local_ui.contains("Tasks: ${tasks.length}"));
        assert!(local_ui.contains("id=\"show-context\""));
        assert!(local_ui.contains("setContextBusy(true)"));
        assert!(local_ui.contains("eventLogEl.textContent = renderAgentContextText(context);"));
        assert!(local_ui.contains("appendCommandMessage(\"/context\""));
        assert!(local_ui.contains("case \"/context\":"));
        assert!(local_ui.contains("case \"/remember\":"));
        assert!(local_ui.contains("case \"/recall\":"));
        assert!(local_ui.contains("case \"/forget\":"));
        assert!(local_ui.contains("case \"/rate\":"));
        assert!(local_ui.contains("rate_local_knowledge_source"));
        assert!(local_ui.contains("Source Ratings"));
        assert!(local_ui.contains("Run Checkpoints"));
        assert!(local_ui.contains("case \"/checkpoint\":"));
        assert!(local_ui.contains("invoke(\"record_local_run_checkpoint\""));
        assert!(local_ui.contains("id=\"record-checkpoint\""));
        assert!(local_ui.contains("function recordDecisionForRun"));
        assert!(local_ui.contains("setCheckpointBusy(true)"));
        assert!(local_ui.contains("Prompt References"));
        assert!(local_ui.contains("Agent Instructions"));
        assert!(local_ui.contains("Worktree Changes"));
        assert!(local_ui.contains("Model usage:"));
        assert!(local_ui.contains("model_usage"));
        assert!(local_ui.contains("eventLogEl.textContent = renderEvidenceText(evidence);"));
        assert!(local_ui.contains("id=\"command-map\""));
        assert!(local_ui.contains("Desktop Agent Command Map"));
        assert!(local_ui.contains("Run Loop"));
        assert!(local_ui.contains("Workspace Context"));
        assert!(local_ui.contains("Run Evidence"));
        assert!(local_ui.contains("Session And Proposals"));
        assert!(local_ui.contains("Evaluation adapters stay outside this desktop surface"));
        assert!(local_ui.contains("commandMapEl.addEventListener(\"click\""));
        assert!(local_ui.contains("appendCommandMessage(\"/map\""));
        assert!(local_ui.contains("case \"/map\":"));
        assert!(local_ui.contains("case \"/commands\":"));
        assert!(local_ui.contains("case \"/history\":"));
        assert!(local_ui.contains("case \"/chat-history\":"));
        assert!(local_ui.contains("invoke(\"local_command_turns\""));
        assert!(local_ui.contains("invoke(\"record_local_command_turn\""));
        assert!(local_ui.contains("function renderChatHistoryText(turns)"));
        assert!(local_ui.contains("function previewChatHistory(commandInput = null)"));
        assert!(local_ui.contains("function renderCommandMessages(messages)"));
        assert!(local_ui.contains("renderChat(turns);"));
        assert!(local_ui.contains("recordLocalCommandTurn(input, output, status).catch"));
        assert!(local_ui.contains("/history -> persisted chat/code-agent turns"));
        assert!(local_ui.contains("function renderUsageText(evidence)"));
        assert!(local_ui.contains("case \"/usage\":"));
        assert!(local_ui.contains("eventLogEl.textContent = renderUsageText(evidence);"));
        assert!(local_ui.contains("function renderEventGcText(preview)"));
        assert!(local_ui.contains("case \"/gc\":"));
        assert!(local_ui.contains("id=\"run-tools\""));
        assert!(local_ui.contains("setRunToolsBusy(true)"));
        assert!(local_ui.contains("function previewRunToolTraceForRun"));
        assert!(local_ui.contains("function renderToolTraceText(trace)"));
        assert!(local_ui.contains("case \"/tools\":"));
        assert!(local_ui.contains("previewRunToolTraceForRun(runId, `/tools ${runId}`)"));
        assert!(local_ui.contains("function renderRunPlanText(plan)"));
        assert!(local_ui.contains("case \"/plan\":"));
        assert!(local_ui.contains("invoke(\"local_run_plan\""));
        assert!(local_ui.contains("function renderRunCompactText(compact)"));
        assert!(local_ui.contains("case \"/compact\":"));
        assert!(local_ui.contains("invoke(\"local_run_compact\""));
        assert!(local_ui.contains("compact.textContent = \"Compact\""));
        assert!(local_ui.contains("function renderWorkspaceCompactText(compact)"));
        assert!(local_ui.contains("case \"/session\":"));
        assert!(local_ui.contains("invoke(\"local_workspace_compact\""));
        assert!(local_ui.contains("previewWorkspaceCompact(\"/session\")"));
        assert!(local_ui.contains("id=\"session-continue\""));
        assert!(local_ui.contains("function performWorkspaceContinuationRun"));
        assert!(local_ui.contains("case \"/session-continue\":"));
        assert!(local_ui.contains("invoke(\"local_workspace_continue_attempt\""));
        assert!(local_ui.contains("id=\"session-usage\""));
        assert!(local_ui.contains("function renderWorkspaceUsageText(usage)"));
        assert!(local_ui.contains("active_task_count"));
        assert!(local_ui.contains("case \"/session-usage\":"));
        assert!(local_ui.contains("invoke(\"local_workspace_usage\""));
        assert!(local_ui.contains("previewWorkspaceUsage(\"/session-usage\")"));
        assert!(local_ui.contains("function renderCoreTraceText(trace)"));
        assert!(local_ui.contains("case \"/trace\":"));
        assert!(local_ui.contains("invoke(\"local_run_core_trace\""));
        assert!(local_ui.contains("function renderRunReviewText(review)"));
        assert!(local_ui.contains("case \"/review\":"));
        assert!(local_ui.contains("invoke(\"local_run_review\""));
        assert!(local_ui.contains("case \"code_change_reviewed\":"));
        assert!(local_ui.contains("id=\"run-status\""));
        assert!(local_ui.contains("function renderRunStatusText(status)"));
        assert!(local_ui.contains("case \"/run-status\":"));
        assert!(local_ui.contains("invoke(\"local_run_status\""));
        assert!(local_ui.contains("previewRunStatusForRun(runId, `/run-status ${runId}`)"));
        assert!(local_ui.contains("id=\"show-tasks\""));
        assert!(local_ui.contains("function renderTasksText(tasks)"));
        assert!(local_ui.contains("case \"/tasks\":"));
        assert!(local_ui.contains("invoke(\"local_tasks\""));
        assert!(local_ui.contains("previewTasks(null, \"/tasks\")"));
        assert!(local_ui.contains("invoke(\"create_local_task\""));
        assert!(local_ui.contains("invoke(\"update_local_task_status\""));
        assert!(local_ui.contains("function renderDoctorText(status, diagnostic, parity)"));
        assert!(local_ui.contains("case \"/doctor\":"));
        assert!(local_ui.contains("id=\"show-doctor\""));
        assert!(local_ui.contains("setDoctorBusy(true)"));
        assert!(local_ui
            .contains("eventLogEl.textContent = renderDoctorText(status, diagnostic, parity);"));
        assert!(local_ui.contains("appendCommandMessage(\"/doctor\""));
        assert!(local_ui.contains("case \"/diff\":"));
        assert!(local_ui.contains("/diff [run_id]"));
        assert!(local_ui.contains("case \"/parity\":"));
        assert!(local_ui.contains("case \"/bundle\":"));
        assert!(local_ui.contains("case \"/artifact\":"));
        assert!(local_ui.contains("function previewCoreParity(commandInput = null)"));
        assert!(local_ui.contains("function previewEvidenceBundle(commandInput = null)"));
        assert!(local_ui
            .contains("function previewArtifactById(artifactId = null, commandInput = null)"));
        assert!(local_ui.contains("previewCoreParity(\"/parity\")"));
        assert!(local_ui.contains("previewEvidenceBundle(\"/bundle\")"));
        assert!(local_ui.contains("previewArtifactById(null, \"/artifact\")"));
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
        assert!(local_ui.contains("case \"/retry\":"));
        assert!(local_ui.contains("continueLocalAgentAttempt"));
        assert!(local_ui.contains("function performContinuationRun"));
        assert!(local_ui.contains("continueRun.textContent = \"Continue\""));
        assert!(local_ui.contains("retryRun.textContent = \"Retry\""));
        assert!(local_ui.contains("function retryInstruction()"));
    }

    #[test]
    fn desktop_local_ui_links_chat_turns_to_run_evidence() {
        let local_ui = include_str!("../local-ui/index.html");

        assert!(local_ui.contains("className = \"chat-actions\""));
        assert!(local_ui.contains("inspect.addEventListener(\"click\", () => selectRun(turn));"));
        assert!(local_ui.contains("review.textContent = \"Review\""));
        assert!(local_ui.contains("decision.textContent = \"Decision\""));
        assert!(local_ui.contains("plan.textContent = \"Plan\""));
        assert!(local_ui.contains("tools.textContent = \"Tools\""));
        assert!(local_ui.contains("rerunRun.textContent = \"Rerun\""));
        assert!(local_ui.contains("await performPromptRun(turn.user_message"));
        assert!(local_ui.contains("heading: \"History Rerun\""));
        assert!(
            local_ui.contains("previewRunStatusForRun(turn.run_id, `/run-status ${turn.run_id}`)")
        );
        assert!(local_ui.contains("previewRunPlanForRun(turn.run_id, `/plan ${turn.run_id}`)"));
        assert!(
            local_ui.contains("previewRunToolTraceForRun(turn.run_id, `/tools ${turn.run_id}`)")
        );
        assert!(
            local_ui.contains("previewRunCompactForRun(turn.run_id, `/compact ${turn.run_id}`)")
        );
        assert!(local_ui.contains("previewRunReviewForRun(turn.run_id, `/review ${turn.run_id}`)"));
        assert!(local_ui.contains("recordDecisionForRun(turn.run_id, null, \"/checkpoint\")"));
        assert!(local_ui.contains("previewProposalForRun(turn.run_id, `/proposal ${turn.run_id}`)"));
    }

    #[test]
    fn desktop_local_ui_recalls_composer_history_from_persisted_turns() {
        let local_ui = include_str!("../local-ui/index.html");

        assert!(local_ui.contains("composerHistory: []"));
        assert!(local_ui.contains("function syncComposerHistory()"));
        assert!(local_ui.contains("...state.chatTurns.map((turn) => ({"));
        assert!(local_ui.contains("...state.commandMessages.map((message) => ({"));
        assert!(local_ui.contains("function recallComposerHistory(direction)"));
        assert!(local_ui.contains("function resetComposerHistoryNavigation()"));
        assert!(local_ui.contains("canRecallComposerHistory(event)"));
        assert!(local_ui.contains("event.key === \"ArrowUp\" || event.key === \"ArrowDown\""));
        assert!(local_ui.contains("function selectHistoryPromptForRerun(parts)"));
        assert!(local_ui.contains("function performPromptRun(prompt, options = {})"));
        assert!(local_ui.contains("case \"/rerun\":"));
        assert!(local_ui.contains("case \"/repeat\":"));
        assert!(local_ui.contains("await performPromptRun(selection.prompt"));
        assert!(local_ui.contains("/rerun [history-number]"));
        assert!(local_ui.contains("History Rerun #${selection.index}"));
        assert!(local_ui.contains("syncComposerHistory();"));
        assert!(local_ui.contains("resetComposerHistoryNavigation();"));
    }

    #[test]
    fn desktop_local_ui_reviews_proposals_from_preview_surface() {
        let local_ui = include_str!("../local-ui/index.html");

        assert!(local_ui.contains("id=\"preview-dry-run\""));
        assert!(local_ui.contains("id=\"preview-apply\""));
        assert!(local_ui.contains("id=\"preview-rollback\""));
        assert!(local_ui.contains("id=\"preview-review\""));
        assert!(local_ui.contains("function reviewPreviewProposalRisk"));
        assert!(local_ui.contains("function rollbackPreviewProposal"));
        assert!(local_ui.contains("invoke(\"review_local_proposal\""));
        assert!(local_ui.contains("invoke(\"rollback_local_proposal\""));
        assert!(local_ui.contains("function previewProposalForRun(runId, commandInput = null)"));
        assert!(local_ui.contains("previewProposalForRun("));
        assert!(local_ui.contains("`/proposal ${state.selectedRunId}`"));
        assert!(local_ui.contains("case \"/risk\":"));
        assert!(local_ui.contains("function reviewPreviewProposal"));
        assert!(local_ui.contains("commandInput: \"/dry-run\""));
        assert!(local_ui.contains("commandInput: \"/apply\""));
        assert!(local_ui.contains("reviewPreviewProposalRisk(null, \"/risk\")"));
        assert!(local_ui.contains("rollbackPreviewProposal(null, \"/rollback\")"));
        assert!(
            local_ui.contains("appendCommandMessage(input, eventLogEl.textContent, \"cancelled\")")
        );
        assert!(local_ui.contains("await applyProposal(artifactId, true)"));
        assert!(local_ui.contains("await applyProposal(artifactId, false)"));
        assert!(local_ui.contains("case \"/dry-run\":"));
        assert!(local_ui.contains("case \"/rollback\":"));
        assert!(local_ui.contains("/dry-run [run_id|artifact_id]"));
        assert!(local_ui.contains("/rollback [artifact_id]"));
        assert!(local_ui.contains("state.previewArtifact"));
    }

    #[test]
    fn desktop_local_ui_attaches_repo_previews_to_prompt() {
        let local_ui = include_str!("../local-ui/index.html");

        assert!(local_ui.contains("id=\"preview-attach\""));
        assert!(local_ui.contains("function attachPreviewFileToPrompt"));
        assert!(local_ui.contains("function renderRepoToolResult(result, commandInput = null)"));
        assert!(local_ui.contains("renderRepoToolResult(await listRepoEntries(), commandInput)"));
        assert!(local_ui.contains("renderRepoToolResult(await searchRepo(query), commandInput)"));
        assert!(local_ui.contains("renderRepoToolResult(await readRepoFile(path), commandInput)"));
        assert!(local_ui.contains("renderRepoToolResult(await runRepoCommand(argv), commandInput)"));
        assert!(local_ui.contains("appendCommandMessage(commandInput, eventLogEl.textContent"));
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
        assert!(local_ui.contains("function previewWorktree(commandInput = null)"));
        assert!(local_ui.contains("case \"/worktree\":"));
        assert!(local_ui.contains("previewWorktree(\"/worktree\")"));
        assert!(local_ui.contains("function renderWorkspaceReplayText(replay)"));
        assert!(local_ui.contains("function previewWorkspaceReplay(commandInput = null"));
        assert!(local_ui.contains("case \"/replay\":"));
        assert!(local_ui.contains("previewWorkspaceReplay(\"/replay\", { refreshAfter: true })"));
    }

    #[test]
    fn desktop_local_ui_streams_live_events_into_inflight_chat() {
        let local_ui = include_str!("../local-ui/index.html");

        assert!(local_ui.contains("function updateInFlightRunEvents(events)"));
        assert!(local_ui.contains("function describeInFlightEvent(event)"));
        assert!(local_ui.contains("updateInFlightRunEvents(feed.events);"));
        assert!(local_ui.contains("state.inFlightRun.eventCount"));
        assert!(local_ui.contains("Latest event:"));
        assert!(local_ui.contains("preserveLive: true"));
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
        assert!(local_ui.contains("invoke(\"local_llm_diagnostic\""));
        assert!(local_ui.contains("invoke(\"core_parity_report\""));
        assert!(local_ui.contains("invoke(\"local_agent_context\""));
        assert!(local_ui.contains("invoke(\"remember_local_knowledge\""));
        assert!(local_ui.contains("invoke(\"read_local_knowledge_source\""));
        assert!(local_ui.contains("invoke(\"remove_local_knowledge\""));
        assert!(local_ui.contains("invoke(\"rate_local_knowledge_source\""));
        assert!(local_ui.contains("invoke(\"local_worktree_snapshot\""));
        assert!(local_ui.contains("invoke(\"local_run_transcript\""));
        assert!(local_ui.contains("invoke(\"local_run_event_gc\""));
        assert!(local_ui.contains("invoke(\"local_run_tool_trace\""));
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
