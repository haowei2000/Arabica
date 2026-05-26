use crate::types::{LocalToolCall, LocalToolResult};
use serde::Serialize;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::thread;
use std::time::{Duration, Instant};

pub trait LocalToolRegistry {
    fn execute(&self, call: &LocalToolCall) -> LocalToolResult;
}

#[derive(Debug, Clone)]
pub struct BuiltinLocalToolRegistry {
    repo_root: PathBuf,
}

impl BuiltinLocalToolRegistry {
    pub fn new(repo_root: impl AsRef<Path>) -> Self {
        Self {
            repo_root: repo_root
                .as_ref()
                .canonicalize()
                .unwrap_or_else(|_| repo_root.as_ref().to_path_buf()),
        }
    }

    fn list_workspace(&self, call: &LocalToolCall) -> Result<serde_json::Value, String> {
        let max_entries = call
            .input
            .get("max_entries")
            .and_then(serde_json::Value::as_u64)
            .unwrap_or(16)
            .min(64) as usize;
        let mut entries = Vec::new();
        let read_dir = fs::read_dir(&self.repo_root)
            .map_err(|err| format!("failed to read workspace root: {err}"))?;

        for entry in read_dir.take(max_entries) {
            let entry = entry.map_err(|err| format!("failed to inspect workspace entry: {err}"))?;
            if is_sensitive_repo_path(&entry.path()) {
                continue;
            }
            let metadata = entry
                .metadata()
                .map_err(|err| format!("failed to inspect workspace metadata: {err}"))?;
            entries.push(WorkspaceEntry {
                name: entry.file_name().to_string_lossy().to_string(),
                kind: if metadata.is_dir() {
                    "directory"
                } else {
                    "file"
                }
                .to_string(),
                size_bytes: metadata.len(),
            });
        }

        Ok(serde_json::json!({
            "repo_root": self.repo_root,
            "entries": entries,
        }))
    }

    fn read_knowledge_source(&self, call: &LocalToolCall) -> Result<serde_json::Value, String> {
        let path = call
            .input
            .get("path")
            .and_then(serde_json::Value::as_str)
            .ok_or_else(|| "read_knowledge_source requires input.path".to_string())?;
        let max_bytes = call
            .input
            .get("max_bytes")
            .and_then(serde_json::Value::as_u64)
            .unwrap_or(4096)
            .min(64 * 1024) as usize;
        let text = fs::read_to_string(path)
            .map_err(|err| format!("failed to read knowledge source: {err}"))?;
        let preview = text.chars().take(max_bytes).collect::<String>();

        Ok(serde_json::json!({
            "source_id": call.input.get("source_id").cloned().unwrap_or(serde_json::Value::Null),
            "path": path,
            "chars_returned": preview.chars().count(),
            "truncated": text.chars().count() > preview.chars().count(),
            "preview": preview,
        }))
    }

    fn read_repo_file(&self, call: &LocalToolCall) -> Result<serde_json::Value, String> {
        let path = call
            .input
            .get("path")
            .and_then(serde_json::Value::as_str)
            .ok_or_else(|| "read_repo_file requires input.path".to_string())?;
        let max_bytes = call
            .input
            .get("max_bytes")
            .and_then(serde_json::Value::as_u64)
            .unwrap_or(8192)
            .min(128 * 1024) as usize;
        let absolute = safe_repo_path(&self.repo_root, path)?;
        if is_sensitive_repo_path(&absolute) {
            return Err("refusing to read sensitive local configuration file".to_string());
        }
        let text = fs::read_to_string(&absolute)
            .map_err(|err| format!("failed to read repo file: {err}"))?;
        let preview = text.chars().take(max_bytes).collect::<String>();

        Ok(serde_json::json!({
            "path": path,
            "absolute_path": absolute,
            "chars_returned": preview.chars().count(),
            "truncated": text.chars().count() > preview.chars().count(),
            "preview": preview,
        }))
    }

    fn search_repo(&self, call: &LocalToolCall) -> Result<serde_json::Value, String> {
        let query = call
            .input
            .get("query")
            .and_then(serde_json::Value::as_str)
            .ok_or_else(|| "search_repo requires input.query".to_string())?
            .trim()
            .to_string();
        if query.is_empty() {
            return Err("search_repo query must not be empty".to_string());
        }
        let max_matches = call
            .input
            .get("max_matches")
            .and_then(serde_json::Value::as_u64)
            .unwrap_or(12)
            .min(40) as usize;
        let mut matches = Vec::new();
        search_dir(
            &self.repo_root,
            &self.repo_root,
            &query,
            max_matches,
            &mut matches,
        )?;

        Ok(serde_json::json!({
            "query": query,
            "matches": matches,
            "match_count": matches.len(),
        }))
    }

    fn run_local_command(&self, call: &LocalToolCall) -> Result<serde_json::Value, String> {
        let argv = parse_command_argv(call)?;
        validate_local_command(&argv)?;
        let cwd = command_cwd(&self.repo_root, call)?;
        let timeout_ms = call
            .input
            .get("timeout_ms")
            .and_then(serde_json::Value::as_u64)
            .unwrap_or(30_000)
            .clamp(1_000, 120_000);
        let max_output_chars = call
            .input
            .get("max_output_chars")
            .and_then(serde_json::Value::as_u64)
            .unwrap_or(12_000)
            .clamp(1_000, 40_000) as usize;
        let started = Instant::now();
        let mut child = Command::new(&argv[0])
            .args(&argv[1..])
            .current_dir(&cwd)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .map_err(|err| format!("failed to start local command: {err}"))?;
        let mut timed_out = false;
        loop {
            if child
                .try_wait()
                .map_err(|err| format!("failed to poll local command: {err}"))?
                .is_some()
            {
                break;
            }
            if started.elapsed() >= Duration::from_millis(timeout_ms) {
                timed_out = true;
                child
                    .kill()
                    .map_err(|err| format!("failed to stop timed-out local command: {err}"))?;
                break;
            }
            thread::sleep(Duration::from_millis(25));
        }
        let output = child
            .wait_with_output()
            .map_err(|err| format!("failed to collect local command output: {err}"))?;
        let stdout = String::from_utf8_lossy(&output.stdout).into_owned();
        let stderr = String::from_utf8_lossy(&output.stderr).into_owned();
        let stdout_preview = truncate_chars(&stdout, max_output_chars);
        let stderr_preview = truncate_chars(&stderr, max_output_chars);

        Ok(serde_json::json!({
            "argv": argv,
            "cwd": cwd,
            "exit_code": output.status.code(),
            "success": output.status.success() && !timed_out,
            "timed_out": timed_out,
            "duration_ms": started.elapsed().as_millis() as u64,
            "stdout": stdout_preview,
            "stderr": stderr_preview,
            "stdout_truncated": stdout.chars().count() > max_output_chars,
            "stderr_truncated": stderr.chars().count() > max_output_chars,
        }))
    }
}

impl LocalToolRegistry for BuiltinLocalToolRegistry {
    fn execute(&self, call: &LocalToolCall) -> LocalToolResult {
        let result = match call.name.as_str() {
            "list_workspace" => self.list_workspace(call),
            "read_repo_file" => self.read_repo_file(call),
            "search_repo" => self.search_repo(call),
            "read_knowledge_source" => self.read_knowledge_source(call),
            "run_local_command" => self.run_local_command(call),
            _ => Err(format!("unknown local tool: {}", call.name)),
        };

        match result {
            Ok(output) => LocalToolResult {
                call_id: call.call_id.clone(),
                name: call.name.clone(),
                success: true,
                output,
                error: None,
            },
            Err(error) => LocalToolResult {
                call_id: call.call_id.clone(),
                name: call.name.clone(),
                success: false,
                output: serde_json::json!({}),
                error: Some(error),
            },
        }
    }
}

fn parse_command_argv(call: &LocalToolCall) -> Result<Vec<String>, String> {
    let argv = call
        .input
        .get("argv")
        .and_then(serde_json::Value::as_array)
        .ok_or_else(|| "run_local_command requires input.argv".to_string())?
        .iter()
        .map(|value| {
            value
                .as_str()
                .map(str::to_string)
                .ok_or_else(|| "run_local_command argv entries must be strings".to_string())
        })
        .collect::<Result<Vec<_>, _>>()?;
    if argv.is_empty() {
        return Err("run_local_command argv must not be empty".to_string());
    }
    if argv
        .iter()
        .any(|part| part.trim().is_empty() || part.contains('\0'))
    {
        return Err("run_local_command argv contains an empty or invalid argument".to_string());
    }
    Ok(argv)
}

fn validate_local_command(argv: &[String]) -> Result<(), String> {
    let cmd = argv[0].as_str();
    let allowed = match cmd {
        "cargo" => cargo_command_allowed(argv),
        "npm" => {
            argv.get(1).is_some_and(|sub| sub == "run")
                && argv.get(2).is_some_and(|script| {
                    matches!(
                        script.as_str(),
                        "build" | "desktop:build" | "lint" | "test" | "typecheck"
                    )
                })
        }
        "uv" => uv_command_allowed(argv),
        "git" => argv
            .get(1)
            .is_some_and(|sub| matches!(sub.as_str(), "status" | "diff" | "show" | "log")),
        "rg" | "ls" | "pwd" => true,
        _ => false,
    };
    if !allowed {
        return Err(format!("local command is not allowed: {}", argv.join(" ")));
    }
    if argv.iter().any(|part| is_dangerous_argument(part)) {
        return Err("local command includes a blocked destructive argument".to_string());
    }
    Ok(())
}

fn cargo_command_allowed(argv: &[String]) -> bool {
    match argv.get(1).map(String::as_str) {
        Some("check" | "test" | "clippy") => true,
        Some("fmt") => argv.iter().any(|part| part == "--check"),
        _ => false,
    }
}

fn uv_command_allowed(argv: &[String]) -> bool {
    if argv.get(1).is_none_or(|sub| sub != "run") {
        return false;
    }
    match argv.get(2).map(String::as_str) {
        Some("pytest") => true,
        Some("ruff") => match argv.get(3).map(String::as_str) {
            Some("check") => true,
            Some("format") => argv.iter().any(|part| part == "--check"),
            _ => false,
        },
        _ => false,
    }
}

fn is_dangerous_argument(value: &str) -> bool {
    matches!(
        value,
        "clean"
            | "publish"
            | "install"
            | "add"
            | "rm"
            | "reset"
            | "checkout"
            | "switch"
            | "commit"
            | "push"
            | "pull"
            | "merge"
            | "rebase"
            | "tag"
            | "stash"
            | "apply"
    ) || value == "--fix"
        || value == "--write"
        || value == "--force"
        || value == "-f"
}

fn command_cwd(repo_root: &Path, call: &LocalToolCall) -> Result<PathBuf, String> {
    let cwd = call
        .input
        .get("cwd")
        .and_then(serde_json::Value::as_str)
        .unwrap_or(".");
    let path = repo_root.join(cwd);
    let canonical = path
        .canonicalize()
        .map_err(|err| format!("failed to resolve command cwd: {err}"))?;
    if !canonical.starts_with(repo_root) {
        return Err("command cwd escapes the workspace root".to_string());
    }
    Ok(canonical)
}

fn truncate_chars(value: &str, max_chars: usize) -> String {
    if value.chars().count() <= max_chars {
        return value.to_string();
    }
    let mut truncated = value
        .chars()
        .take(max_chars.saturating_sub(3))
        .collect::<String>();
    truncated.push_str("...");
    truncated
}

#[derive(Debug, Clone, Serialize)]
struct WorkspaceEntry {
    name: String,
    kind: String,
    size_bytes: u64,
}

#[derive(Debug, Clone, Serialize)]
struct SearchMatch {
    path: String,
    line: usize,
    text: String,
}

fn safe_repo_path(repo_root: &Path, path: &str) -> Result<PathBuf, String> {
    let candidate = repo_root.join(path);
    let canonical = candidate
        .canonicalize()
        .map_err(|err| format!("failed to canonicalize repo path: {err}"))?;
    if !canonical.starts_with(repo_root) {
        return Err("repo path escapes the workspace root".to_string());
    }
    Ok(canonical)
}

fn search_dir(
    repo_root: &Path,
    current: &Path,
    query: &str,
    max_matches: usize,
    matches: &mut Vec<SearchMatch>,
) -> Result<(), String> {
    if matches.len() >= max_matches || should_skip_dir(current) {
        return Ok(());
    }
    let entries = fs::read_dir(current)
        .map_err(|err| format!("failed to read repo directory {}: {err}", current.display()))?;
    for entry in entries {
        if matches.len() >= max_matches {
            break;
        }
        let entry = entry.map_err(|err| format!("failed to inspect repo entry: {err}"))?;
        let path = entry.path();
        let metadata = entry
            .metadata()
            .map_err(|err| format!("failed to inspect repo metadata: {err}"))?;
        if metadata.is_dir() {
            search_dir(repo_root, &path, query, max_matches, matches)?;
        } else if metadata.is_file()
            && metadata.len() <= 1_000_000
            && is_searchable_path(&path)
            && !is_sensitive_repo_path(&path)
        {
            search_file(repo_root, &path, query, max_matches, matches)?;
        }
    }
    Ok(())
}

fn search_file(
    repo_root: &Path,
    path: &Path,
    query: &str,
    max_matches: usize,
    matches: &mut Vec<SearchMatch>,
) -> Result<(), String> {
    let Ok(text) = fs::read_to_string(path) else {
        return Ok(());
    };
    let query_lower = query.to_lowercase();
    for (index, line) in text.lines().enumerate() {
        if matches.len() >= max_matches {
            break;
        }
        if line.to_lowercase().contains(&query_lower) {
            let relative = path
                .strip_prefix(repo_root)
                .unwrap_or(path)
                .display()
                .to_string();
            matches.push(SearchMatch {
                path: relative,
                line: index + 1,
                text: line.trim().chars().take(240).collect(),
            });
        }
    }
    Ok(())
}

fn should_skip_dir(path: &Path) -> bool {
    let Some(name) = path.file_name().and_then(|name| name.to_str()) else {
        return false;
    };
    matches!(
        name,
        ".git" | ".venv" | "node_modules" | "target" | "dist" | "build" | ".structure"
    )
}

fn is_searchable_path(path: &Path) -> bool {
    let Some(extension) = path.extension().and_then(|extension| extension.to_str()) else {
        return false;
    };
    matches!(
        extension,
        "rs" | "py" | "ts" | "tsx" | "js" | "jsx" | "json" | "toml" | "md" | "tex" | "html"
    )
}

fn is_sensitive_repo_path(path: &Path) -> bool {
    let Some(name) = path.file_name().and_then(|name| name.to_str()) else {
        return false;
    };
    if matches!(name, ".env.example" | "example.env") {
        return false;
    }
    name == ".env"
        || name.starts_with(".env.")
        || name.ends_with(".env")
        || name.ends_with(".env.local")
        || name.contains("secret")
        || name.contains("credential")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn command_validation_allows_non_mutating_format_checks() {
        assert!(
            validate_local_command(&strings(&["cargo", "fmt", "--all", "--", "--check"])).is_ok()
        );
        assert!(validate_local_command(&strings(&[
            "uv", "run", "ruff", "format", "--check", "src", "tests"
        ]))
        .is_ok());
    }

    #[test]
    fn command_validation_blocks_mutating_format_commands() {
        assert!(validate_local_command(&strings(&["cargo", "fmt"])).is_err());
        assert!(
            validate_local_command(&strings(&["uv", "run", "ruff", "format", "src", "tests"]))
                .is_err()
        );
    }

    #[test]
    fn command_validation_keeps_lint_checks_non_mutating() {
        assert!(
            validate_local_command(&strings(&["uv", "run", "ruff", "check", "src", "tests"]))
                .is_ok()
        );
        assert!(validate_local_command(&strings(&[
            "uv", "run", "ruff", "check", "--fix", "src", "tests"
        ]))
        .is_err());
        assert!(validate_local_command(&strings(&["cargo", "check", "--workspace"])).is_ok());
    }

    #[test]
    fn builtin_registry_lists_workspace() {
        let root = unique_temp_dir("tools");
        fs::write(root.join("note.md"), "hello").unwrap();
        fs::write(root.join(".env"), "OPENAI__API_KEY=secret").unwrap();
        let registry = BuiltinLocalToolRegistry::new(&root);
        let result = registry.execute(&LocalToolCall {
            call_id: "tool_test".to_string(),
            name: "list_workspace".to_string(),
            input: serde_json::json!({ "max_entries": 8 }),
        });

        assert!(result.success);
        let entries = result.output["entries"].as_array().unwrap();
        assert!(entries.iter().any(|entry| { entry["name"] == "note.md" }));
        assert!(!entries.iter().any(|entry| { entry["name"] == ".env" }));

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn builtin_registry_searches_repo_text() {
        let root = unique_temp_dir("search");
        fs::write(root.join("runtime.rs"), "pub struct LocalAgentRuntime;\n").unwrap();
        fs::write(root.join("secret.env"), "LocalAgentRuntime secret\n").unwrap();
        let registry = BuiltinLocalToolRegistry::new(&root);
        let result = registry.execute(&LocalToolCall {
            call_id: "tool_search".to_string(),
            name: "search_repo".to_string(),
            input: serde_json::json!({ "query": "LocalAgentRuntime" }),
        });

        assert!(result.success);
        assert_eq!(result.output["match_count"], 1);

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn builtin_registry_refuses_sensitive_repo_reads() {
        let root = unique_temp_dir("sensitive");
        fs::write(root.join(".env"), "OPENAI__API_KEY=secret").unwrap();
        let registry = BuiltinLocalToolRegistry::new(&root);
        let result = registry.execute(&LocalToolCall {
            call_id: "tool_read".to_string(),
            name: "read_repo_file".to_string(),
            input: serde_json::json!({ "path": ".env" }),
        });

        assert!(!result.success);
        assert_eq!(
            result.error.as_deref(),
            Some("refusing to read sensitive local configuration file")
        );

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn builtin_registry_runs_allowlisted_local_command() {
        let root = unique_temp_dir("command");
        let registry = BuiltinLocalToolRegistry::new(&root);
        let result = registry.execute(&LocalToolCall {
            call_id: "tool_cmd".to_string(),
            name: "run_local_command".to_string(),
            input: serde_json::json!({
                "argv": ["pwd"],
                "timeout_ms": 5_000,
                "max_output_chars": 2_000
            }),
        });

        assert!(result.success);
        assert_eq!(result.output["success"], true);
        assert!(result.output["stdout"]
            .as_str()
            .unwrap_or_default()
            .contains(root.to_str().unwrap()));

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn builtin_registry_blocks_disallowed_local_command() {
        let root = unique_temp_dir("blocked-command");
        let registry = BuiltinLocalToolRegistry::new(&root);
        let result = registry.execute(&LocalToolCall {
            call_id: "tool_cmd_blocked".to_string(),
            name: "run_local_command".to_string(),
            input: serde_json::json!({
                "argv": ["git", "reset", "--hard"],
            }),
        });

        assert!(!result.success);
        assert_eq!(
            result.error.as_deref(),
            Some("local command is not allowed: git reset --hard")
        );

        fs::remove_dir_all(root).unwrap();
    }

    fn strings(parts: &[&str]) -> Vec<String> {
        parts.iter().map(|part| (*part).to_string()).collect()
    }

    fn unique_temp_dir(label: &str) -> PathBuf {
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let path = std::env::temp_dir().join(format!(
            "structure-local-runtime-{label}-{}-{nanos}",
            std::process::id()
        ));
        fs::create_dir_all(&path).unwrap();
        path
    }
}
