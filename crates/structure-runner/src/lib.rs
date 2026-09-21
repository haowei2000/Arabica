//! Runner environment boundary and concrete execution backends.
//!
//! This crate owns real tool execution, ordered stdout/stderr, cancellation,
//! filesystem confinement, and future process/container/remote runners. It
//! does not call model APIs or own sessions, memory, or UI concerns.

pub mod definitions;
pub mod output;
pub mod shell;
pub mod shell_classifier;

pub use definitions::{
    definition, delete_file_definition, edit_files_definition, find_files_definition,
    grep_definition, list_dir_definition, read_file_definition, shell_definition, tool_definitions,
    write_file_definition,
};
pub use output::{MAX_TOOL_OUTPUT_CHARS, truncate_output};
pub use shell::ShellPolicy;
pub use shell_classifier::classify_shell_interaction;

use std::collections::{BTreeSet, HashSet};
use std::error::Error;
use std::fmt::{Display, Formatter};
use std::path::{Component, Path, PathBuf};

use serde::{Deserialize, Serialize};
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
#[derive(Clone, Copy, Debug, Deserialize, Eq, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum LocalTool {
    ReadFile,
    ListDir,
    Grep,
    FindFiles,
    WriteFile,
    EditFiles,
    DeleteFile,
    Shell,
}

impl LocalTool {
    pub const fn name(self) -> &'static str {
        match self {
            Self::ReadFile => "read_file",
            Self::ListDir => "list_dir",
            Self::Grep => "grep",
            Self::FindFiles => "find_files",
            Self::WriteFile => "write_file",
            Self::EditFiles => "edit_files",
            Self::DeleteFile => "delete_file",
            Self::Shell => "shell",
        }
    }

    pub const fn interaction(self) -> ToolInteractionKind {
        match self {
            Self::ReadFile | Self::ListDir | Self::Grep | Self::FindFiles => {
                ToolInteractionKind::Inspection
            }
            Self::WriteFile | Self::EditFiles | Self::DeleteFile => ToolInteractionKind::Mutation,
            // A command's effect depends on its text; the runner classifies
            // each call from the command instead of from the tool name.
            Self::Shell => ToolInteractionKind::Generic,
        }
    }

    /// Whether this tool is one of the mutually exclusive edit formats.
    ///
    /// Exactly one may be enabled at a time: offering a model two ways to
    /// modify a file makes it choose before it edits, and that choice is one
    /// more thing to get wrong. A second format (an `apply_patch` style whole
    /// diff) is meant to be compared against this one, not offered beside it.
    pub const fn is_edit_format(self) -> bool {
        matches!(self, Self::EditFiles)
    }

    fn from_name(name: &str) -> Option<Self> {
        [
            Self::ReadFile,
            Self::ListDir,
            Self::Grep,
            Self::FindFiles,
            Self::WriteFile,
            Self::EditFiles,
            Self::DeleteFile,
            Self::Shell,
        ]
        .into_iter()
        .find(|tool| tool.name() == name)
    }
}

/// Which tools a [`LocalRunner`] advertises and will execute, plus the output
/// bounds that keep a single tool result from exhausting the context window.
///
/// The policy deserializes from a config file (`[runner]` in TOML, say), so a
/// host can load it; loading and merging files is the host's job. A host that
/// layers project configuration over user configuration must only let the
/// project tighten it: a repository that could enable `shell` or empty
/// `shell.scrub_env` could run code or read the user's credentials.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(default, deny_unknown_fields)]
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
    pub shell: ShellPolicy,
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
            shell: ShellPolicy::default(),
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

    /// Read-only exploration plus the mutation tools a coding agent needs.
    ///
    /// Shell is deliberately absent: it cannot be confined to the root, so a
    /// host adds it only when it can ask the user first.
    pub fn coding() -> Self {
        Self {
            tools: BTreeSet::from([
                LocalTool::ReadFile,
                LocalTool::ListDir,
                LocalTool::Grep,
                LocalTool::FindFiles,
                LocalTool::WriteFile,
                LocalTool::EditFiles,
                LocalTool::DeleteFile,
            ]),
            ..Self::read_only()
        }
    }

    /// Enabled edit formats. More than one is a configuration error; see
    /// [`LocalTool::is_edit_format`].
    pub fn edit_formats(&self) -> Vec<LocalTool> {
        self.tools
            .iter()
            .copied()
            .filter(|tool| tool.is_edit_format())
            .collect()
    }

    /// Reject a policy that is internally inconsistent.
    ///
    /// A host must call this after loading a policy from a file: serde checks
    /// shape, not meaning.
    pub fn validate(&self) -> Result<(), RunnerError> {
        let formats = self.edit_formats();
        if formats.len() > 1 {
            return Err(RunnerError::new(format!(
                "at most one edit format may be enabled, found {}",
                formats
                    .iter()
                    .map(|tool| tool.name())
                    .collect::<Vec<_>>()
                    .join(", ")
            )));
        }
        for (name, value) in [
            ("max_list_entries", self.max_list_entries),
            ("max_grep_matches", self.max_grep_matches),
            ("max_find_results", self.max_find_results),
            ("max_match_line_chars", self.max_match_line_chars),
        ] {
            if value == 0 {
                return Err(RunnerError::new(format!("{name} must be positive")));
            }
        }
        if self.max_read_bytes == 0 {
            return Err(RunnerError::new("max_read_bytes must be positive"));
        }
        self.shell.validate()
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

    /// Resolve a regular file inside the root, refusing symbolic links and any
    /// path that escapes. Returns the canonical path and its size.
    async fn resolve_regular_file(
        &self,
        path: &str,
        tool: &str,
    ) -> Result<(PathBuf, u64), RunnerError> {
        let relative = validate_local_path(path)?;
        let root = tokio::fs::canonicalize(&self.root)
            .await
            .map_err(|error| RunnerError::new(format!("local runner root unavailable: {error}")))?;
        let unresolved_target = root.join(relative);
        let unresolved_metadata = tokio::fs::symlink_metadata(&unresolved_target)
            .await
            .map_err(|error| RunnerError::new(format!("local file unavailable: {error}")))?;
        if unresolved_metadata.file_type().is_symlink() {
            return Err(RunnerError::new(format!("{tool} refuses symbolic links")));
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
        Ok((target, metadata.len()))
    }

    /// Resolve a file this runner may read whole, enforcing the size cap.
    ///
    /// `tool` names the caller so a refusal says which tool refused. `read_file`
    /// keeps its original wording because recorded campaigns contain it.
    async fn resolve_readable_file(&self, path: &str, tool: &str) -> Result<PathBuf, RunnerError> {
        let (target, size) = self.resolve_regular_file(path, tool).await?;
        let limit = self.policy.max_read_bytes;
        if size > limit {
            return Err(RunnerError::new(format!(
                "{tool} exceeds the {limit}-byte limit"
            )));
        }
        Ok(target)
    }

    async fn resolve_existing_file(&self, path: &str) -> Result<PathBuf, RunnerError> {
        self.resolve_readable_file(path, "read_file").await
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

    /// Apply a batch of exact string replacements across one or more files.
    ///
    /// Every edit is validated and applied in memory before anything is
    /// written, so a batch that fails validation changes nothing on disk. Two
    /// edits to the same file apply in order, and the second sees the result of
    /// the first. Writing is still per file, so an I/O failure part way through
    /// can leave earlier files written; the result reports what was applied.
    async fn edit_files(&self, arguments: &serde_json::Value) -> Result<String, RunnerError> {
        let edits = arguments
            .get("edits")
            .and_then(serde_json::Value::as_array)
            .ok_or_else(|| RunnerError::new("edit_files.edits must be an array"))?;
        if edits.is_empty() {
            return Err(RunnerError::new("edit_files.edits must not be empty"));
        }
        let mut pending: Vec<(PathBuf, String)> = Vec::new();
        let mut applied: Vec<String> = Vec::new();
        for (index, edit) in edits.iter().enumerate() {
            let at = index + 1;
            let field = |name: &str| -> Result<String, RunnerError> {
                edit.get(name)
                    .and_then(serde_json::Value::as_str)
                    .map(str::to_owned)
                    .ok_or_else(|| {
                        RunnerError::new(format!("edit_files.edits[{at}].{name} must be a string"))
                    })
            };
            let path = field("path")?;
            let old_string = field("old_string")?;
            let new_string = field("new_string")?;
            let replace_all = edit
                .get("replace_all")
                .and_then(serde_json::Value::as_bool)
                .unwrap_or(false);
            if old_string.is_empty() {
                return Err(RunnerError::new(format!(
                    "edit_files.edits[{at}].old_string must not be empty; use write_file to create or replace a whole file"
                )));
            }
            if old_string == new_string {
                return Err(RunnerError::new(format!(
                    "edit_files.edits[{at}] leaves {path} unchanged"
                )));
            }
            let target = self.resolve_readable_file(&path, "edit_files").await?;
            let current = match pending.iter().find(|(known, _)| known == &target) {
                Some((_, content)) => content.clone(),
                None => tokio::fs::read_to_string(&target)
                    .await
                    .map_err(|error| RunnerError::new(format!("edit_files failed: {error}")))?,
            };
            let matches = current.matches(old_string.as_str()).count();
            if matches == 0 {
                return Err(RunnerError::new(format!(
                    "edit_files.edits[{at}] found no match in {path}{}",
                    near_miss_hint(&current, &old_string)
                )));
            }
            if matches > 1 && !replace_all {
                return Err(RunnerError::new(format!(
                    "edit_files.edits[{at}] matched {matches} times in {path}; extend old_string until it is unique, or set replace_all"
                )));
            }
            let updated = if replace_all {
                current.replace(old_string.as_str(), &new_string)
            } else {
                current.replacen(old_string.as_str(), &new_string, 1)
            };
            let count = if replace_all { matches } else { 1 };
            match pending.iter_mut().find(|(known, _)| known == &target) {
                Some((_, content)) => *content = updated,
                None => pending.push((target, updated)),
            }
            applied.push(format!(
                "{path}: {count} replacement{}",
                if count == 1 { "" } else { "s" }
            ));
        }
        for (target, content) in &pending {
            tokio::fs::write(target, content).await.map_err(|error| {
                RunnerError::new(format!(
                    "edit_files failed while writing {}: {error}",
                    target.display()
                ))
            })?;
        }
        Ok(applied.join("\n"))
    }

    async fn delete_file(&self, arguments: &serde_json::Value) -> Result<String, RunnerError> {
        let path = arguments
            .get("path")
            .and_then(serde_json::Value::as_str)
            .ok_or_else(|| RunnerError::new("delete_file.path must be a string"))?;
        // Deleting does not read the file, so the read size cap must not apply.
        let (target, _) = self.resolve_regular_file(path, "delete_file").await?;
        tokio::fs::remove_file(&target)
            .await
            .map_err(|error| RunnerError::new(format!("delete_file failed: {error}")))?;
        Ok(format!("deleted {path}"))
    }
}

/// Point at the likely intended region when an exact match fails.
///
/// The usual cause is that the model reproduced the surrounding text from
/// memory rather than from the file, so the first line is usually right even
/// when the rest drifted.
fn near_miss_hint(content: &str, old_string: &str) -> String {
    let Some(first) = old_string.lines().next().map(str::trim) else {
        return String::new();
    };
    if first.is_empty() {
        return String::new();
    }
    let lines = content
        .lines()
        .enumerate()
        .filter(|(_, line)| line.trim() == first)
        .map(|(index, _)| (index + 1).to_string())
        .take(5)
        .collect::<Vec<_>>();
    if lines.is_empty() {
        return String::new();
    }
    format!(
        "; its first line appears at line{} {}, so check the surrounding context and whitespace",
        if lines.len() == 1 { "" } else { "s" },
        lines.join(", ")
    )
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

/// One tool call's result before it is wrapped for the Runtime.
struct ToolRun {
    content: String,
    is_error: bool,
    output: Vec<RunnerOutput>,
}

impl ToolRun {
    /// A successful non-shell tool: its text is both the result and the one
    /// stdout chunk, exactly as before this type existed.
    fn text(content: String) -> Self {
        Self {
            output: vec![RunnerOutput::Stdout(content.clone())],
            content,
            is_error: false,
        }
    }

    /// A shell call keeps its streams separate, so the Runtime can map them
    /// one-for-one to `command.output` Events, and fails on a non-zero exit.
    fn shell(outcome: shell::ShellOutcome) -> Self {
        let mut output = Vec::new();
        if !outcome.stdout.is_empty() {
            output.push(RunnerOutput::Stdout(outcome.stdout));
        }
        if !outcome.stderr.is_empty() {
            output.push(RunnerOutput::Stderr(outcome.stderr));
        }
        Self {
            content: outcome.content,
            is_error: outcome.is_error,
            output,
        }
    }

    fn failure(error: RunnerError) -> Self {
        let message = error.to_string();
        Self {
            content: format!("error: {message}"),
            is_error: true,
            output: vec![RunnerOutput::Stderr(message)],
        }
    }
}

impl RunnerEnvironment for LocalRunner {
    fn classify(&self, call: &ToolCallItem) -> ToolInteractionKind {
        match self.enabled_tool(&call.name) {
            // Memory retention only: this classification must never decide
            // whether a command may run.
            Some(LocalTool::Shell) => call
                .arguments
                .get("command")
                .and_then(serde_json::Value::as_str)
                .map_or(ToolInteractionKind::Generic, classify_shell_interaction),
            Some(tool) => tool.interaction(),
            None => ToolInteractionKind::Generic,
        }
    }

    async fn execute(
        &mut self,
        request: ToolExecutionRequest,
    ) -> Result<ToolExecutionResult, RunnerError> {
        self.active_runs.insert(request.run_id.clone());
        let arguments = &request.call.arguments;
        let execution = match self.enabled_tool(&request.call.name) {
            Some(LocalTool::ReadFile) => self.read_file(arguments).await.map(ToolRun::text),
            Some(LocalTool::WriteFile) => self.write_file(arguments).await.map(ToolRun::text),
            Some(LocalTool::ListDir) => self.list_dir(arguments).await.map(ToolRun::text),
            Some(LocalTool::Grep) => self.grep(arguments).await.map(ToolRun::text),
            Some(LocalTool::FindFiles) => self.find_files(arguments).await.map(ToolRun::text),
            Some(LocalTool::EditFiles) => self.edit_files(arguments).await.map(ToolRun::text),
            Some(LocalTool::DeleteFile) => self.delete_file(arguments).await.map(ToolRun::text),
            Some(LocalTool::Shell) => match self.canonical_root().await {
                Ok(root) => shell::run(&root, &self.policy.shell, arguments)
                    .await
                    .map(ToolRun::shell),
                Err(error) => Err(error),
            },
            // A disabled tool is reported exactly like one that does not exist:
            // the model was never told about it, and the reply must not reveal
            // that the capability could be switched on.
            None => Err(RunnerError::new(format!(
                "unknown local tool: {}",
                request.call.name
            ))),
        };
        self.active_runs.remove(&request.run_id);
        let ToolRun {
            content,
            is_error,
            output,
        } = execution.unwrap_or_else(ToolRun::failure);
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
    async fn edit_files_applies_a_batch_in_order_across_files() {
        let root = temp_root("edit-batch").await;
        tokio::fs::write(root.join("a.txt"), "alpha one\n")
            .await
            .expect("fixture is written");
        tokio::fs::write(root.join("b.txt"), "beta\n")
            .await
            .expect("fixture is written");
        let mut runner = LocalRunner::with_policy(&root, LocalRunnerPolicy::coding());
        let report = call(
            &mut runner,
            "edit_files",
            serde_json::json!({"edits": [
                {"path": "a.txt", "old_string": "alpha", "new_string": "ALPHA"},
                {"path": "b.txt", "old_string": "beta", "new_string": "BETA"},
                {"path": "a.txt", "old_string": "ALPHA one", "new_string": "done"}
            ]}),
        )
        .await;
        assert_eq!(
            report,
            "a.txt: 1 replacement\nb.txt: 1 replacement\na.txt: 1 replacement"
        );
        // The third edit matched text the first one produced, so edits to one
        // file must see each other before anything is written.
        assert_eq!(
            tokio::fs::read_to_string(root.join("a.txt"))
                .await
                .expect("file is readable"),
            "done\n"
        );
        assert_eq!(
            tokio::fs::read_to_string(root.join("b.txt"))
                .await
                .expect("file is readable"),
            "BETA\n"
        );
        tokio::fs::remove_dir_all(root).await.expect("cleanup");
    }

    #[tokio::test]
    async fn a_failed_edit_leaves_every_file_untouched() {
        let root = temp_root("edit-atomic").await;
        tokio::fs::write(root.join("a.txt"), "alpha\n")
            .await
            .expect("fixture is written");
        tokio::fs::write(root.join("b.txt"), "beta beta\n")
            .await
            .expect("fixture is written");
        let mut runner = LocalRunner::with_policy(&root, LocalRunnerPolicy::coding());
        // The first edit is valid and the second is ambiguous. Neither may land.
        let report = call(
            &mut runner,
            "edit_files",
            serde_json::json!({"edits": [
                {"path": "a.txt", "old_string": "alpha", "new_string": "ALPHA"},
                {"path": "b.txt", "old_string": "beta", "new_string": "BETA"}
            ]}),
        )
        .await;
        assert!(
            report.contains("matched 2 times in b.txt"),
            "unexpected report: {report}"
        );
        assert_eq!(
            tokio::fs::read_to_string(root.join("a.txt"))
                .await
                .expect("file is readable"),
            "alpha\n"
        );
        tokio::fs::remove_dir_all(root).await.expect("cleanup");
    }

    #[tokio::test]
    async fn a_missing_match_points_at_the_intended_line() {
        let root = temp_root("edit-near-miss").await;
        tokio::fs::write(root.join("a.txt"), "fn main() {\n    body();\n}\n")
            .await
            .expect("fixture is written");
        let mut runner = LocalRunner::with_policy(&root, LocalRunnerPolicy::coding());
        let report = call(
            &mut runner,
            "edit_files",
            serde_json::json!({"edits": [{
                "path": "a.txt",
                "old_string": "fn main() {\n  body();\n}",
                "new_string": "fn main() {}"
            }]}),
        )
        .await;
        assert!(
            report.contains("found no match in a.txt")
                && report.contains("first line appears at line 1"),
            "unexpected report: {report}"
        );
        tokio::fs::remove_dir_all(root).await.expect("cleanup");
    }

    #[tokio::test]
    async fn delete_file_is_confined_and_refuses_links() {
        let root = temp_root("delete").await;
        tokio::fs::write(root.join("gone.txt"), "bye")
            .await
            .expect("fixture is written");
        let mut runner = LocalRunner::with_policy(&root, LocalRunnerPolicy::coding());
        assert_eq!(
            call(
                &mut runner,
                "delete_file",
                serde_json::json!({"path": "gone.txt"})
            )
            .await,
            "deleted gone.txt"
        );
        assert!(!root.join("gone.txt").exists());
        assert_eq!(
            call(
                &mut runner,
                "delete_file",
                serde_json::json!({"path": "../outside.txt"})
            )
            .await,
            "error: local path must not contain '.', '..', or root components"
        );
        tokio::fs::remove_dir_all(root).await.expect("cleanup");
    }

    #[test]
    fn the_coding_policy_offers_exactly_one_edit_format() {
        let policy = LocalRunnerPolicy::coding();
        assert_eq!(policy.edit_formats(), vec![LocalTool::EditFiles]);
        assert_eq!(
            tool_definitions(&policy)
                .iter()
                .map(|definition| definition.name.clone())
                .collect::<Vec<_>>(),
            vec![
                "read_file",
                "list_dir",
                "grep",
                "find_files",
                "write_file",
                "edit_files",
                "delete_file"
            ]
        );
    }

    fn shell_policy() -> LocalRunnerPolicy {
        LocalRunnerPolicy::coding().with_tool(LocalTool::Shell)
    }

    async fn run_tool(
        runner: &mut LocalRunner,
        name: &str,
        arguments: serde_json::Value,
    ) -> ToolExecutionResult {
        runner
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
            .expect("execution is reported")
    }

    #[tokio::test]
    async fn shell_is_unavailable_unless_the_host_adds_it() {
        let root = temp_root("shell-off").await;
        let mut runner = LocalRunner::with_policy(&root, LocalRunnerPolicy::coding());
        assert_eq!(
            call(&mut runner, "shell", serde_json::json!({"command": "true"})).await,
            "error: unknown local tool: shell"
        );
        tokio::fs::remove_dir_all(root).await.expect("cleanup");
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn shell_reports_status_and_keeps_streams_separate() {
        let root = temp_root("shell-status").await;
        let mut runner = LocalRunner::with_policy(&root, shell_policy());
        let failed = run_tool(
            &mut runner,
            "shell",
            serde_json::json!({"command": "echo out; echo err >&2; exit 3"}),
        )
        .await;
        assert!(failed.result.is_error, "a non-zero exit is a tool error");
        assert_eq!(
            failed.result.content,
            vec![ContentBlock::text(
                "exit code 3\nstdout:\nout\n\nstderr:\nerr\n"
            )]
        );
        assert_eq!(
            failed.output,
            vec![
                RunnerOutput::Stdout("out\n".to_owned()),
                RunnerOutput::Stderr("err\n".to_owned())
            ]
        );

        let succeeded = run_tool(
            &mut runner,
            "shell",
            serde_json::json!({"command": "printf ok"}),
        )
        .await;
        assert!(!succeeded.result.is_error);
        assert_eq!(
            succeeded.result.content,
            vec![ContentBlock::text("exit code 0\nstdout:\nok")]
        );
        tokio::fs::remove_dir_all(root).await.expect("cleanup");
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn a_timeout_kills_every_process_the_command_started() {
        let root = temp_root("shell-timeout").await;
        let mut runner = LocalRunner::with_policy(&root, shell_policy());
        // `sleep` runs as a grandchild in the background; killing only `sh`
        // would leave it running for 30 seconds.
        let started = std::time::Instant::now();
        let result = run_tool(
            &mut runner,
            "shell",
            serde_json::json!({
                "command": "sleep 30 & echo $! > grandchild.pid; wait",
                "timeout_sec": 1
            }),
        )
        .await;
        // Without this bound a broken kill only makes the test slow: waiting
        // for `sh` would outlast the sleep, and the grandchild would then be
        // gone for the wrong reason.
        assert!(
            started.elapsed() < std::time::Duration::from_secs(10),
            "the timeout did not stop the call: took {:?}",
            started.elapsed()
        );
        assert!(result.result.is_error);
        let pid = tokio::fs::read_to_string(root.join("grandchild.pid"))
            .await
            .expect("the command recorded its background pid");
        let mut alive = true;
        for _ in 0..20 {
            alive = std::process::Command::new("kill")
                .args(["-0", pid.trim()])
                .status()
                .expect("kill runs")
                .success();
            if !alive {
                break;
            }
            tokio::time::sleep(std::time::Duration::from_millis(50)).await;
        }
        assert!(!alive, "the background grandchild {} survived", pid.trim());
        tokio::fs::remove_dir_all(root).await.expect("cleanup");
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn shell_runs_non_interactively() {
        let root = temp_root("shell-env").await;
        let mut runner = LocalRunner::with_policy(&root, shell_policy());
        assert_eq!(
            call(
                &mut runner,
                "shell",
                serde_json::json!({"command": "printf '%s %s %s' \"$PAGER\" \"$GIT_PAGER\" \"$GIT_TERMINAL_PROMPT\""})
            )
            .await,
            "exit code 0\nstdout:\ncat cat 0"
        );
        tokio::fs::remove_dir_all(root).await.expect("cleanup");
    }

    #[test]
    fn credentials_are_scrubbed_by_name_segment() {
        let policy = ShellPolicy {
            scrub_env: vec!["PROVIDER_SPECIAL".to_owned()],
            allow_env: vec!["GITHUB_TOKEN".to_owned()],
            ..ShellPolicy::default()
        };
        for scrubbed in [
            "OPENAI__API_KEY",
            "GLM_APIKEY",
            "DEEPSEEK_APIKEY",
            "AWS_SECRET_ACCESS_KEY",
            "CODEX_GITHUB_PERSONAL_ACCESS_TOKEN",
            "DB_PASSWORD",
            "PROVIDER_SPECIAL",
        ] {
            assert!(policy.scrubs(scrubbed), "{scrubbed} must be removed");
        }
        for kept in [
            "PATH",
            "HOME",
            "MONKEY",
            "KEYCHAIN_PATH",
            "SSH_AUTH_SOCK",
            "GITHUB_TOKEN",
        ] {
            assert!(!policy.scrubs(kept), "{kept} must be kept");
        }
    }

    #[test]
    fn the_policy_loads_from_toml_and_is_validated() {
        let policy: LocalRunnerPolicy = toml::from_str(
            r#"
            tools = ["read_file", "grep", "edit_files", "shell"]
            read_line_numbers = true
            max_grep_matches = 50

            [shell]
            default_timeout_secs = 30
            max_timeout_secs = 120
            scrub_env = ["MY_PROVIDER_KEY_VAR"]
            "#,
        )
        .expect("the example parses");
        policy.validate().expect("the example is consistent");
        assert!(policy.allows(LocalTool::Shell));
        assert_eq!(policy.max_grep_matches, 50);
        assert_eq!(policy.shell.default_timeout_secs, 30);
        // Unset fields keep their defaults.
        assert_eq!(policy.max_find_results, 500);

        let unknown = toml::from_str::<LocalRunnerPolicy>("tools = [\"read_file\"]\nshel = {}");
        assert!(
            unknown.is_err(),
            "a misspelled key must not be silently ignored"
        );

        let unknown_tool = toml::from_str::<LocalRunnerPolicy>("tools = [\"rm_rf\"]");
        assert!(
            unknown_tool.is_err(),
            "an unknown tool name must be rejected"
        );

        let inconsistent: LocalRunnerPolicy =
            toml::from_str("[shell]\ndefault_timeout_secs = 300\nmax_timeout_secs = 60")
                .expect("shape is valid");
        assert!(inconsistent.validate().is_err());
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
