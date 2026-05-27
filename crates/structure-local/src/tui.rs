use anyhow::{anyhow, Context, Result};
use crossterm::cursor::{Hide, MoveTo, Show};
use crossterm::event::{self, Event, KeyCode, KeyEventKind, KeyModifiers};
use crossterm::execute;
use crossterm::style::{Attribute, Color, Print, ResetColor, SetAttribute, SetForegroundColor};
use crossterm::terminal::{self, Clear, ClearType, EnterAlternateScreen, LeaveAlternateScreen};
use std::io::{stdout, Write};
use std::path::Path;
use std::time::Duration;
use structure_local_core::{
    collect_snapshot, verify_structure_core_parity_for_repo, LocalSnapshot, SurfaceParityReport,
};
use structure_local_runtime::{
    ArtifactRecord, ContinuationRequest, KnowledgeSource, LocalAgentContext, LocalAgentMode,
    LocalAgentRuntime, LocalEvent, LocalEvidenceBundle, LocalLlmDiagnostic, LocalRunCompact,
    LocalRunCoreTrace, LocalRunPlan, LocalRunReview, LocalRunStatusSnapshot, LocalTaskRecord,
    LocalToolCall, LocalToolTraceEntry, ProposalApplyResult, ProposalReview,
    ProposalRollbackResult, RunAttempt, RunEvidenceSummary, RunRequest, RunSummary, RunTranscript,
    WorkspaceCompact, WorkspaceContinuationRequest, WorkspaceEventFeed, WorkspaceSummary,
    WorkspaceUsageSummary, WorktreeSnapshot,
};

pub(crate) fn run_tui(repo_root: &Path) -> Result<()> {
    terminal::enable_raw_mode().context("failed to enable raw mode")?;
    let mut out = stdout();
    execute!(out, EnterAlternateScreen, Hide).context("failed to enter TUI")?;

    let result = tui_loop(repo_root, &mut out);

    execute!(out, Show, LeaveAlternateScreen).ok();
    terminal::disable_raw_mode().ok();
    result
}

#[derive(Debug)]
struct TuiPreview {
    path: String,
    text: String,
}

#[derive(Debug, Clone, Copy)]
enum TuiInputKind {
    AgentPrompt,
    FollowUpPrompt,
    SessionFollowUpPrompt,
    RunCheckpoint,
    KnowledgePath,
    SourceRating,
    WorkspaceId,
    LocalCommand,
}

impl TuiInputKind {
    fn title(self) -> &'static str {
        match self {
            Self::AgentPrompt => "Custom agent prompt",
            Self::FollowUpPrompt => "Follow-up instruction",
            Self::SessionFollowUpPrompt => "Session follow-up instruction",
            Self::RunCheckpoint => "Human decision checkpoint",
            Self::KnowledgePath => "Knowledge file path",
            Self::SourceRating => "Source rating",
            Self::WorkspaceId => "Workspace id",
            Self::LocalCommand => "Local command argv",
        }
    }

    fn placeholder(self) -> &'static str {
        match self {
            Self::AgentPrompt => "Type a prompt and press Enter",
            Self::FollowUpPrompt => "Type a follow-up for the selected run",
            Self::SessionFollowUpPrompt => "Type a follow-up for the whole workspace session",
            Self::RunCheckpoint => "Type a human decision note for the selected run",
            Self::KnowledgePath => "Type an absolute or repo-relative file path",
            Self::SourceRating => "Type [source_id] <1-5> [note]; source_id defaults to latest",
            Self::WorkspaceId => "Type a workspace id to create or open",
            Self::LocalCommand => "Type an allowlisted command, e.g. cargo check --workspace",
        }
    }
}

#[derive(Debug)]
struct TuiInput {
    kind: TuiInputKind,
    value: String,
}

#[derive(Debug)]
struct TuiState {
    snapshot: LocalSnapshot,
    runs: Vec<RunSummary>,
    run_evidence: Vec<RunEvidenceSummary>,
    workspaces: Vec<WorkspaceSummary>,
    active_workspace_id: String,
    knowledge_sources: Vec<KnowledgeSource>,
    tasks: Vec<LocalTaskRecord>,
    artifacts: Vec<ArtifactRecord>,
    worktree: WorktreeSnapshot,
    agent_mode: LocalAgentMode,
    selected: usize,
    preview: Option<TuiPreview>,
    pending_apply_artifact_id: Option<String>,
    input: Option<TuiInput>,
    notice: String,
}

#[derive(Debug)]
struct TuiStateInit {
    snapshot: LocalSnapshot,
    runs: Vec<RunSummary>,
    run_evidence: Vec<RunEvidenceSummary>,
    workspaces: Vec<WorkspaceSummary>,
    active_workspace_id: String,
    knowledge_sources: Vec<KnowledgeSource>,
    tasks: Vec<LocalTaskRecord>,
    artifacts: Vec<ArtifactRecord>,
}

impl TuiState {
    fn new(init: TuiStateInit) -> Self {
        Self {
            snapshot: init.snapshot,
            runs: init.runs,
            run_evidence: init.run_evidence,
            workspaces: init.workspaces,
            active_workspace_id: init.active_workspace_id,
            knowledge_sources: init.knowledge_sources,
            tasks: init.tasks,
            artifacts: init.artifacts,
            worktree: unavailable_worktree_snapshot(),
            agent_mode: LocalAgentMode::CodeAgent,
            selected: 0,
            preview: None,
            pending_apply_artifact_id: None,
            input: None,
            notice: "Ready".to_string(),
        }
    }

    fn refresh(&mut self, repo_root: &Path) -> Result<()> {
        self.snapshot = local_result(collect_snapshot(repo_root))?;
        let runtime = local_result(LocalAgentRuntime::open(repo_root))?;
        if local_result(runtime.list_workspaces(1))?.is_empty() {
            local_result(runtime.ensure_workspace(Some("default".to_string())))?;
        }
        self.workspaces = local_result(runtime.list_workspaces(12))?;
        if !self
            .workspaces
            .iter()
            .any(|workspace| workspace.workspace_id == self.active_workspace_id)
        {
            self.active_workspace_id = self
                .workspaces
                .first()
                .map(|workspace| workspace.workspace_id.clone())
                .unwrap_or_else(|| "default".to_string());
        }
        let workspace_id = self.active_workspace_id.as_str();
        self.runs = local_result(runtime.list_runs(Some(workspace_id), 5))?;
        self.run_evidence = collect_run_evidence(&runtime, &self.runs);
        self.knowledge_sources = local_result(runtime.knowledge_sources(Some(workspace_id), 5))?;
        self.tasks = local_result(runtime.list_tasks(Some(workspace_id), None, 8))?;
        self.artifacts = local_result(runtime.list_artifacts(Some(workspace_id), None, 5))?;
        self.worktree = runtime.worktree_snapshot();
        self.pending_apply_artifact_id = None;
        self.notice = "Snapshot refreshed".to_string();
        Ok(())
    }

    fn record_command_turn(
        &self,
        repo_root: &Path,
        input: &str,
        status: &str,
        output: &str,
    ) -> Result<()> {
        let runtime = local_result(LocalAgentRuntime::open(repo_root))?;
        local_result(runtime.record_command_turn(
            Some(&self.active_workspace_id),
            input,
            output,
            status,
            "tui",
        ))?;
        Ok(())
    }

    fn begin_input(&mut self, kind: TuiInputKind) {
        self.input = Some(TuiInput {
            kind,
            value: String::new(),
        });
        self.notice = kind.placeholder().to_string();
    }

    fn handle_input_key(
        &mut self,
        key: crossterm::event::KeyEvent,
        repo_root: &Path,
    ) -> Result<()> {
        match key.code {
            KeyCode::Esc => {
                self.input = None;
                self.notice = "Input cancelled".to_string();
            }
            KeyCode::Enter => self.submit_input(repo_root)?,
            KeyCode::Backspace => {
                if let Some(input) = &mut self.input {
                    input.value.pop();
                }
            }
            KeyCode::Char(character)
                if !key.modifiers.contains(KeyModifiers::CONTROL)
                    && !key.modifiers.contains(KeyModifiers::ALT) =>
            {
                if let Some(input) = &mut self.input {
                    input.value.push(character);
                }
            }
            _ => {}
        }
        Ok(())
    }

    fn submit_input(&mut self, repo_root: &Path) -> Result<()> {
        let Some(input) = self.input.take() else {
            return Ok(());
        };
        let value = input.value.trim().to_string();
        if value.is_empty() {
            self.notice = format!("{} was empty", input.kind.title());
            return Ok(());
        }

        match input.kind {
            TuiInputKind::AgentPrompt => self.run_custom_prompt(repo_root, value),
            TuiInputKind::FollowUpPrompt => self.run_follow_up_prompt(repo_root, value),
            TuiInputKind::SessionFollowUpPrompt => {
                self.run_session_follow_up_prompt(repo_root, value)
            }
            TuiInputKind::RunCheckpoint => self.record_run_checkpoint(repo_root, value),
            TuiInputKind::KnowledgePath => self.add_knowledge_path(repo_root, value),
            TuiInputKind::SourceRating => self.rate_knowledge_source(repo_root, value),
            TuiInputKind::WorkspaceId => self.open_or_create_workspace(repo_root, value),
            TuiInputKind::LocalCommand => self.run_local_command(repo_root, value),
        }
    }

    fn run_custom_prompt(&mut self, repo_root: &Path, prompt: String) -> Result<()> {
        let runtime = local_result(LocalAgentRuntime::open(repo_root))?;
        let attempt = local_result(runtime.run_prompt_attempt(RunRequest {
            prompt,
            workspace_id: Some(self.active_workspace_id.clone()),
            mode: Some(self.agent_mode.clone()),
        }))?;
        self.active_workspace_id = attempt.run.workspace_id.clone();
        let preview = render_run_attempt(&attempt);
        let notice = if attempt.result.is_some() {
            format!("Finished {}", attempt.run.run_id)
        } else {
            format!("Failed {}", attempt.run.run_id)
        };
        self.refresh(repo_root)?;
        self.preview = Some(TuiPreview {
            path: format!("run attempt / {}", attempt.run.run_id),
            text: preview,
        });
        self.notice = notice;
        Ok(())
    }

    fn rerun_selected_prompt(&mut self, repo_root: &Path) -> Result<()> {
        let Some(source_run) = self.runs.get(self.selected).cloned() else {
            self.notice = "No run selected for rerun".to_string();
            return Ok(());
        };
        let runtime = local_result(LocalAgentRuntime::open(repo_root))?;
        let attempt = local_result(runtime.run_prompt_attempt(RunRequest {
            prompt: source_run.prompt.clone(),
            workspace_id: Some(source_run.workspace_id.clone()),
            mode: Some(self.agent_mode.clone()),
        }))?;
        self.active_workspace_id = attempt.run.workspace_id.clone();
        let preview = render_run_attempt(&attempt);
        let notice = if attempt.result.is_some() {
            format!("Reran {} as {}", source_run.run_id, attempt.run.run_id)
        } else {
            format!(
                "Rerun failed {} as {}",
                source_run.run_id, attempt.run.run_id
            )
        };
        self.refresh(repo_root)?;
        self.preview = Some(TuiPreview {
            path: format!("history rerun / {}", attempt.run.run_id),
            text: preview,
        });
        self.notice = notice;
        Ok(())
    }

    fn run_follow_up_prompt(&mut self, repo_root: &Path, instruction: String) -> Result<()> {
        let Some(run_id) = self.selected_run_id().map(str::to_string) else {
            self.notice = "No run selected for follow-up".to_string();
            return Ok(());
        };
        let runtime = local_result(LocalAgentRuntime::open(repo_root))?;
        let attempt = local_result(runtime.run_continuation_attempt(ContinuationRequest {
            run_id: run_id.clone(),
            extra_instruction: Some(instruction),
            mode: Some(self.agent_mode.clone()),
        }))?;
        self.active_workspace_id = attempt.run.workspace_id.clone();
        let preview = render_run_attempt(&attempt);
        let notice = format!("Continued {run_id} as {}", attempt.run.run_id);
        self.refresh(repo_root)?;
        self.preview = Some(TuiPreview {
            path: format!("continuation / {}", attempt.run.run_id),
            text: preview,
        });
        self.notice = notice;
        Ok(())
    }

    fn run_session_follow_up_prompt(
        &mut self,
        repo_root: &Path,
        instruction: String,
    ) -> Result<()> {
        let runtime = local_result(LocalAgentRuntime::open(repo_root))?;
        let workspace_id = self.active_workspace_id.clone();
        let attempt = local_result(runtime.workspace_continuation_attempt(
            WorkspaceContinuationRequest {
                workspace_id: Some(workspace_id.clone()),
                extra_instruction: Some(instruction),
                mode: Some(self.agent_mode.clone()),
                limit: 12,
            },
        ))?;
        self.active_workspace_id = attempt.run.workspace_id.clone();
        let preview = render_run_attempt(&attempt);
        let notice = format!("Continued session {workspace_id} as {}", attempt.run.run_id);
        self.refresh(repo_root)?;
        self.preview = Some(TuiPreview {
            path: format!("session continuation / {}", attempt.run.run_id),
            text: preview,
        });
        self.notice = notice;
        Ok(())
    }

    fn record_run_checkpoint(&mut self, repo_root: &Path, note: String) -> Result<()> {
        let Some(run_id) = self.selected_run_id().map(str::to_string) else {
            self.notice = "No run selected for checkpoint".to_string();
            return Ok(());
        };
        let runtime = local_result(LocalAgentRuntime::open(repo_root))?;
        let checkpoint = local_result(runtime.record_run_checkpoint(&run_id, &note))?;
        self.preview = Some(TuiPreview {
            path: format!("run checkpoint / {}", checkpoint.run_id),
            text: render_run_checkpoint(&checkpoint),
        });
        self.pending_apply_artifact_id = None;
        let notice = format!("Checkpoint recorded: {}", checkpoint.checkpoint_id);
        self.refresh(repo_root)?;
        self.notice = notice;
        Ok(())
    }

    fn add_knowledge_path(&mut self, repo_root: &Path, path: String) -> Result<()> {
        let runtime = local_result(LocalAgentRuntime::open(repo_root))?;
        let source = local_result(
            runtime.add_knowledge_source(Some(self.active_workspace_id.clone()), path),
        )?;
        let notice = format!("Knowledge added: {}", source.title);
        self.refresh(repo_root)?;
        self.notice = notice;
        Ok(())
    }

    fn remove_latest_knowledge(&mut self, repo_root: &Path) -> Result<()> {
        let Some(source) = self.knowledge_sources.first() else {
            self.notice = "No knowledge source to remove".to_string();
            return Ok(());
        };
        let runtime = local_result(LocalAgentRuntime::open(repo_root))?;
        let removed = local_result(runtime.remove_knowledge_source(&source.source_id))?;
        let notice = format!("Removed registration for {}", removed.title);
        self.refresh(repo_root)?;
        self.notice = notice;
        Ok(())
    }

    fn rate_knowledge_source(&mut self, repo_root: &Path, input: String) -> Result<()> {
        let Some((source_id, rating, note)) =
            parse_source_rating_input(&input, self.knowledge_sources.first())
        else {
            self.notice = "Use [source_id] <1-5> [note]".to_string();
            return Ok(());
        };
        let runtime = local_result(LocalAgentRuntime::open(repo_root))?;
        let run_id = self.selected_run_id().map(str::to_string);
        let rating = local_result(runtime.rate_knowledge_source(
            &source_id,
            run_id.as_deref(),
            rating,
            &note,
        ))?;
        let notice = format!(
            "Rated {} {}/5 for {}",
            rating.source_title,
            rating.rating,
            rating.run_id.as_deref().unwrap_or("workspace")
        );
        self.refresh(repo_root)?;
        self.notice = notice;
        Ok(())
    }

    fn open_or_create_workspace(&mut self, repo_root: &Path, workspace_id: String) -> Result<()> {
        let runtime = local_result(LocalAgentRuntime::open(repo_root))?;
        let workspace = local_result(runtime.ensure_workspace(Some(workspace_id)))?;
        self.active_workspace_id = workspace.workspace_id.clone();
        self.refresh(repo_root)?;
        self.notice = format!("Workspace: {}", workspace.workspace_id);
        Ok(())
    }

    fn run_workspace_check(&mut self, repo_root: &Path) -> Result<()> {
        let runtime = local_result(LocalAgentRuntime::open(repo_root))?;
        let attempt = local_result(
            runtime.run_prompt_attempt(RunRequest {
                prompt: "Inspect the local workspace state and summarize the available context."
                    .to_string(),
                workspace_id: Some(self.active_workspace_id.clone()),
                mode: Some(self.agent_mode.clone()),
            }),
        )?;
        self.active_workspace_id = attempt.run.workspace_id.clone();
        let preview = render_run_attempt(&attempt);
        let notice = if attempt.result.is_some() {
            format!("Finished {}", attempt.run.run_id)
        } else {
            format!("Failed {}", attempt.run.run_id)
        };
        self.refresh(repo_root)?;
        self.preview = Some(TuiPreview {
            path: format!("workspace check / {}", attempt.run.run_id),
            text: preview,
        });
        self.notice = notice;
        Ok(())
    }

    fn preview_agent_context(&mut self, repo_root: &Path) -> Result<()> {
        let runtime = local_result(LocalAgentRuntime::open(repo_root))?;
        let context = local_result(runtime.agent_context(
            Some(&self.active_workspace_id),
            Some(self.agent_mode.clone()),
        ))?;
        let text = render_agent_context(&context);
        self.pending_apply_artifact_id = None;
        self.preview = Some(TuiPreview {
            path: format!("agent context / {}", context.workspace_id),
            text: text.clone(),
        });
        self.notice = format!(
            "Agent context: {} instructions, {} sources, {} tasks, {} turns",
            context.agent_instructions.len(),
            context.knowledge_sources.len(),
            context.tasks.len(),
            context.recent_turns.len()
        );
        self.record_command_turn(repo_root, "A context", "ok", &text)?;
        Ok(())
    }

    fn preview_local_doctor(&mut self, repo_root: &Path) -> Result<()> {
        let snapshot = local_result(collect_snapshot(repo_root))?;
        let runtime = local_result(LocalAgentRuntime::open(repo_root))?;
        let diagnostic = runtime.llm_diagnostic();
        let parity = local_result(verify_structure_core_parity_for_repo(repo_root))?;
        let context = local_result(runtime.agent_context(
            Some(&self.active_workspace_id),
            Some(self.agent_mode.clone()),
        ))?;
        self.snapshot = snapshot.clone();
        let text = render_local_doctor(&snapshot, &diagnostic, &parity, &context);
        self.pending_apply_artifact_id = None;
        self.preview = Some(TuiPreview {
            path: format!("local doctor / {}", context.workspace_id),
            text: text.clone(),
        });
        self.notice = format!(
            "Doctor: llm={} parity={} context={} sources/{} tasks",
            if diagnostic.ok { "ok" } else { "check" },
            if parity.passed { "ok" } else { "drift" },
            context.knowledge_sources.len(),
            context.tasks.len()
        );
        self.record_command_turn(repo_root, "H doctor", "ok", &text)?;
        Ok(())
    }

    fn preview_command_map(&mut self, repo_root: &Path) -> Result<()> {
        let text = render_tui_command_map(self);
        self.pending_apply_artifact_id = None;
        self.preview = Some(TuiPreview {
            path: format!("tui command map / {}", self.active_workspace_id),
            text: text.clone(),
        });
        self.notice = "Command map: Structure runtime actions grouped by agent loop".to_string();
        self.record_command_turn(repo_root, "? map", "ok", &text)?;
        Ok(())
    }

    fn run_local_command(&mut self, repo_root: &Path, command: String) -> Result<()> {
        let runtime = local_result(LocalAgentRuntime::open(repo_root))?;
        let argv = command
            .split_whitespace()
            .map(str::to_string)
            .collect::<Vec<_>>();
        if argv.is_empty() {
            self.notice = "Local command argv was empty".to_string();
            return Ok(());
        }

        let result = local_result(runtime.execute_workspace_tool(
            Some(self.active_workspace_id.clone()),
            "tui",
            LocalToolCall {
                call_id: "tui_run_local_command".to_string(),
                name: "run_local_command".to_string(),
                input: serde_json::json!({
                    "argv": argv,
                    "cwd": ".",
                    "timeout_ms": 30_000,
                    "max_output_chars": 12_000,
                }),
            },
        ))?;
        let notice = if result.success {
            let ok = result
                .output
                .get("success")
                .and_then(serde_json::Value::as_bool)
                .unwrap_or(false);
            if ok {
                "Local command passed".to_string()
            } else {
                "Local command completed with a non-zero result".to_string()
            }
        } else {
            "Local command was rejected or failed".to_string()
        };
        self.pending_apply_artifact_id = None;
        self.preview = Some(TuiPreview {
            path: "run_local_command".to_string(),
            text: render_local_command_result(&result),
        });
        self.notice = notice;
        Ok(())
    }

    fn preview_latest_knowledge(&mut self, repo_root: &Path) -> Result<()> {
        let Some(source) = self.knowledge_sources.first() else {
            self.notice = "No knowledge source to preview".to_string();
            return Ok(());
        };
        let runtime = local_result(LocalAgentRuntime::open(repo_root))?;
        let preview = local_result(runtime.read_knowledge_source(&source.source_id, 64_000))?;
        self.pending_apply_artifact_id = None;
        self.preview = Some(TuiPreview {
            path: preview.source.path,
            text: format!(
                "Knowledge Source\nID: {}\nWorkspace: {}\nRead: {} bytes\nTruncated: {}\n\n{}",
                preview.source.source_id,
                preview.source.workspace_id,
                preview.bytes_read,
                preview.truncated,
                preview.preview
            ),
        });
        self.notice = "Previewing latest knowledge source".to_string();
        Ok(())
    }

    fn preview_latest_artifact(&mut self, repo_root: &Path) -> Result<()> {
        let Some(artifact) = self.artifacts.first().cloned() else {
            self.notice = "No artifact to preview".to_string();
            return Ok(());
        };
        self.pending_apply_artifact_id = None;
        self.preview_artifact_record(repo_root, &artifact, "Previewing latest artifact")
    }

    fn preview_selected_run_transcript(&mut self, repo_root: &Path) -> Result<()> {
        let Some(run) = self.runs.get(self.selected).cloned() else {
            self.notice = "No run selected".to_string();
            return Ok(());
        };
        let runtime = local_result(LocalAgentRuntime::open(repo_root))?;
        let transcript = local_result(runtime.run_transcript(&run.run_id))?;
        self.pending_apply_artifact_id = None;
        self.preview = Some(TuiPreview {
            path: format!("run transcript / {}", transcript.run.run_id),
            text: render_run_transcript(&transcript),
        });
        self.notice = format!("Transcript: {}", run.run_id);
        Ok(())
    }

    fn preview_selected_run_events(&mut self, repo_root: &Path) -> Result<()> {
        let Some(run) = self.runs.get(self.selected).cloned() else {
            self.notice = "No run selected".to_string();
            return Ok(());
        };
        let runtime = local_result(LocalAgentRuntime::open(repo_root))?;
        let events = local_result(runtime.run_events(&run.run_id))?;
        let text = render_run_events(&run, &events);
        self.pending_apply_artifact_id = None;
        self.preview = Some(TuiPreview {
            path: format!("run events / {}", run.run_id),
            text: text.clone(),
        });
        self.notice = format!("Events: {} for {}", events.len(), run.run_id);
        self.record_command_turn(repo_root, &format!("E events {}", run.run_id), "ok", &text)?;
        Ok(())
    }

    fn preview_selected_run_core_trace(&mut self, repo_root: &Path) -> Result<()> {
        let Some(run) = self.runs.get(self.selected).cloned() else {
            self.notice = "No run selected".to_string();
            return Ok(());
        };
        let runtime = local_result(LocalAgentRuntime::open(repo_root))?;
        let trace = local_result(runtime.run_core_trace(&run.run_id))?;
        self.pending_apply_artifact_id = None;
        self.preview = Some(TuiPreview {
            path: format!("core trace / {}", trace.run.run_id),
            text: render_run_core_trace(&trace),
        });
        self.notice = format!("Core trace: {}", run.run_id);
        Ok(())
    }

    fn preview_selected_run_tool_trace(&mut self, repo_root: &Path) -> Result<()> {
        let Some(run) = self.runs.get(self.selected).cloned() else {
            self.notice = "No run selected".to_string();
            return Ok(());
        };
        let runtime = local_result(LocalAgentRuntime::open(repo_root))?;
        let trace = local_result(runtime.run_tool_trace(&run.run_id))?;
        self.pending_apply_artifact_id = None;
        self.preview = Some(TuiPreview {
            path: format!("tool trace / {}", run.run_id),
            text: render_tool_trace(&run, &trace),
        });
        self.notice = format!("Tool trace: {} calls for {}", trace.len(), run.run_id);
        Ok(())
    }

    fn preview_selected_run_plan(&mut self, repo_root: &Path) -> Result<()> {
        let Some(run) = self.runs.get(self.selected).cloned() else {
            self.notice = "No run selected".to_string();
            return Ok(());
        };
        let runtime = local_result(LocalAgentRuntime::open(repo_root))?;
        let plan = local_result(runtime.run_plan(&run.run_id))?;
        self.pending_apply_artifact_id = None;
        self.preview = Some(TuiPreview {
            path: format!("run plan / {}", plan.run.run_id),
            text: render_run_plan(&plan),
        });
        self.notice = format!("Plan: {}", run.run_id);
        Ok(())
    }

    fn preview_selected_run_compact(&mut self, repo_root: &Path) -> Result<()> {
        let Some(run) = self.runs.get(self.selected).cloned() else {
            self.notice = "No run selected".to_string();
            return Ok(());
        };
        let runtime = local_result(LocalAgentRuntime::open(repo_root))?;
        let compact = local_result(runtime.run_compact(&run.run_id))?;
        self.pending_apply_artifact_id = None;
        self.preview = Some(TuiPreview {
            path: format!("run compact / {}", compact.run.run_id),
            text: render_run_compact(&compact),
        });
        self.notice = format!("Compact: {}", run.run_id);
        Ok(())
    }

    fn preview_selected_run_review(&mut self, repo_root: &Path) -> Result<()> {
        let Some(run) = self.runs.get(self.selected).cloned() else {
            self.notice = "No run selected".to_string();
            return Ok(());
        };
        let runtime = local_result(LocalAgentRuntime::open(repo_root))?;
        let review = local_result(runtime.run_review(&run.run_id))?;
        self.pending_apply_artifact_id = None;
        self.preview = Some(TuiPreview {
            path: format!("run review / {}", review.run.run_id),
            text: render_run_review(&review),
        });
        self.notice = format!("Review: {}", run.run_id);
        Ok(())
    }

    fn preview_selected_run_status(&mut self, repo_root: &Path) -> Result<()> {
        let Some(run) = self.runs.get(self.selected).cloned() else {
            self.notice = "No run selected".to_string();
            return Ok(());
        };
        let runtime = local_result(LocalAgentRuntime::open(repo_root))?;
        let status = local_result(runtime.run_status_snapshot(&run.run_id))?;
        self.pending_apply_artifact_id = None;
        self.preview = Some(TuiPreview {
            path: format!("run status / {}", status.run.run_id),
            text: render_run_status_snapshot(&status),
        });
        self.notice = format!("Status: {}", run.run_id);
        Ok(())
    }

    fn preview_latest_proposal(&mut self, repo_root: &Path) -> Result<()> {
        let Some(artifact) =
            select_proposal_artifact(&self.artifacts, self.selected_run_id()).cloned()
        else {
            self.notice = "No code-change proposal to preview".to_string();
            return Ok(());
        };
        let notice = self.proposal_notice(&artifact, "Previewing");
        self.pending_apply_artifact_id = None;
        self.preview_artifact_record(repo_root, &artifact, &notice)
    }

    fn preview_latest_proposal_apply(&mut self, repo_root: &Path) -> Result<()> {
        let Some(artifact) =
            select_proposal_artifact(&self.artifacts, self.selected_run_id()).cloned()
        else {
            self.notice = "No code-change proposal to dry-run".to_string();
            return Ok(());
        };
        let runtime = local_result(LocalAgentRuntime::open(repo_root))?;
        let result = local_result(runtime.apply_code_change_proposal(&artifact.artifact_id, true))?;
        self.pending_apply_artifact_id = Some(result.artifact.artifact_id.clone());
        self.preview = Some(TuiPreview {
            path: result.target_path.clone(),
            text: render_proposal_apply_result(&result),
        });
        self.notice = format!(
            "{} dry-run ready; press y to apply",
            self.proposal_notice(&artifact, "Proposal")
        );
        Ok(())
    }

    fn review_latest_proposal_risk(&mut self, repo_root: &Path) -> Result<()> {
        let Some(artifact) =
            select_proposal_artifact(&self.artifacts, self.selected_run_id()).cloned()
        else {
            self.notice = "No code-change proposal to review".to_string();
            return Ok(());
        };
        let runtime = local_result(LocalAgentRuntime::open(repo_root))?;
        let review = local_result(runtime.review_code_change_proposal(&artifact.artifact_id))?;
        self.pending_apply_artifact_id = None;
        self.preview = Some(TuiPreview {
            path: format!("proposal risk / {}", review.artifact.artifact_id),
            text: render_proposal_review(&review),
        });
        self.notice = format!(
            "{} risk: {}",
            self.proposal_notice(&artifact, "Proposal"),
            review.risk_level
        );
        Ok(())
    }

    fn apply_pending_proposal(&mut self, repo_root: &Path) -> Result<()> {
        let Some(artifact_id) = self.pending_apply_artifact_id.clone() else {
            self.notice = "Press u to dry-run a proposal before applying".to_string();
            return Ok(());
        };
        let runtime = local_result(LocalAgentRuntime::open(repo_root))?;
        let result = local_result(runtime.apply_code_change_proposal(&artifact_id, false))?;
        self.preview = Some(TuiPreview {
            path: result.target_path.clone(),
            text: render_proposal_apply_result(&result),
        });
        self.pending_apply_artifact_id = None;
        let notice = format!("Applied proposal to {}", result.target_path);
        self.refresh(repo_root)?;
        self.notice = notice;
        Ok(())
    }

    fn rollback_latest_proposal(&mut self, repo_root: &Path) -> Result<()> {
        let Some(artifact) =
            select_proposal_artifact(&self.artifacts, self.selected_run_id()).cloned()
        else {
            self.notice = "No code-change proposal to rollback".to_string();
            return Ok(());
        };
        let runtime = local_result(LocalAgentRuntime::open(repo_root))?;
        let result = local_result(runtime.rollback_code_change_proposal(&artifact.artifact_id))?;
        self.preview = Some(TuiPreview {
            path: format!("proposal rollback / {}", result.artifact.artifact_id),
            text: render_proposal_rollback_result(&result),
        });
        self.pending_apply_artifact_id = None;
        let notice = format!("Rolled back proposal for {}", result.target_path);
        self.refresh(repo_root)?;
        self.notice = notice;
        Ok(())
    }

    fn selected_run_id(&self) -> Option<&str> {
        self.runs.get(self.selected).map(|run| run.run_id.as_str())
    }

    fn proposal_notice(&self, artifact: &ArtifactRecord, action: &str) -> String {
        if self.selected_run_id() == Some(artifact.run_id.as_str()) {
            format!("{action} selected run proposal")
        } else {
            format!("{action} latest proposal")
        }
    }

    fn preview_artifact_record(
        &mut self,
        repo_root: &Path,
        artifact: &ArtifactRecord,
        notice: &str,
    ) -> Result<()> {
        let runtime = local_result(LocalAgentRuntime::open(repo_root))?;
        let preview = local_result(runtime.read_artifact(&artifact.artifact_id, 64_000))?;
        self.preview = Some(TuiPreview {
            path: preview.artifact.path,
            text: format!(
                "Artifact\nID: {}\nRun: {}\nWorkspace: {}\nKind: {}\nRead: {} bytes\nTruncated: {}\n\n{}",
                preview.artifact.artifact_id,
                preview.artifact.run_id,
                preview.artifact.workspace_id,
                preview.artifact.kind,
                preview.bytes_read,
                preview.truncated,
                preview.preview
            ),
        });
        self.notice = notice.to_string();
        Ok(())
    }

    fn preview_parity_report(&mut self, repo_root: &Path) -> Result<()> {
        let report = local_result(verify_structure_core_parity_for_repo(repo_root))?;
        self.preview = Some(TuiPreview {
            path: "core/structure_core.json parity".to_string(),
            text: render_parity_report(&report),
        });
        self.notice = if report.passed {
            "Surface parity passed".to_string()
        } else {
            "Surface parity needs attention".to_string()
        };
        Ok(())
    }

    fn preview_evidence_bundle(&mut self, repo_root: &Path) -> Result<()> {
        let runtime = local_result(LocalAgentRuntime::open(repo_root))?;
        let bundle =
            local_result(runtime.local_evidence_bundle(Some(&self.active_workspace_id), 50))?;
        self.preview = Some(TuiPreview {
            path: "local evidence bundle".to_string(),
            text: render_evidence_bundle(&bundle),
        });
        self.notice = format!(
            "Evidence bundle: {} runs, {} events",
            bundle.workspace_replay.runs.len(),
            bundle.workspace_replay.events.len()
        );
        Ok(())
    }

    fn preview_workspace_compact(&mut self, repo_root: &Path) -> Result<()> {
        let runtime = local_result(LocalAgentRuntime::open(repo_root))?;
        let compact = local_result(runtime.workspace_compact(Some(&self.active_workspace_id), 12))?;
        self.preview = Some(TuiPreview {
            path: format!("workspace compact / {}", compact.workspace_id),
            text: render_workspace_compact(&compact),
        });
        self.notice = format!(
            "Workspace compact: {} runs, {} events",
            compact.run_count, compact.event_count
        );
        Ok(())
    }

    fn preview_workspace_usage(&mut self, repo_root: &Path) -> Result<()> {
        let runtime = local_result(LocalAgentRuntime::open(repo_root))?;
        let usage = local_result(runtime.workspace_usage(Some(&self.active_workspace_id), 20))?;
        self.preview = Some(TuiPreview {
            path: format!("workspace usage / {}", usage.workspace_id),
            text: render_workspace_usage(&usage),
        });
        self.notice = format!(
            "Workspace usage: {} runs, {} tokens",
            usage.run_count, usage.model_usage.total_tokens
        );
        Ok(())
    }

    fn preview_workspace_events(&mut self, repo_root: &Path) -> Result<()> {
        let runtime = local_result(LocalAgentRuntime::open(repo_root))?;
        let feed = local_result(runtime.workspace_event_feed(
            Some(&self.active_workspace_id),
            Some(0),
            80,
        ))?;
        let text = render_workspace_event_feed(&feed);
        self.pending_apply_artifact_id = None;
        self.preview = Some(TuiPreview {
            path: format!("workspace events / {}", feed.workspace_id),
            text: text.clone(),
        });
        self.notice = format!(
            "Workspace events: {} after #{}",
            feed.events.len(),
            feed.after_sequence
        );
        self.record_command_turn(
            repo_root,
            &format!("W events {}", feed.workspace_id),
            "ok",
            &text,
        )?;
        Ok(())
    }

    fn preview_workspace_tasks(&mut self, repo_root: &Path) -> Result<()> {
        let runtime = local_result(LocalAgentRuntime::open(repo_root))?;
        let tasks = local_result(runtime.list_tasks(Some(&self.active_workspace_id), None, 50))?;
        self.preview = Some(TuiPreview {
            path: format!("workspace tasks / {}", self.active_workspace_id),
            text: render_tasks(&tasks),
        });
        self.notice = format!("Workspace tasks: {}", tasks.len());
        Ok(())
    }

    fn preview_worktree_snapshot(&mut self, repo_root: &Path) -> Result<()> {
        let runtime = local_result(LocalAgentRuntime::open(repo_root))?;
        let worktree = runtime.worktree_snapshot();
        self.worktree = worktree.clone();
        self.preview = Some(TuiPreview {
            path: "current git worktree".to_string(),
            text: render_worktree_snapshot(&worktree),
        });
        self.notice = format!(
            "Worktree: {} / {} changes",
            if worktree.clean { "clean" } else { "dirty" },
            worktree.changed_files.len()
        );
        Ok(())
    }

    fn cycle_workspace(&mut self, repo_root: &Path) -> Result<()> {
        if self.workspaces.is_empty() {
            self.notice = "No workspace registered".to_string();
            return Ok(());
        }
        let current = self
            .workspaces
            .iter()
            .position(|workspace| workspace.workspace_id == self.active_workspace_id)
            .unwrap_or_default();
        let next = (current + 1) % self.workspaces.len();
        self.active_workspace_id = self.workspaces[next].workspace_id.clone();
        self.refresh(repo_root)?;
        self.notice = format!("Workspace: {}", self.active_workspace_id);
        Ok(())
    }

    fn toggle_agent_mode(&mut self) {
        self.agent_mode = match &self.agent_mode {
            LocalAgentMode::Chat => LocalAgentMode::CodeAgent,
            _ => LocalAgentMode::Chat,
        };
        self.notice = format!("Agent mode: {}", agent_mode_label(&self.agent_mode));
    }

    fn move_selection(&mut self, offset: isize) {
        let item_count = self
            .runs
            .len()
            .max(self.knowledge_sources.len())
            .max(self.artifacts.len());
        if item_count == 0 {
            self.selected = 0;
            return;
        }
        let last = (item_count - 1) as isize;
        self.selected = (self.selected as isize + offset).clamp(0, last) as usize;
        self.notice.clear();
    }

    fn select_first(&mut self) {
        self.selected = 0;
        self.notice.clear();
    }

    fn select_last(&mut self) {
        let item_count = self
            .runs
            .len()
            .max(self.knowledge_sources.len())
            .max(self.artifacts.len());
        if let Some(last) = item_count.checked_sub(1) {
            self.selected = last;
        }
        self.notice.clear();
    }
}

fn tui_loop(repo_root: &Path, out: &mut impl Write) -> Result<()> {
    let snapshot = local_result(collect_snapshot(repo_root))?;
    let runtime = local_result(LocalAgentRuntime::open(repo_root))?;
    if local_result(runtime.list_workspaces(1))?.is_empty() {
        local_result(runtime.ensure_workspace(Some("default".to_string())))?;
    }
    let workspaces = local_result(runtime.list_workspaces(12))?;
    let active_workspace_id = workspaces
        .first()
        .map(|workspace| workspace.workspace_id.clone())
        .unwrap_or_else(|| "default".to_string());
    let runs = local_result(runtime.list_runs(Some(&active_workspace_id), 5))?;
    let run_evidence = collect_run_evidence(&runtime, &runs);
    let knowledge_sources = local_result(runtime.knowledge_sources(Some(&active_workspace_id), 5))?;
    let tasks = local_result(runtime.list_tasks(Some(&active_workspace_id), None, 8))?;
    let artifacts = local_result(runtime.list_artifacts(Some(&active_workspace_id), None, 5))?;
    let worktree = runtime.worktree_snapshot();
    let mut state = TuiState::new(TuiStateInit {
        snapshot,
        runs,
        run_evidence,
        workspaces,
        active_workspace_id,
        knowledge_sources,
        tasks,
        artifacts,
    });
    state.worktree = worktree;
    loop {
        draw_tui(out, &state)?;
        if event::poll(Duration::from_millis(500))? {
            match event::read()? {
                Event::Key(key) if key.kind == KeyEventKind::Press => match key.code {
                    _ if state.input.is_some() => state.handle_input_key(key, repo_root)?,
                    KeyCode::Char('q') | KeyCode::Esc => return Ok(()),
                    KeyCode::Char('r') => state.refresh(repo_root)?,
                    KeyCode::Char('m') => state.toggle_agent_mode(),
                    KeyCode::Char('w') => state.cycle_workspace(repo_root)?,
                    KeyCode::Char('o') => state.begin_input(TuiInputKind::WorkspaceId),
                    KeyCode::Char('c') => state.begin_input(TuiInputKind::AgentPrompt),
                    KeyCode::Char('f') => state.begin_input(TuiInputKind::FollowUpPrompt),
                    KeyCode::Char('F') => state.begin_input(TuiInputKind::SessionFollowUpPrompt),
                    KeyCode::Char('D') => state.begin_input(TuiInputKind::RunCheckpoint),
                    KeyCode::Char('s') => state.begin_input(TuiInputKind::KnowledgePath),
                    KeyCode::Char('R') => state.begin_input(TuiInputKind::SourceRating),
                    KeyCode::Char('!') => state.begin_input(TuiInputKind::LocalCommand),
                    KeyCode::Char('x') => state.remove_latest_knowledge(repo_root)?,
                    KeyCode::Char('n') => state.run_workspace_check(repo_root)?,
                    KeyCode::Char('N') => state.rerun_selected_prompt(repo_root)?,
                    KeyCode::Char('?') => state.preview_command_map(repo_root)?,
                    KeyCode::Char('A') => state.preview_agent_context(repo_root)?,
                    KeyCode::Char('H') => state.preview_local_doctor(repo_root)?,
                    KeyCode::Char('d') => state.preview_worktree_snapshot(repo_root)?,
                    KeyCode::Char('t') => state.preview_selected_run_transcript(repo_root)?,
                    KeyCode::Char('E') => state.preview_selected_run_events(repo_root)?,
                    KeyCode::Char('l') => state.preview_selected_run_plan(repo_root)?,
                    KeyCode::Char('O') => state.preview_selected_run_tool_trace(repo_root)?,
                    KeyCode::Char('C') => state.preview_selected_run_compact(repo_root)?,
                    KeyCode::Char('z') => state.preview_selected_run_core_trace(repo_root)?,
                    KeyCode::Char('b') => state.preview_selected_run_review(repo_root)?,
                    KeyCode::Char('i') => state.preview_selected_run_status(repo_root)?,
                    KeyCode::Char('p') => state.preview_latest_knowledge(repo_root)?,
                    KeyCode::Char('a') => state.preview_latest_artifact(repo_root)?,
                    KeyCode::Char('g') => state.preview_latest_proposal(repo_root)?,
                    KeyCode::Char('h') => state.review_latest_proposal_risk(repo_root)?,
                    KeyCode::Char('u') => state.preview_latest_proposal_apply(repo_root)?,
                    KeyCode::Char('y') => state.apply_pending_proposal(repo_root)?,
                    KeyCode::Char('Y') => state.rollback_latest_proposal(repo_root)?,
                    KeyCode::Char('v') => state.preview_parity_report(repo_root)?,
                    KeyCode::Char('e') => state.preview_evidence_bundle(repo_root)?,
                    KeyCode::Char('S') => state.preview_workspace_compact(repo_root)?,
                    KeyCode::Char('W') => state.preview_workspace_events(repo_root)?,
                    KeyCode::Char('U') => state.preview_workspace_usage(repo_root)?,
                    KeyCode::Char('T') => state.preview_workspace_tasks(repo_root)?,
                    KeyCode::Up | KeyCode::Char('k') => state.move_selection(-1),
                    KeyCode::Down | KeyCode::Char('j') => state.move_selection(1),
                    KeyCode::PageUp => state.move_selection(-5),
                    KeyCode::PageDown => state.move_selection(5),
                    KeyCode::Home => state.select_first(),
                    KeyCode::End => state.select_last(),
                    _ => {}
                },
                _ => {}
            }
        }
    }
}

fn draw_tui(out: &mut impl Write, state: &TuiState) -> Result<()> {
    let (cols, rows) = terminal::size().unwrap_or((100, 30));
    let width = usize::from(cols);
    execute!(out, Clear(ClearType::All), MoveTo(0, 0))?;
    execute!(
        out,
        SetAttribute(Attribute::Bold),
        Print("Structure Local"),
        SetAttribute(Attribute::Reset),
        SetForegroundColor(Color::DarkGrey),
        Print("  local goal mode"),
        ResetColor
    )?;

    write_at(out, 0, 2, "Mode: local only, no API server required", width)?;
    write_at(
        out,
        0,
        3,
        &format!("Repo: {}", state.snapshot.repo_root),
        width,
    )?;
    write_at(
        out,
        0,
        4,
        &format!("Runtime: {}", state.snapshot.runtime_dir),
        width,
    )?;
    write_at(
        out,
        0,
        5,
        &format!(
            "Core: {} surfaces, {} primitives, {} capabilities, {} local runs",
            state.snapshot.product_surfaces.len(),
            state.snapshot.core_manifest.primitives.len(),
            state.snapshot.core_manifest.capabilities.len(),
            state.runs.len()
        ),
        width,
    )?;
    write_at(
        out,
        0,
        6,
        &format!(
            "Workspace: {} ({} registered) | Agent: {} | LLM: {}",
            state.active_workspace_id,
            state.workspaces.len(),
            agent_mode_label(&state.agent_mode),
            llm_status_label(&state.snapshot.llm_config)
        ),
        width,
    )?;
    match &state.input {
        Some(input) => write_at(
            out,
            0,
            7,
            &format!("{}: {}", input.kind.title(), input.value),
            width,
        )?,
        None => muted_at(
            out,
            0,
            7,
            "? map  m mode  o workspace  c prompt  N rerun  A context  H doctor  f run-follow  F session-follow  D decision  i status  l plan  O tools  C compact  S session  W events  U usage  T tasks  z trace  b review  s knowledge  R rate  ! command",
            width,
        )?,
    }

    if rows < 16 {
        write_at(
            out,
            0,
            8,
            "Increase the terminal height to show reports and previews.",
            width,
        )?;
        draw_footer(out, rows, width, &state.notice)?;
        out.flush()?;
        return Ok(());
    }

    let body_top = 8;
    let footer_rows = 2;
    let body_height = rows.saturating_sub(body_top + footer_rows);
    if cols >= 104 {
        let report_width = (width.saturating_mul(46) / 100).clamp(42, 58);
        let preview_x = u16::try_from(report_width + 3).unwrap_or(cols.saturating_sub(1));
        let preview_width = width.saturating_sub(report_width + 4);
        draw_reports(out, state, 0, body_top, report_width, body_height)?;
        draw_preview(out, state, preview_x, body_top, preview_width, body_height)?;
    } else {
        let report_height =
            (body_height.saturating_mul(45) / 100).clamp(5, body_height.saturating_sub(4).max(5));
        draw_reports(out, state, 0, body_top, width, report_height)?;
        draw_preview(
            out,
            state,
            0,
            body_top + report_height + 1,
            width,
            body_height.saturating_sub(report_height + 1),
        )?;
    }
    draw_footer(out, rows, width, &state.notice)?;
    out.flush()?;
    Ok(())
}

fn agent_mode_label(mode: &LocalAgentMode) -> &'static str {
    match mode {
        LocalAgentMode::Chat => "chat",
        LocalAgentMode::CodeAgent => "code_agent",
    }
}

fn llm_status_label(config: &structure_local_core::LocalLlmConfigStatus) -> String {
    if config.configured {
        let model = config.model_name.as_deref().unwrap_or("configured model");
        format!(
            "OPENAI__ configured ({}/{}/{}, {model})",
            config.api_key.source, config.base_url.source, config.model.source
        )
    } else {
        format!(
            "missing OPENAI__ ({}/{}/{})",
            config.api_key.source, config.base_url.source, config.model.source
        )
    }
}

fn draw_reports(
    out: &mut impl Write,
    state: &TuiState,
    x: u16,
    y: u16,
    width: usize,
    height: u16,
) -> Result<()> {
    styled_at(
        out,
        x,
        y,
        "Local agent runs",
        width,
        Some(Attribute::Bold),
        None,
    )?;
    muted_at(
        out,
        x,
        y + 1,
        "press ? for command map, j/k to select, A context, H doctor, i status, t transcript, E events, l plan, O tools, C compact, D decision, S session, W workspace-events, U usage, T tasks, z trace, b review, f run-follow, F session-follow, N rerun, n local check",
        width,
    )?;
    muted_at(
        out,
        x,
        y + 2,
        &format!(
            "worktree: {} / {} changes on {}",
            if state.worktree.clean {
                "clean"
            } else {
                "dirty"
            },
            state.worktree.changed_files.len(),
            state.worktree.branch.as_deref().unwrap_or("unknown")
        ),
        width,
    )?;
    let run_rows = usize::from(height.saturating_sub(3))
        .min(3)
        .min(state.runs.len());
    for index in 0..run_rows {
        let run = &state.runs[index];
        let suffix = state
            .run_evidence
            .iter()
            .find(|evidence| evidence.run.run_id == run.run_id)
            .map(|evidence| {
                format!(
                    "  [{} events, {} tools, {} instr, {} worktree, {} sources, {} ratings, {} checkpoints, {} gc, {} artifacts]",
                    evidence.event_count,
                    evidence.tool_call_count,
                    evidence.agent_instruction_paths.len(),
                    evidence
                        .worktree
                        .as_ref()
                        .map(|worktree| worktree.changed_files.len())
                        .unwrap_or_default(),
                    evidence.knowledge_sources.len(),
                    evidence.source_ratings.len(),
                    evidence.checkpoints.len(),
                    evidence.event_gc.filtered_event_count,
                    evidence.artifacts.len()
                )
            })
            .unwrap_or_default();
        write_at(
            out,
            x,
            y + 3 + u16::try_from(index).unwrap_or_default(),
            &format!(
                "{}{}  {}  {}{}",
                if index == state.selected { "> " } else { "  " },
                run.status,
                run.workspace_id,
                run.prompt,
                suffix
            ),
            width,
        )?;
    }
    let reports_y = y + 4 + u16::try_from(run_rows).unwrap_or_default();
    if reports_y + 4 < y + height {
        styled_at(
            out,
            x,
            reports_y,
            "Workspace knowledge",
            width,
            Some(Attribute::Bold),
            None,
        )?;
        let knowledge_text = if state.knowledge_sources.is_empty() {
            "No registered knowledge sources".to_string()
        } else {
            state
                .knowledge_sources
                .iter()
                .take(2)
                .map(|source| format!("{} ({})", source.title, format_bytes(source.size_bytes)))
                .collect::<Vec<_>>()
                .join("  |  ")
        };
        muted_at(
            out,
            x,
            reports_y + 1,
            "press p to preview latest source, s to add, x to remove registration",
            width,
        )?;
        write_at(out, x, reports_y + 2, &knowledge_text, width)?;
    }
    let tasks_y = reports_y + 4;
    if tasks_y + 3 < y + height {
        styled_at(out, x, tasks_y, "Tasks", width, Some(Attribute::Bold), None)?;
        let task_text = if state.tasks.is_empty() {
            "No local tasks".to_string()
        } else {
            state
                .tasks
                .iter()
                .take(2)
                .map(|task| format!("{} [{}]", task.title, task.status))
                .collect::<Vec<_>>()
                .join("  |  ")
        };
        muted_at(
            out,
            x,
            tasks_y + 1,
            "press T to inspect workspace tasks",
            width,
        )?;
        write_at(out, x, tasks_y + 2, &task_text, width)?;
    }
    let artifacts_y = tasks_y + 4;
    if artifacts_y + 3 < y + height {
        styled_at(
            out,
            x,
            artifacts_y,
            "Artifacts",
            width,
            Some(Attribute::Bold),
            None,
        )?;
        let artifact_text = if state.artifacts.is_empty() {
            "No indexed artifacts".to_string()
        } else {
            state
                .artifacts
                .iter()
                .take(2)
                .map(|artifact| {
                    format!("{} ({})", artifact.kind, format_bytes(artifact.size_bytes))
                })
                .collect::<Vec<_>>()
                .join("  |  ")
        };
        muted_at(
            out,
            x,
            artifacts_y + 1,
            "press a for latest artifact, g selected proposal, h risk, u dry-run, y apply, Y rollback",
            width,
        )?;
        write_at(out, x, artifacts_y + 2, &artifact_text, width)?;
    }
    Ok(())
}

fn draw_preview(
    out: &mut impl Write,
    state: &TuiState,
    x: u16,
    y: u16,
    width: usize,
    height: u16,
) -> Result<()> {
    if height < 3 || width == 0 {
        return Ok(());
    }

    styled_at(out, x, y, "Preview", width, Some(Attribute::Bold), None)?;
    match &state.preview {
        Some(preview) => {
            muted_at(out, x, y + 1, &preview.path, width)?;
            let max_lines = usize::from(height.saturating_sub(3));
            for (line_index, line) in preview.text.lines().take(max_lines).enumerate() {
                write_at(
                    out,
                    x,
                    y + 3 + u16::try_from(line_index).unwrap_or_default(),
                    &clean_line(line),
                    width,
                )?;
            }
            if preview.text.lines().count() > max_lines {
                muted_at(out, x, y + height.saturating_sub(1), "...", width)?;
            }
        }
        None => {
            muted_at(out, x, y + 1, "No preview open.", width)?;
        }
    }
    Ok(())
}

fn draw_footer(out: &mut impl Write, rows: u16, width: usize, notice: &str) -> Result<()> {
    if rows == 0 {
        return Ok(());
    }
    let footer_y = rows.saturating_sub(1);
    let controls = "? map  q quit  r refresh  c prompt  N rerun  A context  H doctor  f run-follow  F session-follow  D decision  ! cmd  t transcript  W workspace-events  l plan  O tools";
    let status_width = width.saturating_sub(controls.len() + 2);
    write_at(out, 0, footer_y, controls, width)?;
    if status_width > 0 && !notice.is_empty() {
        muted_at(
            out,
            u16::try_from(width.saturating_sub(status_width)).unwrap_or_default(),
            footer_y,
            notice,
            status_width,
        )?;
    }
    Ok(())
}

fn write_at(out: &mut impl Write, x: u16, y: u16, value: &str, width: usize) -> Result<()> {
    if width == 0 {
        return Ok(());
    }
    execute!(out, MoveTo(x, y), Print(truncate(value, width)))?;
    Ok(())
}

fn muted_at(out: &mut impl Write, x: u16, y: u16, value: &str, width: usize) -> Result<()> {
    styled_at(out, x, y, value, width, None, Some(Color::DarkGrey))
}

fn styled_at(
    out: &mut impl Write,
    x: u16,
    y: u16,
    value: &str,
    width: usize,
    attribute: Option<Attribute>,
    color: Option<Color>,
) -> Result<()> {
    if width == 0 {
        return Ok(());
    }
    if let Some(attribute) = attribute {
        execute!(out, SetAttribute(attribute))?;
    }
    if let Some(color) = color {
        execute!(out, SetForegroundColor(color))?;
    }
    execute!(out, MoveTo(x, y), Print(truncate(value, width)))?;
    if color.is_some() {
        execute!(out, ResetColor)?;
    }
    if attribute.is_some() {
        execute!(out, SetAttribute(Attribute::Reset))?;
    }
    Ok(())
}

fn clean_line(value: &str) -> String {
    value
        .chars()
        .map(|character| {
            if character.is_control() && character != '\t' {
                ' '
            } else {
                character
            }
        })
        .collect::<String>()
        .replace('\t', "  ")
}

fn format_bytes(bytes: u64) -> String {
    const KIB: f64 = 1024.0;
    const MIB: f64 = KIB * 1024.0;
    if bytes < 1024 {
        format!("{bytes} B")
    } else if bytes < 1024 * 1024 {
        format!("{:.1} KiB", bytes as f64 / KIB)
    } else {
        format!("{:.1} MiB", bytes as f64 / MIB)
    }
}

fn truncate(value: &str, max_chars: usize) -> String {
    if max_chars == 0 {
        return String::new();
    }
    if value.chars().count() <= max_chars {
        return value.to_string();
    }
    if max_chars <= 3 {
        return ".".repeat(max_chars);
    }
    let mut out = value
        .chars()
        .take(max_chars.saturating_sub(3))
        .collect::<String>();
    out.push_str("...");
    out
}

fn compact_json(value: &serde_json::Value) -> String {
    let raw = serde_json::to_string(value).unwrap_or_else(|_| "null".to_string());
    truncate(&raw, 600)
}

fn local_result<T>(result: std::result::Result<T, String>) -> Result<T> {
    result.map_err(|message| anyhow!(message))
}

fn render_parity_report(report: &SurfaceParityReport) -> String {
    let mut text = String::new();
    text.push_str("Surface Parity\n");
    text.push_str(&format!("Schema: {}\n", report.schema_version));
    text.push_str(&format!("Passed: {}\n", report.passed));
    text.push_str(&format!("Surfaces: {}\n", report.surface_count));
    text.push_str(&format!("Primitives: {}\n", report.primitive_count));
    text.push_str(&format!("Capabilities: {}\n\n", report.capability_count));
    for check in &report.checks {
        text.push_str(&format!(
            "{} {}: {}\n",
            if check.passed { "OK" } else { "FAIL" },
            check.id,
            check.summary
        ));
        for detail in &check.details {
            text.push_str(&format!("  - {detail}\n"));
        }
    }
    text
}

fn render_tui_command_map(state: &TuiState) -> String {
    let selected_run = state
        .runs
        .get(state.selected)
        .map(|run| {
            format!(
                "{} / {} / {}",
                run.run_id,
                run.status,
                truncate(&run.prompt, 96)
            )
        })
        .unwrap_or_else(|| "none".to_string());
    let llm = if state.snapshot.llm_config.configured {
        format!(
            "OPENAI__ configured ({})",
            state
                .snapshot
                .llm_config
                .model_name
                .as_deref()
                .unwrap_or("model configured")
        )
    } else {
        "OPENAI__ missing".to_string()
    };
    let mut text = String::new();
    text.push_str("Structure TUI Command Map\n");
    text.push_str(&format!("Workspace: {}\n", state.active_workspace_id));
    text.push_str(&format!("Mode: {}\n", agent_mode_label(&state.agent_mode)));
    text.push_str(&format!("Selected run: {selected_run}\n"));
    text.push_str(&format!("LLM: {llm}\n"));
    text.push_str(&format!(
        "Core: {} surfaces / {} primitives / {} capabilities\n\n",
        state.snapshot.product_surfaces.len(),
        state.snapshot.core_manifest.primitives.len(),
        state.snapshot.core_manifest.capabilities.len()
    ));

    text.push_str("Run Loop\n");
    text.push_str("- c prompt -> LocalAgentRuntime::run_prompt_attempt -> Structure run events\n");
    text.push_str("- n local check -> same run loop with a workspace-inspection prompt\n");
    text.push_str("- N rerun -> fresh run from the selected persisted prompt\n");
    text.push_str("- f run-follow -> run_continuation_attempt from selected transcript/evidence\n");
    text.push_str("- F session-follow -> workspace_continuation_attempt from workspace compact\n");
    text.push_str("- m mode -> switch chat/code_agent without changing the runtime contract\n\n");

    text.push_str("Workspace Context\n");
    text.push_str("- o/w workspace -> create/open or cycle local workspace metadata\n");
    text.push_str("- A context -> LocalAgentContext before model planning\n");
    text.push_str("- H doctor -> OPENAI__ diagnostic + Core parity + Agent Context\n");
    text.push_str("- s/p/x knowledge -> register, preview, or remove path-addressed knowledge\n");
    text.push_str("- R rate -> source_rated feedback replayed into future context\n");
    text.push_str("- T tasks -> workspace/run-linked task events\n");
    text.push_str("- ! command -> allowlisted local tool recorded as workspace events\n\n");

    text.push_str("Run Evidence\n");
    text.push_str(
        "- i status -> latest event, model usage, tool counts, artifacts, next actions\n",
    );
    text.push_str("- t transcript -> shared run transcript and checkpoint notes\n");
    text.push_str("- E events -> complete immutable event stream for the selected run\n");
    text.push_str("- l plan -> event-derived agent progress view\n");
    text.push_str("- O tools -> paired tool_call_requested/tool_call_completed trace\n");
    text.push_str("- C compact -> continuation boundary for the selected run\n");
    text.push_str("- z trace -> Structure Core flow/primitive path\n");
    text.push_str("- b review -> post-run attempt review and next actions\n");
    text.push_str("- D decision -> run_checkpoint_recorded feedback event\n\n");

    text.push_str("Session And Proposals\n");
    text.push_str("- S session -> workspace compact handoff from replayed events\n");
    text.push_str("- W events -> cursor-based workspace event feed from the active workspace\n");
    text.push_str("- U usage -> workspace model/tool/task/artifact/Core totals\n");
    text.push_str("- e evidence -> local evidence bundle for automation and audits\n");
    text.push_str("- a artifact -> latest artifact preview\n");
    text.push_str("- g/h/u/y/Y proposal -> preview, risk-review, dry-run, apply, rollback\n\n");

    text.push_str("Invariant\n");
    text.push_str(
        "Every agent action above calls structure-local-runtime and writes or reads Structure Core-aligned events; inspect actions record command_turn_recorded workspace events. Benchmark adapters stay outside this TUI surface.\n",
    );
    text
}

fn render_local_doctor(
    snapshot: &LocalSnapshot,
    diagnostic: &LocalLlmDiagnostic,
    parity: &SurfaceParityReport,
    context: &LocalAgentContext,
) -> String {
    let llm_state = if diagnostic.ok {
        "ok"
    } else if diagnostic.configured {
        "failed"
    } else {
        "missing"
    };
    let mut text = String::new();
    text.push_str("Structure Local Doctor\n");
    text.push_str(&format!("Workspace: {}\n", context.workspace_id));
    text.push_str(&format!("Mode: {}\n", context.mode));
    text.push_str(&format!("Repository: {}\n", snapshot.repo_root));
    text.push_str(&format!("Runtime: {}\n", snapshot.runtime_dir));
    text.push_str(&format!("Database: {}\n", context.runtime_db));
    text.push_str(&format!(
        "Core: {} surfaces / {} primitives / {} capabilities\n",
        snapshot.product_surfaces.len(),
        snapshot.core_manifest.primitives.len(),
        snapshot.core_manifest.capabilities.len()
    ));
    text.push_str(&format!(
        "Parity: {} / {} checks\n",
        if parity.passed { "passed" } else { "failed" },
        parity.checks.len()
    ));
    text.push_str(&format!(
        "LLM: {} / provider={} / configured={}\n",
        llm_state, diagnostic.provider, diagnostic.configured
    ));
    text.push_str(&format!(
        "Model: {}\n",
        diagnostic.model.as_deref().unwrap_or("not set")
    ));
    text.push_str(&format!(
        "Endpoint: {}\n",
        diagnostic.endpoint.as_deref().unwrap_or("not set")
    ));
    text.push_str(&format!("Elapsed: {} ms\n", diagnostic.elapsed_ms));
    if let Some(response) = &diagnostic.response_preview {
        if !response.is_empty() {
            text.push_str(&format!("Response: {}\n", clean_line(response)));
        }
    }
    if let Some(error) = &diagnostic.error {
        text.push_str(&format!("Error: {error}\n"));
    }
    text.push_str(&format!(
        "Context: {} instructions / {} sources / {} ratings / {} tasks / {} turns\n",
        context.agent_instructions.len(),
        context.knowledge_sources.len(),
        context.source_ratings.len(),
        context.tasks.len(),
        context.recent_turns.len()
    ));
    text.push_str(&format!(
        "Worktree: {} / {} changes\n",
        if context.worktree.clean {
            "clean"
        } else {
            "dirty"
        },
        context.worktree.changed_files.len()
    ));

    text.push_str("\nChecks\n");
    text.push_str(&format!(
        "- OPENAI__: {}\n",
        if diagnostic.ok {
            "chat-completions request succeeded"
        } else if diagnostic.configured {
            "configured but diagnostic failed"
        } else {
            "OPENAI__API_KEY, OPENAI__BASE_URL, or OPENAI__MODEL missing"
        }
    ));
    text.push_str(&format!(
        "- Structure Core parity: {}\n",
        if parity.passed {
            "surface contract aligned"
        } else {
            "surface contract drift detected"
        }
    ));
    text.push_str(&format!(
        "- Agent context: {} knowledge source(s), {} active task(s), {} replayed turn(s)\n",
        context.knowledge_sources.len(),
        context
            .tasks
            .iter()
            .filter(|task| task.status != "done")
            .count(),
        context.recent_turns.len()
    ));

    if !parity.checks.is_empty() {
        text.push_str("\nParity Details\n");
        for check in &parity.checks {
            text.push_str(&format!(
                "- {}: {}\n",
                check.id,
                if check.passed { "ok" } else { "fail" }
            ));
        }
    }
    text
}

fn render_evidence_bundle(bundle: &LocalEvidenceBundle) -> String {
    let mut text = String::new();
    text.push_str("Local Evidence Bundle\n");
    text.push_str(&format!("Schema: {}\n", bundle.schema_version));
    text.push_str(&format!("Workspace: {}\n", bundle.workspace_id));
    text.push_str(&format!("Generated: {}\n", bundle.generated_at_ms));
    text.push_str(&format!("Parity passed: {}\n", bundle.parity_report.passed));
    text.push_str(&format!(
        "Events: {}\n",
        bundle.workspace_replay.events.len()
    ));
    text.push_str(&format!("Runs: {}\n", bundle.workspace_replay.runs.len()));
    text.push_str(&format!(
        "Knowledge sources: {}\n",
        bundle.workspace_replay.knowledge_sources.len()
    ));
    text.push_str(&format!("Tasks: {}\n", bundle.workspace_replay.tasks.len()));
    text.push_str(&format!(
        "Artifacts: {}\n",
        bundle.workspace_replay.artifacts.len()
    ));
    text.push_str(&format!(
        "Run evidence records: {}\n\n",
        bundle.run_evidence.len()
    ));

    text.push_str("\nRecent Run Evidence\n");
    if bundle.run_evidence.is_empty() {
        text.push_str("No run evidence recorded.\n");
    } else {
        for evidence in &bundle.run_evidence {
            text.push_str(&format!(
                "- {}: {} events, {} tools, {} instr, {} worktree, {} sources, {} ratings, {} checkpoints, {} gc-filtered, {} artifacts\n",
                evidence.run.run_id,
                evidence.event_count,
                evidence.tool_call_count,
                evidence.agent_instruction_paths.len(),
                evidence
                    .worktree
                    .as_ref()
                    .map(|worktree| worktree.changed_files.len())
                    .unwrap_or_default(),
                evidence.knowledge_sources.len(),
                evidence.source_ratings.len(),
                evidence.checkpoints.len(),
                evidence.event_gc.filtered_event_count,
                evidence.artifacts.len()
            ));
        }
    }
    text
}

fn render_worktree_snapshot(worktree: &WorktreeSnapshot) -> String {
    let mut text = String::new();
    text.push_str("Current Worktree\n");
    text.push_str(&format!("Available: {}\n", worktree.available));
    text.push_str(&format!(
        "Status: {}\n",
        if worktree.clean { "clean" } else { "dirty" }
    ));
    text.push_str(&format!(
        "Branch: {}\n",
        worktree.branch.as_deref().unwrap_or("unknown")
    ));
    text.push_str(&format!("Changes: {}\n", worktree.changed_files.len()));
    if let Some(error) = &worktree.error {
        text.push_str(&format!("Error: {error}\n"));
    }
    if !worktree.changed_files.is_empty() {
        text.push_str("\nChanged Files\n");
        for change in worktree.changed_files.iter().take(64) {
            text.push_str(&format!("- {} {}\n", change.status, change.path));
        }
    }
    text
}

fn render_agent_context(context: &LocalAgentContext) -> String {
    let mut text = String::new();
    text.push_str("Agent Context\n");
    text.push_str(&format!("Workspace: {}\n", context.workspace_id));
    text.push_str(&format!("Mode: {}\n", context.mode));
    text.push_str(&format!("Repository: {}\n", context.repo_root));
    text.push_str(&format!("Database: {}\n", context.runtime_db));
    text.push_str(&format!("Replay limit: {}\n", context.context_replay_limit));
    text.push_str(&format!(
        "Worktree: {} / {} changes\n",
        if context.worktree.clean {
            "clean"
        } else {
            "dirty"
        },
        context.worktree.changed_files.len()
    ));
    text.push_str(&format!(
        "Instructions: {}\n",
        context.agent_instructions.len()
    ));
    text.push_str(&format!(
        "Knowledge sources: {}\n",
        context.knowledge_sources.len()
    ));
    text.push_str(&format!(
        "Source ratings: {}\n",
        context.source_ratings.len()
    ));
    text.push_str(&format!("Tasks: {}\n", context.tasks.len()));
    text.push_str(&format!("Recent turns: {}\n", context.recent_turns.len()));
    if let Some(error) = &context.worktree.error {
        text.push_str(&format!("Worktree error: {error}\n"));
    }

    if !context.agent_instructions.is_empty() {
        text.push_str("\nAgent Instructions\n");
        for instruction in &context.agent_instructions {
            text.push_str(&format!(
                "- {} ({})\n",
                instruction.path,
                format_bytes(instruction.size_bytes)
            ));
            if !instruction.content_preview.is_empty() {
                text.push_str(&format!("  {}\n", clean_line(&instruction.content_preview)));
            }
        }
    }

    if !context.worktree.changed_files.is_empty() {
        text.push_str("\nWorktree Changes\n");
        for change in context.worktree.changed_files.iter().take(24) {
            text.push_str(&format!("- {} {}\n", change.status, change.path));
        }
    }

    if !context.knowledge_sources.is_empty() {
        text.push_str("\nKnowledge Sources\n");
        for source in &context.knowledge_sources {
            text.push_str(&format!(
                "- {} ({})\n  {}\n",
                source.title,
                format_bytes(source.size_bytes),
                source.path
            ));
        }
    }

    if !context.source_ratings.is_empty() {
        text.push_str("\nSource Ratings\n");
        for rating in context.source_ratings.iter().take(12) {
            let note = if rating.note.is_empty() {
                "no note".to_string()
            } else {
                rating.note.clone()
            };
            text.push_str(&format!(
                "- {}: {}/5 for {} ({})\n",
                rating.source_title,
                rating.rating,
                rating.run_id.as_deref().unwrap_or("workspace"),
                note
            ));
        }
    }

    if !context.tasks.is_empty() {
        text.push_str("\nWorkspace Tasks\n");
        for task in context.tasks.iter().take(12) {
            text.push_str(&format!(
                "- {} [{} / {}] {}\n",
                task.task_id, task.status, task.priority, task.title
            ));
        }
    }

    if !context.recent_turns.is_empty() {
        text.push_str("\nRecent Turns\n");
        for turn in context.recent_turns.iter().take(8) {
            text.push_str(&format!(
                "- {} [{} / {} events] {}\n",
                turn.run_id, turn.status, turn.event_count, turn.user_message
            ));
        }
    }
    text
}

fn render_run_checkpoint(checkpoint: &structure_local_runtime::RunCheckpoint) -> String {
    let mut text = String::new();
    text.push_str("Run Checkpoint\n");
    text.push_str(&format!("ID: {}\n", checkpoint.checkpoint_id));
    text.push_str(&format!("Run: {}\n", checkpoint.run_id));
    text.push_str(&format!("Workspace: {}\n", checkpoint.workspace_id));
    text.push_str(&format!("Created: {}\n\n", checkpoint.created_at_ms));
    text.push_str("Human Decision\n");
    text.push_str(&checkpoint.note);
    text.push('\n');
    text
}

fn render_run_events(run: &RunSummary, events: &[LocalEvent]) -> String {
    let mut text = String::new();
    text.push_str("Run Events\n");
    text.push_str(&format!("Run: {}\n", run.run_id));
    text.push_str(&format!("Workspace: {}\n", run.workspace_id));
    text.push_str(&format!("Status: {}\n", run.status));
    text.push_str(&format!("Events: {}\n\n", events.len()));
    for event in events {
        text.push_str(&format!(
            "#{} {} / {} / {}\n",
            event.sequence, event.kind, event.canonical_flow_id, event.primitive_id
        ));
        text.push_str(&format!("{}\n\n", compact_json(&event.payload)));
    }
    text
}

fn render_workspace_event_feed(feed: &WorkspaceEventFeed) -> String {
    let mut text = String::new();
    text.push_str("Workspace Events\n");
    text.push_str(&format!("Workspace: {}\n", feed.workspace_id));
    text.push_str(&format!("After sequence: {}\n", feed.after_sequence));
    text.push_str(&format!(
        "Last sequence: {}\n",
        feed.last_sequence
            .map(|sequence| sequence.to_string())
            .unwrap_or_else(|| "none".to_string())
    ));
    text.push_str(&format!(
        "Next after sequence: {}\n",
        feed.next_after_sequence
    ));
    text.push_str(&format!("Events: {}\n\n", feed.events.len()));
    for event in &feed.events {
        text.push_str(&format!(
            "#{} {} / run={} / {} / {}\n  {}\n",
            event.sequence,
            event.kind,
            event.run_id.as_deref().unwrap_or("workspace"),
            event.canonical_flow_id,
            event.primitive_id,
            compact_json(&event.payload)
        ));
    }
    text
}

fn render_run_transcript(transcript: &RunTranscript) -> String {
    let mut text = String::new();
    text.push_str("Run Transcript\n");
    text.push_str(&format!("Run: {}\n", transcript.run.run_id));
    text.push_str(&format!("Workspace: {}\n", transcript.run.workspace_id));
    text.push_str(&format!("Status: {}\n", transcript.run.status));
    text.push_str(&format!("Events: {}\n", transcript.events.len()));
    text.push_str(&format!(
        "Flows: {}\n",
        transcript.evidence.canonical_flow_ids.join(", ")
    ));
    text.push_str(&format!(
        "Primitives: {}\n",
        transcript.evidence.primitive_ids.join(", ")
    ));
    text.push_str(&format!(
        "Core aligned: {} / schema {}\n",
        transcript.evidence.core_trace.core_aligned,
        transcript.evidence.core_trace.manifest_schema_version
    ));
    text.push_str(&format!("Tools: {}\n", transcript.evidence.tool_call_count));
    text.push_str(&format!(
        "Model: {} requests / {} responses / {} network\n",
        transcript.evidence.model_usage.model_request_count,
        transcript.evidence.model_usage.model_response_count,
        transcript.evidence.model_usage.network_request_count
    ));
    if transcript.evidence.model_usage.total_tokens > 0 {
        text.push_str(&format!(
            "Tokens: {} prompt / {} completion / {} total\n",
            transcript.evidence.model_usage.prompt_tokens,
            transcript.evidence.model_usage.completion_tokens,
            transcript.evidence.model_usage.total_tokens
        ));
    }
    text.push_str(&format!(
        "Instructions: {}\n",
        transcript.evidence.agent_instruction_paths.len()
    ));
    if let Some(worktree) = &transcript.evidence.worktree {
        text.push_str(&format!(
            "Worktree: {} / {} changes\n",
            if worktree.clean { "clean" } else { "dirty" },
            worktree.changed_files.len()
        ));
    }
    text.push_str(&format!(
        "Artifacts: {}\n\n",
        transcript.evidence.artifact_paths.len()
    ));

    if !transcript.evidence.checkpoints.is_empty() {
        text.push_str("Run Checkpoints\n");
        for checkpoint in &transcript.evidence.checkpoints {
            text.push_str(&format!(
                "- {}: {}\n",
                checkpoint.checkpoint_id, checkpoint.note
            ));
        }
        text.push('\n');
    }

    if let Some(worktree) = &transcript.evidence.worktree {
        if !worktree.changed_files.is_empty() {
            text.push_str("Worktree Changes\n");
            for change in worktree.changed_files.iter().take(16) {
                text.push_str(&format!("- {} {}\n", change.status, change.path));
            }
            text.push('\n');
        }
    }

    if !transcript.evidence.agent_instruction_paths.is_empty() {
        text.push_str("Agent Instructions\n");
        for path in &transcript.evidence.agent_instruction_paths {
            text.push_str(&format!("- {path}\n"));
        }
        text.push('\n');
    }

    if let Some(turn) = &transcript.chat_turn {
        text.push_str("User\n");
        text.push_str(&turn.user_message);
        text.push_str("\n\nStructure\n");
        text.push_str(
            turn.assistant_message
                .as_deref()
                .unwrap_or("No assistant message recorded."),
        );
        text.push_str("\n\n");
    }

    if let Some(response) = &transcript.final_response {
        text.push_str("Final Response\n");
        text.push_str(response);
        text.push_str("\n\n");
    }

    text.push_str("Events\n");
    for event in transcript.events.iter().rev().take(18).rev() {
        text.push_str(&format!(
            "#{} {} / {} / {}\n",
            event.sequence, event.kind, event.canonical_flow_id, event.primitive_id
        ));
    }
    text
}

fn render_run_core_trace(trace: &LocalRunCoreTrace) -> String {
    let mut text = String::new();
    text.push_str("Structure Core Trace\n");
    text.push_str(&format!("Run: {}\n", trace.run.run_id));
    text.push_str(&format!("Workspace: {}\n", trace.run.workspace_id));
    text.push_str(&format!("Status: {}\n", trace.run.status));
    text.push_str(&format!("Schema: {}\n", trace.manifest_schema_version));
    text.push_str(&format!("Core aligned: {}\n", trace.core_aligned));
    text.push_str(&format!("Events: {}\n", trace.event_count));
    text.push_str(&format!(
        "Flow path: {}\n",
        if trace.flow_path.is_empty() {
            "none".to_string()
        } else {
            trace.flow_path.join(" -> ")
        }
    ));
    text.push_str(&format!(
        "Primitive path: {}\n\n",
        if trace.primitive_path.is_empty() {
            "none".to_string()
        } else {
            trace.primitive_path.join(" -> ")
        }
    ));

    text.push_str("Steps\n");
    for step in trace.steps.iter().rev().take(24).rev() {
        text.push_str(&format!(
            "#{} {} / {} / {}\n  {}\n",
            step.sequence,
            step.kind,
            step.canonical_flow_id,
            step.primitive_id,
            step.payload_summary
        ));
    }
    text
}

fn render_tool_trace(run: &RunSummary, trace: &[LocalToolTraceEntry]) -> String {
    let mut text = String::new();
    text.push_str("Tool Trace\n");
    text.push_str(&format!("Run: {}\n", run.run_id));
    text.push_str(&format!("Workspace: {}\n", run.workspace_id));
    text.push_str(&format!("Status: {}\n", run.status));
    text.push_str(&format!("Tool calls: {}\n\n", trace.len()));
    if trace.is_empty() {
        text.push_str("No tool calls recorded.\n");
        return text;
    }
    for entry in trace {
        let status = match entry.success {
            Some(true) => "ok",
            Some(false) => "failed",
            None => "pending",
        };
        text.push_str(&format!(
            "{} / {} / req={} / done={} / {}\n",
            entry.call_id,
            entry.name,
            entry
                .requested_sequence
                .map(|sequence| sequence.to_string())
                .unwrap_or_else(|| "none".to_string()),
            entry
                .completed_sequence
                .map(|sequence| sequence.to_string())
                .unwrap_or_else(|| "none".to_string()),
            status
        ));
        text.push_str(&format!("  input: {}\n", compact_json(&entry.input)));
        if let Some(output) = &entry.output {
            text.push_str(&format!("  output: {}\n", compact_json(output)));
        }
        if let Some(error) = &entry.error {
            text.push_str(&format!("  error: {error}\n"));
        }
    }
    text
}

fn render_run_plan(plan: &LocalRunPlan) -> String {
    let mut text = String::new();
    text.push_str("Run Plan\n");
    text.push_str(&format!("Run: {}\n", plan.run.run_id));
    text.push_str(&format!("Workspace: {}\n", plan.run.workspace_id));
    text.push_str(&format!("Status: {}\n", plan.status));
    text.push_str(&format!(
        "Steps: {} total / {} completed\n",
        plan.step_count, plan.completed_step_count
    ));
    text.push_str(&format!("Model: {} requests\n", plan.model_request_count));
    text.push_str(&format!("Tools: {} requested\n\n", plan.tool_call_count));

    text.push_str("Steps\n");
    for step in plan.steps.iter().rev().take(24).rev() {
        let iteration = step
            .iteration
            .map(|value| format!(" iter {value}"))
            .unwrap_or_default();
        text.push_str(&format!(
            "#{} [{}]{} {}\n",
            step.sequence, step.status, iteration, step.title
        ));
        text.push_str(&format!(
            "  flow={} primitive={} tools={} total_results={}\n",
            step.canonical_flow_id,
            step.primitive_id,
            step.tool_call_count,
            step.total_tool_results
        ));
        if !step.prompt_references.is_empty() {
            text.push_str(&format!(
                "  refs: @{}\n",
                step.prompt_references.join(", @")
            ));
        }
    }
    text
}

fn render_run_compact(compact: &LocalRunCompact) -> String {
    let mut text = String::new();
    text.push_str("Run Compact\n");
    text.push_str(&format!("Run: {}\n", compact.run.run_id));
    text.push_str(&format!("Workspace: {}\n", compact.run.workspace_id));
    text.push_str(&format!("Status: {}\n", compact.status));
    text.push_str(&format!("Core aligned: {}\n", compact.core_aligned));
    text.push_str(&format!("Events: {}\n", compact.event_count));
    text.push_str(&format!("Tools: {}\n", compact.tool_call_count));
    text.push_str(&format!(
        "Model: {} req / {} resp / {} net\n",
        compact.model_usage.model_request_count,
        compact.model_usage.model_response_count,
        compact.model_usage.network_request_count
    ));
    if compact.model_usage.total_tokens > 0 {
        text.push_str(&format!(
            "Tokens: {} prompt / {} completion / {} total\n",
            compact.model_usage.prompt_tokens,
            compact.model_usage.completion_tokens,
            compact.model_usage.total_tokens
        ));
    }
    text.push_str("\nSummary\n");
    text.push_str(&compact.summary);
    text.push('\n');
    if !compact.carry_forward_items.is_empty() {
        text.push_str("\nCarry Forward\n");
        for item in &compact.carry_forward_items {
            text.push_str(&format!("- {item}\n"));
        }
    }
    if !compact.next_actions.is_empty() {
        text.push_str("\nNext Actions\n");
        for action in &compact.next_actions {
            text.push_str(&format!("- {action}\n"));
        }
    }
    text.push_str("\nContinuation Context\n");
    text.push_str(&compact.continuation_context);
    text
}

fn render_run_review(review: &LocalRunReview) -> String {
    let mut text = String::new();
    text.push_str("Run Review\n");
    text.push_str(&format!("Run: {}\n", review.run.run_id));
    text.push_str(&format!("Workspace: {}\n", review.run.workspace_id));
    text.push_str(&format!("Status: {}\n", review.status));
    text.push_str(&format!("Core aligned: {}\n", review.core_aligned));
    text.push_str(&format!("Events: {}\n", review.event_count));
    text.push_str(&format!(
        "Tools: {} total / {} failed\n",
        review.tool_call_count, review.failed_tool_call_count
    ));
    text.push_str(&format!(
        "Model: {} requests / {} responses / {} network\n",
        review.model_usage.model_request_count,
        review.model_usage.model_response_count,
        review.model_usage.network_request_count
    ));
    text.push_str(&format!(
        "Tokens: {} prompt / {} completion / {} total\n",
        review.model_usage.prompt_tokens,
        review.model_usage.completion_tokens,
        review.model_usage.total_tokens
    ));
    text.push_str(&format!(
        "Flow path: {}\n",
        if review.flow_path.is_empty() {
            "none".to_string()
        } else {
            review.flow_path.join(" -> ")
        }
    ));
    text.push_str(&format!(
        "Primitive path: {}\n",
        if review.primitive_path.is_empty() {
            "none".to_string()
        } else {
            review.primitive_path.join(" -> ")
        }
    ));
    if let Some(artifact) = &review.response_artifact {
        text.push_str(&format!("Response artifact: {}\n", artifact.path));
    }
    if let Some(artifact) = &review.proposal_artifact {
        text.push_str(&format!(
            "Proposal artifact: {} / {}\n",
            artifact.artifact_id, artifact.path
        ));
    }
    text.push_str("\nNext Actions\n");
    for action in &review.next_actions {
        text.push_str(&format!("- {action}\n"));
    }
    text
}

fn render_run_status_snapshot(status: &LocalRunStatusSnapshot) -> String {
    let mut text = String::new();
    text.push_str("Run Status\n");
    text.push_str(&format!("Run: {}\n", status.run.run_id));
    text.push_str(&format!("Workspace: {}\n", status.run.workspace_id));
    text.push_str(&format!("Status: {}\n", status.run.status));
    text.push_str(&format!("Terminal: {}\n", status.terminal));
    text.push_str(&format!("Core aligned: {}\n", status.core_aligned));
    text.push_str(&format!("Events: {}\n", status.event_count));
    text.push_str(&format!(
        "Tools: {} total / {} failed / {} pending\n",
        status.tool_call_count, status.failed_tool_call_count, status.pending_tool_call_count
    ));
    text.push_str(&format!(
        "Model: {} requests / {} responses / {} network\n",
        status.model_usage.model_request_count,
        status.model_usage.model_response_count,
        status.model_usage.network_request_count
    ));
    text.push_str(&format!(
        "Tokens: {} prompt / {} completion / {} total\n",
        status.model_usage.prompt_tokens,
        status.model_usage.completion_tokens,
        status.model_usage.total_tokens
    ));
    text.push_str(&format!("Artifacts: {}\n", status.artifact_count));
    if let Some(event) = &status.latest_event {
        text.push_str(&format!(
            "Latest: #{} {} / {} / {}\n{}\n",
            event.sequence, event.kind, event.canonical_flow_id, event.primitive_id, event.summary
        ));
    }
    if let Some(error) = &status.latest_error {
        text.push_str(&format!("Error: {error}\n"));
    }
    if let Some(artifact) = &status.response_artifact {
        text.push_str(&format!("Response artifact: {}\n", artifact.path));
    }
    if let Some(artifact) = &status.proposal_artifact {
        text.push_str(&format!(
            "Proposal artifact: {} / {}\n",
            artifact.artifact_id, artifact.path
        ));
    }
    text.push_str(&format!(
        "Flow path: {}\n",
        if status.flow_path.is_empty() {
            "none".to_string()
        } else {
            status.flow_path.join(" -> ")
        }
    ));
    text.push_str(&format!(
        "Primitive path: {}\n",
        if status.primitive_path.is_empty() {
            "none".to_string()
        } else {
            status.primitive_path.join(" -> ")
        }
    ));
    if !status.next_actions.is_empty() {
        text.push_str("\nNext Actions\n");
        for action in &status.next_actions {
            text.push_str(&format!("- {action}\n"));
        }
    }
    text
}

fn render_workspace_compact(compact: &WorkspaceCompact) -> String {
    let mut text = String::new();
    text.push_str("Workspace Compact\n");
    text.push_str(&format!("Workspace: {}\n", compact.workspace_id));
    text.push_str(&format!("Events: {}\n", compact.event_count));
    text.push_str(&format!("Runs: {}\n", compact.run_count));
    text.push_str(&format!("Knowledge: {}\n", compact.knowledge_source_count));
    text.push_str(&format!("Artifacts: {}\n", compact.artifact_count));
    text.push_str(&format!("Core aligned: {}\n", compact.core_aligned));
    if let Some(sequence) = compact.last_sequence {
        text.push_str(&format!("Last sequence: {sequence}\n"));
    }
    text.push_str("\nSummary\n");
    text.push_str(&compact.summary);
    text.push('\n');
    if !compact.recent_runs.is_empty() {
        text.push_str("\nRecent Runs\n");
        for run in compact.recent_runs.iter().take(12) {
            text.push_str(&format!(
                "- {} / {} / events={} / tools={}\n",
                run.run_id, run.status, run.event_count, run.tool_call_count
            ));
            text.push_str(&format!("  {}\n", run.prompt_summary));
        }
    }
    if !compact.carry_forward_items.is_empty() {
        text.push_str("\nCarry Forward\n");
        for item in &compact.carry_forward_items {
            text.push_str(&format!("- {item}\n"));
        }
    }
    if !compact.next_actions.is_empty() {
        text.push_str("\nNext Actions\n");
        for action in &compact.next_actions {
            text.push_str(&format!("- {action}\n"));
        }
    }
    text.push_str("\nContinuation Context\n");
    text.push_str(&compact.continuation_context);
    text
}

fn render_workspace_usage(usage: &WorkspaceUsageSummary) -> String {
    let mut text = String::new();
    text.push_str("Workspace Usage\n");
    text.push_str(&format!("Workspace: {}\n", usage.workspace_id));
    text.push_str(&format!("Runs: {}\n", usage.run_count));
    text.push_str(&format!("Events: {}\n", usage.event_count));
    text.push_str(&format!("Tools: {}\n", usage.tool_call_count));
    text.push_str(&format!("Artifacts: {}\n", usage.artifact_count));
    text.push_str(&format!("Knowledge: {}\n", usage.knowledge_source_count));
    text.push_str(&format!("Core aligned: {}\n", usage.core_aligned));
    text.push_str(&format!(
        "Model: {} requests / {} responses / {} network\n",
        usage.model_usage.model_request_count,
        usage.model_usage.model_response_count,
        usage.model_usage.network_request_count
    ));
    text.push_str(&format!(
        "Tokens: prompt={} completion={} total={}\n",
        usage.model_usage.prompt_tokens,
        usage.model_usage.completion_tokens,
        usage.model_usage.total_tokens
    ));
    text.push_str(&format!(
        "Response chars: {}\n",
        usage.model_usage.response_chars
    ));
    text.push_str(&format!(
        "Flow path: {}\n",
        if usage.flow_path.is_empty() {
            "none".to_string()
        } else {
            usage.flow_path.join(" -> ")
        }
    ));
    text.push_str(&format!(
        "Primitive path: {}\n\n",
        if usage.primitive_path.is_empty() {
            "none".to_string()
        } else {
            usage.primitive_path.join(" -> ")
        }
    ));
    text.push_str(&usage.summary);
    text.push_str("\n\nRecent Runs\n");
    for run in usage.runs.iter().take(20) {
        text.push_str(&format!(
            "- {} / {} / events={} / tools={} / tokens={} / artifacts={} / core={}\n",
            run.run_id,
            run.status,
            run.event_count,
            run.tool_call_count,
            run.model_usage.total_tokens,
            run.artifact_count,
            run.core_aligned
        ));
    }
    text
}

fn render_tasks(tasks: &[LocalTaskRecord]) -> String {
    let mut text = String::new();
    text.push_str("Workspace Tasks\n");
    if tasks.is_empty() {
        text.push_str("No local tasks recorded.\n");
        return text;
    }
    let todo = tasks.iter().filter(|task| task.status == "todo").count();
    let in_progress = tasks
        .iter()
        .filter(|task| task.status == "in_progress")
        .count();
    let done = tasks.iter().filter(|task| task.status == "done").count();
    text.push_str(&format!(
        "Total: {} / todo={} / in_progress={} / done={}\n\n",
        tasks.len(),
        todo,
        in_progress,
        done
    ));
    for task in tasks {
        text.push_str(&format!(
            "- {} [{} / {}] {}\n",
            task.task_id, task.status, task.priority, task.title
        ));
        text.push_str(&format!(
            "  workspace={} run={}\n",
            task.workspace_id,
            task.run_id.as_deref().unwrap_or("workspace")
        ));
    }
    text
}

fn render_run_attempt(attempt: &RunAttempt) -> String {
    let mut text = String::new();
    text.push_str("Run Attempt\n");
    text.push_str(&format!("Run: {}\n", attempt.run.run_id));
    text.push_str(&format!("Workspace: {}\n", attempt.run.workspace_id));
    text.push_str(&format!("Status: {}\n", attempt.run.status));
    text.push_str(&format!("Events: {}\n", attempt.events.len()));
    if let Some(error) = &attempt.error {
        text.push_str(&format!("Error: {error}\n"));
    }
    if let Some(result) = &attempt.result {
        text.push_str(&format!("Artifact: {}\n", result.artifact_path));
        text.push_str(&format!(
            "Response chars: {}\n\n",
            result.final_response.chars().count()
        ));
        text.push_str(&result.final_response);
        text.push_str("\n\n");
    }
    text.push_str("Events\n");
    for event in attempt.events.iter().rev().take(18).rev() {
        text.push_str(&format!(
            "#{} {} / {} / {}\n",
            event.sequence, event.kind, event.canonical_flow_id, event.primitive_id
        ));
    }
    text
}

fn render_proposal_apply_result(result: &ProposalApplyResult) -> String {
    let mut text = String::new();
    text.push_str("Proposal Apply\n");
    text.push_str(&format!("Artifact: {}\n", result.artifact.artifact_id));
    text.push_str(&format!("Run: {}\n", result.artifact.run_id));
    text.push_str(&format!("Workspace: {}\n", result.artifact.workspace_id));
    text.push_str(&format!("Target: {}\n", result.target_path));
    text.push_str(&format!("Applied: {}\n", result.applied));
    text.push_str(&format!("Dry run: {}\n", result.dry_run));
    text.push_str(&format!("Added lines: {}\n", result.added_lines));
    text.push_str(&format!("Bytes written: {}\n\n", result.bytes_written));
    if result.dry_run {
        text.push_str("Review this preview, then press y to apply.\n\n");
    } else {
        text.push_str("The apply action was recorded as code_change_applied.\n\n");
    }
    text.push_str(&result.preview);
    text
}

fn render_proposal_rollback_result(result: &ProposalRollbackResult) -> String {
    let mut text = String::new();
    text.push_str("Proposal Rollback\n");
    text.push_str(&format!("Artifact: {}\n", result.artifact.artifact_id));
    text.push_str(&format!("Run: {}\n", result.artifact.run_id));
    text.push_str(&format!("Workspace: {}\n", result.artifact.workspace_id));
    text.push_str(&format!("Target: {}\n", result.target_path));
    text.push_str(&format!(
        "Backup: {} / {}\n",
        result.backup_artifact.artifact_id, result.backup_artifact.path
    ));
    text.push_str(&format!("Restored: {}\n", result.restored));
    text.push_str(&format!("Target existed: {}\n", result.target_existed));
    text.push_str(&format!("Bytes written: {}\n\n", result.bytes_written));
    text.push_str("The rollback action was recorded as code_change_reverted.\n\n");
    text.push_str(&result.preview);
    text
}

fn render_proposal_review(review: &ProposalReview) -> String {
    let mut text = String::new();
    text.push_str("Proposal Risk Review\n");
    text.push_str(&format!("Artifact: {}\n", review.artifact.artifact_id));
    text.push_str(&format!("Run: {}\n", review.artifact.run_id));
    text.push_str(&format!("Workspace: {}\n", review.artifact.workspace_id));
    text.push_str(&format!("Target: {}\n", review.target_path));
    text.push_str(&format!("Risk: {}\n", review.risk_level));
    text.push_str(&format!("Can apply: {}\n", review.can_apply));
    text.push_str("Dry-run: required\n");
    text.push_str(&format!("New file: {}\n", review.new_file));
    text.push_str(&format!("Target exists: {}\n", review.target_exists));
    text.push_str(&format!("Hunks: {}\n", review.hunk_count));
    text.push_str(&format!(
        "Lines: {} added / {} removed\n\n",
        review.added_lines, review.removed_lines
    ));
    text.push_str("Checks\n");
    for check in &review.checks {
        text.push_str(&format!(
            "[{}] {} - {}\n",
            check.status, check.id, check.message
        ));
    }
    text
}

fn render_local_command_result(result: &structure_local_runtime::LocalToolResult) -> String {
    let mut text = String::new();
    text.push_str("Local Command\n");
    text.push_str(&format!("Tool success: {}\n", result.success));
    if let Some(error) = &result.error {
        text.push_str(&format!("Error: {error}\n"));
        return text;
    }

    let argv = result
        .output
        .get("argv")
        .and_then(serde_json::Value::as_array)
        .map(|items| {
            items
                .iter()
                .filter_map(serde_json::Value::as_str)
                .collect::<Vec<_>>()
                .join(" ")
        })
        .unwrap_or_else(|| "<unknown command>".to_string());
    let cwd = result
        .output
        .get("cwd")
        .and_then(serde_json::Value::as_str)
        .unwrap_or(".");
    let exit_code = result
        .output
        .get("exit_code")
        .and_then(serde_json::Value::as_i64)
        .map(|value| value.to_string())
        .unwrap_or_else(|| "signal".to_string());
    let passed = result
        .output
        .get("success")
        .and_then(serde_json::Value::as_bool)
        .unwrap_or(false);
    let timed_out = result
        .output
        .get("timed_out")
        .and_then(serde_json::Value::as_bool)
        .unwrap_or(false);
    let duration_ms = result
        .output
        .get("duration_ms")
        .and_then(serde_json::Value::as_u64)
        .unwrap_or_default();
    let stdout = result
        .output
        .get("stdout")
        .and_then(serde_json::Value::as_str)
        .unwrap_or("");
    let stderr = result
        .output
        .get("stderr")
        .and_then(serde_json::Value::as_str)
        .unwrap_or("");
    let stdout_truncated = result
        .output
        .get("stdout_truncated")
        .and_then(serde_json::Value::as_bool)
        .unwrap_or(false);
    let stderr_truncated = result
        .output
        .get("stderr_truncated")
        .and_then(serde_json::Value::as_bool)
        .unwrap_or(false);

    text.push_str(&format!("$ {argv}\n"));
    text.push_str(&format!("Cwd: {cwd}\n"));
    text.push_str(&format!("Passed: {passed}\n"));
    text.push_str(&format!("Exit: {exit_code}\n"));
    text.push_str(&format!("Timed out: {timed_out}\n"));
    text.push_str(&format!("Duration: {duration_ms} ms\n"));
    text.push_str(&format!("Stdout truncated: {stdout_truncated}\n"));
    text.push_str(&format!("Stderr truncated: {stderr_truncated}\n\n"));
    if stdout.is_empty() {
        text.push_str("Stdout: <empty>\n");
    } else {
        text.push_str("Stdout:\n");
        text.push_str(stdout);
        text.push('\n');
    }
    if stderr.is_empty() {
        text.push_str("\nStderr: <empty>\n");
    } else {
        text.push_str("\nStderr:\n");
        text.push_str(stderr);
        text.push('\n');
    }
    text
}

fn collect_run_evidence(
    runtime: &LocalAgentRuntime,
    runs: &[RunSummary],
) -> Vec<RunEvidenceSummary> {
    runs.iter()
        .filter_map(|run| runtime.run_evidence_summary(&run.run_id).ok())
        .collect()
}

fn parse_source_rating_input(
    input: &str,
    default_source: Option<&KnowledgeSource>,
) -> Option<(String, u8, String)> {
    let mut parts = input.split_whitespace().collect::<Vec<_>>();
    if parts.is_empty() {
        return None;
    }
    if let Ok(rating) = parts[0].parse::<u8>() {
        if !(1..=5).contains(&rating) {
            return None;
        }
        let source_id = default_source?.source_id.clone();
        let note = parts.split_off(1).join(" ");
        return Some((source_id, rating, note));
    }

    let source_id = parts.remove(0).to_string();
    let rating = parts.first()?.parse::<u8>().ok()?;
    if !(1..=5).contains(&rating) {
        return None;
    }
    let note = parts.into_iter().skip(1).collect::<Vec<_>>().join(" ");
    Some((source_id, rating, note))
}

fn unavailable_worktree_snapshot() -> WorktreeSnapshot {
    WorktreeSnapshot {
        available: false,
        clean: true,
        branch: None,
        changed_files: Vec::new(),
        error: Some("worktree snapshot has not been loaded".to_string()),
    }
}

fn select_proposal_artifact<'a>(
    artifacts: &'a [ArtifactRecord],
    selected_run_id: Option<&str>,
) -> Option<&'a ArtifactRecord> {
    if let Some(run_id) = selected_run_id {
        if let Some(artifact) = artifacts
            .iter()
            .find(|artifact| artifact.kind == "code_change_proposal" && artifact.run_id == run_id)
        {
            return Some(artifact);
        }
    }
    artifacts
        .iter()
        .find(|artifact| artifact.kind == "code_change_proposal")
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::path::PathBuf;
    use std::sync::Mutex;
    use std::time::{SystemTime, UNIX_EPOCH};

    static ENV_LOCK: Mutex<()> = Mutex::new(());

    #[test]
    fn tui_local_command_records_workspace_tool_events() {
        let root = unique_repo("tui-local-command-events");
        let snapshot = local_result(collect_snapshot(&root)).unwrap();
        let mut state = TuiState::new(TuiStateInit {
            snapshot,
            runs: Vec::new(),
            run_evidence: Vec::new(),
            workspaces: Vec::new(),
            active_workspace_id: "tui-events".to_string(),
            knowledge_sources: Vec::new(),
            tasks: Vec::new(),
            artifacts: Vec::new(),
        });

        state.run_local_command(&root, "pwd".to_string()).unwrap();

        let runtime = local_result(LocalAgentRuntime::open(&root)).unwrap();
        let feed =
            local_result(runtime.workspace_event_feed(Some("tui-events"), Some(0), 10)).unwrap();

        assert!(state.notice.contains("Local command passed"));
        assert!(state
            .preview
            .as_ref()
            .is_some_and(|preview| preview.text.contains("Local Command")));
        assert_eq!(feed.events.len(), 2);
        assert_eq!(feed.events[0].kind, "tool_call_requested");
        assert_eq!(feed.events[0].run_id, None);
        assert_eq!(feed.events[0].payload["surface"], "tui");
        assert_eq!(feed.events[0].payload["name"], "run_local_command");
        assert_eq!(feed.events[1].kind, "tool_call_completed");
        assert_eq!(feed.events[1].run_id, None);
        assert_eq!(feed.events[1].payload["surface"], "tui");
        assert_eq!(feed.events[1].payload["success"], true);

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn tui_proposal_selection_prefers_selected_run_before_latest() {
        let latest = artifact("proposal_latest", "run_latest", "code_change_proposal");
        let selected = artifact("proposal_selected", "run_selected", "code_change_proposal");
        let log = artifact("log", "run_selected", "run_output");
        let artifacts = vec![latest, log, selected];

        let artifact = select_proposal_artifact(&artifacts, Some("run_selected")).unwrap();

        assert_eq!(artifact.artifact_id, "proposal_selected");
        assert_eq!(
            select_proposal_artifact(&artifacts, Some("unknown"))
                .unwrap()
                .artifact_id,
            "proposal_latest"
        );
        assert_eq!(
            select_proposal_artifact(&artifacts, None)
                .unwrap()
                .artifact_id,
            "proposal_latest"
        );
    }

    #[test]
    fn tui_source_rating_input_defaults_to_latest_source() {
        let source = knowledge_source("src_latest");

        let parsed =
            parse_source_rating_input("5 helpful context", Some(&source)).expect("rating input");

        assert_eq!(parsed.0, "src_latest");
        assert_eq!(parsed.1, 5);
        assert_eq!(parsed.2, "helpful context");
        assert!(parse_source_rating_input("6 too high", Some(&source)).is_none());
        assert!(parse_source_rating_input("5 no source", None).is_none());
        assert_eq!(
            parse_source_rating_input("src_other 3 partial", Some(&source)).unwrap(),
            ("src_other".to_string(), 3, "partial".to_string())
        );
    }

    #[test]
    fn tui_command_map_groups_agent_actions_by_core_loop() {
        let root = unique_repo("tui-command-map");
        let snapshot = local_result(collect_snapshot(&root)).unwrap();
        let mut state = TuiState::new(TuiStateInit {
            snapshot,
            runs: vec![RunSummary {
                run_id: "run_selected".to_string(),
                workspace_id: "tui-map".to_string(),
                prompt: "Inspect Structure Core event loop affordances".to_string(),
                status: "finished".to_string(),
                final_response: Some("done".to_string()),
                created_at_ms: 1,
                updated_at_ms: 2,
            }],
            run_evidence: Vec::new(),
            workspaces: Vec::new(),
            active_workspace_id: "tui-map".to_string(),
            knowledge_sources: Vec::new(),
            tasks: Vec::new(),
            artifacts: Vec::new(),
        });

        state.preview_command_map(&root).unwrap();

        assert!(state.notice.contains("Command map"));
        let preview = state.preview.as_ref().expect("command map preview");
        assert_eq!(preview.path, "tui command map / tui-map");
        assert!(preview.text.contains("Structure TUI Command Map"));
        assert!(preview
            .text
            .contains("Selected run: run_selected / finished"));
        assert!(preview.text.contains("Run Loop"));
        assert!(preview
            .text
            .contains("c prompt -> LocalAgentRuntime::run_prompt_attempt"));
        assert!(preview
            .text
            .contains("N rerun -> fresh run from the selected persisted prompt"));
        assert!(preview.text.contains("Workspace Context"));
        assert!(preview.text.contains("H doctor -> OPENAI__ diagnostic"));
        assert!(preview.text.contains("Run Evidence"));
        assert!(preview
            .text
            .contains("O tools -> paired tool_call_requested/tool_call_completed"));
        assert!(preview
            .text
            .contains("E events -> complete immutable event stream"));
        assert!(preview
            .text
            .contains("W events -> cursor-based workspace event feed"));
        assert!(preview.text.contains("Session And Proposals"));
        assert!(preview
            .text
            .contains("command_turn_recorded workspace events"));
        assert!(preview
            .text
            .contains("Benchmark adapters stay outside this TUI surface"));
        let runtime = LocalAgentRuntime::open(&root).unwrap();
        let turns = runtime.command_turns(Some("tui-map"), 10).unwrap();
        assert!(turns
            .iter()
            .any(|turn| turn.input == "? map" && turn.surface == "tui"));

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn tui_previews_agent_context_without_starting_run() {
        let _guard = ENV_LOCK.lock().unwrap();
        let previous_openai = openai_env_keys()
            .iter()
            .map(|key| (*key, std::env::var(key).ok()))
            .collect::<Vec<_>>();
        for key in openai_env_keys() {
            std::env::remove_var(key);
        }

        let root = unique_repo("tui-agent-context");
        fs::write(root.join("AGENTS.md"), "Use Structure Core from TUI.").unwrap();
        let source_path = root.join("context-note.md");
        fs::write(&source_path, "TUI context preview source.").unwrap();
        let snapshot = local_result(collect_snapshot(&root)).unwrap();
        let mut state = TuiState::new(TuiStateInit {
            snapshot,
            runs: Vec::new(),
            run_evidence: Vec::new(),
            workspaces: Vec::new(),
            active_workspace_id: "tui-context".to_string(),
            knowledge_sources: Vec::new(),
            tasks: Vec::new(),
            artifacts: Vec::new(),
        });

        let result = (|| -> Result<()> {
            state.add_knowledge_path(&root, source_path.to_string_lossy().to_string())?;
            state.preview_agent_context(&root)?;

            assert!(state.notice.contains("Agent context:"));
            let preview = state.preview.as_ref().expect("agent context preview");
            assert_eq!(preview.path, "agent context / tui-context");
            assert!(preview.text.contains("Agent Context"));
            assert!(preview.text.contains("Workspace: tui-context"));
            assert!(preview.text.contains("Mode: code_agent"));
            assert!(preview.text.contains("Agent Instructions"));
            assert!(preview.text.contains("AGENTS.md"));
            assert!(preview.text.contains("Knowledge Sources"));
            assert!(preview.text.contains("context-note.md"));

            let runtime = local_result(LocalAgentRuntime::open(&root))?;
            assert!(local_result(runtime.list_runs(Some("tui-context"), 1))?.is_empty());
            let turns = local_result(runtime.command_turns(Some("tui-context"), 10))?;
            assert!(turns
                .iter()
                .any(|turn| turn.input == "A context" && turn.surface == "tui"));
            Ok(())
        })();

        fs::remove_dir_all(root).ok();
        for (key, value) in previous_openai {
            restore_env(key, value);
        }
        result.unwrap();
    }

    #[test]
    fn tui_previews_local_doctor_without_starting_run() {
        let _guard = ENV_LOCK.lock().unwrap();
        let previous_openai = openai_env_keys()
            .iter()
            .map(|key| (*key, std::env::var(key).ok()))
            .collect::<Vec<_>>();
        for key in openai_env_keys() {
            std::env::remove_var(key);
        }

        let root = unique_repo("tui-doctor");
        fs::write(
            root.join("AGENTS.md"),
            "Doctor must preserve Structure Core.",
        )
        .unwrap();
        let snapshot = local_result(collect_snapshot(&root)).unwrap();
        let mut state = TuiState::new(TuiStateInit {
            snapshot,
            runs: Vec::new(),
            run_evidence: Vec::new(),
            workspaces: Vec::new(),
            active_workspace_id: "tui-doctor".to_string(),
            knowledge_sources: Vec::new(),
            tasks: Vec::new(),
            artifacts: Vec::new(),
        });

        let result = (|| -> Result<()> {
            state.preview_local_doctor(&root)?;

            assert!(state.notice.contains("Doctor:"));
            let preview = state.preview.as_ref().expect("doctor preview");
            assert_eq!(preview.path, "local doctor / tui-doctor");
            assert!(preview.text.contains("Structure Local Doctor"));
            assert!(preview.text.contains("Workspace: tui-doctor"));
            assert!(preview.text.contains("LLM: missing"));
            assert!(preview.text.contains("OPENAI__API_KEY"));
            assert!(preview.text.contains("Structure Core parity:"));
            assert!(preview.text.contains("Parity Details"));
            assert!(preview.text.contains("Context: 1 instructions"));

            let runtime = local_result(LocalAgentRuntime::open(&root))?;
            assert!(local_result(runtime.list_runs(Some("tui-doctor"), 1))?.is_empty());
            let turns = local_result(runtime.command_turns(Some("tui-doctor"), 10))?;
            assert!(turns
                .iter()
                .any(|turn| turn.input == "H doctor" && turn.surface == "tui"));
            Ok(())
        })();

        fs::remove_dir_all(root).ok();
        for (key, value) in previous_openai {
            restore_env(key, value);
        }
        result.unwrap();
    }

    #[test]
    fn tui_custom_prompt_opens_attempt_preview() {
        let _guard = ENV_LOCK.lock().unwrap();
        let previous_openai = openai_env_keys()
            .iter()
            .map(|key| (*key, std::env::var(key).ok()))
            .collect::<Vec<_>>();
        for key in openai_env_keys() {
            std::env::remove_var(key);
        }

        let root = unique_repo("tui-attempt-preview");
        let snapshot = local_result(collect_snapshot(&root)).unwrap();
        let mut state = TuiState::new(TuiStateInit {
            snapshot,
            runs: Vec::new(),
            run_evidence: Vec::new(),
            workspaces: Vec::new(),
            active_workspace_id: "tui-preview".to_string(),
            knowledge_sources: Vec::new(),
            tasks: Vec::new(),
            artifacts: Vec::new(),
        });

        let result = (|| -> Result<()> {
            state.run_custom_prompt(&root, "Summarize this workspace.".to_string())?;

            assert!(state.notice.starts_with("Finished run_"));
            let preview = state.preview.as_ref().expect("run attempt preview");
            assert!(preview.path.starts_with("run attempt / run_"));
            assert!(preview.text.contains("Run Attempt"));
            assert!(preview.text.contains("Events"));
            assert!(!state.runs.is_empty());
            let original_run_id = state.runs.first().unwrap().run_id.clone();

            state.rerun_selected_prompt(&root)?;
            assert!(state.notice.contains(&original_run_id));
            assert!(state.notice.contains("Reran"));
            let rerun_preview = state.preview.as_ref().expect("history rerun preview");
            assert!(rerun_preview.path.starts_with("history rerun / run_"));
            assert!(rerun_preview.text.contains("Run Attempt"));
            assert!(state.runs.len() >= 2);

            let runtime = local_result(LocalAgentRuntime::open(&root))?;
            let latest = state.runs.first().expect("latest rerun");
            let transcript = local_result(runtime.run_transcript(&latest.run_id))?;
            assert_eq!(transcript.run.prompt, "Summarize this workspace.");

            state.preview_selected_run_core_trace(&root)?;
            let trace_preview = state.preview.as_ref().expect("core trace preview");
            assert!(trace_preview.path.starts_with("core trace / run_"));
            assert!(trace_preview.text.contains("Structure Core Trace"));
            assert!(trace_preview.text.contains("Flow path:"));
            assert!(trace_preview.text.contains("Primitive path:"));
            assert!(trace_preview.text.contains("Core aligned: true"));

            state.preview_selected_run_plan(&root)?;
            let plan_preview = state.preview.as_ref().expect("run plan preview");
            assert!(plan_preview.path.starts_with("run plan / run_"));
            assert!(plan_preview.text.contains("Run Plan"));
            assert!(plan_preview.text.contains("Steps:"));
            assert!(plan_preview.text.contains("Model:"));

            state.preview_selected_run_tool_trace(&root)?;
            let tool_preview = state.preview.as_ref().expect("tool trace preview");
            assert!(tool_preview.path.starts_with("tool trace / run_"));
            assert!(tool_preview.text.contains("Tool Trace"));
            assert!(tool_preview.text.contains("Tool calls:"));
            assert!(tool_preview.text.contains("list_workspace"));

            state.preview_selected_run_events(&root)?;
            let events_preview = state.preview.as_ref().expect("run events preview");
            assert!(events_preview.path.starts_with("run events / run_"));
            assert!(events_preview.text.contains("Run Events"));
            assert!(events_preview.text.contains("workspace_context_loaded"));
            assert!(events_preview.text.contains("run_finished"));

            state.preview_selected_run_compact(&root)?;
            let compact_preview = state.preview.as_ref().expect("run compact preview");
            assert!(compact_preview.path.starts_with("run compact / run_"));
            assert!(compact_preview.text.contains("Run Compact"));
            assert!(compact_preview.text.contains("Carry Forward"));
            assert!(compact_preview.text.contains("Continuation Context"));

            state.preview_workspace_compact(&root)?;
            let session_preview = state.preview.as_ref().expect("workspace compact preview");
            assert!(session_preview
                .path
                .starts_with("workspace compact / tui-preview"));
            assert!(session_preview.text.contains("Workspace Compact"));
            assert!(session_preview.text.contains("Recent Runs"));
            assert!(session_preview.text.contains("session handoff boundary"));

            state.preview_workspace_events(&root)?;
            let workspace_events_preview =
                state.preview.as_ref().expect("workspace events preview");
            assert!(workspace_events_preview
                .path
                .starts_with("workspace events / tui-preview"));
            assert!(workspace_events_preview.text.contains("Workspace Events"));
            assert!(workspace_events_preview
                .text
                .contains("workspace_context_loaded"));
            assert!(workspace_events_preview.text.contains("run_finished"));
            assert!(workspace_events_preview
                .text
                .contains("Next after sequence:"));

            state.preview_workspace_usage(&root)?;
            let usage_preview = state.preview.as_ref().expect("workspace usage preview");
            assert!(usage_preview
                .path
                .starts_with("workspace usage / tui-preview"));
            assert!(usage_preview.text.contains("Workspace Usage"));
            assert!(usage_preview.text.contains("Tokens:"));
            assert!(usage_preview.text.contains("Recent Runs"));

            state.preview_selected_run_review(&root)?;
            let review_preview = state.preview.as_ref().expect("run review preview");
            assert!(review_preview.path.starts_with("run review / run_"));
            assert!(review_preview.text.contains("Run Review"));
            assert!(review_preview.text.contains("Next Actions"));

            state.review_latest_proposal_risk(&root)?;
            let risk_preview = state.preview.as_ref().expect("proposal risk preview");
            assert!(risk_preview.path.starts_with("proposal risk / art_"));
            assert!(risk_preview.text.contains("Proposal Risk Review"));
            assert!(risk_preview.text.contains("Dry-run: required"));
            let runtime = local_result(LocalAgentRuntime::open(&root))?;
            let turns = local_result(runtime.command_turns(Some("tui-preview"), 20))?;
            assert!(turns
                .iter()
                .any(|turn| turn.input.starts_with("E events run_") && turn.surface == "tui"));
            assert!(turns
                .iter()
                .any(|turn| turn.input == "W events tui-preview" && turn.surface == "tui"));
            Ok(())
        })();

        fs::remove_dir_all(root).ok();
        for (key, value) in previous_openai {
            restore_env(key, value);
        }
        result.unwrap();
    }

    #[test]
    fn tui_follow_up_runs_continuation_through_runtime() {
        let _guard = ENV_LOCK.lock().unwrap();
        let previous_openai = openai_env_keys()
            .iter()
            .map(|key| (*key, std::env::var(key).ok()))
            .collect::<Vec<_>>();
        for key in openai_env_keys() {
            std::env::remove_var(key);
        }

        let root = unique_repo("tui-follow-up");
        let snapshot = local_result(collect_snapshot(&root)).unwrap();
        let mut state = TuiState::new(TuiStateInit {
            snapshot,
            runs: Vec::new(),
            run_evidence: Vec::new(),
            workspaces: Vec::new(),
            active_workspace_id: "tui-follow-up".to_string(),
            knowledge_sources: Vec::new(),
            tasks: Vec::new(),
            artifacts: Vec::new(),
        });

        let result = (|| -> Result<()> {
            state.run_custom_prompt(&root, "Summarize this workspace.".to_string())?;
            let original_run_id = state.runs.first().unwrap().run_id.clone();
            state.run_follow_up_prompt(
                &root,
                "Continue with one implementation note.".to_string(),
            )?;

            assert!(state.notice.contains(&original_run_id));
            assert!(state.notice.contains("Continued"));
            assert!(state.runs.len() >= 2);
            let preview = state.preview.as_ref().expect("continuation preview");
            assert!(preview.path.starts_with("continuation / run_"));
            assert!(preview.text.contains("Run Attempt"));
            assert!(preview.text.contains("Events"));

            let runtime = local_result(LocalAgentRuntime::open(&root))?;
            let latest = state.runs.first().expect("latest continuation run");
            let evidence = local_result(runtime.run_evidence_summary(&latest.run_id))?;
            assert!(evidence.event_count > 0);
            assert!(evidence.core_trace.core_aligned);

            state.run_session_follow_up_prompt(
                &root,
                "Continue from the whole workspace compact.".to_string(),
            )?;
            assert!(state.notice.contains("Continued session"));
            let session_preview = state
                .preview
                .as_ref()
                .expect("session continuation preview");
            assert!(session_preview
                .path
                .starts_with("session continuation / run_"));
            assert!(session_preview.text.contains("Run Attempt"));
            assert!(state.runs.len() >= 3);

            let latest_session = state.runs.first().expect("latest session continuation run");
            let transcript = local_result(runtime.run_transcript(&latest_session.run_id))?;
            assert!(transcript
                .run
                .prompt
                .contains("Continue Structure workspace/session"));
            assert!(transcript
                .run
                .prompt
                .contains("Compact Structure workspace context"));
            Ok(())
        })();

        fs::remove_dir_all(root).ok();
        for (key, value) in previous_openai {
            restore_env(key, value);
        }
        result.unwrap();
    }

    #[test]
    fn tui_records_checkpoint_for_selected_run_and_compact_context() {
        let _guard = ENV_LOCK.lock().unwrap();
        let previous_openai = openai_env_keys()
            .iter()
            .map(|key| (*key, std::env::var(key).ok()))
            .collect::<Vec<_>>();
        for key in openai_env_keys() {
            std::env::remove_var(key);
        }

        let root = unique_repo("tui-checkpoint");
        let snapshot = local_result(collect_snapshot(&root)).unwrap();
        let mut state = TuiState::new(TuiStateInit {
            snapshot,
            runs: Vec::new(),
            run_evidence: Vec::new(),
            workspaces: Vec::new(),
            active_workspace_id: "tui-checkpoint".to_string(),
            knowledge_sources: Vec::new(),
            tasks: Vec::new(),
            artifacts: Vec::new(),
        });

        let result = (|| -> Result<()> {
            state.run_custom_prompt(&root, "Create a checkpointable local plan.".to_string())?;
            let run_id = state.runs.first().unwrap().run_id.clone();
            let note = "Human decision: keep Structure Core as the TUI scheduler.";

            state.record_run_checkpoint(&root, note.to_string())?;

            assert!(state.notice.starts_with("Checkpoint recorded: chk_"));
            let checkpoint_preview = state.preview.as_ref().expect("checkpoint preview");
            assert!(checkpoint_preview.path.starts_with("run checkpoint / run_"));
            assert!(checkpoint_preview.text.contains("Run Checkpoint"));
            assert!(checkpoint_preview.text.contains(note));

            let runtime = local_result(LocalAgentRuntime::open(&root))?;
            let evidence = local_result(runtime.run_evidence_summary(&run_id))?;
            assert_eq!(evidence.checkpoints.len(), 1);
            assert_eq!(evidence.checkpoints[0].note, note);
            assert!(state
                .run_evidence
                .iter()
                .any(|evidence| evidence.run.run_id == run_id && evidence.checkpoints.len() == 1));

            state.preview_selected_run_transcript(&root)?;
            let transcript_preview = state.preview.as_ref().expect("transcript preview");
            assert!(transcript_preview.text.contains("Run Checkpoints"));
            assert!(transcript_preview.text.contains(note));

            state.preview_selected_run_compact(&root)?;
            let compact_preview = state.preview.as_ref().expect("compact preview");
            assert!(compact_preview.text.contains("Human checkpoints"));
            assert!(compact_preview.text.contains(note));
            Ok(())
        })();

        fs::remove_dir_all(root).ok();
        for (key, value) in previous_openai {
            restore_env(key, value);
        }
        result.unwrap();
    }

    fn artifact(artifact_id: &str, run_id: &str, kind: &str) -> ArtifactRecord {
        ArtifactRecord {
            artifact_id: artifact_id.to_string(),
            run_id: run_id.to_string(),
            workspace_id: "workspace".to_string(),
            kind: kind.to_string(),
            path: format!("artifacts/{artifact_id}.md"),
            size_bytes: 42,
            created_at_ms: 1,
        }
    }

    fn knowledge_source(source_id: &str) -> KnowledgeSource {
        KnowledgeSource {
            source_id: source_id.to_string(),
            workspace_id: "workspace".to_string(),
            path: format!("/tmp/{source_id}.md"),
            title: format!("{source_id}.md"),
            size_bytes: 42,
            added_at_ms: 1,
        }
    }

    fn openai_env_keys() -> [&'static str; 3] {
        ["OPENAI__API_KEY", "OPENAI__BASE_URL", "OPENAI__MODEL"]
    }

    fn restore_env(key: &str, value: Option<String>) {
        if let Some(value) = value {
            std::env::set_var(key, value);
        } else {
            std::env::remove_var(key);
        }
    }

    fn unique_repo(label: &str) -> PathBuf {
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let root = std::env::temp_dir().join(format!(
            "structure-tui-{label}-{}-{nanos}",
            std::process::id()
        ));
        fs::create_dir_all(root.join("src/structure")).unwrap();
        fs::create_dir_all(root.join("frontend")).unwrap();
        fs::write(root.join("pyproject.toml"), "").unwrap();
        root.canonicalize().unwrap()
    }
}
