//! Drives the real, compiled `structure sessions list` binary as a
//! subprocess. Unlike `acp_binary_e2e.rs` / `print_binary_e2e.rs`, this needs
//! no mock model provider at all -- listing sessions touches no provider --
//! which is exactly the behavior this test locks in: `run()` in `main.rs`
//! must dispatch `sessions` before it resolves the provider configuration,
//! not unconditionally beforehand, or `structure sessions list` would fail
//! with an unrelated "API key is required" error for a user who only wants
//! to see their session history.

use std::path::{Path, PathBuf};

use structure_adapters::{FileSessionStore, NewSession};
use structure_protocol::SessionId;

/// Creates the directory and returns its canonicalized path. Canonicalizing
/// matters here in a way it would not for most temp-dir helpers: this test
/// computes `workspace_id_for` itself (in `seed_session`) *and* lets the
/// real subprocess compute it again independently (from its own
/// `std::env::current_dir()` after `current_dir(workspace_root)`), and the
/// two must land on the same hash. On macOS, `std::env::temp_dir()` returns
/// a path through `/var`, a symlink to `/private/var`; a subprocess's
/// `current_dir()` resolves that symlink but this function's raw
/// `PathBuf` join does not, so the two processes would otherwise hash two
/// different strings for what is, on disk, the same directory.
fn temp_dir(label: &str) -> PathBuf {
    let unique = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .expect("clock is valid")
        .as_nanos();
    let path = std::env::temp_dir().join(format!("structure-sessions-e2e-{label}-{unique}"));
    std::fs::create_dir_all(&path).expect("temp dir created");
    path.canonicalize().expect("temp dir canonicalizes")
}

fn seed_session(structure_home: &Path, workspace_root: &Path, session_id: &str) {
    let workspace_id = structure_cli::host::workspace_id_for(workspace_root);
    FileSessionStore::create(
        structure_home,
        NewSession {
            session_id: &SessionId::new(session_id),
            workspace_id: &workspace_id,
            cwd: workspace_root,
            profile: None,
            instructions_sha256: None,
        },
    )
    .expect("session file is created");
}

fn run_sessions(
    structure_home: &Path,
    workspace_root: &Path,
    args: &[&str],
) -> std::process::Output {
    std::process::Command::new(env!("CARGO_BIN_EXE_structure"))
        .arg("sessions")
        .args(args)
        .current_dir(workspace_root)
        .env_remove("OPENAI__API_KEY")
        .env_remove("OPENAI__BASE_URL")
        .env_remove("OPENAI__MODEL")
        .env("STRUCTURE_HOME", structure_home)
        .output()
        .expect("spawn the real structure binary")
}

#[test]
fn sessions_list_needs_no_model_provider_and_reports_an_empty_home() {
    let structure_home = temp_dir("empty-home");
    let workspace_root = temp_dir("empty-workspace");

    let output = run_sessions(&structure_home, &workspace_root, &["list"]);
    assert!(
        output.status.success(),
        "sessions list must succeed with no provider env vars set; stderr: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(
        String::from_utf8_lossy(&output.stdout).contains("no sessions found"),
        "stdout: {}",
        String::from_utf8_lossy(&output.stdout)
    );

    std::fs::remove_dir_all(&workspace_root).ok();
}

#[test]
fn sessions_list_scopes_to_the_current_workspace_unless_all_is_passed() {
    let structure_home = temp_dir("scoped-home");
    let workspace_a = temp_dir("workspace-a");
    let workspace_b = temp_dir("workspace-b");

    seed_session(&structure_home, &workspace_a, "session-a");
    seed_session(&structure_home, &workspace_b, "session-b");

    let scoped = run_sessions(&structure_home, &workspace_a, &["list"]);
    assert!(scoped.status.success());
    let scoped_stdout = String::from_utf8_lossy(&scoped.stdout).into_owned();
    assert!(
        scoped_stdout.contains("session-a"),
        "stdout: {scoped_stdout}"
    );
    assert!(
        !scoped_stdout.contains("session-b"),
        "unscoped listing must not include the other workspace's session; stdout: {scoped_stdout}"
    );

    let all = run_sessions(&structure_home, &workspace_a, &["list", "--all"]);
    assert!(all.status.success());
    let all_stdout = String::from_utf8_lossy(&all.stdout).into_owned();
    assert!(all_stdout.contains("session-a"), "stdout: {all_stdout}");
    assert!(all_stdout.contains("session-b"), "stdout: {all_stdout}");

    std::fs::remove_dir_all(&workspace_a).ok();
    std::fs::remove_dir_all(&workspace_b).ok();
}
