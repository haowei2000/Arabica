use std::collections::HashSet;
use std::error::Error;
use std::fmt::{Display, Formatter};
use std::path::{Component, Path, PathBuf};
use std::process::Command as ProcessCommand;
use std::sync::{Arc, Mutex};
use std::time::Instant;

use serde::{Deserialize, Serialize};
use structure_model::{
    FinishReason, MessageItem, RuntimeItem, RuntimeResponse, RuntimeRole, RuntimeUsage,
    ShortMemoryItem, ToolCallItem,
};
use structure_protocol::{
    Command, CommandEnvelope, CommandId, Event, EventEnvelope, RunId, WorkspaceId,
};
use structure_provider::{ModelProvider, ModelRunRequest, ModelRunResult, ProviderError};
use structure_runner::LocalRunner;
use structure_runtime::{CoreRuntime, ShortMemoryPolicy};
use structure_session::SessionManager;

pub const TIER_B_REPORT_SCHEMA_VERSION: &str = "structure.short-memory.tier-b/v1";

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum TierBEvidenceLevel {
    Fixture,
    LiveApi,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct TierBProviderMetadata {
    pub api_type: String,
    pub model: String,
    pub base_url: String,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct ExpectedFile {
    pub path: String,
    pub exact_content: String,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct TierBTask {
    pub task_id: String,
    #[serde(default)]
    pub setup_prompts: Vec<String>,
    pub prompt: String,
    pub expected_files: Vec<ExpectedFile>,
    pub final_output_contains: Vec<String>,
    pub max_tool_calls: usize,
}

impl TierBTask {
    pub fn write_file(
        task_id: impl Into<String>,
        path: impl Into<String>,
        exact_content: impl Into<String>,
    ) -> Result<Self, TierBError> {
        let task_id = task_id.into();
        let path = path.into();
        let exact_content = exact_content.into();
        validate_relative_path(&path)?;
        if task_id.trim().is_empty() {
            return Err(TierBError::new("task_id must not be empty"));
        }
        let payload = serde_json::json!({
            "path": path,
            "content": exact_content,
        });
        Ok(Self {
            task_id,
            setup_prompts: Vec::new(),
            prompt: format!(
                "Use the write_file tool to complete this exact file task. Do not claim success before the tool succeeds.\n<file_task_json>{payload}</file_task_json>\nAfter the tool succeeds, reply with TASK_COMPLETE."
            ),
            expected_files: vec![ExpectedFile {
                path: payload["path"]
                    .as_str()
                    .expect("path was constructed from a string")
                    .to_owned(),
                exact_content: payload["content"]
                    .as_str()
                    .expect("content was constructed from a string")
                    .to_owned(),
            }],
            final_output_contains: vec!["TASK_COMPLETE".to_owned()],
            max_tool_calls: 2,
        })
    }

    pub fn recall_write_file(
        task_id: impl Into<String>,
        path: impl Into<String>,
        evidence_key: impl Into<String>,
        exact_content: impl Into<String>,
    ) -> Result<Self, TierBError> {
        let task_id = task_id.into();
        let path = path.into();
        let evidence_key = evidence_key.into();
        let exact_content = exact_content.into();
        validate_relative_path(&path)?;
        if task_id.trim().is_empty() || evidence_key.trim().is_empty() {
            return Err(TierBError::new(
                "task_id and evidence_key must not be empty",
            ));
        }
        let evidence = serde_json::json!({
            "key": evidence_key,
            "value": exact_content,
        });
        let instruction = serde_json::json!({
            "key": evidence["key"],
            "path": path,
        });
        Ok(Self {
            task_id,
            setup_prompts: vec![format!(
                "Remember this evidence for the next task and reply with MEMORY_STORED.\n<memory_evidence_json>{evidence}</memory_evidence_json>"
            )],
            prompt: format!(
                "Retrieve the evidence identified by key from short memory, then use write_file to write its exact value. Do not claim success before the tool succeeds.\n<memory_write_task_json>{instruction}</memory_write_task_json>\nAfter the tool succeeds, reply with TASK_COMPLETE."
            ),
            expected_files: vec![ExpectedFile {
                path: instruction["path"]
                    .as_str()
                    .expect("path was constructed from a string")
                    .to_owned(),
                exact_content: evidence["value"]
                    .as_str()
                    .expect("content was constructed from a string")
                    .to_owned(),
            }],
            final_output_contains: vec!["TASK_COMPLETE".to_owned()],
            max_tool_calls: 2,
        })
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct TierBSuiteConfig {
    pub suite_id: String,
    pub evidence_level: TierBEvidenceLevel,
    pub provider: TierBProviderMetadata,
    pub runner_root: PathBuf,
    pub short_memory_policy: ShortMemoryPolicy,
    pub tasks: Vec<TierBTask>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct ProviderCallObservation {
    pub call_index: usize,
    pub run_id: RunId,
    pub short_memory_entries: usize,
    pub short_memory_item_bytes: usize,
    pub long_memory_entries: usize,
    pub continuation_items: usize,
    pub tool_definitions: usize,
    pub response_items: usize,
    pub finish_reason: Option<FinishReason>,
    pub input_tokens: u64,
    pub output_tokens: u64,
    pub cached_input_tokens: u64,
    pub latency_ms: u64,
    pub succeeded: bool,
    pub error: Option<String>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct FileOracleResult {
    pub path: String,
    pub expected_chars: usize,
    pub observed_chars: Option<usize>,
    pub observed_fingerprint: Option<String>,
    pub content_matches: bool,
    pub error: Option<String>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct TierBTaskChecks {
    pub terminal_success: bool,
    pub expected_files_match: bool,
    pub final_output_markers_match: bool,
    pub no_tool_errors: bool,
    pub within_tool_call_limit: bool,
}

impl TierBTaskChecks {
    fn passed(&self) -> bool {
        self.terminal_success
            && self.expected_files_match
            && self.final_output_markers_match
            && self.no_tool_errors
            && self.within_tool_call_limit
    }

    fn score_bps(&self) -> u64 {
        let passed = [
            self.terminal_success,
            self.expected_files_match,
            self.final_output_markers_match,
            self.no_tool_errors,
            self.within_tool_call_limit,
        ]
        .into_iter()
        .filter(|passed| *passed)
        .count();
        passed as u64 * 10_000 / 5
    }
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct TierBRun {
    pub task: TierBTask,
    pub session_id: String,
    pub run_id: Option<RunId>,
    pub passed: bool,
    pub task_score_bps: u64,
    pub checks: TierBTaskChecks,
    pub final_output: Option<String>,
    pub file_oracles: Vec<FileOracleResult>,
    pub provider_calls: Vec<ProviderCallObservation>,
    pub input_tokens: u64,
    pub output_tokens: u64,
    pub cached_input_tokens: u64,
    pub provider_latency_ms: u64,
    pub elapsed_ms: u64,
    pub tool_call_count: usize,
    pub tool_error_count: usize,
    pub redundant_tool_call_count: usize,
    pub events: Vec<EventEnvelope>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct TierBAggregate {
    pub run_count: usize,
    pub passed_run_count: usize,
    pub pass_rate_bps: u64,
    pub total_provider_calls: usize,
    pub total_input_tokens: u64,
    pub total_output_tokens: u64,
    pub total_cached_input_tokens: u64,
    pub total_provider_latency_ms: u64,
    pub total_tool_calls: usize,
    pub total_redundant_tool_calls: usize,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct TierBReport {
    pub schema_version: String,
    pub suite_id: String,
    pub evidence_level: TierBEvidenceLevel,
    pub provider: TierBProviderMetadata,
    pub runner_root: PathBuf,
    pub short_memory_policy: ShortMemoryPolicy,
    pub environment: TierBEnvironment,
    pub runs: Vec<TierBRun>,
    pub aggregate: TierBAggregate,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct TierBEnvironment {
    pub target_arch: String,
    pub target_os: String,
    pub rustc_version: Option<String>,
    pub git_revision: Option<String>,
    pub git_worktree_dirty: Option<bool>,
}

#[derive(Clone)]
struct ProviderRecorder {
    observations: Arc<Mutex<Vec<ProviderCallObservation>>>,
}

impl ProviderRecorder {
    fn new() -> Self {
        Self {
            observations: Arc::new(Mutex::new(Vec::new())),
        }
    }

    fn len(&self) -> usize {
        self.observations
            .lock()
            .expect("provider recorder lock is not poisoned")
            .len()
    }

    fn from(&self, start: usize) -> Vec<ProviderCallObservation> {
        self.observations
            .lock()
            .expect("provider recorder lock is not poisoned")[start..]
            .to_vec()
    }
}

struct RecordingProvider<P> {
    inner: P,
    recorder: ProviderRecorder,
}

impl<P> RecordingProvider<P> {
    fn new(inner: P, recorder: ProviderRecorder) -> Self {
        Self { inner, recorder }
    }
}

impl<P: ModelProvider> ModelProvider for RecordingProvider<P> {
    async fn complete(
        &mut self,
        request: ModelRunRequest,
    ) -> Result<ModelRunResult, ProviderError> {
        let short_memory_items: Vec<_> = request
            .short_memory
            .iter()
            .map(|entry| &entry.item)
            .collect();
        let short_memory_item_bytes =
            serde_json::to_vec(&short_memory_items).map_or(0, |encoded| encoded.len());
        let started = Instant::now();
        let result = self.inner.complete(request.clone()).await;
        let latency_ms = saturating_u64(started.elapsed().as_millis());
        let (response_items, finish_reason, usage, error) = match &result {
            Ok(result) => {
                let response = result.response.as_ref();
                (
                    response.map_or(0, |response| response.items.len()),
                    response.and_then(|response| response.finish_reason.clone()),
                    response.map_or_else(RuntimeUsage::default, |response| response.usage.clone()),
                    None,
                )
            }
            Err(error) => (0, None, RuntimeUsage::default(), Some(error.to_string())),
        };
        let mut observations = self
            .recorder
            .observations
            .lock()
            .expect("provider recorder lock is not poisoned");
        let call_index = observations.len() + 1;
        observations.push(ProviderCallObservation {
            call_index,
            run_id: request.run_id,
            short_memory_entries: request.short_memory.len(),
            short_memory_item_bytes,
            long_memory_entries: request.long_memory.len(),
            continuation_items: request.continuation.len(),
            tool_definitions: request.tools.len(),
            response_items,
            finish_reason,
            input_tokens: usage.input_tokens,
            output_tokens: usage.output_tokens,
            cached_input_tokens: usage.cached_input_tokens,
            latency_ms,
            succeeded: error.is_none(),
            error,
        });
        result
    }

    async fn cancel(&mut self, run_id: &RunId) -> Result<bool, ProviderError> {
        self.inner.cancel(run_id).await
    }
}

pub async fn run_tier_b_suite<P: ModelProvider>(
    config: TierBSuiteConfig,
    provider: P,
) -> Result<TierBReport, TierBError> {
    validate_suite(&config)?;
    tokio::fs::create_dir_all(&config.runner_root)
        .await
        .map_err(|error| TierBError::new(format!("runner root creation failed: {error}")))?;
    let runner_root = tokio::fs::canonicalize(&config.runner_root)
        .await
        .map_err(|error| TierBError::new(format!("runner root unavailable: {error}")))?;
    for task in &config.tasks {
        prepare_task_directories(&runner_root, task).await?;
    }

    let recorder = ProviderRecorder::new();
    let runtime = CoreRuntime::with_short_memory_policy(
        RecordingProvider::new(provider, recorder.clone()),
        LocalRunner::new(&runner_root),
        config.short_memory_policy.clone(),
    );
    let mut manager = SessionManager::new(runtime);
    let mut runs = Vec::with_capacity(config.tasks.len());

    for (index, task) in config.tasks.iter().cloned().enumerate() {
        let create_events = manager
            .handle(CommandEnvelope::new(
                CommandId::new(format!("tier-b-create-{index:04}")),
                None,
                Command::SessionCreate {
                    workspace_id: WorkspaceId::new(format!("tier-b-workspace-{index:04}")),
                },
            ))
            .await
            .map_err(|error| TierBError::new(format!("session creation failed: {error}")))?;
        let session_id = create_events
            .first()
            .map(|event| event.session_id.clone())
            .ok_or_else(|| TierBError::new("session creation returned no event"))?;
        let provider_start = recorder.len();
        let started = Instant::now();
        let mut events = create_events;
        for (setup_index, prompt) in task.setup_prompts.iter().enumerate() {
            let setup_events = manager
                .handle(CommandEnvelope::new(
                    CommandId::new(format!("tier-b-setup-{index:04}-{setup_index:04}")),
                    Some(session_id.clone()),
                    Command::MessageSend {
                        content: prompt.clone(),
                    },
                ))
                .await
                .map_err(|error| TierBError::new(format!("task setup failed: {error}")))?;
            events.extend(setup_events);
        }
        let task_events = manager
            .handle(CommandEnvelope::new(
                CommandId::new(format!("tier-b-task-{index:04}")),
                Some(session_id.clone()),
                Command::MessageSend {
                    content: task.prompt.clone(),
                },
            ))
            .await
            .map_err(|error| TierBError::new(format!("task dispatch failed: {error}")))?;
        let elapsed_ms = saturating_u64(started.elapsed().as_millis());
        let provider_calls = recorder.from(provider_start);
        events.extend(task_events);
        runs.push(
            score_run(
                &runner_root,
                task,
                session_id.to_string(),
                provider_calls,
                elapsed_ms,
                events,
            )
            .await,
        );
    }

    let aggregate = aggregate(&runs);
    Ok(TierBReport {
        schema_version: TIER_B_REPORT_SCHEMA_VERSION.to_owned(),
        suite_id: config.suite_id,
        evidence_level: config.evidence_level,
        provider: config.provider,
        runner_root,
        short_memory_policy: config.short_memory_policy,
        environment: TierBEnvironment {
            target_arch: std::env::consts::ARCH.to_owned(),
            target_os: std::env::consts::OS.to_owned(),
            rustc_version: command_output("rustc", &["--version"]),
            git_revision: command_output("git", &["rev-parse", "HEAD"]),
            git_worktree_dirty: command_output("git", &["status", "--porcelain"])
                .map(|output| !output.is_empty()),
        },
        runs,
        aggregate,
    })
}

async fn score_run(
    runner_root: &Path,
    task: TierBTask,
    session_id: String,
    provider_calls: Vec<ProviderCallObservation>,
    elapsed_ms: u64,
    events: Vec<EventEnvelope>,
) -> TierBRun {
    let run_id = events.iter().rev().find_map(|event| event.run_id.clone());
    let is_evaluated_run = |event: &EventEnvelope| event.run_id == run_id;
    let final_output = events
        .iter()
        .rev()
        .filter(|event| is_evaluated_run(event))
        .find_map(|event| match &event.event {
            Event::RunCompleted { output } => output.clone(),
            _ => None,
        });
    let terminal_success = events
        .iter()
        .filter(|event| is_evaluated_run(event))
        .any(|event| matches!(event.event, Event::RunCompleted { .. }))
        && !events
            .iter()
            .filter(|event| is_evaluated_run(event))
            .any(|event| matches!(event.event, Event::RunFailed { .. }));
    let tool_calls: Vec<_> = events
        .iter()
        .filter(|event| is_evaluated_run(event))
        .filter_map(|event| match &event.event {
            Event::ToolCallRequested {
                name, arguments, ..
            } => Some((name.clone(), arguments.clone())),
            _ => None,
        })
        .collect();
    let tool_error_count = events
        .iter()
        .filter(|event| is_evaluated_run(event))
        .filter(|event| matches!(event.event, Event::ToolCallCompleted { is_error: true, .. }))
        .count();
    let mut seen_calls = HashSet::new();
    let redundant_tool_call_count = tool_calls
        .iter()
        .filter(|(name, arguments)| {
            !seen_calls.insert(format!(
                "{name}:{}",
                serde_json::to_string(arguments).unwrap_or_default()
            ))
        })
        .count();
    let file_oracles = evaluate_files(runner_root, &task.expected_files).await;
    let expected_files_match = file_oracles.iter().all(|oracle| oracle.content_matches);
    let final_output_markers_match = task.final_output_contains.iter().all(|marker| {
        final_output
            .as_ref()
            .is_some_and(|output| output.contains(marker))
    });
    let checks = TierBTaskChecks {
        terminal_success,
        expected_files_match,
        final_output_markers_match,
        no_tool_errors: tool_error_count == 0,
        within_tool_call_limit: tool_calls.len() <= task.max_tool_calls,
    };
    let input_tokens = provider_calls.iter().map(|call| call.input_tokens).sum();
    let output_tokens = provider_calls.iter().map(|call| call.output_tokens).sum();
    let cached_input_tokens = provider_calls
        .iter()
        .map(|call| call.cached_input_tokens)
        .sum();
    let provider_latency_ms = provider_calls.iter().map(|call| call.latency_ms).sum();

    TierBRun {
        passed: checks.passed(),
        task_score_bps: checks.score_bps(),
        task,
        session_id,
        run_id,
        checks,
        final_output,
        file_oracles,
        provider_calls,
        input_tokens,
        output_tokens,
        cached_input_tokens,
        provider_latency_ms,
        elapsed_ms,
        tool_call_count: tool_calls.len(),
        tool_error_count,
        redundant_tool_call_count,
        events,
    }
}

async fn evaluate_files(root: &Path, expected: &[ExpectedFile]) -> Vec<FileOracleResult> {
    let mut results = Vec::with_capacity(expected.len());
    for oracle in expected {
        let target = root.join(&oracle.path);
        match tokio::fs::read_to_string(&target).await {
            Ok(content) => results.push(FileOracleResult {
                path: oracle.path.clone(),
                expected_chars: oracle.exact_content.chars().count(),
                observed_chars: Some(content.chars().count()),
                observed_fingerprint: Some(stable_fingerprint(&content)),
                content_matches: content == oracle.exact_content,
                error: None,
            }),
            Err(error) => results.push(FileOracleResult {
                path: oracle.path.clone(),
                expected_chars: oracle.exact_content.chars().count(),
                observed_chars: None,
                observed_fingerprint: None,
                content_matches: false,
                error: Some(error.to_string()),
            }),
        }
    }
    results
}

fn aggregate(runs: &[TierBRun]) -> TierBAggregate {
    let passed_run_count = runs.iter().filter(|run| run.passed).count();
    TierBAggregate {
        run_count: runs.len(),
        passed_run_count,
        pass_rate_bps: if runs.is_empty() {
            0
        } else {
            passed_run_count as u64 * 10_000 / runs.len() as u64
        },
        total_provider_calls: runs.iter().map(|run| run.provider_calls.len()).sum(),
        total_input_tokens: runs.iter().map(|run| run.input_tokens).sum(),
        total_output_tokens: runs.iter().map(|run| run.output_tokens).sum(),
        total_cached_input_tokens: runs.iter().map(|run| run.cached_input_tokens).sum(),
        total_provider_latency_ms: runs.iter().map(|run| run.provider_latency_ms).sum(),
        total_tool_calls: runs.iter().map(|run| run.tool_call_count).sum(),
        total_redundant_tool_calls: runs.iter().map(|run| run.redundant_tool_call_count).sum(),
    }
}

fn validate_suite(config: &TierBSuiteConfig) -> Result<(), TierBError> {
    if config.suite_id.trim().is_empty() {
        return Err(TierBError::new("suite_id must not be empty"));
    }
    if config.tasks.is_empty() {
        return Err(TierBError::new("Tier-B suite requires at least one task"));
    }
    for task in &config.tasks {
        if task.max_tool_calls == 0 {
            return Err(TierBError::new(format!(
                "task {} max_tool_calls must be greater than zero",
                task.task_id
            )));
        }
        for file in &task.expected_files {
            validate_relative_path(&file.path)?;
        }
    }
    Ok(())
}

async fn prepare_task_directories(root: &Path, task: &TierBTask) -> Result<(), TierBError> {
    for expected in &task.expected_files {
        let target = root.join(&expected.path);
        if tokio::fs::try_exists(&target)
            .await
            .map_err(|error| TierBError::new(format!("expected file preflight failed: {error}")))?
        {
            return Err(TierBError::new(format!(
                "expected file already exists and would contaminate the oracle: {}",
                target.display()
            )));
        }
        let parent = target
            .parent()
            .ok_or_else(|| TierBError::new("expected file has no parent"))?;
        tokio::fs::create_dir_all(parent)
            .await
            .map_err(|error| TierBError::new(format!("task directory creation failed: {error}")))?;
    }
    Ok(())
}

fn validate_relative_path(path: &str) -> Result<(), TierBError> {
    let path = Path::new(path);
    if path.as_os_str().is_empty()
        || path.is_absolute()
        || path
            .components()
            .any(|component| !matches!(component, Component::Normal(_)))
    {
        return Err(TierBError::new(
            "Tier-B file paths must be non-empty, relative, and traversal-free",
        ));
    }
    Ok(())
}

fn stable_fingerprint(value: &str) -> String {
    let hash = value
        .as_bytes()
        .iter()
        .fold(0xcbf2_9ce4_8422_2325_u64, |hash, byte| {
            (hash ^ u64::from(*byte)).wrapping_mul(0x0000_0100_0000_01b3)
        });
    format!("fnv1a64:{hash:016x}")
}

fn saturating_u64(value: u128) -> u64 {
    u64::try_from(value).unwrap_or(u64::MAX)
}

fn command_output(program: &str, arguments: &[&str]) -> Option<String> {
    let output = ProcessCommand::new(program).args(arguments).output().ok()?;
    output
        .status
        .success()
        .then(|| String::from_utf8_lossy(&output.stdout).trim().to_owned())
}

#[derive(Debug, Default)]
pub struct FixtureFileProvider {
    next_call_id: usize,
}

impl ModelProvider for FixtureFileProvider {
    async fn complete(
        &mut self,
        request: ModelRunRequest,
    ) -> Result<ModelRunResult, ProviderError> {
        let input_tokens = request.input.chars().count().div_ceil(4) as u64;
        if request
            .continuation
            .iter()
            .any(|item| matches!(item, RuntimeItem::ToolResult(_)))
        {
            let output = "TASK_COMPLETE".to_owned();
            return Ok(ModelRunResult {
                final_output: Some(output.clone()),
                response: Some(RuntimeResponse {
                    items: vec![RuntimeItem::Message(MessageItem::text(
                        RuntimeRole::Assistant,
                        output,
                    ))],
                    finish_reason: Some(FinishReason::Stop),
                    usage: RuntimeUsage {
                        input_tokens,
                        output_tokens: 2,
                        cached_input_tokens: 0,
                    },
                }),
            });
        }
        if tagged_payload(&request.input, "memory_evidence_json").is_ok() {
            let output = "MEMORY_STORED".to_owned();
            return Ok(ModelRunResult {
                final_output: Some(output.clone()),
                response: Some(RuntimeResponse {
                    items: vec![RuntimeItem::Message(MessageItem::text(
                        RuntimeRole::Assistant,
                        output,
                    ))],
                    finish_reason: Some(FinishReason::Stop),
                    usage: RuntimeUsage {
                        input_tokens,
                        output_tokens: 2,
                        cached_input_tokens: 0,
                    },
                }),
            });
        }
        let payload =
            if let Ok(instruction) = tagged_payload(&request.input, "memory_write_task_json") {
                recall_file_payload(&request, &instruction)?
            } else {
                tagged_payload(&request.input, "file_task_json")?
            };
        self.next_call_id += 1;
        let call = ToolCallItem {
            id: None,
            call_id: format!("fixture-call-{}", self.next_call_id),
            name: "write_file".to_owned(),
            arguments: payload,
            provider_state: None,
        };
        Ok(ModelRunResult {
            final_output: None,
            response: Some(RuntimeResponse {
                items: vec![RuntimeItem::ToolCall(call)],
                finish_reason: Some(FinishReason::ToolCalls),
                usage: RuntimeUsage {
                    input_tokens,
                    output_tokens: 8,
                    cached_input_tokens: 0,
                },
            }),
        })
    }

    async fn cancel(&mut self, _run_id: &RunId) -> Result<bool, ProviderError> {
        Ok(false)
    }
}

fn tagged_payload(input: &str, tag: &str) -> Result<serde_json::Value, ProviderError> {
    let open = format!("<{tag}>");
    let close = format!("</{tag}>");
    let start = input
        .find(&open)
        .map(|index| index + open.len())
        .ok_or_else(|| ProviderError::new(format!("fixture task is missing {tag}")))?;
    let end = input[start..]
        .find(&close)
        .map(|index| start + index)
        .ok_or_else(|| ProviderError::new(format!("fixture task has no closing {tag}")))?;
    serde_json::from_str(&input[start..end])
        .map_err(|error| ProviderError::new(format!("invalid fixture file task: {error}")))
}

fn recall_file_payload(
    request: &ModelRunRequest,
    instruction: &serde_json::Value,
) -> Result<serde_json::Value, ProviderError> {
    let key = instruction["key"]
        .as_str()
        .ok_or_else(|| ProviderError::new("memory task key must be a string"))?;
    let path = instruction["path"]
        .as_str()
        .ok_or_else(|| ProviderError::new("memory task path must be a string"))?;
    let value = request
        .short_memory
        .iter()
        .rev()
        .filter_map(|entry| match &entry.item {
            ShortMemoryItem::UserMessage { content } => {
                tagged_payload(content, "memory_evidence_json").ok()
            }
            _ => None,
        })
        .find(|evidence| evidence["key"].as_str() == Some(key))
        .and_then(|evidence| evidence["value"].as_str().map(str::to_owned))
        .ok_or_else(|| ProviderError::new(format!("evidence key {key} was not recalled")))?;
    Ok(serde_json::json!({"path": path, "content": value}))
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct TierBError {
    message: String,
}

impl TierBError {
    fn new(message: impl Into<String>) -> Self {
        Self {
            message: message.into(),
        }
    }
}

impl Display for TierBError {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        self.message.fmt(formatter)
    }
}

impl Error for TierBError {}

#[cfg(test)]
mod tests {
    use std::time::{SystemTime, UNIX_EPOCH};

    use super::*;

    fn unique_root(name: &str) -> PathBuf {
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("clock is valid")
            .as_nanos();
        std::env::temp_dir().join(format!("structure-tier-b-{name}-{nonce}"))
    }

    #[tokio::test]
    async fn fixture_runs_the_real_session_runtime_and_local_runner_loop() {
        let root = unique_root("success");
        let task = TierBTask::recall_write_file(
            "write-file-1",
            "run-0001/result.txt",
            "evidence-1",
            "written by Tier-B\n",
        )
        .expect("task is valid");
        let report = run_tier_b_suite(
            TierBSuiteConfig {
                suite_id: "fixture-suite".to_owned(),
                evidence_level: TierBEvidenceLevel::Fixture,
                provider: TierBProviderMetadata {
                    api_type: "fixture".to_owned(),
                    model: "fixture-file-provider".to_owned(),
                    base_url: "fixture://local".to_owned(),
                },
                runner_root: root.clone(),
                short_memory_policy: ShortMemoryPolicy::default(),
                tasks: vec![task],
            },
            FixtureFileProvider::default(),
        )
        .await
        .expect("fixture suite runs");

        assert_eq!(report.schema_version, TIER_B_REPORT_SCHEMA_VERSION);
        assert_eq!(report.evidence_level, TierBEvidenceLevel::Fixture);
        assert_eq!(report.aggregate.passed_run_count, 1);
        assert_eq!(report.aggregate.total_provider_calls, 3);
        assert_eq!(report.aggregate.total_tool_calls, 1);
        assert!(report.runs[0].passed);
        assert_eq!(report.runs[0].task_score_bps, 10_000);
        assert!(report.runs[0].file_oracles[0].content_matches);
        assert!(
            report.runs[0]
                .provider_calls
                .iter()
                .any(|call| call.short_memory_entries > 0)
        );
        assert!(
            report.runs[0]
                .events
                .iter()
                .any(|event| matches!(event.event, Event::ToolCallCompleted { .. }))
        );
        tokio::fs::remove_dir_all(root)
            .await
            .expect("fixture root is removed");
    }

    #[tokio::test]
    async fn missing_file_cannot_pass_only_from_a_completion_claim() {
        #[derive(Debug, Default)]
        struct ClaimOnlyProvider;

        impl ModelProvider for ClaimOnlyProvider {
            async fn complete(
                &mut self,
                _request: ModelRunRequest,
            ) -> Result<ModelRunResult, ProviderError> {
                Ok(ModelRunResult {
                    final_output: Some("TASK_COMPLETE".to_owned()),
                    response: Some(RuntimeResponse {
                        items: vec![RuntimeItem::Message(MessageItem::text(
                            RuntimeRole::Assistant,
                            "TASK_COMPLETE",
                        ))],
                        finish_reason: Some(FinishReason::Stop),
                        usage: RuntimeUsage::default(),
                    }),
                })
            }

            async fn cancel(&mut self, _run_id: &RunId) -> Result<bool, ProviderError> {
                Ok(false)
            }
        }

        let root = unique_root("claim-only");
        let report = run_tier_b_suite(
            TierBSuiteConfig {
                suite_id: "claim-only-suite".to_owned(),
                evidence_level: TierBEvidenceLevel::Fixture,
                provider: TierBProviderMetadata {
                    api_type: "fixture".to_owned(),
                    model: "claim-only".to_owned(),
                    base_url: "fixture://local".to_owned(),
                },
                runner_root: root.clone(),
                short_memory_policy: ShortMemoryPolicy::default(),
                tasks: vec![
                    TierBTask::write_file("write-file-claim", "run-0001/missing.txt", "must exist")
                        .expect("task is valid"),
                ],
            },
            ClaimOnlyProvider,
        )
        .await
        .expect("claim-only suite runs");

        assert!(!report.runs[0].passed);
        assert!(!report.runs[0].checks.expected_files_match);
        assert!(report.runs[0].checks.final_output_markers_match);
        tokio::fs::remove_dir_all(root)
            .await
            .expect("fixture root is removed");
    }
}
