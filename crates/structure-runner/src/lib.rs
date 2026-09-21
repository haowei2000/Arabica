//! Runner environment boundary and concrete execution backends.
//!
//! This crate owns real tool execution, ordered stdout/stderr, cancellation,
//! filesystem confinement, and future process/container/remote runners. It
//! does not call model APIs or own sessions, memory, or UI concerns.

pub mod definitions;
pub mod output;
pub mod shell_classifier;

pub use definitions::{
    definition, find_files_definition, grep_definition, list_dir_definition, read_file_definition,
    tool_definitions, write_file_definition,
};
pub use output::{MAX_TOOL_OUTPUT_CHARS, truncate_output};
pub use shell_classifier::classify_shell_interaction;

use std::collections::{BTreeSet, HashSet};
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

/// One tool the local runner can execute.
///
/// A tool absent from the active [`LocalRunnerPolicy`] is treated as unknown:
/// it is neither advertised nor executable, and a model that names it anyway
/// gets the same error as for a tool that does not exist. Enabling execution is
/// therefore an explicit decision by the host, not a consequence of the model
/// guessing a name.
#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub enum LocalTool {
    ReadFile,
    ListDir,
    Grep,
    FindFiles,
    WriteFile,
}

impl LocalTool {
    pub const fn name(self) -> &'static str {
        match self {
            Self::ReadFile => "read_file",
            Self::ListDir => "list_dir",
            Self::Grep => "grep",
            Self::FindFiles => "find_files",
            Self::WriteFile => "write_file",
        }
    }

    pub const fn interaction(self) -> ToolInteractionKind {
        match self {
            Self::ReadFile | Self::ListDir | Self::Grep | Self::FindFiles => {
                ToolInteractionKind::Inspection
            }
            Self::WriteFile => ToolInteractionKind::Mutation,
        }
    }

    fn from_name(name: &str) -> Option<Self> {
        [
            Self::ReadFile,
            Self::ListDir,
            Self::Grep,
            Self::FindFiles,
            Self::WriteFile,
        ]
        .into_iter()
        .find(|tool| tool.name() == name)
    }
}

/// Which tools a [`LocalRunner`] advertises and will execute, plus the output
/// bounds that keep a single tool result from exhausting the context window.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct LocalRunnerPolicy {
    pub tools: BTreeSet<LocalTool>,
    /// Prefix each `read_file` line with its 1-based number. Off in the legacy
    /// policy because the benchmark campaigns recorded output without it.
    pub read_line_numbers: bool,
    pub max_read_bytes: u64,
    pub max_list_entries: usize,
    pub max_grep_matches: usize,
    pub max_find_results: usize,
    pub max_match_line_chars: usize,
}

impl LocalRunnerPolicy {
    /// The pre-existing surface: read and write, no line numbers. Keeping this
    /// the default preserves the behavior every recorded campaign depends on.
    pub fn legacy() -> Self {
        Self {
            tools: BTreeSet::from([LocalTool::ReadFile, LocalTool::WriteFile]),
            read_line_numbers: false,
            max_read_bytes: MAX_LOCAL_READ_BYTES,
            max_list_entries: 1_000,
            max_grep_matches: 200,
            max_find_results: 500,
            max_match_line_chars: 300,
        }
    }

    /// Inspection only: nothing in this set can change the workspace, so a host
    /// may run it without asking the user.
    pub fn read_only() -> Self {
        Self {
            tools: BTreeSet::from([
                LocalTool::ReadFile,
                LocalTool::ListDir,
                LocalTool::Grep,
                LocalTool::FindFiles,
            ]),
            read_line_numbers: true,
            ..Self::legacy()
        }
    }

    #[must_use]
    pub fn with_tool(mut self, tool: LocalTool) -> Self {
        self.tools.insert(tool);
        self
    }

    pub fn allows(&self, tool: LocalTool) -> bool {
        self.tools.contains(&tool)
    }
}

impl Default for LocalRunnerPolicy {
    fn default() -> Self {
        Self::legacy()
    }
}

/// Real local execution environment confined to one existing filesystem root.
#[derive(Debug)]
pub struct LocalRunner {
    root: PathBuf,
    policy: LocalRunnerPolicy,
    active_runs: HashSet<RunId>,
}

impl LocalRunner {
    pub fn new(root: impl Into<PathBuf>) -> Self {
        Self::with_policy(root, LocalRunnerPolicy::legacy())
    }

    pub fn with_policy(root: impl Into<PathBuf>, policy: LocalRunnerPolicy) -> Self {
        Self {
            root: root.into(),
            policy,
            active_runs: HashSet::new(),
        }
    }

    pub fn root(&self) -> &Path {
        &self.root
    }

    pub fn policy(&self) -> &LocalRunnerPolicy {
        &self.policy
    }

    fn enabled_tool(&self, name: &str) -> Option<LocalTool> {
        LocalTool::from_name(name).filter(|tool| self.policy.allows(*tool))
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
        let limit = self.policy.max_read_bytes;
        if metadata.len() > limit {
            return Err(RunnerError::new(format!(
                "read_file exceeds the {limit}-byte limit"
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
        let content = tokio::fs::read_to_string(&target)
            .await
            .map_err(|error| RunnerError::new(format!("read_file failed: {error}")))?;
        if !self.policy.read_line_numbers {
            return Ok(content);
        }
        // Line numbers let an edit tool report a near-miss by location and give
        // the model a stable way to talk about a region it has not copied.
        Ok(content
            .lines()
            .enumerate()
            .map(|(index, line)| format!("{:>6}\t{line}", index + 1))
            .collect::<Vec<_>>()
            .join("\n"))
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

    async fn canonical_root(&self) -> Result<PathBuf, RunnerError> {
        tokio::fs::canonicalize(&self.root)
            .await
            .map_err(|error| RunnerError::new(format!("local runner root unavailable: {error}")))
    }

    /// Resolve an optional relative `path` argument against the root. A missing
    /// argument means the root itself, because `validate_local_path` rejects
    /// `.` and an agent should not have to spell the root differently.
    async fn resolve_directory(
        &self,
        arguments: &serde_json::Value,
    ) -> Result<PathBuf, RunnerError> {
        let root = self.canonical_root().await?;
        let Some(path) = arguments.get("path").and_then(serde_json::Value::as_str) else {
            return Ok(root);
        };
        let relative = validate_local_path(path)?;
        let target = tokio::fs::canonicalize(root.join(relative))
            .await
            .map_err(|error| RunnerError::new(format!("local directory unavailable: {error}")))?;
        if !target.starts_with(&root) {
            return Err(RunnerError::new("local path escapes runner root"));
        }
        Ok(target)
    }

    async fn list_dir(&self, arguments: &serde_json::Value) -> Result<String, RunnerError> {
        let target = self.resolve_directory(arguments).await?;
        let mut reader = tokio::fs::read_dir(&target)
            .await
            .map_err(|error| RunnerError::new(format!("list_dir failed: {error}")))?;
        let mut names = Vec::new();
        while let Some(entry) = reader
            .next_entry()
            .await
            .map_err(|error| RunnerError::new(format!("list_dir failed: {error}")))?
        {
            let name = entry.file_name().to_string_lossy().into_owned();
            let file_type = entry
                .file_type()
                .await
                .map_err(|error| RunnerError::new(format!("list_dir failed: {error}")))?;
            // Symbolic links are marked and never followed, matching read_file
            // and write_file.
            names.push(if file_type.is_symlink() {
                format!("{name}@")
            } else if file_type.is_dir() {
                format!("{name}/")
            } else {
                name
            });
        }
        if names.is_empty() {
            return Ok("(empty directory)".to_owned());
        }
        names.sort();
        let total = names.len();
        names.truncate(self.policy.max_list_entries);
        if total > names.len() {
            names.push(format!(
                "...[{} more entries; narrow the path]...",
                total - names.len() + 1
            ));
        }
        Ok(names.join("\n"))
    }

    async fn grep(&self, arguments: &serde_json::Value) -> Result<String, RunnerError> {
        let pattern = arguments
            .get("pattern")
            .and_then(serde_json::Value::as_str)
            .ok_or_else(|| RunnerError::new("grep.pattern must be a string"))?;
        let regex = regex::Regex::new(pattern).map_err(|error| {
            RunnerError::new(format!(
                "grep.pattern is not a valid regular expression: {error}"
            ))
        })?;
        let root = self.canonical_root().await?;
        let target = self.resolve_directory(arguments).await?;
        let max_matches = self.policy.max_grep_matches;
        let max_line_chars = self.policy.max_match_line_chars;
        let max_bytes = self.policy.max_read_bytes;
        // The walker and its file reads are blocking, so they must not run on a
        // runtime worker that also drives the model request.
        tokio::task::spawn_blocking(move || {
            grep_blocking(
                &root,
                &target,
                &regex,
                max_matches,
                max_line_chars,
                max_bytes,
            )
        })
        .await
        .map_err(|error| RunnerError::new(format!("grep failed: {error}")))?
    }

    async fn find_files(&self, arguments: &serde_json::Value) -> Result<String, RunnerError> {
        let glob = arguments
            .get("glob")
            .and_then(serde_json::Value::as_str)
            .ok_or_else(|| RunnerError::new("find_files.glob must be a string"))?;
        let matcher = globset::GlobBuilder::new(glob)
            .literal_separator(true)
            .build()
            .map_err(|error| {
                RunnerError::new(format!("find_files.glob is not a valid glob: {error}"))
            })?
            .compile_matcher();
        let root = self.canonical_root().await?;
        let target = self.resolve_directory(arguments).await?;
        let max_results = self.policy.max_find_results;
        tokio::task::spawn_blocking(move || {
            find_files_blocking(&root, &target, &matcher, max_results)
        })
        .await
        .map_err(|error| RunnerError::new(format!("find_files failed: {error}")))?
    }
}

fn grep_blocking(
    root: &Path,
    target: &Path,
    regex: &regex::Regex,
    max_matches: usize,
    max_line_chars: usize,
    max_bytes: u64,
) -> Result<String, RunnerError> {
    let mut matches = Vec::new();
    let mut truncated = false;
    for entry in ignore::WalkBuilder::new(target).build().flatten() {
        if !entry.file_type().is_some_and(|kind| kind.is_file()) {
            continue;
        }
        let Ok(metadata) = entry.metadata() else {
            continue;
        };
        if metadata.len() > max_bytes {
            continue;
        }
        // A file that is not UTF-8 is binary for this purpose; skipping it is
        // cheaper and safer than scanning for NUL bytes.
        let Ok(content) = std::fs::read_to_string(entry.path()) else {
            continue;
        };
        let display = entry
            .path()
            .strip_prefix(root)
            .unwrap_or(entry.path())
            .display()
            .to_string();
        for (index, line) in content.lines().enumerate() {
            if !regex.is_match(line) {
                continue;
            }
            if matches.len() >= max_matches {
                truncated = true;
                break;
            }
            let mut text = line.trim_end().to_owned();
            if text.chars().count() > max_line_chars {
                text = text.chars().take(max_line_chars).collect::<String>() + "...";
            }
            matches.push(format!("{display}:{}:{text}", index + 1));
        }
        if truncated {
            break;
        }
    }
    if matches.is_empty() {
        return Ok("(no matches)".to_owned());
    }
    if truncated {
        matches.push(format!(
            "...[stopped at {max_matches} matches; narrow the pattern or path]..."
        ));
    }
    Ok(matches.join("\n"))
}

fn find_files_blocking(
    root: &Path,
    target: &Path,
    matcher: &globset::GlobMatcher,
    max_results: usize,
) -> Result<String, RunnerError> {
    let mut paths = Vec::new();
    let mut truncated = false;
    for entry in ignore::WalkBuilder::new(target).build().flatten() {
        if !entry.file_type().is_some_and(|kind| kind.is_file()) {
            continue;
        }
        let relative = entry.path().strip_prefix(root).unwrap_or(entry.path());
        if !matcher.is_match(relative) {
            continue;
        }
        if paths.len() >= max_results {
            truncated = true;
            break;
        }
        paths.push(relative.display().to_string());
    }
    if paths.is_empty() {
        return Ok("(no files matched)".to_owned());
    }
    paths.sort();
    if truncated {
        paths.push(format!(
            "...[stopped at {max_results} files; narrow the glob]..."
        ));
    }
    Ok(paths.join("\n"))
}

impl RunnerEnvironment for LocalRunner {
    fn classify(&self, call: &ToolCallItem) -> ToolInteractionKind {
        self.enabled_tool(&call.name)
            .map_or(ToolInteractionKind::Generic, LocalTool::interaction)
    }

    async fn execute(
        &mut self,
        request: ToolExecutionRequest,
    ) -> Result<ToolExecutionResult, RunnerError> {
        self.active_runs.insert(request.run_id.clone());
        let execution = match self.enabled_tool(&request.call.name) {
            Some(LocalTool::ReadFile) => self.read_file(&request.call.arguments).await,
            Some(LocalTool::WriteFile) => self.write_file(&request.call.arguments).await,
            Some(LocalTool::ListDir) => self.list_dir(&request.call.arguments).await,
            Some(LocalTool::Grep) => self.grep(&request.call.arguments).await,
            Some(LocalTool::FindFiles) => self.find_files(&request.call.arguments).await,
            // A disabled tool is reported exactly like one that does not exist:
            // the model was never told about it, and the reply must not reveal
            // that the capability could be switched on.
            None => Err(RunnerError::new(format!(
                "unknown local tool: {}",
                request.call.name
            ))),
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

    /// Create an empty directory unique to one test.
    async fn temp_root(label: &str) -> PathBuf {
        let unique = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .expect("clock is valid")
            .as_nanos();
        let root = std::env::temp_dir().join(format!("structure-runner-{label}-{unique}"));
        tokio::fs::create_dir(&root)
            .await
            .expect("test root is created");
        root
    }

    async fn call(runner: &mut LocalRunner, name: &str, arguments: serde_json::Value) -> String {
        let result = runner
            .execute(ToolExecutionRequest {
                run_id: RunId::new("run-1"),
                call: ToolCallItem {
                    id: None,
                    call_id: "call-1".to_owned(),
                    name: name.to_owned(),
                    arguments,
                    provider_state: None,
                },
            })
            .await
            .expect("execution is reported");
        match result.result.content.first() {
            Some(ContentBlock::Text { text }) => text.clone(),
            other => panic!("unexpected tool content: {other:?}"),
        }
    }

    #[tokio::test]
    async fn the_legacy_policy_hides_the_tools_it_does_not_enable() {
        let root = temp_root("legacy").await;
        let mut runner = LocalRunner::new(&root);
        // A disabled tool must be indistinguishable from one that never
        // existed, so a model cannot discover switched-off capabilities.
        for name in ["list_dir", "grep", "find_files", "no_such_tool"] {
            assert_eq!(
                call(&mut runner, name, serde_json::json!({})).await,
                format!("error: unknown local tool: {name}")
            );
        }
        assert_eq!(
            tool_definitions(&LocalRunnerPolicy::legacy())
                .iter()
                .map(|definition| definition.name.clone())
                .collect::<Vec<_>>(),
            vec!["read_file".to_owned(), "write_file".to_owned()]
        );
        tokio::fs::remove_dir_all(root).await.expect("cleanup");
    }

    #[tokio::test]
    async fn the_read_only_policy_cannot_change_the_workspace() {
        let root = temp_root("read-only").await;
        let mut runner = LocalRunner::with_policy(&root, LocalRunnerPolicy::read_only());
        assert_eq!(
            call(
                &mut runner,
                "write_file",
                serde_json::json!({"path": "new.txt", "content": "blocked"})
            )
            .await,
            "error: unknown local tool: write_file"
        );
        assert!(!root.join("new.txt").exists());
        tokio::fs::remove_dir_all(root).await.expect("cleanup");
    }

    #[tokio::test]
    async fn read_file_numbers_lines_only_when_the_policy_asks() {
        let root = temp_root("line-numbers").await;
        tokio::fs::write(root.join("two.txt"), "alpha\nbeta\n")
            .await
            .expect("fixture is written");

        let mut legacy = LocalRunner::new(&root);
        assert_eq!(
            call(
                &mut legacy,
                "read_file",
                serde_json::json!({"path": "two.txt"})
            )
            .await,
            "alpha\nbeta\n"
        );

        let mut numbered = LocalRunner::with_policy(&root, LocalRunnerPolicy::read_only());
        assert_eq!(
            call(
                &mut numbered,
                "read_file",
                serde_json::json!({"path": "two.txt"})
            )
            .await,
            "     1\talpha\n     2\tbeta"
        );
        tokio::fs::remove_dir_all(root).await.expect("cleanup");
    }

    #[tokio::test]
    async fn list_dir_marks_directories_and_defaults_to_the_root() {
        let root = temp_root("list").await;
        tokio::fs::create_dir(root.join("src"))
            .await
            .expect("directory is created");
        tokio::fs::write(root.join("Cargo.toml"), "[package]")
            .await
            .expect("fixture is written");
        let mut runner = LocalRunner::with_policy(&root, LocalRunnerPolicy::read_only());
        assert_eq!(
            call(&mut runner, "list_dir", serde_json::json!({})).await,
            "Cargo.toml\nsrc/"
        );
        assert_eq!(
            call(&mut runner, "list_dir", serde_json::json!({"path": "src"})).await,
            "(empty directory)"
        );
        assert_eq!(
            call(&mut runner, "list_dir", serde_json::json!({"path": "../"})).await,
            "error: local path must not contain '.', '..', or root components"
        );
        tokio::fs::remove_dir_all(root).await.expect("cleanup");
    }

    #[tokio::test]
    async fn grep_reports_matches_by_path_and_line() {
        let root = temp_root("grep").await;
        tokio::fs::write(root.join("a.txt"), "first\nneedle here\n")
            .await
            .expect("fixture is written");
        tokio::fs::write(root.join("b.txt"), "nothing\n")
            .await
            .expect("fixture is written");
        let mut runner = LocalRunner::with_policy(&root, LocalRunnerPolicy::read_only());
        assert_eq!(
            call(
                &mut runner,
                "grep",
                serde_json::json!({"pattern": "need.e"})
            )
            .await,
            "a.txt:2:needle here"
        );
        assert_eq!(
            call(
                &mut runner,
                "grep",
                serde_json::json!({"pattern": "absent"})
            )
            .await,
            "(no matches)"
        );
        assert!(
            call(&mut runner, "grep", serde_json::json!({"pattern": "("}))
                .await
                .starts_with("error: grep.pattern is not a valid regular expression")
        );
        tokio::fs::remove_dir_all(root).await.expect("cleanup");
    }

    #[tokio::test]
    async fn find_files_matches_globs_without_crossing_separators() {
        let root = temp_root("find").await;
        tokio::fs::create_dir(root.join("src"))
            .await
            .expect("directory is created");
        tokio::fs::write(root.join("src/lib.rs"), "")
            .await
            .expect("fixture is written");
        tokio::fs::write(root.join("top.rs"), "")
            .await
            .expect("fixture is written");
        let mut runner = LocalRunner::with_policy(&root, LocalRunnerPolicy::read_only());
        assert_eq!(
            call(
                &mut runner,
                "find_files",
                serde_json::json!({"glob": "*.rs"})
            )
            .await,
            "top.rs"
        );
        assert_eq!(
            call(
                &mut runner,
                "find_files",
                serde_json::json!({"glob": "**/*.rs"})
            )
            .await,
            "src/lib.rs\ntop.rs"
        );
        tokio::fs::remove_dir_all(root).await.expect("cleanup");
    }

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
