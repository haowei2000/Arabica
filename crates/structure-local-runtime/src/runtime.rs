use crate::model::{
    selected_planning_provider, selected_synthesis_provider, EnvApiModelProvider, ModelRequest,
};
use crate::store::{new_id, SqliteLocalStore};
use crate::tools::{BuiltinLocalToolRegistry, LocalToolRegistry};
use crate::types::{
    AgentInstruction, ArtifactPreview, ArtifactRecord, ChatTurn, CoreExecutionTrace,
    KnowledgeSource, KnowledgeSourcePreview, LocalAgentContext, LocalAgentMode, LocalEvent,
    LocalEvidenceBundle, LocalLlmDiagnostic, LocalToolCall, LocalToolResult, ModelTokenUsage,
    ModelUsageSummary, ProposalApplyResult, RunAttempt, RunEventKind, RunEvidenceSummary,
    RunResult, RunStatus, RunSummary, RunTranscript, WorkspaceEventFeed, WorkspaceReplay,
    WorkspaceSummary, WorktreeChange, WorktreeSnapshot,
};
use serde::Serialize;
use std::env;
use std::fs;
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::process::Command;
use structure_local_core::{
    collect_snapshot, default_repo_root, structure_core_manifest,
    verify_structure_core_parity_for_repo,
};

#[derive(Debug, Clone)]
pub struct RunRequest {
    pub prompt: String,
    pub workspace_id: Option<String>,
    pub mode: Option<LocalAgentMode>,
}

#[derive(Debug, Clone)]
pub struct ContinuationRequest {
    pub run_id: String,
    pub extra_instruction: Option<String>,
    pub mode: Option<LocalAgentMode>,
}

pub struct LocalAgentRuntime {
    repo_root: PathBuf,
    runtime_dir: PathBuf,
    store: SqliteLocalStore,
}

impl LocalAgentRuntime {
    pub fn open(repo_root: impl AsRef<Path>) -> Result<Self, String> {
        let repo_root = repo_root
            .as_ref()
            .canonicalize()
            .map_err(|err| format!("failed to canonicalize repo root: {err}"))?;
        load_openai_env_file(&repo_root)?;
        let runtime_dir = env::var("STRUCTURE_LOCAL_RUNTIME_DIR")
            .map(PathBuf::from)
            .unwrap_or_else(|_| repo_root.join(".structure/local"));
        fs::create_dir_all(runtime_dir.join("artifacts"))
            .map_err(|err| format!("failed to create local artifact directory: {err}"))?;
        let store = SqliteLocalStore::open(&runtime_dir)?;
        Ok(Self {
            repo_root,
            runtime_dir,
            store,
        })
    }

    pub fn open_default() -> Result<Self, String> {
        let repo_root = default_repo_root()?;
        Self::open(repo_root)
    }

    pub fn db_path(&self) -> &Path {
        self.store.db_path()
    }

    pub fn repo_root(&self) -> &Path {
        &self.repo_root
    }

    pub fn worktree_snapshot(&self) -> WorktreeSnapshot {
        collect_worktree_snapshot(&self.repo_root)
    }

    pub fn ensure_workspace(
        &self,
        workspace_id: Option<String>,
    ) -> Result<WorkspaceSummary, String> {
        self.store.ensure_workspace(workspace_id, &self.repo_root)
    }

    pub fn agent_context(
        &self,
        workspace_id: Option<&str>,
        mode: Option<LocalAgentMode>,
    ) -> Result<LocalAgentContext, String> {
        let workspace = self
            .store
            .ensure_workspace(workspace_id.map(str::to_string), &self.repo_root)?;
        self.build_agent_context(&workspace.workspace_id, mode.unwrap_or_default(), None, 8)
    }

    pub fn list_workspaces(&self, limit: usize) -> Result<Vec<WorkspaceSummary>, String> {
        self.store.list_workspaces(limit)
    }

    pub fn workspace(&self, workspace_id: &str) -> Result<WorkspaceSummary, String> {
        self.store.workspace_by_id(workspace_id)
    }

    pub fn run_prompt(&self, request: RunRequest) -> Result<RunResult, String> {
        let attempt = self.run_prompt_attempt(request)?;
        if let Some(result) = attempt.result {
            return Ok(result);
        }
        Err(attempt
            .error
            .unwrap_or_else(|| "local run failed without an error message".to_string()))
    }

    pub fn run_prompt_attempt(&self, request: RunRequest) -> Result<RunAttempt, String> {
        if request.prompt.trim().is_empty() {
            return Err("prompt must not be empty".to_string());
        }
        let mode = request.mode.unwrap_or_default();

        let workspace = self
            .store
            .ensure_workspace(request.workspace_id, &self.repo_root)?;
        let run_id = new_id("run");
        let mut run = self
            .store
            .create_run(&run_id, &workspace.workspace_id, &request.prompt)?;
        self.store.append_event(
            &workspace.workspace_id,
            Some(&run_id),
            RunEventKind::WorkspaceOpened,
            &workspace,
        )?;
        self.store.append_event(
            &workspace.workspace_id,
            Some(&run_id),
            RunEventKind::RunCreated,
            &run,
        )?;
        self.store.append_event(
            &workspace.workspace_id,
            Some(&run_id),
            RunEventKind::ChatMessageRecorded,
            &serde_json::json!({
                "role": "user",
                "mode": mode.as_str(),
                "content": request.prompt,
            }),
        )?;
        self.store.append_event(
            &workspace.workspace_id,
            Some(&run_id),
            RunEventKind::PromptReceived,
            &serde_json::json!({
                "prompt": request.prompt,
                "mode": mode.as_str(),
                "surface": "local_rust",
            }),
        )?;

        run = self.store.update_run(&run_id, RunStatus::Running, None)?;
        match self.execute_running_run(run.clone(), mode) {
            Ok(result) => Ok(RunAttempt {
                run: result.run.clone(),
                events: result.events.clone(),
                result: Some(result),
                error: None,
            }),
            Err(error) => {
                let failed_run = self
                    .store
                    .update_run(&run_id, RunStatus::Failed, Some(&error))?;
                self.store.append_event(
                    &workspace.workspace_id,
                    Some(&run_id),
                    RunEventKind::RunFailed,
                    &serde_json::json!({
                        "run": failed_run,
                        "error": error,
                    }),
                )?;
                let events = self.store.run_events(&run_id)?;
                Ok(RunAttempt {
                    run: failed_run,
                    events,
                    result: None,
                    error: Some(error),
                })
            }
        }
    }

    pub fn add_knowledge_source(
        &self,
        workspace_id: Option<String>,
        path: impl AsRef<Path>,
    ) -> Result<KnowledgeSource, String> {
        let workspace = self.store.ensure_workspace(workspace_id, &self.repo_root)?;
        let source = self
            .store
            .add_knowledge_source(&workspace.workspace_id, path)?;
        self.store.append_event(
            &workspace.workspace_id,
            None,
            RunEventKind::WorkspaceContextLoaded,
            &serde_json::json!({
                "action": "knowledge_source_added",
                "source": source,
            }),
        )?;
        Ok(source)
    }

    pub fn add_text_knowledge_source(
        &self,
        workspace_id: Option<String>,
        text: &str,
    ) -> Result<KnowledgeSource, String> {
        let text = text.trim();
        if text.is_empty() {
            return Err("knowledge text must not be empty".to_string());
        }
        if text.len() > 64_000 {
            return Err("knowledge text must be 64000 bytes or less".to_string());
        }
        let workspace = self.store.ensure_workspace(workspace_id, &self.repo_root)?;
        let knowledge_dir = self
            .runtime_dir
            .join("knowledge")
            .join(safe_path_component(&workspace.workspace_id));
        fs::create_dir_all(&knowledge_dir)
            .map_err(|err| format!("failed to create local knowledge directory: {err}"))?;
        let path = knowledge_dir.join(format!("memory_{}.md", new_id("note")));
        let content = format!(
            "# Local Agent Memory\n\nWorkspace: `{}`\n\n{}\n",
            workspace.workspace_id, text
        );
        fs::write(&path, content)
            .map_err(|err| format!("failed to write local knowledge memory: {err}"))?;
        let source = self
            .store
            .add_knowledge_source(&workspace.workspace_id, &path)?;
        self.store.append_event(
            &workspace.workspace_id,
            None,
            RunEventKind::WorkspaceContextLoaded,
            &serde_json::json!({
                "action": "knowledge_text_remembered",
                "source": source,
            }),
        )?;
        Ok(source)
    }

    pub fn knowledge_sources(
        &self,
        workspace_id: Option<&str>,
        limit: usize,
    ) -> Result<Vec<KnowledgeSource>, String> {
        let workspace_id = workspace_id.unwrap_or("default");
        self.store.list_knowledge_sources(workspace_id, limit)
    }

    pub fn knowledge_source(&self, source_id: &str) -> Result<KnowledgeSource, String> {
        self.store.knowledge_source_by_id(source_id)
    }

    pub fn read_knowledge_source(
        &self,
        source_id: &str,
        max_bytes: u64,
    ) -> Result<KnowledgeSourcePreview, String> {
        let source = self.knowledge_source(source_id)?;
        let (preview, bytes_read, truncated) =
            read_limited_text(&source.path, source.size_bytes, max_bytes)
                .map_err(|err| format!("failed to read knowledge source: {err}"))?;

        Ok(KnowledgeSourcePreview {
            source,
            preview,
            bytes_read,
            truncated,
        })
    }

    pub fn remove_knowledge_source(&self, source_id: &str) -> Result<KnowledgeSource, String> {
        let source = self.store.delete_knowledge_source(source_id)?;
        self.store.append_event(
            &source.workspace_id,
            None,
            RunEventKind::WorkspaceContextLoaded,
            &serde_json::json!({
                "action": "knowledge_source_removed",
                "source": source,
            }),
        )?;
        Ok(source)
    }

    pub fn list_runs(
        &self,
        workspace_id: Option<&str>,
        limit: usize,
    ) -> Result<Vec<RunSummary>, String> {
        self.store.list_runs(workspace_id, limit)
    }

    pub fn chat_turns(
        &self,
        workspace_id: Option<&str>,
        limit: usize,
    ) -> Result<Vec<ChatTurn>, String> {
        let runs = self.store.list_runs(workspace_id, limit)?;
        let mut turns = Vec::new();
        for run in runs {
            let events = self.store.run_events(&run.run_id)?;
            let mode = run_mode_from_events(&events)
                .unwrap_or_else(|| LocalAgentMode::CodeAgent.as_str().to_string());
            turns.push(ChatTurn {
                run_id: run.run_id,
                workspace_id: run.workspace_id,
                mode,
                user_message: run.prompt,
                assistant_message: run.final_response,
                status: run.status,
                event_count: events.len(),
                created_at_ms: run.created_at_ms,
                updated_at_ms: run.updated_at_ms,
            });
        }
        Ok(turns)
    }

    pub fn workspace_replay(
        &self,
        workspace_id: Option<&str>,
        limit: usize,
    ) -> Result<WorkspaceReplay, String> {
        let workspace_id = workspace_id.unwrap_or("default");
        let events = self.store.workspace_events(workspace_id, limit)?;
        let runs = self.store.list_runs(Some(workspace_id), limit)?;
        let knowledge_sources = self.store.list_knowledge_sources(workspace_id, limit)?;
        let artifacts = self.store.list_artifacts(Some(workspace_id), None, limit)?;
        let last_sequence = events.last().map(|event| event.sequence);

        Ok(WorkspaceReplay {
            workspace_id: workspace_id.to_string(),
            events,
            runs,
            knowledge_sources,
            artifacts,
            last_sequence,
        })
    }

    pub fn workspace_event_feed(
        &self,
        workspace_id: Option<&str>,
        after_sequence: Option<i64>,
        limit: usize,
    ) -> Result<WorkspaceEventFeed, String> {
        let workspace_id = workspace_id.unwrap_or("default");
        let after_sequence = after_sequence.unwrap_or_default().max(0);
        let limit = limit.clamp(1, 500);
        let events = self
            .store
            .workspace_events_after(workspace_id, after_sequence, limit)?;
        let last_sequence = events.last().map(|event| event.sequence);
        let next_after_sequence = last_sequence.unwrap_or(after_sequence);

        Ok(WorkspaceEventFeed {
            workspace_id: workspace_id.to_string(),
            after_sequence,
            events,
            last_sequence,
            next_after_sequence,
        })
    }

    pub fn execute_workspace_tool(
        &self,
        workspace_id: Option<String>,
        surface: &str,
        call: LocalToolCall,
    ) -> Result<LocalToolResult, String> {
        let workspace = self.store.ensure_workspace(workspace_id, &self.repo_root)?;
        self.store.append_event(
            &workspace.workspace_id,
            None,
            RunEventKind::ToolCallRequested,
            &serde_json::json!({
                "call_id": call.call_id.clone(),
                "name": call.name.clone(),
                "input": call.input.clone(),
                "surface": surface,
            }),
        )?;

        let registry = BuiltinLocalToolRegistry::new(&self.repo_root);
        let result = registry.execute(&call);
        self.store.append_event(
            &workspace.workspace_id,
            None,
            RunEventKind::ToolCallCompleted,
            &serde_json::json!({
                "call_id": result.call_id.clone(),
                "name": result.name.clone(),
                "success": result.success,
                "output": result.output.clone(),
                "error": result.error.clone(),
                "surface": surface,
            }),
        )?;
        Ok(result)
    }

    pub fn local_evidence_bundle(
        &self,
        workspace_id: Option<&str>,
        limit: usize,
    ) -> Result<LocalEvidenceBundle, String> {
        let workspace_id = workspace_id.unwrap_or("default");
        let snapshot = collect_snapshot(&self.repo_root)?;
        let parity_report = verify_structure_core_parity_for_repo(&self.repo_root)?;
        let workspace_replay = self.workspace_replay(Some(workspace_id), limit)?;
        let run_evidence = workspace_replay
            .runs
            .iter()
            .filter_map(|run| self.run_evidence_summary(&run.run_id).ok())
            .collect::<Vec<_>>();

        Ok(LocalEvidenceBundle {
            schema_version: "local-evidence-bundle-v1".to_string(),
            generated_at_ms: crate::store::now_ms(),
            workspace_id: workspace_id.to_string(),
            snapshot,
            parity_report,
            workspace_replay,
            run_evidence,
        })
    }

    pub fn llm_diagnostic(&self) -> LocalLlmDiagnostic {
        EnvApiModelProvider::diagnose_from_env()
    }

    pub fn list_artifacts(
        &self,
        workspace_id: Option<&str>,
        run_id: Option<&str>,
        limit: usize,
    ) -> Result<Vec<ArtifactRecord>, String> {
        self.store.list_artifacts(workspace_id, run_id, limit)
    }

    pub fn artifact(&self, artifact_id: &str) -> Result<ArtifactRecord, String> {
        self.store.artifact_by_id(artifact_id)
    }

    pub fn read_artifact(
        &self,
        artifact_id: &str,
        max_bytes: u64,
    ) -> Result<ArtifactPreview, String> {
        let artifact = self.artifact(artifact_id)?;
        let (preview, bytes_read, truncated) =
            read_limited_text(&artifact.path, artifact.size_bytes, max_bytes)
                .map_err(|err| format!("failed to read artifact: {err}"))?;
        Ok(ArtifactPreview {
            artifact,
            preview,
            bytes_read,
            truncated,
        })
    }

    pub fn apply_code_change_proposal(
        &self,
        artifact_id: &str,
        dry_run: bool,
    ) -> Result<ProposalApplyResult, String> {
        let artifact = self.artifact(artifact_id)?;
        if artifact.kind != "code_change_proposal" {
            return Err(format!(
                "artifact {} is {}, not code_change_proposal",
                artifact.artifact_id, artifact.kind
            ));
        }
        let proposal = fs::read_to_string(&artifact.path)
            .map_err(|err| format!("failed to read proposal artifact: {err}"))?;
        let patch = parse_proposal_patch(&proposal)?;
        let target = safe_proposal_target_path(&self.repo_root, &patch.target_path)?;
        if patch.new_file && target.exists() {
            return Err(format!(
                "proposal patch creates {}, but the target already exists",
                patch.target_path
            ));
        }
        let current_text = if target.exists() {
            fs::read_to_string(&target)
                .map_err(|err| format!("failed to read proposal target: {err}"))?
        } else {
            String::new()
        };
        let patched_text = apply_parsed_patch(&current_text, &patch)?;
        let bytes_written = patched_text.len() as u64;

        if !dry_run {
            if let Some(parent) = target.parent() {
                fs::create_dir_all(parent)
                    .map_err(|err| format!("failed to create proposal target directory: {err}"))?;
            }
            let mut file = fs::OpenOptions::new()
                .create(true)
                .truncate(true)
                .write(true)
                .open(&target)
                .map_err(|err| format!("failed to open proposal target for write: {err}"))?;
            file.write_all(patched_text.as_bytes())
                .map_err(|err| format!("failed to apply proposal patch: {err}"))?;
        }

        let result = ProposalApplyResult {
            artifact,
            target_path: target.display().to_string(),
            applied: !dry_run,
            dry_run,
            added_lines: patch.added_line_count(),
            bytes_written: if dry_run { 0 } else { bytes_written },
            preview: patched_text.chars().take(4000).collect(),
        };
        self.store.append_event(
            &result.artifact.workspace_id,
            Some(&result.artifact.run_id),
            RunEventKind::CodeChangeApplied,
            &result,
        )?;
        Ok(result)
    }

    pub fn run_by_id(&self, run_id: &str) -> Result<RunSummary, String> {
        self.store.run_by_id(run_id)
    }

    pub fn run_events(&self, run_id: &str) -> Result<Vec<crate::types::LocalEvent>, String> {
        self.store.run_events(run_id)
    }

    pub fn run_evidence_summary(&self, run_id: &str) -> Result<RunEvidenceSummary, String> {
        let run = self.run_by_id(run_id)?;
        let events = self.run_events(run_id)?;
        let mut knowledge_sources = Vec::new();
        let mut artifact_paths = Vec::new();
        let mut event_kinds = Vec::new();
        let mut canonical_flow_ids = Vec::new();
        let mut primitive_ids = Vec::new();
        let mut agent_instruction_paths = Vec::new();
        let mut worktree = None;
        let mut prompt_references = Vec::new();
        let mut tool_call_count = 0;
        let mut model_usage = ModelUsageSummary::default();

        for event in &events {
            if !event_kinds.contains(&event.kind) {
                event_kinds.push(event.kind.clone());
            }
            if !canonical_flow_ids.contains(&event.canonical_flow_id) {
                canonical_flow_ids.push(event.canonical_flow_id.clone());
            }
            if !primitive_ids.contains(&event.primitive_id) {
                primitive_ids.push(event.primitive_id.clone());
            }
            match event.kind.as_str() {
                "model_requested" => {
                    model_usage.model_request_count += 1;
                    if event
                        .payload
                        .get("network_required")
                        .and_then(|value| value.as_bool())
                        .unwrap_or(false)
                    {
                        model_usage.network_request_count += 1;
                    }
                }
                "model_responded" => {
                    model_usage.model_response_count += 1;
                    model_usage.response_chars += event
                        .payload
                        .get("response_chars")
                        .and_then(|value| value.as_u64())
                        .unwrap_or_default()
                        as usize;
                    if let Some(usage) = event
                        .payload
                        .get("usage")
                        .cloned()
                        .and_then(|value| serde_json::from_value::<ModelTokenUsage>(value).ok())
                    {
                        model_usage.prompt_tokens += usage.prompt_tokens;
                        model_usage.completion_tokens += usage.completion_tokens;
                        model_usage.total_tokens += usage.total_tokens;
                    }
                }
                "workspace_context_loaded" => {
                    if let Some(instructions) = event
                        .payload
                        .get("agent_instructions")
                        .and_then(|value| value.as_array())
                    {
                        for instruction in instructions {
                            if let Some(path) =
                                instruction.get("path").and_then(|value| value.as_str())
                            {
                                if !agent_instruction_paths
                                    .iter()
                                    .any(|existing| existing == path)
                                {
                                    agent_instruction_paths.push(path.to_string());
                                }
                            }
                        }
                    }
                    if worktree.is_none() {
                        worktree = event
                            .payload
                            .get("worktree")
                            .cloned()
                            .and_then(|value| serde_json::from_value(value).ok());
                    }
                }
                "agent_step_planned" => {
                    if let Some(references) = event
                        .payload
                        .get("prompt_references")
                        .and_then(|value| value.as_array())
                    {
                        for reference in references {
                            if let Some(reference) = reference.as_str() {
                                if !prompt_references
                                    .iter()
                                    .any(|existing| existing == reference)
                                {
                                    prompt_references.push(reference.to_string());
                                }
                            }
                        }
                    }
                }
                "knowledge_retrieved" => {
                    if let Some(sources) = event
                        .payload
                        .get("sources")
                        .and_then(|value| value.as_array())
                    {
                        for source in sources {
                            if let Ok(source) =
                                serde_json::from_value::<KnowledgeSource>(source.clone())
                            {
                                if !knowledge_sources.iter().any(|existing: &KnowledgeSource| {
                                    existing.source_id == source.source_id
                                }) {
                                    knowledge_sources.push(source);
                                }
                            }
                        }
                    }
                }
                "tool_call_completed" => {
                    tool_call_count += 1;
                }
                "artifact_written" => {
                    if let Some(path) = event.payload.get("path").and_then(|value| value.as_str()) {
                        artifact_paths.push(path.to_string());
                    }
                }
                _ => {}
            }
        }
        let artifacts = self.store.list_artifacts(None, Some(run_id), 32)?;
        for artifact in &artifacts {
            if !artifact_paths.contains(&artifact.path) {
                artifact_paths.push(artifact.path.clone());
            }
        }

        let core_trace = build_core_execution_trace(&events)?;
        let final_response_chars = run
            .final_response
            .as_ref()
            .map(|response| response.chars().count())
            .unwrap_or_default();

        Ok(RunEvidenceSummary {
            run,
            event_count: events.len(),
            tool_call_count,
            model_usage,
            agent_instruction_paths,
            worktree,
            prompt_references,
            knowledge_sources,
            artifact_paths,
            artifacts,
            event_kinds,
            canonical_flow_ids,
            primitive_ids,
            core_trace,
            final_response_chars,
        })
    }

    pub fn run_transcript(&self, run_id: &str) -> Result<RunTranscript, String> {
        let run = self.run_by_id(run_id)?;
        let events = self.run_events(run_id)?;
        let evidence = self.run_evidence_summary(run_id)?;
        let chat_turn = self
            .chat_turns(Some(&run.workspace_id), 128)?
            .into_iter()
            .find(|turn| turn.run_id == run.run_id);
        let final_response = run.final_response.clone();
        Ok(RunTranscript {
            run,
            chat_turn,
            events,
            evidence,
            final_response,
        })
    }

    pub fn run_continuation_attempt(
        &self,
        request: ContinuationRequest,
    ) -> Result<RunAttempt, String> {
        let transcript = self.run_transcript(&request.run_id)?;
        let prompt = build_continuation_prompt(&transcript, request.extra_instruction.as_deref());
        self.run_prompt_attempt(RunRequest {
            prompt,
            workspace_id: Some(transcript.run.workspace_id),
            mode: request.mode,
        })
    }

    fn write_response_artifact(&self, run_id: &str, response: &str) -> Result<PathBuf, String> {
        let artifact_dir = self.runtime_dir.join("artifacts").join(run_id);
        fs::create_dir_all(&artifact_dir)
            .map_err(|err| format!("failed to create run artifact directory: {err}"))?;
        let path = artifact_dir.join("response.md");
        fs::write(&path, response)
            .map_err(|err| format!("failed to write local response artifact: {err}"))?;
        Ok(path)
    }

    fn write_code_change_proposal_artifact(
        &self,
        run: &RunSummary,
        tool_results: &[LocalToolResult],
        model_response: &str,
    ) -> Result<(PathBuf, String, String), String> {
        let artifact_dir = self.runtime_dir.join("artifacts").join(&run.run_id);
        fs::create_dir_all(&artifact_dir)
            .map_err(|err| format!("failed to create run artifact directory: {err}"))?;
        let inspected_files = inspected_files_from_tools(tool_results);
        let (proposal, proposal_source) =
            render_code_change_proposal(&self.repo_root, run, &inspected_files, model_response);
        let path = artifact_dir.join("code_change_proposal.md");
        fs::write(&path, &proposal)
            .map_err(|err| format!("failed to write code change proposal artifact: {err}"))?;
        Ok((path, proposal, proposal_source))
    }

    fn execute_running_run(
        &self,
        run: RunSummary,
        mode: LocalAgentMode,
    ) -> Result<RunResult, String> {
        let context =
            self.build_agent_context(&run.workspace_id, mode.clone(), Some(&run.run_id), 8)?;
        self.store.append_event(
            &run.workspace_id,
            Some(&run.run_id),
            RunEventKind::WorkspaceContextLoaded,
            &serde_json::json!({
                "workspace_id": run.workspace_id,
                "repo_root": context.repo_root.clone(),
                "runtime_db": context.runtime_db.clone(),
                "mode": context.mode.clone(),
                "agent_instruction_count": context.agent_instructions.len(),
                "agent_instructions": context.agent_instructions.clone(),
                "worktree": context.worktree.clone(),
                "knowledge_sources": context.knowledge_sources.len(),
                "recent_turns": context.recent_turns.len(),
                "context_replay_limit": context.context_replay_limit,
            }),
        )?;
        self.store.append_event(
            &run.workspace_id,
            Some(&run.run_id),
            RunEventKind::KnowledgeRetrieved,
            &KnowledgePayload {
                sources: context.knowledge_sources.clone(),
            },
        )?;

        let model = selected_planning_provider()?;
        let model_request = ModelRequest {
            run: run.clone(),
            repo_root: self.repo_root.clone(),
            agent_instructions: context.agent_instructions,
            worktree: context.worktree,
            knowledge: context.knowledge_sources,
            recent_turns: context.recent_turns,
            mode: mode.clone(),
        };
        let registry = BuiltinLocalToolRegistry::new(&self.repo_root);
        let mut tool_results = Vec::new();
        let prompt_references = prompt_path_references(&run.prompt);
        if !prompt_references.is_empty() {
            let prompt_reference_labels = prompt_references
                .iter()
                .map(|reference| reference.display.clone())
                .collect::<Vec<_>>();
            self.store.append_event(
                &run.workspace_id,
                Some(&run.run_id),
                RunEventKind::AgentStepPlanned,
                &serde_json::json!({
                    "mode": mode.as_str(),
                    "planner": "structure_local_runtime",
                    "steps": ["prompt_reference_resolution"],
                    "tool_call_count": prompt_references.len(),
                    "total_tool_results": tool_results.len(),
                    "prompt_references": prompt_reference_labels,
                }),
            )?;
            for reference in &prompt_references {
                let call = LocalToolCall {
                    call_id: new_id("tool"),
                    name: "read_repo_file".to_string(),
                    input: serde_json::json!({
                        "path": reference.path,
                        "max_bytes": 8192,
                    }),
                };
                self.store.append_event(
                    &run.workspace_id,
                    Some(&run.run_id),
                    RunEventKind::ToolCallRequested,
                    &call,
                )?;
                let result = registry.execute(&call);
                self.store.append_event(
                    &run.workspace_id,
                    Some(&run.run_id),
                    RunEventKind::ToolCallCompleted,
                    &result,
                )?;
                tool_results.push(result);
            }
        }
        let mut seen_tool_calls = Vec::new();
        let mut planning_iterations = 0usize;
        for iteration in 1..=3 {
            planning_iterations = iteration;
            self.store.append_event(
                &run.workspace_id,
                Some(&run.run_id),
                RunEventKind::ModelRequested,
                &serde_json::json!({
                    "provider": model.provider_id(),
                    "phase": "tool_planning",
                    "iteration": iteration,
                    "prior_tool_results": tool_results.len(),
                    "network_required": model.provider_id() != "local_deterministic",
                }),
            )?;
            let plan = model.plan_next(&model_request, &tool_results)?;
            let plan_provider = plan.provider.clone();
            let plan_usage = plan.usage.clone();
            let new_tool_calls = plan
                .tool_calls
                .into_iter()
                .filter(|call| {
                    let fingerprint = tool_call_fingerprint(call);
                    if seen_tool_calls.contains(&fingerprint) {
                        return false;
                    }
                    seen_tool_calls.push(fingerprint);
                    true
                })
                .collect::<Vec<_>>();
            self.store.append_event(
                &run.workspace_id,
                Some(&run.run_id),
                RunEventKind::AgentStepPlanned,
                &serde_json::json!({
                    "mode": mode.as_str(),
                    "planner": "structure_local_runtime",
                    "iteration": iteration,
                    "steps": [
                        "workspace_context_replay",
                        "tool_planning",
                        "local_tool_execution",
                        "response_synthesis",
                        "artifact_persistence"
                    ],
                    "tool_call_count": new_tool_calls.len(),
                    "total_tool_results": tool_results.len(),
                }),
            )?;
            self.store.append_event(
                &run.workspace_id,
                Some(&run.run_id),
                RunEventKind::ModelResponded,
                &serde_json::json!({
                    "provider": plan_provider,
                    "phase": "tool_planning",
                    "iteration": iteration,
                    "tool_calls": &new_tool_calls,
                    "usage": plan_usage,
                }),
            )?;

            if new_tool_calls.is_empty() {
                break;
            }
            for call in &new_tool_calls {
                self.store.append_event(
                    &run.workspace_id,
                    Some(&run.run_id),
                    RunEventKind::ToolCallRequested,
                    call,
                )?;
                let result = registry.execute(call);
                self.store.append_event(
                    &run.workspace_id,
                    Some(&run.run_id),
                    RunEventKind::ToolCallCompleted,
                    &result,
                )?;
                tool_results.push(result);
            }
        }

        let synthesis_model = selected_synthesis_provider()?;
        self.store.append_event(
            &run.workspace_id,
            Some(&run.run_id),
            RunEventKind::ModelRequested,
            &serde_json::json!({
                "provider": synthesis_model.provider_id(),
                "phase": "response_synthesis",
                "tool_results": tool_results.len(),
                "planning_iterations": planning_iterations,
                "network_required": synthesis_model.provider_id() != "local_deterministic",
            }),
        )?;
        let output = synthesis_model.synthesize(&model_request, &tool_results)?;
        let output_provider = output.provider;
        let output_usage = output.usage;
        let mut final_response = output.final_response;
        let proposal = if matches!(mode, LocalAgentMode::CodeAgent) {
            let (path, proposal, proposal_source) =
                self.write_code_change_proposal_artifact(&run, &tool_results, &final_response)?;
            final_response.push_str("\n## Code Change Proposal\n\n");
            final_response.push_str("A proposed change artifact was produced for review. ");
            final_response.push_str("No repository files were modified by this run.\n\n");
            final_response.push_str(&format!("Artifact: `{}`\n\n", path.display()));
            final_response.push_str(&format!("Source: `{proposal_source}`\n\n"));
            final_response.push_str("```diff\n");
            final_response.push_str(&extract_patch_sketch(&proposal));
            final_response.push_str("\n```\n");
            Some((path, proposal_source))
        } else {
            None
        };
        self.store.append_event(
            &run.workspace_id,
            Some(&run.run_id),
            RunEventKind::ModelResponded,
            &serde_json::json!({
                "provider": output_provider,
                "phase": "response_synthesis",
                "response_chars": final_response.chars().count(),
                "response_preview": final_response.chars().take(240).collect::<String>(),
                "usage": output_usage,
            }),
        )?;
        self.store.append_event(
            &run.workspace_id,
            Some(&run.run_id),
            RunEventKind::ChatMessageRecorded,
            &serde_json::json!({
                "role": "assistant",
                "mode": mode.as_str(),
                "content": final_response,
            }),
        )?;

        let artifact_path = self.write_response_artifact(&run.run_id, &final_response)?;
        let artifact = self.store.add_artifact(
            &run.run_id,
            &run.workspace_id,
            "assistant_response",
            &artifact_path,
        )?;
        self.store.append_event(
            &run.workspace_id,
            Some(&run.run_id),
            RunEventKind::ArtifactWritten,
            &artifact,
        )?;
        if let Some((proposal_path, proposal_source)) = proposal {
            let proposal_artifact = self.store.add_artifact(
                &run.run_id,
                &run.workspace_id,
                "code_change_proposal",
                &proposal_path,
            )?;
            self.store.append_event(
                &run.workspace_id,
                Some(&run.run_id),
                RunEventKind::CodeChangeProposed,
                &serde_json::json!({
                    "artifact": proposal_artifact,
                    "proposal_source": proposal_source,
                    "status": "proposed_only",
                    "applied": false,
                }),
            )?;
            self.store.append_event(
                &run.workspace_id,
                Some(&run.run_id),
                RunEventKind::ArtifactWritten,
                &proposal_artifact,
            )?;
        }

        let run = self
            .store
            .update_run(&run.run_id, RunStatus::Finished, Some(&final_response))?;
        self.store.append_event(
            &run.workspace_id,
            Some(&run.run_id),
            RunEventKind::RunFinished,
            &run,
        )?;
        let events = self.store.run_events(&run.run_id)?;

        Ok(RunResult {
            run,
            events,
            final_response,
            artifact_path: artifact.path.clone(),
            artifact,
        })
    }

    fn build_agent_context(
        &self,
        workspace_id: &str,
        mode: LocalAgentMode,
        exclude_run_id: Option<&str>,
        limit: usize,
    ) -> Result<LocalAgentContext, String> {
        let agent_instructions = load_agent_instructions(&self.repo_root)?;
        let worktree = collect_worktree_snapshot(&self.repo_root);
        let knowledge_sources = self.store.list_knowledge_sources(workspace_id, limit)?;
        let recent_turns = self
            .chat_turns(Some(workspace_id), limit)?
            .into_iter()
            .filter(|turn| {
                exclude_run_id != Some(turn.run_id.as_str()) && turn.assistant_message.is_some()
            })
            .collect::<Vec<_>>();
        Ok(LocalAgentContext {
            workspace_id: workspace_id.to_string(),
            mode: mode.as_str().to_string(),
            repo_root: self.repo_root.display().to_string(),
            runtime_db: self.db_path().display().to_string(),
            agent_instructions,
            worktree,
            knowledge_sources,
            recent_turns,
            context_replay_limit: limit,
        })
    }
}

fn run_mode_from_events(events: &[LocalEvent]) -> Option<String> {
    events.iter().find_map(|event| match event.kind.as_str() {
        "prompt_received" | "chat_message_recorded" | "agent_step_planned" => event
            .payload
            .get("mode")
            .and_then(|value| value.as_str())
            .map(ToOwned::to_owned),
        _ => None,
    })
}

fn safe_path_component(value: &str) -> String {
    let component = value
        .chars()
        .map(|ch| {
            if ch.is_ascii_alphanumeric() || matches!(ch, '-' | '_') {
                ch
            } else {
                '_'
            }
        })
        .collect::<String>();
    if component.is_empty() {
        "default".to_string()
    } else {
        component
    }
}

fn load_openai_env_file(repo_root: &Path) -> Result<(), String> {
    let env_path = repo_root.join(".env");
    if !env_path.exists() {
        return Ok(());
    }
    let entries = dotenvy::from_path_iter(&env_path)
        .map_err(|err| format!("failed to parse {}: {err}", env_path.display()))?;
    for entry in entries {
        let (key, value) =
            entry.map_err(|err| format!("failed to parse {}: {err}", env_path.display()))?;
        if matches!(
            key.as_str(),
            "OPENAI__API_KEY" | "OPENAI__BASE_URL" | "OPENAI__MODEL"
        ) && env::var_os(&key).is_none()
        {
            env::set_var(key, value);
        }
    }
    Ok(())
}

fn read_limited_text(
    path: impl AsRef<Path>,
    size_bytes: u64,
    max_bytes: u64,
) -> Result<(String, u64, bool), String> {
    let max_bytes = max_bytes.clamp(1, 1_000_000);
    let file = fs::File::open(path).map_err(|err| err.to_string())?;
    let mut reader = file.take(max_bytes);
    let mut buffer = Vec::new();
    reader
        .read_to_end(&mut buffer)
        .map_err(|err| err.to_string())?;
    let bytes_read = buffer.len() as u64;
    let truncated = size_bytes > bytes_read;
    let preview = String::from_utf8_lossy(&buffer).into_owned();
    Ok((preview, bytes_read, truncated))
}

fn load_agent_instructions(repo_root: &Path) -> Result<Vec<AgentInstruction>, String> {
    let mut instructions = Vec::new();
    let path = repo_root.join("AGENTS.md");
    if !path.exists() {
        return Ok(instructions);
    }
    let metadata = fs::metadata(&path)
        .map_err(|err| format!("failed to inspect AGENTS.md instructions: {err}"))?;
    if !metadata.is_file() {
        return Ok(instructions);
    }
    let (content_preview, _bytes_read, truncated) =
        read_limited_text(&path, metadata.len(), 32_000)
            .map_err(|err| format!("failed to read AGENTS.md instructions: {err}"))?;
    instructions.push(AgentInstruction {
        path: "AGENTS.md".to_string(),
        title: "Repository Agent Instructions".to_string(),
        size_bytes: metadata.len(),
        content_preview,
        truncated,
    });
    Ok(instructions)
}

fn collect_worktree_snapshot(repo_root: &Path) -> WorktreeSnapshot {
    match Command::new("git")
        .arg("-C")
        .arg(repo_root)
        .args(["status", "--short", "--branch", "--untracked-files=all"])
        .output()
    {
        Ok(output) if output.status.success() => {
            let text = String::from_utf8_lossy(&output.stdout);
            parse_git_status_snapshot(&text)
        }
        Ok(output) => WorktreeSnapshot {
            available: false,
            clean: true,
            branch: None,
            changed_files: Vec::new(),
            error: Some(
                String::from_utf8_lossy(&output.stderr)
                    .chars()
                    .take(500)
                    .collect::<String>(),
            )
            .filter(|value| !value.trim().is_empty())
            .or_else(|| Some(format!("git status exited with {}", output.status))),
        },
        Err(error) => WorktreeSnapshot {
            available: false,
            clean: true,
            branch: None,
            changed_files: Vec::new(),
            error: Some(format!("failed to run git status: {error}")),
        },
    }
}

fn parse_git_status_snapshot(text: &str) -> WorktreeSnapshot {
    let mut branch = None;
    let mut changed_files = Vec::new();
    for line in text.lines() {
        if let Some(rest) = line.strip_prefix("## ") {
            branch = Some(rest.split("...").next().unwrap_or(rest).trim().to_string())
                .filter(|value| !value.is_empty());
            continue;
        }
        if line.len() < 3 {
            continue;
        }
        let status = line[..2].trim().to_string();
        let path = line[3..].trim().to_string();
        if !status.is_empty() && !path.is_empty() {
            changed_files.push(WorktreeChange { status, path });
        }
    }
    changed_files.truncate(64);
    WorktreeSnapshot {
        available: true,
        clean: changed_files.is_empty(),
        branch,
        changed_files,
        error: None,
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct PromptPathReference {
    display: String,
    path: String,
}

fn prompt_path_references(prompt: &str) -> Vec<PromptPathReference> {
    let mut references = Vec::new();
    for raw_token in prompt.split_whitespace() {
        let Some(reference) = parse_prompt_path_reference(raw_token) else {
            continue;
        };
        if !references
            .iter()
            .any(|existing: &PromptPathReference| existing.display == reference.display)
        {
            references.push(reference);
        }
    }
    references
}

fn parse_prompt_path_reference(raw_token: &str) -> Option<PromptPathReference> {
    let reference = raw_token.strip_prefix('@')?;
    let reference = reference.trim_matches(|ch: char| {
        matches!(ch, ',' | '.' | ':' | ';' | ')' | ']' | '}' | '"' | '\'')
    });
    let path = prompt_reference_path_part(reference);
    looks_like_repo_path_reference(path).then(|| PromptPathReference {
        display: reference.to_string(),
        path: path.to_string(),
    })
}

fn prompt_reference_path_part(reference: &str) -> &str {
    if let Some((path, line)) = reference.rsplit_once("#L") {
        if !path.is_empty() && line.chars().all(|ch| ch.is_ascii_digit()) {
            return path;
        }
    }
    if let Some((path, line)) = reference.rsplit_once("#l") {
        if !path.is_empty() && line.chars().all(|ch| ch.is_ascii_digit()) {
            return path;
        }
    }
    if let Some((path, line)) = reference.rsplit_once(':') {
        if !path.is_empty() && line.chars().all(|ch| ch.is_ascii_digit()) {
            return path;
        }
    }
    reference
}

fn looks_like_repo_path_reference(reference: &str) -> bool {
    !reference.is_empty()
        && !reference.starts_with('/')
        && !reference.contains("..")
        && !reference.contains("://")
        && reference
            .chars()
            .all(|ch| ch.is_ascii_alphanumeric() || matches!(ch, '_' | '-' | '.' | '/'))
        && (reference.contains('/') || reference.contains('.'))
}

#[derive(Debug, Clone, Serialize)]
struct KnowledgePayload {
    sources: Vec<KnowledgeSource>,
}

fn build_core_execution_trace(
    events: &[crate::types::LocalEvent],
) -> Result<CoreExecutionTrace, String> {
    let manifest = structure_core_manifest()?;
    let manifest_flow_ids = manifest
        .canonical_flow
        .iter()
        .map(|step| step.id.as_str())
        .collect::<Vec<_>>();
    let manifest_primitive_ids = manifest
        .primitives
        .iter()
        .map(|primitive| primitive.id.as_str())
        .collect::<Vec<_>>();
    let mut flow_ids = Vec::new();
    let mut primitive_ids = Vec::new();

    for event in events {
        push_unique_string(&mut flow_ids, &event.canonical_flow_id);
        push_unique_string(&mut primitive_ids, &event.primitive_id);
    }

    let invalid_flow_ids = flow_ids
        .iter()
        .filter(|flow_id| !manifest_flow_ids.contains(&flow_id.as_str()))
        .cloned()
        .collect::<Vec<_>>();
    let invalid_primitive_ids = primitive_ids
        .iter()
        .filter(|primitive_id| !manifest_primitive_ids.contains(&primitive_id.as_str()))
        .cloned()
        .collect::<Vec<_>>();

    Ok(CoreExecutionTrace {
        manifest_schema_version: manifest.schema_version,
        event_count: events.len(),
        flow_ids,
        primitive_ids,
        core_aligned: invalid_flow_ids.is_empty() && invalid_primitive_ids.is_empty(),
        invalid_flow_ids,
        invalid_primitive_ids,
    })
}

const CONTINUATION_SNIPPET_MAX_CHARS: usize = 1_200;

fn build_continuation_prompt(
    transcript: &RunTranscript,
    extra_instruction: Option<&str>,
) -> String {
    let assistant_message = transcript
        .chat_turn
        .as_ref()
        .and_then(|turn| turn.assistant_message.as_deref())
        .or(transcript.final_response.as_deref())
        .unwrap_or("No assistant response was recorded.");
    let assistant_excerpt = continuation_response_excerpt(assistant_message);
    let artifact_paths = if transcript.evidence.artifact_paths.is_empty() {
        "none".to_string()
    } else {
        transcript.evidence.artifact_paths.join("\n")
    };
    let extra_instruction = extra_instruction
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .unwrap_or("Continue the same task from the previous run using the persisted transcript, event evidence, workspace context, and current repository state.");

    format!(
        "Continue from Structure local run `{run_id}` in workspace `{workspace_id}`.\n\n\
Previous user prompt:\n{previous_prompt}\n\n\
Previous assistant response summary:\n{assistant_summary}\n\n\
Previous run evidence:\n- status: {status}\n- events: {event_count}\n- completed tool calls: {tool_call_count}\n- core aligned: {core_aligned}\n- artifacts:\n{artifact_paths}\n\n\
Produce the next assistant response now. Treat the continuation instruction as the active user request for this new run.\n\n\
Continuation instruction:\n{extra_instruction}\n",
        run_id = transcript.run.run_id,
        workspace_id = transcript.run.workspace_id,
        previous_prompt = truncate_for_prompt(
            &transcript.run.prompt,
            CONTINUATION_SNIPPET_MAX_CHARS,
        ),
        assistant_summary =
            truncate_for_prompt(&assistant_excerpt, CONTINUATION_SNIPPET_MAX_CHARS),
        status = transcript.run.status,
        event_count = transcript.events.len(),
        tool_call_count = transcript.evidence.tool_call_count,
        core_aligned = transcript.evidence.core_trace.core_aligned,
    )
}

fn continuation_response_excerpt(response: &str) -> String {
    let mut body = Vec::new();
    let mut after_runtime_header = false;
    for line in response.lines() {
        if line.starts_with("## Tool Evidence Summary") {
            break;
        }
        if after_runtime_header {
            body.push(line);
            continue;
        }
        if line.starts_with("- Mode:") || line.starts_with("**Mode:**") {
            after_runtime_header = true;
        }
    }

    let excerpt = body.join("\n").trim().to_string();
    if !excerpt.is_empty() {
        excerpt
    } else if after_runtime_header {
        "Previous response contained no assistant body before tool evidence.".to_string()
    } else {
        response.trim().to_string()
    }
}

fn truncate_for_prompt(value: &str, max_chars: usize) -> String {
    let mut output = value.chars().take(max_chars).collect::<String>();
    if value.chars().count() > max_chars {
        output.push_str("\n...[truncated]");
    }
    output
}

fn tool_call_fingerprint(call: &crate::types::LocalToolCall) -> String {
    let input = serde_json::to_string(&call.input).unwrap_or_else(|_| "{}".to_string());
    format!("{}:{input}", call.name)
}

fn inspected_files_from_tools(tool_results: &[LocalToolResult]) -> Vec<String> {
    let mut files = Vec::new();
    for result in tool_results {
        if !result.success {
            continue;
        }
        if result.name == "search_repo" {
            if let Some(matches) = result
                .output
                .get("matches")
                .and_then(|value| value.as_array())
            {
                for item in matches {
                    if let Some(path) = item.get("path").and_then(|value| value.as_str()) {
                        push_unique_file(&mut files, path);
                    }
                }
            }
        }
        if result.name == "read_repo_file" {
            if let Some(path) = result.output.get("path").and_then(|value| value.as_str()) {
                push_unique_file(&mut files, path);
            }
        }
    }
    files.truncate(8);
    files
}

fn push_unique_file(files: &mut Vec<String>, path: &str) {
    if !files.iter().any(|existing| existing == path) {
        files.push(path.to_string());
    }
}

fn push_unique_string(values: &mut Vec<String>, value: &str) {
    if !values.iter().any(|existing| existing == value) {
        values.push(value.to_string());
    }
}

fn render_code_change_proposal(
    repo_root: &Path,
    run: &RunSummary,
    inspected_files: &[String],
    model_response: &str,
) -> (String, String) {
    let target_file = "docs/local-code-agent-proposal.md";
    let inspected = if inspected_files.is_empty() {
        "- No concrete source files were selected by the local search tool.\n".to_string()
    } else {
        inspected_files
            .iter()
            .map(|path| format!("- `{path}`\n"))
            .collect::<String>()
    };
    let (patch, proposal_source) = model_response_patch(repo_root, model_response)
        .map(|patch| (patch, "model_diff".to_string()))
        .unwrap_or_else(|| {
            (
                format!(
                    r#"```diff
diff --git a/{target_file} b/{target_file}
new file mode 100644
--- /dev/null
+++ b/{target_file}
@@
+# Proposed Structure local code-agent change
+Run: {run_id}
+Workspace: {workspace_id}
+Intent: {escaped_prompt}
+Evidence: review the run events, tool results, and this proposal artifact before applying.
```"#,
                    target_file = target_file,
                    run_id = run.run_id,
                    workspace_id = run.workspace_id,
                    escaped_prompt = run.prompt.replace('\n', " ")
                ),
                "runtime_fallback".to_string(),
            )
        });

    let proposal = format!(
        r#"# Structure Code Change Proposal

Status: proposed_only
Applied: false
Run: `{run_id}`
Workspace: `{workspace_id}`
Source: `{proposal_source}`

## Intent

{prompt}

## Files Inspected

{inspected}
## Review Notes

This artifact is generated inside the Rust local event loop. It is evidence for
the agent's proposed direction and does not modify repository files. A later
approval/apply step can turn a reviewed proposal into an actual patch.

## Patch Sketch

{patch}
"#,
        run_id = run.run_id,
        workspace_id = run.workspace_id,
        proposal_source = proposal_source,
        prompt = run.prompt,
        inspected = inspected,
        patch = patch
    );
    (proposal, proposal_source)
}

fn model_response_patch(repo_root: &Path, model_response: &str) -> Option<String> {
    let patch = extract_first_diff_block(model_response)?;
    let parsed = parse_proposal_patch(&patch).ok()?;
    safe_proposal_target_path(repo_root, &parsed.target_path).ok()?;
    Some(patch)
}

fn extract_first_diff_block(text: &str) -> Option<String> {
    let marker = "```diff";
    let start = text.find(marker)?;
    let after_marker = &text[start + marker.len()..];
    let body = after_marker.strip_prefix('\n').unwrap_or(after_marker);
    let end = body.find("\n```").unwrap_or(body.len());
    let patch_body = body[..end].trim();
    (!patch_body.is_empty()).then(|| {
        let mut patch = String::from("```diff\n");
        patch.push_str(patch_body);
        patch.push_str("\n```");
        patch
    })
}

fn extract_patch_sketch(proposal: &str) -> String {
    let marker = "```diff\n";
    let Some(start) = proposal.find(marker) else {
        return proposal.chars().take(1000).collect();
    };
    let body = &proposal[start + marker.len()..];
    let end = body.find("\n```").unwrap_or(body.len());
    body[..end].to_string()
}

#[derive(Debug, Clone)]
struct ParsedProposalPatch {
    target_path: String,
    new_file: bool,
    hunks: Vec<PatchHunk>,
}

impl ParsedProposalPatch {
    fn added_line_count(&self) -> usize {
        self.hunks
            .iter()
            .flat_map(|hunk| &hunk.lines)
            .filter(|line| matches!(line, PatchLine::Add(_)))
            .count()
    }
}

#[derive(Debug, Clone)]
struct PatchHunk {
    lines: Vec<PatchLine>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
enum PatchLine {
    Context(String),
    Add(String),
    Remove(String),
}

fn parse_proposal_patch(proposal: &str) -> Result<ParsedProposalPatch, String> {
    let mut target_path = None;
    let mut new_file = false;
    let mut in_patch = false;
    let mut hunks = Vec::new();
    let mut current_hunk: Option<PatchHunk> = None;

    for line in proposal.lines() {
        if let Some(rest) = line.strip_prefix("diff --git a/") {
            if let Some((_, target)) = rest.split_once(" b/") {
                target_path = Some(target.trim().to_string());
            }
            continue;
        }
        if line.trim() == "```diff" {
            in_patch = true;
            continue;
        }
        if in_patch && line.trim() == "```" {
            break;
        }
        if !in_patch {
            continue;
        }
        if line == "new file mode 100644" {
            new_file = true;
            continue;
        }
        if line == "--- /dev/null" {
            new_file = true;
            continue;
        }
        if let Some(rest) = line.strip_prefix("+++ b/") {
            target_path = Some(rest.trim().to_string());
            continue;
        }
        if line.starts_with("+++") || line.starts_with("---") {
            continue;
        }
        if line.starts_with("@@") {
            if let Some(hunk) = current_hunk.take() {
                hunks.push(hunk);
            }
            current_hunk = Some(PatchHunk { lines: Vec::new() });
            continue;
        }
        let Some(hunk) = current_hunk.as_mut() else {
            continue;
        };
        if let Some(added) = line.strip_prefix('+') {
            hunk.lines.push(PatchLine::Add(added.to_string()));
        } else if let Some(removed) = line.strip_prefix('-') {
            hunk.lines.push(PatchLine::Remove(removed.to_string()));
        } else if let Some(context) = line.strip_prefix(' ') {
            hunk.lines.push(PatchLine::Context(context.to_string()));
        } else if line == r"\ No newline at end of file" {
            continue;
        } else {
            hunk.lines.push(PatchLine::Context(line.to_string()));
        }
    }
    if let Some(hunk) = current_hunk.take() {
        hunks.push(hunk);
    }

    let target_path = target_path.ok_or_else(|| {
        "proposal patch does not include a diff target path generated by Structure".to_string()
    })?;
    if hunks
        .iter()
        .flat_map(|hunk| &hunk.lines)
        .all(|line| !matches!(line, PatchLine::Add(_)))
    {
        return Err("proposal patch does not contain any added lines".to_string());
    }

    Ok(ParsedProposalPatch {
        target_path,
        new_file,
        hunks,
    })
}

fn apply_parsed_patch(current_text: &str, patch: &ParsedProposalPatch) -> Result<String, String> {
    let source_lines = split_lines(current_text);
    let mut output = Vec::new();
    let mut cursor = 0usize;

    for hunk in &patch.hunks {
        let match_lines = hunk
            .lines
            .iter()
            .filter_map(|line| match line {
                PatchLine::Context(value) | PatchLine::Remove(value) => Some(value.as_str()),
                PatchLine::Add(_) => None,
            })
            .collect::<Vec<_>>();
        let start = find_hunk_start(&source_lines, cursor, &match_lines).ok_or_else(|| {
            format!(
                "proposal patch did not match target context for {}",
                patch.target_path
            )
        })?;
        output.extend(source_lines[cursor..start].iter().cloned());

        let mut source_index = start;
        for line in &hunk.lines {
            match line {
                PatchLine::Context(value) => {
                    if source_lines.get(source_index).map(String::as_str) != Some(value.as_str()) {
                        return Err(format!(
                            "proposal patch context mismatch for {}",
                            patch.target_path
                        ));
                    }
                    output.push(value.clone());
                    source_index += 1;
                }
                PatchLine::Remove(value) => {
                    if source_lines.get(source_index).map(String::as_str) != Some(value.as_str()) {
                        return Err(format!(
                            "proposal patch removal mismatch for {}",
                            patch.target_path
                        ));
                    }
                    source_index += 1;
                }
                PatchLine::Add(value) => output.push(value.clone()),
            }
        }
        cursor = source_index;
    }
    output.extend(source_lines[cursor..].iter().cloned());

    let mut text = output.join("\n");
    if !text.is_empty() {
        text.push('\n');
    }
    Ok(text)
}

fn split_lines(text: &str) -> Vec<String> {
    if text.is_empty() {
        return Vec::new();
    }
    text.lines().map(str::to_string).collect()
}

fn find_hunk_start(source: &[String], cursor: usize, match_lines: &[&str]) -> Option<usize> {
    if match_lines.is_empty() {
        return Some(cursor);
    }
    if match_lines.len() > source.len() || cursor > source.len() - match_lines.len() {
        return None;
    }
    (cursor..=source.len().saturating_sub(match_lines.len())).find(|start| {
        source[*start..*start + match_lines.len()]
            .iter()
            .map(String::as_str)
            .eq(match_lines.iter().copied())
    })
}

fn safe_proposal_target_path(repo_root: &Path, relative_path: &str) -> Result<PathBuf, String> {
    let path = Path::new(relative_path);
    if path.is_absolute() {
        return Err("proposal target must be repo-relative".to_string());
    }
    if path
        .components()
        .any(|component| matches!(component, std::path::Component::ParentDir))
    {
        return Err("proposal target escapes the workspace root".to_string());
    }
    let candidate = repo_root.join(path);
    if is_sensitive_proposal_target(&candidate) {
        return Err("refusing to apply proposal to sensitive local configuration file".to_string());
    }
    if candidate.exists() {
        let canonical = candidate
            .canonicalize()
            .map_err(|err| format!("failed to canonicalize proposal target: {err}"))?;
        if !canonical.starts_with(repo_root) {
            return Err("proposal target escapes the workspace root".to_string());
        }
        return Ok(canonical);
    }
    let parent = candidate
        .parent()
        .ok_or_else(|| "proposal target does not have a parent directory".to_string())?;
    let canonical_parent = if parent.exists() {
        parent
            .canonicalize()
            .map_err(|err| format!("failed to canonicalize proposal target parent: {err}"))?
    } else {
        let nearest = existing_parent(parent)?;
        nearest
            .canonicalize()
            .map_err(|err| format!("failed to canonicalize proposal target parent: {err}"))?
    };
    if !canonical_parent.starts_with(repo_root) {
        return Err("proposal target escapes the workspace root".to_string());
    }
    Ok(candidate)
}

fn existing_parent(path: &Path) -> Result<PathBuf, String> {
    let mut current = path;
    loop {
        if current.exists() {
            return Ok(current.to_path_buf());
        }
        current = current
            .parent()
            .ok_or_else(|| "proposal target parent escapes the workspace root".to_string())?;
    }
}

fn is_sensitive_proposal_target(path: &Path) -> bool {
    let Some(name) = path.file_name().and_then(|name| name.to_str()) else {
        return false;
    };
    if matches!(name, ".env.example" | "example.env") {
        return false;
    }
    name == ".env"
        || name.starts_with(".env.")
        || name.ends_with(".env")
        || name.ends_with(".env.local")
        || name.contains("secret")
        || name.contains("credential")
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::test_env::OpenAiEnvGuard;
    use std::io::{Read, Write};
    use std::net::TcpListener;
    use std::thread;

    #[test]
    fn local_runtime_runs_prompt_and_replays_events() {
        let _env = OpenAiEnvGuard::clear();
        let root = unique_repo("run-loop");
        let runtime = LocalAgentRuntime::open(&root).unwrap();
        let result = runtime
            .run_prompt(RunRequest {
                prompt: "Summarize this workspace".to_string(),
                workspace_id: None,
                mode: None,
            })
            .unwrap();

        assert_eq!(result.run.status, "finished");
        assert!(result
            .final_response
            .contains("embedded Structure event loop"));
        assert!(result.final_response.contains("Tool Evidence"));
        assert!(result.final_response.contains("Code Change Proposal"));
        assert!(Path::new(&result.artifact_path).exists());
        assert_eq!(
            result.events.last().map(|event| event.kind.as_str()),
            Some("run_finished")
        );
        assert!(result
            .events
            .iter()
            .any(|event| event.kind == "tool_call_completed"));
        assert!(result.events.iter().any(|event| {
            event.kind == "agent_step_planned"
                && event
                    .payload
                    .get("iteration")
                    .and_then(|value| value.as_u64())
                    == Some(2)
                && event
                    .payload
                    .get("tool_call_count")
                    .and_then(|value| value.as_u64())
                    == Some(0)
        }));
        assert!(result
            .events
            .iter()
            .any(|event| event.kind == "code_change_proposed"));
        assert_eq!(result.artifact.kind, "assistant_response");
        let artifacts = runtime
            .list_artifacts(None, Some(&result.run.run_id), 10)
            .unwrap();
        assert_eq!(artifacts.len(), 2);
        assert!(artifacts
            .iter()
            .any(|artifact| artifact.kind == "code_change_proposal"));

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn local_runtime_resolves_prompt_path_references_before_planning() {
        let _env = OpenAiEnvGuard::clear();
        let root = unique_repo("prompt-references");
        fs::create_dir_all(root.join("docs")).unwrap();
        fs::write(
            root.join("docs/local-agent.md"),
            "Structure local agent path reference evidence.",
        )
        .unwrap();
        let runtime = LocalAgentRuntime::open(&root).unwrap();

        let result = runtime
            .run_prompt(RunRequest {
                prompt: "Use @docs/local-agent.md:1 in the answer.".to_string(),
                workspace_id: None,
                mode: Some(LocalAgentMode::Chat),
            })
            .unwrap();

        assert!(result
            .events
            .iter()
            .any(|event| event.kind == "agent_step_planned"
                && event
                    .payload
                    .get("steps")
                    .and_then(|steps| steps.as_array())
                    .is_some_and(|steps| steps
                        .iter()
                        .any(|step| step.as_str() == Some("prompt_reference_resolution")))));
        assert!(result.events.iter().any(|event| {
            event.kind == "tool_call_completed"
                && event.payload.get("name").and_then(|value| value.as_str())
                    == Some("read_repo_file")
                && event
                    .payload
                    .get("output")
                    .and_then(|output| output.get("path"))
                    .and_then(|path| path.as_str())
                    == Some("docs/local-agent.md")
        }));
        assert!(result
            .final_response
            .contains("Structure local agent path reference evidence."));
        let evidence = runtime.run_evidence_summary(&result.run.run_id).unwrap();
        assert_eq!(
            evidence.prompt_references,
            vec!["docs/local-agent.md:1".to_string()]
        );
        let transcript = runtime.run_transcript(&result.run.run_id).unwrap();
        assert_eq!(
            transcript.evidence.prompt_references,
            vec!["docs/local-agent.md:1".to_string()]
        );

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn prompt_path_reference_parser_supports_line_qualified_paths() {
        let references = prompt_path_references(
            "Read @src/lib.rs:42, compare @docs/guide.md#L7 and ignore @../secret",
        );

        assert_eq!(
            references,
            vec![
                PromptPathReference {
                    display: "src/lib.rs:42".to_string(),
                    path: "src/lib.rs".to_string(),
                },
                PromptPathReference {
                    display: "docs/guide.md#L7".to_string(),
                    path: "docs/guide.md".to_string(),
                },
            ]
        );
    }

    #[test]
    fn local_runtime_loads_agents_md_as_workspace_instructions() {
        let _env = OpenAiEnvGuard::clear();
        let root = unique_repo("agent-instructions");
        fs::write(
            root.join("AGENTS.md"),
            "Always keep local CLI and desktop behavior aligned with Structure core.",
        )
        .unwrap();
        let runtime = LocalAgentRuntime::open(&root).unwrap();

        let result = runtime
            .run_prompt(RunRequest {
                prompt: "Summarize the workspace instructions.".to_string(),
                workspace_id: None,
                mode: Some(LocalAgentMode::Chat),
            })
            .unwrap();

        assert!(result.final_response.contains("Agent Instructions"));
        assert!(result
            .final_response
            .contains("Always keep local CLI and desktop behavior aligned"));
        assert!(result.events.iter().any(|event| {
            event.kind == "workspace_context_loaded"
                && event
                    .payload
                    .get("agent_instruction_count")
                    .and_then(|value| value.as_u64())
                    == Some(1)
                && event
                    .payload
                    .get("agent_instructions")
                    .and_then(|value| value.as_array())
                    .is_some_and(|items| {
                        items.iter().any(|item| {
                            item.get("path").and_then(|value| value.as_str()) == Some("AGENTS.md")
                        })
                    })
        }));
        let evidence = runtime.run_evidence_summary(&result.run.run_id).unwrap();
        assert_eq!(
            evidence.agent_instruction_paths,
            vec!["AGENTS.md".to_string()]
        );
        let transcript = runtime.run_transcript(&result.run.run_id).unwrap();
        assert_eq!(
            transcript.evidence.agent_instruction_paths,
            vec!["AGENTS.md".to_string()]
        );

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn local_runtime_exposes_agent_context_without_starting_run() {
        let _env = OpenAiEnvGuard::clear();
        let root = unique_repo("agent-context");
        fs::write(
            root.join("AGENTS.md"),
            "Use Structure core context for every local surface.",
        )
        .unwrap();
        let source_path = root.join("notes.md");
        fs::write(&source_path, "workspace knowledge").unwrap();
        let runtime = LocalAgentRuntime::open(&root).unwrap();
        runtime
            .add_knowledge_source(Some("ctx".to_string()), &source_path)
            .unwrap();
        runtime
            .run_prompt(RunRequest {
                prompt: "Seed one prior assistant turn.".to_string(),
                workspace_id: Some("ctx".to_string()),
                mode: Some(LocalAgentMode::Chat),
            })
            .unwrap();

        let context = runtime
            .agent_context(Some("ctx"), Some(LocalAgentMode::CodeAgent))
            .unwrap();

        assert_eq!(context.workspace_id, "ctx");
        assert_eq!(context.mode, "code_agent");
        assert_eq!(context.context_replay_limit, 8);
        assert_eq!(context.agent_instructions.len(), 1);
        assert_eq!(context.knowledge_sources.len(), 1);
        assert_eq!(context.recent_turns.len(), 1);
        assert!(context.repo_root.contains(root.to_str().unwrap()));
        assert!(context.runtime_db.ends_with("structure.db"));

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn local_runtime_records_worktree_snapshot_in_run_evidence() {
        let _env = OpenAiEnvGuard::clear();
        let root = unique_repo("worktree");
        run_git(&root, &["init"]);
        run_git(&root, &["checkout", "-b", "feature/local-agent"]);
        fs::write(root.join("tracked.md"), "tracked\n").unwrap();
        run_git(&root, &["add", "tracked.md"]);
        run_git(
            &root,
            &[
                "-c",
                "user.name=Structure Test",
                "-c",
                "user.email=structure@example.test",
                "commit",
                "-m",
                "seed",
            ],
        );
        fs::write(root.join("tracked.md"), "changed\n").unwrap();
        fs::write(root.join("new-note.md"), "new\n").unwrap();
        let runtime = LocalAgentRuntime::open(&root).unwrap();

        let result = runtime
            .run_prompt(RunRequest {
                prompt: "Inspect dirty worktree state.".to_string(),
                workspace_id: None,
                mode: Some(LocalAgentMode::Chat),
            })
            .unwrap();
        let evidence = runtime.run_evidence_summary(&result.run.run_id).unwrap();
        let worktree = evidence.worktree.expect("worktree snapshot should exist");

        assert!(worktree.available);
        assert!(!worktree.clean);
        assert_eq!(worktree.branch.as_deref(), Some("feature/local-agent"));
        assert!(worktree
            .changed_files
            .iter()
            .any(|change| change.path == "tracked.md"));
        assert!(worktree
            .changed_files
            .iter()
            .any(|change| change.path == "new-note.md"));
        assert!(result.final_response.contains("Worktree"));

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn local_runtime_exposes_current_worktree_snapshot() {
        let _env = OpenAiEnvGuard::clear();
        let root = unique_repo("worktree-snapshot");
        run_git(&root, &["init"]);
        run_git(&root, &["checkout", "-b", "feature/worktree-snapshot"]);
        fs::write(root.join("tracked.md"), "tracked\n").unwrap();
        run_git(&root, &["add", "tracked.md"]);
        run_git(
            &root,
            &[
                "-c",
                "user.name=Structure Test",
                "-c",
                "user.email=structure@example.test",
                "commit",
                "-m",
                "initial",
            ],
        );
        fs::write(root.join("tracked.md"), "tracked\nchanged\n").unwrap();
        fs::write(root.join("untracked.md"), "new\n").unwrap();

        let runtime = LocalAgentRuntime::open(&root).unwrap();
        let worktree = runtime.worktree_snapshot();

        assert!(worktree.available);
        assert!(!worktree.clean);
        assert_eq!(
            worktree.branch.as_deref(),
            Some("feature/worktree-snapshot")
        );
        assert!(worktree
            .changed_files
            .iter()
            .any(|change| change.path == "tracked.md"));
        assert!(worktree
            .changed_files
            .iter()
            .any(|change| change.path == "untracked.md"));

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn local_runtime_lists_explicit_workspaces() {
        let _env = OpenAiEnvGuard::clear();
        let root = unique_repo("workspaces");
        let runtime = LocalAgentRuntime::open(&root).unwrap();
        runtime
            .ensure_workspace(Some("paper".to_string()))
            .expect("workspace should be created");
        runtime
            .run_prompt(RunRequest {
                prompt: "Run inside the app workspace".to_string(),
                workspace_id: Some("app".to_string()),
                mode: None,
            })
            .unwrap();

        let workspaces = runtime.list_workspaces(10).unwrap();
        let app = runtime.workspace("app").unwrap();

        assert_eq!(app.workspace_id, "app");
        assert!(workspaces
            .iter()
            .any(|workspace| workspace.workspace_id == "paper"));
        assert!(workspaces
            .iter()
            .any(|workspace| workspace.workspace_id == "app"));

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn local_runtime_attaches_knowledge_to_context() {
        let _env = OpenAiEnvGuard::clear();
        let root = unique_repo("knowledge");
        let source_path = root.join("note.md");
        fs::write(&source_path, "local context").unwrap();
        let runtime = LocalAgentRuntime::open(&root).unwrap();
        runtime
            .add_knowledge_source(None, &source_path)
            .expect("knowledge source should be stored");
        let result = runtime
            .run_prompt(RunRequest {
                prompt: "Use the note".to_string(),
                workspace_id: None,
                mode: None,
            })
            .unwrap();

        assert!(result.final_response.contains("note.md"));
        assert!(result.final_response.contains("local context"));
        assert!(result
            .events
            .iter()
            .any(|event| event.kind == "knowledge_retrieved"));
        let evidence = runtime.run_evidence_summary(&result.run.run_id).unwrap();
        assert_eq!(evidence.knowledge_sources.len(), 1);
        assert_eq!(evidence.artifacts.len(), 2);
        assert!(evidence.tool_call_count >= 1);
        assert!(evidence
            .event_kinds
            .contains(&"knowledge_retrieved".to_string()));
        assert!(evidence
            .canonical_flow_ids
            .contains(&"disclose".to_string()));
        assert!(evidence
            .primitive_ids
            .contains(&"multi_level_disclosure".to_string()));
        assert!(evidence.core_trace.core_aligned);
        assert_eq!(
            evidence.core_trace.manifest_schema_version,
            "2026.05".to_string()
        );
        assert!(evidence
            .core_trace
            .flow_ids
            .contains(&"disclose".to_string()));
        assert!(evidence.core_trace.invalid_flow_ids.is_empty());
        assert!(evidence.core_trace.invalid_primitive_ids.is_empty());
        let transcript = runtime.run_transcript(&result.run.run_id).unwrap();
        assert_eq!(transcript.run.run_id, result.run.run_id);
        assert!(transcript
            .chat_turn
            .as_ref()
            .and_then(|turn| turn.assistant_message.as_ref())
            .is_some_and(|message| message.contains("local context")));
        assert_eq!(transcript.evidence.event_count, transcript.events.len());
        assert!(transcript
            .evidence
            .canonical_flow_ids
            .contains(&"disclose".to_string()));
        assert!(transcript.evidence.core_trace.core_aligned);

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn local_runtime_remembers_text_as_workspace_knowledge() {
        let _env = OpenAiEnvGuard::clear();
        let root = unique_repo("remember-text");
        let runtime = LocalAgentRuntime::open(&root).unwrap();
        let source = runtime
            .add_text_knowledge_source(
                Some("memory".to_string()),
                "The local agent should remember the phrase violet archive.",
            )
            .unwrap();

        assert_eq!(source.workspace_id, "memory");
        assert!(source.path.contains(".structure/local/knowledge/memory"));
        assert!(source.title.starts_with("memory_note_"));
        let preview = runtime
            .read_knowledge_source(&source.source_id, 10_000)
            .unwrap();
        assert!(preview.preview.contains("violet archive"));
        let events = runtime
            .workspace_event_feed(Some("memory"), Some(0), 20)
            .unwrap()
            .events;
        assert!(events.iter().any(|event| {
            event.kind == "workspace_context_loaded"
                && event.payload.get("action").and_then(|value| value.as_str())
                    == Some("knowledge_text_remembered")
        }));
        let context = runtime
            .agent_context(Some("memory"), Some(LocalAgentMode::Chat))
            .unwrap();
        assert_eq!(context.knowledge_sources.len(), 1);

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn local_runtime_replays_recent_chat_turns_into_next_run() {
        let _env = OpenAiEnvGuard::clear();
        let root = unique_repo("recent-turns");
        let runtime = LocalAgentRuntime::open(&root).unwrap();
        let first = runtime
            .run_prompt(RunRequest {
                prompt: "Remember the phrase blue circuit".to_string(),
                workspace_id: Some("thread".to_string()),
                mode: Some(LocalAgentMode::Chat),
            })
            .unwrap();
        let second = runtime
            .run_prompt(RunRequest {
                prompt: "What did I ask you to remember?".to_string(),
                workspace_id: Some("thread".to_string()),
                mode: Some(LocalAgentMode::Chat),
            })
            .unwrap();

        assert!(second.final_response.contains("Replayed Conversation"));
        assert!(second.final_response.contains("blue circuit"));
        let turns = runtime.chat_turns(Some("thread"), 8).unwrap();
        assert!(turns.iter().any(|turn| {
            turn.run_id == first.run.run_id && turn.mode == LocalAgentMode::Chat.as_str()
        }));
        assert!(turns.iter().any(|turn| {
            turn.run_id == second.run.run_id && turn.mode == LocalAgentMode::Chat.as_str()
        }));
        assert!(second.events.iter().any(|event| {
            event.kind == "workspace_context_loaded"
                && event
                    .payload
                    .get("recent_turns")
                    .and_then(|value| value.as_u64())
                    == Some(1)
        }));
        assert!(first.final_response.contains("blue circuit"));

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn local_runtime_replays_workspace_and_reads_artifacts() {
        let _env = OpenAiEnvGuard::clear();
        let root = unique_repo("replay");
        let runtime = LocalAgentRuntime::open(&root).unwrap();
        let result = runtime
            .run_prompt(RunRequest {
                prompt: "Create a replayable run".to_string(),
                workspace_id: None,
                mode: None,
            })
            .unwrap();

        let replay = runtime.workspace_replay(None, 50).unwrap();
        let artifact_preview = runtime
            .read_artifact(&result.artifact.artifact_id, 128)
            .unwrap();

        assert_eq!(replay.workspace_id, "default");
        assert!(replay.events.len() >= result.events.len());
        assert_eq!(replay.artifacts.len(), 2);
        assert_eq!(
            replay.last_sequence,
            replay.events.last().map(|event| event.sequence)
        );
        assert_eq!(
            artifact_preview.artifact.artifact_id,
            result.artifact.artifact_id
        );
        assert!(artifact_preview
            .preview
            .contains("Structure Local Agent Response"));

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn local_runtime_exposes_workspace_event_feed_cursor() {
        let _env = OpenAiEnvGuard::clear();
        let root = unique_repo("event-feed");
        let runtime = LocalAgentRuntime::open(&root).unwrap();
        let first = runtime
            .run_prompt(RunRequest {
                prompt: "Create cursor events".to_string(),
                workspace_id: Some("feed".to_string()),
                mode: Some(LocalAgentMode::Chat),
            })
            .unwrap();
        let feed = runtime
            .workspace_event_feed(Some("feed"), Some(0), 4)
            .unwrap();
        let next = runtime
            .workspace_event_feed(Some("feed"), Some(feed.next_after_sequence), 100)
            .unwrap();

        assert_eq!(feed.workspace_id, "feed");
        assert_eq!(feed.after_sequence, 0);
        assert_eq!(feed.events.len(), 4);
        assert_eq!(
            feed.next_after_sequence,
            feed.events.last().unwrap().sequence
        );
        assert!(next
            .events
            .iter()
            .all(|event| event.sequence > feed.next_after_sequence));
        assert!(first.events.len() > feed.events.len());

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn local_runtime_records_manual_workspace_tool_events() {
        let _env = OpenAiEnvGuard::clear();
        let root = unique_repo("manual-tool-events");
        fs::write(root.join("manual.md"), "manual event evidence").unwrap();
        let runtime = LocalAgentRuntime::open(&root).unwrap();

        let result = runtime
            .execute_workspace_tool(
                Some("ops".to_string()),
                "cli_session",
                LocalToolCall {
                    call_id: "manual_search".to_string(),
                    name: "search_repo".to_string(),
                    input: serde_json::json!({
                        "query": "manual event",
                        "max_matches": 4,
                    }),
                },
            )
            .unwrap();
        let feed = runtime
            .workspace_event_feed(Some("ops"), Some(0), 10)
            .unwrap();

        assert!(result.success);
        assert_eq!(result.name, "search_repo");
        assert_eq!(feed.events.len(), 2);
        assert_eq!(feed.events[0].kind, "tool_call_requested");
        assert_eq!(feed.events[0].run_id, None);
        assert_eq!(feed.events[0].payload["surface"], "cli_session");
        assert_eq!(feed.events[0].payload["name"], "search_repo");
        assert_eq!(feed.events[1].kind, "tool_call_completed");
        assert_eq!(feed.events[1].run_id, None);
        assert_eq!(feed.events[1].payload["surface"], "cli_session");
        assert_eq!(feed.events[1].payload["success"], true);

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn local_runtime_builds_evidence_bundle() {
        let _env = OpenAiEnvGuard::clear();
        let root = unique_repo("bundle");
        write_parity_marker_files(&root);
        let runtime = LocalAgentRuntime::open(&root).unwrap();
        let result = runtime
            .run_prompt(RunRequest {
                prompt: "Bundle this local evidence".to_string(),
                workspace_id: None,
                mode: None,
            })
            .unwrap();

        let bundle = runtime.local_evidence_bundle(None, 50).unwrap();

        assert_eq!(bundle.schema_version, "local-evidence-bundle-v1");
        assert_eq!(bundle.workspace_id, "default");
        assert!(bundle.parity_report.passed);
        assert!(bundle
            .workspace_replay
            .runs
            .iter()
            .any(|run| run.run_id == result.run.run_id));
        assert!(bundle
            .run_evidence
            .iter()
            .any(|evidence| evidence.run.run_id == result.run.run_id));

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn local_runtime_previews_and_removes_knowledge_source() {
        let _env = OpenAiEnvGuard::clear();
        let root = unique_repo("knowledge-preview");
        let source_path = root.join("source.md");
        fs::write(&source_path, "first line\nsecond line\nthird line").unwrap();
        let runtime = LocalAgentRuntime::open(&root).unwrap();
        let source = runtime
            .add_knowledge_source(None, &source_path)
            .expect("knowledge source should be stored");

        let preview = runtime
            .read_knowledge_source(&source.source_id, 12)
            .expect("knowledge source should be readable");
        let removed = runtime
            .remove_knowledge_source(&source.source_id)
            .expect("knowledge source should be removable");
        let remaining = runtime.knowledge_sources(None, 10).unwrap();

        assert_eq!(preview.source.source_id, source.source_id);
        assert_eq!(preview.preview, "first line\ns");
        assert_eq!(preview.bytes_read, 12);
        assert!(preview.truncated);
        assert_eq!(removed.source_id, source.source_id);
        assert!(remaining.is_empty());
        assert!(source_path.exists());

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn local_runtime_applies_reviewed_code_change_proposal() {
        let _env = OpenAiEnvGuard::clear();
        let root = unique_repo("proposal-apply");
        let runtime = LocalAgentRuntime::open(&root).unwrap();
        let result = runtime
            .run_prompt(RunRequest {
                prompt: "Create a local proposal for the code agent".to_string(),
                workspace_id: Some("apply".to_string()),
                mode: Some(LocalAgentMode::CodeAgent),
            })
            .unwrap();
        let proposal = runtime
            .list_artifacts(Some("apply"), Some(&result.run.run_id), 10)
            .unwrap()
            .into_iter()
            .find(|artifact| artifact.kind == "code_change_proposal")
            .expect("proposal artifact should exist");

        let dry_run = runtime
            .apply_code_change_proposal(&proposal.artifact_id, true)
            .unwrap();
        assert!(!dry_run.applied);
        assert!(dry_run.dry_run);
        assert_eq!(dry_run.bytes_written, 0);
        assert!(!Path::new(&dry_run.target_path).exists());

        let applied = runtime
            .apply_code_change_proposal(&proposal.artifact_id, false)
            .unwrap();
        assert!(applied.applied);
        assert!(!applied.dry_run);
        assert!(applied.added_lines >= 1);
        assert!(applied.bytes_written > 0);
        let target_text = fs::read_to_string(&applied.target_path).unwrap();
        assert!(target_text.contains("Proposed Structure local code-agent change"));
        assert!(applied
            .target_path
            .ends_with("docs/local-code-agent-proposal.md"));
        let events = runtime.run_events(&result.run.run_id).unwrap();
        assert!(events
            .iter()
            .any(|event| event.kind == "code_change_applied"));

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn parsed_proposal_patch_replaces_existing_context() {
        let proposal = r#"# Reviewed Proposal

```diff
diff --git a/docs/example.md b/docs/example.md
--- a/docs/example.md
+++ b/docs/example.md
@@
 heading
-old line
+new line
 tail
```
"#;
        let patch = parse_proposal_patch(proposal).unwrap();
        let patched = apply_parsed_patch("heading\nold line\ntail\n", &patch).unwrap();

        assert_eq!(patch.target_path, "docs/example.md");
        assert!(!patch.new_file);
        assert_eq!(patch.added_line_count(), 1);
        assert_eq!(patched, "heading\nnew line\ntail\n");
    }

    #[test]
    fn generated_proposal_targets_review_artifact_file() {
        let root = unique_repo("fallback-proposal").canonicalize().unwrap();
        let run = RunSummary {
            run_id: "run_test".to_string(),
            workspace_id: "default".to_string(),
            prompt: "Inspect core manifest".to_string(),
            status: "finished".to_string(),
            final_response: None,
            created_at_ms: 1,
            updated_at_ms: 1,
        };
        let proposal = render_code_change_proposal(
            &root,
            &run,
            &[
                "core/structure_core.json".to_string(),
                "src/main.rs".to_string(),
            ],
            "No model diff was provided.",
        );
        let (proposal, proposal_source) = proposal;
        let patch = parse_proposal_patch(&proposal).unwrap();

        assert_eq!(patch.target_path, "docs/local-code-agent-proposal.md");
        assert!(patch.new_file);
        assert_eq!(proposal_source, "runtime_fallback");
        assert!(proposal.contains("core/structure_core.json"));
        assert!(proposal.contains("new file mode 100644"));

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn generated_proposal_prefers_safe_model_diff() {
        let root = unique_repo("model-proposal").canonicalize().unwrap();
        let run = RunSummary {
            run_id: "run_model_patch".to_string(),
            workspace_id: "default".to_string(),
            prompt: "Add a note".to_string(),
            status: "finished".to_string(),
            final_response: None,
            created_at_ms: 1,
            updated_at_ms: 1,
        };
        let model_response = r#"Here is the proposed patch.

```diff
diff --git a/docs/model-note.md b/docs/model-note.md
new file mode 100644
--- /dev/null
+++ b/docs/model-note.md
@@
+# Model proposed note
+This patch came from the model response.
```
"#;

        let (proposal, proposal_source) =
            render_code_change_proposal(&root, &run, &[], model_response);
        let patch = parse_proposal_patch(&proposal).unwrap();

        assert_eq!(proposal_source, "model_diff");
        assert_eq!(patch.target_path, "docs/model-note.md");
        assert!(proposal.contains("Source: `model_diff`"));
        assert!(proposal.contains("Model proposed note"));

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn local_runtime_loads_openai_env_from_repo_dotenv_without_overriding_shell() {
        let _env = OpenAiEnvGuard::clear();
        env::set_var("OPENAI__MODEL", "shell-model");
        let root = unique_repo("dotenv");
        fs::write(
            root.join(".env"),
            "OPENAI__API_KEY=dotenv-key\nOPENAI__BASE_URL=http://dotenv.test/v1\nOPENAI__MODEL=dotenv-model\n",
        )
        .unwrap();

        let runtime = LocalAgentRuntime::open(&root).unwrap();

        assert_eq!(runtime.repo_root(), root.canonicalize().unwrap());
        assert_eq!(env::var("OPENAI__API_KEY").unwrap(), "dotenv-key");
        assert_eq!(
            env::var("OPENAI__BASE_URL").unwrap(),
            "http://dotenv.test/v1"
        );
        assert_eq!(env::var("OPENAI__MODEL").unwrap(), "shell-model");

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn local_runtime_reports_missing_llm_diagnostic_without_network() {
        let _env = OpenAiEnvGuard::clear();
        let root = unique_repo("llm-diagnostic-missing");
        let runtime = LocalAgentRuntime::open(&root).unwrap();

        let diagnostic = runtime.llm_diagnostic();

        assert_eq!(diagnostic.provider, "local_env_api");
        assert!(!diagnostic.configured);
        assert!(!diagnostic.ok);
        assert!(diagnostic.error.unwrap().contains("OPENAI__BASE_URL"));
        assert!(diagnostic.endpoint.is_none());

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn local_runtime_continues_from_run_transcript_and_evidence() {
        let _env = OpenAiEnvGuard::clear();
        let root = unique_repo("continuation");
        let runtime = LocalAgentRuntime::open(&root).unwrap();
        let first = runtime
            .run_prompt(RunRequest {
                prompt: "Remember the phrase silver lattice".to_string(),
                workspace_id: Some("thread".to_string()),
                mode: Some(LocalAgentMode::Chat),
            })
            .unwrap();

        let continuation = runtime
            .run_continuation_attempt(ContinuationRequest {
                run_id: first.run.run_id.clone(),
                extra_instruction: Some("Say the remembered phrase back.".to_string()),
                mode: Some(LocalAgentMode::Chat),
            })
            .unwrap();

        assert_eq!(continuation.run.workspace_id, "thread");
        assert!(continuation.run.prompt.contains(&first.run.run_id));
        assert!(continuation
            .run
            .prompt
            .contains("Remember the phrase silver lattice"));
        assert!(continuation
            .run
            .prompt
            .contains("Say the remembered phrase back."));
        assert!(continuation.run.prompt.contains("core aligned: true"));
        assert!(continuation
            .events
            .iter()
            .any(|event| event.kind == "run_finished"));

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn local_runtime_continuation_excerpt_handles_empty_runtime_envelope() {
        let response = "# Structure Local Agent Response\n\n\
This response was produced by the embedded Structure event loop.\n\n\
- Provider: `local_env_api`\n\
- Mode: `chat`\n\n\
\n\
## Tool Evidence Summary\n\n\
- list_workspace: ok\n";

        assert_eq!(
            continuation_response_excerpt(response),
            "Previous response contained no assistant body before tool evidence."
        );
    }

    #[test]
    fn local_runtime_continuation_excerpt_strips_markdown_runtime_envelope() {
        let response = "# Structure Local Agent Response\n\
This response was produced by the embedded Structure event loop.\n\
**Provider:** `local_env_api`\n\
**Mode:** `chat`\n\n\
User-facing follow-up.\n\n\
## Tool Evidence Summary\n";

        assert_eq!(
            continuation_response_excerpt(response),
            "User-facing follow-up."
        );
    }

    #[test]
    fn local_runtime_checks_configured_llm_api() {
        let _env = OpenAiEnvGuard::clear();
        let server = MockOpenAiServer::start(vec![serde_json::json!({
            "id": "chatcmpl-diagnostic",
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "structure-local-ok"
                    },
                    "finish_reason": "stop"
                }
            ]
        })]);
        env::set_var("OPENAI__API_KEY", "mock-key");
        env::set_var("OPENAI__BASE_URL", server.base_url());
        env::set_var("OPENAI__MODEL", "mock-openai-model");
        let root = unique_repo("llm-diagnostic-ok");
        let runtime = LocalAgentRuntime::open(&root).unwrap();

        let diagnostic = runtime.llm_diagnostic();

        assert!(diagnostic.configured);
        assert!(diagnostic.ok);
        assert_eq!(diagnostic.model.as_deref(), Some("mock-openai-model"));
        assert!(diagnostic
            .endpoint
            .as_deref()
            .unwrap_or_default()
            .ends_with("/chat/completions"));
        assert_eq!(
            diagnostic.response_preview.as_deref(),
            Some("structure-local-ok")
        );
        assert!(diagnostic.error.is_none());

        server.join();
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn local_runtime_persists_failed_run_when_openai_api_fails() {
        let _env = OpenAiEnvGuard::clear();
        let root = unique_repo("api-failure");
        fs::write(
            root.join(".env"),
            "OPENAI__API_KEY=test-key\nOPENAI__BASE_URL=http://127.0.0.1:9/v1\nOPENAI__MODEL=test-model\n",
        )
        .unwrap();
        let runtime = LocalAgentRuntime::open(&root).unwrap();

        let error = runtime
            .run_prompt(RunRequest {
                prompt: "This should record a failed API-backed run".to_string(),
                workspace_id: Some("api-failure".to_string()),
                mode: Some(LocalAgentMode::CodeAgent),
            })
            .unwrap_err();
        let runs = runtime.list_runs(Some("api-failure"), 1).unwrap();
        let run = runs.first().expect("failed run should be persisted");
        let events = runtime.run_events(&run.run_id).unwrap();

        assert!(error.contains("local LLM API request failed"));
        assert_eq!(run.status, "failed");
        assert!(run
            .final_response
            .as_deref()
            .unwrap_or_default()
            .contains("local LLM API request failed"));
        assert!(events.iter().any(|event| {
            event.kind == "model_requested"
                && event.payload.get("phase").and_then(|value| value.as_str())
                    == Some("tool_planning")
                && event
                    .payload
                    .get("provider")
                    .and_then(|value| value.as_str())
                    == Some("local_env_api")
        }));
        assert!(events.iter().any(|event| event.kind == "run_failed"));

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn local_runtime_returns_failed_run_attempt_with_events() {
        let _env = OpenAiEnvGuard::clear();
        let root = unique_repo("api-attempt-failure");
        fs::write(
            root.join(".env"),
            "OPENAI__API_KEY=test-key\nOPENAI__BASE_URL=http://127.0.0.1:9/v1\nOPENAI__MODEL=test-model\n",
        )
        .unwrap();
        let runtime = LocalAgentRuntime::open(&root).unwrap();

        let attempt = runtime
            .run_prompt_attempt(RunRequest {
                prompt: "Return a failed attempt with replayable events".to_string(),
                workspace_id: Some("api-attempt-failure".to_string()),
                mode: Some(LocalAgentMode::CodeAgent),
            })
            .unwrap();

        assert_eq!(attempt.run.status, "failed");
        assert!(attempt.result.is_none());
        assert!(attempt
            .error
            .as_deref()
            .unwrap_or_default()
            .contains("local LLM API request failed"));
        assert!(attempt.events.iter().any(|event| {
            event.kind == "model_requested"
                && event
                    .payload
                    .get("provider")
                    .and_then(|value| value.as_str())
                    == Some("local_env_api")
        }));
        assert!(attempt
            .events
            .iter()
            .any(|event| event.kind == "run_failed"));

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn local_runtime_completes_openai_compatible_tool_call_loop() {
        let _env = OpenAiEnvGuard::clear();
        let server = MockOpenAiServer::start(vec![
            serde_json::json!({
                "id": "chatcmpl-plan",
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": null,
                            "tool_calls": [
                                {
                                    "id": "call_list_workspace",
                                    "type": "function",
                                    "function": {
                                        "name": "list_workspace",
                                        "arguments": "{\"max_entries\":5}"
                                    }
                                }
                            ]
                        },
                        "finish_reason": "tool_calls"
                    }
                ],
                "usage": {
                    "prompt_tokens": 11,
                    "completion_tokens": 7,
                    "total_tokens": 18
                }
            }),
            serde_json::json!({
                "id": "chatcmpl-plan-done",
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "No more tools are required."
                        },
                        "finish_reason": "stop"
                    }
                ],
                "usage": {
                    "prompt_tokens": 13,
                    "completion_tokens": 5,
                    "total_tokens": 18
                }
            }),
            serde_json::json!({
                "id": "chatcmpl-synthesis",
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "Mock API synthesized an answer from the Structure local tool evidence."
                        },
                        "finish_reason": "stop"
                    }
                ],
                "usage": {
                    "prompt_tokens": 17,
                    "completion_tokens": 9,
                    "total_tokens": 26
                }
            }),
        ]);
        env::set_var("OPENAI__API_KEY", "mock-key");
        env::set_var("OPENAI__BASE_URL", server.base_url());
        env::set_var("OPENAI__MODEL", "mock-openai-model");
        let root = unique_repo("api-success");
        fs::write(root.join("README.md"), "mock api workspace").unwrap();
        let runtime = LocalAgentRuntime::open(&root).unwrap();

        let result = runtime
            .run_prompt(RunRequest {
                prompt: "Use the configured API model and inspect this workspace.".to_string(),
                workspace_id: Some("api-success".to_string()),
                mode: Some(LocalAgentMode::CodeAgent),
            })
            .unwrap();
        let events = runtime.run_events(&result.run.run_id).unwrap();

        assert_eq!(result.run.status, "finished");
        assert!(result.final_response.contains("environment API model"));
        assert!(result
            .final_response
            .contains("Mock API synthesized an answer"));
        assert!(events.iter().any(|event| {
            event.kind == "model_requested"
                && event.payload["provider"] == "local_env_api"
                && event.payload["network_required"] == true
        }));
        assert!(events.iter().any(|event| {
            event.kind == "tool_call_requested"
                && event.payload["name"] == "list_workspace"
                && event.payload["call_id"] == "call_list_workspace"
        }));
        assert!(events.iter().any(|event| {
            event.kind == "model_responded"
                && event.payload["phase"] == "response_synthesis"
                && event.payload["provider"] == "local_env_api"
                && event.payload["usage"]["total_tokens"] == 26
        }));
        let evidence = runtime.run_evidence_summary(&result.run.run_id).unwrap();
        assert_eq!(evidence.model_usage.model_request_count, 3);
        assert_eq!(evidence.model_usage.model_response_count, 3);
        assert_eq!(evidence.model_usage.network_request_count, 3);
        assert_eq!(evidence.model_usage.prompt_tokens, 41);
        assert_eq!(evidence.model_usage.completion_tokens, 21);
        assert_eq!(evidence.model_usage.total_tokens, 62);
        assert!(evidence.model_usage.response_chars > 0);
        assert!(runtime
            .list_artifacts(Some("api-success"), Some(&result.run.run_id), 10)
            .unwrap()
            .iter()
            .any(|artifact| artifact.kind == "code_change_proposal"));
        server.join();
        fs::remove_dir_all(root).unwrap();
    }

    struct MockOpenAiServer {
        base_url: String,
        handle: Option<thread::JoinHandle<()>>,
    }

    impl MockOpenAiServer {
        fn start(responses: Vec<serde_json::Value>) -> Self {
            let listener = TcpListener::bind("127.0.0.1:0").unwrap();
            let address = listener.local_addr().unwrap();
            let handle = thread::spawn(move || {
                for response in responses {
                    let (mut stream, _) = listener.accept().unwrap();
                    read_http_request(&mut stream);
                    let body = serde_json::to_string(&response).unwrap();
                    let reply = format!(
                        "HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: {}\r\nconnection: close\r\n\r\n{}",
                        body.len(),
                        body
                    );
                    stream.write_all(reply.as_bytes()).unwrap();
                }
            });
            Self {
                base_url: format!("http://{address}/v1"),
                handle: Some(handle),
            }
        }

        fn base_url(&self) -> &str {
            &self.base_url
        }

        fn join(mut self) {
            if let Some(handle) = self.handle.take() {
                handle.join().unwrap();
            }
        }
    }

    fn read_http_request(stream: &mut std::net::TcpStream) {
        let mut buffer = Vec::new();
        let mut chunk = [0_u8; 1024];
        loop {
            let read = stream.read(&mut chunk).unwrap();
            if read == 0 {
                break;
            }
            buffer.extend_from_slice(&chunk[..read]);
            let Some(header_end) = find_header_end(&buffer) else {
                continue;
            };
            let header = String::from_utf8_lossy(&buffer[..header_end]);
            let content_length = header
                .lines()
                .find_map(|line| {
                    let (name, value) = line.split_once(':')?;
                    name.eq_ignore_ascii_case("content-length")
                        .then(|| value.trim().parse::<usize>().ok())
                        .flatten()
                })
                .unwrap_or_default();
            let body_start = header_end + 4;
            if buffer.len() >= body_start + content_length {
                break;
            }
        }
    }

    fn run_git(root: &Path, args: &[&str]) {
        let output = Command::new("git")
            .arg("-C")
            .arg(root)
            .args(args)
            .output()
            .unwrap_or_else(|err| panic!("failed to run git {args:?}: {err}"));
        assert!(
            output.status.success(),
            "git {:?} failed: {}",
            args,
            String::from_utf8_lossy(&output.stderr)
        );
    }

    fn find_header_end(buffer: &[u8]) -> Option<usize> {
        buffer.windows(4).position(|window| window == b"\r\n\r\n")
    }

    fn unique_repo(label: &str) -> PathBuf {
        let root = std::env::temp_dir().join(format!(
            "structure-local-runtime-repo-{label}-{}-{}",
            std::process::id(),
            crate::store::now_ms()
        ));
        fs::create_dir_all(root.join("src/structure")).unwrap();
        fs::create_dir_all(root.join("frontend")).unwrap();
        fs::write(root.join("pyproject.toml"), "").unwrap();
        root
    }

    fn write_parity_marker_files(root: &Path) {
        write_file(
            root,
            "crates/structure-local/src/cli.rs",
            r#"
            Command::Core Command::Surfaces Command::Chat Command::Run Command::Continue Command::Proposals Command::Tui
            run_core run_surfaces run_chat_agent run_continue_agent run_continuation_from_session run_local_agent
            WorkspaceCommand::Create WorkspaceCommand::List WorkspaceCommand::Show
            KnowledgeCommand::Add KnowledgeCommand::Show KnowledgeCommand::Remove
            RunsCommand::Events RunsCommand::Transcript WorkspaceCommand::Events WorkspaceCommand::Replay
            ArtifactsCommand::List ArtifactsCommand::Show
            ProposalsCommand::List ProposalsCommand::Show ProposalsCommand::Apply
            "#,
        );
        write_file(
            root,
            "frontend/src-tauri/src/lib.rs",
            r#"
            fn local_snapshot() {} fn core_manifest() {} core_manifest,
            fn local_agent_run() {} fn local_agent_continue_attempt() {}
            fn local_chat_turns() {} local_chat_turns,
            fn create_local_workspace() {} fn local_workspaces() {}
            fn local_repo_entries() {} fn local_repo_search() {} fn read_local_repo_file() {}
            fn add_local_knowledge() {} fn read_local_knowledge_source() {}
            fn remove_local_knowledge() {} remove_local_knowledge,
            fn local_run_transcript() {} fn local_run_events() {}
            fn local_workspace_event_feed() {} local_workspace_event_feed,
            fn local_workspace_replay() {} local_workspace_replay,
            fn local_artifacts() {} fn read_local_artifact() {} read_local_artifact,
            fn apply_local_proposal() {}
            "#,
        );
        write_file(
            root,
            "frontend/src-tauri/local-ui/index.html",
            r#"Preview Latest Proposal Code-Agent Chat"#,
        );
        write_file(
            root,
            "frontend/src/core/structureCore.ts",
            "STRUCTURE_CORE_MANIFEST BenchmarkReportSchema CoreCapability benchmark_report_schema",
        );
        write_file(
            root,
            "src/structure/routers/runs/runs.py",
            "async def create_run(): pass",
        );
        write_file(
            root,
            "src/structure/services/runs/run_application_service.py",
            "async def start_user_run(): pass",
        );
        write_file(
            root,
            "src/structure/services/workspace_context/workspace_context_service.py",
            "class WorkspaceContextService: pass\nasync def get(): pass\nasync def list(): pass",
        );
        write_file(
            root,
            "src/structure/routers/workspaces/workspace.py",
            "async def list_workspace_contexts(): pass\nasync def remove_workspace_context(): pass",
        );
        write_file(
            root,
            "frontend/src/services/eventService.ts",
            "LIST_BY_WORKSPACE LIST_BY_RUN",
        );
        write_file(
            root,
            "src/structure/services/events/event_crud.py",
            "class EventCRUD: pass",
        );
        write_file(
            root,
            "src/structure/routers/runs/artifacts.py",
            "async def list_artifacts(): pass\nasync def get_artifact(): pass",
        );
        write_file(
            root,
            "src/structure/services/runs/artifact_crud.py",
            "class ArtifactCRUD: pass",
        );
        write_file(
            root,
            "benchmarks/adapters/structure_run.py",
            "class StructureRunBenchmarkAgent: pass",
        );
    }

    fn write_file(root: &Path, relative_path: &str, text: &str) {
        let path = root.join(relative_path);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, text).unwrap();
    }
}
