use crate::types::{LocalToolCall, LocalToolResult};
use serde::Serialize;
use std::fs;
use std::path::{Path, PathBuf};

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
            repo_root: repo_root.as_ref().to_path_buf(),
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
}

impl LocalToolRegistry for BuiltinLocalToolRegistry {
    fn execute(&self, call: &LocalToolCall) -> LocalToolResult {
        let result = match call.name.as_str() {
            "list_workspace" => self.list_workspace(call),
            "read_repo_file" => self.read_repo_file(call),
            "search_repo" => self.search_repo(call),
            "read_knowledge_source" => self.read_knowledge_source(call),
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
        } else if metadata.is_file() && metadata.len() <= 1_000_000 && is_searchable_path(&path) {
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn builtin_registry_lists_workspace() {
        let root = unique_temp_dir("tools");
        fs::write(root.join("note.md"), "hello").unwrap();
        let registry = BuiltinLocalToolRegistry::new(&root);
        let result = registry.execute(&LocalToolCall {
            call_id: "tool_test".to_string(),
            name: "list_workspace".to_string(),
            input: serde_json::json!({ "max_entries": 4 }),
        });

        assert!(result.success);
        assert!(result.output["entries"]
            .as_array()
            .unwrap()
            .iter()
            .any(|entry| { entry["name"] == "note.md" }));

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn builtin_registry_searches_repo_text() {
        let root = unique_temp_dir("search");
        fs::write(root.join("runtime.rs"), "pub struct LocalAgentRuntime;\n").unwrap();
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
