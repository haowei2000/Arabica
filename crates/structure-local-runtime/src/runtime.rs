use crate::model::{
    selected_planning_provider, selected_synthesis_provider, EnvApiModelProvider, ModelRequest,
};
use crate::store::{new_id, now_ms, SqliteLocalStore};
use crate::tools::{BuiltinLocalToolRegistry, LocalToolRegistry};
use crate::types::{
    AgentInstruction, ArtifactPreview, ArtifactRecord, ChatTurn, CommandTurn, CoreExecutionTrace,
    EventGcPreview, EventGcSummary, KnowledgeSource, KnowledgeSourcePreview, LocalAgentContext,
    LocalAgentMode, LocalEvent, LocalEvidenceBundle, LocalLlmDiagnostic, LocalRunCompact,
    LocalRunCoreTrace, LocalRunCoreTraceStep, LocalRunPlan, LocalRunPlanStep, LocalRunReview,
    LocalRunStatusEvent, LocalRunStatusSnapshot, LocalTaskRecord, LocalToolCall, LocalToolResult,
    LocalToolTraceEntry, ModelTokenUsage, ModelUsageSummary, ProposalApplyResult, ProposalReview,
    ProposalReviewCheck, ProposalRollbackResult, RunAttempt, RunCheckpoint, RunEventKind,
    RunEvidenceSummary, RunResult, RunStatus, RunSummary, RunTranscript, SourceRating,
    WorkspaceCompact, WorkspaceCompactRun, WorkspaceEventFeed, WorkspaceReplay, WorkspaceSummary,
    WorkspaceUsageRun, WorkspaceUsageSummary, WorktreeChange, WorktreeSnapshot,
};
use serde::Serialize;
use std::collections::HashMap;
use std::env;
use std::fs;
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::process::Command;
use structure_local_core::{
    collect_snapshot, default_repo_root, structure_core_manifest,
    verify_structure_core_parity_for_repo,
};

pub const DEFAULT_EVENT_GC_RETAIN_LAST: usize = 24;

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

#[derive(Debug, Clone)]
pub struct WorkspaceContinuationRequest {
    pub workspace_id: Option<String>,
    pub extra_instruction: Option<String>,
    pub mode: Option<LocalAgentMode>,
    pub limit: usize,
}

pub struct LocalAgentRuntime {
    repo_root: PathBuf,
    runtime_dir: PathBuf,
    store: SqliteLocalStore,
}

#[derive(Debug, Clone, Copy)]
struct SourceRatingPriority {
    rating: u8,
    sequence: i64,
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

    pub fn rate_knowledge_source(
        &self,
        source_id: &str,
        run_id: Option<&str>,
        rating: u8,
        note: &str,
    ) -> Result<SourceRating, String> {
        if !(1..=5).contains(&rating) {
            return Err("source rating must be between 1 and 5".to_string());
        }
        let note = note.trim();
        if note.chars().count() > 2_000 {
            return Err("source rating note must be 2000 characters or fewer".to_string());
        }
        let source = self.knowledge_source(source_id)?;
        if let Some(run_id) = run_id {
            let run = self.run_by_id(run_id)?;
            if run.workspace_id != source.workspace_id {
                return Err("source rating run must belong to the same workspace".to_string());
            }
        }
        let rating = SourceRating {
            source_id: source.source_id.clone(),
            workspace_id: source.workspace_id.clone(),
            run_id: run_id.map(str::to_string),
            rating,
            note: note.to_string(),
            source_title: source.title.clone(),
            source_path: source.path.clone(),
            created_at_ms: now_ms(),
        };
        self.store.append_event(
            &source.workspace_id,
            run_id,
            RunEventKind::SourceRated,
            &rating,
        )?;
        Ok(rating)
    }

    pub fn record_run_checkpoint(&self, run_id: &str, note: &str) -> Result<RunCheckpoint, String> {
        let note = note.trim();
        if note.is_empty() {
            return Err("checkpoint note must not be empty".to_string());
        }
        if note.chars().count() > 4_000 {
            return Err("checkpoint note must be 4000 characters or fewer".to_string());
        }
        let run = self.run_by_id(run_id)?;
        let checkpoint = RunCheckpoint {
            checkpoint_id: new_id("chk"),
            run_id: run.run_id.clone(),
            workspace_id: run.workspace_id.clone(),
            note: note.to_string(),
            created_at_ms: now_ms(),
        };
        self.store.append_event(
            &checkpoint.workspace_id,
            Some(&checkpoint.run_id),
            RunEventKind::RunCheckpointRecorded,
            &checkpoint,
        )?;
        Ok(checkpoint)
    }

    pub fn create_task(
        &self,
        workspace_id: Option<String>,
        run_id: Option<&str>,
        title: &str,
        priority: Option<&str>,
    ) -> Result<LocalTaskRecord, String> {
        let title = title.trim();
        if title.is_empty() {
            return Err("task title must not be empty".to_string());
        }
        if title.chars().count() > 2_000 {
            return Err("task title must be 2000 characters or fewer".to_string());
        }
        let priority = normalize_task_priority(priority.unwrap_or("normal"))?;
        let workspace = if let Some(run_id) = run_id {
            let run = self.run_by_id(run_id)?;
            if let Some(workspace_id) = workspace_id.as_deref() {
                if workspace_id != run.workspace_id {
                    return Err("task run must belong to the selected workspace".to_string());
                }
            }
            self.store.workspace_by_id(&run.workspace_id)?
        } else {
            self.store.ensure_workspace(workspace_id, &self.repo_root)?
        };
        let task = self
            .store
            .create_task(&workspace.workspace_id, run_id, title, priority)?;
        self.store.append_event(
            &task.workspace_id,
            task.run_id.as_deref(),
            RunEventKind::TaskCreated,
            &task,
        )?;
        Ok(task)
    }

    pub fn update_task_status(
        &self,
        task_id: &str,
        status: &str,
        note: Option<&str>,
    ) -> Result<LocalTaskRecord, String> {
        let status = normalize_task_status(status)?;
        let task = self.store.update_task_status(task_id, status)?;
        self.store.append_event(
            &task.workspace_id,
            task.run_id.as_deref(),
            RunEventKind::TaskUpdated,
            &serde_json::json!({
                "task": task,
                "note": note.unwrap_or("").trim(),
            }),
        )?;
        Ok(task)
    }

    pub fn list_tasks(
        &self,
        workspace_id: Option<&str>,
        status: Option<&str>,
        limit: usize,
    ) -> Result<Vec<LocalTaskRecord>, String> {
        let status = status.map(normalize_task_status).transpose()?;
        self.store
            .list_tasks(workspace_id, status, limit.clamp(1, 500))
    }

    pub fn task(&self, task_id: &str) -> Result<LocalTaskRecord, String> {
        self.store.task_by_id(task_id)
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

    pub fn record_command_turn(
        &self,
        workspace_id: Option<&str>,
        input: &str,
        output: &str,
        status: &str,
        surface: &str,
    ) -> Result<CommandTurn, String> {
        let workspace_id = workspace_id.unwrap_or("default");
        self.ensure_workspace(Some(workspace_id.to_string()))?;
        let turn = CommandTurn {
            workspace_id: workspace_id.to_string(),
            input: input.to_string(),
            output: output.to_string(),
            status: status.to_string(),
            surface: surface.to_string(),
            created_at_ms: now_ms(),
        };
        self.store
            .append_event(workspace_id, None, RunEventKind::CommandTurnRecorded, &turn)?;
        Ok(turn)
    }

    pub fn command_turns(
        &self,
        workspace_id: Option<&str>,
        limit: usize,
    ) -> Result<Vec<CommandTurn>, String> {
        let workspace_id = workspace_id.unwrap_or("default");
        let scan_limit = limit.clamp(1, 100).saturating_mul(10).max(100);
        let events = self.store.workspace_events(workspace_id, scan_limit)?;
        let mut turns = events
            .into_iter()
            .filter(|event| event.kind == "command_turn_recorded")
            .filter_map(|event| serde_json::from_value::<CommandTurn>(event.payload).ok())
            .collect::<Vec<_>>();
        let excess = turns.len().saturating_sub(limit);
        if excess > 0 {
            turns.drain(0..excess);
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
        let tasks = self.store.list_tasks(Some(workspace_id), None, limit)?;
        let artifacts = self.store.list_artifacts(Some(workspace_id), None, limit)?;
        let last_sequence = events.last().map(|event| event.sequence);

        Ok(WorkspaceReplay {
            workspace_id: workspace_id.to_string(),
            events,
            runs,
            knowledge_sources,
            tasks,
            artifacts,
            last_sequence,
        })
    }

    pub fn workspace_compact(
        &self,
        workspace_id: Option<&str>,
        limit: usize,
    ) -> Result<WorkspaceCompact, String> {
        let replay = self.workspace_replay(workspace_id, limit)?;
        let mut recent_runs = Vec::new();
        for run in &replay.runs {
            let Ok(transcript) = self.run_transcript(&run.run_id) else {
                continue;
            };
            let compact = run_compact_from_transcript(&transcript);
            recent_runs.push(WorkspaceCompactRun {
                run_id: run.run_id.clone(),
                status: run.status.clone(),
                prompt_summary: one_line_compact(&run.prompt, 320),
                response_summary: run
                    .final_response
                    .as_deref()
                    .map(continuation_response_excerpt)
                    .map(|response| one_line_compact(&response, 480)),
                event_count: compact.event_count,
                tool_call_count: compact.tool_call_count,
                model_usage: compact.model_usage,
                artifact_paths: compact.artifact_paths,
                core_aligned: compact.core_aligned,
                updated_at_ms: run.updated_at_ms,
            });
        }
        Ok(workspace_compact_from_replay(replay, recent_runs))
    }

    pub fn workspace_usage(
        &self,
        workspace_id: Option<&str>,
        limit: usize,
    ) -> Result<WorkspaceUsageSummary, String> {
        let workspace_id = workspace_id.unwrap_or("default");
        let runs = self.store.list_runs(Some(workspace_id), limit)?;
        let knowledge_source_count = self
            .store
            .list_knowledge_sources(workspace_id, limit)?
            .len();
        let artifact_count = self
            .store
            .list_artifacts(Some(workspace_id), None, limit)?
            .len();
        let tasks = self.store.list_tasks(Some(workspace_id), None, limit)?;
        let task_count = tasks.len();
        let active_task_count = tasks.iter().filter(|task| task_is_active(task)).count();
        let mut model_usage = ModelUsageSummary::default();
        let mut event_count = 0;
        let mut tool_call_count = 0;
        let mut core_aligned = true;
        let mut flow_path = Vec::new();
        let mut primitive_path = Vec::new();
        let mut usage_runs = Vec::new();

        for run in runs {
            let evidence = self.run_evidence_summary(&run.run_id)?;
            event_count += evidence.event_count;
            tool_call_count += evidence.tool_call_count;
            add_model_usage(&mut model_usage, &evidence.model_usage);
            core_aligned &= evidence.core_trace.core_aligned;
            extend_unique(&mut flow_path, &evidence.core_trace.flow_ids);
            extend_unique(&mut primitive_path, &evidence.core_trace.primitive_ids);
            usage_runs.push(WorkspaceUsageRun {
                run_id: evidence.run.run_id,
                status: evidence.run.status,
                event_count: evidence.event_count,
                tool_call_count: evidence.tool_call_count,
                artifact_count: evidence.artifacts.len(),
                model_usage: evidence.model_usage,
                core_aligned: evidence.core_trace.core_aligned,
                updated_at_ms: evidence.run.updated_at_ms,
            });
        }

        let summary = format!(
            "Workspace {workspace_id} has {run_count} recent runs, {event_count} run events, {tool_call_count} completed tool calls, {task_count} tasks ({active_task_count} active), {model_responses} model responses, {network_requests} network requests, and {total_tokens} total tokens. Core alignment is {alignment}.",
            run_count = usage_runs.len(),
            model_responses = model_usage.model_response_count,
            network_requests = model_usage.network_request_count,
            total_tokens = model_usage.total_tokens,
            alignment = if core_aligned { "aligned" } else { "drifted" },
        );

        Ok(WorkspaceUsageSummary {
            workspace_id: workspace_id.to_string(),
            generated_at_ms: now_ms(),
            run_count: usage_runs.len(),
            event_count,
            tool_call_count,
            artifact_count,
            knowledge_source_count,
            task_count,
            active_task_count,
            model_usage,
            core_aligned,
            flow_path,
            primitive_path,
            runs: usage_runs,
            summary,
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
        let target_existed = target.exists();
        let mut backup_artifact = None;

        if !dry_run {
            let backup_path = self.write_proposal_backup_artifact(
                &artifact.run_id,
                &artifact.artifact_id,
                &current_text,
            )?;
            backup_artifact = Some(self.store.add_artifact(
                &artifact.run_id,
                &artifact.workspace_id,
                "code_change_backup",
                backup_path,
            )?);
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
            backup_artifact,
            target_path: target.display().to_string(),
            target_existed,
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

    pub fn rollback_code_change_proposal(
        &self,
        artifact_id: &str,
    ) -> Result<ProposalRollbackResult, String> {
        let artifact = self.artifact(artifact_id)?;
        if artifact.kind != "code_change_proposal" {
            return Err(format!(
                "artifact {} is {}, not code_change_proposal",
                artifact.artifact_id, artifact.kind
            ));
        }
        let apply_result = self.latest_applied_proposal_event(&artifact)?;
        let backup_artifact = apply_result.backup_artifact.clone().ok_or_else(|| {
            "latest applied proposal did not record a rollback backup artifact".to_string()
        })?;
        let target = PathBuf::from(&apply_result.target_path);
        let backup_text = fs::read_to_string(&backup_artifact.path)
            .map_err(|err| format!("failed to read proposal rollback backup: {err}"))?;

        if apply_result.target_existed {
            if let Some(parent) = target.parent() {
                fs::create_dir_all(parent)
                    .map_err(|err| format!("failed to create rollback target directory: {err}"))?;
            }
            fs::write(&target, &backup_text)
                .map_err(|err| format!("failed to restore proposal target: {err}"))?;
        } else if target.exists() {
            fs::remove_file(&target)
                .map_err(|err| format!("failed to remove newly-created proposal target: {err}"))?;
        }

        let result = ProposalRollbackResult {
            artifact,
            backup_artifact,
            target_path: target.display().to_string(),
            restored: true,
            target_existed: apply_result.target_existed,
            bytes_written: if apply_result.target_existed {
                backup_text.len() as u64
            } else {
                0
            },
            preview: backup_text.chars().take(4000).collect(),
        };
        self.store.append_event(
            &result.artifact.workspace_id,
            Some(&result.artifact.run_id),
            RunEventKind::CodeChangeReverted,
            &result,
        )?;
        Ok(result)
    }

    pub fn review_code_change_proposal(&self, artifact_id: &str) -> Result<ProposalReview, String> {
        let artifact = self.artifact(artifact_id)?;
        if artifact.kind != "code_change_proposal" {
            return Err(format!(
                "artifact {} is {}, not code_change_proposal",
                artifact.artifact_id, artifact.kind
            ));
        }
        let proposal = fs::read_to_string(&artifact.path)
            .map_err(|err| format!("failed to read proposal artifact: {err}"))?;
        let review = review_code_change_proposal(&self.repo_root, artifact, &proposal);
        self.store.append_event(
            &review.artifact.workspace_id,
            Some(&review.artifact.run_id),
            RunEventKind::CodeChangeReviewed,
            &serde_json::json!({
                "review": review.clone(),
                "approval_gate": "explicit_dry_run_or_apply_required",
            }),
        )?;
        Ok(review)
    }

    pub fn run_by_id(&self, run_id: &str) -> Result<RunSummary, String> {
        self.store.run_by_id(run_id)
    }

    pub fn run_events(&self, run_id: &str) -> Result<Vec<crate::types::LocalEvent>, String> {
        self.store.run_events(run_id)
    }

    pub fn run_event_gc_preview(
        &self,
        run_id: &str,
        retain_last: Option<usize>,
    ) -> Result<EventGcPreview, String> {
        let run = self.run_by_id(run_id)?;
        let events = self.run_events(run_id)?;
        let retain_last = retain_last
            .unwrap_or(DEFAULT_EVENT_GC_RETAIN_LAST)
            .clamp(1, 1_000);
        let split_at = events.len().saturating_sub(retain_last);
        let filtered_events = events[..split_at].to_vec();
        let retained_events = events[split_at..].to_vec();
        let summary = event_gc_summary_from_parts(retain_last, &retained_events, &filtered_events);
        Ok(EventGcPreview {
            run,
            summary,
            retained_events,
            filtered_events,
        })
    }

    pub fn run_tool_trace(&self, run_id: &str) -> Result<Vec<LocalToolTraceEntry>, String> {
        let events = self.run_events(run_id)?;
        Ok(tool_trace_from_events(&events))
    }

    pub fn run_core_trace(&self, run_id: &str) -> Result<LocalRunCoreTrace, String> {
        let run = self.run_by_id(run_id)?;
        let events = self.run_events(run_id)?;
        let core_trace = build_core_execution_trace(&events)?;
        Ok(core_trace_from_events(run, &events, core_trace))
    }

    pub fn run_plan(&self, run_id: &str) -> Result<LocalRunPlan, String> {
        let run = self.run_by_id(run_id)?;
        let events = self.run_events(run_id)?;
        Ok(run_plan_from_events(run, &events))
    }

    pub fn run_review(&self, run_id: &str) -> Result<LocalRunReview, String> {
        let evidence = self.run_evidence_summary(run_id)?;
        let core_trace = self.run_core_trace(run_id)?;
        let tool_trace = self.run_tool_trace(run_id)?;
        let proposal_artifact = evidence
            .artifacts
            .iter()
            .find(|artifact| artifact.kind == "code_change_proposal")
            .cloned();
        let response_artifact = evidence
            .artifacts
            .iter()
            .find(|artifact| artifact.kind == "assistant_response")
            .cloned()
            .or_else(|| {
                evidence
                    .artifacts
                    .iter()
                    .find(|artifact| artifact.path.ends_with("/response.md"))
                    .cloned()
            });
        let failed_tool_call_count = tool_trace
            .iter()
            .filter(|entry| entry.success == Some(false))
            .count();
        let next_actions = run_review_next_actions(
            &evidence,
            proposal_artifact.as_ref(),
            response_artifact.as_ref(),
            failed_tool_call_count,
        );

        Ok(LocalRunReview {
            run: evidence.run.clone(),
            status: evidence.run.status.clone(),
            core_aligned: core_trace.core_aligned,
            flow_path: core_trace.flow_path,
            primitive_path: core_trace.primitive_path,
            event_count: evidence.event_count,
            tool_call_count: evidence.tool_call_count,
            failed_tool_call_count,
            model_usage: evidence.model_usage,
            proposal_artifact,
            response_artifact,
            final_response_chars: evidence.final_response_chars,
            next_actions,
        })
    }

    pub fn run_status_snapshot(&self, run_id: &str) -> Result<LocalRunStatusSnapshot, String> {
        let evidence = self.run_evidence_summary(run_id)?;
        let core_trace = self.run_core_trace(run_id)?;
        let tool_trace = self.run_tool_trace(run_id)?;
        let latest_event =
            self.run_events(run_id)?
                .into_iter()
                .last()
                .map(|event| LocalRunStatusEvent {
                    sequence: event.sequence,
                    kind: event.kind.clone(),
                    canonical_flow_id: event.canonical_flow_id.clone(),
                    primitive_id: event.primitive_id.clone(),
                    summary: event_payload_summary(&event),
                    created_at_ms: event.created_at_ms,
                });
        let proposal_artifact = evidence
            .artifacts
            .iter()
            .find(|artifact| artifact.kind == "code_change_proposal")
            .cloned();
        let response_artifact = evidence
            .artifacts
            .iter()
            .find(|artifact| artifact.kind == "assistant_response")
            .cloned()
            .or_else(|| {
                evidence
                    .artifacts
                    .iter()
                    .find(|artifact| artifact.path.ends_with("/response.md"))
                    .cloned()
            });
        let failed_tool_call_count = tool_trace
            .iter()
            .filter(|entry| entry.success == Some(false))
            .count();
        let pending_tool_call_count = tool_trace
            .iter()
            .filter(|entry| entry.success.is_none())
            .count();
        let latest_error = latest_run_error(&evidence.run, &tool_trace, latest_event.as_ref());
        let terminal = matches!(
            evidence.run.status.as_str(),
            status if status == RunStatus::Finished.as_str() || status == RunStatus::Failed.as_str()
        );
        let next_actions = run_status_next_actions(
            &evidence,
            proposal_artifact.as_ref(),
            response_artifact.as_ref(),
            failed_tool_call_count,
            pending_tool_call_count,
            latest_error.as_deref(),
        );

        Ok(LocalRunStatusSnapshot {
            run: evidence.run.clone(),
            generated_at_ms: now_ms(),
            terminal,
            core_aligned: core_trace.core_aligned,
            event_count: evidence.event_count,
            latest_event,
            model_usage: evidence.model_usage,
            tool_call_count: evidence.tool_call_count,
            failed_tool_call_count,
            pending_tool_call_count,
            artifact_count: evidence.artifacts.len(),
            response_artifact,
            proposal_artifact,
            latest_error,
            flow_path: core_trace.flow_path,
            primitive_path: core_trace.primitive_path,
            next_actions,
        })
    }

    pub fn run_compact(&self, run_id: &str) -> Result<LocalRunCompact, String> {
        let transcript = self.run_transcript(run_id)?;
        Ok(run_compact_from_transcript(&transcript))
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
        let mut source_ratings = Vec::new();
        let mut checkpoints = Vec::new();
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
                "source_rated" => {
                    if let Ok(rating) =
                        serde_json::from_value::<SourceRating>(event.payload.clone())
                    {
                        source_ratings.push(rating);
                    }
                }
                "run_checkpoint_recorded" => {
                    if let Ok(checkpoint) =
                        serde_json::from_value::<RunCheckpoint>(event.payload.clone())
                    {
                        checkpoints.push(checkpoint);
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
        let event_gc = event_gc_summary_for_events(&events, DEFAULT_EVENT_GC_RETAIN_LAST);
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
            source_ratings,
            checkpoints,
            event_gc,
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

    pub fn workspace_continuation_attempt(
        &self,
        request: WorkspaceContinuationRequest,
    ) -> Result<RunAttempt, String> {
        let compact = self.workspace_compact(request.workspace_id.as_deref(), request.limit)?;
        let prompt =
            build_workspace_continuation_prompt(&compact, request.extra_instruction.as_deref());
        self.run_prompt_attempt(RunRequest {
            prompt,
            workspace_id: Some(compact.workspace_id),
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

    fn write_proposal_backup_artifact(
        &self,
        run_id: &str,
        proposal_artifact_id: &str,
        current_text: &str,
    ) -> Result<PathBuf, String> {
        let artifact_dir = self.runtime_dir.join("artifacts").join(run_id);
        fs::create_dir_all(&artifact_dir)
            .map_err(|err| format!("failed to create proposal backup directory: {err}"))?;
        let path = artifact_dir.join(format!("{proposal_artifact_id}.rollback.txt"));
        fs::write(&path, current_text)
            .map_err(|err| format!("failed to write proposal rollback backup: {err}"))?;
        Ok(path)
    }

    fn latest_applied_proposal_event(
        &self,
        artifact: &ArtifactRecord,
    ) -> Result<ProposalApplyResult, String> {
        self.run_events(&artifact.run_id)?
            .into_iter()
            .rev()
            .filter(|event| event.kind == RunEventKind::CodeChangeApplied.as_str())
            .filter_map(|event| serde_json::from_value::<ProposalApplyResult>(event.payload).ok())
            .find(|result| {
                result.applied
                    && !result.dry_run
                    && result.artifact.artifact_id == artifact.artifact_id
            })
            .ok_or_else(|| {
                format!(
                    "no applied code-change event found for proposal {}",
                    artifact.artifact_id
                )
            })
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
                "source_ratings": context.source_ratings.len(),
                "knowledge_rating_policy": "latest_source_rated_priority",
                "task_count": context.tasks.len(),
                "active_tasks": context.tasks.iter().filter(|task| task_is_active(task)).count(),
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
            tasks: context.tasks,
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
        let source_ratings = self.workspace_source_ratings(workspace_id)?;
        let source_rating_priorities = source_rating_priorities(&source_ratings);
        let mut knowledge_sources = self
            .store
            .list_knowledge_sources(workspace_id, limit.saturating_mul(4).max(limit))?;
        prioritize_knowledge_sources(&mut knowledge_sources, &source_rating_priorities);
        knowledge_sources.truncate(limit);
        let tasks = self.store.list_tasks(Some(workspace_id), None, limit)?;
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
            source_ratings,
            tasks,
            recent_turns,
            context_replay_limit: limit,
        })
    }

    fn workspace_source_ratings(&self, workspace_id: &str) -> Result<Vec<SourceRating>, String> {
        let mut ratings_by_source: HashMap<String, (SourceRating, i64)> = HashMap::new();
        for event in self.store.workspace_events(workspace_id, 512)? {
            if event.kind != RunEventKind::SourceRated.as_str() {
                continue;
            }
            let Ok(rating) = serde_json::from_value::<SourceRating>(event.payload.clone()) else {
                continue;
            };
            if rating.workspace_id != workspace_id {
                continue;
            }
            ratings_by_source.insert(rating.source_id.clone(), (rating, event.sequence));
        }
        let mut ratings = ratings_by_source
            .into_values()
            .collect::<Vec<(SourceRating, i64)>>();
        ratings.sort_by(|(left, left_sequence), (right, right_sequence)| {
            right
                .rating
                .cmp(&left.rating)
                .then_with(|| right_sequence.cmp(left_sequence))
                .then_with(|| right.created_at_ms.cmp(&left.created_at_ms))
        });
        Ok(ratings.into_iter().map(|(rating, _)| rating).collect())
    }
}

fn source_rating_priorities(ratings: &[SourceRating]) -> HashMap<String, SourceRatingPriority> {
    ratings
        .iter()
        .enumerate()
        .map(|(index, rating)| {
            (
                rating.source_id.clone(),
                SourceRatingPriority {
                    rating: rating.rating,
                    sequence: (ratings.len() - index) as i64,
                },
            )
        })
        .collect()
}

fn prioritize_knowledge_sources(
    sources: &mut [KnowledgeSource],
    ratings: &HashMap<String, SourceRatingPriority>,
) {
    sources.sort_by(|left, right| {
        let left_rating = ratings.get(&left.source_id);
        let right_rating = ratings.get(&right.source_id);
        knowledge_rating_score(right_rating)
            .cmp(&knowledge_rating_score(left_rating))
            .then_with(|| {
                right_rating
                    .map(|rating| rating.sequence)
                    .unwrap_or_default()
                    .cmp(
                        &left_rating
                            .map(|rating| rating.sequence)
                            .unwrap_or_default(),
                    )
            })
            .then_with(|| right.added_at_ms.cmp(&left.added_at_ms))
            .then_with(|| right.source_id.cmp(&left.source_id))
    });
}

fn knowledge_rating_score(priority: Option<&SourceRatingPriority>) -> i16 {
    priority
        .map(|rating| i16::from(rating.rating) - 3)
        .unwrap_or_default()
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

fn normalize_task_priority(priority: &str) -> Result<&'static str, String> {
    match priority.trim().to_ascii_lowercase().as_str() {
        "" | "normal" | "medium" => Ok("normal"),
        "low" => Ok("low"),
        "high" => Ok("high"),
        "urgent" => Ok("urgent"),
        other => Err(format!(
            "unknown task priority {other}; expected low, normal, high, or urgent"
        )),
    }
}

fn normalize_task_status(status: &str) -> Result<&'static str, String> {
    match status.trim().to_ascii_lowercase().as_str() {
        "todo" | "open" | "pending" => Ok("todo"),
        "doing" | "in_progress" | "running" => Ok("in_progress"),
        "done" | "complete" | "completed" => Ok("done"),
        "cancelled" | "canceled" | "cancel" => Ok("cancelled"),
        other => Err(format!(
            "unknown task status {other}; expected todo, in_progress, done, or cancelled"
        )),
    }
}

fn task_is_active(task: &LocalTaskRecord) -> bool {
    matches!(task.status.as_str(), "todo" | "in_progress")
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

fn event_gc_summary_for_events(
    events: &[crate::types::LocalEvent],
    retain_last: usize,
) -> EventGcSummary {
    let retain_last = retain_last.max(1);
    let split_at = events.len().saturating_sub(retain_last);
    event_gc_summary_from_parts(retain_last, &events[split_at..], &events[..split_at])
}

fn event_gc_summary_from_parts(
    retain_last: usize,
    retained_events: &[crate::types::LocalEvent],
    filtered_events: &[crate::types::LocalEvent],
) -> EventGcSummary {
    EventGcSummary {
        policy_id: "retain_last_n_events".to_string(),
        retain_last,
        retained_event_count: retained_events.len(),
        filtered_event_count: filtered_events.len(),
        retained_sequences: retained_events.iter().map(|event| event.sequence).collect(),
        filtered_sequences: filtered_events.iter().map(|event| event.sequence).collect(),
    }
}

fn run_review_next_actions(
    evidence: &RunEvidenceSummary,
    proposal_artifact: Option<&ArtifactRecord>,
    response_artifact: Option<&ArtifactRecord>,
    failed_tool_call_count: usize,
) -> Vec<String> {
    let mut actions = Vec::new();
    if !evidence.core_trace.core_aligned {
        actions.push("Inspect Core trace drift before trusting this run.".to_string());
    }
    if failed_tool_call_count > 0 {
        actions.push(format!(
            "Inspect {failed_tool_call_count} failed tool call(s) with /tools."
        ));
    }
    if let Some(proposal) = proposal_artifact {
        actions.push(format!(
            "Review proposal {} with /risk, then dry-run before apply.",
            proposal.artifact_id
        ));
    }
    if evidence.run.status == RunStatus::Failed.as_str() {
        actions.push("Continue from this failed run after inspecting events.".to_string());
    } else if proposal_artifact.is_none() {
        actions.push("Continue the run if more inspection or edits are needed.".to_string());
    }
    if response_artifact.is_some() {
        actions.push("Open the response artifact for reproducible evidence.".to_string());
    }
    if actions.is_empty() {
        actions.push("No immediate follow-up action required.".to_string());
    }
    actions
}

fn latest_run_error(
    run: &RunSummary,
    tool_trace: &[LocalToolTraceEntry],
    latest_event: Option<&LocalRunStatusEvent>,
) -> Option<String> {
    if run.status == RunStatus::Failed.as_str() {
        return run
            .final_response
            .as_ref()
            .map(|error| compact_summary(error, 240));
    }
    if latest_event.is_some_and(|event| event.kind == RunEventKind::RunFailed.as_str()) {
        return latest_event.map(|event| event.summary.clone());
    }
    tool_trace
        .iter()
        .rev()
        .find_map(|entry| entry.error.as_ref())
        .map(|error| compact_summary(error, 240))
}

fn run_status_next_actions(
    evidence: &RunEvidenceSummary,
    proposal_artifact: Option<&ArtifactRecord>,
    response_artifact: Option<&ArtifactRecord>,
    failed_tool_call_count: usize,
    pending_tool_call_count: usize,
    latest_error: Option<&str>,
) -> Vec<String> {
    let mut actions = Vec::new();
    if !evidence.core_trace.core_aligned {
        actions.push(
            "Inspect the Core trace because this run drifted from the Structure manifest."
                .to_string(),
        );
    }
    if pending_tool_call_count > 0 {
        actions.push(format!(
            "Watch the event feed; {pending_tool_call_count} tool call(s) are still pending."
        ));
    }
    if failed_tool_call_count > 0 {
        actions.push(format!(
            "Inspect {failed_tool_call_count} failed tool call(s), then retry or continue with extra instruction."
        ));
    }
    if evidence.run.status == RunStatus::Failed.as_str() {
        let suffix = latest_error
            .map(|error| format!(" Latest error: {error}"))
            .unwrap_or_default();
        actions.push(format!(
            "Retry or continue from this failed run after reviewing events.{suffix}"
        ));
    } else if evidence.run.status == RunStatus::Finished.as_str() {
        if let Some(proposal) = proposal_artifact {
            actions.push(format!(
                "Review proposal {} with risk checks before dry-run or apply.",
                proposal.artifact_id
            ));
        } else {
            actions.push(
                "Continue this run or the whole workspace session if more work is needed."
                    .to_string(),
            );
        }
    } else {
        actions.push("Refresh status or follow the workspace event feed until the run reaches a terminal state.".to_string());
    }
    if response_artifact.is_some() {
        actions.push(
            "Open the response artifact when you need reproducible output evidence.".to_string(),
        );
    }
    actions
}

fn workspace_compact_from_replay(
    replay: WorkspaceReplay,
    recent_runs: Vec<WorkspaceCompactRun>,
) -> WorkspaceCompact {
    let core_trace =
        build_core_execution_trace(&replay.events).unwrap_or_else(|_| CoreExecutionTrace {
            manifest_schema_version: "unknown".to_string(),
            event_count: replay.events.len(),
            flow_ids: Vec::new(),
            primitive_ids: Vec::new(),
            invalid_flow_ids: Vec::new(),
            invalid_primitive_ids: Vec::new(),
            core_aligned: false,
        });
    let latest_run = recent_runs.iter().max_by_key(|run| run.updated_at_ms);
    let failed_runs = recent_runs
        .iter()
        .filter(|run| run.status == RunStatus::Failed.as_str())
        .count();
    let proposal_artifacts = replay
        .artifacts
        .iter()
        .filter(|artifact| artifact.kind == "code_change_proposal")
        .count();
    let active_tasks = replay
        .tasks
        .iter()
        .filter(|task| task_is_active(task))
        .collect::<Vec<_>>();

    let mut carry_forward_items = Vec::new();
    if let Some(run) = latest_run {
        carry_forward_items.push(format!(
            "Latest run: {} / {} / {}",
            run.run_id, run.status, run.prompt_summary
        ));
        if let Some(response) = &run.response_summary {
            carry_forward_items.push(format!("Latest response: {response}"));
        }
    }
    if !recent_runs.is_empty() {
        carry_forward_items.push(format!(
            "Recent runs: {}",
            recent_runs
                .iter()
                .map(|run| format!("{}({})", run.run_id, run.status))
                .collect::<Vec<_>>()
                .join(", ")
        ));
    }
    if !replay.knowledge_sources.is_empty() {
        carry_forward_items.push(format!(
            "Knowledge sources: {}",
            replay
                .knowledge_sources
                .iter()
                .map(|source| source.path.as_str())
                .collect::<Vec<_>>()
                .join(", ")
        ));
    }
    if !active_tasks.is_empty() {
        carry_forward_items.push(format!(
            "Active tasks: {}",
            active_tasks
                .iter()
                .map(|task| format!("{}({}/{})", task.title, task.status, task.priority))
                .collect::<Vec<_>>()
                .join(", ")
        ));
    }
    let checkpoints = replay
        .events
        .iter()
        .filter(|event| event.kind == RunEventKind::RunCheckpointRecorded.as_str())
        .filter_map(|event| serde_json::from_value::<RunCheckpoint>(event.payload.clone()).ok())
        .collect::<Vec<_>>();
    if !checkpoints.is_empty() {
        carry_forward_items.push(format!(
            "Human checkpoints: {}",
            checkpoints
                .iter()
                .rev()
                .take(8)
                .map(|checkpoint| {
                    format!(
                        "{}: {}",
                        checkpoint.run_id,
                        one_line_compact(&checkpoint.note, 180)
                    )
                })
                .collect::<Vec<_>>()
                .join(" | ")
        ));
    }
    if !replay.artifacts.is_empty() {
        carry_forward_items.push(format!(
            "Artifacts: {}",
            replay
                .artifacts
                .iter()
                .map(|artifact| artifact.path.as_str())
                .collect::<Vec<_>>()
                .join(", ")
        ));
    }
    carry_forward_items.push(format!(
        "Core path: {}",
        if core_trace.flow_ids.is_empty() {
            "none".to_string()
        } else {
            core_trace.flow_ids.join(" -> ")
        }
    ));
    carry_forward_items.push(format!(
        "Primitive path: {}",
        if core_trace.primitive_ids.is_empty() {
            "none".to_string()
        } else {
            core_trace.primitive_ids.join(" -> ")
        }
    ));
    if let Some(sequence) = replay.last_sequence {
        carry_forward_items.push(format!("Workspace event cursor: #{sequence}"));
    }

    let mut next_actions = Vec::new();
    if let Some(run) = latest_run {
        next_actions.push(format!(
            "Continue from latest run {} when the next user request depends on this session.",
            run.run_id
        ));
    } else {
        next_actions
            .push("Start a new local chat or code-agent run in this workspace.".to_string());
    }
    if failed_runs > 0 {
        next_actions.push(format!(
            "Inspect {failed_runs} failed run(s) before using this compact for handoff."
        ));
    }
    if proposal_artifacts > 0 {
        next_actions.push(format!(
            "Review {proposal_artifacts} code-change proposal artifact(s) with risk checks before apply."
        ));
    }
    if !active_tasks.is_empty() {
        next_actions.push(format!(
            "Resolve or continue {} active workspace task(s) before closing this session.",
            active_tasks.len()
        ));
    }
    if replay.knowledge_sources.is_empty() {
        next_actions.push(
            "Add workspace knowledge if future turns need persistent local context.".to_string(),
        );
    }
    if !core_trace.core_aligned {
        next_actions
            .push("Inspect Core trace drift before trusting this workspace compact.".to_string());
    }

    let summary = format!(
        "Workspace {} has {} events, {} recent runs, {} knowledge sources, {} tasks ({} active), and {} artifacts. Core alignment is {}.",
        replay.workspace_id,
        replay.events.len(),
        recent_runs.len(),
        replay.knowledge_sources.len(),
        replay.tasks.len(),
        active_tasks.len(),
        replay.artifacts.len(),
        if core_trace.core_aligned { "aligned" } else { "drifted" },
    );
    let continuation_context = workspace_continuation_context(
        &replay,
        &recent_runs,
        &summary,
        &carry_forward_items,
        &next_actions,
        &core_trace,
    );

    WorkspaceCompact {
        workspace_id: replay.workspace_id.clone(),
        generated_at_ms: now_ms(),
        event_count: replay.events.len(),
        run_count: recent_runs.len(),
        knowledge_source_count: replay.knowledge_sources.len(),
        task_count: replay.tasks.len(),
        active_task_count: active_tasks.len(),
        artifact_count: replay.artifacts.len(),
        last_sequence: replay.last_sequence,
        core_aligned: core_trace.core_aligned,
        flow_path: core_trace.flow_ids,
        primitive_path: core_trace.primitive_ids,
        recent_runs,
        summary,
        carry_forward_items,
        next_actions,
        continuation_context,
    }
}

fn workspace_continuation_context(
    replay: &WorkspaceReplay,
    recent_runs: &[WorkspaceCompactRun],
    summary: &str,
    carry_forward_items: &[String],
    next_actions: &[String],
    core_trace: &CoreExecutionTrace,
) -> String {
    let runs = if recent_runs.is_empty() {
        "- none".to_string()
    } else {
        recent_runs
            .iter()
            .map(|run| {
                format!(
                    "- {} / {} / events={} / tools={} / prompt={}",
                    run.run_id,
                    run.status,
                    run.event_count,
                    run.tool_call_count,
                    run.prompt_summary
                )
            })
            .collect::<Vec<_>>()
            .join("\n")
    };
    let carry = if carry_forward_items.is_empty() {
        "- No carry-forward items were derived.".to_string()
    } else {
        carry_forward_items
            .iter()
            .map(|item| format!("- {item}"))
            .collect::<Vec<_>>()
            .join("\n")
    };
    let actions = if next_actions.is_empty() {
        "- No immediate follow-up action required.".to_string()
    } else {
        next_actions
            .iter()
            .map(|action| format!("- {action}"))
            .collect::<Vec<_>>()
            .join("\n")
    };
    let active_tasks = replay
        .tasks
        .iter()
        .filter(|task| task_is_active(task))
        .map(|task| {
            format!(
                "- {} [{} / {}] {} (run={})",
                task.task_id,
                task.status,
                task.priority,
                task.title,
                task.run_id.as_deref().unwrap_or("workspace")
            )
        })
        .collect::<Vec<_>>()
        .join("\n");

    format!(
        "Compact Structure workspace context for `{workspace_id}`.\n\n\
Summary: {summary}\n\
Events: {events}\n\
Runs: {runs_count}\n\
Knowledge sources: {knowledge_count}\n\
Tasks: {task_count} ({active_task_count} active)\n\
Artifacts: {artifact_count}\n\
Last event sequence: {last_sequence}\n\
Core aligned: {core_aligned}\n\
Flow path: {flow_path}\n\
Primitive path: {primitive_path}\n\n\
Recent runs:\n{runs}\n\n\
Active tasks:\n{tasks}\n\n\
Carry forward:\n{carry}\n\n\
Next actions:\n{actions}\n\n\
Use this compact as a session handoff boundary for local chat/code-agent work. Preserve Structure Core event, path, disclosure, source-evaluation, GC, and evidence concepts when continuing.",
        workspace_id = replay.workspace_id,
        events = replay.events.len(),
        runs_count = recent_runs.len(),
        knowledge_count = replay.knowledge_sources.len(),
        task_count = replay.tasks.len(),
        active_task_count = replay.tasks.iter().filter(|task| task_is_active(task)).count(),
        artifact_count = replay.artifacts.len(),
        last_sequence = replay
            .last_sequence
            .map(|value| value.to_string())
            .unwrap_or_else(|| "none".to_string()),
        core_aligned = core_trace.core_aligned,
        flow_path = if core_trace.flow_ids.is_empty() {
            "none".to_string()
        } else {
            core_trace.flow_ids.join(" -> ")
        },
        primitive_path = if core_trace.primitive_ids.is_empty() {
            "none".to_string()
        } else {
            core_trace.primitive_ids.join(" -> ")
        },
        tasks = if active_tasks.is_empty() {
            "- none".to_string()
        } else {
            active_tasks
        },
    )
}

fn add_model_usage(total: &mut ModelUsageSummary, usage: &ModelUsageSummary) {
    total.model_request_count += usage.model_request_count;
    total.model_response_count += usage.model_response_count;
    total.network_request_count += usage.network_request_count;
    total.prompt_tokens += usage.prompt_tokens;
    total.completion_tokens += usage.completion_tokens;
    total.total_tokens += usage.total_tokens;
    total.response_chars += usage.response_chars;
}

fn extend_unique(target: &mut Vec<String>, values: &[String]) {
    for value in values {
        if !target.contains(value) {
            target.push(value.clone());
        }
    }
}

fn run_compact_from_transcript(transcript: &RunTranscript) -> LocalRunCompact {
    let evidence = &transcript.evidence;
    let proposal_artifact = evidence
        .artifacts
        .iter()
        .find(|artifact| artifact.kind == "code_change_proposal");
    let response_artifact = evidence
        .artifacts
        .iter()
        .find(|artifact| artifact.kind == "assistant_response")
        .or_else(|| {
            evidence
                .artifacts
                .iter()
                .find(|artifact| artifact.path.ends_with("/response.md"))
        });
    let failed_tool_call_count = transcript
        .events
        .iter()
        .filter(|event| {
            event.kind == "tool_call_completed"
                && event
                    .payload
                    .get("success")
                    .and_then(serde_json::Value::as_bool)
                    == Some(false)
        })
        .count();
    let assistant_message = transcript
        .chat_turn
        .as_ref()
        .and_then(|turn| turn.assistant_message.as_deref())
        .or(transcript.final_response.as_deref())
        .unwrap_or("No assistant response was recorded.");
    let assistant_excerpt = continuation_response_excerpt(assistant_message);
    let mut carry_forward_items = Vec::new();
    carry_forward_items.push(format!(
        "Previous prompt: {}",
        one_line_compact(&transcript.run.prompt, 320)
    ));
    carry_forward_items.push(format!(
        "Previous assistant outcome: {}",
        one_line_compact(&assistant_excerpt, 520)
    ));
    carry_forward_items.push(format!(
        "Core path: {}",
        if evidence.core_trace.flow_ids.is_empty() {
            "none".to_string()
        } else {
            evidence.core_trace.flow_ids.join(" -> ")
        }
    ));
    carry_forward_items.push(format!(
        "Primitive path: {}",
        if evidence.core_trace.primitive_ids.is_empty() {
            "none".to_string()
        } else {
            evidence.core_trace.primitive_ids.join(" -> ")
        }
    ));
    if !evidence.prompt_references.is_empty() {
        carry_forward_items.push(format!(
            "Prompt references: @{}",
            evidence.prompt_references.join(", @")
        ));
    }
    if !evidence.knowledge_sources.is_empty() {
        carry_forward_items.push(format!(
            "Knowledge sources: {}",
            evidence
                .knowledge_sources
                .iter()
                .map(|source| source.path.as_str())
                .collect::<Vec<_>>()
                .join(", ")
        ));
    }
    if !evidence.checkpoints.is_empty() {
        carry_forward_items.push(format!(
            "Human checkpoints: {}",
            evidence
                .checkpoints
                .iter()
                .map(|checkpoint| one_line_compact(&checkpoint.note, 220))
                .collect::<Vec<_>>()
                .join(" | ")
        ));
    }
    if let Some(worktree) = &evidence.worktree {
        carry_forward_items.push(format!(
            "Worktree: {} on {} with {} changed file(s)",
            if worktree.clean { "clean" } else { "dirty" },
            worktree.branch.as_deref().unwrap_or("unknown branch"),
            worktree.changed_files.len()
        ));
    }
    if !evidence.artifact_paths.is_empty() {
        carry_forward_items.push(format!("Artifacts: {}", evidence.artifact_paths.join(", ")));
    }

    let summary = format!(
        "Run {run_id} in workspace {workspace_id} is {status}. It recorded {events} events, {tools} completed tool calls, {models} model responses, and {tokens} total tokens. Core alignment is {core}.",
        run_id = transcript.run.run_id,
        workspace_id = transcript.run.workspace_id,
        status = transcript.run.status,
        events = evidence.event_count,
        tools = evidence.tool_call_count,
        models = evidence.model_usage.model_response_count,
        tokens = evidence.model_usage.total_tokens,
        core = if evidence.core_trace.core_aligned {
            "aligned"
        } else {
            "drifted"
        },
    );
    let next_actions = run_review_next_actions(
        evidence,
        proposal_artifact,
        response_artifact,
        failed_tool_call_count,
    );
    let continuation_context =
        build_compact_continuation_context(transcript, &assistant_excerpt, &carry_forward_items);

    LocalRunCompact {
        run: transcript.run.clone(),
        status: transcript.run.status.clone(),
        core_aligned: evidence.core_trace.core_aligned,
        event_count: evidence.event_count,
        tool_call_count: evidence.tool_call_count,
        model_usage: evidence.model_usage.clone(),
        artifact_paths: evidence.artifact_paths.clone(),
        summary,
        carry_forward_items,
        next_actions,
        continuation_context,
    }
}

fn core_trace_from_events(
    run: RunSummary,
    events: &[crate::types::LocalEvent],
    core_trace: CoreExecutionTrace,
) -> LocalRunCoreTrace {
    let mut flow_path = Vec::new();
    let mut primitive_path = Vec::new();
    let steps = events
        .iter()
        .map(|event| {
            push_changed_string(&mut flow_path, &event.canonical_flow_id);
            push_changed_string(&mut primitive_path, &event.primitive_id);
            LocalRunCoreTraceStep {
                sequence: event.sequence,
                kind: event.kind.clone(),
                canonical_flow_id: event.canonical_flow_id.clone(),
                primitive_id: event.primitive_id.clone(),
                payload_summary: event_payload_summary(event),
            }
        })
        .collect::<Vec<_>>();

    LocalRunCoreTrace {
        run,
        manifest_schema_version: core_trace.manifest_schema_version,
        core_aligned: core_trace.core_aligned,
        event_count: events.len(),
        flow_path,
        primitive_path,
        steps,
    }
}

fn push_changed_string(values: &mut Vec<String>, value: &str) {
    if values.last().is_none_or(|existing| existing != value) {
        values.push(value.to_string());
    }
}

fn run_plan_from_events(run: RunSummary, events: &[crate::types::LocalEvent]) -> LocalRunPlan {
    let mut model_request_count = 0usize;
    let mut tool_call_count = 0usize;
    let mut steps = Vec::new();

    for event in events {
        match event.kind.as_str() {
            "model_requested" => model_request_count += 1,
            "tool_call_requested" => tool_call_count += 1,
            "agent_step_planned" => {
                let planned_steps = event
                    .payload
                    .get("steps")
                    .and_then(serde_json::Value::as_array)
                    .map(|values| {
                        values
                            .iter()
                            .filter_map(serde_json::Value::as_str)
                            .map(humanize_plan_step)
                            .collect::<Vec<_>>()
                    })
                    .unwrap_or_default();
                let title = if planned_steps.is_empty() {
                    "Agent step planned".to_string()
                } else {
                    planned_steps.join(" -> ")
                };
                steps.push(LocalRunPlanStep {
                    sequence: event.sequence,
                    title,
                    status: "completed".to_string(),
                    canonical_flow_id: event.canonical_flow_id.clone(),
                    primitive_id: event.primitive_id.clone(),
                    iteration: event
                        .payload
                        .get("iteration")
                        .and_then(serde_json::Value::as_u64),
                    tool_call_count: event
                        .payload
                        .get("tool_call_count")
                        .and_then(serde_json::Value::as_u64)
                        .unwrap_or_default() as usize,
                    total_tool_results: event
                        .payload
                        .get("total_tool_results")
                        .and_then(serde_json::Value::as_u64)
                        .unwrap_or_default() as usize,
                    prompt_references: event
                        .payload
                        .get("prompt_references")
                        .and_then(serde_json::Value::as_array)
                        .map(|values| {
                            values
                                .iter()
                                .filter_map(serde_json::Value::as_str)
                                .map(str::to_string)
                                .collect::<Vec<_>>()
                        })
                        .unwrap_or_default(),
                });
            }
            _ => {}
        }
    }

    if steps.is_empty() {
        steps.push(LocalRunPlanStep {
            sequence: events
                .first()
                .map(|event| event.sequence)
                .unwrap_or_default(),
            title: "Open workspace -> record prompt -> synthesize response".to_string(),
            status: if run.status == RunStatus::Failed.as_str() {
                "failed".to_string()
            } else {
                "completed".to_string()
            },
            canonical_flow_id: "event".to_string(),
            primitive_id: "event_audit".to_string(),
            iteration: None,
            tool_call_count,
            total_tool_results: tool_call_count,
            prompt_references: Vec::new(),
        });
    }

    LocalRunPlan {
        status: run.status.clone(),
        step_count: steps.len(),
        completed_step_count: steps
            .iter()
            .filter(|step| step.status == "completed")
            .count(),
        model_request_count,
        tool_call_count,
        run,
        steps,
    }
}

fn humanize_plan_step(step: &str) -> String {
    step.split('_')
        .filter(|part| !part.is_empty())
        .map(|part| {
            let mut chars = part.chars();
            match chars.next() {
                Some(first) => format!("{}{}", first.to_ascii_uppercase(), chars.as_str()),
                None => String::new(),
            }
        })
        .collect::<Vec<_>>()
        .join(" ")
}

fn event_payload_summary(event: &crate::types::LocalEvent) -> String {
    match event.kind.as_str() {
        "workspace_opened" => format!("workspace {}", event.workspace_id),
        "task_created" => format!(
            "task {} {}",
            json_str(&event.payload, "task_id", "task"),
            json_str(&event.payload, "title", "created")
        ),
        "task_updated" => {
            let task = event.payload.get("task").unwrap_or(&event.payload);
            format!(
                "task {} {}",
                json_str(task, "task_id", "task"),
                json_str(task, "status", "updated")
            )
        }
        "run_created" | "prompt_received" => event
            .payload
            .get("prompt")
            .and_then(|value| value.as_str())
            .map(|prompt| compact_summary(prompt, 96))
            .unwrap_or_else(|| "prompt recorded".to_string()),
        "chat_message_recorded" => event
            .payload
            .get("role")
            .and_then(|value| value.as_str())
            .unwrap_or("chat")
            .to_string(),
        "command_turn_recorded" => format!(
            "{} {} {}",
            json_str(&event.payload, "surface", "surface"),
            json_str(&event.payload, "input", "command"),
            json_str(&event.payload, "status", "ok")
        ),
        "workspace_context_loaded" => format!(
            "mode={} instructions={} knowledge={} turns={}",
            json_str(&event.payload, "mode", "unknown"),
            json_u64(&event.payload, "agent_instruction_count"),
            json_u64(&event.payload, "knowledge_sources"),
            json_u64(&event.payload, "recent_turns")
        ),
        "knowledge_retrieved" => format!(
            "{} knowledge source(s)",
            event
                .payload
                .get("sources")
                .and_then(|value| value.as_array())
                .map(|sources| sources.len())
                .unwrap_or_default()
        ),
        "agent_step_planned" => format!(
            "phase={} tool_calls={} refs={}",
            json_str(&event.payload, "phase", "planning"),
            json_u64(&event.payload, "tool_call_count"),
            event
                .payload
                .get("prompt_references")
                .and_then(|value| value.as_array())
                .map(|refs| refs.len())
                .unwrap_or_default()
        ),
        "model_requested" => format!(
            "phase={} provider={} model={}",
            json_str(&event.payload, "phase", "unknown"),
            json_str(&event.payload, "provider", "unknown"),
            json_str(&event.payload, "model", "unknown")
        ),
        "model_responded" => format!(
            "phase={} chars={}",
            json_str(&event.payload, "phase", "unknown"),
            json_u64(&event.payload, "response_chars")
        ),
        "tool_call_requested" => format!(
            "{} {}",
            json_str(&event.payload, "name", "tool"),
            json_str(&event.payload, "call_id", "")
        )
        .trim()
        .to_string(),
        "tool_call_completed" => format!(
            "{} {} {}",
            json_str(&event.payload, "name", "tool"),
            json_str(&event.payload, "call_id", ""),
            if event
                .payload
                .get("success")
                .and_then(|value| value.as_bool())
                .unwrap_or(false)
            {
                "ok"
            } else {
                "failed"
            }
        )
        .trim()
        .to_string(),
        "artifact_written" | "code_change_proposed" => {
            json_str(&event.payload, "path", "artifact").to_string()
        }
        "code_change_reviewed" => {
            let review = event.payload.get("review").unwrap_or(&event.payload);
            format!(
                "{} risk={} can_apply={}",
                json_str(review, "target_path", "proposal"),
                json_str(review, "risk_level", "unknown"),
                review
                    .get("can_apply")
                    .and_then(|value| value.as_bool())
                    .unwrap_or(false)
            )
        }
        "code_change_applied" => format!(
            "{} {}",
            json_str(&event.payload, "target_path", "proposal"),
            if event
                .payload
                .get("dry_run")
                .and_then(|value| value.as_bool())
                .unwrap_or(false)
            {
                "dry-run"
            } else {
                "applied"
            }
        ),
        "source_rated" => format!(
            "{} {}/5",
            json_str(&event.payload, "source_title", "source"),
            json_u64(&event.payload, "rating")
        ),
        "run_checkpoint_recorded" => format!(
            "checkpoint {}",
            event
                .payload
                .get("note")
                .and_then(|value| value.as_str())
                .map(|note| compact_summary(note, 96))
                .unwrap_or_else(|| "recorded".to_string())
        ),
        "run_finished" => "terminal finished".to_string(),
        "run_failed" => event
            .payload
            .get("error")
            .and_then(|value| value.as_str())
            .map(|error| compact_summary(error, 120))
            .unwrap_or_else(|| "terminal failed".to_string()),
        _ => compact_summary(&event.payload.to_string(), 120),
    }
}

fn json_str<'a>(value: &'a serde_json::Value, key: &str, fallback: &'a str) -> &'a str {
    value
        .get(key)
        .and_then(|value| value.as_str())
        .unwrap_or(fallback)
}

fn json_u64(value: &serde_json::Value, key: &str) -> u64 {
    value
        .get(key)
        .and_then(|value| value.as_u64())
        .unwrap_or_default()
}

fn compact_summary(value: &str, max_chars: usize) -> String {
    if value.chars().count() > max_chars {
        format!("{}...", value.chars().take(max_chars).collect::<String>())
    } else {
        value.to_string()
    }
}

fn tool_trace_from_events(events: &[crate::types::LocalEvent]) -> Vec<LocalToolTraceEntry> {
    let mut trace = Vec::<LocalToolTraceEntry>::new();
    for event in events {
        match event.kind.as_str() {
            "tool_call_requested" => {
                if let Ok(call) = serde_json::from_value::<LocalToolCall>(event.payload.clone()) {
                    trace.push(LocalToolTraceEntry {
                        call_id: call.call_id,
                        name: call.name,
                        requested_sequence: Some(event.sequence),
                        completed_sequence: None,
                        input: call.input,
                        success: None,
                        output: None,
                        error: None,
                    });
                }
            }
            "tool_call_completed" => {
                if let Ok(result) = serde_json::from_value::<LocalToolResult>(event.payload.clone())
                {
                    if let Some(entry) = trace
                        .iter_mut()
                        .rev()
                        .find(|entry| entry.call_id == result.call_id)
                    {
                        entry.completed_sequence = Some(event.sequence);
                        entry.success = Some(result.success);
                        entry.output = Some(result.output);
                        entry.error = result.error;
                    } else {
                        trace.push(LocalToolTraceEntry {
                            call_id: result.call_id,
                            name: result.name,
                            requested_sequence: None,
                            completed_sequence: Some(event.sequence),
                            input: serde_json::Value::Null,
                            success: Some(result.success),
                            output: Some(result.output),
                            error: result.error,
                        });
                    }
                }
            }
            _ => {}
        }
    }
    trace
}

const CONTINUATION_SNIPPET_MAX_CHARS: usize = 1_200;

fn build_continuation_prompt(
    transcript: &RunTranscript,
    extra_instruction: Option<&str>,
) -> String {
    let compact = run_compact_from_transcript(transcript);
    let extra_instruction = extra_instruction
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .unwrap_or("Continue the same task from the previous run using the persisted transcript, event evidence, workspace context, and current repository state.");

    format!(
        "Continue from Structure local run `{run_id}` in workspace `{workspace_id}` using the compacted Structure event context below.\n\n\
This compact context is the durable continuation boundary for the new run. It is derived from immutable Structure events, run evidence, artifacts, model usage, and Core trace metadata; do not treat it as an opaque chat buffer.\n\n\
{compact_context}\n\n\
Produce the next assistant response now. Treat the continuation instruction as the active user request for this new run.\n\n\
Continuation instruction:\n{extra_instruction}\n",
        run_id = transcript.run.run_id,
        workspace_id = transcript.run.workspace_id,
        compact_context = compact.continuation_context,
    )
}

fn build_workspace_continuation_prompt(
    compact: &WorkspaceCompact,
    extra_instruction: Option<&str>,
) -> String {
    let extra_instruction = extra_instruction
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .unwrap_or("Continue this workspace session using the compact workspace context, replayed events, recent run evidence, knowledge sources, artifacts, and current repository state.");

    format!(
        "Continue Structure workspace/session `{workspace_id}` using the compacted workspace event context below.\n\n\
This compact context is the durable session handoff boundary for the new run. It is derived from immutable Structure workspace events, recent run compacts, knowledge sources, artifacts, model usage, and Core trace metadata; do not treat it as an opaque chat buffer.\n\n\
{compact_context}\n\n\
Produce the next assistant response now. Treat the session continuation instruction as the active user request for this new run.\n\n\
Session continuation instruction:\n{extra_instruction}\n",
        workspace_id = compact.workspace_id,
        compact_context = compact.continuation_context,
    )
}

fn build_compact_continuation_context(
    transcript: &RunTranscript,
    assistant_excerpt: &str,
    carry_forward_items: &[String],
) -> String {
    let items = if carry_forward_items.is_empty() {
        "- No carry-forward items were derived.".to_string()
    } else {
        carry_forward_items
            .iter()
            .map(|item| format!("- {item}"))
            .collect::<Vec<_>>()
            .join("\n")
    };
    let artifacts = if transcript.evidence.artifact_paths.is_empty() {
        "none".to_string()
    } else {
        transcript.evidence.artifact_paths.join("\n")
    };

    format!(
        "Compact Structure context for continuing run `{run_id}`.\n\n\
Workspace: {workspace_id}\n\
Status: {status}\n\
Core aligned: {core_aligned}\n\
Events: {events}\n\
Tool calls: {tool_calls}\n\
Model requests: {model_requests}\n\
Total tokens: {tokens}\n\n\
Carry forward:\n{items}\n\n\
Previous assistant outcome:\n{assistant}\n\n\
Artifact evidence:\n{artifacts}\n\n\
Use this compact as the durable context boundary for the next local chat or code-agent step. Preserve Structure Core event, path, disclosure, and evidence concepts when continuing.",
        run_id = transcript.run.run_id,
        workspace_id = transcript.run.workspace_id,
        status = transcript.run.status,
        core_aligned = transcript.evidence.core_trace.core_aligned,
        events = transcript.evidence.event_count,
        tool_calls = transcript.evidence.tool_call_count,
        model_requests = transcript.evidence.model_usage.model_request_count,
        tokens = transcript.evidence.model_usage.total_tokens,
        assistant = truncate_for_prompt(assistant_excerpt, CONTINUATION_SNIPPET_MAX_CHARS),
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

fn one_line_compact(value: &str, max_chars: usize) -> String {
    let normalized = value.split_whitespace().collect::<Vec<_>>().join(" ");
    compact_summary(&normalized, max_chars)
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

    fn removed_line_count(&self) -> usize {
        self.hunks
            .iter()
            .flat_map(|hunk| &hunk.lines)
            .filter(|line| matches!(line, PatchLine::Remove(_)))
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

fn review_code_change_proposal(
    repo_root: &Path,
    artifact: ArtifactRecord,
    proposal: &str,
) -> ProposalReview {
    let mut checks = Vec::new();
    let mut target_path = "unknown".to_string();
    let mut target_exists = false;
    let mut new_file = false;
    let mut hunk_count = 0usize;
    let mut added_lines = 0usize;
    let mut removed_lines = 0usize;
    let mut can_apply = true;

    match parse_proposal_patch(proposal) {
        Ok(patch) => {
            target_path = patch.target_path.clone();
            new_file = patch.new_file;
            hunk_count = patch.hunks.len();
            added_lines = patch.added_line_count();
            removed_lines = patch.removed_line_count();
            checks.push(proposal_check(
                "patch_parse",
                "ok",
                format!(
                    "Parsed {hunk_count} hunk(s), {added_lines} added line(s), {removed_lines} removed line(s)."
                ),
            ));

            match safe_proposal_target_path(repo_root, &patch.target_path) {
                Ok(target) => {
                    target_exists = target.exists();
                    checks.push(proposal_check(
                        "target_safety",
                        "ok",
                        format!("Target stays inside workspace: {}", target.display()),
                    ));
                    if patch.new_file && target_exists {
                        can_apply = false;
                        checks.push(proposal_check(
                            "new_file_conflict",
                            "fail",
                            format!(
                                "{} already exists but the proposal creates it.",
                                patch.target_path
                            ),
                        ));
                    } else {
                        checks.push(proposal_check(
                            "new_file_conflict",
                            "ok",
                            if patch.new_file {
                                "New target does not exist yet.".to_string()
                            } else {
                                "Proposal modifies an existing or context-checked target."
                                    .to_string()
                            },
                        ));
                    }

                    let current_text = if target_exists {
                        match fs::read_to_string(&target) {
                            Ok(text) => text,
                            Err(error) => {
                                can_apply = false;
                                checks.push(proposal_check(
                                    "target_read",
                                    "fail",
                                    format!("Failed to read target before apply: {error}"),
                                ));
                                String::new()
                            }
                        }
                    } else {
                        String::new()
                    };
                    if can_apply {
                        match apply_parsed_patch(&current_text, &patch) {
                            Ok(_) => checks.push(proposal_check(
                                "patch_context",
                                "ok",
                                "Patch context matched the current workspace state.",
                            )),
                            Err(error) => {
                                can_apply = false;
                                checks.push(proposal_check(
                                    "patch_context",
                                    "fail",
                                    format!("Patch context check failed: {error}"),
                                ));
                            }
                        }
                    }
                }
                Err(error) => {
                    can_apply = false;
                    checks.push(proposal_check("target_safety", "fail", error));
                }
            }
        }
        Err(error) => {
            can_apply = false;
            checks.push(proposal_check("patch_parse", "fail", error));
        }
    }

    checks.push(proposal_check(
        "approval",
        "ok",
        "Apply remains gated by an explicit dry-run/approval action.",
    ));
    let risk_level = proposal_risk_level(can_apply, new_file, added_lines, removed_lines);
    ProposalReview {
        artifact,
        target_path,
        target_exists,
        new_file,
        hunk_count,
        added_lines,
        removed_lines,
        risk_level,
        can_apply,
        dry_run_required: true,
        checks,
    }
}

fn proposal_check(
    id: impl Into<String>,
    status: impl Into<String>,
    message: impl Into<String>,
) -> ProposalReviewCheck {
    ProposalReviewCheck {
        id: id.into(),
        status: status.into(),
        message: message.into(),
    }
}

fn proposal_risk_level(
    can_apply: bool,
    new_file: bool,
    added_lines: usize,
    removed_lines: usize,
) -> String {
    if !can_apply {
        return "blocked".to_string();
    }
    if removed_lines > 0 || added_lines + removed_lines > 200 {
        return "medium".to_string();
    }
    if new_file {
        return "low_new_file".to_string();
    }
    "low".to_string()
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
    fn local_runtime_records_source_rating_as_feedback_event() {
        let _env = OpenAiEnvGuard::clear();
        let root = unique_repo("source-rating");
        let source_path = root.join("source.md");
        fs::write(&source_path, "rating context").unwrap();
        let runtime = LocalAgentRuntime::open(&root).unwrap();
        let source = runtime.add_knowledge_source(None, &source_path).unwrap();
        let result = runtime
            .run_prompt(RunRequest {
                prompt: "Use the rated source".to_string(),
                workspace_id: None,
                mode: Some(LocalAgentMode::Chat),
            })
            .unwrap();
        let rating = runtime
            .rate_knowledge_source(
                &source.source_id,
                Some(&result.run.run_id),
                5,
                "useful grounding",
            )
            .unwrap();

        assert_eq!(rating.source_id, source.source_id);
        assert_eq!(rating.run_id.as_deref(), Some(result.run.run_id.as_str()));
        let events = runtime.run_events(&result.run.run_id).unwrap();
        assert!(events.iter().any(|event| {
            event.kind == "source_rated"
                && event.canonical_flow_id == "feedback"
                && event.primitive_id == "source_evaluation"
        }));
        let evidence = runtime.run_evidence_summary(&result.run.run_id).unwrap();
        assert_eq!(evidence.source_ratings.len(), 1);
        assert_eq!(evidence.source_ratings[0].rating, 5);
        assert!(evidence
            .primitive_ids
            .contains(&"source_evaluation".to_string()));
        assert!(evidence
            .canonical_flow_ids
            .contains(&"feedback".to_string()));
        assert!(evidence.core_trace.core_aligned);

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn local_runtime_replays_source_ratings_into_agent_context_priority() {
        let _env = OpenAiEnvGuard::clear();
        let root = unique_repo("source-rating-priority");
        let useful_path = root.join("useful.md");
        let noisy_path = root.join("noisy.md");
        fs::write(&useful_path, "trusted context").unwrap();
        fs::write(&noisy_path, "noisy context").unwrap();
        let runtime = LocalAgentRuntime::open(&root).unwrap();
        let useful = runtime
            .add_knowledge_source(Some("rated".to_string()), &useful_path)
            .unwrap();
        let noisy = runtime
            .add_knowledge_source(Some("rated".to_string()), &noisy_path)
            .unwrap();

        runtime
            .rate_knowledge_source(&useful.source_id, None, 5, "prefer this source")
            .unwrap();
        runtime
            .rate_knowledge_source(&noisy.source_id, None, 1, "demote this source")
            .unwrap();

        let context = runtime
            .agent_context(Some("rated"), Some(LocalAgentMode::CodeAgent))
            .unwrap();
        assert_eq!(context.source_ratings.len(), 2);
        assert_eq!(context.source_ratings[0].source_id, useful.source_id);
        assert_eq!(context.knowledge_sources[0].source_id, useful.source_id);
        assert_eq!(context.knowledge_sources[1].source_id, noisy.source_id);

        let result = runtime
            .run_prompt(RunRequest {
                prompt: "Use the preferred source first.".to_string(),
                workspace_id: Some("rated".to_string()),
                mode: Some(LocalAgentMode::Chat),
            })
            .unwrap();
        let context_event = result
            .events
            .iter()
            .find(|event| event.kind == "workspace_context_loaded")
            .unwrap();
        assert_eq!(context_event.payload["source_ratings"], 2);
        assert_eq!(
            context_event.payload["knowledge_rating_policy"],
            "latest_source_rated_priority"
        );
        let knowledge_event = result
            .events
            .iter()
            .find(|event| event.kind == "knowledge_retrieved")
            .unwrap();
        assert_eq!(
            knowledge_event.payload["sources"][0]["source_id"],
            useful.source_id
        );

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn local_runtime_records_run_checkpoint_into_continuation_context() {
        let _env = OpenAiEnvGuard::clear();
        let root = unique_repo("run-checkpoint");
        let runtime = LocalAgentRuntime::open(&root).unwrap();
        let result = runtime
            .run_prompt(RunRequest {
                prompt: "Create a checkpointable plan.".to_string(),
                workspace_id: Some("checkpoints".to_string()),
                mode: Some(LocalAgentMode::Chat),
            })
            .unwrap();

        let checkpoint = runtime
            .record_run_checkpoint(
                &result.run.run_id,
                "User decision: keep the Rust local runtime as the source of truth.",
            )
            .unwrap();

        assert_eq!(checkpoint.run_id, result.run.run_id);
        let events = runtime.run_events(&result.run.run_id).unwrap();
        assert!(events.iter().any(|event| {
            event.kind == "run_checkpoint_recorded"
                && event.canonical_flow_id == "feedback"
                && event.primitive_id == "event_audit"
        }));
        let evidence = runtime.run_evidence_summary(&result.run.run_id).unwrap();
        assert_eq!(evidence.checkpoints.len(), 1);
        assert!(evidence
            .checkpoints
            .first()
            .is_some_and(|item| item.note.contains("source of truth")));
        let compact = runtime.run_compact(&result.run.run_id).unwrap();
        assert!(compact.continuation_context.contains("Human checkpoints"));
        assert!(compact.continuation_context.contains("source of truth"));

        let continuation = runtime
            .run_continuation_attempt(ContinuationRequest {
                run_id: result.run.run_id.clone(),
                extra_instruction: Some("Continue with the checkpoint.".to_string()),
                mode: Some(LocalAgentMode::Chat),
            })
            .unwrap();
        assert!(continuation
            .run
            .prompt
            .contains("User decision: keep the Rust local runtime"));

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn local_runtime_records_workspace_tasks_as_events() {
        let _env = OpenAiEnvGuard::clear();
        let root = unique_repo("local-tasks");
        let runtime = LocalAgentRuntime::open(&root).unwrap();
        let result = runtime
            .run_prompt(RunRequest {
                prompt: "Create evidence for a task-linked run.".to_string(),
                workspace_id: Some("tasks".to_string()),
                mode: Some(LocalAgentMode::Chat),
            })
            .unwrap();

        let task = runtime
            .create_task(
                Some("tasks".to_string()),
                Some(&result.run.run_id),
                "Review local task event evidence",
                Some("high"),
            )
            .unwrap();
        let updated = runtime
            .update_task_status(&task.task_id, "done", Some("verified"))
            .unwrap();
        let active_task = runtime
            .create_task(
                Some("tasks".to_string()),
                None,
                "Carry active workspace task into context",
                Some("normal"),
            )
            .unwrap();
        let tasks = runtime.list_tasks(Some("tasks"), Some("done"), 20).unwrap();
        let context = runtime
            .agent_context(Some("tasks"), Some(LocalAgentMode::CodeAgent))
            .unwrap();
        let replay = runtime.workspace_replay(Some("tasks"), 50).unwrap();
        let compact = runtime.workspace_compact(Some("tasks"), 50).unwrap();
        let usage = runtime.workspace_usage(Some("tasks"), 50).unwrap();
        let events = runtime.run_events(&result.run.run_id).unwrap();

        assert_eq!(task.status, "todo");
        assert_eq!(task.priority, "high");
        assert_eq!(updated.status, "done");
        assert_eq!(active_task.status, "todo");
        assert!(tasks.iter().any(|item| item.task_id == task.task_id));
        assert!(context
            .tasks
            .iter()
            .any(|item| item.task_id == active_task.task_id));
        assert!(replay.tasks.iter().any(|item| item.task_id == task.task_id));
        assert_eq!(compact.task_count, 2);
        assert_eq!(compact.active_task_count, 1);
        assert!(compact
            .continuation_context
            .contains("Carry active workspace task into context"));
        assert_eq!(usage.task_count, 2);
        assert_eq!(usage.active_task_count, 1);
        assert!(usage.summary.contains("2 tasks (1 active)"));
        assert!(events.iter().any(|event| {
            event.kind == "task_created"
                && event.canonical_flow_id == "goal"
                && event.primitive_id == "event_audit"
        }));
        assert!(events.iter().any(|event| {
            event.kind == "task_updated"
                && event.canonical_flow_id == "feedback"
                && event.primitive_id == "event_audit"
        }));

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn local_runtime_builds_plan_from_agent_step_events() {
        let _env = OpenAiEnvGuard::clear();
        let root = unique_repo("run-plan");
        let note = root.join("plan_note.md");
        fs::write(&note, "plan-visible context").unwrap();
        let runtime = LocalAgentRuntime::open(&root).unwrap();
        let result = runtime
            .run_prompt(RunRequest {
                prompt: "Inspect @plan_note.md and summarize the agent plan.".to_string(),
                workspace_id: Some("plan".to_string()),
                mode: Some(LocalAgentMode::Chat),
            })
            .unwrap();

        let plan = runtime.run_plan(&result.run.run_id).unwrap();

        assert_eq!(plan.run.run_id, result.run.run_id);
        assert_eq!(plan.status, "finished");
        assert!(plan.step_count >= 1);
        assert_eq!(plan.completed_step_count, plan.step_count);
        assert!(plan.model_request_count >= 1);
        assert!(plan.tool_call_count >= 1);
        assert!(plan
            .steps
            .iter()
            .any(|step| step.title.contains("Prompt Reference Resolution")));
        assert!(plan
            .steps
            .iter()
            .any(|step| step.prompt_references.contains(&"plan_note.md".to_string())));

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn local_runtime_compacts_run_context_from_events_and_evidence() {
        let _env = OpenAiEnvGuard::clear();
        let root = unique_repo("run-compact");
        let note = root.join("compact_note.md");
        fs::write(&note, "compact-visible context").unwrap();
        let runtime = LocalAgentRuntime::open(&root).unwrap();
        let result = runtime
            .run_prompt(RunRequest {
                prompt: "Inspect @compact_note.md and produce a compactable answer.".to_string(),
                workspace_id: Some("compact".to_string()),
                mode: Some(LocalAgentMode::Chat),
            })
            .unwrap();

        let compact = runtime.run_compact(&result.run.run_id).unwrap();

        assert_eq!(compact.run.run_id, result.run.run_id);
        assert_eq!(compact.status, "finished");
        assert!(compact.core_aligned);
        assert!(compact.event_count >= compact.tool_call_count);
        assert!(compact.summary.contains(&result.run.run_id));
        assert!(compact
            .carry_forward_items
            .iter()
            .any(|item| item.contains("Previous prompt")));
        assert!(compact
            .carry_forward_items
            .iter()
            .any(|item| item.contains("Prompt references")));
        assert!(compact
            .continuation_context
            .contains("Compact Structure context"));
        assert!(compact
            .continuation_context
            .contains("Structure Core event, path, disclosure, and evidence"));

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn local_runtime_previews_event_gc_without_deleting_events() {
        let _env = OpenAiEnvGuard::clear();
        let root = unique_repo("event-gc");
        let runtime = LocalAgentRuntime::open(&root).unwrap();
        let result = runtime
            .run_prompt(RunRequest {
                prompt: "Inspect enough context to produce events.".to_string(),
                workspace_id: Some("gc".to_string()),
                mode: Some(LocalAgentMode::Chat),
            })
            .unwrap();

        let before = runtime.run_events(&result.run.run_id).unwrap();
        let preview = runtime
            .run_event_gc_preview(&result.run.run_id, Some(4))
            .unwrap();
        let after = runtime.run_events(&result.run.run_id).unwrap();

        assert_eq!(before.len(), after.len());
        assert_eq!(preview.summary.policy_id, "retain_last_n_events");
        assert_eq!(preview.summary.retain_last, 4);
        assert_eq!(preview.summary.retained_event_count, 4);
        assert_eq!(
            preview.summary.filtered_event_count,
            before.len().saturating_sub(4)
        );
        assert_eq!(preview.retained_events.len(), 4);
        assert!(preview
            .summary
            .retained_sequences
            .iter()
            .all(|sequence| !preview.summary.filtered_sequences.contains(sequence)));
        let evidence = runtime.run_evidence_summary(&result.run.run_id).unwrap();
        assert_eq!(evidence.event_gc.policy_id, "retain_last_n_events");
        assert_eq!(evidence.event_gc.retain_last, DEFAULT_EVENT_GC_RETAIN_LAST);
        assert_eq!(
            evidence.event_gc.retained_event_count + evidence.event_gc.filtered_event_count,
            evidence.event_count
        );

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn local_runtime_summarizes_tool_trace_from_events() {
        let _env = OpenAiEnvGuard::clear();
        let root = unique_repo("tool-trace");
        fs::write(root.join("trace.md"), "tool trace context").unwrap();
        let runtime = LocalAgentRuntime::open(&root).unwrap();
        let result = runtime
            .run_prompt(RunRequest {
                prompt: "Read @trace.md and summarize the trace.".to_string(),
                workspace_id: Some("tools".to_string()),
                mode: Some(LocalAgentMode::Chat),
            })
            .unwrap();

        let trace = runtime.run_tool_trace(&result.run.run_id).unwrap();

        assert!(!trace.is_empty());
        assert!(trace.iter().any(|entry| entry.name == "read_repo_file"));
        assert!(trace
            .iter()
            .all(|entry| entry.requested_sequence.is_some() || entry.completed_sequence.is_some()));
        assert!(trace.iter().any(|entry| entry.success == Some(true)));
        assert!(trace
            .iter()
            .any(|entry| entry.input["path"].as_str() == Some("trace.md")));

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn local_runtime_builds_run_status_snapshot_from_events() {
        let _env = OpenAiEnvGuard::clear();
        let root = unique_repo("run-status");
        fs::write(root.join("status.md"), "run status context").unwrap();
        let runtime = LocalAgentRuntime::open(&root).unwrap();
        let result = runtime
            .run_prompt(RunRequest {
                prompt: "Read @status.md and summarize the run status.".to_string(),
                workspace_id: Some("status".to_string()),
                mode: Some(LocalAgentMode::CodeAgent),
            })
            .unwrap();

        let status = runtime.run_status_snapshot(&result.run.run_id).unwrap();

        assert_eq!(status.run.run_id, result.run.run_id);
        assert!(status.terminal);
        assert!(status.core_aligned);
        assert_eq!(status.event_count, result.events.len());
        assert!(status.latest_event.is_some());
        assert_eq!(
            status
                .latest_event
                .as_ref()
                .map(|event| event.kind.as_str()),
            Some("run_finished")
        );
        assert!(status.model_usage.model_request_count >= 1);
        assert!(status.tool_call_count >= 1);
        assert_eq!(status.pending_tool_call_count, 0);
        assert!(status.response_artifact.is_some());
        assert!(status.artifact_count >= 1);
        assert!(status.flow_path.contains(&"evidence".to_string()));
        assert!(status
            .next_actions
            .iter()
            .any(|action| action.contains("Continue") || action.contains("Review proposal")));

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn local_runtime_summarizes_core_trace_path_from_events() {
        let _env = OpenAiEnvGuard::clear();
        let root = unique_repo("core-trace");
        fs::write(root.join("trace.md"), "core trace context").unwrap();
        let runtime = LocalAgentRuntime::open(&root).unwrap();
        let result = runtime
            .run_prompt(RunRequest {
                prompt: "Read @trace.md and summarize the Structure Core trace.".to_string(),
                workspace_id: Some("trace".to_string()),
                mode: Some(LocalAgentMode::CodeAgent),
            })
            .unwrap();

        let trace = runtime.run_core_trace(&result.run.run_id).unwrap();

        assert_eq!(trace.run.run_id, result.run.run_id);
        assert_eq!(trace.manifest_schema_version, "2026.05");
        assert!(trace.core_aligned);
        assert_eq!(trace.event_count, trace.steps.len());
        assert!(trace.flow_path.contains(&"goal".to_string()));
        assert!(trace.flow_path.contains(&"event".to_string()));
        assert!(trace.flow_path.contains(&"evidence".to_string()));
        assert!(trace.primitive_path.contains(&"event_audit".to_string()));
        assert!(trace
            .steps
            .iter()
            .any(|step| step.kind == "workspace_context_loaded"
                && step.payload_summary.contains("instructions=")));
        assert!(trace.steps.iter().any(|step| step.kind == "run_finished"
            && step.payload_summary.contains("terminal finished")));

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn local_runtime_builds_run_review_from_shared_evidence() {
        let _env = OpenAiEnvGuard::clear();
        let root = unique_repo("run-review");
        let runtime = LocalAgentRuntime::open(&root).unwrap();
        let result = runtime
            .run_prompt(RunRequest {
                prompt: "Create a local code-agent review proposal.".to_string(),
                workspace_id: Some("review".to_string()),
                mode: Some(LocalAgentMode::CodeAgent),
            })
            .unwrap();

        let review = runtime.run_review(&result.run.run_id).unwrap();

        assert_eq!(review.run.run_id, result.run.run_id);
        assert_eq!(review.status, "finished");
        assert!(review.core_aligned);
        assert!(review.event_count > 0);
        assert!(review.tool_call_count > 0);
        assert!(review.flow_path.contains(&"evidence".to_string()));
        assert!(review.primitive_path.contains(&"event_audit".to_string()));
        assert!(review.response_artifact.is_some());
        assert!(review.proposal_artifact.is_some());
        assert!(review
            .next_actions
            .iter()
            .any(|action| action.contains("dry-run")));

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
    fn local_runtime_compacts_workspace_session_from_replay_evidence() {
        let _env = OpenAiEnvGuard::clear();
        let root = unique_repo("workspace-compact");
        let runtime = LocalAgentRuntime::open(&root).unwrap();
        runtime
            .add_text_knowledge_source(Some("paper".to_string()), "session facts")
            .unwrap();
        let first = runtime
            .run_prompt(RunRequest {
                prompt: "Remember the workspace compact session.".to_string(),
                workspace_id: Some("paper".to_string()),
                mode: Some(LocalAgentMode::Chat),
            })
            .unwrap();
        let second = runtime
            .run_continuation_attempt(ContinuationRequest {
                run_id: first.run.run_id.clone(),
                extra_instruction: Some("Continue the same workspace session.".to_string()),
                mode: Some(LocalAgentMode::Chat),
            })
            .unwrap();

        let compact = runtime.workspace_compact(Some("paper"), 10).unwrap();

        assert_eq!(compact.workspace_id, "paper");
        assert!(compact.core_aligned);
        assert!(compact.event_count > 0);
        assert!(compact.run_count >= 2);
        assert_eq!(compact.knowledge_source_count, 1);
        assert!(compact
            .recent_runs
            .iter()
            .any(|run| run.run_id == second.run.run_id));
        assert!(compact
            .carry_forward_items
            .iter()
            .any(|item| item.contains("Latest run")));
        assert!(compact
            .continuation_context
            .contains("Compact Structure workspace context"));
        assert!(compact
            .continuation_context
            .contains("session handoff boundary"));

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn local_runtime_continues_from_workspace_compact_context() {
        let _env = OpenAiEnvGuard::clear();
        let root = unique_repo("workspace-continuation");
        let runtime = LocalAgentRuntime::open(&root).unwrap();
        runtime
            .add_text_knowledge_source(Some("session".to_string()), "workspace memory")
            .unwrap();
        let first = runtime
            .run_prompt(RunRequest {
                prompt: "Remember the workspace handoff phrase copper loom".to_string(),
                workspace_id: Some("session".to_string()),
                mode: Some(LocalAgentMode::Chat),
            })
            .unwrap();

        let continuation = runtime
            .workspace_continuation_attempt(WorkspaceContinuationRequest {
                workspace_id: Some("session".to_string()),
                extra_instruction: Some("Say the workspace handoff phrase back.".to_string()),
                mode: Some(LocalAgentMode::Chat),
                limit: 12,
            })
            .unwrap();

        assert_eq!(continuation.run.workspace_id, "session");
        assert!(continuation
            .run
            .prompt
            .contains("Continue Structure workspace/session"));
        assert!(continuation.run.prompt.contains(&first.run.run_id));
        assert!(continuation
            .run
            .prompt
            .contains("workspace handoff phrase copper loom"));
        assert!(continuation
            .run
            .prompt
            .contains("Compact Structure workspace context"));
        assert!(continuation
            .run
            .prompt
            .contains("durable session handoff boundary"));
        assert!(continuation
            .run
            .prompt
            .contains("Say the workspace handoff phrase back."));
        assert!(continuation
            .events
            .iter()
            .any(|event| event.kind == "run_finished"));

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn local_runtime_summarizes_workspace_usage_across_recent_runs() {
        let _env = OpenAiEnvGuard::clear();
        let root = unique_repo("workspace-usage");
        let runtime = LocalAgentRuntime::open(&root).unwrap();
        runtime
            .add_text_knowledge_source(Some("usage".to_string()), "usage knowledge")
            .unwrap();
        let first = runtime
            .run_prompt(RunRequest {
                prompt: "Summarize workspace usage once.".to_string(),
                workspace_id: Some("usage".to_string()),
                mode: Some(LocalAgentMode::Chat),
            })
            .unwrap();
        let _second = runtime
            .run_continuation_attempt(ContinuationRequest {
                run_id: first.run.run_id,
                extra_instruction: Some("Summarize workspace usage twice.".to_string()),
                mode: Some(LocalAgentMode::Chat),
            })
            .unwrap();

        let usage = runtime.workspace_usage(Some("usage"), 10).unwrap();

        assert_eq!(usage.workspace_id, "usage");
        assert!(usage.core_aligned);
        assert_eq!(usage.run_count, 2);
        assert!(usage.event_count > 0);
        assert!(usage.tool_call_count > 0);
        assert_eq!(usage.knowledge_source_count, 1);
        assert!(usage.model_usage.model_request_count >= usage.run_count);
        assert!(usage.model_usage.model_response_count >= usage.run_count);
        assert!(usage.flow_path.contains(&"goal".to_string()));
        assert!(usage.primitive_path.contains(&"event_audit".to_string()));
        assert!(usage.summary.contains("total tokens"));
        assert_eq!(usage.runs.len(), 2);

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
    fn local_runtime_records_desktop_command_turns_as_workspace_events() {
        let _env = OpenAiEnvGuard::clear();
        let root = unique_repo("command-turn-events");
        let runtime = LocalAgentRuntime::open(&root).unwrap();

        let turn = runtime
            .record_command_turn(
                Some("desktop"),
                "/context",
                "Agent Context\nWorkspace: desktop",
                "ok",
                "desktop",
            )
            .unwrap();
        let turns = runtime.command_turns(Some("desktop"), 10).unwrap();
        let feed = runtime
            .workspace_event_feed(Some("desktop"), Some(0), 10)
            .unwrap();

        assert_eq!(turn.input, "/context");
        assert_eq!(turn.surface, "desktop");
        assert_eq!(turn.status, "ok");
        assert_eq!(turns.len(), 1);
        assert_eq!(turns[0].output, "Agent Context\nWorkspace: desktop");
        assert!(feed.events.iter().any(|event| {
            event.kind == "command_turn_recorded"
                && event.canonical_flow_id == "event"
                && event.primitive_id == "event_audit"
        }));

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

        let review = runtime
            .review_code_change_proposal(&proposal.artifact_id)
            .unwrap();
        assert_eq!(review.artifact.artifact_id, proposal.artifact_id);
        assert_eq!(review.risk_level, "low_new_file");
        assert!(review.can_apply);
        assert!(review.dry_run_required);
        assert!(review.added_lines >= 1);
        assert_eq!(review.removed_lines, 0);
        assert!(review
            .checks
            .iter()
            .any(|check| check.id == "patch_context" && check.status == "ok"));

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
        assert!(!applied.target_existed);
        let backup = applied
            .backup_artifact
            .as_ref()
            .expect("apply should persist rollback backup");
        assert_eq!(backup.kind, "code_change_backup");
        let target_text = fs::read_to_string(&applied.target_path).unwrap();
        assert!(target_text.contains("Proposed Structure local code-agent change"));
        assert!(applied
            .target_path
            .ends_with("docs/local-code-agent-proposal.md"));

        let rollback = runtime
            .rollback_code_change_proposal(&proposal.artifact_id)
            .unwrap();
        assert!(rollback.restored);
        assert!(!rollback.target_existed);
        assert_eq!(rollback.backup_artifact.artifact_id, backup.artifact_id);
        assert!(!Path::new(&rollback.target_path).exists());

        let events = runtime.run_events(&result.run.run_id).unwrap();
        assert!(events.iter().any(|event| {
            event.kind == "code_change_reviewed"
                && event.canonical_flow_id == "feedback"
                && event.primitive_id == "event_audit"
                && event.payload["review"]["artifact"]["artifact_id"] == proposal.artifact_id
        }));
        assert!(events
            .iter()
            .any(|event| event.kind == "code_change_applied"));
        assert!(events
            .iter()
            .any(|event| event.kind == "code_change_reverted"));

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
        assert!(continuation
            .run
            .prompt
            .contains("Compact Structure context"));
        assert!(continuation.run.prompt.contains("Core aligned: true"));
        assert!(continuation
            .run
            .prompt
            .contains("durable continuation boundary"));
        assert!(continuation
            .run
            .prompt
            .contains("Structure Core event, path, disclosure, and evidence"));
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
            Command::Core Command::Surfaces Command::Chat Command::Run Command::Continue Command::Tasks Command::Proposals Command::Tui
            run_core run_surfaces run_chat_agent run_continue_agent run_continuation_from_session run_workspace_continuation_from_session run_local_agent
            WorkspaceCommand::Create WorkspaceCommand::List WorkspaceCommand::Show
            KnowledgeCommand::Add KnowledgeCommand::Show KnowledgeCommand::Remove
            RunsCommand::Events RunsCommand::Status RunsCommand::Trace RunsCommand::Review RunsCommand::Transcript WorkspaceCommand::Events WorkspaceCommand::Replay WorkspaceCommand::Compact WorkspaceCommand::Continue WorkspaceCommand::Usage
            ArtifactsCommand::List ArtifactsCommand::Show
            ProposalsCommand::List ProposalsCommand::Show ProposalsCommand::Apply ProposalsCommand::Rollback
            "#,
        );
        write_file(
            root,
            "frontend/src-tauri/src/lib.rs",
            r#"
            fn local_snapshot() {} fn core_manifest() {} core_manifest,
            fn local_agent_run() {} fn local_agent_continue_attempt() {}
            fn local_workspace_continue_attempt() {} local_workspace_continue_attempt,
            fn local_workspace_usage() {} local_workspace_usage,
            fn local_tasks() {} fn create_local_task() {} fn update_local_task_status() {}
            fn local_chat_turns() {} local_chat_turns,
            fn local_command_turns() {} fn record_local_command_turn() {}
            fn create_local_workspace() {} fn local_workspaces() {}
            fn local_repo_entries() {} fn local_repo_search() {} fn read_local_repo_file() {}
            fn add_local_knowledge() {} fn read_local_knowledge_source() {}
            fn remove_local_knowledge() {} remove_local_knowledge,
            fn local_run_transcript() {} fn local_run_status() {} fn local_run_events() {} fn local_run_core_trace() {} fn local_run_review() {}
            fn local_workspace_event_feed() {} local_workspace_event_feed,
            fn local_workspace_replay() {} local_workspace_replay,
            fn local_workspace_compact() {} local_workspace_compact,
            fn local_artifacts() {} fn read_local_artifact() {} read_local_artifact,
            fn apply_local_proposal() {} fn rollback_local_proposal() {}
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
