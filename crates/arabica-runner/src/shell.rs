//! The `shell` tool: one `sh -c` command, bounded in time and output, whose
//! processes do not outlive the call.
//!
//! Shell is the one local tool that cannot be confined to the workspace root.
//! Everything here limits what a single call can cost or leak. None of it makes
//! shell safe to run without a permission decision; that decision belongs to
//! the host, which should ask the user before every call.

use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

use serde::{Deserialize, Serialize};

use crate::RunnerError;
use crate::output::truncate_output;

static CAPTURE_SEQUENCE: AtomicU64 = AtomicU64::new(0);

/// `_`-separated name segments that mark an environment variable as a secret.
const CREDENTIAL_SEGMENTS: &[&str] = &[
    "KEY",
    "APIKEY",
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "PASSWD",
    "CREDENTIAL",
    "CREDENTIALS",
    "AUTHORIZATION",
    "BEARER",
    "ACCESSKEY",
    "PAT",
    "SIGNING",
    "PRIVATEKEY",
];

/// Limits for the `shell` tool.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(default, deny_unknown_fields)]
pub struct ShellPolicy {
    /// Seconds a command may run when the call does not choose.
    pub default_timeout_secs: u64,
    /// Upper bound on any timeout the model requests.
    pub max_timeout_secs: u64,
    /// Bytes kept from each captured stream. The middle of a larger stream is
    /// dropped before the model-facing result is truncated again.
    pub max_capture_bytes: u64,
    /// Exact variable names removed from the child environment in addition to
    /// the credential pattern, for example a provider's `api_key_env`.
    pub scrub_env: Vec<String>,
    /// Exact variable names exempt from the credential pattern.
    pub allow_env: Vec<String>,
}

impl Default for ShellPolicy {
    fn default() -> Self {
        Self {
            default_timeout_secs: 120,
            max_timeout_secs: 600,
            max_capture_bytes: 256 * 1024,
            scrub_env: Vec::new(),
            allow_env: Vec::new(),
        }
    }
}

impl ShellPolicy {
    /// Whether a variable is removed from the child environment.
    ///
    /// Anything a command can read, the model can read and send to its
    /// provider: `env` is one command away. Credentials are therefore removed
    /// by name pattern rather than listed one by one, because a real
    /// environment holds more of them than any list anticipates. Matching whole
    /// `_`-separated segments avoids false positives such as `MONKEY` or
    /// `KEYCHAIN_PATH`, and leaves `SSH_AUTH_SOCK` usable for git.
    pub fn scrubs(&self, name: &str) -> bool {
        if self.allow_env.iter().any(|allowed| allowed == name) {
            return false;
        }
        if self.scrub_env.iter().any(|scrubbed| scrubbed == name) {
            return true;
        }
        name.to_ascii_uppercase()
            .split('_')
            .any(|segment| CREDENTIAL_SEGMENTS.contains(&segment))
    }

    pub(crate) fn validate(&self) -> Result<(), RunnerError> {
        if self.default_timeout_secs == 0 || self.max_timeout_secs < self.default_timeout_secs {
            return Err(RunnerError::new(
                "shell timeouts must satisfy 1 <= default_timeout_secs <= max_timeout_secs",
            ));
        }
        if self.max_capture_bytes < 2 {
            return Err(RunnerError::new(
                "shell.max_capture_bytes must be at least 2",
            ));
        }
        Ok(())
    }
}

/// What one shell call produced.
pub(crate) struct ShellOutcome {
    /// Model-facing result: status line plus both streams, truncated.
    pub content: String,
    pub is_error: bool,
    pub stdout: String,
    pub stderr: String,
}

#[cfg(unix)]
pub(crate) async fn run(
    root: &Path,
    policy: &ShellPolicy,
    arguments: &serde_json::Value,
) -> Result<ShellOutcome, RunnerError> {
    let command = arguments
        .get("command")
        .and_then(serde_json::Value::as_str)
        .ok_or_else(|| RunnerError::new("shell.command must be a string"))?;
    if command.trim().is_empty() {
        return Err(RunnerError::new("shell.command must not be empty"));
    }
    if let Some(reason) = is_catastrophic_command(command) {
        return Err(RunnerError::new(format!(
            "shell command blocked by security sandbox: {reason}"
        )));
    }
    let timeout_secs = arguments
        .get("timeout_sec")
        .and_then(serde_json::Value::as_u64)
        .unwrap_or(policy.default_timeout_secs)
        .clamp(1, policy.max_timeout_secs);

    // Streams go to files, not pipes: a pipe the parent is not draining fills
    // and blocks the child, and a command that prints a lot must not deadlock.
    let capture = CaptureDir::create()?;
    let stdout_path = capture.path().join("stdout");
    let stderr_path = capture.path().join("stderr");
    let open = |path: &Path| {
        std::fs::File::create(path)
            .map_err(|error| RunnerError::new(format!("shell capture file unavailable: {error}")))
    };
    let mut child_command = tokio::process::Command::new("sh");
    child_command
        .arg("-c")
        .arg(command)
        .current_dir(root)
        .stdin(std::process::Stdio::null())
        .stdout(open(&stdout_path)?)
        .stderr(open(&stderr_path)?)
        // A new process group lets one signal reach everything the command
        // starts, including background jobs that outlive `sh` itself.
        .process_group(0)
        .kill_on_drop(true)
        .env("GIT_TERMINAL_PROMPT", "0")
        .env("PAGER", "cat")
        .env("GIT_PAGER", "cat");
    for (name, _) in std::env::vars_os() {
        if let Some(name) = name.to_str()
            && policy.scrubs(name)
        {
            child_command.env_remove(name);
        }
    }

    let mut child = child_command
        .spawn()
        .map_err(|error| RunnerError::new(format!("shell failed to start: {error}")))?;
    // Declared after `child`, so on an early return or a dropped future the
    // group is killed before `kill_on_drop` reaps the direct child.
    let mut group = ProcessGroup::new(child.id());
    let waited =
        tokio::time::timeout(std::time::Duration::from_secs(timeout_secs), child.wait()).await;
    // Kill the group whether or not the direct child exited: a call's
    // processes must not outlive it. After a normal exit the group is usually
    // already empty, and the kill is a harmless no-op.
    group.kill();
    let (exit_code, timed_out) = match waited {
        Ok(Ok(status)) => (status.code(), false),
        Ok(Err(error)) => return Err(RunnerError::new(format!("shell wait failed: {error}"))),
        Err(_) => {
            // The group kill above should already have ended `sh`. Kill the
            // direct child explicitly as well: if the group kill failed, for
            // instance because the child id was unavailable, waiting alone
            // would block until the command finished on its own and the
            // timeout would enforce nothing.
            let _ = child.start_kill();
            let _ = child.wait().await;
            (None, true)
        }
    };

    let stdout = read_bounded(&stdout_path, policy.max_capture_bytes).await?;
    let stderr = read_bounded(&stderr_path, policy.max_capture_bytes).await?;
    let mut content = match (timed_out, exit_code) {
        (true, _) => format!(
            "timed out after {timeout_secs}s; the command and every process it started were killed"
        ),
        (false, Some(code)) => format!("exit code {code}"),
        (false, None) => "terminated by a signal".to_owned(),
    };
    if !stdout.is_empty() {
        content.push_str("\nstdout:\n");
        content.push_str(&stdout);
    }
    if !stderr.is_empty() {
        content.push_str("\nstderr:\n");
        content.push_str(&stderr);
    }
    Ok(ShellOutcome {
        content: truncate_output(content),
        is_error: timed_out || exit_code != Some(0),
        stdout,
        stderr,
    })
}

#[cfg(not(unix))]
pub(crate) async fn run(
    _root: &Path,
    _policy: &ShellPolicy,
    _arguments: &serde_json::Value,
) -> Result<ShellOutcome, RunnerError> {
    Err(RunnerError::new("shell is only supported on unix hosts"))
}

/// The process group a shell call started, killed on drop.
#[cfg(unix)]
struct ProcessGroup(Option<rustix::process::Pid>);

#[cfg(unix)]
impl ProcessGroup {
    fn new(child_id: Option<u32>) -> Self {
        // `process_group(0)` makes the child the leader of a new group whose
        // id equals the child's pid.
        Self(
            child_id
                .and_then(|id| i32::try_from(id).ok())
                .and_then(rustix::process::Pid::from_raw),
        )
    }

    fn kill(&mut self) {
        if let Some(group) = self.0.take() {
            // ESRCH means the group is already gone, which is the goal.
            let _ = rustix::process::kill_process_group(group, rustix::process::Signal::KILL);
        }
    }
}

#[cfg(unix)]
impl Drop for ProcessGroup {
    fn drop(&mut self) {
        self.kill();
    }
}

/// A private directory for one call's captured output, removed on drop.
struct CaptureDir(PathBuf);

impl CaptureDir {
    fn create() -> Result<Self, RunnerError> {
        let path = std::env::temp_dir().join(format!(
            "structure-shell-{}-{}",
            std::process::id(),
            CAPTURE_SEQUENCE.fetch_add(1, Ordering::Relaxed)
        ));
        let mut builder = std::fs::DirBuilder::new();
        // Created with 0700 in one step, so no other user can open the
        // capture files between creation and a later permission change.
        #[cfg(unix)]
        {
            use std::os::unix::fs::DirBuilderExt;
            builder.mode(0o700);
        }
        builder.create(&path).map_err(|error| {
            RunnerError::new(format!("shell capture directory unavailable: {error}"))
        })?;
        Ok(Self(path))
    }

    fn path(&self) -> &Path {
        &self.0
    }
}

impl Drop for CaptureDir {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

/// Read a captured stream without loading an arbitrarily large file.
async fn read_bounded(path: &Path, max_bytes: u64) -> Result<String, RunnerError> {
    use tokio::io::{AsyncReadExt, AsyncSeekExt};

    let unavailable =
        |error: std::io::Error| RunnerError::new(format!("shell output unavailable: {error}"));
    let mut file = tokio::fs::File::open(path).await.map_err(unavailable)?;
    let size = file.metadata().await.map_err(unavailable)?.len();
    if size <= max_bytes {
        let mut bytes = Vec::new();
        file.read_to_end(&mut bytes).await.map_err(unavailable)?;
        return Ok(String::from_utf8_lossy(&bytes).into_owned());
    }
    let half = max_bytes / 2;
    let mut head = vec![0; usize::try_from(half).unwrap_or(usize::MAX)];
    file.read_exact(&mut head).await.map_err(unavailable)?;
    file.seek(std::io::SeekFrom::Start(size - half))
        .await
        .map_err(unavailable)?;
    let mut tail = vec![0; usize::try_from(half).unwrap_or(usize::MAX)];
    file.read_exact(&mut tail).await.map_err(unavailable)?;
    Ok(format!(
        "{}\n...[{} bytes omitted]...\n{}",
        String::from_utf8_lossy(&head),
        size - 2 * half,
        String::from_utf8_lossy(&tail)
    ))
}

/// Checks whether a shell command is catastrophic to the host or environment.
pub(crate) fn is_catastrophic_command(command: &str) -> Option<&'static str> {
    let trimmed = command.trim();
    if trimmed.is_empty() {
        return None;
    }

    // 1. Fork bomb detection (e.g. :(){ :|:& };:)
    let stripped_ws: String = trimmed.chars().filter(|c| !c.is_whitespace()).collect();
    if stripped_ws.contains(":(){:|:&};:") || stripped_ws.contains(":(){:|:&};") {
        return Some("fork bomb detected");
    }

    // 2. Chained command detection (quote-aware splitting)
    for part in split_command_chain(trimmed) {
        let part = part.trim();
        if part.is_empty() {
            continue;
        }

        let tokens = tokenize_args(part);
        if tokens.is_empty() {
            continue;
        }

        // Skip command wrappers like sudo, doas, env
        let mut idx = 0;
        while idx < tokens.len() {
            let t = tokens[idx].as_str();
            if t == "sudo" || t == "doas" || t == "env" || (t.starts_with('-') && idx == 0) {
                idx += 1;
            } else {
                break;
            }
        }
        if idx >= tokens.len() {
            continue;
        }

        let cmd_name = tokens[idx].as_str();
        let args = &tokens[idx + 1..];

        // 3. Destructive recursive removal of filesystem root
        if cmd_name == "rm" {
            let has_recursive = args.iter().any(|arg| {
                if arg == "-r" || arg == "-R" || arg == "--recursive" {
                    return true;
                }
                if arg.starts_with('-') && !arg.starts_with("--") {
                    return arg.contains('r') || arg.contains('R');
                }
                false
            });

            if has_recursive {
                for arg in args {
                    if arg == "/"
                        || arg == "/*"
                        || arg == "/."
                        || arg == "/.."
                        || arg == "/.*"
                        || arg == "--no-preserve-root"
                    {
                        return Some(
                            "destructive recursive deletion of filesystem root is blocked",
                        );
                    }
                }
            }
        }

        // 4. Raw block device destruction (mkfs, dd)
        if cmd_name.starts_with("mkfs") {
            for arg in args {
                if is_raw_block_device(arg) {
                    return Some("formatting raw system disk device is blocked");
                }
            }
        }

        if cmd_name == "dd" {
            for arg in args {
                if let Some(target) = arg.strip_prefix("of=")
                    && is_raw_block_device(target)
                {
                    return Some("direct overwrite of raw system disk device is blocked");
                }
            }
        }

        // 5. System root permission destruction (chmod -R, chown -R on /)
        if cmd_name == "chmod" || cmd_name == "chown" {
            let has_recursive = args.iter().any(|arg| arg == "-R" || arg == "--recursive");
            if has_recursive {
                for arg in args {
                    if arg == "/" || arg == "/*" {
                        return Some(
                            "recursive permission destruction of filesystem root is blocked",
                        );
                    }
                }
            }
        }
    }

    None
}

fn is_raw_block_device(path: &str) -> bool {
    let p = path.trim();
    p.starts_with("/dev/sd")
        || p.starts_with("/dev/nvme")
        || p.starts_with("/dev/vd")
        || p.starts_with("/dev/hd")
        || p.starts_with("/dev/disk")
}

fn split_command_chain(command: &str) -> Vec<String> {
    let mut parts = Vec::new();
    let mut current = String::new();
    let mut in_single = false;
    let mut in_double = false;
    let mut chars = command.chars().peekable();

    while let Some(c) = chars.next() {
        match c {
            '\'' if !in_double => {
                in_single = !in_single;
                current.push(c);
            }
            '"' if !in_single => {
                in_double = !in_double;
                current.push(c);
            }
            ';' if !in_single && !in_double => {
                parts.push(std::mem::take(&mut current));
            }
            '&' | '|' if !in_single && !in_double => {
                if chars.peek() == Some(&c) {
                    chars.next();
                }
                parts.push(std::mem::take(&mut current));
            }
            _ => {
                current.push(c);
            }
        }
    }
    if !current.trim().is_empty() {
        parts.push(current);
    }
    parts
}

fn tokenize_args(segment: &str) -> Vec<String> {
    let mut tokens = Vec::new();
    let mut current = String::new();
    let mut in_single = false;
    let mut in_double = false;

    for c in segment.chars() {
        match c {
            '\'' if !in_double => {
                in_single = !in_single;
            }
            '"' if !in_single => {
                in_double = !in_double;
            }
            c if c.is_whitespace() && !in_single && !in_double => {
                if !current.is_empty() {
                    tokens.push(std::mem::take(&mut current));
                }
            }
            _ => {
                current.push(c);
            }
        }
    }
    if !current.is_empty() {
        tokens.push(current);
    }
    tokens
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn catastrophic_commands_are_blocked() {
        for blocked in [
            "rm -rf /",
            "rm -fr /",
            "rm -r -f /",
            "rm -rf /*",
            "rm -rf /.*",
            "sudo rm -rf /",
            "doas rm -rf /",
            "rm --recursive --no-preserve-root /",
            ":(){ :|:& };:",
            ":(){:|:&};:",
            "mkfs.ext4 /dev/sda1",
            "mkfs /dev/nvme0n1p1",
            "dd if=/dev/zero of=/dev/sda bs=1M",
            "chmod -R 777 /",
            "chown -R root:root /",
            "echo hello; rm -rf /",
            "cd /tmp && rm -rf /",
            "true || rm -rf /",
        ] {
            assert!(
                is_catastrophic_command(blocked).is_some(),
                "must block catastrophic command: {blocked}"
            );
        }
    }

    #[test]
    fn safe_commands_are_allowed() {
        for safe in [
            "cargo build",
            "rm -rf target/",
            "rm -rf /tmp/scratch",
            "rm file.txt",
            "echo 'rm -rf /'",
            "echo \"rm -rf /\"",
            "cat /dev/null",
            "dd if=/dev/zero of=test.img bs=1M count=10",
            "chmod 644 /tmp/file.txt",
            "git clean -fdx",
        ] {
            assert!(
                is_catastrophic_command(safe).is_none(),
                "must allow safe command: {safe}"
            );
        }
    }
}
