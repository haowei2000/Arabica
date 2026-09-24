//! Full-screen terminal conversation built on the same session and runtime as plain chat.

use std::collections::VecDeque;
use std::io;
use std::path::{Component, Path};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use crossterm::event::{
    self, Event as InputEvent, KeyCode, KeyEvent, KeyEventKind, KeyModifiers, MouseEvent,
    MouseEventKind,
};
use ratatui::layout::{Constraint, Layout};
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span, Text};
use ratatui::widgets::{Block, Borders, Paragraph, Wrap};
use ratatui::{DefaultTerminal, Frame};
use structure_adapters::FileSessionStore;
use structure_model::{ContentBlock, RuntimeItem, RuntimeRole};
use structure_protocol::{
    Command, CommandEnvelope, CommandId, Event, EventEnvelope, ToolPermissionOutcome,
    ToolPermissionScope, ToolPermissionSource,
};
use structure_provider::{ApiProviderConfig, ModelProgress, ModelProgressSink};
use structure_runtime::{
    PermissionDecision, PermissionRequest, RunCancellation, RunControl, ToolPermissionGate,
};
use structure_session::{DispatchControl, EventVisibility, FanOutObserver, SessionEventObserver};

use crate::context;
use crate::host::workspace_id_for;
use crate::interactive::{self, InteractiveOptions, InteractiveSession};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum Kind {
    User,
    Assistant,
    Reasoning,
    Tool,
    Info,
    Error,
}

#[derive(Clone, Debug)]
struct Entry {
    kind: Kind,
    text: String,
}

struct PermissionView {
    tool: String,
    details: String,
    scroll: u16,
}

struct ContextView {
    text: String,
    scroll: u16,
}

#[derive(Clone, Debug)]
struct CompletionView {
    replace_start: usize,
    replace_end: usize,
    candidates: Vec<String>,
    selected: usize,
}

const COMMANDS: &[&str] = &[
    "/context",
    "/diff",
    "/exit",
    "/help",
    "/model",
    "/quit",
    "/resume",
    "/session",
    "/sessions",
    "/thinking",
    "/undo",
];
const THINKING_LEVELS: &[&str] = &["off", "on", "low", "medium", "high"];

fn workspace_files(root: &Path) -> Vec<String> {
    let mut files = Vec::new();
    for entry in ignore::WalkBuilder::new(root).build().flatten() {
        if !entry.file_type().is_some_and(|kind| kind.is_file()) {
            continue;
        }
        if let Ok(path) = entry.path().strip_prefix(root)
            && let Some(path) = path.to_str()
        {
            files.push(path.to_owned());
        }
        if files.len() >= 10_000 {
            break;
        }
    }
    files.sort();
    files
}

fn configured_models(current: &str) -> Vec<String> {
    let mut models = vec![current.to_owned()];
    if let Ok(configured) = std::env::var("STRUCTURE__MODELS") {
        for model in configured
            .split(',')
            .map(str::trim)
            .filter(|model| !model.is_empty())
        {
            if !models.iter().any(|known| known == model) {
                models.push(model.to_owned());
            }
        }
    }
    models
}

fn completion_for(
    input: &str,
    cursor: usize,
    files: &[String],
    models: &[String],
) -> Option<CompletionView> {
    let before = &input[..cursor];
    let word_start = before
        .char_indices()
        .rev()
        .find(|(_, ch)| ch.is_whitespace())
        .map_or(0, |(i, ch)| i + ch.len_utf8());
    let token = &before[word_start..];
    let (replace_start, candidates): (usize, Vec<String>) =
        if word_start == 0 && token.starts_with('/') && !token.contains(' ') {
            (
                word_start,
                COMMANDS
                    .iter()
                    .filter(|command| command.starts_with(token))
                    .map(|command| (*command).to_owned())
                    .collect(),
            )
        } else if before.starts_with("/thinking ") && word_start == "/thinking ".len() {
            (
                word_start,
                THINKING_LEVELS
                    .iter()
                    .filter(|level| level.starts_with(token))
                    .map(|level| (*level).to_owned())
                    .collect(),
            )
        } else if before.starts_with("/model ") && word_start == "/model ".len() {
            (
                word_start,
                models
                    .iter()
                    .filter(|model| model.to_lowercase().contains(&token.to_lowercase()))
                    .cloned()
                    .collect(),
            )
        } else if let Some(query) = token.strip_prefix('@') {
            let query = query.to_lowercase();
            let mut matches: Vec<_> = files
                .iter()
                .filter(|file| file.to_lowercase().contains(&query))
                .take(50)
                .map(|file| {
                    if file.contains(char::is_whitespace) {
                        format!("@\"{file}\"")
                    } else {
                        format!("@{file}")
                    }
                })
                .collect();
            matches.sort_by_key(|file| (!file[1..].to_lowercase().starts_with(&query), file.len()));
            (word_start, matches)
        } else {
            return None;
        };
    if candidates.is_empty() || (candidates.len() == 1 && candidates[0] == token) {
        return None;
    }
    Some(CompletionView {
        replace_start,
        replace_end: cursor
            + input[cursor..]
                .find(char::is_whitespace)
                .unwrap_or(input.len() - cursor),
        candidates,
        selected: 0,
    })
}

impl ContextView {
    fn handle_scroll(&mut self, code: KeyCode) {
        match code {
            KeyCode::Up => self.scroll = self.scroll.saturating_sub(1),
            KeyCode::Down => self.scroll = self.scroll.saturating_add(1),
            KeyCode::PageUp => self.scroll = self.scroll.saturating_sub(10),
            KeyCode::PageDown => self.scroll = self.scroll.saturating_add(10),
            KeyCode::Home => self.scroll = 0,
            KeyCode::End => self.scroll = u16::MAX,
            _ => {}
        }
    }
}

impl PermissionView {
    fn new(request: &PermissionRequest, root: &Path) -> Self {
        Self {
            tool: request.call.name.clone(),
            details: permission_details(&request.call.name, &request.call.arguments, root),
            scroll: 0,
        }
    }

    fn handle_scroll(&mut self, code: KeyCode) {
        match code {
            KeyCode::Up => self.scroll = self.scroll.saturating_sub(1),
            KeyCode::Down => self.scroll = self.scroll.saturating_add(1),
            KeyCode::PageUp => self.scroll = self.scroll.saturating_sub(10),
            KeyCode::PageDown => self.scroll = self.scroll.saturating_add(10),
            KeyCode::Home => self.scroll = 0,
            KeyCode::End => self.scroll = u16::MAX,
            _ => {}
        }
    }
}

fn permission_details(tool: &str, arguments: &serde_json::Value, root: &Path) -> String {
    if tool == "write_file"
        && let (Some(path), Some(content)) = (
            arguments.get("path").and_then(|v| v.as_str()),
            arguments.get("content").and_then(|v| v.as_str()),
        )
    {
        let preview = write_file_preview(root, path, content);
        return format!("Path: {path}\n\n{preview}\nContent:\n{content}");
    }
    serde_json::to_string_pretty(arguments).unwrap_or_else(|_| arguments.to_string())
}

fn write_file_preview(root: &Path, path: &str, content: &str) -> String {
    let relative = Path::new(path);
    if relative.as_os_str().is_empty()
        || !relative
            .components()
            .all(|part| matches!(part, Component::Normal(_)))
    {
        return "Change preview unavailable: path is outside the workspace.\n".to_owned();
    }
    let Ok(root) = root.canonicalize() else {
        return "Change preview unavailable: workspace cannot be resolved.\n".to_owned();
    };
    let target = root.join(relative);
    let Ok(parent) = target
        .parent()
        .expect("relative path has a parent")
        .canonicalize()
    else {
        return "Change preview unavailable: parent directory cannot be resolved.\n".to_owned();
    };
    if !parent.starts_with(&root) {
        return "Change preview unavailable: path is outside the workspace.\n".to_owned();
    }
    if !target.exists() {
        return format!(
            "Change preview: new file ({} lines)\n",
            content.lines().count()
        );
    }
    let Ok(metadata) = target.symlink_metadata() else {
        return "Change preview unavailable: current file cannot be inspected.\n".to_owned();
    };
    if !metadata.file_type().is_file() || metadata.len() > 512 * 1024 {
        return "Change preview unavailable: target is not a regular file or exceeds 512 KiB.\n"
            .to_owned();
    }
    let Ok(old) = std::fs::read_to_string(&target) else {
        return "Change preview unavailable: current file cannot be read as UTF-8.\n".to_owned();
    };
    line_change_preview(&old, content)
}

fn line_change_preview(old: &str, new: &str) -> String {
    if old == new {
        return "Change preview: no content change.\n".to_owned();
    }
    let before: Vec<_> = old.lines().collect();
    let after: Vec<_> = new.lines().collect();
    if new.len() > 512 * 1024
        || before.len() + after.len() > 2_000
        || before.len().saturating_mul(after.len()) > 250_000
    {
        return format!(
            "Change preview: {} old lines → {} new lines; line diff exceeds display limit.\n",
            before.len(),
            after.len()
        );
    }
    let mut lengths = vec![vec![0_u32; after.len() + 1]; before.len() + 1];
    for i in (0..before.len()).rev() {
        for j in (0..after.len()).rev() {
            lengths[i][j] = if before[i] == after[j] {
                lengths[i + 1][j + 1] + 1
            } else {
                lengths[i + 1][j].max(lengths[i][j + 1])
            };
        }
    }
    let mut out = String::from("Change preview (current - / proposed +):\n");
    let (mut i, mut j) = (0, 0);
    while i < before.len() || j < after.len() {
        if i < before.len() && j < after.len() && before[i] == after[j] {
            out.push_str("  ");
            out.push_str(before[i]);
            out.push('\n');
            i += 1;
            j += 1;
        } else if i < before.len() && (j == after.len() || lengths[i + 1][j] >= lengths[i][j + 1]) {
            out.push_str("- ");
            out.push_str(before[i]);
            out.push('\n');
            i += 1;
        } else {
            out.push_str("+ ");
            out.push_str(after[j]);
            out.push('\n');
            j += 1;
        }
    }
    if old.ends_with('\n') != new.ends_with('\n') {
        out.push_str(if new.ends_with('\n') {
            "+ final newline\n"
        } else {
            "- final newline\n"
        });
    }
    out
}

#[derive(Default)]
struct App {
    entries: Vec<Entry>,
    input: String,
    cursor: usize,
    queued: VecDeque<String>,
    scroll_from_bottom: usize,
    busy: bool,
    show_thinking: bool,
    permission: Option<PermissionView>,
    context: Option<ContextView>,
    completion: Option<CompletionView>,
    file_index: Option<Vec<String>>,
    models: Vec<String>,
    status: String,
    model: String,
    thinking: String,
    workspace: String,
    session: String,
}

impl App {
    fn new(session: &InteractiveSession) -> Self {
        let mut app = Self {
            show_thinking: true,
            status: "Ready".to_owned(),
            workspace: session.runner_root.display().to_string(),
            session: session.session_id.to_string(),
            models: configured_models(&session.config.model),
            ..Self::default()
        };
        app.refresh_config(session);
        app.entries.push(Entry {
            kind: Kind::Info,
            text: "Enter sends · Shift+Enter or Ctrl+J adds a line · /help lists commands"
                .to_owned(),
        });
        app
    }

    fn refresh_config(&mut self, session: &InteractiveSession) {
        self.model.clone_from(&session.config.model);
        self.thinking = if !session.config.thinking_enabled {
            "off".to_owned()
        } else {
            session
                .config
                .reasoning_effort
                .clone()
                .unwrap_or_else(|| "on".to_owned())
        };
        if !self.models.contains(&self.model) {
            self.models.push(self.model.clone());
        }
    }

    fn refresh_completion(&mut self) {
        let before = &self.input[..self.cursor];
        let token = before.rsplit(char::is_whitespace).next().unwrap_or("");
        if token.starts_with('@') && self.file_index.is_none() {
            self.file_index = Some(workspace_files(Path::new(&self.workspace)));
        }
        self.completion = completion_for(
            &self.input,
            self.cursor,
            self.file_index.as_deref().unwrap_or(&[]),
            &self.models,
        );
    }

    fn apply_completion(&mut self) {
        let Some(completion) = self.completion.take() else {
            return;
        };
        let candidate = &completion.candidates[completion.selected];
        self.input
            .replace_range(completion.replace_start..completion.replace_end, candidate);
        self.cursor = completion.replace_start + candidate.len();
        if candidate.starts_with('@') || (candidate.starts_with('/') && !candidate.contains(' ')) {
            self.input.insert(self.cursor, ' ');
            self.cursor += 1;
        }
    }

    fn push(&mut self, kind: Kind, text: impl Into<String>) {
        self.entries.push(Entry {
            kind,
            text: text.into(),
        });
        self.scroll_from_bottom = 0;
    }

    fn append(&mut self, kind: Kind, text: String) {
        if let Some(last) = self.entries.last_mut()
            && last.kind == kind
        {
            last.text.push_str(&text);
        } else {
            self.push(kind, text);
        }
        self.scroll_from_bottom = 0;
    }

    fn insert(&mut self, text: &str) {
        self.input.insert_str(self.cursor, text);
        self.cursor += text.len();
    }

    fn backspace(&mut self) {
        if self.cursor > 0 {
            let previous = self.input[..self.cursor]
                .char_indices()
                .next_back()
                .map(|(index, _)| index)
                .unwrap_or(0);
            self.input.replace_range(previous..self.cursor, "");
            self.cursor = previous;
        }
    }

    fn delete(&mut self) {
        if self.cursor < self.input.len() {
            let next = self.cursor + self.input[self.cursor..].chars().next().unwrap().len_utf8();
            self.input.replace_range(self.cursor..next, "");
        }
    }

    fn take_input(&mut self) -> String {
        self.cursor = 0;
        self.completion = None;
        std::mem::take(&mut self.input).trim().to_owned()
    }

    fn apply(&mut self, event: UiEvent) {
        match event {
            UiEvent::Text(text) => self.append(Kind::Assistant, text),
            UiEvent::Reasoning(text) => self.append(Kind::Reasoning, text),
            UiEvent::Tool(name) => self.push(Kind::Tool, format!("→ {name}")),
            UiEvent::ToolDone(name, error) => self.push(
                if error { Kind::Error } else { Kind::Tool },
                if error {
                    format!("✗ {name} failed")
                } else {
                    format!("✓ {name} completed")
                },
            ),
        }
    }

    fn load_history(
        &mut self,
        session: &InteractiveSession,
    ) -> Result<(), Box<dyn std::error::Error>> {
        let workspace = workspace_id_for(&session.runner_root);
        let stored = FileSessionStore::read_session(
            &session.structure_home,
            &workspace,
            &session.session_id,
        )?;
        for envelope in stored.events {
            match envelope.event {
                Event::MessageAccepted { content } => self.push(Kind::User, content),
                Event::ModelResponseItem {
                    item: RuntimeItem::Message(message),
                    ..
                } if message.role == RuntimeRole::Assistant => {
                    let text = message
                        .content
                        .iter()
                        .filter_map(|block| match block {
                            ContentBlock::Text { text } => Some(text.as_str()),
                            _ => None,
                        })
                        .collect::<String>();
                    if !text.is_empty() {
                        self.append(Kind::Assistant, text);
                    }
                }
                Event::ModelResponseItem {
                    item: RuntimeItem::Reasoning(reasoning),
                    ..
                } => {
                    let text = reasoning.summary.join("\n\n");
                    if !text.is_empty() {
                        self.append(Kind::Reasoning, text);
                    }
                }
                Event::ToolCallRequested { name, .. } => self.push(Kind::Tool, format!("→ {name}")),
                Event::ToolCallCompleted { name, is_error, .. } => {
                    self.apply(UiEvent::ToolDone(name, is_error))
                }
                _ => {}
            }
        }
        Ok(())
    }
}

enum UiEvent {
    Text(String),
    Reasoning(String),
    Tool(String),
    ToolDone(String, bool),
}

#[derive(Default)]
struct StreamState {
    step_text: String,
    step_reasoning: String,
    displayed: String,
}

struct TuiObserver {
    state: Mutex<StreamState>,
    sender: tokio::sync::mpsc::UnboundedSender<UiEvent>,
}

impl TuiObserver {
    fn new(sender: tokio::sync::mpsc::UnboundedSender<UiEvent>) -> Arc<Self> {
        Arc::new(Self {
            state: Mutex::new(StreamState::default()),
            sender,
        })
    }

    fn sink(self: &Arc<Self>) -> ModelProgressSink {
        let observer = Arc::clone(self);
        ModelProgressSink::new(move |progress| {
            let mut state = observer.state.lock().expect("TUI progress lock poisoned");
            match progress {
                ModelProgress::Start => {
                    state.step_text.clear();
                    state.step_reasoning.clear();
                }
                ModelProgress::Message(text) => {
                    state.step_text.push_str(&text);
                    state.displayed.push_str(&text);
                    let _ = observer.sender.send(UiEvent::Text(text));
                }
                ModelProgress::Reasoning(text) => {
                    state.step_reasoning.push_str(&text);
                    let _ = observer.sender.send(UiEvent::Reasoning(text));
                }
            }
        })
    }
}

impl SessionEventObserver for TuiObserver {
    fn observe(&self, envelope: &EventEnvelope, _visibility: EventVisibility) {
        match &envelope.event {
            Event::ModelResponseItem {
                item: RuntimeItem::Message(message),
                ..
            } if message.role == RuntimeRole::Assistant => {
                let full = message
                    .content
                    .iter()
                    .filter_map(|block| match block {
                        ContentBlock::Text { text } => Some(text.as_str()),
                        _ => None,
                    })
                    .collect::<String>();
                let mut state = self.state.lock().expect("TUI progress lock poisoned");
                let extra = full
                    .strip_prefix(&state.step_text)
                    .unwrap_or(&full)
                    .to_owned();
                if !extra.is_empty() {
                    state.displayed.push_str(&extra);
                    let _ = self.sender.send(UiEvent::Text(extra));
                }
            }
            Event::ModelResponseItem {
                item: RuntimeItem::Reasoning(reasoning),
                ..
            } => {
                let full = reasoning.summary.join("\n\n");
                let state = self.state.lock().expect("TUI progress lock poisoned");
                let extra = full.strip_prefix(&state.step_reasoning).unwrap_or(&full);
                if !extra.is_empty() {
                    let _ = self.sender.send(UiEvent::Reasoning(extra.to_owned()));
                }
            }
            Event::ToolCallRequested { name, .. } => {
                let _ = self.sender.send(UiEvent::Tool(name.clone()));
            }
            Event::ToolCallCompleted { name, is_error, .. } => {
                let _ = self.sender.send(UiEvent::ToolDone(name.clone(), *is_error));
            }
            Event::RunCompleted {
                output: Some(output),
            } => {
                let state = self.state.lock().expect("TUI progress lock poisoned");
                if !output.is_empty() && !state.displayed.ends_with(output) {
                    let _ = self.sender.send(UiEvent::Text(output.clone()));
                }
            }
            _ => {}
        }
    }
}

fn entry_style(kind: Kind) -> Style {
    match kind {
        Kind::User => Style::default()
            .fg(Color::Cyan)
            .add_modifier(Modifier::BOLD),
        Kind::Assistant => Style::default().fg(Color::Green),
        Kind::Reasoning => Style::default().fg(Color::DarkGray),
        Kind::Tool => Style::default().fg(Color::Yellow),
        Kind::Info => Style::default().fg(Color::Gray),
        Kind::Error => Style::default().fg(Color::Red),
    }
}

fn render(frame: &mut Frame, app: &App) {
    let area = frame.area();
    if area.width < 25 || area.height < 8 {
        frame.render_widget(
            Paragraph::new("Enlarge the terminal to use Structure"),
            area,
        );
        return;
    }
    let input_lines = app.input.split('\n').count().clamp(1, 5) as u16;
    let completion_lines = app
        .completion
        .as_ref()
        .map_or(0, |view| view.candidates.len().min(5) as u16 + 2);
    let sections = Layout::vertical([
        Constraint::Length(1),
        Constraint::Min(3),
        Constraint::Length(completion_lines),
        Constraint::Length(input_lines + 2),
        Constraint::Length(1),
    ])
    .split(area);
    let heading = Line::from(vec![
        Span::styled(
            " Structure ",
            Style::default()
                .fg(Color::Black)
                .bg(Color::Cyan)
                .add_modifier(Modifier::BOLD),
        ),
        Span::raw(format!(
            "  {}  ·  session {}",
            app.workspace,
            app.session.chars().take(8).collect::<String>()
        )),
    ]);
    frame.render_widget(Paragraph::new(heading), sections[0]);

    let mut lines = Vec::new();
    for entry in &app.entries {
        if entry.kind == Kind::Reasoning && !app.show_thinking {
            continue;
        }
        let label = match entry.kind {
            Kind::User => "You",
            Kind::Assistant => "Structure",
            Kind::Reasoning => "Thinking",
            Kind::Tool => "Tool",
            Kind::Info => "Info",
            Kind::Error => "Error",
        };
        lines.push(Line::styled(label, entry_style(entry.kind)));
        for line in entry.text.split('\n') {
            lines.push(Line::styled(format!("  {line}"), entry_style(entry.kind)));
        }
        lines.push(Line::raw(""));
    }
    let transcript = Paragraph::new(Text::from(lines))
        .block(
            Block::default()
                .borders(Borders::ALL)
                .title(" Conversation "),
        )
        .wrap(Wrap { trim: false });
    let width = sections[1].width.saturating_sub(2).max(1);
    let visible_height = sections[1].height.saturating_sub(2) as usize;
    let total = transcript.line_count(width);
    let bottom = total.saturating_sub(visible_height);
    let offset = bottom
        .saturating_sub(app.scroll_from_bottom)
        .min(u16::MAX as usize) as u16;
    frame.render_widget(transcript.scroll((offset, 0)), sections[1]);

    if let Some(completion) = &app.completion {
        let start = completion.selected.saturating_sub(4);
        let lines = completion
            .candidates
            .iter()
            .enumerate()
            .skip(start)
            .take(5)
            .map(|(index, candidate)| {
                let style = if index == completion.selected {
                    Style::default().fg(Color::Black).bg(Color::Cyan)
                } else {
                    Style::default()
                };
                Line::styled(
                    format!(
                        " {} {}",
                        if index == completion.selected {
                            "›"
                        } else {
                            " "
                        },
                        candidate
                    ),
                    style,
                )
            })
            .collect::<Vec<_>>();
        frame.render_widget(
            Paragraph::new(lines).block(
                Block::default()
                    .borders(Borders::ALL)
                    .title(" Complete · Tab/Enter select "),
            ),
            sections[2],
        );
    }
    let editor_title = if app.busy {
        " Queued input "
    } else {
        " Message "
    };
    let cursor_row = app.input[..app.cursor]
        .chars()
        .filter(|&ch| ch == '\n')
        .count();
    let visible_rows = sections[3].height.saturating_sub(2).max(1) as usize;
    let first_row = cursor_row.saturating_sub(visible_rows - 1);
    let visible_input = app
        .input
        .split('\n')
        .skip(first_row)
        .take(visible_rows)
        .collect::<Vec<_>>()
        .join("\n");
    let input = Paragraph::new(visible_input)
        .block(Block::default().borders(Borders::ALL).title(editor_title))
        .wrap(Wrap { trim: false });
    frame.render_widget(input, sections[3]);
    if !app.busy && app.permission.is_none() && app.context.is_none() {
        let before = &app.input[..app.cursor];
        let row = cursor_row.saturating_sub(first_row) as u16;
        let column = Line::from(before.rsplit('\n').next().unwrap_or("")).width() as u16;
        let x = sections[3].x + 1 + column.min(sections[3].width.saturating_sub(3));
        let y = sections[3].y + 1 + row.min(sections[3].height.saturating_sub(3));
        frame.set_cursor_position((x, y));
    }
    let footer = format!(
        " {} · model {} · thinking {} · {}{} · PgUp/PgDn or wheel scroll · Ctrl+T thinking · Ctrl+C exit",
        app.status,
        app.model,
        app.thinking,
        if app.busy { "working" } else { "ready" },
        if app.queued.is_empty() {
            String::new()
        } else {
            format!(" · {} queued", app.queued.len())
        },
    );
    frame.render_widget(
        Paragraph::new(footer).style(Style::default().fg(Color::DarkGray)),
        sections[4],
    );

    if let Some(permission) = &app.permission {
        frame.render_widget(ratatui::widgets::Clear, area);
        if area.width < 50 || area.height < 10 {
            frame.render_widget(
                Paragraph::new("Enlarge the terminal to review tool permission"),
                area,
            );
            return;
        }
        let popup = ratatui::layout::Rect {
            x: area.x + 1,
            y: area.y + 1,
            width: area.width - 2,
            height: area.height - 2,
        };
        let block = Block::default()
            .borders(Borders::ALL)
            .title(format!(" Allow {}? ", permission.tool));
        let inner = block.inner(popup);
        frame.render_widget(block, popup);
        let sections = Layout::vertical([Constraint::Min(1), Constraint::Length(3)]).split(inner);
        let details = Paragraph::new(permission.details.as_str()).wrap(Wrap { trim: false });
        let total = details.line_count(sections[0].width.max(1));
        let max_scroll = total.saturating_sub(sections[0].height as usize);
        let scroll = (permission.scroll as usize).min(max_scroll) as u16;
        frame.render_widget(details.scroll((scroll, 0)), sections[0]);
        frame.render_widget(
            Paragraph::new(
                "↑/↓ or PgUp/PgDn: inspect arguments\n[y] Allow once  [a] Allow for session\n[n] Deny once   [v] Deny for session",
            )
            .style(Style::default().fg(Color::Yellow)),
            sections[1],
        );
    }
    if let Some(context) = &app.context {
        frame.render_widget(ratatui::widgets::Clear, area);
        let popup = ratatui::layout::Rect {
            x: area.x + 1,
            y: area.y + 1,
            width: area.width - 2,
            height: area.height - 2,
        };
        let block = Block::default().borders(Borders::ALL).title(" Context ");
        let inner = block.inner(popup);
        frame.render_widget(block, popup);
        let sections = Layout::vertical([Constraint::Min(1), Constraint::Length(1)]).split(inner);
        let details = Paragraph::new(context.text.as_str()).wrap(Wrap { trim: false });
        let total = details.line_count(sections[0].width.max(1));
        let max_scroll = total.saturating_sub(sections[0].height as usize);
        let scroll = (context.scroll as usize).min(max_scroll) as u16;
        frame.render_widget(details.scroll((scroll, 0)), sections[0]);
        frame.render_widget(
            Paragraph::new("↑/↓ PgUp/PgDn Home/End scroll · Esc or q closes")
                .style(Style::default().fg(Color::Yellow)),
            sections[1],
        );
    }
}

fn draw(terminal: &mut DefaultTerminal, app: &App) -> io::Result<()> {
    terminal.draw(|frame| render(frame, app))?;
    Ok(())
}

struct InputEvents {
    receiver: tokio::sync::mpsc::UnboundedReceiver<io::Result<InputEvent>>,
    stop: Arc<AtomicBool>,
}

impl Drop for InputEvents {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Relaxed);
    }
}

fn input_events() -> InputEvents {
    let (sender, receiver) = tokio::sync::mpsc::unbounded_channel();
    let stop = Arc::new(AtomicBool::new(false));
    let thread_stop = Arc::clone(&stop);
    tokio::task::spawn_blocking(move || {
        while !thread_stop.load(Ordering::Relaxed) {
            match event::poll(Duration::from_millis(100)) {
                Ok(true) => {
                    let result = event::read();
                    let failed = result.is_err();
                    if sender.send(result).is_err() || failed {
                        break;
                    }
                }
                Ok(false) => {}
                Err(error) => {
                    let _ = sender.send(Err(error));
                    break;
                }
            }
        }
    });
    InputEvents { receiver, stop }
}

/// Wheel-scroll line step. PgUp/PgDn move 10; a wheel notch feels right
/// around 3.
const WHEEL_STEP: u16 = 3;

/// Mouse wheel scrolling, mapped onto the same targets as PgUp/PgDn: the
/// context view when open, the permission details when a decision is
/// pending, otherwise the transcript. Returns whether the event was a
/// scroll; every other mouse event is ignored so terminals keep their
/// usual behavior (with capture on, text selection needs Shift held, the
/// standard trade-off).
fn handle_mouse(app: &mut App, mouse: MouseEvent) -> bool {
    let up = match mouse.kind {
        MouseEventKind::ScrollUp => true,
        MouseEventKind::ScrollDown => false,
        _ => return false,
    };
    if let Some(context) = app.context.as_mut() {
        context.scroll = if up {
            context.scroll.saturating_sub(WHEEL_STEP)
        } else {
            context.scroll.saturating_add(WHEEL_STEP)
        };
    } else if let Some(permission) = app.permission.as_mut() {
        permission.scroll = if up {
            permission.scroll.saturating_sub(WHEEL_STEP)
        } else {
            permission.scroll.saturating_add(WHEEL_STEP)
        };
    } else {
        app.scroll_from_bottom = if up {
            app.scroll_from_bottom
                .saturating_add(usize::from(WHEEL_STEP))
        } else {
            app.scroll_from_bottom
                .saturating_sub(usize::from(WHEEL_STEP))
        };
    }
    true
}

fn handle_editor(app: &mut App, event: InputEvent) -> EditorAction {
    match event {
        InputEvent::Paste(text) => app.insert(&text),
        InputEvent::Key(key) if key.kind == KeyEventKind::Press => match key {
            KeyEvent {
                code: KeyCode::Up, ..
            } if app.completion.is_some() => {
                let completion = app.completion.as_mut().expect("completion visible");
                completion.selected = completion.selected.saturating_sub(1);
                return EditorAction::None;
            }
            KeyEvent {
                code: KeyCode::Down,
                ..
            } if app.completion.is_some() => {
                let completion = app.completion.as_mut().expect("completion visible");
                completion.selected =
                    (completion.selected + 1).min(completion.candidates.len() - 1);
                return EditorAction::None;
            }
            KeyEvent {
                code: KeyCode::Tab, ..
            } if app.completion.is_some() => app.apply_completion(),
            KeyEvent {
                code: KeyCode::Enter,
                modifiers: KeyModifiers::NONE,
                ..
            } if app.completion.is_some() => app.apply_completion(),
            KeyEvent {
                code: KeyCode::Esc, ..
            } if app.completion.is_some() => {
                app.completion = None;
                return EditorAction::None;
            }
            KeyEvent {
                code: KeyCode::Char('c'),
                modifiers: KeyModifiers::CONTROL,
                ..
            } => return EditorAction::Exit,
            KeyEvent {
                code: KeyCode::Char('t'),
                modifiers: KeyModifiers::CONTROL,
                ..
            } => app.show_thinking = !app.show_thinking,
            KeyEvent {
                code: KeyCode::Char('j'),
                modifiers: KeyModifiers::CONTROL,
                ..
            } => app.insert("\n"),
            KeyEvent {
                code: KeyCode::Enter,
                modifiers,
                ..
            } if modifiers
                .intersects(KeyModifiers::SHIFT | KeyModifiers::CONTROL | KeyModifiers::ALT) =>
            {
                app.insert("\n")
            }
            KeyEvent {
                code: KeyCode::Enter,
                ..
            } => {
                let text = app.take_input();
                if !text.is_empty() {
                    return EditorAction::Submit(text);
                }
            }
            KeyEvent {
                code: KeyCode::Backspace,
                ..
            } => app.backspace(),
            KeyEvent {
                code: KeyCode::Delete,
                ..
            } => app.delete(),
            KeyEvent {
                code: KeyCode::Left,
                ..
            } if app.cursor > 0 => {
                app.cursor = app.input[..app.cursor]
                    .char_indices()
                    .next_back()
                    .map(|(i, _)| i)
                    .unwrap_or(0);
            }
            KeyEvent {
                code: KeyCode::Right,
                ..
            } if app.cursor < app.input.len() => {
                app.cursor += app.input[app.cursor..].chars().next().unwrap().len_utf8();
            }
            KeyEvent {
                code: KeyCode::Home,
                ..
            } => app.cursor = app.input[..app.cursor].rfind('\n').map_or(0, |i| i + 1),
            KeyEvent {
                code: KeyCode::End, ..
            } => {
                app.cursor += app.input[app.cursor..]
                    .find('\n')
                    .unwrap_or(app.input.len() - app.cursor)
            }
            KeyEvent {
                code: KeyCode::PageUp,
                ..
            } => app.scroll_from_bottom = app.scroll_from_bottom.saturating_add(10),
            KeyEvent {
                code: KeyCode::PageDown,
                ..
            } => app.scroll_from_bottom = app.scroll_from_bottom.saturating_sub(10),
            KeyEvent {
                code: KeyCode::Esc, ..
            } => {
                app.input.clear();
                app.cursor = 0;
            }
            KeyEvent {
                code: KeyCode::Char(ch),
                modifiers,
                ..
            } if !modifiers.intersects(KeyModifiers::CONTROL | KeyModifiers::ALT) => {
                app.insert(&ch.to_string())
            }
            _ => {}
        },
        _ => {}
    }
    app.refresh_completion();
    EditorAction::None
}

enum EditorAction {
    None,
    Submit(String),
    Exit,
}

fn command(session: &mut InteractiveSession, app: &mut App, text: &str) -> bool {
    if matches!(text, "/exit" | "/quit") {
        return true;
    }
    match text {
        "/help" => app.push(Kind::Info, "/help  /exit  /session  /sessions  /resume <id>  /context  /diff  /undo  /model <name>  /thinking <off|on|low|medium|high>\nEnter sends; Shift+Enter or Ctrl+J adds a line. Esc cancels a run. PageUp/PageDown scroll. Ctrl+T toggles thinking."),
        "/session" => app.push(Kind::Info, format!("session: {}\nworkspace: {}\nmodel: {}\nprovider: {}\nread only: {}\nshell: {}", session.session_id, session.runner_root.display(), session.config.model, session.config.api_type, session.read_only, session.allow_shell)),
        "/model" => app.push(Kind::Info, format!("current model: {}\nusage: /model <name>", session.config.model)),
        "/thinking" => app.push(Kind::Info, format!("current thinking: {}\nusage: /thinking <off|on|low|medium|high>", app.thinking)),
        "/diff" => app.push(Kind::Info, session.write_report()),
        "/undo" => app.push(Kind::Info, session.undo_last_write()),
        "/context" => match context::report(session) {
            Ok(text) => app.context = Some(ContextView { text, scroll: 0 }),
            Err(error) => app.push(Kind::Error, format!("context: {error}")),
        },
        _ if text.starts_with("/model ") => {
            let model = text.trim_start_matches("/model ").trim();
            if model.is_empty() { app.push(Kind::Error, "usage: /model <name>"); }
            else { match session.change_model(model) {
                Ok(()) => { app.refresh_config(session); app.push(Kind::Info, format!("model: {model}")); }
                Err(error) => app.push(Kind::Error, error.to_string()),
            }}
        }
        _ if text.starts_with("/thinking ") => {
            let level = text.trim_start_matches("/thinking ").trim();
            match session.change_thinking(level) {
                Ok(()) => { app.refresh_config(session); app.push(Kind::Info, format!("thinking: {level}")); }
                Err(error) => app.push(Kind::Error, error.to_string()),
            }
        }
        _ => app.push(Kind::Error, format!("unknown command: {text}")),
    }
    false
}

fn permission_decision(key: KeyEvent) -> Option<PermissionDecision> {
    match key.code {
        KeyCode::Char('y') => Some(PermissionDecision::allow_once()),
        KeyCode::Char('a') => Some(PermissionDecision::allow_for_session()),
        KeyCode::Char('v') => Some(PermissionDecision {
            outcome: ToolPermissionOutcome::Denied,
            scope: ToolPermissionScope::Session,
            source: ToolPermissionSource::User,
        }),
        KeyCode::Char('n') | KeyCode::Esc => Some(PermissionDecision::deny_once()),
        _ => None,
    }
}

async fn run_turn(
    session: &mut InteractiveSession,
    app: &mut App,
    terminal: &mut DefaultTerminal,
    keys: &mut tokio::sync::mpsc::UnboundedReceiver<io::Result<InputEvent>>,
    text: String,
) -> Result<(), Box<dyn std::error::Error>> {
    app.push(Kind::User, text.clone());
    app.busy = true;
    app.status = "Working".to_owned();
    let (ui_tx, mut ui_rx) = tokio::sync::mpsc::unbounded_channel();
    let tui_observer = TuiObserver::new(ui_tx);
    session.refresh_instructions();
    interactive::set_progress(
        session.manager.runtime_mut().model_mut(),
        tui_observer.sink(),
    );
    let observer: Arc<dyn SessionEventObserver> = Arc::new(FanOutObserver::new(vec![
        Arc::clone(&session.store) as Arc<dyn SessionEventObserver>,
        Arc::clone(&tui_observer) as Arc<dyn SessionEventObserver>,
    ]));
    let cancellation = RunCancellation::new();
    let (permission_tx, mut permission_rx) =
        tokio::sync::mpsc::unbounded_channel::<PermissionRequest>();
    let control = DispatchControl {
        run: RunControl {
            cancellation: Some(cancellation.clone()),
            permissions: Some(ToolPermissionGate {
                policy: interactive::policy(),
                approver: Some(permission_tx),
            }),
        },
        observer: Some(observer),
    };
    let envelope = CommandEnvelope::new(
        CommandId::new(uuid::Uuid::now_v7().to_string()),
        Some(session.session_id.clone()),
        Command::MessageSend { content: text },
    );
    let dispatch = session.manager.dispatch(envelope, control);
    tokio::pin!(dispatch);
    let mut pending: Option<PermissionRequest> = None;
    let mut tick = tokio::time::interval(Duration::from_millis(40));
    let result = loop {
        tokio::select! {
            result = &mut dispatch => break result,
            Some(update) = ui_rx.recv() => {
                app.apply(update);
                while let Ok(update) = ui_rx.try_recv() { app.apply(update); }
            }
            Some(request) = permission_rx.recv(), if pending.is_none() => {
                app.permission = Some(PermissionView::new(&request, &session.runner_root));
                pending = Some(request);
                draw(terminal, app)?;
            }
            Some(key) = keys.recv() => {
                let key = key?;
                if let InputEvent::Mouse(mouse) = &key {
                    handle_mouse(app, *mouse);
                } else if pending.is_some() {
                    if let InputEvent::Key(key) = key && key.kind == KeyEventKind::Press {
                        if let Some(decision) = permission_decision(key) {
                            let _ = pending.take().expect("permission pending").reply.send(decision);
                            app.permission = None;
                        } else if key.code == KeyCode::Char('c') && key.modifiers == KeyModifiers::CONTROL {
                            let _ = pending.take().expect("permission pending").reply.send(PermissionDecision::deny_once());
                            app.permission = None;
                            cancellation.cancel();
                        } else if let Some(permission) = app.permission.as_mut() {
                            permission.handle_scroll(key.code);
                        }
                    }
                } else if let InputEvent::Key(key) = key {
                    if key.kind == KeyEventKind::Press && (key.code == KeyCode::Esc || (key.code == KeyCode::Char('c') && key.modifiers == KeyModifiers::CONTROL)) {
                        cancellation.cancel();
                        app.status = "Cancelling".to_owned();
                    } else {
                        match handle_editor(app, InputEvent::Key(key)) {
                            EditorAction::Submit(text) => { app.queued.push_back(text); app.status = "Message queued".to_owned(); }
                            EditorAction::Exit => { cancellation.cancel(); app.status = "Cancelling".to_owned(); }
                            EditorAction::None => {}
                        }
                    }
                } else { let _ = handle_editor(app, key); }
                draw(terminal, app)?;
            }
            _ = tick.tick() => draw(terminal, app)?,
        }
    };
    while let Ok(update) = ui_rx.try_recv() {
        app.apply(update);
    }
    app.busy = false;
    app.permission = None;
    app.file_index = None;
    match result {
        Ok(events) => {
            app.status = "Ready".to_owned();
            for event in events {
                match event.event {
                    Event::RunFailed { message } => {
                        app.status = "Failed".to_owned();
                        app.push(Kind::Error, message);
                    }
                    Event::RunCancelled => {
                        app.status = "Cancelled".to_owned();
                        app.push(Kind::Info, "Run cancelled");
                    }
                    _ => {}
                }
            }
        }
        Err(error) => {
            app.status = "Failed".to_owned();
            app.push(Kind::Error, error.to_string());
        }
    }
    draw(terminal, app)?;
    Ok(())
}

struct RestoreTerminal;

impl Drop for RestoreTerminal {
    fn drop(&mut self) {
        let _ = crossterm::execute!(
            io::stdout(),
            event::DisableMouseCapture,
            event::DisableBracketedPaste
        );
        ratatui::restore();
    }
}

async fn run_inner(
    config: ApiProviderConfig,
    options: InteractiveOptions,
) -> Result<i32, Box<dyn std::error::Error>> {
    let mut session = InteractiveSession::open(config, options).await?;
    let mut app = App::new(&session);
    app.load_history(&session)?;
    let mut terminal = ratatui::try_init()?;
    let _restore = RestoreTerminal;
    crossterm::execute!(
        io::stdout(),
        event::EnableBracketedPaste,
        event::EnableMouseCapture
    )?;
    let mut keys = input_events();
    draw(&mut terminal, &app)?;
    loop {
        let next = if let Some(text) = app.queued.pop_front() {
            Some(text)
        } else {
            let key = keys
                .receiver
                .recv()
                .await
                .ok_or("terminal input closed")??;
            if let InputEvent::Mouse(mouse) = &key {
                handle_mouse(&mut app, *mouse);
                draw(&mut terminal, &app)?;
                continue;
            }
            if app.context.is_some() {
                if let InputEvent::Key(key) = key
                    && key.kind == KeyEventKind::Press
                {
                    if matches!(key.code, KeyCode::Esc | KeyCode::Char('q')) {
                        app.context = None;
                    } else if let Some(context) = app.context.as_mut() {
                        context.handle_scroll(key.code);
                    }
                }
                draw(&mut terminal, &app)?;
                continue;
            }
            match handle_editor(&mut app, key) {
                EditorAction::None => None,
                EditorAction::Submit(text) => Some(text),
                EditorAction::Exit => break,
            }
        };
        if let Some(text) = next {
            if text.starts_with('/') {
                // /sessions and /resume need awaits, so they are handled
                // here rather than in the synchronous command().
                if text == "/sessions" {
                    match session.session_list() {
                        Ok(lines) if lines.is_empty() => {
                            app.push(Kind::Info, "no sessions found");
                        }
                        Ok(lines) => app.push(Kind::Info, lines.join("\n")),
                        Err(error) => app.push(Kind::Error, error.to_string()),
                    }
                } else if let Some(id) = text.strip_prefix("/resume ") {
                    let id = id.trim();
                    if id.is_empty() {
                        app.push(Kind::Error, "usage: /resume <id>  (see /sessions)");
                    } else {
                        match session.switch_to(id).await {
                            Ok(next) => {
                                session = next;
                                app.refresh_config(&session);
                                app.push(Kind::Info, format!("session: {}", session.session_id));
                            }
                            Err(error) => app.push(Kind::Error, error.to_string()),
                        }
                    }
                } else if command(&mut session, &mut app, &text) {
                    break;
                }
            } else {
                run_turn(
                    &mut session,
                    &mut app,
                    &mut terminal,
                    &mut keys.receiver,
                    text,
                )
                .await?;
            }
        }
        draw(&mut terminal, &app)?;
    }
    Ok(0)
}

pub async fn run(config: ApiProviderConfig, options: InteractiveOptions) -> i32 {
    if options.allow_shell && options.read_only {
        eprintln!("error: --allow-shell and --read-only are mutually exclusive");
        return 2;
    }
    match run_inner(config, options).await {
        Ok(code) => code,
        Err(error) => {
            eprintln!("error: {error}");
            1
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use ratatui::{Terminal, backend::TestBackend};

    fn wheel(up: bool) -> MouseEvent {
        MouseEvent {
            kind: if up {
                MouseEventKind::ScrollUp
            } else {
                MouseEventKind::ScrollDown
            },
            column: 0,
            row: 0,
            modifiers: KeyModifiers::empty(),
        }
    }

    #[test]
    fn wheel_scrolls_transcript_context_and_permission_views() {
        let mut app = App::default();
        assert!(handle_mouse(&mut app, wheel(true)));
        assert_eq!(app.scroll_from_bottom, WHEEL_STEP as usize);
        for _ in 0..10 {
            handle_mouse(&mut app, wheel(false));
        }
        // Saturates at the bottom instead of wrapping around.
        assert_eq!(app.scroll_from_bottom, 0);

        // The context view scrolls independently while open.
        app.context = Some(ContextView {
            text: "long".to_owned(),
            scroll: 100,
        });
        handle_mouse(&mut app, wheel(true));
        assert_eq!(app.context.as_ref().unwrap().scroll, 100 - WHEEL_STEP);
        handle_mouse(&mut app, wheel(false));
        assert_eq!(app.context.as_ref().unwrap().scroll, 100);
        app.context = None;
        assert_eq!(app.scroll_from_bottom, 0);

        // Non-scroll mouse events (moves, drags) are left alone.
        assert!(!handle_mouse(
            &mut app,
            MouseEvent {
                kind: MouseEventKind::Moved,
                column: 1,
                row: 1,
                modifiers: KeyModifiers::empty(),
            }
        ));
    }

    #[test]
    fn editor_handles_multiline_and_unicode_backspace() {
        let mut app = App::default();
        app.insert("你好");
        app.insert("\nworld");
        app.backspace();
        assert_eq!(app.input, "你好\nworl");
        app.cursor = "你".len();
        app.backspace();
        assert_eq!(app.input, "好\nworl");
        assert_eq!(app.cursor, 0);
    }

    #[test]
    fn completion_replaces_command_and_file_tokens() {
        let files = vec!["src/main.rs".to_owned(), "docs/design notes.md".to_owned()];
        let models = vec!["model-a".to_owned()];
        let slash = completion_for("/con", 4, &files, &models).unwrap();
        assert_eq!(slash.candidates, vec!["/context"]);
        let file = completion_for("read @main", 10, &files, &models).unwrap();
        assert_eq!(file.candidates, vec!["@src/main.rs"]);
        assert_eq!(file.replace_start, 5);
        let spaced = completion_for("@design", 7, &files, &models).unwrap();
        assert_eq!(spaced.candidates, vec!["@\"docs/design notes.md\""]);
        assert!(completion_for("/help", 5, &files, &models).is_none());
    }

    #[test]
    fn completion_keyboard_selects_without_sending() {
        let mut app = App {
            workspace: "/nonexistent".to_owned(),
            input: "/th".to_owned(),
            cursor: 3,
            ..App::default()
        };
        app.refresh_completion();
        assert!(matches!(
            handle_editor(
                &mut app,
                InputEvent::Key(KeyEvent::new(KeyCode::Tab, KeyModifiers::NONE))
            ),
            EditorAction::None
        ));
        assert_eq!(app.input, "/thinking ");
        assert!(app.completion.is_some());
        assert!(matches!(
            handle_editor(
                &mut app,
                InputEvent::Key(KeyEvent::new(KeyCode::Down, KeyModifiers::NONE))
            ),
            EditorAction::None
        ));
        assert!(matches!(
            handle_editor(
                &mut app,
                InputEvent::Key(KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE))
            ),
            EditorAction::None
        ));
        assert_eq!(app.input, "/thinking on");
    }

    #[test]
    fn permission_keeps_choices_visible_for_long_file_content() {
        let mut app = App::default();
        app.entries.push(Entry {
            kind: Kind::Reasoning,
            text: "background reasoning".to_owned(),
        });
        app.permission = Some(PermissionView {
            tool: "write_file".to_owned(),
            details: format!(
                "Path: docs/summary.md\n\nContent:\n{}",
                "long line\n".repeat(100)
            ),
            scroll: 0,
        });
        let mut terminal = Terminal::new(TestBackend::new(80, 24)).unwrap();
        terminal.draw(|frame| render(frame, &app)).unwrap();
        let output = terminal
            .backend()
            .buffer()
            .content()
            .iter()
            .map(|cell| cell.symbol())
            .collect::<String>();
        assert!(output.contains("Path: docs/summary.md"));
        assert!(output.contains("[y] Allow once"));
        assert!(output.contains("[v] Deny for session"));
        assert!(!output.contains("background reasoning"));

        app.permission.as_mut().unwrap().handle_scroll(KeyCode::End);
        terminal.draw(|frame| render(frame, &app)).unwrap();
        let output = terminal
            .backend()
            .buffer()
            .content()
            .iter()
            .map(|cell| cell.symbol())
            .collect::<String>();
        assert!(output.contains("[y] Allow once"));
        assert!(output.contains("[v] Deny for session"));
    }

    #[test]
    fn context_view_keeps_controls_visible_when_scrolled() {
        let app = App {
            context: Some(ContextView {
                text: format!("LAST MODEL REQUEST · ACTUAL\n{}", "batch\n".repeat(100)),
                scroll: u16::MAX,
            }),
            ..App::default()
        };
        let mut terminal = Terminal::new(TestBackend::new(80, 24)).unwrap();
        terminal.draw(|frame| render(frame, &app)).unwrap();
        let output = terminal
            .backend()
            .buffer()
            .content()
            .iter()
            .map(|cell| cell.symbol())
            .collect::<String>();
        assert!(output.contains("Esc or q closes"));
        assert!(output.contains("batch"));
    }

    #[test]
    fn write_permission_shows_complete_content() {
        let content = "important detail\n".repeat(100);
        let details = permission_details(
            "write_file",
            &serde_json::json!({"path": "docs/summary.md", "content": content}),
            Path::new("."),
        );
        assert!(details.starts_with("Path: docs/summary.md\n\nChange preview"));
        assert!(details.ends_with("important detail\n"));
        assert_eq!(details.matches("important detail").count(), 100);
    }

    #[test]
    fn write_permission_preview_shows_changed_lines() {
        let root = std::env::temp_dir().join(format!("structure-preview-{}", uuid::Uuid::now_v7()));
        std::fs::create_dir(&root).unwrap();
        std::fs::write(root.join("note.txt"), "kept\nold\n").unwrap();
        let details = permission_details(
            "write_file",
            &serde_json::json!({"path":"note.txt","content":"kept\nnew\n"}),
            &root,
        );
        assert!(details.contains("  kept\n- old\n+ new\n"));
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn write_permission_preview_rejects_parent_traversal() {
        let preview = write_file_preview(Path::new("."), "../private.txt", "replacement");
        assert!(preview.contains("outside the workspace"));
        assert!(!preview.contains("replacement"));
    }

    #[test]
    fn permission_keys_map_to_expected_outcome_and_scope() {
        for (key, outcome, scope) in [
            (
                'y',
                ToolPermissionOutcome::Allowed,
                ToolPermissionScope::Once,
            ),
            (
                'a',
                ToolPermissionOutcome::Allowed,
                ToolPermissionScope::Session,
            ),
            (
                'n',
                ToolPermissionOutcome::Denied,
                ToolPermissionScope::Once,
            ),
            (
                'v',
                ToolPermissionOutcome::Denied,
                ToolPermissionScope::Session,
            ),
        ] {
            let decision =
                permission_decision(KeyEvent::new(KeyCode::Char(key), KeyModifiers::NONE)).unwrap();
            assert_eq!(decision.outcome, outcome);
            assert_eq!(decision.scope, scope);
        }
    }
}
