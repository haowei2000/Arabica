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
    ArtifactRecord, BuiltinLocalToolRegistry, KnowledgeSource, LocalAgentMode, LocalAgentRuntime,
    LocalEvidenceBundle, LocalToolCall, LocalToolRegistry, ProposalApplyResult, RunEvidenceSummary,
    RunRequest, RunSummary, WorkspaceSummary,
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
    KnowledgePath,
    WorkspaceId,
    LocalCommand,
}

impl TuiInputKind {
    fn title(self) -> &'static str {
        match self {
            Self::AgentPrompt => "Custom agent prompt",
            Self::KnowledgePath => "Knowledge file path",
            Self::WorkspaceId => "Workspace id",
            Self::LocalCommand => "Local command argv",
        }
    }

    fn placeholder(self) -> &'static str {
        match self {
            Self::AgentPrompt => "Type a prompt and press Enter",
            Self::KnowledgePath => "Type an absolute or repo-relative file path",
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
    artifacts: Vec<ArtifactRecord>,
    agent_mode: LocalAgentMode,
    selected: usize,
    preview: Option<TuiPreview>,
    pending_apply_artifact_id: Option<String>,
    input: Option<TuiInput>,
    notice: String,
}

impl TuiState {
    fn new(
        snapshot: LocalSnapshot,
        runs: Vec<RunSummary>,
        run_evidence: Vec<RunEvidenceSummary>,
        workspaces: Vec<WorkspaceSummary>,
        active_workspace_id: String,
        knowledge_sources: Vec<KnowledgeSource>,
        artifacts: Vec<ArtifactRecord>,
    ) -> Self {
        Self {
            snapshot,
            runs,
            run_evidence,
            workspaces,
            active_workspace_id,
            knowledge_sources,
            artifacts,
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
        self.artifacts = local_result(runtime.list_artifacts(Some(workspace_id), None, 5))?;
        self.pending_apply_artifact_id = None;
        self.notice = "Snapshot refreshed".to_string();
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
            TuiInputKind::KnowledgePath => self.add_knowledge_path(repo_root, value),
            TuiInputKind::WorkspaceId => self.open_or_create_workspace(repo_root, value),
            TuiInputKind::LocalCommand => self.run_local_command(repo_root, value),
        }
    }

    fn run_custom_prompt(&mut self, repo_root: &Path, prompt: String) -> Result<()> {
        let runtime = local_result(LocalAgentRuntime::open(repo_root))?;
        let result = local_result(runtime.run_prompt(RunRequest {
            prompt,
            workspace_id: Some(self.active_workspace_id.clone()),
            mode: Some(self.agent_mode.clone()),
        }))?;
        let notice = format!("Finished {}", result.run.run_id);
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
        let result = local_result(
            runtime.run_prompt(RunRequest {
                prompt: "Inspect the local workspace state and summarize the available context."
                    .to_string(),
                workspace_id: Some(self.active_workspace_id.clone()),
                mode: Some(self.agent_mode.clone()),
            }),
        )?;
        self.runs = local_result(runtime.list_runs(Some(&self.active_workspace_id), 5))?;
        self.run_evidence = collect_run_evidence(&runtime, &self.runs);
        self.knowledge_sources =
            local_result(runtime.knowledge_sources(Some(&self.active_workspace_id), 5))?;
        self.artifacts =
            local_result(runtime.list_artifacts(Some(&self.active_workspace_id), None, 5))?;
        self.notice = format!("Finished {}", result.run.run_id);
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

        let registry = BuiltinLocalToolRegistry::new(runtime.repo_root());
        let result = registry.execute(&LocalToolCall {
            call_id: "tui_run_local_command".to_string(),
            name: "run_local_command".to_string(),
            input: serde_json::json!({
                "argv": argv,
                "cwd": ".",
                "timeout_ms": 30_000,
                "max_output_chars": 12_000,
            }),
        });
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

    fn preview_latest_proposal(&mut self, repo_root: &Path) -> Result<()> {
        let Some(artifact) = self.latest_proposal_artifact() else {
            self.notice = "No code-change proposal to preview".to_string();
            return Ok(());
        };
        self.pending_apply_artifact_id = None;
        self.preview_artifact_record(repo_root, &artifact, "Previewing latest proposal")
    }

    fn preview_latest_proposal_apply(&mut self, repo_root: &Path) -> Result<()> {
        let Some(artifact) = self.latest_proposal_artifact() else {
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
        self.notice = "Dry-run ready; press y to apply this proposal".to_string();
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

    fn latest_proposal_artifact(&self) -> Option<ArtifactRecord> {
        self.artifacts
            .iter()
            .find(|artifact| artifact.kind == "code_change_proposal")
            .cloned()
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
    let artifacts = local_result(runtime.list_artifacts(Some(&active_workspace_id), None, 5))?;
    let mut state = TuiState::new(
        snapshot,
        runs,
        run_evidence,
        workspaces,
        active_workspace_id,
        knowledge_sources,
        artifacts,
    );
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
                    KeyCode::Char('s') => state.begin_input(TuiInputKind::KnowledgePath),
                    KeyCode::Char('!') => state.begin_input(TuiInputKind::LocalCommand),
                    KeyCode::Char('x') => state.remove_latest_knowledge(repo_root)?,
                    KeyCode::Char('n') => state.run_workspace_check(repo_root)?,
                    KeyCode::Char('p') => state.preview_latest_knowledge(repo_root)?,
                    KeyCode::Char('a') => state.preview_latest_artifact(repo_root)?,
                    KeyCode::Char('g') => state.preview_latest_proposal(repo_root)?,
                    KeyCode::Char('u') => state.preview_latest_proposal_apply(repo_root)?,
                    KeyCode::Char('y') => state.apply_pending_proposal(repo_root)?,
                    KeyCode::Char('v') => state.preview_parity_report(repo_root)?,
                    KeyCode::Char('e') => state.preview_evidence_bundle(repo_root)?,
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
            "m mode  o workspace  c prompt  s knowledge path  ! local command",
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
        "press m to toggle chat/code_agent, w to switch workspace, n to run a local check",
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
                    "  [{} events, {} tools, {} sources, {} artifacts]",
                    evidence.event_count,
                    evidence.tool_call_count,
                    evidence.knowledge_sources.len(),
                    evidence.artifacts.len()
                )
            })
            .unwrap_or_default();
        write_at(
            out,
            x,
            y + 3 + u16::try_from(index).unwrap_or_default(),
            &format!(
                "{}  {}  {}{}",
                run.status, run.workspace_id, run.prompt, suffix
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
    let artifacts_y = reports_y + 4;
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
            "press a for latest artifact, g proposal, u dry-run apply, y apply",
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
    let controls = "q quit  r refresh  m mode  o workspace  c prompt  ! cmd  n run  g proposal  u dry-run  y apply  e bundle";
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
                "- {}: {} events, {} tools, {} sources, {} artifacts\n",
                evidence.run.run_id,
                evidence.event_count,
                evidence.tool_call_count,
                evidence.knowledge_sources.len(),
                evidence.artifacts.len()
            ));
        }
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
