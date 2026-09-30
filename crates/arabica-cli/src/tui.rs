//! Fullscreen terminal conversation: a scrolling history pane on top, a
//! Claude-style divider, and a pinned editor block at the bottom. Output
//! buffers into `Transcript` (rows grouped into blocks) and the renderer
//! paints the pane each frame, so block highlight, scrolling, and the
//! divider all stay consistent without scrollback surgery.
//!
//! Middle steps render folded: a tool call is one summary line whose
//! status flips in place, results are status lines, and file edits show a
//! colored diff. Ctrl+O toggles verbose printing for later events.

use std::collections::{BTreeMap, VecDeque};
use std::io;
use std::path::Path;
use std::sync::atomic::AtomicBool;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use crate::host::HostModel;
use arabica_adapters::FileSessionStore;
use arabica_model::{ContentBlock, RuntimeItem, RuntimeRole};
use arabica_protocol::{
    Command, CommandEnvelope, CommandId, Event, EventEnvelope, ToolPermissionOutcome,
    ToolPermissionScope, ToolPermissionSource,
};
use arabica_provider::{ApiProviderConfig, ModelProgress, ModelProgressSink};
use arabica_runtime::BlendRoutingPolicy;
use arabica_runtime::{
    PermissionDecision, PermissionRequest, RunCancellation, RunControl, ToolPermissionGate,
};
use arabica_session::{DispatchControl, EventVisibility, FanOutObserver, SessionEventObserver};
use crossterm::SynchronizedUpdate as _;
use crossterm::event::{self, Event as InputEvent, KeyCode, KeyEvent, KeyEventKind, KeyModifiers};
use crossterm::terminal::{disable_raw_mode, enable_raw_mode};
use ratatui::layout::{Constraint, Layout, Rect};
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::Paragraph;
use ratatui::{DefaultTerminal, Frame, Terminal};
use similar::{ChangeTag, TextDiff};
use unicode_segmentation::UnicodeSegmentation;
use unicode_width::UnicodeWidthStr;

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

/// Copies `line` into an owned `Line`, preserving both span styles and the
/// line style. `Line::from(spans)` would drop `line.style`, which is where
/// `wrap_styled`/`Line::styled` put the theme color of most transcript rows.
fn paint_row(line: &Line) -> Line<'static> {
    let mut rebuilt = Line::from(
        line.spans
            .iter()
            .map(|span| Span::styled(span.content.to_string(), span.style))
            .collect::<Vec<_>>(),
    );
    rebuilt.style = line.style;
    rebuilt
}

struct EditorLayout {
    rows: Vec<String>,
    cursor_row: usize,
    cursor_column: usize,
}

fn layout_editor(input: &str, cursor: usize, width: usize) -> EditorLayout {
    let width = width.max(1);
    let cursor = cursor.min(input.len());
    let prompt = if width >= 2 { "❯ " } else { "❯" };
    let indent = " ".repeat(2.min(width));
    let mut rows = vec![prompt.to_owned()];
    let mut row = 0;
    let mut column = UnicodeWidthStr::width(prompt).min(width);
    let mut byte_offset = 0;
    let mut cursor_position = (0, column);
    for grapheme in input.graphemes(true) {
        if grapheme == "\n" {
            if cursor == byte_offset {
                cursor_position = (row, column);
            }
            rows.push(indent.clone());
            row += 1;
            column = UnicodeWidthStr::width(indent.as_str());
            byte_offset += grapheme.len();
            if cursor == byte_offset {
                cursor_position = (row, column);
            }
            continue;
        }
        let grapheme_width = UnicodeWidthStr::width(grapheme);
        let row_indent = if row == 0 {
            UnicodeWidthStr::width(prompt)
        } else {
            UnicodeWidthStr::width(indent.as_str())
        };
        if column + grapheme_width > width && column > row_indent {
            rows.push(indent.clone());
            row += 1;
            column = UnicodeWidthStr::width(indent.as_str());
        }
        if cursor == byte_offset {
            cursor_position = (row, column);
        }
        rows[row].push_str(grapheme);
        column += grapheme_width;
        byte_offset += grapheme.len();
        if cursor == byte_offset {
            cursor_position = (row, column);
        }
    }
    EditorLayout {
        rows,
        cursor_row: cursor_position.0,
        cursor_column: cursor_position.1,
    }
}

fn editor_has_multiple_visual_rows(input: &str, cursor: usize, width: usize) -> bool {
    layout_editor(input, cursor, width).rows.len() > 1
}

fn composer_height(viewport_height: usize, editor_rows: usize) -> usize {
    editor_rows.min((viewport_height / 3).clamp(1, 8)).max(1)
}

fn visual_row_positions(input: &str, width: usize) -> Vec<(usize, usize, usize)> {
    let width = width.max(1);
    let prompt = if width >= 2 { "❯ " } else { "❯" };
    let indent = UnicodeWidthStr::width("  ").min(width);
    let mut positions = vec![(0, 0, UnicodeWidthStr::width(prompt).min(width))];
    let mut row = 0;
    let mut column = UnicodeWidthStr::width(prompt).min(width);
    let mut byte_offset = 0;
    for grapheme in input.graphemes(true) {
        if grapheme == "\n" {
            byte_offset += 1;
            row += 1;
            column = indent;
            positions.push((byte_offset, row, column));
            continue;
        }
        let grapheme_width = UnicodeWidthStr::width(grapheme);
        let row_indent = if row == 0 {
            UnicodeWidthStr::width(prompt).min(width)
        } else {
            indent
        };
        if column + grapheme_width > width && column > row_indent {
            row += 1;
            column = indent;
            if positions
                .last()
                .is_some_and(|position| position.0 == byte_offset)
            {
                *positions.last_mut().expect("checked above") = (byte_offset, row, column);
            } else {
                positions.push((byte_offset, row, column));
            }
        }
        byte_offset += grapheme.len();
        column += grapheme_width;
        positions.push((byte_offset, row, column));
    }
    positions
}

fn vertical_cursor_move(
    input: &str,
    cursor: usize,
    up: bool,
    goal_column: Option<usize>,
    width: usize,
) -> Option<(usize, usize)> {
    let positions = visual_row_positions(input, width);
    let current = positions
        .iter()
        .find(|position| position.0 == cursor.min(input.len()))
        .copied()?;
    let column = goal_column.unwrap_or(current.2);
    let target_row = if up {
        current.1.checked_sub(1)?
    } else {
        current.1 + 1
    };
    let target = positions
        .iter()
        .filter(|position| position.1 == target_row)
        .min_by_key(|position| (position.2.abs_diff(column), position.2 > column))?;
    Some((target.0, column))
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
        for grapheme in raw.graphemes(true) {
            let w = UnicodeWidthStr::width(grapheme);
            if current_width + w > width && !current.is_empty() {
                lines.push(Line::styled(std::mem::take(&mut current), style));
                current_width = 0;
            }
            current.push_str(grapheme);
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
    /// Original, unwrapped rows. Layout-dependent wrapping belongs to the
    /// renderer so resizing never changes copied or searched source text.
    lines: Vec<Line<'static>>,
    blocks: Vec<(usize, usize)>,
    copy_texts: Vec<Option<String>>,
}

impl Transcript {
    fn push_block(&mut self, lines: Vec<Line<'static>>) {
        let start = self.lines.len();
        self.lines.extend(lines);
        self.blocks.push((start, self.lines.len()));
        self.copy_texts.push(None);
    }

    fn push_block_with_copy(&mut self, lines: Vec<Line<'static>>, copy_text: String) {
        self.push_block(lines);
        *self.copy_texts.last_mut().expect("block just added") = Some(copy_text);
    }

    /// Drops every recorded row and block, for `/resume` switching to a
    /// different session whose history is replayed from its own store.
    fn clear(&mut self) {
        self.lines.clear();
        self.blocks.clear();
        self.copy_texts.clear();
    }
}

#[derive(Clone)]
struct DisplayRow {
    line: Line<'static>,
    block_index: usize,
    source_line: usize,
    byte_offset: usize,
}

fn wrap_line_for_display(
    line: &Line<'_>,
    width: usize,
    block_index: usize,
    source_line: usize,
) -> Vec<DisplayRow> {
    let width = width.max(1);
    let plain: String = line
        .spans
        .iter()
        .map(|span| span.content.as_ref())
        .collect();
    if plain == "─" {
        return vec![DisplayRow {
            line: Line::styled("─".repeat(width), line.style),
            block_index,
            source_line,
            byte_offset: 0,
        }];
    }
    if plain.is_empty() {
        return vec![DisplayRow {
            line: paint_row(line),
            block_index,
            source_line,
            byte_offset: 0,
        }];
    }

    let mut rows = Vec::new();
    let mut spans: Vec<Span<'static>> = Vec::new();
    let mut row_width = 0;
    let mut byte_offset = 0;
    let mut row_start = 0;
    for span in &line.spans {
        for grapheme in span.content.graphemes(true) {
            let grapheme_width = UnicodeWidthStr::width(grapheme);
            if row_width > 0 && row_width + grapheme_width > width {
                let mut rendered = Line::from(std::mem::take(&mut spans));
                rendered.style = line.style;
                rows.push(DisplayRow {
                    line: rendered,
                    block_index,
                    source_line,
                    byte_offset: row_start,
                });
                row_width = 0;
                row_start = byte_offset;
            }
            match spans.last_mut() {
                Some(last) if last.style == span.style => last.content.to_mut().push_str(grapheme),
                _ => spans.push(Span::styled(grapheme.to_owned(), span.style)),
            }
            row_width += grapheme_width;
            byte_offset += grapheme.len();
        }
    }
    let mut rendered = Line::from(spans);
    rendered.style = line.style;
    rows.push(DisplayRow {
        line: rendered,
        block_index,
        source_line,
        byte_offset: row_start,
    });
    rows
}

fn display_rows(transcript: &Transcript, width: usize) -> Vec<DisplayRow> {
    let mut rows = Vec::new();
    for (block_index, (start, end)) in transcript.blocks.iter().enumerate() {
        for source_line in *start..*end {
            rows.extend(wrap_line_for_display(
                &transcript.lines[source_line],
                width,
                block_index,
                source_line,
            ));
        }
    }
    rows
}

/// Splits `text` into styled spans following the source line's span
/// layout: styles are copied from the original spans proportionally by
/// walking both texts in step. Used by search-hit rendering, which
/// rebuilds a row with highlight segments.
fn push_plain_spans(spans: &mut Vec<Span>, text: &str, source: &Line) {
    let source_text: Vec<char> = source
        .spans
        .iter()
        .flat_map(|span| span.content.chars().collect::<Vec<_>>())
        .collect();
    let mut styles = Vec::new();
    for span in &source.spans {
        for _ in 0..span.content.chars().count() {
            styles.push(span.style);
        }
    }
    for (offset, ch) in text.chars().enumerate() {
        let style = styles
            .get(source_text.len().saturating_sub(1).min(offset))
            .copied()
            .unwrap_or_default();
        match spans.last_mut() {
            Some(last) if last.style == style => {
                last.content.to_mut().push(ch);
            }
            _ => spans.push(Span::styled(ch.to_string(), style)),
        }
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
    transcript.copy_texts[block] = None;
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
    let text = transcript
        .copy_texts
        .get(block)
        .and_then(Option::as_ref)
        .cloned()
        .unwrap_or_else(|| {
            transcript.lines[start..end]
                .iter()
                .map(|line| {
                    line.spans
                        .iter()
                        .map(|span| span.content.to_string())
                        .collect::<String>()
                })
                .collect::<Vec<_>>()
                .join("\n")
        });
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
    Ok(text.lines().count().max(1))
}

/// Moves the scroll anchor one block up or down. The highlight itself is
/// applied by the renderer (reverse video on the selected block), so no
/// in-place terminal surgery is needed in fullscreen mode.
fn navigate_blocks(transcript: &Transcript, app: &mut App, up: bool) {
    let count = transcript.blocks.len();
    if count == 0 {
        return;
    }
    let visible_rows = LAST_PANE_ROWS
        .load(std::sync::atomic::Ordering::Relaxed)
        .max(1);
    let was_pinned = app.scroll_pinned;
    let current = app.highlighted.unwrap_or(count - 1);
    let target = if up {
        current.saturating_sub(1)
    } else {
        (current + 1).min(count - 1)
    };
    app.highlighted = Some(target);
    // Scroll so the target block's first row sits one quarter down the
    // pane -- the same rule search jumps use, expressed in one place.
    let rows = display_rows(
        transcript,
        LAST_WIDTH.load(std::sync::atomic::Ordering::Relaxed).max(1),
    );
    let target_row = rows
        .iter()
        .position(|row| row.block_index == target)
        .unwrap_or(0);
    app.set_scroll_anchor(&rows, target_row.saturating_sub(visible_rows / 4));
    // Navigating to the last block when it already fills the pane means
    // "follow the bottom" again, the way every chat UI behaves.
    app.scroll_pinned = target == count - 1 && (target_row + visible_rows) >= rows.len();
    if was_pinned && !app.scroll_pinned {
        app.new_output_baseline = Some(transcript.blocks.len());
    } else if app.scroll_pinned {
        app.new_output_baseline = None;
    }
}

/// The index of the transcript row shown at the top of the history pane.
/// `scroll_pinned` follows the bottom; manual scroll, search jumps, and
/// block navigation all set `scroll_offset_rows`, which only ever needs
/// clamping here. This is the single source of truth for what "scrolled
/// to X" means -- the click-to-block mapper uses it too.
fn first_visible_row(app: &App, rows: &[DisplayRow], visible_rows: usize) -> usize {
    let max_scroll = rows.len().saturating_sub(visible_rows);
    if app.scroll_pinned {
        max_scroll
    } else {
        let anchored = app.scroll_anchor.and_then(|anchor| {
            rows.iter().position(|row| {
                row.block_index == anchor.block_index
                    && row.source_line == anchor.source_line
                    && row.byte_offset <= anchor.byte_offset
                    && anchor.byte_offset
                        < row.byte_offset
                            + row
                                .line
                                .spans
                                .iter()
                                .map(|span| span.content.len())
                                .sum::<usize>()
                                .max(1)
            })
        });
        anchored.unwrap_or(app.scroll_offset_rows).min(max_scroll)
    }
}

/// Scrolls the history pane by `delta` rows (negative scrolls up). Manual
/// scrolling releases any search anchor and stops bottom-following; reaching
/// the bottom re-engages follow so new output resumes auto-scrolling.
fn scroll_by(app: &mut App, transcript: &Transcript, visible_rows: usize, delta: isize) {
    let rows = display_rows(
        transcript,
        LAST_WIDTH.load(std::sync::atomic::Ordering::Relaxed).max(1),
    );
    let max_scroll = rows.len().saturating_sub(visible_rows) as isize;
    let current = first_visible_row(app, &rows, visible_rows) as isize;
    let next = (current + delta).clamp(0, max_scroll);
    if app.scroll_pinned && next < max_scroll {
        app.new_output_baseline = Some(transcript.blocks.len());
    }
    app.set_scroll_anchor(&rows, next as usize);
    app.scroll_pinned = next >= max_scroll;
    if app.scroll_pinned {
        app.new_output_baseline = None;
    }
    app.release_search_anchor();
}

fn print_block(transcript: &mut Transcript, text: &str, style: Style) {
    let lines = text
        .split('\n')
        .map(|line| Line::styled(line.to_owned(), style))
        .collect();
    transcript.push_block_with_copy(lines, text.to_owned());
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

/// Terminal width as of the last draw; input movement and history anchors use
/// it between frames. Defaults to 80 before the first frame.
static LAST_WIDTH: std::sync::atomic::AtomicUsize = std::sync::atomic::AtomicUsize::new(80);

/// History-pane height as of the last draw, for scrolling between frames.
static LAST_PANE_ROWS: std::sync::atomic::AtomicUsize = std::sync::atomic::AtomicUsize::new(18);

/// A user's message, prefixed so it stands out in the scrollback.
fn print_user(transcript: &mut Transcript, text: &str) {
    // Claude-style divider above each user message.
    transcript.push_block(vec![Line::styled("\u{2500}".to_owned(), theme::muted())]);
    let mut lines = Vec::new();
    for (index, raw) in text.split('\n').enumerate() {
        let prefix = if index == 0 { "> " } else { "  " };
        let spans = vec![
            Span::styled(prefix.to_owned(), theme::user()),
            Span::raw(raw.to_owned()),
        ];
        lines.push(Line::from(spans));
    }
    lines.push(Line::raw(String::new()));
    transcript.push_block_with_copy(lines, text.to_owned());
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
    vec![current.to_owned()]
}

struct CompletionView {
    replace_start: usize,
    replace_end: usize,
    candidates: Vec<String>,
    selected: usize,
}

/// An active case-insensitive substring search over the transcript.
#[derive(Clone, Debug)]
struct Search {
    query: String,
    /// Index into the flattened match list the view is anchored to.
    current: usize,
    /// While set, the view stays locked to `current`'s match; scrolling or
    /// typing releases the lock (F3/Shift+F3 re-engages it).
    pinned: bool,
}

/// One match: (line index, byte range within the line's plain text).
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct SearchHit {
    line: usize,
    start_byte: usize,
    end_byte: usize,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct ScrollAnchor {
    block_index: usize,
    source_line: usize,
    byte_offset: usize,
}

/// Case-insensitive substring matches of `query` within a single line's
/// plain text (byte offsets, safe on char boundaries for ASCII queries and
/// computed on char boundaries for any query).
fn line_matches(line_text: &str, query: &str) -> Vec<(usize, usize)> {
    let lower_line = line_text.to_lowercase();
    let lower_query = query.to_lowercase();
    let mut hits = Vec::new();
    if lower_query.is_empty() {
        return hits;
    }
    let mut from = 0;
    while let Some(relative) = lower_line[from..].find(&lower_query) {
        let start = from + relative;
        let end = start + lower_query.len();
        // Byte offsets may split multi-byte chars after lowercasing; snap
        // both ends to char boundaries of the original text.
        let is_boundary = |b: usize| line_text.is_char_boundary(b);
        if is_boundary(start) && is_boundary(end) {
            hits.push((start, end));
        }
        from = end.max(start + 1);
    }
    hits
}

/// All hits of `query` across the transcript, in order.
fn search_hits(transcript: &Transcript, query: &str) -> Vec<SearchHit> {
    let mut hits = Vec::new();
    for (line_index, line) in transcript.lines.iter().enumerate() {
        let text: String = line
            .spans
            .iter()
            .map(|span| span.content.to_string())
            .collect();
        for (start, end) in line_matches(&text, query) {
            hits.push(SearchHit {
                line: line_index,
                start_byte: start,
                end_byte: end,
            });
        }
    }
    hits
}

fn display_hit_ranges(
    transcript: &Transcript,
    row: &DisplayRow,
    query: &str,
) -> Vec<(usize, usize)> {
    let source = &transcript.lines[row.source_line];
    let source_text: String = source
        .spans
        .iter()
        .map(|span| span.content.as_ref())
        .collect();
    let display_text: String = row
        .line
        .spans
        .iter()
        .map(|span| span.content.as_ref())
        .collect();
    let row_end = row.byte_offset + display_text.len();
    line_matches(&source_text, query)
        .into_iter()
        .filter_map(|(start, end)| {
            let visible_start = start.max(row.byte_offset);
            let visible_end = end.min(row_end);
            (visible_start < visible_end).then_some((
                visible_start - row.byte_offset,
                visible_end - row.byte_offset,
            ))
        })
        .collect()
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
    let found: Option<(usize, Vec<String>)> =
        if word_start == 0 && token.starts_with('/') && !token.contains(' ') {
            Some((
                word_start,
                COMMANDS
                    .iter()
                    .filter(|command| command.starts_with(token))
                    .map(|command| (*command).to_owned())
                    .collect(),
            ))
        } else if before.starts_with("/thinking ") && word_start == "/thinking ".len() {
            Some((
                word_start,
                THINKING_LEVELS
                    .iter()
                    .filter(|level| level.starts_with(token))
                    .map(|level| (*level).to_owned())
                    .collect(),
            ))
        } else if before.starts_with("/model ") && word_start == "/model ".len() {
            Some((
                word_start,
                models
                    .iter()
                    .filter(|model| model.to_lowercase().contains(&token.to_lowercase()))
                    .cloned()
                    .collect(),
            ))
        } else if let Some(query) = token.strip_prefix('@').map(str::to_lowercase) {
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
            Some((word_start, matches))
        } else {
            None
        };
    // No match anywhere, or a single candidate identical to the token,
    // offers nothing to choose.
    let (replace_start, candidates) = found?;
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
    vertical_column: Option<usize>,
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
    /// Active history-pane search: the query and the match to keep in view.
    search: Option<Search>,
    /// Index of the first visible transcript row (0 = top of history).
    /// Manual scrolling always moves this; `scroll_pinned` short-circuits
    /// it back to the bottom when new output follows.
    scroll_offset_rows: usize,
    scroll_anchor: Option<ScrollAnchor>,
    /// Transcript block count when the user began reviewing history.
    new_output_baseline: Option<usize>,
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
    "Allow this session",
    "Deny once",
    "Deny this session",
];

impl Default for App {
    fn default() -> Self {
        Self {
            input: String::new(),
            cursor: 0,
            vertical_column: None,
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
            search: None,
            scroll_offset_rows: 0,
            scroll_anchor: None,
            new_output_baseline: None,
            permission_prompt: None,
            running_tool: None,
        }
    }
}

impl App {
    /// Starts (or restarts) a search with `query`, anchored at the first
    /// match.
    fn start_search(&mut self, transcript: &Transcript, query: String) {
        if query.is_empty() {
            self.search = None;
            return;
        }
        self.search = Some(Search {
            query,
            current: 0,
            pinned: true,
        });
        if self.scroll_pinned {
            self.new_output_baseline = Some(transcript.blocks.len());
        }
        self.jump_search_to_visible(transcript);
    }

    /// Moves to the next (down=false→previous) match.
    fn step_search(&mut self, transcript: &Transcript, forward: bool) {
        let Some(search) = self.search.as_mut() else {
            return;
        };
        let hits = search_hits(transcript, &search.query);
        if hits.is_empty() {
            return;
        }
        search.current = if forward {
            (search.current + 1) % hits.len()
        } else {
            search.current.checked_sub(1).unwrap_or(hits.len() - 1)
        };
        search.pinned = true;
        self.jump_search_to_visible(transcript);
    }

    /// Drops the search's hold on the scroll position without forgetting
    /// the query, so F3/Shift+F3 can re-anchor to another match later.
    fn release_search_anchor(&mut self) {
        if let Some(search) = self.search.as_mut() {
            search.pinned = false;
        }
    }

    /// Pins the scroll so the current match's row is on screen.
    fn jump_search_to_visible(&mut self, transcript: &Transcript) {
        let Some(search) = self.search.as_ref() else {
            return;
        };
        let hits = search_hits(transcript, &search.query);
        let Some(hit) = hits.get(search.current) else {
            return;
        };
        // Anchor the hit near the top of the pane (one row of context
        // above it), retaining the source text location across reflow.
        let width = LAST_WIDTH.load(std::sync::atomic::Ordering::Relaxed).max(1);
        let rows = display_rows(transcript, width);
        let hit_row = rows
            .iter()
            .position(|row| {
                if row.source_line != hit.line {
                    return false;
                }
                let row_text: String = row
                    .line
                    .spans
                    .iter()
                    .map(|span| span.content.as_ref())
                    .collect();
                hit.start_byte >= row.byte_offset
                    && hit.start_byte < row.byte_offset + row_text.len().max(1)
            })
            .unwrap_or(0);
        self.set_scroll_anchor(&rows, hit_row.saturating_sub(1));
        self.scroll_pinned = false;
    }

    fn set_scroll_anchor(&mut self, rows: &[DisplayRow], row_index: usize) {
        self.scroll_offset_rows = row_index;
        self.scroll_anchor = rows.get(row_index).map(|row| ScrollAnchor {
            block_index: row.block_index,
            source_line: row.source_line,
            byte_offset: row.byte_offset,
        });
    }

    fn new(session: &InteractiveSession) -> Self {
        let models = if session.model_configs.is_empty() {
            configured_models(&session.config.model)
        } else {
            session.model_configs.keys().cloned().collect()
        };
        Self {
            models,
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
                .grapheme_indices(true)
                .next_back()
                .map_or(0, |(start, _)| cursor - start);
            self.input.replace_range(cursor - remove..cursor, "");
            self.cursor -= remove;
            self.refresh_completion();
        }
    }

    fn delete(&mut self) {
        let cursor = self.cursor.min(self.input.len());
        if cursor < self.input.len() {
            let remove = self.input[cursor..]
                .graphemes(true)
                .next()
                .map_or(0, str::len);
            self.input.replace_range(cursor..cursor + remove, "");
            self.refresh_completion();
        }
    }

    fn move_vertical(&mut self, up: bool) {
        let cursor = self.cursor.min(self.input.len());
        let Some((target_cursor, column)) = vertical_cursor_move(
            &self.input,
            cursor,
            up,
            self.vertical_column,
            LAST_WIDTH.load(std::sync::atomic::Ordering::Relaxed).max(4),
        ) else {
            return;
        };
        self.cursor = target_cursor;
        self.vertical_column = Some(column);
        self.refresh_completion();
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
    Exit,
}

/// Restores the terminal even on early returns and errors.
struct InlineGuard;

impl Drop for InlineGuard {
    fn drop(&mut self) {
        let _ = disable_raw_mode();
        let _ = crossterm::execute!(
            io::stdout(),
            crossterm::event::DisableMouseCapture,
            crossterm::terminal::LeaveAlternateScreen,
            crossterm::cursor::Show
        );
        // The transcript lived in the alternate buffer; print the last
        // status so exiting does not feel like losing the session.
        println!();
    }
}

struct FullscreenLayout {
    sections: Vec<Rect>,
    editor: EditorLayout,
    divider_index: Option<usize>,
    tail_index: Option<usize>,
    input_index: usize,
    hints_index: Option<usize>,
    status_index: Option<usize>,
}

fn permission_lines(prompt: &str, selected: usize, width: usize) -> (bool, Vec<Line<'static>>) {
    let width = width.max(1);
    let option_width = PERMISSION_OPTIONS
        .iter()
        .enumerate()
        .map(|(index, label)| {
            let label = if index == selected {
                format!(" [{label}] ")
            } else {
                format!("  {label}  ")
            };
            UnicodeWidthStr::width(label.as_str())
        })
        .sum::<usize>();
    let horizontal_width = UnicodeWidthStr::width(prompt) + 2 + option_width;
    if horizontal_width <= width {
        let mut spans = vec![
            Span::styled(prompt.to_owned(), theme::tool()),
            Span::raw("  "),
        ];
        spans.extend(PERMISSION_OPTIONS.iter().enumerate().map(|(index, label)| {
            let label = if index == selected {
                format!(" [{label}] ")
            } else {
                format!("  {label}  ")
            };
            let style = if index == selected {
                Style::default().fg(Color::Black).bg(Color::Cyan)
            } else {
                theme::muted()
            };
            Span::styled(label, style)
        }));
        return (false, vec![Line::from(spans)]);
    }

    let mut lines = wrap_styled(prompt, width, theme::tool());
    for (index, label) in PERMISSION_OPTIONS.iter().enumerate() {
        let selected = index == selected;
        let marker = if selected { "> " } else { "  " };
        let style = if selected {
            Style::default().fg(Color::Black).bg(Color::Cyan)
        } else {
            theme::muted()
        };
        let option_lines = wrap_styled(label, width.saturating_sub(2).max(1), style);
        for (row, line) in option_lines.into_iter().enumerate() {
            let prefix = if row == 0 { marker } else { "  " };
            let mut spans = vec![Span::styled(prefix.to_owned(), style)];
            spans.extend(line.spans);
            lines.push(Line::from(spans));
        }
    }
    lines.extend(wrap_styled(
        "↑↓ choose · Enter · y/a/n/v · Esc · Ctrl+C",
        width,
        theme::muted(),
    ));
    (true, lines)
}

fn fullscreen_layout(area: Rect, app: &App) -> FullscreenLayout {
    let editor = layout_editor(&app.input, app.cursor, area.width as usize);
    let permission = app
        .permission_prompt
        .as_ref()
        .map(|prompt| permission_lines(prompt, app.permission_choice, area.width as usize));
    let permission_vertical = permission.as_ref().is_some_and(|(vertical, _)| *vertical);
    let permission_rows = permission
        .as_ref()
        .map(|(_, lines)| lines.len())
        .unwrap_or(1);
    let mut input_height = composer_height(area.height as usize, editor.rows.len());
    if permission_vertical {
        input_height = input_height.min(
            (area.height as usize)
                .saturating_sub(permission_rows)
                .max(1),
        );
    }
    let mut constraints = vec![Constraint::Min(1)];
    let mut next = 1;
    let has_permission = permission.is_some();
    let divider_index = if !permission_vertical && area.height >= 3 {
        let index = next;
        constraints.push(Constraint::Length(1));
        next += 1;
        Some(index)
    } else {
        None
    };
    let tail_index = if has_permission || area.height >= 4 {
        let index = next;
        constraints.push(Constraint::Length(if permission_vertical {
            permission_rows.min(area.height.saturating_sub(1) as usize) as u16
        } else {
            1
        }));
        next += 1;
        Some(index)
    } else {
        None
    };
    let input_index = next;
    constraints.push(Constraint::Length(input_height as u16));
    next += 1;
    let hints_index = if !permission_vertical && area.height as usize > next {
        let index = next;
        constraints.push(Constraint::Length(1));
        next += 1;
        Some(index)
    } else {
        None
    };
    let status_index = if !permission_vertical && area.height as usize > next {
        let index = next;
        constraints.push(Constraint::Length(1));
        Some(index)
    } else {
        None
    };
    FullscreenLayout {
        sections: Layout::vertical(constraints).split(area).to_vec(),
        editor,
        divider_index,
        tail_index,
        input_index,
        hints_index,
        status_index,
    }
}

/// Draws the bottom viewport: streaming tail, completion hint, input row,
/// status line.
fn draw(terminal: &mut DefaultTerminal, app: &App, transcript: &Transcript) -> io::Result<()> {
    let size = terminal.size().ok();
    let area = size
        .map(|size| Rect::new(0, 0, size.width, size.height))
        .unwrap_or(Rect::new(0, 0, 80, 24));
    LAST_WIDTH.store(area.width as usize, std::sync::atomic::Ordering::Relaxed);
    LAST_PANE_ROWS.store(
        fullscreen_layout(area, app).sections[0].height as usize,
        std::sync::atomic::Ordering::Relaxed,
    );
    // Fence the frame in a synchronized-update pair (CSI ?2026): the
    // terminal applies the whole repaint atomically, so fast frames never
    // tear mid-draw. Terminals without support recover via the spec's
    // timeout. Codex CLI and Claude Code fence the same way.
    io::stdout().sync_update(|_| {
        terminal
            .draw(|frame| render_fullscreen(frame, app, transcript))
            .map(|_| ())
    })?
}

/// Fullscreen layout: scrolling history pane on top, Claude-style divider
/// pinned above the bottom editor block, editor + hints + status at the
/// bottom. The highlight (reverse video) is applied here, at render time.
fn render_fullscreen(frame: &mut Frame, app: &App, transcript: &Transcript) {
    let area = frame.area();
    let layout = fullscreen_layout(area, app);
    let sections = &layout.sections;
    let editor = &layout.editor;
    let divider_index = layout.divider_index;
    let tail_index = layout.tail_index;
    let input_index = layout.input_index;
    let hints_index = layout.hints_index;
    let status_index = layout.status_index;

    // ---- history pane ----
    let visible_rows = sections[0].height as usize;
    // Reflow source rows for the current width. Transcript storage remains
    // independent of terminal size.
    let highlight = app.highlighted;
    let rows = display_rows(transcript, sections[0].width as usize);
    // Auto-follow the bottom unless the user scrolled away; every anchor
    // (search jump, block navigation, click) resolves through the same
    // first_visible_row so one coordinate system governs the pane.
    let total = rows.len();
    let offset = first_visible_row(app, &rows, visible_rows);
    let shown = &rows[offset.min(total)..(offset + visible_rows).min(total)];
    let search = app.search.as_ref();
    let lines: Vec<Line> = shown
        .iter()
        .map(|display_row| {
            let line = &display_row.line;
            let highlighted = highlight.is_some_and(|selected| selected == display_row.block_index);
            let hit_ranges: Vec<(usize, usize)> = search
                .map(|search| display_hit_ranges(transcript, display_row, &search.query))
                .unwrap_or_default();
            let hit_style = Style::default().fg(Color::Black).bg(Color::Yellow);
            if !hit_ranges.is_empty() {
                // Rebuild spans, splitting any span text at match boundaries
                // so the hit substring gets the highlight style.
                let plain: String = line
                    .spans
                    .iter()
                    .map(|span| span.content.to_string())
                    .collect();
                let mut spans: Vec<Span> = Vec::new();
                let mut consumed = 0usize;
                for (start, end) in &hit_ranges {
                    if *start > consumed {
                        push_plain_spans(&mut spans, &plain[consumed..*start], line);
                    }
                    push_plain_spans(&mut spans, &plain[*start..*end], line);
                    let last = spans.last_mut().expect("pushed above");
                    last.style = last
                        .style
                        .add_modifier(ratatui::style::Modifier::BOLD)
                        .fg(Color::Black)
                        .bg(Color::Yellow);
                    consumed = *end;
                }
                push_plain_spans(&mut spans, &plain[consumed..], line);
                let mut rebuilt = Line::from(spans);
                rebuilt.style = line.style;
                return rebuilt;
            }
            let _ = hit_style;
            let mut row = paint_row(line);
            if highlighted {
                row.style = row.style.add_modifier(Modifier::REVERSED);
            }
            row
        })
        .collect();
    frame.render_widget(Paragraph::new(lines), sections[0]);

    // ---- divider ----
    if let Some(divider_index) = divider_index {
        let divider = "\u{2500}".repeat(sections[divider_index].width as usize);
        frame.render_widget(
            Paragraph::new(Line::styled(divider, theme::muted())),
            sections[divider_index],
        );
    }

    // ---- row: streaming tail / completion / permission prompt ----
    if let Some(tail_index) = tail_index
        && let Some(prompt) = &app.permission_prompt
    {
        let (_, lines) = permission_lines(
            prompt,
            app.permission_choice,
            sections[tail_index].width as usize,
        );
        frame.render_widget(Paragraph::new(lines), sections[tail_index]);
    } else if let Some(tail_index) = tail_index
        && let Some(completion) = &app.completion
    {
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
        frame.render_widget(Paragraph::new(Line::from(candidates)), sections[tail_index]);
    } else if let Some(tail_index) = tail_index
        && app.busy
        && !app.live.is_empty()
    {
        // Assistant text streams here; thinking streams inside the
        // history pane below.
        let tail: String = {
            let width = sections[tail_index].width as usize;
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
            sections[tail_index],
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
    let input_area = sections[input_index];
    let editor_start = if input_area.height == 0 {
        0
    } else {
        editor
            .cursor_row
            .saturating_add(1)
            .saturating_sub(input_area.height as usize)
    };
    let editor_end = (editor_start + input_area.height as usize).min(editor.rows.len());
    let editor_lines = editor.rows[editor_start..editor_end]
        .iter()
        .map(|row| {
            if let Some(content) = row.strip_prefix("❯ ") {
                Line::from(vec![
                    Span::styled("❯ ".to_owned(), theme::user()),
                    Span::raw(content.to_owned()),
                ])
            } else if let Some(content) = row.strip_prefix('❯') {
                Line::from(vec![
                    Span::styled("❯".to_owned(), theme::user()),
                    Span::raw(content.to_owned()),
                ])
            } else {
                let content = row
                    .strip_prefix("  ")
                    .or_else(|| row.strip_prefix(' '))
                    .unwrap_or(row);
                Line::from(vec![
                    Span::styled(row[..row.len() - content.len()].to_owned(), theme::user()),
                    Span::raw(content.to_owned()),
                ])
            }
        })
        .collect::<Vec<_>>();
    frame.render_widget(Paragraph::new(editor_lines), input_area);
    let cursor_x = (input_area.x as usize + editor.cursor_column)
        .min(area.width.saturating_sub(1) as usize) as u16;
    if input_area.height > 0 && !app.busy && app.permission_prompt.is_none() {
        let cursor_y = input_area.y + (editor.cursor_row - editor_start) as u16;
        frame.set_cursor_position((cursor_x, cursor_y));
    }

    // ---- hints row ----
    if let Some(hints_index) = hints_index {
        let width = sections[hints_index].width;
        let hints = if app.permission_prompt.is_some() {
            if width < 60 {
                " ↑↓ choose · Enter confirm · Esc deny · Ctrl+C cancel"
            } else {
                " ↑↓ choose · Enter confirm · y once · a session · n deny · v deny session · Esc deny · Ctrl+C cancel"
            }
        } else if app.completion.is_some() {
            " ↑↓ choose · Tab/Enter accept · Esc close"
        } else if app.search.is_some() {
            " F3 next · Shift+F3 previous · Esc close"
        } else if app.highlighted.is_some() && !app.busy {
            " ↑↓ block · Enter/c copy · Ctrl+End latest · Esc close"
        } else if app.busy {
            " Esc/Ctrl+C cancel · type to queue · Ctrl+End latest"
        } else if width < 100 {
            " Enter send · Shift+Enter newline · ↑↓ history · Ctrl+C clear/exit · Ctrl+End latest"
        } else {
            " Enter send · Shift+Enter newline · ↑↓ history · Ctrl+C clear/exit · Ctrl+D delete/exit · Ctrl+A/E line · Ctrl+←/→ word · Ctrl+End latest · /help"
        };
        frame.render_widget(
            Paragraph::new(Line::styled(hints, theme::muted())),
            sections[hints_index],
        );
    }

    // ---- status row ----
    if let Some(status_index) = status_index {
        let new_blocks = app
            .new_output_baseline
            .map(|baseline| transcript.blocks.len().saturating_sub(baseline))
            .unwrap_or(0);
        let new_output = if !app.scroll_pinned && new_blocks > 0 {
            format!(" · ↓ {new_blocks} new")
        } else {
            String::new()
        };
        let search_status = app
            .search
            .as_ref()
            .and_then(|search| {
                let hits = search_hits(transcript, &search.query);
                (!hits.is_empty()).then(|| {
                    format!(
                        " · {}/{}",
                        search.current.min(hits.len().saturating_sub(1)) + 1,
                        hits.len()
                    )
                })
            })
            .unwrap_or_default();
        let queued_status = if app.queued.is_empty() {
            String::new()
        } else {
            format!(" · {} queued", app.queued.len())
        };
        let status = format!(" {}{search_status}{queued_status}{new_output}", app.status);
        frame.render_widget(
            Paragraph::new(Line::styled(status, theme::muted())),
            sections[status_index],
        );
    }
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
                app.permission_prompt = Some(format!("Permission for {}", request.call.name));
                pending = Some(request);
                draw(terminal, app, transcript)?;
            }
            Some(key) = keys.recv() => {
                let key = key?;
                if pending.is_some() {
                    if let InputEvent::Key(key) = key && key.kind == KeyEventKind::Press {
                        let decision = match key.code {
                            KeyCode::PageUp | KeyCode::PageDown => {
                                let pane_rows = LAST_PANE_ROWS
                                    .load(std::sync::atomic::Ordering::Relaxed)
                                    .max(1);
                                let page_delta = (pane_rows as isize - 1).max(1);
                                let delta = if key.code == KeyCode::PageUp {
                                    -page_delta
                                } else {
                                    page_delta
                                };
                                scroll_by(app, transcript, pane_rows, delta);
                                None
                            }
                            KeyCode::End if key.modifiers.contains(KeyModifiers::CONTROL) => {
                                app.scroll_pinned = true;
                                app.scroll_anchor = None;
                                app.new_output_baseline = None;
                                None
                            }
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
                    } else if let InputEvent::Mouse(mouse) = key {
                        use crossterm::event::MouseEventKind;
                        if matches!(mouse.kind, MouseEventKind::ScrollUp | MouseEventKind::ScrollDown) {
                            let pane_rows = LAST_PANE_ROWS
                                .load(std::sync::atomic::Ordering::Relaxed)
                                .max(1);
                            let delta = if mouse.kind == MouseEventKind::ScrollUp { -3 } else { 3 };
                            scroll_by(app, transcript, pane_rows, delta);
                        }
                    }
                } else if let InputEvent::Key(key) = key {
                    if key.kind == KeyEventKind::Press
                        && app.search.is_some()
                        && key.code == KeyCode::F(3)
                    {
                        app.step_search(transcript, !key.modifiers.contains(KeyModifiers::SHIFT));
                    } else if key.kind == KeyEventKind::Press
                        && key.code == KeyCode::End
                        && key.modifiers.contains(KeyModifiers::CONTROL)
                    {
                        app.scroll_pinned = true;
                        app.scroll_anchor = None;
                        app.new_output_baseline = None;
                        app.highlighted = None;
                    } else if key.kind == KeyEventKind::Press && key.code == KeyCode::Esc {
                        if !dismiss_temporary_state(app) {
                            cancellation.cancel();
                            app.status = "Cancelling".to_owned();
                        }
                    } else if key.kind == KeyEventKind::Press
                        && key.code == KeyCode::Char('c')
                        && key.modifiers == KeyModifiers::CONTROL
                    {
                        cancellation.cancel();
                        app.status = "Cancelling".to_owned();
                    } else if key.kind == KeyEventKind::Press && key.code == KeyCode::Char('o') && key.modifiers == KeyModifiers::CONTROL {
                        app.verbose = !app.verbose;
                        app.status = if app.verbose { "Verbose".to_owned() } else { "Working".to_owned() };
                    } else if key.kind == KeyEventKind::Press && app.completion.is_none()
                        && (matches!(key.code, KeyCode::PageUp | KeyCode::PageDown)
                            || (matches!(key.code, KeyCode::Up | KeyCode::Down)
                                && !editor_has_multiple_visual_rows(
                                    &app.input,
                                    app.cursor,
                                    LAST_WIDTH
                                        .load(std::sync::atomic::Ordering::Relaxed)
                                        .max(1),
                                )))
                    {
                        // While a run is in flight the editor has nothing to
                        // do with these keys: they scroll the history pane
                        // and navigate blocks, exactly as when idle.
                        match key.code {
                            KeyCode::Up => navigate_blocks(transcript, app, true),
                            KeyCode::Down => navigate_blocks(transcript, app, false),
                            KeyCode::PageUp | KeyCode::PageDown => {
                                let pane_rows = LAST_PANE_ROWS
                                    .load(std::sync::atomic::Ordering::Relaxed)
                                    .max(1);
                                let page_delta = (pane_rows as isize - 1).max(1);
                                let delta = if key.code == KeyCode::PageUp {
                                    -page_delta
                                } else {
                                    page_delta
                                };
                                scroll_by(app, transcript, pane_rows, delta);
                            }
                            _ => {}
                        }
                    } else {
                        match handle_editor(app, InputEvent::Key(key)) {
                            EditorAction::Submit(text) => {
                                app.queued.push_back(text);
                                app.scroll_pinned = true;
                                app.scroll_anchor = None;
                                app.new_output_baseline = None;
                                app.highlighted = None;
                                app.status = "Queued".to_owned();
                            }
                            EditorAction::None => {}
                            EditorAction::Exit => {}
                        }
                    }
                } else if let InputEvent::Mouse(mouse) = key {
                    // The wheel works mid-run too: reading back while the
                    // model streams is the main use of manual scrolling.
                    use crossterm::event::MouseEventKind;
                    if let MouseEventKind::ScrollUp | MouseEventKind::ScrollDown = mouse.kind {
                        let pane_rows = LAST_PANE_ROWS
                            .load(std::sync::atomic::Ordering::Relaxed)
                            .max(1);
                        let delta = if mouse.kind == MouseEventKind::ScrollUp { -3 } else { 3 };
                        scroll_by(app, transcript, pane_rows, delta);
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
    draw(terminal, app, transcript)?;
    Ok(())
}

impl App {
    fn permission_cleanup(&mut self) {
        // Reserved for future pending-permission UI state.
    }
}

fn handle_editor(app: &mut App, event: InputEvent) -> EditorAction {
    if let InputEvent::Key(key) = &event {
        if !matches!(key.code, KeyCode::Up | KeyCode::Down) {
            app.vertical_column = None;
        }
    } else {
        app.vertical_column = None;
    }
    match event {
        InputEvent::Paste(text) => {
            app.insert(&text);
            EditorAction::None
        }
        InputEvent::Key(key) if key.kind == KeyEventKind::Press => match key {
            KeyEvent {
                code: KeyCode::Char('c'),
                modifiers,
                ..
            } if modifiers.contains(KeyModifiers::CONTROL) => {
                if app.input.is_empty() {
                    return EditorAction::Exit;
                }
                app.input.clear();
                app.cursor = 0;
                app.refresh_completion();
                EditorAction::None
            }
            KeyEvent {
                code: KeyCode::Char('d'),
                modifiers,
                ..
            } if modifiers.contains(KeyModifiers::CONTROL) => {
                if app.input.is_empty() {
                    return EditorAction::Exit;
                }
                app.delete();
                EditorAction::None
            }
            KeyEvent {
                code: KeyCode::Char('a'),
                modifiers,
                ..
            } if modifiers.contains(KeyModifiers::CONTROL) => {
                app.cursor = app.input[..app.cursor.min(app.input.len())]
                    .rfind('\n')
                    .map_or(0, |index| index + 1);
                app.refresh_completion();
                EditorAction::None
            }
            KeyEvent {
                code: KeyCode::Char('e'),
                modifiers,
                ..
            } if modifiers.contains(KeyModifiers::CONTROL) => {
                let cursor = app.cursor.min(app.input.len());
                app.cursor = cursor
                    + app.input[cursor..]
                        .find('\n')
                        .unwrap_or(app.input.len() - cursor);
                app.refresh_completion();
                EditorAction::None
            }
            KeyEvent {
                code: KeyCode::Char('u'),
                modifiers,
                ..
            } if modifiers.contains(KeyModifiers::CONTROL) => {
                let cursor = app.cursor.min(app.input.len());
                let start = app.input[..cursor].rfind('\n').map_or(0, |index| index + 1);
                app.input.replace_range(start..cursor, "");
                app.cursor = start;
                app.refresh_completion();
                EditorAction::None
            }
            KeyEvent {
                code: KeyCode::Char('k'),
                modifiers,
                ..
            } if modifiers.contains(KeyModifiers::CONTROL) => {
                let cursor = app.cursor.min(app.input.len());
                let end = cursor
                    + app.input[cursor..]
                        .find('\n')
                        .unwrap_or(app.input.len() - cursor);
                app.input.replace_range(cursor..end, "");
                app.refresh_completion();
                EditorAction::None
            }
            KeyEvent {
                code: KeyCode::Char('w'),
                modifiers,
                ..
            } if modifiers.contains(KeyModifiers::CONTROL) => {
                let cursor = app.cursor.min(app.input.len());
                let before = &app.input[..cursor];
                let trimmed = before.trim_end();
                let start = trimmed
                    .rfind(char::is_whitespace)
                    .map_or(0, |index| index + 1);
                app.input.replace_range(start..cursor, "");
                app.cursor = start;
                app.refresh_completion();
                EditorAction::None
            }
            KeyEvent {
                code: KeyCode::Left,
                modifiers,
                ..
            } if modifiers.contains(KeyModifiers::CONTROL) => {
                let cursor = app.cursor.min(app.input.len());
                let before = app.input[..cursor].trim_end();
                app.cursor = before
                    .rfind(char::is_whitespace)
                    .map_or(0, |index| index + 1);
                app.refresh_completion();
                EditorAction::None
            }
            KeyEvent {
                code: KeyCode::Right,
                modifiers,
                ..
            } if modifiers.contains(KeyModifiers::CONTROL) => {
                let cursor = app.cursor.min(app.input.len());
                let after = &app.input[cursor..];
                let leading = after.len() - after.trim_start().len();
                let rest = &after[leading..];
                let word = rest.find(char::is_whitespace).unwrap_or(rest.len());
                app.cursor = cursor + leading + word;
                app.refresh_completion();
                EditorAction::None
            }
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
                code: KeyCode::Up, ..
            } if editor_has_multiple_visual_rows(
                &app.input,
                app.cursor,
                LAST_WIDTH.load(std::sync::atomic::Ordering::Relaxed).max(1),
            ) =>
            {
                app.move_vertical(true);
                EditorAction::None
            }
            KeyEvent {
                code: KeyCode::Down,
                ..
            } if editor_has_multiple_visual_rows(
                &app.input,
                app.cursor,
                LAST_WIDTH.load(std::sync::atomic::Ordering::Relaxed).max(1),
            ) =>
            {
                app.move_vertical(false);
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
                let cursor = app.cursor.min(app.input.len());
                let before = &app.input[..cursor];
                let trimmed = before.trim_end();
                let start = trimmed
                    .rfind(char::is_whitespace)
                    .map_or(0, |index| index + 1);
                app.input.replace_range(start..cursor, "");
                app.cursor = start;
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
                        .grapheme_indices(true)
                        .next_back()
                        .map_or(0, |(start, _)| cursor - start);
                }
                app.refresh_completion();
                EditorAction::None
            }
            KeyEvent {
                code: KeyCode::Right,
                ..
            } => {
                if app.cursor < app.input.len() {
                    app.cursor += app.input[app.cursor..]
                        .graphemes(true)
                        .next()
                        .unwrap()
                        .len();
                }
                app.refresh_completion();
                EditorAction::None
            }
            KeyEvent {
                code: KeyCode::Home,
                ..
            } => {
                app.cursor = app.input[..app.cursor].rfind('\n').map_or(0, |i| i + 1);
                app.refresh_completion();
                EditorAction::None
            }
            KeyEvent {
                code: KeyCode::End, ..
            } => {
                app.cursor += app.input[app.cursor..]
                    .find('\n')
                    .unwrap_or(app.input.len() - app.cursor);
                app.refresh_completion();
                EditorAction::None
            }
            KeyEvent {
                code: KeyCode::Esc, ..
            } => EditorAction::None,
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

fn dismiss_temporary_state(app: &mut App) -> bool {
    if app.completion.take().is_some() {
        return true;
    }
    if app.search.take().is_some() {
        return true;
    }
    app.highlighted.take().is_some()
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
        _ if is_find_command(text) => {
            let query = text.trim_start_matches("/find").trim().to_owned();
            if query.is_empty() {
                print_dim(
                    transcript,
                    "usage: /find <text>  (F3/Shift+F3 jump, Esc exits search)",
                );
            } else {
                app.start_search(transcript, query);
                let hits = app
                    .search
                    .as_ref()
                    .map(|search| search_hits(transcript, &search.query).len())
                    .unwrap_or(0);
                print_dim(
                    transcript,
                    &format!("search: {hits} matches (F3/Shift+F3 jump, Esc exits)"),
                );
            }
        }
        "/help" => {
            print_dim(
                transcript,
                "/help  /exit  /session  /sessions  /resume [id]  /find <text>  /diff  /undo  /model <name>  /thinking <off|on|low|medium|high>\nF3/Shift+F3 next/previous search result · Esc closes completion, search, then history selection · Ctrl+C clears the draft or exits when empty · Ctrl+D deletes forward or exits when empty · Ctrl+A/E line start/end · Ctrl+U/K delete to line edge · Ctrl+End latest · Ctrl+O verbose · Ctrl+T thinking · @path mentions files\nHistory: PageUp/PageDown or mouse wheel scroll · click a block then Enter/c copies it · sending or queueing returns to latest\nPermission: Enter allows once by default · ↑↓ select · y/a allow once/session · n/v deny once/session · Esc denies once · Ctrl+C cancels the run",
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

fn is_find_command(text: &str) -> bool {
    let Some(suffix) = text.strip_prefix("/find") else {
        return false;
    };
    suffix.is_empty() || suffix.chars().next().is_some_and(char::is_whitespace)
}

// ---------------------------------------------------------------------------
// Entry points
// ---------------------------------------------------------------------------

fn print_history(transcript: &mut Transcript, session: &InteractiveSession) {
    let workspace = workspace_id_for(&session.runner_root);
    let stored = match FileSessionStore::read_session(
        &session.arabica_home,
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
    let model = HostModel::Api(arabica_provider::ApiModelProvider::new(config.clone())?);
    run_inner_with_model(config, model, None, options).await
}

async fn run_inner_with_model(
    config: ApiProviderConfig,
    model: HostModel,
    blend_policy: Option<BlendRoutingPolicy>,
    options: InteractiveOptions,
) -> Result<i32, Box<dyn std::error::Error>> {
    run_inner_with_model_configs(config, model, blend_policy, BTreeMap::new(), options).await
}

async fn run_inner_with_model_configs(
    config: ApiProviderConfig,
    model: HostModel,
    blend_policy: Option<BlendRoutingPolicy>,
    model_configs: BTreeMap<String, ApiProviderConfig>,
    options: InteractiveOptions,
) -> Result<i32, Box<dyn std::error::Error>> {
    let mut session = InteractiveSession::open_with_model_configs(
        config,
        model,
        blend_policy,
        model_configs,
        options,
    )
    .await?;
    let mut app = App::new(&session);
    app.status = format!("session {}", session.session_id);

    enable_raw_mode()?;
    let _guard = InlineGuard;
    crossterm::execute!(
        io::stdout(),
        crossterm::terminal::EnterAlternateScreen,
        crossterm::event::EnableBracketedPaste,
        crossterm::event::EnableMouseCapture
    )?;
    let backend = ratatui::backend::CrosstermBackend::new(io::stdout());
    let mut terminal = Terminal::new(backend)?;

    let mut transcript = Transcript::default();
    // Startup banner: the Arabica mark in block characters, then the
    // brand line. Committed as one block so block navigation treats it
    // like any other content.
    transcript.push_block(vec![
        Line::styled(
            " \u{2591}\u{2592}\u{2593} \u{2591}\u{2592}\u{2593}   \u{2591}\u{2592}\u{2593}\u{2593}",
            theme::tool(),
        ),
        Line::styled(
            " \u{2593}\u{2592}\u{2591} \u{2593}\u{2592}\u{2591}   \u{2592}\u{2591}\u{2591}  arabica",
            theme::tool(),
        ),
        Line::styled(
            "  \u{2591}\u{2592}\u{2593}   \u{2591}\u{2592}    \u{2593}\u{2593}\u{2592}  a coding agent in your terminal",
            theme::muted(),
        ),
        Line::raw(String::new()),
    ]);
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
            if let InputEvent::Mouse(mouse) = &key {
                use crossterm::event::{MouseEvent, MouseEventKind};
                let MouseEvent { kind, .. } = mouse;
                let mut dirty = false;
                let pane_rows = LAST_PANE_ROWS
                    .load(std::sync::atomic::Ordering::Relaxed)
                    .max(1);
                match kind {
                    MouseEventKind::ScrollUp => {
                        scroll_by(&mut app, &transcript, pane_rows, -3);
                        dirty = true;
                    }
                    MouseEventKind::ScrollDown => {
                        scroll_by(&mut app, &transcript, pane_rows, 3);
                        dirty = true;
                    }
                    MouseEventKind::Down(_button) => {
                        // Click a history row: highlight its block. Clicks
                        // elsewhere (e.g. the editor) just restore focus.
                        let rows = display_rows(
                            &transcript,
                            LAST_WIDTH.load(std::sync::atomic::Ordering::Relaxed).max(1),
                        );
                        let total = rows.len();
                        if (mouse.row as usize) < pane_rows && total > 0 {
                            let first_visible =
                                first_visible_row(&app, &rows, pane_rows.min(total));
                            let clicked_line =
                                (first_visible + mouse.row as usize).min(total.saturating_sub(1));
                            app.highlighted = Some(rows[clicked_line].block_index);
                            if app.scroll_pinned {
                                app.new_output_baseline = Some(transcript.blocks.len());
                            }
                            app.scroll_pinned = false;
                            dirty = true;
                        }
                    }
                    _ => {}
                }
                if dirty {
                    draw(&mut terminal, &app, &transcript)?;
                }
                continue;
            }
            if !app.busy
                && app.permission_prompt.is_none()
                && let InputEvent::Key(key) = &key
                && key.kind == KeyEventKind::Press
            {
                if key.code == KeyCode::End && key.modifiers.contains(KeyModifiers::CONTROL) {
                    app.scroll_pinned = true;
                    app.scroll_anchor = None;
                    app.highlighted = None;
                    app.new_output_baseline = None;
                    draw(&mut terminal, &app, &transcript)?;
                    continue;
                }
                // Search navigation has dedicated keys so every ordinary
                // character remains available to the draft editor.
                if app.search.is_some() && key.code == KeyCode::F(3) {
                    app.step_search(&transcript, !key.modifiers.contains(KeyModifiers::SHIFT));
                    draw(&mut terminal, &app, &transcript)?;
                    continue;
                }
                // Escape dismisses one transient state at a time.
                if key.code == KeyCode::Esc && dismiss_temporary_state(&mut app) {
                    draw(&mut terminal, &app, &transcript)?;
                    continue;
                }
                // Wheel-free environments: PageUp/PageDown page the pane.
                if app.completion.is_none()
                    && matches!(key.code, KeyCode::PageUp | KeyCode::PageDown)
                {
                    let pane_rows = LAST_PANE_ROWS
                        .load(std::sync::atomic::Ordering::Relaxed)
                        .max(1);
                    let page_delta = (pane_rows as isize - 1).max(1);
                    let delta = if key.code == KeyCode::PageUp {
                        -page_delta
                    } else {
                        page_delta
                    };
                    scroll_by(&mut app, &transcript, pane_rows, delta);
                    draw(&mut terminal, &app, &transcript)?;
                    continue;
                }
                if matches!(key.code, KeyCode::Up | KeyCode::Down)
                    && app.completion.is_none()
                    && !editor_has_multiple_visual_rows(
                        &app.input,
                        app.cursor,
                        LAST_WIDTH.load(std::sync::atomic::Ordering::Relaxed).max(1),
                    )
                {
                    // A multiline draft owns Up/Down for cursor movement;
                    // otherwise they navigate transcript blocks.
                    navigate_blocks(&transcript, &mut app, key.code == KeyCode::Up);
                    draw(&mut terminal, &app, &transcript)?;
                    continue;
                }
            }
            // With a block highlighted, Enter or c copies it.
            if app.input.is_empty()
                && app.completion.is_none()
                && !app.busy
                && let InputEvent::Key(key) = &key
                && key.kind == KeyEventKind::Press
                && app.highlighted.is_some()
                && (key.code == KeyCode::Enter
                    || (key.code == KeyCode::Char('c') && key.modifiers.is_empty()))
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
                EditorAction::Exit => break,
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
                app.scroll_pinned = true;
                app.scroll_anchor = None;
                app.new_output_baseline = None;
                app.highlighted = None;
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

pub async fn run_with_model(
    config: ApiProviderConfig,
    model: HostModel,
    blend_policy: Option<BlendRoutingPolicy>,
    options: InteractiveOptions,
) -> i32 {
    run_with_model_configs(config, model, blend_policy, BTreeMap::new(), options).await
}

pub async fn run_with_model_configs(
    config: ApiProviderConfig,
    model: HostModel,
    blend_policy: Option<BlendRoutingPolicy>,
    model_configs: BTreeMap<String, ApiProviderConfig>,
    options: InteractiveOptions,
) -> i32 {
    if options.allow_shell && options.read_only {
        eprintln!("error: --allow-shell and --read-only are mutually exclusive");
        return 2;
    }
    match run_inner_with_model_configs(config, model, blend_policy, model_configs, options).await {
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
    fn line_matches_finds_all_case_insensitive_hits() {
        assert_eq!(
            line_matches("Hello hello HELLO", "hello"),
            vec![(0, 5), (6, 11), (12, 17)]
        );
        assert!(line_matches("nothing", "xyz").is_empty());
        assert!(line_matches("anything", "").is_empty());
        // Unicode-safe: byte offsets land on char boundaries.
        // Lowercasing can change byte lengths ("É" 2B -> "é" 2B stays, but
        // offsets still shift); assert boundaries only.
        let hits = line_matches("héllo HÉLLO", "héllo");
        assert_eq!(hits.len(), 2);
    }

    #[test]
    fn search_hits_walks_all_lines_in_order() {
        let mut transcript = Transcript::default();
        transcript.push_block(vec![Line::raw("alpha bravo")]);
        transcript.push_block(vec![Line::raw("bravo alpha"), Line::raw("none here")]);
        let hits = search_hits(&transcript, "alpha");
        assert_eq!(hits.len(), 2);
        assert_eq!(hits[0].line, 0);
        assert_eq!(hits[1].line, 1);
    }

    #[test]
    fn permission_choices_name_their_scope_and_default_to_allow_once() {
        let app = App::default();
        assert_eq!(app.permission_choice, 0);
        assert_eq!(
            PERMISSION_OPTIONS,
            [
                "Allow once",
                "Allow this session",
                "Deny once",
                "Deny this session",
            ]
        );

        let cases = [
            (
                KeyCode::Char('y'),
                ToolPermissionOutcome::Allowed,
                ToolPermissionScope::Once,
            ),
            (
                KeyCode::Char('a'),
                ToolPermissionOutcome::Allowed,
                ToolPermissionScope::Session,
            ),
            (
                KeyCode::Char('n'),
                ToolPermissionOutcome::Denied,
                ToolPermissionScope::Once,
            ),
            (
                KeyCode::Esc,
                ToolPermissionOutcome::Denied,
                ToolPermissionScope::Once,
            ),
            (
                KeyCode::Char('v'),
                ToolPermissionOutcome::Denied,
                ToolPermissionScope::Session,
            ),
        ];
        for (code, outcome, scope) in cases {
            let decision = permission_decision(KeyEvent::new(code, KeyModifiers::NONE)).unwrap();
            assert_eq!(decision.outcome, outcome);
            assert_eq!(decision.scope, scope);
        }
    }

    #[test]
    fn permission_choices_stack_on_narrow_terminals_and_stay_visible() {
        let prompt = "Permission for write_file";
        let (vertical, lines) = permission_lines(prompt, 0, 28);
        assert!(vertical);
        let content = lines
            .iter()
            .map(|line| {
                line.spans
                    .iter()
                    .map(|span| span.content.as_ref())
                    .collect::<String>()
            })
            .collect::<Vec<_>>();
        let joined = content.join("\n");
        for label in [
            prompt,
            "Allow once",
            "Allow this session",
            "Deny once",
            "Deny this session",
            "Enter",
            "Ctrl+C",
        ] {
            assert!(joined.contains(label), "missing {label:?} in {joined:?}");
        }

        let app = App {
            permission_prompt: Some(prompt.to_owned()),
            ..Default::default()
        };
        let layout = fullscreen_layout(Rect::new(0, 0, 28, 18), &app);
        let tail = layout
            .tail_index
            .expect("permission chooser has a fixed area");
        assert_eq!(layout.sections[tail].height as usize, lines.len());
        let backend = ratatui::backend::TestBackend::new(28, 18);
        let mut terminal = Terminal::new(backend).unwrap();
        let transcript = Transcript::default();
        terminal
            .draw(|frame| render_fullscreen(frame, &app, &transcript))
            .unwrap();
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
    fn cursor_column_uses_only_text_before_the_cursor_on_its_line() {
        assert_eq!(layout_editor("hello world", 7, 40).cursor_column, 9);
        assert_eq!(layout_editor("first\nsecond", 9, 40).cursor_column, 5);
    }

    #[test]
    fn editor_supports_terminal_shortcuts() {
        let key = |ch| InputEvent::Key(KeyEvent::new(KeyCode::Char(ch), KeyModifiers::CONTROL));
        let mut app = App::default();
        app.insert("hello world");
        assert!(matches!(
            handle_editor(&mut app, key('a')),
            EditorAction::None
        ));
        assert_eq!(app.cursor, 0);
        assert!(matches!(
            handle_editor(&mut app, key('e')),
            EditorAction::None
        ));
        assert_eq!(app.cursor, app.input.len());
        assert!(matches!(
            handle_editor(&mut app, key('w')),
            EditorAction::None
        ));
        assert_eq!(app.input, "hello ");
        assert!(matches!(
            handle_editor(&mut app, key('k')),
            EditorAction::None
        ));
        assert_eq!(app.input, "hello ");
        app.cursor = 5;
        assert!(matches!(
            handle_editor(&mut app, key('u')),
            EditorAction::None
        ));
        assert_eq!(app.input, " ");
        app.insert("x");
        app.cursor = 0;
        assert!(matches!(
            handle_editor(&mut app, key('d')),
            EditorAction::None
        ));
        assert_eq!(app.input, " ");
        assert!(matches!(
            handle_editor(&mut app, key('c')),
            EditorAction::None
        ));
        assert!(app.input.is_empty());
        assert!(matches!(
            handle_editor(&mut app, key('d')),
            EditorAction::Exit
        ));
    }

    #[test]
    fn multiline_editor_moves_vertically_and_keeps_its_column() {
        let mut app = App::default();
        app.insert("first line\nx\nlast line");
        app.cursor = app.input.len() - 2;

        assert!(matches!(
            handle_editor(
                &mut app,
                InputEvent::Key(KeyEvent::new(KeyCode::Up, KeyModifiers::NONE))
            ),
            EditorAction::None
        ));
        assert_eq!(&app.input[app.cursor..], "\nlast line");
        assert_eq!(app.input[..app.cursor].rsplit('\n').next(), Some("x"));

        assert!(matches!(
            handle_editor(
                &mut app,
                InputEvent::Key(KeyEvent::new(KeyCode::Down, KeyModifiers::NONE))
            ),
            EditorAction::None
        ));
        assert_eq!(&app.input[app.cursor..], "ne");
        assert_eq!(app.input[..app.cursor].rsplit('\n').next(), Some("last li"));
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
    fn find_command_accepts_a_query_argument_without_matching_prefixes() {
        assert!(is_find_command("/find"));
        assert!(is_find_command("/find needle text"));
        assert!(!is_find_command("/findneedle"));
        assert!(!is_find_command("/finder"));
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
    fn search_letters_remain_editable_and_escape_preserves_the_draft() {
        let mut app = App {
            search: Some(Search {
                query: "match".to_owned(),
                current: 0,
                pinned: false,
            }),
            ..Default::default()
        };
        assert!(matches!(
            handle_editor(
                &mut app,
                InputEvent::Key(KeyEvent::new(KeyCode::Char('n'), KeyModifiers::NONE))
            ),
            EditorAction::None
        ));
        assert_eq!(app.input, "n");
        assert!(matches!(
            handle_editor(
                &mut app,
                InputEvent::Key(KeyEvent::new(KeyCode::Char('N'), KeyModifiers::SHIFT))
            ),
            EditorAction::None
        ));
        assert_eq!(app.input, "nN");
        assert!(matches!(
            handle_editor(
                &mut app,
                InputEvent::Key(KeyEvent::new(KeyCode::Esc, KeyModifiers::NONE))
            ),
            EditorAction::None
        ));
        assert_eq!(app.input, "nN");
    }

    #[test]
    fn escape_dismisses_completion_search_and_history_one_at_a_time() {
        let mut app = App {
            input: "keep this".to_owned(),
            completion: Some(CompletionView {
                replace_start: 0,
                replace_end: 4,
                candidates: vec!["kept".to_owned()],
                selected: 0,
            }),
            search: Some(Search {
                query: "match".to_owned(),
                current: 0,
                pinned: false,
            }),
            highlighted: Some(2),
            ..Default::default()
        };
        assert!(dismiss_temporary_state(&mut app));
        assert!(app.completion.is_none());
        assert!(app.search.is_some());
        assert!(app.highlighted.is_some());
        assert!(dismiss_temporary_state(&mut app));
        assert!(app.search.is_none());
        assert!(app.highlighted.is_some());
        assert!(dismiss_temporary_state(&mut app));
        assert!(app.highlighted.is_none());
        assert!(!dismiss_temporary_state(&mut app));
        assert_eq!(app.input, "keep this");
    }

    #[test]
    fn horizontal_cursor_motion_refreshes_completion_range() {
        let mut app = App {
            models: vec!["alpha".to_owned(), "alps".to_owned()],
            ..Default::default()
        };
        app.insert("/model alp");
        assert!(app.completion.is_some());
        handle_editor(
            &mut app,
            InputEvent::Key(KeyEvent::new(KeyCode::Left, KeyModifiers::NONE)),
        );
        let completion = app.completion.as_ref().unwrap();
        assert_eq!(completion.replace_end, app.input.len());
        assert_eq!(completion.candidates, vec!["alpha", "alps"]);
    }

    #[test]
    fn control_right_counts_skipped_spaces() {
        let mut app = App::default();
        app.insert("word   next");
        app.cursor = "word".len();
        handle_editor(
            &mut app,
            InputEvent::Key(KeyEvent::new(KeyCode::Right, KeyModifiers::CONTROL)),
        );
        assert_eq!(app.cursor, app.input.len());
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

    #[test]
    fn unicode_wrap_keeps_combining_and_emoji_graphemes_intact() {
        let text = "A界e\u{301}👩\u{200d}💻B";
        let lines = wrap_styled(text, 4, Style::default());
        assert!(lines.iter().all(|line| {
            let plain: String = line
                .spans
                .iter()
                .map(|span| span.content.as_ref())
                .collect();
            UnicodeWidthStr::width(plain.as_str()) <= 4
        }));
        let rendered: String = lines
            .iter()
            .map(|line| {
                line.spans
                    .iter()
                    .map(|span| span.content.as_ref())
                    .collect::<String>()
            })
            .collect();
        assert_eq!(rendered, text);
        assert!(lines.iter().any(|line| {
            line.spans
                .iter()
                .any(|span| span.content.contains("👩\u{200d}💻"))
        }));
    }

    #[test]
    fn editor_expands_and_tracks_cursor_through_wrapped_unicode_text() {
        let input = "a界bc\ne\u{301}👩\u{200d}💻";
        let layout = layout_editor(input, input.len(), 6);
        assert_eq!(layout.rows.len(), 3);
        assert_eq!(layout.cursor_row, 2);
        assert_eq!(layout.cursor_column, 5);
    }

    #[test]
    fn composer_grows_to_eight_rows_or_one_third_of_the_viewport() {
        assert_eq!(composer_height(24, 20), 8);
        assert_eq!(composer_height(9, 20), 3);
        assert_eq!(composer_height(2, 20), 1);
        assert_eq!(composer_height(24, 2), 2);
    }

    #[test]
    fn fullscreen_render_handles_tiny_and_normal_terminal_sizes() {
        let mut transcript = Transcript::default();
        print_text(
            &mut transcript,
            "a long history line that must reflow as the terminal changes width",
        );
        let app = App {
            input: "a long draft that wraps over several lines\nwith another line".to_owned(),
            cursor: 5,
            ..Default::default()
        };
        for (width, height) in [(1, 1), (8, 2), (12, 4), (80, 24)] {
            let backend = ratatui::backend::TestBackend::new(width, height);
            let mut terminal = Terminal::new(backend).unwrap();
            terminal
                .draw(|frame| render_fullscreen(frame, &app, &transcript))
                .unwrap();
        }
    }

    #[test]
    fn editor_vertical_motion_follows_wrapped_rows_and_keeps_goal_column() {
        let input = "abcdefghi\nx\n123456789";
        let (cursor, column) = vertical_cursor_move(input, 3, false, None, 8).unwrap();
        assert_eq!((cursor, column), (9, 5));
        let (cursor, column) = vertical_cursor_move(input, cursor, false, Some(column), 8).unwrap();
        assert_eq!((cursor, column), (11, 5));
        let (cursor, column) = vertical_cursor_move(input, cursor, false, Some(column), 8).unwrap();
        assert_eq!((cursor, column), (15, 5));
    }

    #[test]
    fn editor_moves_and_deletes_by_grapheme_cluster() {
        let mut app = App::default();
        app.insert("e\u{301}👩\u{200d}💻");
        handle_editor(
            &mut app,
            InputEvent::Key(KeyEvent::new(KeyCode::Left, KeyModifiers::NONE)),
        );
        assert_eq!(&app.input[app.cursor..], "👩\u{200d}💻");
        app.delete();
        assert_eq!(app.input, "e\u{301}");
        app.backspace();
        assert!(app.input.is_empty());
    }

    #[test]
    fn resize_reflows_history_without_losing_the_reading_anchor() {
        let mut transcript = Transcript::default();
        transcript.push_block(vec![Line::raw("abcdefghijklmnopqrstuvwxyz")]);
        let narrow = display_rows(&transcript, 10);
        let wide = display_rows(&transcript, 20);
        assert_eq!(narrow.len(), 3);
        assert_eq!(wide.len(), 2);
        let mut app = App::default();
        app.set_scroll_anchor(&narrow, 2);
        app.scroll_pinned = false;
        let visible = first_visible_row(&app, &wide, 1);
        assert_eq!(wide[visible].byte_offset, 20);
    }

    #[test]
    fn search_highlight_continues_across_reflowed_rows() {
        let mut transcript = Transcript::default();
        transcript.push_block(vec![Line::raw("abcdefgh")]);
        let rows = display_rows(&transcript, 5);
        assert_eq!(
            display_hit_ranges(&transcript, &rows[0], "def"),
            vec![(3, 5)]
        );
        assert_eq!(
            display_hit_ranges(&transcript, &rows[1], "def"),
            vec![(0, 1)]
        );
    }

    #[test]
    fn user_message_copy_text_keeps_source_newlines_without_display_prefixes() {
        let mut transcript = Transcript::default();
        print_user(&mut transcript, "first\nsecond");
        assert_eq!(transcript.copy_texts[1].as_deref(), Some("first\nsecond"));
        assert_eq!(transcript.lines[1].spans[0].content, "> ");
        assert_eq!(transcript.lines[2].spans[0].content, "  ");
    }

    #[test]
    fn paint_row_keeps_the_line_style_and_highlight_extends_it() {
        // Transcript rows carry their theme color as a line style (that is
        // what Line::styled/wrap_styled produce); the renderer must keep it
        // when rebuilding rows for Paragraph, which reads only spans.
        let source = Line::styled("plan.".to_owned(), theme::tool());
        let row = paint_row(&source);
        assert_eq!(row.style.fg, Some(Color::Yellow));

        let mut highlighted = paint_row(&source);
        highlighted.style = highlighted.style.add_modifier(Modifier::REVERSED);
        assert_eq!(
            highlighted.style.add_modifier,
            Modifier::REVERSED,
            "highlight extends, not replaces, the style"
        );
        assert_eq!(highlighted.style.fg, Some(Color::Yellow));
    }

    /// A transcript of `blocks` one-row blocks, pane shows `visible`.
    fn scrolled_transcript(blocks: usize) -> Transcript {
        let mut transcript = Transcript::default();
        for index in 0..blocks {
            transcript.push_block(vec![Line::raw(format!("row {index}"))]);
        }
        transcript
    }

    #[test]
    fn scroll_by_moves_from_the_bottom_and_re_engages_follow_at_the_bottom() {
        // 100 rows, 10 visible: pinned at the bottom (offset 90).
        let transcript = scrolled_transcript(100);
        let rows = display_rows(&transcript, 80);
        let mut app = App::default();
        let visible = 10;
        assert_eq!(first_visible_row(&app, &rows, visible), 90);

        // One wheel up: three rows up, follow off.
        scroll_by(&mut app, &transcript, visible, -3);
        assert_eq!(first_visible_row(&app, &rows, visible), 87);
        assert!(!app.scroll_pinned);

        // More up, never past the top.
        scroll_by(&mut app, &transcript, visible, -1000);
        assert_eq!(first_visible_row(&app, &rows, visible), 0);
        scroll_by(&mut app, &transcript, visible, -3);
        assert_eq!(first_visible_row(&app, &rows, visible), 0);

        // Down past the bottom: clamps and re-engages follow.
        scroll_by(&mut app, &transcript, visible, 1000);
        assert_eq!(first_visible_row(&app, &rows, visible), 90);
        assert!(app.scroll_pinned);
    }

    #[test]
    fn navigate_blocks_scrolls_the_target_into_view_and_lands_on_follow() {
        let transcript = scrolled_transcript(30);
        let rows = display_rows(&transcript, 80);
        let mut app = App::default();
        // With no highlight, Up starts from the last block (29).
        navigate_blocks(&transcript, &mut app, true);
        assert_eq!(app.highlighted, Some(28));
        // The target block's first row lands one quarter down the pane
        // (LAST_PANE_ROWS defaults to 18: offset 24), and the renderer's
        // clamp folds that into the last screenful (offset 12), putting
        // block 28 in view -- never a mirrored position from the bottom.
        let visible = 18;
        assert_eq!(app.scroll_offset_rows, 24);
        assert_eq!(first_visible_row(&app, &rows, visible), 12);
        assert!(!app.scroll_pinned);

        // Walking all the way back down to the last block re-pins follow.
        for _ in 0..29 {
            navigate_blocks(&transcript, &mut app, false);
        }
        assert_eq!(app.highlighted, Some(29));
        assert!(app.scroll_pinned);
    }

    #[test]
    fn search_anchor_moves_with_the_hit_and_manual_scroll_releases_it() {
        // Two unique queries so hits are unambiguous across 100 rows.
        let mut transcript = scrolled_transcript(100);
        transcript.push_block(vec![Line::raw("needle alpha")]);
        transcript.push_block(vec![Line::raw("filler")]);
        transcript.push_block(vec![Line::raw("needle beta")]);
        let mut app = App::default();
        let rows = display_rows(&transcript, 80);
        app.start_search(&transcript, "needle".to_owned());
        // First hit is "needle alpha" (line 100); anchored one row above.
        assert_eq!(app.scroll_offset_rows, 99);

        // n steps to "needle beta" (line 102) and re-anchors there.
        app.step_search(&transcript, true);
        assert_eq!(app.scroll_offset_rows, 101);
        assert!(app.search.as_ref().is_some_and(|search| search.pinned));

        // Wheel scroll releases the anchor; the view moves with the wheel
        // (the anchor was clamped to the last screenful at 103-10=93).
        scroll_by(&mut app, &transcript, 10, -3);
        assert!(app.search.as_ref().is_some_and(|search| !search.pinned));
        assert_eq!(first_visible_row(&app, &rows, 10), 90);

        // n re-engages the anchor, wrapping from the last hit back to the
        // first ("needle alpha", anchored one row above it).
        app.step_search(&transcript, true);
        assert_eq!(app.scroll_offset_rows, 99);
        assert!(app.search.as_ref().is_some_and(|search| search.pinned));
    }
}
