//! Discovery and rendering of workspace project instructions (`AGENTS.md`).
//!
//! Every CLI surface (terminal chat, `arabica acp`, `structure -p`) builds
//! or refreshes its runtime through [`crate::host::system_instructions`],
//! which calls [`rendered`] here, so the same files reach the model
//! regardless of entry point.

use std::path::Path;

use sha2::{Digest, Sha256};

/// The file name each workspace directory level may contribute.
const FILE_NAME: &str = "AGENTS.md";

/// Collect the `AGENTS.md` files that apply between `cwd` and the workspace
/// `root` (both inclusive), rendered as annotated instruction texts.
///
/// Directory level gives the priority: entries are ordered root-first, so
/// the deepest (most specific) file is rendered last, closest to the
/// conversation, and wins attention when two files disagree. Each entry
/// names the file it came from, so the model request records the source. A
/// file that is missing, unreadable, or not valid UTF-8 is skipped rather
/// than failing the session.
pub(crate) fn rendered(root: &Path, cwd: &Path) -> Vec<String> {
    let mut levels = Vec::new();
    let mut current = Some(cwd);
    while let Some(dir) = current {
        levels.push(dir);
        if dir == root {
            break;
        }
        current = dir.parent();
    }
    // If `cwd` is not under `root`, the walk above never reached it; fall
    // back to the root alone so the workspace's own instructions still apply.
    if !levels.contains(&root) {
        levels.push(root);
    }
    levels
        .into_iter()
        .rev()
        .filter_map(|dir| read_one(root, dir))
        .collect()
}

fn read_one(root: &Path, dir: &Path) -> Option<String> {
    let path = dir.join(FILE_NAME);
    let content = std::fs::read_to_string(&path).ok()?;
    let relative = path.strip_prefix(root).unwrap_or(&path);
    Some(format!(
        "Project instructions from {FILE_NAME} at {}:\n\n{content}",
        relative.display()
    ))
}

/// Hash of the effective system instructions, for the session header's
/// `instructions_sha256` field: a cheap "did the effective instructions
/// change" signal, never a second copy of the prompt text.
pub(crate) fn sha256(instructions: &[String]) -> String {
    let mut hasher = Sha256::new();
    for instruction in instructions {
        hasher.update(instruction.as_bytes());
        hasher.update([0]);
    }
    hex::encode(hasher.finalize())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    fn temp_root(label: &str) -> PathBuf {
        let unique = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .expect("clock is valid")
            .as_nanos();
        let root = std::env::temp_dir().join(format!("arabica-cli-instructions-{label}-{unique}"));
        std::fs::create_dir_all(&root).expect("test root is created");
        root
    }

    #[test]
    fn missing_files_discover_nothing() {
        let root = temp_root("missing");
        assert!(rendered(&root, &root).is_empty());
        std::fs::remove_dir_all(root).expect("cleanup");
    }

    #[test]
    fn a_root_file_is_annotated_with_its_relative_path() {
        let root = temp_root("root-file");
        std::fs::write(root.join(FILE_NAME), "Keep it small.\n").expect("file is written");
        let rendered = rendered(&root, &root);
        assert_eq!(rendered.len(), 1);
        assert!(
            rendered[0].starts_with("Project instructions from AGENTS.md at AGENTS.md:\n\n"),
            "{}",
            rendered[0]
        );
        assert!(rendered[0].contains("Keep it small."));
        std::fs::remove_dir_all(root).expect("cleanup");
    }

    #[test]
    fn deeper_directories_render_after_the_root() {
        let root = temp_root("levels");
        let nested = root.join("crates/arabica-cli");
        std::fs::create_dir_all(&nested).expect("nested directory is created");
        std::fs::write(root.join(FILE_NAME), "Workspace wide rule.\n").expect("root file");
        std::fs::write(nested.join(FILE_NAME), "Crate specific rule.\n").expect("nested file");
        let rendered = rendered(&root, &nested);
        assert_eq!(rendered.len(), 2);
        assert!(rendered[0].contains("at AGENTS.md:"));
        assert!(rendered[0].contains("Workspace wide rule."));
        assert!(rendered[1].contains("at crates/arabica-cli/AGENTS.md:"));
        assert!(rendered[1].contains("Crate specific rule."));
        std::fs::remove_dir_all(root).expect("cleanup");
    }

    #[test]
    fn a_cwd_outside_the_root_falls_back_to_the_root() {
        let root = temp_root("inside");
        let outside = temp_root("outside");
        std::fs::write(root.join(FILE_NAME), "Root only rule.\n").expect("root file");
        let rendered = rendered(&root, &outside);
        assert_eq!(rendered.len(), 1);
        assert!(rendered[0].contains("Root only rule."));
        std::fs::remove_dir_all(root).expect("cleanup");
        std::fs::remove_dir_all(outside).expect("cleanup");
    }

    #[test]
    fn a_file_that_is_not_utf8_is_skipped() {
        let root = temp_root("binary");
        std::fs::write(root.join(FILE_NAME), [0xff, 0xfe, 0x00]).expect("binary file");
        assert!(rendered(&root, &root).is_empty());
        std::fs::remove_dir_all(root).expect("cleanup");
    }

    #[test]
    fn the_hash_tracks_the_effective_instructions() {
        let a = sha256(&["one".to_owned()]);
        let a_again = sha256(&["one".to_owned()]);
        let b = sha256(&["one".to_owned(), "two".to_owned()]);
        assert_eq!(a, a_again);
        assert_ne!(a, b);
        // The NUL separator keeps ["ab"] and ["a", "b"] distinct.
        assert_ne!(
            sha256(&["ab".to_owned()]),
            sha256(&["a".to_owned(), "b".to_owned()])
        );
    }
}
