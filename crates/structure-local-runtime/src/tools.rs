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
}

impl LocalToolRegistry for BuiltinLocalToolRegistry {
    fn execute(&self, call: &LocalToolCall) -> LocalToolResult {
        let result = match call.name.as_str() {
            "list_workspace" => self.list_workspace(call),
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
