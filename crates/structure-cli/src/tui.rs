//! Full-screen terminal conversation built on the same session and runtime as plain chat.

use std::collections::VecDeque;
use std::io;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use crossterm::event::{self, Event as InputEvent, KeyCode, KeyEvent, KeyEventKind, KeyModifiers};
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

impl PermissionView {
    fn new(request: &PermissionRequest) -> Self {
        Self {
            tool: request.call.name.clone(),
            details: permission_details(&request.call.name, &request.call.arguments),
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

fn permission_details(tool: &str, arguments: &serde_json::Value) -> String {
    if tool == "write_file"
        && let (Some(path), Some(content)) = (
            arguments.get("path").and_then(|v| v.as_str()),
            arguments.get("content").and_then(|v| v.as_str()),
        )
    {
        return format!("Path: {path}\n\nContent:\n{content}");
    }
    serde_json::to_string_pretty(arguments).unwrap_or_else(|_| arguments.to_string())
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
    let sections = Layout::vertical([
        Constraint::Length(1),
        Constraint::Min(3),
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

    let editor_title = if app.busy {
        " Queued input "
    } else {
        " Message "
    };
    let cursor_row = app.input[..app.cursor]
        .chars()
        .filter(|&ch| ch == '\n')
        .count();
    let visible_rows = sections[2].height.saturating_sub(2).max(1) as usize;
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
    frame.render_widget(input, sections[2]);
    if !app.busy && app.permission.is_none() {
        let before = &app.input[..app.cursor];
        let row = cursor_row.saturating_sub(first_row) as u16;
        let column = Line::from(before.rsplit('\n').next().unwrap_or("")).width() as u16;
        let x = sections[2].x + 1 + column.min(sections[2].width.saturating_sub(3));
        let y = sections[2].y + 1 + row.min(sections[2].height.saturating_sub(3));
        frame.set_cursor_position((x, y));
    }
    let footer = format!(
        " {} · model {} · thinking {} · {}{} · PgUp/PgDn scroll · Ctrl+T thinking · Ctrl+C exit",
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
        sections[3],
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

fn handle_editor(app: &mut App, event: InputEvent) -> EditorAction {
    match event {
        InputEvent::Paste(text) => app.insert(&text),
        InputEvent::Key(key) if key.kind == KeyEventKind::Press => match key {
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
        "/help" => app.push(Kind::Info, "/help  /exit  /session  /model <name>  /thinking <off|on|low|medium|high>\nEnter sends; Shift+Enter or Ctrl+J adds a line. Esc cancels a run. PageUp/PageDown scroll. Ctrl+T toggles thinking."),
        "/session" => app.push(Kind::Info, format!("session: {}\nworkspace: {}\nmodel: {}\nprovider: {}\nread only: {}\nshell: {}", session.session_id, session.runner_root.display(), session.config.model, session.config.api_type, session.read_only, session.allow_shell)),
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
                app.permission = Some(PermissionView::new(&request));
                pending = Some(request);
                draw(terminal, app)?;
            }
            Some(key) = keys.recv() => {
                let key = key?;
                if pending.is_some() {
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
        let _ = crossterm::execute!(io::stdout(), event::DisableBracketedPaste);
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
    crossterm::execute!(io::stdout(), event::EnableBracketedPaste)?;
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
            match handle_editor(&mut app, key) {
                EditorAction::None => None,
                EditorAction::Submit(text) => Some(text),
                EditorAction::Exit => break,
            }
        };
        if let Some(text) = next {
            if text.starts_with('/') {
                if command(&mut session, &mut app, &text) {
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
    fn write_permission_shows_complete_content() {
        let content = "important detail\n".repeat(100);
        let details = permission_details(
            "write_file",
            &serde_json::json!({"path": "docs/summary.md", "content": content}),
        );
        assert!(details.starts_with("Path: docs/summary.md\n\nContent:\n"));
        assert!(details.ends_with("important detail\n"));
        assert_eq!(details.matches("important detail").count(), 100);
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
