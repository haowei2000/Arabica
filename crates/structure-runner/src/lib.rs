//! Runner environment boundary and concrete execution backends.
//!
//! This crate owns real tool execution, ordered stdout/stderr, cancellation,
//! filesystem confinement, and future process/container/remote runners. It
//! does not call model APIs or own sessions, memory, or UI concerns.

pub mod definitions;
pub mod output;
pub mod shell_classifier;

pub use definitions::{read_file_definition, write_file_definition};
pub use output::{MAX_TOOL_OUTPUT_CHARS, truncate_output};
pub use shell_classifier::classify_shell_interaction;

use std::collections::HashSet;
use std::error::Error;
use std::fmt::{Display, Formatter};
use std::path::{Component, Path, PathBuf};

use structure_model::{ContentBlock, ToolCallItem, ToolResultItem};
use structure_protocol::{RunId, ToolInteractionKind};

const MAX_LOCAL_READ_BYTES: u64 = 1024 * 1024;

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum RunnerOutput {
    Stdout(String),
    Stderr(String),
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct RunnerError {
    message: String,
}

impl RunnerError {
    pub fn new(message: impl Into<String>) -> Self {
        Self {
            message: message.into(),
        }
    }
}

impl Display for RunnerError {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        self.message.fmt(formatter)
    }
}

impl Error for RunnerError {}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ToolExecutionRequest {
    pub run_id: RunId,
    pub call: ToolCallItem,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ToolExecutionResult {
    pub result: ToolResultItem,
    pub output: Vec<RunnerOutput>,
}

#[allow(async_fn_in_trait)]
pub trait RunnerEnvironment {
    fn classify(&self, _call: &ToolCallItem) -> ToolInteractionKind {
        ToolInteractionKind::Generic
    }

    async fn execute(
        &mut self,
        request: ToolExecutionRequest,
    ) -> Result<ToolExecutionResult, RunnerError>;
    async fn cancel(&mut self, run_id: &RunId) -> Result<bool, RunnerError>;
}

#[derive(Debug, Default)]
pub struct NoopRunner;

impl RunnerEnvironment for NoopRunner {
    async fn execute(
        &mut self,
        _request: ToolExecutionRequest,
    ) -> Result<ToolExecutionResult, RunnerError> {
        Err(RunnerError::new("no execution environment is configured"))
    }

    async fn cancel(&mut self, _run_id: &RunId) -> Result<bool, RunnerError> {
        Ok(false)
    }
}

/// Real local execution environment confined to one existing filesystem root.
#[derive(Debug)]
pub struct LocalRunner {
    root: PathBuf,
    active_runs: HashSet<RunId>,
}

impl LocalRunner {
    pub fn new(root: impl Into<PathBuf>) -> Self {
        Self {
            root: root.into(),
            active_runs: HashSet::new(),
        }
    }

    pub fn root(&self) -> &Path {
        &self.root
    }

    async fn resolve_existing_file(&self, path: &str) -> Result<PathBuf, RunnerError> {
        let relative = validate_local_path(path)?;
        let root = tokio::fs::canonicalize(&self.root)
            .await
            .map_err(|error| RunnerError::new(format!("local runner root unavailable: {error}")))?;
        let unresolved_target = root.join(relative);
        let unresolved_metadata = tokio::fs::symlink_metadata(&unresolved_target)
            .await
            .map_err(|error| RunnerError::new(format!("local file unavailable: {error}")))?;
        if unresolved_metadata.file_type().is_symlink() {
            return Err(RunnerError::new("read_file refuses symbolic links"));
        }
        let target = tokio::fs::canonicalize(unresolved_target)
            .await
            .map_err(|error| RunnerError::new(format!("local file unavailable: {error}")))?;
        if !target.starts_with(&root) {
            return Err(RunnerError::new("local file path escapes runner root"));
        }
        let metadata = tokio::fs::metadata(&target)
            .await
            .map_err(|error| RunnerError::new(format!("local file unavailable: {error}")))?;
        if !metadata.is_file() {
            return Err(RunnerError::new("local path is not a regular file"));
        }
        if metadata.len() > MAX_LOCAL_READ_BYTES {
            return Err(RunnerError::new(format!(
                "read_file exceeds the {MAX_LOCAL_READ_BYTES}-byte limit"
            )));
        }
        Ok(target)
    }

    async fn read_file(&self, arguments: &serde_json::Value) -> Result<String, RunnerError> {
        let path = arguments
            .get("path")
            .and_then(serde_json::Value::as_str)
            .ok_or_else(|| RunnerError::new("read_file.path must be a string"))?;
        let target = self.resolve_existing_file(path).await?;
        tokio::fs::read_to_string(&target)
            .await
            .map_err(|error| RunnerError::new(format!("read_file failed: {error}")))
    }

    async fn write_file(&self, arguments: &serde_json::Value) -> Result<String, RunnerError> {
        let path = arguments
            .get("path")
            .and_then(serde_json::Value::as_str)
            .ok_or_else(|| RunnerError::new("write_file.path must be a string"))?;
        let content = arguments
            .get("content")
            .and_then(serde_json::Value::as_str)
            .ok_or_else(|| RunnerError::new("write_file.content must be a string"))?;
        let relative = validate_local_path(path)?;
        let root = tokio::fs::canonicalize(&self.root)
            .await
            .map_err(|error| RunnerError::new(format!("local runner root unavailable: {error}")))?;
        let target = root.join(relative);
        let parent = tokio::fs::canonicalize(
            target
                .parent()
                .ok_or_else(|| RunnerError::new("write_file path has no parent"))?,
        )
        .await
        .map_err(|error| RunnerError::new(format!("write_file parent unavailable: {error}")))?;
        if !parent.starts_with(&root) {
            return Err(RunnerError::new("write_file path escapes runner root"));
        }
        if let Ok(metadata) = tokio::fs::symlink_metadata(&target).await
            && metadata.file_type().is_symlink()
        {
            return Err(RunnerError::new("write_file refuses symbolic links"));
        }
        tokio::fs::write(&target, content)
            .await
            .map_err(|error| RunnerError::new(format!("write_file failed: {error}")))?;
        Ok(format!(
            "wrote {} bytes to {}",
            content.len(),
            target.display()
        ))
    }
}

impl RunnerEnvironment for LocalRunner {
    fn classify(&self, call: &ToolCallItem) -> ToolInteractionKind {
        match call.name.as_str() {
            "read_file" => ToolInteractionKind::Inspection,
            "write_file" => ToolInteractionKind::Mutation,
            _ => ToolInteractionKind::Generic,
        }
    }

    async fn execute(
        &mut self,
        request: ToolExecutionRequest,
    ) -> Result<ToolExecutionResult, RunnerError> {
        self.active_runs.insert(request.run_id.clone());
        let execution = match request.call.name.as_str() {
            "read_file" => self.read_file(&request.call.arguments).await,
            "write_file" => self.write_file(&request.call.arguments).await,
            name => Err(RunnerError::new(format!("unknown local tool: {name}"))),
        };
        self.active_runs.remove(&request.run_id);
        let (content, is_error, output) = match execution {
            Ok(content) => (content.clone(), false, vec![RunnerOutput::Stdout(content)]),
            Err(error) => {
                let message = error.to_string();
                (
                    format!("error: {message}"),
                    true,
                    vec![RunnerOutput::Stderr(message)],
                )
            }
        };
        Ok(ToolExecutionResult {
            result: ToolResultItem {
                id: None,
                call_id: request.call.call_id,
                name: Some(request.call.name),
                content: vec![ContentBlock::text(content)],
                is_error,
            },
            output,
        })
    }

    async fn cancel(&mut self, run_id: &RunId) -> Result<bool, RunnerError> {
        Ok(self.active_runs.remove(run_id))
    }
}

fn validate_local_path(path: &str) -> Result<&Path, RunnerError> {
    let path = Path::new(path);
    if path.as_os_str().is_empty() || path.is_absolute() {
        return Err(RunnerError::new(
            "local path must be non-empty and relative",
        ));
    }
    if path
        .components()
        .any(|component| !matches!(component, Component::Normal(_)))
    {
        return Err(RunnerError::new(
            "local path must not contain '.', '..', or root components",
        ));
    }
    Ok(path)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn local_runner_writes_only_inside_its_root() {
        let unique = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .expect("clock is valid")
            .as_nanos();
        let root = std::env::temp_dir().join(format!("structure-local-runner-{unique}"));
        tokio::fs::create_dir(&root)
            .await
            .expect("test root is created");
        let mut runner = LocalRunner::new(&root);
        let result = runner
            .execute(ToolExecutionRequest {
                run_id: RunId::new("run-1"),
                call: ToolCallItem {
                    id: None,
                    call_id: "call-1".to_owned(),
                    name: "write_file".to_owned(),
                    arguments: serde_json::json!({
                        "path": "created.txt",
                        "content": "written by LocalRunner"
                    }),
                    provider_state: None,
                },
            })
            .await
            .expect("execution is reported");
        assert!(!result.result.is_error);
        assert_eq!(
            tokio::fs::read_to_string(root.join("created.txt"))
                .await
                .expect("written file is readable"),
            "written by LocalRunner"
        );

        let escaped = runner
            .execute(ToolExecutionRequest {
                run_id: RunId::new("run-2"),
                call: ToolCallItem {
                    id: None,
                    call_id: "call-2".to_owned(),
                    name: "write_file".to_owned(),
                    arguments: serde_json::json!({
                        "path": "../escaped.txt",
                        "content": "blocked"
                    }),
                    provider_state: None,
                },
            })
            .await
            .expect("policy failure is a tool result");
        assert!(escaped.result.is_error);
        tokio::fs::remove_dir_all(root)
            .await
            .expect("test root is removed");
    }

    #[tokio::test]
    async fn local_runner_reads_only_regular_files_inside_its_root() {
        let unique = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .expect("clock is valid")
            .as_nanos();
        let root = std::env::temp_dir().join(format!("structure-local-reader-{unique}"));
        tokio::fs::create_dir(&root)
            .await
            .expect("test root is created");
        tokio::fs::write(root.join("input.txt"), "evidence")
            .await
            .expect("fixture is written");
        let mut runner = LocalRunner::new(&root);
        let result = runner
            .execute(ToolExecutionRequest {
                run_id: RunId::new("run-read"),
                call: ToolCallItem {
                    id: None,
                    call_id: "call-read".to_owned(),
                    name: "read_file".to_owned(),
                    arguments: serde_json::json!({"path": "input.txt"}),
                    provider_state: None,
                },
            })
            .await
            .expect("read is reported");
        assert!(!result.result.is_error);
        assert_eq!(result.result.content, vec![ContentBlock::text("evidence")]);

        tokio::fs::remove_dir_all(root)
            .await
            .expect("test root is removed");
    }
}
