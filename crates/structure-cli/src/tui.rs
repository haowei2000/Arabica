//! Inline terminal conversation, Claude-Code style: the transcript is
//! printed straight into the terminal's native scrollback (so normal
//! scrolling, selection, and search keep working) while a small viewport
//! pinned to the bottom owns the input editor and status line.
//!
//! Middle steps render folded: a tool call is one summary line, results
//! are status lines, and file edits show a colored diff. Ctrl+O toggles
//! verbose printing for later events.

use std::collections::VecDeque;
use std::io;
use std::path::Path;
use std::sync::atomic::AtomicBool;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use crossterm::SynchronizedUpdate as _;
use crossterm::event::{self, Event as InputEvent, KeyCode, KeyEvent, KeyEventKind, KeyModifiers};
use crossterm::terminal::{disable_raw_mode, enable_raw_mode};
use ratatui::layout::{Constraint, Layout};
use ratatui::style::{Color, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::Paragraph;
use ratatui::{DefaultTerminal, Frame, Terminal, TerminalOptions, Viewport};
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

/// Rows owned by the inline viewport at the bottom of the screen.
const VIEWPORT_LINES: u16 = 4;
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
        Style::default().fg(Color::Magenta)
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
// Styled printing into the scrollback
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

/// In-process mirror of what we have committed to the scrollback: every
/// row we printed, grouped into blocks (one user message, one tool line,
/// one diff, one thinking summary...). Powers the up/down block
/// navigation: the terminal cannot repaint true scrollback, but the
/// bottom-pinned viewport fixes a known screen origin, so blocks that are
/// still on screen can be redrawn with a highlight.
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
}

/// Commits one block: prints the rows above the viewport and records them.
fn commit_lines(
    terminal: &mut DefaultTerminal,
    transcript: &mut Transcript,
    lines: Vec<Line<'static>>,
) -> io::Result<()> {
    insert_lines(terminal, lines.clone())?;
    transcript.push_block(lines);
    Ok(())
}

/// Screen y of committed line index `i`, given the viewport is pinned to
/// the bottom: negative means the row has scrolled off the screen.
fn screen_row(line_index: usize, total_lines: usize, rows: u16) -> i32 {
    let viewport_top = i32::from(rows.saturating_sub(VIEWPORT_LINES));
    viewport_top - (total_lines - line_index - 1) as i32 - 1
}

fn ratatui_color(color: ratatui::style::Color) -> crossterm::style::Color {
    use crossterm::style::Color as C;
    match color {
        ratatui::style::Color::Reset => C::Reset,
        ratatui::style::Color::Black => C::Black,
        ratatui::style::Color::Red => C::DarkRed,
        ratatui::style::Color::Green => C::DarkGreen,
        ratatui::style::Color::Yellow => C::DarkYellow,
        ratatui::style::Color::Blue => C::DarkBlue,
        ratatui::style::Color::Magenta => C::DarkMagenta,
        ratatui::style::Color::Cyan => C::DarkCyan,
        ratatui::style::Color::Gray => C::Grey,
        ratatui::style::Color::DarkGray => C::DarkGrey,
        ratatui::style::Color::LightRed => C::Red,
        ratatui::style::Color::LightGreen => C::Green,
        ratatui::style::Color::LightYellow => C::Yellow,
        ratatui::style::Color::LightBlue => C::Blue,
        ratatui::style::Color::LightMagenta => C::Magenta,
        ratatui::style::Color::LightCyan => C::Cyan,
        ratatui::style::Color::White => C::White,
        ratatui::style::Color::Indexed(value) => C::AnsiValue(value),
        ratatui::style::Color::Rgb(r, g, b) => C::Rgb { r, g, b },
    }
}

/// Moves the block highlight up or down across blocks that are still on
/// screen. Only repaints the previous (unhighlighted) and next
/// (highlighted) blocks.
fn navigate_blocks(
    terminal: &mut DefaultTerminal,
    transcript: &Transcript,
    app: &mut App,
    up: bool,
) -> io::Result<()> {
    let (_, rows) = crossterm::terminal::size()?;
    let total = transcript.lines.len();
    let visible: Vec<usize> = transcript
        .blocks
        .iter()
        .enumerate()
        .filter(|(_, span)| span.1 > 0 && screen_row(span.1 - 1, total, rows) >= 0)
        .map(|(index, _)| index)
        .collect();
    if visible.is_empty() {
        return Ok(());
    }
    let next = match app.highlighted {
        None => {
            if up {
                *visible.last().expect("visible is not empty")
            } else {
                // Down from nothing starts at the oldest visible block.
                visible[0]
            }
        }
        Some(current) => {
            match visible.iter().position(|&block| block == current) {
                Some(position) => {
                    if up {
                        visible[position.saturating_sub(1)]
                    } else {
                        visible[(position + 1).min(visible.len() - 1)]
                    }
                }
                // The highlighted block scrolled off screen; restart at the tail.
                None => *visible.last().expect("visible is not empty"),
            }
        }
    };
    if let Some(current) = app.highlighted
        && current != next
    {
        repaint_block(transcript, current, false)?;
    }
    app.highlighted = Some(next);
    let _ = terminal;
    repaint_block(transcript, next, true)
}

/// Clears the block highlight, restoring the block's original colors.
fn clear_highlight(transcript: &Transcript, app: &mut App) -> io::Result<()> {
    if let Some(current) = app.highlighted.take() {
        repaint_block(transcript, current, false)?;
    }
    Ok(())
}

/// Redraws one block in place on the current screen, with or without the
/// navigation highlight (reverse video). Rows that have scrolled off are
/// skipped; rows of a block partially off-screen still repaint their
/// visible tail. Assumes the viewport is at the bottom (the user has not
/// scrolled the pane with terminal-native scrolling) -- that assumption is
/// inherent to any scrollback repainting, including Claude Code's.
fn repaint_block(transcript: &Transcript, block: usize, highlighted: bool) -> io::Result<()> {
    use crossterm::style::{Attribute, Print, SetAttribute, SetForegroundColor};
    use crossterm::{QueueableCommand, cursor::MoveTo};
    let (width, rows) = crossterm::terminal::size()?;
    let (start, end) = match transcript.blocks.get(block) {
        Some(span) => *span,
        None => return Ok(()),
    };
    let total = transcript.lines.len();
    let mut stdout = io::stdout();
    stdout.queue(MoveTo(0, 0))?;
    let mut pending_rows: Vec<(u16, &Line)> = Vec::new();
    for i in start..end {
        let y = screen_row(i, total, rows);
        if y >= 0 && y < i32::from(rows) {
            pending_rows.push((y as u16, &transcript.lines[i]));
        }
    }
    io::stdout().sync_update(|stdout| {
        for (y, line) in pending_rows {
            let mut column = 0u16;
            stdout.queue(MoveTo(0, y))?;
            if highlighted {
                stdout.queue(SetAttribute(Attribute::Reverse))?;
            }
            for span in &line.spans {
                stdout.queue(SetForegroundColor(ratatui_color(
                    span.style.fg.unwrap_or(ratatui::style::Color::Reset),
                )))?;
                for ch in span.content.chars() {
                    if column >= width {
                        break;
                    }
                    stdout.queue(Print(ch))?;
                    column += char_width(ch) as u16;
                }
            }
            if highlighted {
                stdout.queue(SetAttribute(Attribute::NoReverse))?;
            }
            stdout.queue(SetAttribute(Attribute::Reset))?;
            // Clear any remainder of the row.
            stdout.queue(Print(" ".repeat(width.saturating_sub(column) as usize)))?;
        }
        Ok::<(), io::Error>(())
    })??;
    // Park the cursor back inside the viewport input row.
    let _ = MoveTo(0, 0);
    Ok(())
}

/// Writes styled rows into the scrollback above the viewport. Ratatui's
/// inline `insert_before` takes a buffer-drawing closure, so spans are
/// painted cell by cell (with the same width approximation used for
/// wrapping). The whole insert is fenced in a synchronized-update pair
/// (`CSI ?2026`) so the terminal applies the scroll and the composer
/// repaint atomically -- Codex CLI and Claude Code do the same; terminals
/// without support recover via the spec's timeout.
fn insert_lines(terminal: &mut DefaultTerminal, lines: Vec<Line<'static>>) -> io::Result<()> {
    io::stdout().sync_update(|_stdout| {
        let height = lines.len() as u16;
        terminal.insert_before(height, |buffer: &mut ratatui::prelude::Buffer| {
            let width = buffer.area.width;
            for (row, line) in lines.iter().enumerate() {
                let y = row as u16;
                let mut x = 0u16;
                for span in &line.spans {
                    for ch in span.content.chars() {
                        if x >= width {
                            break;
                        }
                        let cell = &mut buffer[(x, y)];
                        cell.set_char(ch);
                        cell.set_style(span.style);
                        x = x.saturating_add(char_width(ch) as u16).min(width);
                    }
                }
            }
        })
    })?
}
/// Convenience wrapper: plain paragraph rows with one style.
fn print_block(
    terminal: &mut DefaultTerminal,
    transcript: &mut Transcript,
    text: &str,
    style: Style,
) -> io::Result<()> {
    let width = terminal_area_width(terminal);
    let lines = wrap_styled(text, width, style);
    commit_lines(terminal, transcript, lines)
}

/// One assistant text block: no prefix, just the words, terminal width.
fn print_text(
    terminal: &mut DefaultTerminal,
    transcript: &mut Transcript,
    text: &str,
) -> io::Result<()> {
    print_block(terminal, transcript, text, theme::base())
}

fn print_dim(
    terminal: &mut DefaultTerminal,
    transcript: &mut Transcript,
    text: &str,
) -> io::Result<()> {
    print_block(terminal, transcript, text, theme::muted())
}

/// Tool-call lines and permission prompts: the "something is happening"
/// color, distinct from prose and from results.
fn print_tool(
    terminal: &mut DefaultTerminal,
    transcript: &mut Transcript,
    text: &str,
) -> io::Result<()> {
    print_block(terminal, transcript, text, theme::tool())
}

/// Results and products: completions, file summaries, command output.
fn print_artifact(
    terminal: &mut DefaultTerminal,
    transcript: &mut Transcript,
    text: &str,
) -> io::Result<()> {
    print_block(terminal, transcript, text, theme::artifact())
}

fn print_error(
    terminal: &mut DefaultTerminal,
    transcript: &mut Transcript,
    text: &str,
) -> io::Result<()> {
    print_block(terminal, transcript, text, theme::error())
}

fn terminal_area_width(terminal: &DefaultTerminal) -> usize {
    terminal
        .size()
        .map(|area| area.width as usize)
        .unwrap_or(80)
}

/// A user's message, prefixed so it stands out in the scrollback.
fn print_user(
    terminal: &mut DefaultTerminal,
    transcript: &mut Transcript,
    text: &str,
) -> io::Result<()> {
    let width = terminal_area_width(terminal).saturating_sub(2);
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
    commit_lines(terminal, transcript, lines)
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
    /// Set while a permission decision is pending: the tool label shown in
    /// the viewport chooser.
    permission_prompt: Option<String>,
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
            permission_prompt: None,
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
                is_error,
                ..
            } => {
                let _ = self.sender.send(UiEvent::ToolDone {
                    call_id: call_id.clone(),
                    name: name.clone(),
                    is_error: *is_error,
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
        println!();
    }
}

/// Draws the bottom viewport: streaming tail, completion hint, input row,
/// status line.
fn draw(terminal: &mut DefaultTerminal, app: &App) -> io::Result<()> {
    terminal
        .draw(|frame| render_viewport(frame, app))
        .map(|_| ())
}

fn render_viewport(frame: &mut Frame, app: &App) {
    let area = frame.area();
    let sections = Layout::vertical([
        Constraint::Length(1),
        Constraint::Length(1),
        Constraint::Length(1),
        Constraint::Length(1),
    ])
    .split(area);

    // Row 0: streaming tail while busy, otherwise the completion hint.
    // Thinking streams dim; assistant text streams bright.
    let streaming: Option<(&String, Style)> = if !app.live.is_empty() {
        Some((&app.live, Style::default()))
    } else if !app.live_reasoning.is_empty() {
        Some((&app.live_reasoning, theme::muted()))
    } else {
        None
    };
    if let Some((source, style)) = streaming {
        let tail: String = {
            let width = area.width as usize;
            source
                .chars()
                .rev()
                .take(width * 2)
                .collect::<Vec<_>>()
                .into_iter()
                .rev()
                .collect()
        };
        frame.render_widget(Paragraph::new(Line::styled(tail, style)), sections[0]);
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
                    Style::default().fg(Color::Cyan)
                };
                Span::styled(label, style)
            })
            .collect::<Vec<_>>();
        frame.render_widget(Paragraph::new(Line::from(candidates)), sections[0]);
    }

    if let Some(prompt) = &app.permission_prompt {
        // Permission chooser: option row replaces the input; the hint row
        // explains navigation. Up/down + Enter, or the y/a/n/v shortcuts.
        frame.render_widget(
            Paragraph::new(Line::styled(prompt.clone(), theme::tool())),
            sections[0],
        );
        let mut options: Vec<Span> = vec![Span::raw(" ")];
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
        frame.render_widget(Paragraph::new(Line::from(options)), sections[1]);
        frame.render_widget(
            Paragraph::new(Line::styled(
                " ↑/↓ choose · Enter confirm · y/a/n/v shortcut",
                theme::muted(),
            )),
            sections[2],
        );
        frame.render_widget(
            Paragraph::new(Line::styled(
                " Waiting for permission".to_owned(),
                theme::muted(),
            )),
            sections[3],
        );
        return;
    }

    // Row 1: the input line with a blinking-free cursor.
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
            Span::styled("❯ ".to_owned(), theme::user()),
            Span::raw(last_line),
        ])),
        sections[1],
    );
    let cursor_x = 2 + before_width as u16;
    let cursor_x = cursor_x.min(area.width.saturating_sub(1));
    if !app.busy {
        frame.set_cursor_position((cursor_x, sections[1].y));
    }

    // Row 2: the key hints, pinned under the input line so they are
    // always visible exactly where the user is looking.
    frame.render_widget(
        Paragraph::new(Line::styled(
            " Enter send · Shift+Enter newline · Ctrl+O verbose · Ctrl+T thinking · /help",
            theme::muted(),
        )),
        sections[2],
    );

    // Row 3: status.
    let status = format!(" {}", app.status);
    frame.render_widget(
        Paragraph::new(Line::styled(status, theme::muted())),
        sections[3],
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
fn flush_live(
    terminal: &mut DefaultTerminal,
    transcript: &mut Transcript,
    app: &mut App,
) -> io::Result<()> {
    if !app.live.is_empty() {
        let text = std::mem::take(&mut app.live);
        print_text(terminal, transcript, &text)?;
    }
    Ok(())
}

/// Flushes the accumulated thinking block as one dim paragraph, prefixed
/// on its first line. Called when real output, a tool call, or the end of
/// the turn arrives -- thinking stays together instead of one row per
/// delta.
fn flush_reasoning(
    terminal: &mut DefaultTerminal,
    transcript: &mut Transcript,
    app: &mut App,
) -> io::Result<()> {
    if !app.live_reasoning.is_empty() {
        let text = std::mem::take(&mut app.live_reasoning);
        if app.verbose {
            print_dim(terminal, transcript, &format!("· {text}"))?;
        } else {
            let words = text.split_whitespace().count();
            print_dim(terminal, transcript, &format!("· thinking · {words} words"))?;
        }
    }
    Ok(())
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
    print_user(terminal, transcript, &text)?;
    draw(terminal, app)?;

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
                        flush_reasoning(terminal, transcript, app)?;
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
                            print_text(terminal, transcript, line.trim_end_matches('\n'))?;
                        }
                    }
                    UiEvent::Reasoning(text) => {
                        if app.show_thinking {
                            flush_live(terminal, transcript, app)?;
                            app.live_reasoning.push_str(&text);
                        }
                    }
                    UiEvent::ToolStart { name, summary } => {
                        flush_reasoning(terminal, transcript, app)?;
                        flush_live(terminal, transcript, app)?;
                        if summary.is_empty() {
                            print_tool(terminal, transcript, &format!("⏺ {name}"))?;
                        } else {
                            print_tool(terminal, transcript, &format!("⏺ {name} · {summary}"))?;
                        }
                    }
                    UiEvent::ToolDone { call_id, name, is_error } => {
                        flush_live(terminal, transcript, app)?;
                        if is_error {
                            print_error(terminal, transcript, &format!("  ✗ {name} failed"))?;
                        } else if let Some(checkpoint) = checkpoint_for(&write_journal, &call_id) {
                            let max_lines = if app.verbose {
                                usize::MAX
                            } else {
                                DIFF_FOLD_LINES
                            };
                            for movement in &checkpoint.movements {
                                insert_lines(terminal, movement_diff_lines(movement, max_lines))?;
                            }
                        } else {
                            // No file movement to show: a quiet result line
                            // confirms the call landed.
                            print_artifact(terminal, transcript, &format!("  ✓ {name}"))?;
                        }
                    }
                }
                draw(terminal, app)?;
            }
            Some(request) = permission_rx.recv(), if pending.is_none() => {
                flush_reasoning(terminal, transcript, app)?;
                flush_live(terminal, transcript, app)?;
                let details = permission_details(&request.call.name, &request.call.arguments);
                print_tool(terminal, transcript, &format!("⏺ {} · {}", request.call.name, tool_summary(&request.call.name, &request.call.arguments)))?;
                if !details.is_empty() {
                    print_dim(terminal, transcript, &details)?;
                }
                app.status = "Waiting for permission".to_owned();
                app.permission_choice = 0;
                app.permission_prompt = Some(format!("Allow {}?", request.call.name));
                pending = Some(request);
                draw(terminal, app)?;
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
                draw(terminal, app)?;
            }
            _ = tick.tick() => draw(terminal, app)?,
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
    flush_reasoning(terminal, transcript, app)?;
    flush_live(terminal, transcript, app)?;
    app.busy = false;
    app.permission_cleanup();
    match result {
        Ok(events) => {
            app.status = "Ready".to_owned();
            for event in events {
                match event.event {
                    Event::RunFailed { message } => {
                        app.status = "Failed".to_owned();
                        print_error(terminal, transcript, &format!("✗ {message}"))?;
                    }
                    Event::RunCancelled => {
                        app.status = "Cancelled".to_owned();
                        print_dim(terminal, transcript, "run cancelled")?;
                    }
                    _ => {}
                }
            }
        }
        Err(error) => {
            app.status = "Failed".to_owned();
            print_error(terminal, transcript, &error.to_string())?;
        }
    }
    println!(); // blank line after each turn
    draw(terminal, app)?;
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
                code: KeyCode::Up, ..
            } if app.completion.is_some() => {
                if let Some(completion) = app.completion.as_mut() {
                    completion.selected = completion.selected.saturating_sub(1);
                }
                EditorAction::None
            }
            KeyEvent {
                code: KeyCode::Down,
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
    terminal: &mut DefaultTerminal,
    transcript: &mut Transcript,
    text: &str,
) -> bool {
    if matches!(text, "/exit" | "/quit") {
        return true;
    }
    match text {
        "/help" => {
            let _ = print_dim(
                terminal,
                transcript,
                "/help  /exit  /session  /sessions  /resume [id]  /diff  /undo  /model <name>  /thinking <off|on|low|medium|high>\nCtrl+O verbose · Ctrl+T thinking · Esc cancels a run · @path mentions files",
            );
        }
        "/session" => {
            let _ = print_dim(
                terminal,
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
            let _ = print_dim(
                terminal,
                transcript,
                &format!(
                    "current model: {}\nusage: /model <name>",
                    session.config.model
                ),
            );
        }
        "/thinking" => {
            let _ = print_dim(
                terminal,
                transcript,
                "usage: /thinking <off|on|low|medium|high>",
            );
        }
        "/diff" => {
            let _ = print_block(
                terminal,
                transcript,
                &session.write_report(),
                Style::default(),
            );
        }
        "/undo" => {
            let message = session.undo_last_write();
            let _ = print_artifact(terminal, transcript, &message);
        }
        "/context" => match context::report(session) {
            Ok(report) => {
                let _ = print_block(terminal, transcript, &report, theme::muted());
            }
            Err(error) => {
                let _ = print_error(terminal, transcript, &format!("context: {error}"));
            }
        },
        "/sessions" => match session.session_list() {
            Ok(lines) if lines.is_empty() => {
                let _ = print_dim(terminal, transcript, "no sessions found");
            }
            Ok(lines) => {
                let _ = print_dim(terminal, transcript, &lines.join("\n"));
            }
            Err(error) => {
                let _ = print_error(terminal, transcript, &error.to_string());
            }
        },
        "/resume" => {
            // List sessions and let the next input pick one.
            match session.session_list_entries() {
                Ok(entries) if entries.is_empty() => {
                    let _ = print_dim(terminal, transcript, "no sessions found");
                }
                Ok(entries) => {
                    let listing = entries
                        .iter()
                        .enumerate()
                        .map(|(index, entry)| format!("{:>3}. {}", index + 1, entry.line))
                        .collect::<Vec<_>>()
                        .join("\n");
                    let _ = print_dim(
                        terminal,
                        transcript,
                        &format!("{listing}\nresume: type a number or id prefix"),
                    );
                    app.resume_pick = true;
                    app.pick_entries = Some(entries.into_iter().map(|entry| entry.id).collect());
                }
                Err(error) => {
                    let _ = print_error(terminal, transcript, &error.to_string());
                }
            }
        }
        _ if text.starts_with("/model ") => {
            let model = text.trim_start_matches("/model ").trim();
            if model.is_empty() {
                let _ = print_error(terminal, transcript, "usage: /model <name>");
            } else {
                match session.change_model(model) {
                    Ok(()) => {
                        let _ = print_tool(terminal, transcript, &format!("model: {model}"));
                    }
                    Err(error) => {
                        let _ = print_error(terminal, transcript, &error.to_string());
                    }
                }
            }
        }
        _ if text.starts_with("/thinking ") => {
            let level = text.trim_start_matches("/thinking ").trim();
            match session.change_thinking(level) {
                Ok(()) => {
                    let _ = print_tool(terminal, transcript, &format!("thinking: {level}"));
                }
                Err(error) => {
                    let _ = print_error(terminal, transcript, &error.to_string());
                }
            }
        }
        _ => {
            let _ = print_error(terminal, transcript, &format!("unknown command: {text}"));
        }
    }
    false
}

// ---------------------------------------------------------------------------
// Entry points
// ---------------------------------------------------------------------------

fn print_history(
    terminal: &mut DefaultTerminal,
    transcript: &mut Transcript,
    session: &InteractiveSession,
) -> io::Result<()> {
    let workspace = workspace_id_for(&session.runner_root);
    let stored = match FileSessionStore::read_session(
        &session.structure_home,
        &workspace,
        &session.session_id,
    ) {
        Ok(stored) => stored,
        Err(_) => return Ok(()),
    };
    let mut turns = 0usize;
    for envelope in stored.events {
        match envelope.event {
            Event::MessageAccepted { content } => {
                print_user(terminal, transcript, &content)?;
                turns += 1;
            }
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
                    print_text(terminal, transcript, &text)?;
                }
            }
            Event::ToolCallRequested {
                name, arguments, ..
            } => {
                print_tool(
                    terminal,
                    transcript,
                    &format!("⏺ {name} · {}", tool_summary(&name, &arguments)),
                )?;
            }
            Event::ToolCallCompleted { name, is_error, .. } if is_error => {
                print_error(terminal, transcript, &format!("  ✗ {name} failed"))?;
            }
            _ => {}
        }
    }
    if turns > 0 {
        print_dim(
            terminal,
            transcript,
            &format!("— resumed session, {turns} earlier messages —"),
        )?;
    }
    Ok(())
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
    // Pin the input box to the bottom of the screen, the way Claude Code
    // does: clear, then anchor the inline viewport to the bottommost rows
    // by placing the cursor there before the viewport is created (ratatui
    // anchors an inline viewport to the current cursor row). New output
    // scrolls in above it from then on.
    crossterm::execute!(
        io::stdout(),
        crossterm::terminal::Clear(crossterm::terminal::ClearType::All),
        crossterm::cursor::MoveTo(0, 0),
    )?;
    let rows = crossterm::terminal::size()?.1;
    if rows > VIEWPORT_LINES {
        crossterm::execute!(
            io::stdout(),
            crossterm::cursor::MoveTo(0, rows - VIEWPORT_LINES),
        )?;
    }
    crossterm::execute!(io::stdout(), crossterm::event::EnableBracketedPaste)?;
    let backend = ratatui::backend::CrosstermBackend::new(io::stdout());
    let mut terminal = Terminal::with_options(
        backend,
        TerminalOptions {
            viewport: Viewport::Inline(VIEWPORT_LINES),
        },
    )?;

    let mut transcript = Transcript::default();
    print_history(&mut terminal, &mut transcript, &session)?;
    app.file_index = Some(workspace_files(&session.runner_root));
    draw(&mut terminal, &app)?;

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
            if let InputEvent::Key(key) = &key
                && key.kind == KeyEventKind::Press
                && app.input.is_empty()
                && app.completion.is_none()
                && !app.busy
                && matches!(key.code, KeyCode::Up | KeyCode::Down)
            {
                navigate_blocks(
                    &mut terminal,
                    &transcript,
                    &mut app,
                    key.code == KeyCode::Up,
                )?;
                draw(&mut terminal, &app)?;
                continue;
            }
            // Any other key drops the block highlight.
            if app.highlighted.is_some() {
                clear_highlight(&transcript, &mut app)?;
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
                            print_tool(
                                &mut terminal,
                                &mut transcript,
                                &format!("session: {}", session.session_id),
                            )?;
                        }
                        Err(error) => {
                            print_error(&mut terminal, &mut transcript, &error.to_string())?
                        }
                    },
                    None => print_error(
                        &mut terminal,
                        &mut transcript,
                        &format!("no session matches '{trimmed}'"),
                    )?,
                }
                draw(&mut terminal, &app)?;
                continue;
            }
            if text.starts_with('/') {
                if command(
                    &mut session,
                    &mut app,
                    &mut terminal,
                    &mut transcript,
                    &text,
                ) {
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
