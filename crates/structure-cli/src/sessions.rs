//! `structure sessions list`: show sessions stored under `$STRUCTURE_HOME`
//! (`structure_adapters::FileSessionStore`), the same files `-p`'s
//! `--continue`/`--resume <id>` (`crates/structure-cli/src/print.rs`) pick
//! back up.

use clap::Subcommand;
use structure_adapters::{FileSessionStore, SessionListing};

#[derive(Subcommand, Debug)]
pub enum SessionsAction {
    /// List stored sessions, most recently active first.
    List {
        /// List sessions from every workspace, not just this one.
        #[arg(long)]
        all: bool,
    },
}

pub fn run(action: SessionsAction) -> i32 {
    let SessionsAction::List { all } = action;
    list(all)
}

fn list(all: bool) -> i32 {
    let structure_home = match structure_adapters::default_structure_home() {
        Ok(home) => home,
        Err(error) => {
            eprintln!("error: {error}");
            return 2;
        }
    };
    let runner_root = match std::env::current_dir() {
        Ok(root) => root,
        Err(error) => {
            eprintln!("error: {error}");
            return 2;
        }
    };
    let workspace_id = crate::host::workspace_id_for(&runner_root);
    let scope = if all { None } else { Some(&workspace_id) };

    let lines = match listing_lines(&structure_home, scope) {
        Ok(lines) => lines,
        Err(error) => {
            eprintln!("error: {error}");
            return 1;
        }
    };
    if lines.is_empty() {
        println!("no sessions found");
        return 0;
    }
    for line in &lines {
        println!("{line}");
    }
    0
}

/// One stored session formatted for display, plus its id for pickers.
pub(crate) struct SessionEntry {
    pub(crate) id: String,
    pub(crate) line: String,
}

/// One formatted line per stored session, most recently active first --
/// what `structure sessions list` prints and what the terminal `/sessions`
/// command shows. Empty when nothing is stored.
pub(crate) fn listing_lines(
    structure_home: &std::path::Path,
    scope: Option<&structure_protocol::WorkspaceId>,
) -> Result<Vec<String>, Box<dyn std::error::Error>> {
    Ok(listing_entries(structure_home, scope)?
        .into_iter()
        .map(|entry| entry.line)
        .collect())
}

/// The same listings as [`listing_lines`], keeping each session's id
/// alongside its formatted line so an interactive picker can act on the
/// selection.
pub(crate) fn listing_entries(
    structure_home: &std::path::Path,
    scope: Option<&structure_protocol::WorkspaceId>,
) -> Result<Vec<SessionEntry>, Box<dyn std::error::Error>> {
    let listings = FileSessionStore::list_sessions(structure_home, scope)?;
    let now_ms = now_ms();
    Ok(listings
        .into_iter()
        .map(|listing| {
            let id = listing.header.id.to_string();
            SessionEntry {
                id,
                line: format_listing(&listing, scope.is_none(), now_ms),
            }
        })
        .collect())
}

fn now_ms() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .expect("clock is after the epoch")
        .as_millis() as u64
}

fn format_listing(listing: &SessionListing, show_workspace: bool, now_ms: u64) -> String {
    let last_active = relative_time(listing.modified_at_ms, now_ms);
    if show_workspace {
        format!(
            "{:<40}  {:<10}  {:<20}  {}",
            listing.header.id, last_active, listing.header.workspace_id, listing.header.cwd
        )
    } else {
        format!(
            "{:<40}  {:<10}  {}",
            listing.header.id, last_active, listing.header.cwd
        )
    }
}

/// A short, unbounded relative-time label ("3m ago", "5d ago", ...) rather
/// than an absolute calendar date: a real date needs calendar arithmetic
/// (leap years, month lengths), which is not worth a chrono/time dependency
/// just for this list command.
fn relative_time(modified_at_ms: Option<u64>, now_ms: u64) -> String {
    let Some(modified_at_ms) = modified_at_ms else {
        return "-".to_owned();
    };
    let elapsed_secs = now_ms.saturating_sub(modified_at_ms) / 1000;
    if elapsed_secs < 60 {
        "just now".to_owned()
    } else if elapsed_secs < 60 * 60 {
        format!("{}m ago", elapsed_secs / 60)
    } else if elapsed_secs < 60 * 60 * 24 {
        format!("{}h ago", elapsed_secs / (60 * 60))
    } else {
        format!("{}d ago", elapsed_secs / (60 * 60 * 24))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn relative_time_buckets_by_the_largest_whole_unit() {
        let now = 1_000_000_000u64;
        let secs = |n: u64| now - n * 1000;
        assert_eq!(relative_time(None, now), "-");
        assert_eq!(relative_time(Some(now), now), "just now");
        assert_eq!(relative_time(Some(secs(59)), now), "just now");
        assert_eq!(relative_time(Some(secs(60)), now), "1m ago");
        assert_eq!(relative_time(Some(secs(90)), now), "1m ago");
        assert_eq!(relative_time(Some(secs(3599)), now), "59m ago");
        assert_eq!(relative_time(Some(secs(3600)), now), "1h ago");
        assert_eq!(relative_time(Some(secs(86_399)), now), "23h ago");
        assert_eq!(relative_time(Some(secs(86_400)), now), "1d ago");
    }

    #[test]
    fn relative_time_never_panics_on_a_modified_time_after_now() {
        // A clock adjustment or a file touched between "list the dir" and
        // "read its metadata" could put modified_at_ms slightly ahead of
        // now_ms; saturating_sub must clamp to zero, not overflow/panic.
        let now = 1_000u64;
        assert_eq!(relative_time(Some(now + 5_000), now), "just now");
    }

    fn header(id: &str, workspace_id: &str, cwd: &str) -> structure_adapters::SessionHeader {
        structure_adapters::SessionHeader {
            schema: "structure.session/v1".to_owned(),
            id: structure_protocol::SessionId::new(id),
            workspace_id: structure_protocol::WorkspaceId::new(workspace_id),
            cwd: cwd.to_owned(),
            created_at_ms: 0,
            protocol_version: structure_protocol::PROTOCOL_VERSION.to_owned(),
            profile: None,
            instructions_sha256: None,
        }
    }

    #[test]
    fn format_listing_omits_the_workspace_column_unless_asked_for_all() {
        let listing = SessionListing {
            header: header("session-1", "ws-1", "/repo"),
            path: "/home/sessions/ws-1/session-1.jsonl".into(),
            modified_at_ms: Some(0),
        };
        let scoped = format_listing(&listing, false, 0);
        assert!(!scoped.contains("ws-1"));
        assert!(scoped.contains("session-1"));
        assert!(scoped.contains("/repo"));

        let all = format_listing(&listing, true, 0);
        assert!(all.contains("ws-1"));
    }
}
