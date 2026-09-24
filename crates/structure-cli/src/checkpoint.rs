//! Checkpoints behind the terminal `/diff` and `/undo` commands.
//!
//! The local write tools (`write_file`, `edit_files`, `delete_file`) are
//! snapshotted before and after each call the runner executes, so the
//! agent's own file changes can be inspected and reverted without ever
//! touching changes the user made by hand: `/undo` first checks that every
//! file still holds exactly what the agent's call left behind, and refuses
//! (touching nothing) when it does not.
//!
//! The journal lives in process memory. A resumed session starts with an
//! empty journal, and writes made by MCP tools are not checkpointed at all
//! -- a server's side effects are not visible to this host.

use std::fmt::Write as _;
use std::path::Path;

use similar::{ChangeTag, TextDiff};
use structure_model::ToolCallItem;

/// Context lines shown around each diff hunk in `/diff`.
const DIFF_CONTEXT: usize = 3;

/// One file's movement across one agent tool call. `None` means the file
/// did not exist at that point, so a created file is `(None, Some(_))` and
/// a deleted one is `(Some(_), None)`.
#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) struct FileMovement {
    /// Relative path, spelled the way the tool call spelled it.
    pub(crate) path: String,
    pub(crate) before: Option<Vec<u8>>,
    pub(crate) after: Option<Vec<u8>>,
}

/// Every file one tool call touched. `/undo` reverts a whole call at once:
/// a single `edit_files` call may edit several files as one batch, and
/// half-reverting a batch is never what the user meant.
#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) struct WriteCheckpoint {
    pub(crate) call_id: String,
    pub(crate) tool: String,
    pub(crate) movements: Vec<FileMovement>,
}

/// What one `/undo` did, for the command's reply.
#[derive(Debug, Eq, PartialEq)]
pub(crate) enum UndoOutcome {
    /// The journal holds no agent writes.
    NothingToDo,
    /// The most recent call was reverted; lists the files it touched.
    Reverted { tool: String, paths: Vec<String> },
    /// A file changed on disk since the agent's call, so nothing was
    /// touched and the checkpoint stays for a later retry.
    Conflict { path: String },
    /// Restoring failed on an I/O error; the state of the other files in
    /// the same call is unspecified.
    Failed(String),
}

#[derive(Debug, Default)]
pub(crate) struct WriteJournal {
    checkpoints: Vec<WriteCheckpoint>,
}

impl WriteJournal {
    pub(crate) fn record(&mut self, checkpoint: WriteCheckpoint) {
        self.checkpoints.push(checkpoint);
    }

    /// Test-only: production paths report emptiness through `report` and
    /// `undo_last` instead of asking directly.
    #[cfg(test)]
    pub(crate) fn is_empty(&self) -> bool {
        self.checkpoints.is_empty()
    }

    /// Revert the most recent checkpointed call, or explain why not. The
    /// conflict check is all-or-nothing: if any file in the call no longer
    /// matches what the call left behind, every file is left alone.
    pub(crate) fn undo_last(&mut self, root: &Path) -> UndoOutcome {
        let Some(checkpoint) = self.checkpoints.last() else {
            return UndoOutcome::NothingToDo;
        };
        for movement in &checkpoint.movements {
            if read_file(root, &movement.path) != movement.after {
                return UndoOutcome::Conflict {
                    path: movement.path.clone(),
                };
            }
        }
        let checkpoint = self
            .checkpoints
            .pop()
            .expect("checked above that the journal is not empty");
        let mut paths = Vec::new();
        for movement in checkpoint.movements.iter().rev() {
            match restore(root, movement) {
                Ok(()) => paths.push(movement.path.clone()),
                Err(error) => return UndoOutcome::Failed(error),
            }
        }
        UndoOutcome::Reverted {
            tool: checkpoint.tool,
            paths,
        }
    }

    /// The `/diff` report: the agent's net change per file this session,
    /// with a unified diff, plus a marker when the file has since moved on
    /// on disk. Files the user changed by hand never appear here.
    pub(crate) fn report(&self, root: &Path) -> String {
        if self.checkpoints.is_empty() {
            return "No agent file changes recorded in this session.\n".to_owned();
        }
        let mut out = String::from("AGENT FILE CHANGES THIS SESSION\n");
        for net in self.net_movements() {
            let NetMovement {
                path,
                before,
                after,
                writes,
            } = net;
            match (before.as_deref(), after.as_deref()) {
                (None, Some(after)) => match std::str::from_utf8(after) {
                    Ok(after) => render_diff(&mut out, &path, "", after, "created"),
                    Err(_) => {
                        let _ = writeln!(out, "\n{path} · created · binary, {} bytes", after.len());
                    }
                },
                (Some(_), None) => {
                    let _ = writeln!(out, "\n{path} · deleted");
                }
                (Some(before), Some(after)) if before == after => {
                    let _ = writeln!(out, "\n{path} · written {writes}×, net unchanged");
                }
                (Some(before), Some(after)) => {
                    match (std::str::from_utf8(before), std::str::from_utf8(after)) {
                        (Ok(before), Ok(after)) => {
                            render_diff(
                                &mut out,
                                &path,
                                before,
                                after,
                                &format!("{writes} writes"),
                            );
                        }
                        _ => {
                            let _ = writeln!(
                                out,
                                "\n{path} · {writes} writes · binary, {} → {} bytes",
                                before.len(),
                                after.len()
                            );
                        }
                    }
                }
                // A no-op movement is never recorded in the first place.
                (None, None) => {}
            }
            if read_file(root, &path).as_deref() != after.as_deref() {
                out.push_str("  (changed on disk since the agent's last write)\n");
            }
        }
        out
    }

    /// First-before to last-after per path, in first-touch order: what the
    /// agent's writes netted out to, regardless of how many calls it took.
    fn net_movements(&self) -> Vec<NetMovement> {
        let mut nets: Vec<NetMovement> = Vec::new();
        for checkpoint in &self.checkpoints {
            for movement in &checkpoint.movements {
                if let Some(net) = nets.iter_mut().find(|net| net.path == movement.path) {
                    net.after = movement.after.clone();
                    net.writes += 1;
                } else {
                    nets.push(NetMovement {
                        path: movement.path.clone(),
                        before: movement.before.clone(),
                        after: movement.after.clone(),
                        writes: 1,
                    });
                }
            }
        }
        nets
    }
}

/// The per-path aggregate `report` renders: the first recorded `before`,
/// the last recorded `after`, and how many calls touched the path.
struct NetMovement {
    path: String,
    before: Option<Vec<u8>>,
    after: Option<Vec<u8>>,
    writes: usize,
}

/// One file's hunk of the `/diff` report: a summary line, then a unified
/// diff when both sides are UTF-8 text. Non-UTF-8 content summarizes as
/// bytes instead of printing garbage.
fn render_diff(out: &mut String, path: &str, before: &str, after: &str, label: &str) {
    let diff = TextDiff::from_lines(before, after);
    let (added, removed) = diff
        .iter_all_changes()
        .fold((0, 0), |(added, removed), change| match change.tag() {
            ChangeTag::Insert => (added + 1, removed),
            ChangeTag::Delete => (added, removed + 1),
            ChangeTag::Equal => (added, removed),
        });
    let counts = match (added, removed) {
        (0, 0) => String::new(),
        (added, 0) => format!(" · +{added} lines"),
        (0, removed) => format!(" · -{removed} lines"),
        (added, removed) => format!(" · +{added} -{removed} lines"),
    };
    let _ = writeln!(out, "\n{path} · {label}{counts}");
    let _ = writeln!(
        out,
        "{}",
        diff.unified_diff()
            .context_radius(DIFF_CONTEXT)
            .header(&format!("a/{path}"), &format!("b/{path}"))
    );
}

fn read_file(root: &Path, path: &str) -> Option<Vec<u8>> {
    std::fs::read(root.join(path)).ok()
}

/// Put `before` back: recreate the file, delete a created one, or restore
/// deleted bytes.
fn restore(root: &Path, movement: &FileMovement) -> Result<(), String> {
    let target = root.join(&movement.path);
    match &movement.before {
        Some(content) => std::fs::write(&target, content)
            .map_err(|error| format!("could not restore {}: {error}", movement.path)),
        None => std::fs::remove_file(&target)
            .map_err(|error| format!("could not remove {}: {error}", movement.path)),
    }
}

/// The relative paths a call will write, in first-mention order, for the
/// three local tools that change files. Read-only tools and MCP tools
/// return none. The paths are spelled as the call spelled them; the local
/// runner validates them before anything is written.
pub(crate) fn mutating_paths(call: &ToolCallItem) -> Vec<String> {
    let mut paths = Vec::new();
    let mut push = |path: Option<&str>| {
        if let Some(path) = path
            && !paths.iter().any(|seen: &String| seen == path)
        {
            paths.push(path.to_owned());
        }
    };
    match call.name.as_str() {
        "write_file" | "delete_file" => push(
            call.arguments
                .get("path")
                .and_then(serde_json::Value::as_str),
        ),
        "edit_files" => {
            if let Some(edits) = call
                .arguments
                .get("edits")
                .and_then(|value| value.as_array())
            {
                for edit in edits {
                    push(edit.get("path").and_then(serde_json::Value::as_str));
                }
            }
        }
        _ => {}
    }
    paths
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn temp_root(label: &str) -> std::path::PathBuf {
        let unique = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .expect("clock is valid")
            .as_nanos();
        let root = std::env::temp_dir().join(format!("structure-cli-checkpoint-{label}-{unique}"));
        std::fs::create_dir_all(&root).expect("test root is created");
        root
    }

    fn movement(path: &str, before: Option<&str>, after: Option<&str>) -> FileMovement {
        FileMovement {
            path: path.to_owned(),
            before: before.map(str::as_bytes).map(Vec::from),
            after: after.map(str::as_bytes).map(Vec::from),
        }
    }

    fn journal_with(movements: FileMovement) -> WriteJournal {
        let mut journal = WriteJournal::default();
        journal.record(WriteCheckpoint {
            call_id: "call-1".to_owned(),
            tool: "write_file".to_owned(),
            movements: vec![movements],
        });
        journal
    }

    #[test]
    fn undo_restores_previous_content() {
        let root = temp_root("restore");
        std::fs::write(root.join("note.txt"), "old").expect("file is written");
        std::fs::write(root.join("note.txt"), "agent edit").expect("agent writes");
        let mut journal = journal_with(movement("note.txt", Some("old"), Some("agent edit")));
        assert_eq!(
            journal.undo_last(&root),
            UndoOutcome::Reverted {
                tool: "write_file".to_owned(),
                paths: vec!["note.txt".to_owned()],
            }
        );
        assert_eq!(
            std::fs::read_to_string(root.join("note.txt")).unwrap(),
            "old"
        );
        assert!(journal.is_empty());
        std::fs::remove_dir_all(root).expect("cleanup");
    }

    #[test]
    fn undo_of_a_created_file_deletes_it() {
        let root = temp_root("create");
        std::fs::write(root.join("new.txt"), "agent content").expect("agent creates");
        let mut journal = journal_with(movement("new.txt", None, Some("agent content")));
        assert_eq!(
            journal.undo_last(&root),
            UndoOutcome::Reverted {
                tool: "write_file".to_owned(),
                paths: vec!["new.txt".to_owned()],
            }
        );
        assert!(!root.join("new.txt").exists());
        std::fs::remove_dir_all(root).expect("cleanup");
    }

    #[test]
    fn undo_of_a_deleted_file_restores_it() {
        let root = temp_root("delete");
        // The agent deleted the file, so nothing is on disk at undo time.
        let mut journal = journal_with(movement("note.txt", Some("original"), None));
        assert_eq!(
            journal.undo_last(&root),
            UndoOutcome::Reverted {
                tool: "write_file".to_owned(),
                paths: vec!["note.txt".to_owned()],
            }
        );
        assert_eq!(
            std::fs::read_to_string(root.join("note.txt")).unwrap(),
            "original"
        );
        std::fs::remove_dir_all(root).expect("cleanup");
    }

    #[test]
    fn undo_refuses_when_the_user_changed_the_file_and_keeps_the_checkpoint() {
        let root = temp_root("conflict");
        std::fs::write(root.join("note.txt"), "user edit").expect("user writes");
        let mut journal = journal_with(movement("note.txt", Some("old"), Some("agent edit")));
        assert_eq!(
            journal.undo_last(&root),
            UndoOutcome::Conflict {
                path: "note.txt".to_owned()
            }
        );
        // The user's content survives and the checkpoint is still there,
        // so /undo works again once the file is back to the agent's bytes.
        assert_eq!(
            std::fs::read_to_string(root.join("note.txt")).unwrap(),
            "user edit"
        );
        assert!(!journal.is_empty());
        std::fs::write(root.join("note.txt"), "agent edit").expect("file returns to agent bytes");
        assert!(matches!(
            journal.undo_last(&root),
            UndoOutcome::Reverted { .. }
        ));
        std::fs::remove_dir_all(root).expect("cleanup");
    }

    #[test]
    fn one_call_reverts_all_its_files_or_none() {
        let root = temp_root("batch");
        std::fs::write(root.join("a.txt"), "one").expect("a is written");
        std::fs::write(root.join("b.txt"), "user edit").expect("b holds user bytes");
        let mut journal = WriteJournal::default();
        journal.record(WriteCheckpoint {
            call_id: "call-1".to_owned(),
            tool: "edit_files".to_owned(),
            movements: vec![
                movement("a.txt", Some("one old"), Some("one")),
                movement("b.txt", Some("two old"), Some("two")),
            ],
        });
        assert_eq!(
            journal.undo_last(&root),
            UndoOutcome::Conflict {
                path: "b.txt".to_owned()
            }
        );
        // `a` was in the same call and must not have been reverted either.
        assert_eq!(std::fs::read_to_string(root.join("a.txt")).unwrap(), "one");
        std::fs::remove_dir_all(root).expect("cleanup");
    }

    #[test]
    fn report_shows_net_changes_and_external_edits() {
        let root = temp_root("report");
        let mut journal = WriteJournal::default();
        journal.record(WriteCheckpoint {
            call_id: "call-1".to_owned(),
            tool: "write_file".to_owned(),
            movements: vec![movement("list.txt", None, Some("alpha\nbeta\n"))],
        });
        journal.record(WriteCheckpoint {
            call_id: "call-2".to_owned(),
            tool: "edit_files".to_owned(),
            movements: vec![movement(
                "list.txt",
                Some("alpha\nbeta\n"),
                Some("alpha\ngamma\n"),
            )],
        });
        let report = journal.report(&root);
        assert!(
            report.contains("AGENT FILE CHANGES THIS SESSION"),
            "{report}"
        );
        // The net of create-then-edit is a created file: the whole final
        // content shows as additions.
        assert!(report.contains("list.txt · created · +2 lines"), "{report}");
        assert!(report.contains("+alpha"), "{report}");
        assert!(report.contains("+gamma"), "{report}");
        // list.txt does not exist on disk, which differs from the agent's
        // last write, so the external-change marker shows.
        assert!(
            report.contains("changed on disk since the agent's last write"),
            "{report}"
        );
        // A file the agent only wrote identical bytes to reports as
        // net-unchanged rather than an empty diff.
        journal.record(WriteCheckpoint {
            call_id: "call-3".to_owned(),
            tool: "write_file".to_owned(),
            movements: vec![movement("same.txt", Some("x"), Some("x"))],
        });
        assert!(
            journal
                .report(&root)
                .contains("same.txt · written 1×, net unchanged"),
            "{}",
            journal.report(&root)
        );
        std::fs::remove_dir_all(root).expect("cleanup");
    }

    #[test]
    fn empty_report_says_so() {
        assert_eq!(
            WriteJournal::default().report(Path::new("/nonexistent")),
            "No agent file changes recorded in this session.\n"
        );
    }

    fn call(name: &str, arguments: serde_json::Value) -> ToolCallItem {
        ToolCallItem {
            id: None,
            call_id: "call-1".to_owned(),
            name: name.to_owned(),
            arguments,
            provider_state: None,
        }
    }

    #[test]
    fn mutating_paths_covers_the_three_write_tools() {
        assert_eq!(
            mutating_paths(&call(
                "write_file",
                json!({"path": "a.txt", "content": "x"})
            )),
            vec!["a.txt"]
        );
        assert_eq!(
            mutating_paths(&call("delete_file", json!({"path": "b.txt"}))),
            vec!["b.txt"]
        );
        // edit_files touches several files once each, in order, even when
        // edits repeat a path.
        assert_eq!(
            mutating_paths(&call(
                "edit_files",
                json!({"edits": [
                    {"path": "a.txt", "old_string": "x", "new_string": "y"},
                    {"path": "c.txt", "old_string": "x", "new_string": "y"},
                    {"path": "a.txt", "old_string": "y", "new_string": "z"}
                ]})
            )),
            vec!["a.txt", "c.txt"]
        );
        assert!(mutating_paths(&call("read_file", json!({"path": "a.txt"}))).is_empty());
    }
}
