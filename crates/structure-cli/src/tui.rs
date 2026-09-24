//! Fullscreen terminal conversation: a scrolling history pane on top, a
//! Claude-style divider, and a pinned editor block at the bottom. Output
//! buffers into `Transcript` (rows grouped into blocks) and the renderer
//! paints the pane each frame, so block highlight, scrolling, and the
//! divider all stay consistent without scrollback surgery.
//!
//! Middle steps render folded: a tool call is one summary line whose
//! status flips in place, results are status lines, and file edits show a
//! colored diff. Ctrl+O toggles verbose printing for later events.

use std::collections::VecDeque;
use std::io;
use std::path::Path;
use std::sync::atomic::AtomicBool;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use crossterm::event::{self, Event as InputEvent, KeyCode, KeyEvent, KeyEventKind, KeyModifiers};
use crossterm::terminal::{disable_raw_mode, enable_raw_mode};
use ratatui::layout::{Constraint, Layout};
use ratatui::style::{Color, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::Paragraph;
use ratatui::{DefaultTerminal, Frame, Terminal};
use similar::{ChangeTag, TextDiff};
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

use crate::checkpoint::{self, WriteCheckpoint};
use crate::context;
use crate::host::workspace_id_for;
use crate::interactive::{self, InteractiveOptions, InteractiveSession};

/// Diff lines shown before folding when verbose mode is off.
const DIFF_FOLD_LINES: usize = 12;

// ---------------------------------------------------------------------------
// Theme: semantic color roles for the transcript
//
// Every role uses named ANSI colors (or Color::Reset, the terminal's own
// foreground), never hardcoded RGB, so the terminal's configured theme
// maps them and light/dark terminals both stay readable. Roles:
//   base     — assistant prose (inherits the terminal foreground)
//   user     — the user's own messages
//   tool     — tool-call lines and permission prompts (distinct at a glance)
//   muted    — thinking, hints, status bar, folded output (recedes)
//   added / removed — diff insertions and deletions
//   artifact — results and products: ✓ completions, file summaries
//   error    — failures
// ---------------------------------------------------------------------------
mod theme {
    use ratatui::style::{Color, Modifier, Style};

    pub(crate) fn base() -> Style {
        Style::default().fg(Color::Reset)
    }

    pub(crate) fn user() -> Style {
        Style::default()
            .fg(Color::Cyan)
            .add_modifier(Modifier::BOLD)
    }

    pub(crate) fn tool() -> Style {
        Style::default().fg(Color::Yellow)
    }

    /// A finished tool call: green check.
    pub(crate) fn tool_ok() -> Style {
        Style::default().fg(Color::Green)
    }

    pub(crate) fn muted() -> Style {
        Style::default().fg(Color::DarkGray)
    }

    pub(crate) fn added() -> Style {
        Style::default().fg(Color::Green)
    }

    pub(crate) fn removed() -> Style {
        Style::default().fg(Color::Red)
    }

    pub(crate) fn artifact() -> Style {
        Style::default().fg(Color::Cyan)
    }

    pub(crate) fn error() -> Style {
        Style::default().fg(Color::Red)
    }
}

// ---------------------------------------------------------------------------
// Buffered output helpers
// ---------------------------------------------------------------------------

/// Display width of one character: CJK and other wide characters count as
/// two columns. An approximation good enough for wrapping without a
/// unicode-width dependency.
fn char_width(ch: char) -> usize {
    if ch.is_ascii() { 1 } else { 2 }
}

fn text_width(text: &str) -> usize {
    text.chars().map(char_width).sum()
}

/// Hard-wraps `text` at display width `width`, keeping style per source
/// line. Every returned Line is a separate scrollback row.
fn wrap_styled(text: &str, width: usize, style: Style) -> Vec<Line<'static>> {
    let mut lines = Vec::new();
    for raw in text.split('\n') {
        if raw.is_empty() {
            lines.push(Line::styled(String::new(), style));
            continue;
        }
        let mut current = String::new();
        let mut current_width = 0;
        for ch in raw.chars() {
            let w = char_width(ch);
            if current_width + w > width && !current.is_empty() {
                lines.push(Line::styled(std::mem::take(&mut current), style));
                current_width = 0;
            }
            current.push(ch);
            current_width += w;
        }
        lines.push(Line::styled(current, style));
    }
    lines
}

/// Every row the session produced, grouped into blocks (one user message,
/// one tool line, one diff, one thinking summary...). The fullscreen
/// renderer paints from this buffer each frame; block navigation and
/// copy act on block indices.
#[derive(Default)]
struct Transcript {
    lines: Vec<Line<'static>>,
    blocks: Vec<(usize, usize)>,
}

impl Transcript {
    fn push_block(&mut self, lines: Vec<Line<'static>>) {
        let start = self.lines.len();
        self.lines.extend(lines);
        self.blocks.push((start, self.lines.len()));
    }

    /// Drops every recorded row and block, for `/resume` switching to a
    /// different session whose history is replayed from its own store.
    fn clear(&mut self) {
        self.lines.clear();
        self.blocks.clear();
    }
}

/// Replaces a one-row block's line in place (the running ⏺ line flipping
/// to ✓/✗). Returns `false` when the block is not a single row; the caller
/// then prints the status as its own row.
fn flip_tool_line(transcript: &mut Transcript, block: usize, line: Line<'static>) -> bool {
    let (start, end) = match transcript.blocks.get(block) {
        Some(span) => *span,
        None => return false,
    };
    if end - start != 1 {
        return false;
    }
    transcript.lines[start] = line;
    true
}

/// Minimal standard base64 encoder for OSC 52 clipboard payloads.
fn base64(data: &[u8]) -> String {
    const ALPHABET: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut out = String::with_capacity(data.len().div_ceil(3) * 4);
    for chunk in data.chunks(3) {
        let b0 = u32::from(chunk[0]);
        let b1 = u32::from(*chunk.get(1).unwrap_or(&0));
        let b2 = u32::from(*chunk.get(2).unwrap_or(&0));
        let triple = (b0 << 16) | (b1 << 8) | b2;
        out.push(ALPHABET[(triple >> 18) as usize & 0x3f] as char);
        out.push(ALPHABET[(triple >> 12) as usize & 0x3f] as char);
        out.push(if chunk.len() > 1 {
            ALPHABET[(triple >> 6) as usize & 0x3f] as char
        } else {
            '='
        });
        out.push(if chunk.len() > 2 {
            ALPHABET[triple as usize & 0x3f] as char
        } else {
            '='
        });
    }
    out
}

/// Copies a block's plain text to the system clipboard: OSC 52 first (SSH
/// transparent), then the platform command as fallback (macOS `pbcopy`,
/// Linux `wl-copy`/`xclip`, Windows `clip.exe`). Returns the copied lines.
fn copy_block_to_clipboard(transcript: &Transcript, block: usize) -> std::io::Result<usize> {
    use std::io::Write;
    let (start, end) = match transcript.blocks.get(block) {
        Some(span) => *span,
        None => return Ok(0),
    };
    let text = transcript.lines[start..end]
        .iter()
        .map(|line| {
            line.spans
                .iter()
                .map(|span| span.content.to_string())
                .collect::<String>()
        })
        .collect::<Vec<_>>()
        .join("\n");
    // OSC 52; terminals that disabled it simply ignore the sequence.
    let _ = write!(
        std::io::stdout(),
        "\x1b]52;c;{}\x07",
        base64(text.as_bytes())
    );
    #[cfg(target_os = "macos")]
    let command = "pbcopy";
    #[cfg(all(unix, not(target_os = "macos")))]
    let command = if std::env::var_os("WAYLAND_DISPLAY").is_some() {
        "wl-copy"
    } else {
        "xclip -selection clipboard"
    };
    #[cfg(windows)]
    let command = "clip";
    if let Ok(mut child) = std::process::Command::new(
        command
            .split_whitespace()
            .next()
            .expect("command has a program"),
    )
    .args(command.split_whitespace().skip(1))
    .stdin(std::process::Stdio::piped())
    .spawn()
    {
        if let Some(stdin) = child.stdin.as_mut() {
            let _ = stdin.write_all(text.as_bytes());
        }
        let _ = child.wait();
    }
    Ok(end - start)
}

/// Moves the scroll anchor one block up or down. The highlight itself is
/// applied by the renderer (reverse video on the selected block), so no
/// in-place terminal surgery is needed in fullscreen mode.
fn navigate_blocks(transcript: &Transcript, app: &mut App, up: bool) {
    let count = transcript.blocks.len();
    if count == 0 {
        return;
    }
    app.scroll_pinned = false;
    let current = app.highlighted.unwrap_or(count - 1);
    app.highlighted = Some(if up {
        current.saturating_sub(1)
    } else {
        (current + 1).min(count - 1)
    });
}

fn print_block(transcript: &mut Transcript, text: &str, style: Style) {
    let width = LAST_WIDTH
        .load(std::sync::atomic::Ordering::Relaxed)
        .max(40);
    let lines = wrap_styled(text, width, style);
    transcript.push_block(lines);
}

/// One assistant text block: no prefix, just the words, terminal width.
fn print_text(transcript: &mut Transcript, text: &str) {
    print_block(transcript, text, theme::base())
}

fn print_dim(transcript: &mut Transcript, text: &str) {
    print_block(transcript, text, theme::muted())
}

/// Tool-call lines and permission prompts: the "something is happening"
/// color, distinct from prose and from results.
fn print_tool(transcript: &mut Transcript, text: &str) {
    print_block(transcript, text, theme::tool())
}

/// A finished tool status line (green ✓).
fn print_tool_ok(transcript: &mut Transcript, text: &str) {
    print_block(transcript, text, theme::tool_ok())
}

/// Results and products: completions, file summaries, command output.
fn print_artifact(transcript: &mut Transcript, text: &str) {
    print_block(transcript, text, theme::artifact())
}

fn print_error(transcript: &mut Transcript, text: &str) {
    print_block(transcript, text, theme::error())
}

/// Terminal width as of the last draw; wrapping for buffered output uses
/// this. Defaults to 80 before the first frame.
static LAST_WIDTH: std::sync::atomic::AtomicUsize = std::sync::atomic::AtomicUsize::new(80);

/// A user's message, prefixed so it stands out in the scrollback.
fn print_user(transcript: &mut Transcript, text: &str) {
    // Claude-style divider above each user message.
    let width = LAST_WIDTH
        .load(std::sync::atomic::Ordering::Relaxed)
        .max(40);
    transcript.push_block(vec![Line::styled(
        "\u{2500}".repeat(width.saturating_sub(2)),
        theme::muted(),
    )]);
    let width = width.saturating_sub(2);
    let mut lines = Vec::new();
    for (index, raw) in wrap_styled(text, width.max(1), Style::default())
        .into_iter()
        .enumerate()
    {
        let prefix = if index == 0 { "> " } else { "  " };
        let mut spans = vec![Span::styled(prefix.to_owned(), theme::user())];
        spans.extend(
            raw.spans
                .into_iter()
                .map(|span| Span::styled(span.content.into_owned(), Style::default())),
        );
        lines.push(Line::from(spans));
    }
    lines.push(Line::raw(String::new()));
    transcript.push_block(lines);
}

// ---------------------------------------------------------------------------
// Folded tool rendering (pure, unit-testable)
// ---------------------------------------------------------------------------

/// One-line summary of a tool call: what it acts on, not the full JSON.
fn tool_summary(name: &str, arguments: &serde_json::Value) -> String {
    let path = || {
        arguments
            .get("path")
            .and_then(serde_json::Value::as_str)
            .unwrap_or("")
            .to_owned()
    };
    match name {
        "write_file" => format!(
            "{} ({} bytes)",
            path(),
            arguments
                .get("content")
                .and_then(serde_json::Value::as_str)
                .map(str::len)
                .unwrap_or(0)
        ),
        "read_file" | "delete_file" | "list_dir" => path(),
        "edit_files" => {
            let edits = arguments
                .get("edits")
                .and_then(serde_json::Value::as_array)
                .cloned()
                .unwrap_or_default();
            let files: Vec<String> = edits
                .iter()
                .filter_map(|edit| {
                    edit.get("path")
                        .and_then(serde_json::Value::as_str)
                        .map(str::to_owned)
                })
                .collect::<Vec<_>>();
            let mut unique = files.clone();
            unique.dedup();
            format!(
                "{} edit{} in {}",
                edits.len(),
                if edits.len() == 1 { "" } else { "s" },
                unique.join(", ")
            )
        }
        "grep" => arguments
            .get("pattern")
            .and_then(serde_json::Value::as_str)
            .unwrap_or("")
            .to_owned(),
        "find_files" => arguments
            .get("pattern")
            .and_then(serde_json::Value::as_str)
            .unwrap_or("")
            .to_owned(),
        "shell" => arguments
            .get("command")
            .and_then(serde_json::Value::as_str)
            .unwrap_or("")
            .to_owned(),
        "memory_search" => arguments
            .get("query")
            .and_then(serde_json::Value::as_str)
            .unwrap_or("")
            .to_owned(),
        _ => {
            let head = serde_json::to_string(arguments).unwrap_or_default();
            head.chars().take(60).collect()
        }
    }
}

/// Colored unified-diff lines for one file movement. Deletions are red,
/// insertions green, hunk headers cyan, context dim. `created` files diff
/// from empty so every added line shows as `+`.
fn movement_diff_lines(
    movement: &checkpoint::FileMovement,
    max_lines: usize,
) -> Vec<Line<'static>> {
    let before = movement
        .before
        .as_deref()
        .and_then(|bytes| std::str::from_utf8(bytes).ok());
    let after = movement
        .after
        .as_deref()
        .and_then(|bytes| std::str::from_utf8(bytes).ok());
    let (before, after) = match (before, after) {
        (Some(before), Some(after)) => (before, after),
        // Binary or missing content: show a byte-count note instead.
        _ => {
            let (from, to) = (
                movement.before.as_ref().map_or(0, Vec::len),
                movement.after.as_ref().map_or(0, Vec::len),
            );
            return vec![Line::styled(
                format!("    binary content, {from} → {to} bytes"),
                theme::muted(),
            )];
        }
    };
    let diff = TextDiff::from_lines(before, after);
    let mut lines = vec![Line::styled(
        format!("    {} ({})", movement.path, {
            let (added, removed) =
                diff.iter_all_changes()
                    .fold((0, 0), |(added, removed), change| match change.tag() {
                        ChangeTag::Insert => (added + 1, removed),
                        ChangeTag::Delete => (added, removed + 1),
                        ChangeTag::Equal => (added, removed),
                    });
            format!("+{added} -{removed}")
        }),
        theme::artifact(),
    )];
    let body = diff.unified_diff().context_radius(3).to_string();
    for (shown, row) in body.lines().enumerate() {
        if shown >= max_lines {
            lines.push(Line::styled(
                format!("    … {max_lines}+ lines, Ctrl+O for verbose"),
                theme::muted(),
            ));
            break;
        }
        let style = if row.starts_with("@@") {
            theme::muted()
        } else if row.starts_with('-') && !row.starts_with("---") {
            theme::removed()
        } else if row.starts_with('+') && !row.starts_with("+++") {
            theme::added()
        } else {
            theme::muted()
        };
        lines.push(Line::styled(format!("  {row}"), style));
    }
    lines
}

// ---------------------------------------------------------------------------
// Editor state and completion (kept from the full-screen TUI)
// ---------------------------------------------------------------------------

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

fn permission_details(tool: &str, arguments: &serde_json::Value) -> String {
    if tool == "write_file"
        && let (Some(path), Some(content)) = (
            arguments.get("path").and_then(|v| v.as_str()),
            arguments.get("content").and_then(|v| v.as_str()),
        )
    {
        return format!("Path: {path}\n{content}");
    }
    if tool == "edit_files"
        && let Some(edits) = arguments.get("edits").and_then(|v| v.as_array())
    {
        return edits
            .iter()
            .map(|edit| {
                format!(
                    "{}: {} → {}",
                    edit.get("path").and_then(|v| v.as_str()).unwrap_or("?"),
                    edit.get("old_string")
                        .and_then(|v| v.as_str())
                        .unwrap_or(""),
                    edit.get("new_string")
                        .and_then(|v| v.as_str())
                        .unwrap_or(""),
                )
            })
            .collect::<Vec<_>>()
            .join("\n");
    }
    if tool == "delete_file" {
        return format!(
            "Path: {}",
            arguments
                .get("path")
                .and_then(|v| v.as_str())
                .unwrap_or("?")
        );
    }
    String::new()
}

// ---------------------------------------------------------------------------
// App state
// ---------------------------------------------------------------------------

struct App {
    input: String,
    cursor: usize,
    queued: VecDeque<String>,
    busy: bool,
    show_thinking: bool,
    verbose: bool,
    completion: Option<CompletionView>,
    /// Streaming text of the not-yet-newline-terminated assistant line.
    live: String,
    /// Streaming thinking for the current step, folded into one dim block
    /// once real output starts.
    live_reasoning: String,
    models: Vec<String>,
    status: String,
    /// When set, the next submitted input resolves a `/resume` listing.
    resume_pick: bool,
    /// Session ids behind the pending `/resume` listing, in display order.
    pick_entries: Option<Vec<String>>,
    file_index: Option<Vec<String>>,
    /// Selected row of the inline permission chooser while a decision is
    /// pending (0..4: allow once / allow session / deny once / deny session).
    permission_choice: usize,
    /// Block currently highlighted by up/down navigation, if any.
    highlighted: Option<usize>,
    /// False once the user scrolled the history pane away from the bottom;
    /// new output stops auto-following until they return.
    scroll_pinned: bool,
    /// Set while a permission decision is pending: the tool label shown in
    /// the viewport chooser.
    permission_prompt: Option<String>,
    /// The transcript block of the currently-running tool line, so its
    /// completion flips that same line to ✓/✗ instead of printing a second
    /// row.
    running_tool: Option<usize>,
}

/// The permission chooser's options, in navigation order.
const PERMISSION_OPTIONS: [&str; 4] = [
    "Allow once",
    "Allow always",
    "Deny once",
    "Deny for session",
];

impl Default for App {
    fn default() -> Self {
        Self {
            input: String::new(),
            cursor: 0,
            queued: VecDeque::new(),
            busy: false,
            show_thinking: true,
            verbose: false,
            completion: None,
            live: String::new(),
            live_reasoning: String::new(),
            models: Vec::new(),
            status: "Ready".to_owned(),
            resume_pick: false,
            pick_entries: None,
            file_index: None,
            permission_choice: 0,
            highlighted: None,
            scroll_pinned: true,
            permission_prompt: None,
            running_tool: None,
        }
    }
}

impl App {
    fn new(session: &InteractiveSession) -> Self {
        Self {
            models: configured_models(&session.config.model),
            ..Self::default()
        }
    }

    fn insert(&mut self, text: &str) {
        let cursor = self.cursor.min(self.input.len());
        self.input.insert_str(cursor, text);
        self.cursor += text.len();
        self.refresh_completion();
    }

    fn backspace(&mut self) {
        if self.cursor > 0 {
            let cursor = self.cursor.min(self.input.len());
            let remove = self.input[..cursor]
                .chars()
                .next_back()
                .map_or(0, char::len_utf8);
            self.input.replace_range(cursor - remove..cursor, "");
            self.cursor -= remove;
            self.refresh_completion();
        }
    }

    fn delete(&mut self) {
        let cursor = self.cursor.min(self.input.len());
        if cursor < self.input.len() {
            let remove = self.input[cursor..]
                .chars()
                .next()
                .map_or(0, char::len_utf8);
            self.input.replace_range(cursor..cursor + remove, "");
            self.refresh_completion();
        }
    }

    fn take_input(&mut self) -> String {
        self.cursor = 0;
        std::mem::take(&mut self.input)
    }

    fn refresh_completion(&mut self) {
        self.completion = completion_for(
            &self.input,
            self.cursor,
            self.file_index.as_deref().unwrap_or(&[]),
            &self.models,
        )
        .filter(|_| !self.busy);
    }

    fn apply_completion(&mut self) {
        let Some(completion) = self.completion.as_ref() else {
            return;
        };
        let candidate = completion.candidates[completion.selected].clone();
        let start = completion.replace_start;
        let end = completion.replace_end.min(self.input.len()).max(start);
        self.input.replace_range(start..end, &candidate);
        self.cursor = start + candidate.len();
        self.completion = None;
    }
}

// ---------------------------------------------------------------------------
// Events from the running turn
// ---------------------------------------------------------------------------

enum UiEvent {
    Text(String),
    Reasoning(String),
    ToolStart {
        name: String,
        summary: String,
    },
    ToolDone {
        call_id: String,
        name: String,
        is_error: bool,
        result: String,
    },
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
            Event::ToolCallRequested {
                name, arguments, ..
            } => {
                let _ = self.sender.send(UiEvent::ToolStart {
                    name: name.clone(),
                    summary: tool_summary(name, arguments),
                });
            }
            Event::ToolCallCompleted {
                call_id,
                name,
                result,
                is_error,
                ..
            } => {
                let _ = self.sender.send(UiEvent::ToolDone {
                    call_id: call_id.clone(),
                    name: name.clone(),
                    is_error: *is_error,
                    result: result.clone(),
                });
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

// ---------------------------------------------------------------------------
// Terminal plumbing
// ---------------------------------------------------------------------------

struct InputEvents {
    receiver: tokio::sync::mpsc::UnboundedReceiver<io::Result<InputEvent>>,
    stop: Arc<AtomicBool>,
}

impl Drop for InputEvents {
    fn drop(&mut self) {
        self.stop.store(true, std::sync::atomic::Ordering::Relaxed);
    }
}

fn input_events() -> InputEvents {
    use std::sync::atomic::Ordering;
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

enum EditorAction {
    None,
    Submit(String),
}

/// Restores the terminal even on early returns and errors.
struct InlineGuard;

impl Drop for InlineGuard {
    fn drop(&mut self) {
        let _ = disable_raw_mode();
        let _ = crossterm::execute!(io::stdout(), crossterm::cursor::Show);
        // The transcript lives in the alternate buffer; print the last
        // status so exiting does not feel like losing the session.
        println!();
    }
}

/// Draws the bottom viewport: streaming tail, completion hint, input row,
/// status line.
fn draw(terminal: &mut DefaultTerminal, app: &App, transcript: &Transcript) -> io::Result<()> {
    LAST_WIDTH.store(
        terminal
            .size()
            .map(|area| area.width as usize)
            .unwrap_or(80),
        std::sync::atomic::Ordering::Relaxed,
    );
    terminal
        .draw(|frame| render_fullscreen(frame, app, transcript))
        .map(|_| ())
}

/// Fullscreen layout: scrolling history pane on top, Claude-style divider
/// pinned above the bottom editor block, editor + hints + status at the
/// bottom. The highlight (reverse video) is applied here, at render time.
fn render_fullscreen(frame: &mut Frame, app: &App, transcript: &Transcript) {
    let area = frame.area();
    let sections = Layout::vertical([
        Constraint::Min(3),    // history pane
        Constraint::Length(1), // divider
        Constraint::Length(1), // streaming tail / completion / permission prompt
        Constraint::Length(1), // input
        Constraint::Length(1), // hints
        Constraint::Length(1), // status
    ])
    .split(area);

    // ---- history pane ----
    let visible_rows = sections[0].height as usize;
    // Re-wrap nothing: rows were wrapped at commit time with LAST_WIDTH.
    // Just flatten to (line, highlighted?) pairs.
    let highlight = app.highlighted;
    let mut rows: Vec<(&Line, bool)> = Vec::new();
    for (index, (start, end)) in transcript.blocks.iter().enumerate() {
        let highlighted = highlight.is_some_and(|h| h == index);
        for line in &transcript.lines[*start..*end] {
            rows.push((line, highlighted));
        }
    }
    // Auto-follow the bottom unless the user scrolled away.
    let total = rows.len();
    let max_scroll = total.saturating_sub(visible_rows);
    let offset = if app.scroll_pinned {
        max_scroll
    } else if let Some(highlight) = highlight {
        // Keep the highlighted block's first row visible.
        let first_row = transcript
            .blocks
            .get(highlight)
            .map(|(start, _)| *start)
            .unwrap_or(total);
        first_row.saturating_sub(visible_rows / 4).min(max_scroll)
    } else {
        max_scroll
    };
    let shown = &rows[offset.min(total)..(offset + visible_rows).min(total)];
    let lines: Vec<Line> = shown
        .iter()
        .map(|(line, highlighted)| {
            if *highlighted {
                Line::from(
                    line.spans
                        .iter()
                        .map(|span| {
                            Span::styled(
                                span.content.to_string(),
                                span.style.add_modifier(ratatui::style::Modifier::REVERSED),
                            )
                        })
                        .collect::<Vec<_>>(),
                )
            } else {
                Line::from(
                    line.spans
                        .iter()
                        .map(|span| Span::styled(span.content.to_string(), span.style))
                        .collect::<Vec<_>>(),
                )
            }
        })
        .collect();
    frame.render_widget(Paragraph::new(lines), sections[0]);

    // ---- divider ----
    let divider = "\u{2500}".repeat((sections[1].width as usize).saturating_sub(1).max(1));
    frame.render_widget(
        Paragraph::new(Line::styled(divider, theme::muted())),
        sections[1],
    );

    // ---- row: streaming tail / completion / permission prompt ----
    if let Some(prompt) = &app.permission_prompt {
        let mut options: Vec<Span> =
            vec![Span::styled(prompt.clone(), theme::tool()), Span::raw("  ")];
        for (index, label) in PERMISSION_OPTIONS.iter().enumerate() {
            let label = if index == app.permission_choice {
                format!(" [{label}] ")
            } else {
                format!("  {label}  ")
            };
            let style = if index == app.permission_choice {
                Style::default().fg(Color::Black).bg(Color::Cyan)
            } else {
                theme::muted()
            };
            options.push(Span::styled(label, style));
        }
        frame.render_widget(Paragraph::new(Line::from(options)), sections[2]);
    } else if let Some(completion) = &app.completion {
        let candidates = completion
            .candidates
            .iter()
            .enumerate()
            .map(|(index, candidate)| {
                let label = if index == completion.selected {
                    format!(" [{candidate}] ")
                } else {
                    format!(" {candidate} ")
                };
                let style = if index == completion.selected {
                    Style::default().fg(Color::Black).bg(Color::Cyan)
                } else {
                    theme::muted()
                };
                Span::styled(label, style)
            })
            .collect::<Vec<_>>();
        frame.render_widget(Paragraph::new(Line::from(candidates)), sections[2]);
    } else if app.busy && !app.live.is_empty() {
        // Assistant text streams here; thinking streams inside the
        // history pane below.
        let tail: String = {
            let width = sections[2].width as usize;
            app.live
                .chars()
                .rev()
                .take(width * 2)
                .collect::<Vec<_>>()
                .into_iter()
                .rev()
                .collect()
        };
        frame.render_widget(
            Paragraph::new(Line::styled(tail, theme::base())),
            sections[2],
        );
    }

    // Streaming thinking grows inside the history pane: its tail renders
    // as the pane's bottom rows, dim, before being folded into a summary
    // block on flush.
    if app.busy && !app.live_reasoning.is_empty() {
        let width = sections[0].width.max(1) as usize;
        let pane_rows = sections[0].height as usize;
        let tail: String = {
            let budget = width
                .saturating_mul(pane_rows.saturating_sub(offset))
                .max(width);
            app.live_reasoning
                .chars()
                .rev()
                .take(budget * 2)
                .collect::<Vec<_>>()
                .into_iter()
                .rev()
                .collect()
        };
        let wrapped: Vec<Line> = wrap_styled(&tail, width, theme::muted())
            .into_iter()
            .take(pane_rows.saturating_sub(offset))
            .collect();
        let pane = sections[0];
        let tail_rows = wrapped.len() as u16;
        let tail_area = ratatui::layout::Rect {
            x: pane.x,
            y: pane.y + pane.height.saturating_sub(tail_rows),
            width: pane.width,
            height: tail_rows,
        };
        frame.render_widget(ratatui::widgets::Clear, tail_area);
        frame.render_widget(Paragraph::new(wrapped), tail_area);
    }

    // ---- input row ----
    let last_line = app.input[app.input[..app.cursor.min(app.input.len())]
        .rfind('\n')
        .map_or(0, |i| i + 1)..]
        .split('\n')
        .next()
        .unwrap_or("")
        .to_owned();
    let before_width = text_width(&last_line);
    frame.render_widget(
        Paragraph::new(Line::from(vec![
            Span::styled("\u{276f} ".to_owned(), theme::user()),
            Span::raw(last_line),
        ])),
        sections[3],
    );
    let cursor_x = 2 + before_width as u16;
    let cursor_x = cursor_x.min(area.width.saturating_sub(1));
    if !app.busy && app.permission_prompt.is_none() {
        frame.set_cursor_position((cursor_x, sections[3].y));
    }

    // ---- hints row ----
    frame.render_widget(
        Paragraph::new(Line::styled(
            " Enter send \u{b7} Shift+Enter newline \u{b7} \u{2191}\u{2193} blocks \u{b7} Enter copies block \u{b7} Ctrl+O verbose \u{b7} /help",
            theme::muted(),
        )),
        sections[4],
    );

    // ---- status row ----
    let status = format!(" {}", app.status);
    frame.render_widget(
        Paragraph::new(Line::styled(status, theme::muted())),
        sections[5],
    );
}

// ---------------------------------------------------------------------------
// Turn execution
// ---------------------------------------------------------------------------

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

/// The number of bytes of `live` that are safe to commit to the scrollback:
/// every complete line before the opening line of an unmatched ``` code
/// fence. Lines inside an open fence hold back until the fence closes, so
/// a half-streamed block never renders as garbled text (the Codex/pi/
/// Gemini streaming approach). Always a line boundary; `0` when nothing is
/// committable.
fn committable_prefix(live: &str) -> usize {
    let Some(last_newline) = live.rfind('\n') else {
        return 0;
    };
    let complete = &live[..last_newline + 1];
    let mut in_fence = false;
    let mut open_fence_end = 0;
    let mut offset = 0;
    for line in complete.split_inclusive('\n') {
        let line_len = line.len();
        if line.trim_start().starts_with("```") {
            in_fence = !in_fence;
            if in_fence {
                // Hold everything after this delimiter line until the
                // fence closes.
                open_fence_end = offset + line_len;
            }
        }
        offset += line_len;
    }
    if in_fence {
        open_fence_end
    } else {
        complete.len()
    }
}

/// Flushes any unterminated streaming line into the scrollback.
fn flush_live(transcript: &mut Transcript, app: &mut App) {
    if !app.live.is_empty() {
        let text = std::mem::take(&mut app.live);
        print_text(transcript, &text);
    }
}

/// Flushes the accumulated thinking block as one dim paragraph, prefixed
/// on its first line. Called when real output, a tool call, or the end of
/// the turn arrives -- thinking stays together instead of one row per
/// delta.
fn flush_reasoning(transcript: &mut Transcript, app: &mut App) {
    if !app.live_reasoning.is_empty() {
        let text = std::mem::take(&mut app.live_reasoning);
        if app.verbose {
            print_dim(transcript, &format!("· {text}"));
        } else {
            let words = text.split_whitespace().count();
            print_dim(transcript, &format!("· thinking · {words} words"));
        }
    }
}

/// The write-checkpoint for a finished call, for diff rendering.
fn checkpoint_for(
    journal: &std::sync::Arc<std::sync::Mutex<checkpoint::WriteJournal>>,
    call_id: &str,
) -> Option<WriteCheckpoint> {
    let journal = journal.lock().expect("write journal lock poisoned");
    journal.latest_for_call(call_id)
}

async fn run_turn(
    session: &mut InteractiveSession,
    app: &mut App,
    terminal: &mut DefaultTerminal,
    transcript: &mut Transcript,
    keys: &mut tokio::sync::mpsc::UnboundedReceiver<io::Result<InputEvent>>,
    text: String,
) -> Result<(), Box<dyn std::error::Error>> {
    app.busy = true;
    app.status = "Working".to_owned();
    app.refresh_completion();
    print_user(transcript, &text);
    draw(terminal, app, transcript)?;

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
    // Taken before dispatch's mutable borrow of the session: the loop
    // below reads write checkpoints while the run is in flight.
    let write_journal = session.manager.runtime().runner().write_journal();
    let dispatch = session.manager.dispatch(envelope, control);
    tokio::pin!(dispatch);
    let mut pending: Option<PermissionRequest> = None;
    let mut tick = tokio::time::interval(Duration::from_millis(40));
    let result = loop {
        tokio::select! {
            result = &mut dispatch => break result,
            Some(update) = ui_rx.recv() => {
                match update {
                    UiEvent::Text(text) => {
                        flush_reasoning(transcript, app);
                        app.live.push_str(&text);
                        // Completed lines go straight to the scrollback,
                        // except while a code fence is open: those lines
                        // hold back until the fence closes, so a half-
                        // streamed block never renders as broken text.
                        loop {
                            let end = committable_prefix(&app.live);
                            let Some(newline) = app.live[..end].find('\n') else {
                                break;
                            };
                            let line: String = app.live.drain(..newline + 1).collect();
                            print_text(transcript, line.trim_end_matches('\n'));
                        }
                    }
                    UiEvent::Reasoning(text) => {
                        if app.show_thinking {
                            flush_live(transcript, app);
                            app.live_reasoning.push_str(&text);
                        }
                    }
                    UiEvent::ToolStart { name, summary } => {
                        flush_reasoning(transcript, app);
                        flush_live(transcript, app);
                        let line = if summary.is_empty() {
                            format!("⏺ {name}")
                        } else {
                            format!("⏺ {name} · {summary}")
                        };
                        print_tool(transcript, &line);
                        app.running_tool = Some(transcript.blocks.len() - 1);
                    }
                    UiEvent::ToolDone {
                        call_id,
                        name,
                        is_error,
                        result,
                    } => {
                        flush_reasoning(transcript, app);
                        flush_live(transcript, app);
                        // Flip the running ⏺ line in place to ✓/✗ when it
                        // is still a single recorded row; otherwise print
                        // the status as its own row.
                        let status_line = if is_error {
                            Line::styled(format!("✗ {name}"), theme::removed())
                        } else {
                            Line::styled(format!("✓ {name}"), theme::tool_ok())
                        };
                        let mut printed_status = false;
                        if let Some(block) = app.running_tool.take()
                            && flip_tool_line(transcript, block, status_line.clone())
                        {
                            printed_status = true;
                        }
                        if is_error {
                            if !printed_status {
                                transcript.push_block(vec![status_line]);
                            }
                        } else if let Some(checkpoint) = checkpoint_for(&write_journal, &call_id) {
                            let max_lines = if app.verbose {
                                usize::MAX
                            } else {
                                DIFF_FOLD_LINES
                            };
                            for movement in &checkpoint.movements {
                                transcript
                                    .push_block(movement_diff_lines(movement, max_lines));
                            }
                        } else if !printed_status {
                            // No file movement to show: a quiet result line
                            // confirms the call landed.
                            print_artifact(transcript, &format!("  ✓ {name}"));
                        }
                        // Result preview under the status line, unless the
                        // result is trivial or a diff already told the story.
                        let already_shown = checkpoint_for(&write_journal, &call_id).is_some();
                        let preview = result.trim();
                        if !preview.is_empty()
                            && !already_shown
                            && preview != "ok"
                            && !preview.starts_with("wrote ")
                        {
                            let max_chars = if app.verbose {
                                8_000
                            } else {
                                400
                            };
                            let mut excerpt: String = preview.chars().take(max_chars).collect();
                            if preview.chars().count() > max_chars {
                                let total_lines = preview.lines().count();
                                excerpt.push_str(&format!(
                                    "\n  … truncated, {total_lines} lines total (Ctrl+O verbose)"
                                ));
                            }
                            print_block(
                                transcript,
                                &format!("  {excerpt}"),
                                theme::muted(),
                            );
                        }
                    }
                }
                draw(terminal, app, transcript)?;
            }
            Some(request) = permission_rx.recv(), if pending.is_none() => {
                flush_reasoning(transcript, app);
                flush_live(transcript, app);
                let details = permission_details(&request.call.name, &request.call.arguments);
                if app.running_tool.is_none() {
                    // The observer's ⏺ line already announced this call;
                    // only print one when there is none to flip later.
                    print_tool(transcript, &format!("⏺ {} · {}", request.call.name, tool_summary(&request.call.name, &request.call.arguments)));
                    app.running_tool = Some(transcript.blocks.len() - 1);
                }
                if !details.is_empty() {
                    print_dim(transcript, &details);
                }
                app.status = "Waiting for permission".to_owned();
                app.permission_choice = 0;
                app.permission_prompt = Some(format!("Allow {}?", request.call.name));
                pending = Some(request);
                draw(terminal, app, transcript)?;
            }
            Some(key) = keys.recv() => {
                let key = key?;
                if pending.is_some() {
                    if let InputEvent::Key(key) = key && key.kind == KeyEventKind::Press {
                        let decision = match key.code {
                            KeyCode::Up => {
                                app.permission_choice =
                                    (app.permission_choice + PERMISSION_OPTIONS.len() - 1)
                                        % PERMISSION_OPTIONS.len();
                                None
                            }
                            KeyCode::Down => {
                                app.permission_choice =
                                    (app.permission_choice + 1) % PERMISSION_OPTIONS.len();
                                None
                            }
                            KeyCode::Enter => Some(match app.permission_choice {
                                1 => PermissionDecision::allow_for_session(),
                                2 => PermissionDecision::deny_once(),
                                3 => PermissionDecision {
                                    outcome: ToolPermissionOutcome::Denied,
                                    scope: ToolPermissionScope::Session,
                                    source: ToolPermissionSource::User,
                                },
                                _ => PermissionDecision::allow_once(),
                            }),
                            _ => permission_decision(key),
                        };
                        if let Some(decision) = decision {
                            let _ = pending.take().expect("permission pending").reply.send(decision);
                            app.permission_prompt = None;
                            app.permission_choice = 0;
                            app.status = "Working".to_owned();
                        } else if key.code == KeyCode::Char('c') && key.modifiers == KeyModifiers::CONTROL {
                            let _ = pending.take().expect("permission pending").reply.send(PermissionDecision::deny_once());
                            app.permission_prompt = None;
                            cancellation.cancel();
                        }
                    }
                } else if let InputEvent::Key(key) = key {
                    if key.kind == KeyEventKind::Press && (key.code == KeyCode::Esc || (key.code == KeyCode::Char('c') && key.modifiers == KeyModifiers::CONTROL)) {
                        cancellation.cancel();
                        app.status = "Cancelling".to_owned();
                    } else if key.kind == KeyEventKind::Press && key.code == KeyCode::Char('o') && key.modifiers == KeyModifiers::CONTROL {
                        app.verbose = !app.verbose;
                        app.status = if app.verbose { "Verbose".to_owned() } else { "Working".to_owned() };
                    } else {
                        match handle_editor(app, InputEvent::Key(key)) {
                            EditorAction::Submit(text) => { app.queued.push_back(text); app.status = "Queued".to_owned(); }
                            EditorAction::None => {}
                        }
                    }
                }
                draw(terminal, app, transcript)?;
            }
            _ = tick.tick() => draw(terminal, app, transcript)?,
        }
    };
    while let Ok(update) = ui_rx.try_recv() {
        if let UiEvent::Text(text) = update {
            app.live.push_str(&text);
        } else if app.show_thinking
            && let UiEvent::Reasoning(text) = update
        {
            app.live_reasoning.push_str(&text);
        }
    }
    flush_reasoning(transcript, app);
    flush_live(transcript, app);
    app.busy = false;
    app.permission_cleanup();
    match result {
        Ok(events) => {
            app.status = "Ready".to_owned();
            for event in events {
                match event.event {
                    Event::RunFailed { message } => {
                        app.status = "Failed".to_owned();
                        print_error(transcript, &format!("✗ {message}"));
                    }
                    Event::RunCancelled => {
                        app.status = "Cancelled".to_owned();
                        print_dim(transcript, "run cancelled");
                    }
                    _ => {}
                }
            }
        }
        Err(error) => {
            app.status = "Failed".to_owned();
            print_error(transcript, &error.to_string());
        }
    }
    println!(); // blank line after each turn
    draw(terminal, app, transcript)?;
    Ok(())
}

impl App {
    fn permission_cleanup(&mut self) {
        // Reserved for future pending-permission UI state.
    }
}

fn handle_editor(app: &mut App, event: InputEvent) -> EditorAction {
    match event {
        InputEvent::Paste(text) => {
            app.insert(&text);
            EditorAction::None
        }
        InputEvent::Key(key) if key.kind == KeyEventKind::Press => match key {
            KeyEvent {
                code: KeyCode::Up | KeyCode::Left,
                ..
            } if app.completion.is_some() => {
                if let Some(completion) = app.completion.as_mut() {
                    completion.selected = completion.selected.saturating_sub(1);
                }
                EditorAction::None
            }
            KeyEvent {
                code: KeyCode::Down | KeyCode::Right,
                ..
            } if app.completion.is_some() => {
                if let Some(completion) = app.completion.as_mut() {
                    completion.selected =
                        (completion.selected + 1).min(completion.candidates.len() - 1);
                }
                EditorAction::None
            }
            KeyEvent {
                code: KeyCode::Enter | KeyCode::Tab,
                modifiers,
                ..
            } if app.completion.is_some()
                && !modifiers.contains(KeyModifiers::SHIFT)
                && !modifiers.contains(KeyModifiers::CONTROL) =>
            {
                app.apply_completion();
                EditorAction::None
            }
            KeyEvent {
                code: KeyCode::Enter,
                modifiers,
                ..
            } if modifiers.contains(KeyModifiers::SHIFT)
                || modifiers.contains(KeyModifiers::ALT) =>
            {
                app.insert("\n");
                EditorAction::None
            }
            KeyEvent {
                code: KeyCode::Enter,
                ..
            } => {
                let text = app.take_input();
                EditorAction::Submit(text)
            }
            KeyEvent {
                code: KeyCode::Backspace,
                modifiers,
                ..
            } if modifiers.contains(KeyModifiers::CONTROL) => {
                // Clear the whole input line.
                app.input.clear();
                app.cursor = 0;
                app.refresh_completion();
                EditorAction::None
            }
            KeyEvent {
                code: KeyCode::Backspace,
                ..
            } => {
                app.backspace();
                EditorAction::None
            }
            KeyEvent {
                code: KeyCode::Delete,
                ..
            } => {
                app.delete();
                EditorAction::None
            }
            KeyEvent {
                code: KeyCode::Left,
                ..
            } => {
                if app.cursor > 0 {
                    let cursor = app.cursor.min(app.input.len());
                    app.cursor -= app.input[..cursor]
                        .chars()
                        .next_back()
                        .map_or(0, char::len_utf8);
                }
                EditorAction::None
            }
            KeyEvent {
                code: KeyCode::Right,
                ..
            } => {
                if app.cursor < app.input.len() {
                    app.cursor += app.input[app.cursor..].chars().next().unwrap().len_utf8();
                }
                EditorAction::None
            }
            KeyEvent {
                code: KeyCode::Home,
                ..
            } => {
                app.cursor = app.input[..app.cursor].rfind('\n').map_or(0, |i| i + 1);
                EditorAction::None
            }
            KeyEvent {
                code: KeyCode::End, ..
            } => {
                app.cursor += app.input[app.cursor..]
                    .find('\n')
                    .unwrap_or(app.input.len() - app.cursor);
                EditorAction::None
            }
            KeyEvent {
                code: KeyCode::Esc, ..
            } => {
                app.input.clear();
                app.cursor = 0;
                app.refresh_completion();
                EditorAction::None
            }
            KeyEvent {
                code: KeyCode::Char(ch),
                modifiers,
                ..
            } if !modifiers.intersects(KeyModifiers::CONTROL | KeyModifiers::ALT) => {
                app.insert(&ch.to_string());
                EditorAction::None
            }
            _ => EditorAction::None,
        },
        _ => EditorAction::None,
    }
}

// ---------------------------------------------------------------------------
// Slash commands (results are printed into the scrollback)
// ---------------------------------------------------------------------------

/// Returns true when the REPL should exit.
fn command(
    session: &mut InteractiveSession,
    app: &mut App,
    transcript: &mut Transcript,
    text: &str,
) -> bool {
    if matches!(text, "/exit" | "/quit") {
        return true;
    }
    match text {
        "/help" => {
            print_dim(
                transcript,
                "/help  /exit  /session  /sessions  /resume [id]  /diff  /undo  /model <name>  /thinking <off|on|low|medium|high>\nCtrl+O verbose · Ctrl+T thinking · Esc cancels a run · @path mentions files",
            );
        }
        "/session" => {
            print_dim(
                transcript,
                &format!(
                    "session: {}\nworkspace: {}\nmodel: {}\nprovider: {}\nread only: {}\nshell: {}",
                    session.session_id,
                    session.runner_root.display(),
                    session.config.model,
                    session.config.api_type,
                    session.read_only,
                    session.allow_shell
                ),
            );
        }
        "/model" => {
            print_dim(
                transcript,
                &format!(
                    "current model: {}\nusage: /model <name>",
                    session.config.model
                ),
            );
        }
        "/thinking" => {
            print_dim(transcript, "usage: /thinking <off|on|low|medium|high>");
        }
        "/diff" => {
            print_block(transcript, &session.write_report(), Style::default());
        }
        "/undo" => {
            let message = session.undo_last_write();
            print_artifact(transcript, &message);
        }
        "/context" => match context::report(session) {
            Ok(report) => {
                print_block(transcript, &report, theme::muted());
            }
            Err(error) => {
                print_error(transcript, &format!("context: {error}"));
            }
        },
        "/sessions" => match session.session_list() {
            Ok(lines) if lines.is_empty() => {
                print_dim(transcript, "no sessions found");
            }
            Ok(lines) => {
                print_dim(transcript, &lines.join("\n"));
            }
            Err(error) => {
                print_error(transcript, &error.to_string());
            }
        },
        "/resume" => {
            // List sessions and let the next input pick one.
            match session.session_list_entries() {
                Ok(entries) if entries.is_empty() => {
                    print_dim(transcript, "no sessions found");
                }
                Ok(entries) => {
                    let listing = entries
                        .iter()
                        .enumerate()
                        .map(|(index, entry)| format!("{:>3}. {}", index + 1, entry.line))
                        .collect::<Vec<_>>()
                        .join("\n");
                    print_dim(
                        transcript,
                        &format!("{listing}\nresume: type a number or id prefix"),
                    );
                    app.resume_pick = true;
                    app.pick_entries = Some(entries.into_iter().map(|entry| entry.id).collect());
                }
                Err(error) => {
                    print_error(transcript, &error.to_string());
                }
            }
        }
        _ if text.starts_with("/model ") => {
            let model = text.trim_start_matches("/model ").trim();
            if model.is_empty() {
                print_error(transcript, "usage: /model <name>");
            } else {
                match session.change_model(model) {
                    Ok(()) => {
                        print_tool(transcript, &format!("model: {model}"));
                    }
                    Err(error) => {
                        print_error(transcript, &error.to_string());
                    }
                }
            }
        }
        _ if text.starts_with("/thinking ") => {
            let level = text.trim_start_matches("/thinking ").trim();
            match session.change_thinking(level) {
                Ok(()) => {
                    print_tool(transcript, &format!("thinking: {level}"));
                }
                Err(error) => {
                    print_error(transcript, &error.to_string());
                }
            }
        }
        _ => {
            print_error(transcript, &format!("unknown command: {text}"));
        }
    }
    false
}

// ---------------------------------------------------------------------------
// Entry points
// ---------------------------------------------------------------------------

fn print_history(transcript: &mut Transcript, session: &InteractiveSession) {
    let workspace = workspace_id_for(&session.runner_root);
    let stored = match FileSessionStore::read_session(
        &session.structure_home,
        &workspace,
        &session.session_id,
    ) {
        Ok(stored) => stored,
        Err(_) => return,
    };
    // Assistant responses arrive as many delta events; buffer each turn's
    // text so a reply renders as one paragraph, not one block per delta.
    // Thinking folds to a word-count summary, matching live rendering.
    let mut turns = 0usize;
    let mut pending_text = String::new();
    let mut pending_reasoning_words = 0usize;
    let mut pending_reasoning = false;
    for envelope in stored.events {
        match envelope.event {
            Event::MessageAccepted { content } => {
                if !pending_text.is_empty() {
                    let text = std::mem::take(&mut pending_text);
                    print_text(transcript, text.trim());
                }
                if pending_reasoning {
                    print_dim(
                        transcript,
                        &format!("· thinking · {pending_reasoning_words} words"),
                    );
                    pending_reasoning = false;
                    pending_reasoning_words = 0;
                }
                print_user(transcript, &content);
                turns += 1;
            }
            Event::ModelResponseItem {
                item: RuntimeItem::Message(message),
                ..
            } if message.role == RuntimeRole::Assistant => {
                for block in &message.content {
                    if let ContentBlock::Text { text } = block {
                        pending_text.push_str(text);
                    }
                }
            }
            Event::ModelResponseItem {
                item: RuntimeItem::Reasoning(reasoning),
                ..
            } => {
                pending_reasoning_words += reasoning.summary.join(" ").split_whitespace().count();
                pending_reasoning = true;
            }
            Event::ToolCallRequested {
                name, arguments, ..
            } => {
                if !pending_text.is_empty() {
                    let text = std::mem::take(&mut pending_text);
                    print_text(transcript, text.trim());
                }
                if pending_reasoning {
                    print_dim(
                        transcript,
                        &format!("· thinking · {pending_reasoning_words} words"),
                    );
                    pending_reasoning = false;
                    pending_reasoning_words = 0;
                }
                print_tool(
                    transcript,
                    &format!("\u{23fa} {name} \u{b7} {}", tool_summary(&name, &arguments)),
                );
            }
            Event::ToolCallCompleted { name, is_error, .. } => {
                if is_error {
                    print_error(transcript, &format!("\u{2717} {name}"));
                } else {
                    print_tool_ok(transcript, &format!("\u{2713} {name}"));
                }
            }
            Event::ModelResponseCompleted { .. } => {
                if !pending_text.is_empty() {
                    let text = std::mem::take(&mut pending_text);
                    print_text(transcript, text.trim());
                }
                if pending_reasoning {
                    print_dim(
                        transcript,
                        &format!("· thinking · {pending_reasoning_words} words"),
                    );
                    pending_reasoning = false;
                    pending_reasoning_words = 0;
                }
            }
            _ => {}
        }
    }
    if !pending_text.is_empty() {
        let text = std::mem::take(&mut pending_text);
        print_text(transcript, text.trim());
    }
    if turns > 0 {
        print_dim(
            transcript,
            &format!("\u{2014} resumed session, {turns} earlier messages \u{2014}"),
        );
    }
}

async fn run_inner(
    config: ApiProviderConfig,
    options: InteractiveOptions,
) -> Result<i32, Box<dyn std::error::Error>> {
    let mut session = InteractiveSession::open(config, options).await?;
    let mut app = App::new(&session);
    app.status = format!("session {}", session.session_id);

    enable_raw_mode()?;
    let _guard = InlineGuard;
    crossterm::execute!(io::stdout(), crossterm::event::EnableBracketedPaste)?;
    let backend = ratatui::backend::CrosstermBackend::new(io::stdout());
    let mut terminal = Terminal::new(backend)?;

    let mut transcript = Transcript::default();
    print_history(&mut transcript, &session);
    app.file_index = Some(workspace_files(&session.runner_root));
    draw(&mut terminal, &app, &transcript)?;

    let mut keys = input_events();
    loop {
        let next = if let Some(text) = app.queued.pop_front() {
            Some(text)
        } else {
            let key = keys
                .receiver
                .recv()
                .await
                .ok_or("terminal input closed")??;
            // Scroll keys land here only when the editor does not consume
            // them (handled below); scrolling moves the history pane while
            // the input stays pinned. Any typed character snaps back to
            // the bottom.
            if !app.busy
                && app.permission_prompt.is_none()
                && let InputEvent::Key(key) = &key
                && key.kind == KeyEventKind::Press
                && matches!(key.code, KeyCode::Up | KeyCode::Down)
                && (app.completion.is_none() || app.input.is_empty())
            {
                // Block-granularity scrolling: move one block per press.
                navigate_blocks(&transcript, &mut app, key.code == KeyCode::Up);
                draw(&mut terminal, &app, &transcript)?;
                continue;
            }
            // With a block highlighted, Enter or c copies it.
            if app.input.is_empty()
                && app.completion.is_none()
                && !app.busy
                && let InputEvent::Key(key) = &key
                && key.kind == KeyEventKind::Press
                && app.highlighted.is_some()
                && matches!(key.code, KeyCode::Enter | KeyCode::Char('c'))
            {
                let block = app.highlighted.expect("checked above");
                let copied = match copy_block_to_clipboard(&transcript, block) {
                    Ok(copied) => copied,
                    Err(error) => {
                        print_error(&mut transcript, &error.to_string());
                        0
                    }
                };
                if copied > 0 {
                    print_dim(
                        &mut transcript,
                        &format!("  \u{29c9} copied {copied} lines"),
                    );
                }
                draw(&mut terminal, &app, &transcript)?;
                continue;
            }
            match handle_editor(&mut app, key) {
                EditorAction::None => None,
                EditorAction::Submit(text) => Some(text),
            }
        };
        if let Some(text) = next {
            if app.resume_pick {
                // Resolve the pending /resume selection.
                app.resume_pick = false;
                let ids = app.pick_entries.take().unwrap_or_default();
                let trimmed = text.trim();
                let id = if let Ok(number) = trimmed.parse::<usize>() {
                    ids.get(number.saturating_sub(1)).cloned()
                } else {
                    ids.iter().find(|id| id.starts_with(trimmed)).cloned()
                };
                match id {
                    Some(id) => match session.switch_to(&id).await {
                        Ok(next_session) => {
                            session = next_session;
                            app = App::new(&session);
                            transcript.clear();
                            print_tool(
                                &mut transcript,
                                &format!("session: {}", session.session_id),
                            );
                            print_history(&mut transcript, &session);
                        }
                        Err(error) => {
                            print_error(&mut transcript, &error.to_string());
                        }
                    },
                    None => {
                        print_error(&mut transcript, &format!("no session matches '{trimmed}'"));
                    }
                }
                draw(&mut terminal, &app, &transcript)?;
                continue;
            }
            if text.starts_with('/') {
                if command(&mut session, &mut app, &mut transcript, &text) {
                    break;
                }
            } else if !text.trim().is_empty() {
                run_turn(
                    &mut session,
                    &mut app,
                    &mut terminal,
                    &mut transcript,
                    &mut keys.receiver,
                    text,
                )
                .await?;
            }
        }
        draw(&mut terminal, &app, &transcript)?;
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
    use serde_json::json;

    #[test]
    fn committable_prefix_holds_back_open_fences() {
        // Plain prose: every complete line commits immediately.
        assert_eq!(
            committable_prefix("hello\nworld\npartial"),
            "hello\nworld\n".len()
        );
        assert_eq!(committable_prefix("no newline yet"), 0);

        // A fence opens: prose before it commits, everything after holds.
        let text = "Look:\n```rust\nfn main() {}\nstill inside\n";
        assert_eq!(committable_prefix(text), "Look:\n```rust\n".len());

        // Once the fence closes, everything commits again.
        let closed = "Look:\n```rust\ncode\n```\nafter\nmore\n";
        assert_eq!(committable_prefix(closed), closed.len());

        // Two fences on and off.
        let twice = "```\na\n```\ntext\n```\nb\n";
        assert_eq!(committable_prefix(twice), "```\na\n```\ntext\n```\n".len());

        // Indented fence delimiters still count.
        assert_eq!(committable_prefix("  ```\nbody\n"), "  ```\n".len());
    }

    #[test]
    fn base64_encodes_the_standard_alphabet() {
        assert_eq!(base64(b""), "");
        assert_eq!(base64(b"f"), "Zg==");
        assert_eq!(base64(b"fo"), "Zm8=");
        assert_eq!(base64(b"foo"), "Zm9v");
        assert_eq!(base64(b"foobar"), "Zm9vYmFy");
    }

    #[test]
    fn flip_tool_line_replaces_single_row_blocks_only() {
        let mut transcript = Transcript::default();
        transcript.push_block(vec![Line::styled(
            "⏺ read_file a.txt".to_owned(),
            theme::tool(),
        )]);
        transcript.push_block(vec![Line::raw("diff line 1"), Line::raw("diff line 2")]);
        assert!(flip_tool_line(
            &mut transcript,
            0,
            Line::styled("✓ read_file a.txt".to_owned(), theme::tool_ok())
        ));
        assert_eq!(transcript.lines[0].spans[0].content, "✓ read_file a.txt");
        // Multi-row blocks are refused so row counts stay stable.
        assert!(!flip_tool_line(&mut transcript, 1, Line::raw("nope")));
        assert_eq!(transcript.lines[1].spans[0].content, "diff line 1");
        assert!(!flip_tool_line(
            &mut transcript,
            99,
            Line::raw("out of range")
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
        app.cursor = 0;
    }

    #[test]
    fn completion_for_slash_and_file_tokens() {
        let files = vec!["src/main.rs".to_owned()];
        let models = vec!["m1".to_owned(), "m2".to_owned()];
        let slash = completion_for("/con", 4, &files, &models).unwrap();
        assert_eq!(slash.candidates, vec!["/context"]);
        let none = completion_for("/help", 5, &files, &models);
        assert!(none.is_none());
        let file = completion_for("@ma", 3, &files, &models).unwrap();
        assert_eq!(file.candidates, vec!["@src/main.rs"]);
        let model = completion_for("/model ", 7, &files, &models).unwrap();
        assert_eq!(model.candidates.len(), 2);
    }

    #[test]
    fn apply_completion_replaces_the_token() {
        let mut app = App {
            models: vec!["alpha".to_owned()],
            ..Default::default()
        };
        app.insert("/model al");
        assert!(app.completion.is_some());
        app.apply_completion();
        assert_eq!(app.input, "/model alpha");
    }

    #[test]
    fn tool_summary_names_the_target() {
        assert_eq!(
            tool_summary("write_file", &json!({"path": "a.txt", "content": "hello"})),
            "a.txt (5 bytes)"
        );
        assert_eq!(
            tool_summary(
                "edit_files",
                &json!({"edits": [
                    {"path": "a.txt", "old_string": "x", "new_string": "y"},
                    {"path": "a.txt", "old_string": "y", "new_string": "z"},
                    {"path": "b.txt", "old_string": "1", "new_string": "2"}
                ]})
            ),
            "3 edits in a.txt, b.txt"
        );
        assert_eq!(
            tool_summary("delete_file", &json!({"path": "b.txt"})),
            "b.txt"
        );
        assert_eq!(
            tool_summary("shell", &json!({"command": "cargo test"})),
            "cargo test"
        );
    }

    #[test]
    fn movement_diff_lines_color_and_fold() {
        let movement = checkpoint::FileMovement {
            path: "note.txt".to_owned(),
            before: Some(b"alpha\nbeta\n".to_vec()),
            after: Some(b"alpha\ngamma\n".to_vec()),
        };
        let lines = movement_diff_lines(&movement, usize::MAX);
        assert!(lines[0].spans[0].content.contains("note.txt"), "{lines:?}");
        assert!(lines[0].spans[0].content.contains("+1 -1"), "{lines:?}");
        let rendered: Vec<String> = lines
            .iter()
            .map(|line| line.spans.iter().map(|span| span.content.clone()).collect())
            .collect();
        assert!(
            rendered.iter().any(|line| line.contains("-beta")),
            "{rendered:?}"
        );
        assert!(
            rendered.iter().any(|line| line.contains("+gamma")),
            "{rendered:?}"
        );

        // Folding truncates the body and says how to expand it.
        let long = checkpoint::FileMovement {
            path: "big.txt".to_owned(),
            before: Some(Vec::new()),
            after: Some("line\n".repeat(50).into_bytes()),
        };
        let folded = movement_diff_lines(&long, 5);
        assert!(folded.len() < 10, "{folded:?}");
        let rendered: String = folded
            .iter()
            .map(|line| {
                line.spans
                    .iter()
                    .map(|span| span.content.clone())
                    .collect::<String>()
            })
            .collect();
        assert!(rendered.contains("Ctrl+O"), "{rendered}");
    }

    #[test]
    fn wrap_styled_respects_display_width() {
        // Wide CJK characters count as two columns.
        let lines = wrap_styled("你好世界x", 5, Style::default());
        assert_eq!(lines.len(), 2, "{lines:?}");
        assert_eq!(lines[0].spans[0].content, "你好");
        assert_eq!(lines[1].spans[0].content, "世界x");
    }
}
