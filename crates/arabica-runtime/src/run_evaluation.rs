//! Immutable run evidence, acceptance scorers, and observed policy comparisons.
use std::collections::{BTreeMap, BTreeSet};

use arabica_protocol::{Event, EventEnvelope, ModelCallOutcome, RunId, SessionId, WorkspaceId};
use regex::Regex;
use serde::{Deserialize, Serialize};

use crate::{EvaluationError, EvaluationPluginMetadata};

const MAX_OUTPUT_BYTES: usize = 64 * 1024;

#[derive(Clone, Debug, Default, Deserialize, Eq, PartialEq, Serialize)]
pub struct RunTiming {
    pub queue_ms: Option<u64>,
    /// RunStarted through the first terminal event, including approval waits.
    pub wall_ms: Option<u64>,
    pub approval_wait_ms: Option<u64>,
    /// Wall time minus the union of approval-wait intervals.
    pub active_ms: Option<u64>,
    pub first_response_ms: Option<u64>,
    pub model_call_ms_total: u64,
    pub model_calls: usize,
    /// Sum of actual runner durations, not parallel wall-clock time.
    pub runner_ms_total: Option<u64>,
    pub runner_calls: usize,
    pub tool_calls: usize,
    pub incomplete_tool_calls: usize,
    pub input_tokens: u64,
    pub output_tokens: u64,
    pub usage_reported_calls: usize,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct ObservedTool {
    pub call_id: String,
    pub name: String,
    pub is_error: Option<bool>,
    pub runner_outcome: Option<ModelCallOutcome>,
}

/// A bounded projection of already-persisted facts. No live file reads, prompts,
/// command arguments, provider envelopes, or raw tool output enter this snapshot.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct RunEvidenceSnapshot {
    pub schema_version: u64,
    pub workspace_id: WorkspaceId,
    pub session_id: SessionId,
    pub run_id: RunId,
    pub terminal_sequence: u64,
    pub terminal_status: String,
    #[serde(default)]
    pub workload_fingerprint: Option<String>,
    #[serde(default)]
    pub fixture_fingerprint: Option<String>,
    pub policy_fingerprints: BTreeSet<String>,
    pub model_registry_fingerprints: BTreeSet<String>,
    pub timing: RunTiming,
    pub final_output: Option<String>,
    pub output_omitted: bool,
    pub tools: Vec<ObservedTool>,
    #[serde(default)]
    pub artifacts: BTreeMap<String, ArtifactEvidence>,
    pub fingerprint: String,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(tag = "status", rename_all = "snake_case")]
pub enum ArtifactEvidence {
    Captured {
        content: String,
    },
    TestResult {
        exit_code: Option<i32>,
        passed: bool,
        output: String,
        output_truncated: bool,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        error: Option<String>,
    },
    ModifiedFiles {
        paths: Vec<String>,
    },
    Missing,
    Unavailable,
}

impl RunEvidenceSnapshot {
    /// Attach controlled artifact evidence before freezing the snapshot.
    /// Reject excessive evidence rather than scoring a silently truncated file.
    pub fn with_artifacts(
        mut self,
        artifacts: BTreeMap<String, ArtifactEvidence>,
    ) -> Result<Self, EvaluationError> {
        let bytes: usize = artifacts
            .iter()
            .map(|(path, artifact)| {
                path.len()
                    + match artifact {
                        ArtifactEvidence::Captured { content } => content.len(),
                        ArtifactEvidence::TestResult { output, .. } => output.len(),
                        ArtifactEvidence::ModifiedFiles { paths } => {
                            paths.iter().map(|p| p.len()).sum::<usize>()
                        }
                        _ => 0,
                    }
            })
            .sum();
        if artifacts.len() > 128 || bytes > MAX_OUTPUT_BYTES {
            return Err(EvaluationError("artifact snapshot exceeds bounds".into()));
        }
        self.artifacts = artifacts;
        self.fingerprint.clear();
        self.fingerprint = crate::context::fingerprint(&self);
        Ok(self)
    }
}

fn duration(start: Option<u64>, end: Option<u64>) -> Option<u64> {
    end?.checked_sub(start?)
}

fn interval_union(intervals: &mut [(u64, u64)]) -> u64 {
    intervals.sort_unstable();
    let mut total = 0_u64;
    let mut previous = None;
    for &(start, end) in intervals.iter() {
        match previous {
            Some((left, right)) if start <= right => previous = Some((left, right.max(end))),
            Some((left, right)) => {
                total = total.saturating_add(right - left);
                previous = Some((start, end));
            }
            None => previous = Some((start, end)),
        }
    }
    if let Some((left, right)) = previous {
        total = total.saturating_add(right - left);
    }
    total
}

/// Build snapshots only for terminal runs, stopping at their first terminal
/// event. Later conversation turns cannot change the evidence being graded.
pub fn run_evidence_snapshots(history: &[EventEnvelope]) -> Vec<RunEvidenceSnapshot> {
    let mut runs = BTreeMap::new();
    for event in history {
        if let Some(run) = &event.run_id {
            runs.entry((
                event.workspace_id.clone(),
                event.session_id.clone(),
                run.clone(),
            ))
            .or_insert_with(Vec::new)
            .push(event);
        }
    }
    runs.into_iter()
        .filter_map(|((workspace_id, session_id, run_id), mut events)| {
            events.sort_by_key(|event| event.sequence);
            let terminal_index = events.iter().position(|event| {
                matches!(
                    event.event,
                    Event::RunCompleted { .. } | Event::RunFailed { .. } | Event::RunCancelled
                )
            })?;
            let events = &events[..=terminal_index];
            let terminal = events.last()?;
            let end = terminal.occurred_at_ms;
            let mut messages = Vec::new();
            let mut scheduled = None;
            let mut started = None;
            let mut first_response = None;
            let mut waits = BTreeMap::new();
            let mut intervals = Vec::new();
            let mut timing = RunTiming::default();
            let mut tools = BTreeMap::<String, ObservedTool>::new();
            let mut policy_fingerprints = BTreeSet::new();
            let mut model_registry_fingerprints = BTreeSet::new();
            let mut runner_total = 0_u64;
            let mut invalid_clock = false;
            for pair in events.windows(2) {
                invalid_clock |= pair[1].occurred_at_ms < pair[0].occurred_at_ms;
            }
            for envelope in events {
                match &envelope.event {
                    Event::MessageAccepted { content } => {
                        messages.push(content);
                    }
                    Event::RunScheduled => {
                        scheduled.get_or_insert(envelope.occurred_at_ms);
                    }
                    Event::RunStarted => {
                        started.get_or_insert(envelope.occurred_at_ms);
                    }
                    Event::ModelResponseItem { .. } => {
                        first_response.get_or_insert(envelope.occurred_at_ms);
                    }
                    Event::ModelRouteSelected {
                        policy_fingerprint,
                        model_registry_snapshot,
                        ..
                    } => {
                        policy_fingerprints.insert(policy_fingerprint.clone());
                        model_registry_fingerprints
                            .insert(crate::context::fingerprint(model_registry_snapshot));
                    }
                    Event::ModelCallObserved {
                        elapsed_ms, usage, ..
                    } => {
                        timing.model_calls += 1;
                        timing.model_call_ms_total =
                            timing.model_call_ms_total.saturating_add(*elapsed_ms);
                        if let Some(usage) = usage {
                            timing.usage_reported_calls += 1;
                            timing.input_tokens =
                                timing.input_tokens.saturating_add(usage.input_tokens);
                            timing.output_tokens =
                                timing.output_tokens.saturating_add(usage.output_tokens);
                        }
                    }
                    Event::ToolCallRequested { call_id, name, .. } => {
                        tools.insert(
                            call_id.clone(),
                            ObservedTool {
                                call_id: call_id.clone(),
                                name: name.clone(),
                                is_error: None,
                                runner_outcome: None,
                            },
                        );
                    }
                    Event::ToolCallPermissionRequested { call_id } => {
                        waits
                            .entry(call_id.clone())
                            .or_insert(envelope.occurred_at_ms);
                    }
                    Event::ToolCallPermissionResolved { call_id, .. } => {
                        if let Some(start) = waits.remove(call_id) {
                            intervals.push((start, envelope.occurred_at_ms.max(start)));
                        }
                    }
                    Event::ToolExecutionObserved {
                        call_id,
                        elapsed_ms,
                        outcome,
                    } => {
                        timing.runner_calls += 1;
                        runner_total = runner_total.saturating_add(*elapsed_ms);
                        if let Some(tool) = tools.get_mut(call_id) {
                            tool.runner_outcome = Some(*outcome);
                        }
                    }
                    Event::ToolCallCompleted {
                        call_id, is_error, ..
                    } => {
                        if let Some(tool) = tools.get_mut(call_id) {
                            tool.is_error = Some(*is_error);
                        }
                    }
                    _ => {}
                }
            }
            if !invalid_clock {
                timing.queue_ms = duration(scheduled, started);
                timing.wall_ms = duration(started, Some(end));
                timing.first_response_ms = duration(started, first_response);
                if let Some(start) = started {
                    intervals.extend(waits.into_values().map(|left| (left, end.max(left))));
                    for (left, right) in &mut intervals {
                        *left = (*left).max(start);
                        *right = (*right).min(end).max(*left);
                    }
                    let approval = interval_union(&mut intervals);
                    timing.approval_wait_ms = Some(approval);
                    timing.active_ms = timing.wall_ms.and_then(|wall| wall.checked_sub(approval));
                }
            }
            timing.runner_ms_total = (timing.runner_calls > 0).then_some(runner_total);
            timing.tool_calls = tools.len();
            timing.incomplete_tool_calls = tools
                .values()
                .filter(|tool| tool.is_error.is_none())
                .count();
            let (terminal_status, output) = match &terminal.event {
                Event::RunCompleted { output } => ("completed", output.clone()),
                Event::RunFailed { .. } => ("failed", None),
                _ => ("cancelled", None),
            };
            let output_omitted = output
                .as_ref()
                .is_some_and(|value| value.len() > MAX_OUTPUT_BYTES);
            let mut snapshot = RunEvidenceSnapshot {
                schema_version: 1,
                workspace_id,
                session_id,
                run_id,
                terminal_sequence: terminal.sequence,
                terminal_status: terminal_status.into(),
                fixture_fingerprint: None,
                workload_fingerprint: (!messages.is_empty())
                    .then(|| crate::context::fingerprint(&messages)),
                policy_fingerprints,
                model_registry_fingerprints,
                timing,
                final_output: if output_omitted { None } else { output },
                output_omitted,
                tools: tools.into_values().collect(),
                artifacts: BTreeMap::new(),
                fingerprint: String::new(),
            };
            snapshot.fingerprint = crate::context::fingerprint(&snapshot);
            Some(snapshot)
        })
        .collect()
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum AcceptanceStatus {
    Pass,
    Fail,
    Unknown,
    NotApplicable,
}

fn default_test_timeout_ms() -> u64 {
    30_000
}

fn default_test_output_limit() -> usize {
    64 * 1024
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
pub enum AcceptanceCheck {
    ArtifactContains {
        path: String,
        value: String,
    },
    ExactFileContent {
        path: String,
        expected: String,
    },
    JsonField {
        path: String,
        #[serde(default)]
        pointer: String,
        expected: serde_json::Value,
    },
    JsonSchema {
        path: String,
        required_fields: Vec<String>,
    },
    PermittedFiles {
        allowed_paths: Vec<String>,
    },
    ControlledTest {
        id: String,
        command: String,
        #[serde(default = "default_test_timeout_ms")]
        timeout_ms: u64,
        #[serde(default = "default_test_output_limit")]
        output_limit_bytes: usize,
    },
    OutputContains {
        value: String,
    },
    OutputRegex {
        pattern: String,
    },
    /// Checks a successful tool result; it does not prove the artifact is correct.
    ToolSucceeded {
        name: String,
    },
}

impl AcceptanceCheck {
    pub fn validate(&self) -> Result<(), EvaluationError> {
        match self {
            AcceptanceCheck::OutputRegex { pattern } => {
                Regex::new(pattern)
                    .map_err(|_| EvaluationError("invalid acceptance regular expression".into()))?;
            }
            AcceptanceCheck::ArtifactContains { path, value } => {
                if path.trim().is_empty() || value.is_empty() {
                    return Err(EvaluationError("empty artifact criterion".into()));
                }
            }
            AcceptanceCheck::ExactFileContent { path, .. } => {
                if path.trim().is_empty() {
                    return Err(EvaluationError("empty exact file criterion path".into()));
                }
            }
            AcceptanceCheck::JsonField { path, .. } => {
                if path.trim().is_empty() {
                    return Err(EvaluationError("empty json field criterion path".into()));
                }
            }
            AcceptanceCheck::JsonSchema {
                path,
                required_fields,
            } => {
                if path.trim().is_empty() || required_fields.is_empty() {
                    return Err(EvaluationError("empty json schema criterion".into()));
                }
                if required_fields.iter().any(|field| field.trim().is_empty()) {
                    return Err(EvaluationError("empty field name in json schema".into()));
                }
            }
            AcceptanceCheck::PermittedFiles { allowed_paths } => {
                if allowed_paths.is_empty() || allowed_paths.iter().any(|p| p.trim().is_empty()) {
                    return Err(EvaluationError("invalid permitted files list".into()));
                }
            }
            AcceptanceCheck::ControlledTest {
                id,
                command,
                timeout_ms,
                output_limit_bytes,
            } => {
                if id.trim().is_empty()
                    || command.trim().is_empty()
                    || *timeout_ms == 0
                    || *output_limit_bytes == 0
                {
                    return Err(EvaluationError(
                        "invalid controlled test specification".into(),
                    ));
                }
            }
            AcceptanceCheck::OutputContains { value } => {
                if value.is_empty() {
                    return Err(EvaluationError("empty output acceptance marker".into()));
                }
            }
            AcceptanceCheck::ToolSucceeded { name } => {
                if name.trim().is_empty() {
                    return Err(EvaluationError("empty acceptance tool name".into()));
                }
            }
        }
        Ok(())
    }
}

fn default_criterion_weight() -> f64 {
    1.0
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct AcceptanceCriterion {
    pub id: String,
    pub check: AcceptanceCheck,
    #[serde(default = "default_criterion_weight")]
    pub weight: f64,
    #[serde(default)]
    pub required: bool,
}

impl AcceptanceCriterion {
    pub fn new(id: impl Into<String>, check: AcceptanceCheck) -> Self {
        Self {
            id: id.into(),
            check,
            weight: 1.0,
            required: false,
        }
    }

    pub fn required(mut self) -> Self {
        self.required = true;
        self
    }

    pub fn with_weight(mut self, weight: f64) -> Self {
        self.weight = weight;
        self
    }
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct AcceptanceSpec {
    pub task_id: String,
    pub version: u64,
    pub criteria: Vec<AcceptanceCriterion>,
}

impl AcceptanceSpec {
    pub fn validate(&self) -> Result<(), EvaluationError> {
        let mut ids = BTreeSet::new();
        if self.task_id.trim().is_empty() || self.version == 0 || self.criteria.len() > 128 {
            return Err(EvaluationError("invalid acceptance specification".into()));
        }
        let mut total_weight = 0.0;
        for criterion in &self.criteria {
            if criterion.id.trim().is_empty() || !ids.insert(&criterion.id) {
                return Err(EvaluationError(
                    "acceptance criterion IDs must be unique and non-empty".into(),
                ));
            }
            if criterion.weight < 0.0 || !criterion.weight.is_finite() {
                return Err(EvaluationError(
                    "acceptance criterion weight must be non-negative and finite".into(),
                ));
            }
            total_weight += criterion.weight;
            criterion.check.validate()?;
        }
        if !self.criteria.is_empty() && total_weight <= 0.0 {
            return Err(EvaluationError(
                "total criterion weight must be strictly positive".into(),
            ));
        }
        Ok(())
    }
    pub fn fingerprint(&self) -> String {
        crate::context::fingerprint(self)
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct AcceptanceResult {
    pub criterion_id: String,
    pub status: AcceptanceStatus,
    pub evidence: String,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct RunAcceptance {
    pub scorer: EvaluationPluginMetadata,
    pub task_id: String,
    pub specification_fingerprint: String,
    pub snapshot_fingerprint: String,
    pub results: Vec<AcceptanceResult>,
    pub completion_bps: Option<u32>,
    pub coverage_bps: Option<u32>,
    pub verified_success: Option<bool>,
}

pub trait RunAcceptanceScorer: std::fmt::Debug + Send + Sync {
    fn metadata(&self) -> EvaluationPluginMetadata;
    fn score(
        &self,
        snapshot: &RunEvidenceSnapshot,
        specification: &AcceptanceSpec,
    ) -> Result<RunAcceptance, EvaluationError>;
}

/// Execute a controlled test command in an isolated, bounded environment.
///
/// Gives checkers separate timeouts and bounded output capture.
/// Distinguishes failed test assertions (non-zero exit code) from evaluator faults (timeout, spawn failure).
pub fn execute_controlled_test(
    root: &std::path::Path,
    command: &str,
    timeout_ms: u64,
    output_limit_bytes: usize,
) -> ArtifactEvidence {
    use std::io::Read;
    use std::process::{Command, Stdio};
    use std::time::{Duration, Instant};

    let mut child = match Command::new("sh")
        .arg("-c")
        .arg(command)
        .current_dir(root)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
    {
        Ok(child) => child,
        Err(err) => {
            return ArtifactEvidence::TestResult {
                exit_code: None,
                passed: false,
                output: String::new(),
                output_truncated: false,
                error: Some(format!("failed to spawn test command: {err}")),
            };
        }
    };

    let start = Instant::now();
    let timeout = Duration::from_millis(timeout_ms.max(1));
    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break status,
            Ok(None) => {
                if start.elapsed() >= timeout {
                    let _ = child.kill();
                    let _ = child.wait();
                    return ArtifactEvidence::TestResult {
                        exit_code: None,
                        passed: false,
                        output: String::new(),
                        output_truncated: false,
                        error: Some(format!("test execution timed out after {timeout_ms}ms")),
                    };
                }
                std::thread::sleep(Duration::from_millis(10));
            }
            Err(err) => {
                let _ = child.kill();
                let _ = child.wait();
                return ArtifactEvidence::TestResult {
                    exit_code: None,
                    passed: false,
                    output: String::new(),
                    output_truncated: false,
                    error: Some(format!("failed to wait for test process: {err}")),
                };
            }
        }
    };

    let mut combined_output = Vec::new();
    if let Some(stdout) = child.stdout.take() {
        let _ = stdout
            .take((output_limit_bytes + 1) as u64)
            .read_to_end(&mut combined_output);
    }
    if let Some(stderr) = child.stderr.take() {
        let remaining = (output_limit_bytes + 1).saturating_sub(combined_output.len()) as u64;
        let _ = stderr.take(remaining).read_to_end(&mut combined_output);
    }

    let output_truncated = combined_output.len() > output_limit_bytes;
    if output_truncated {
        combined_output.truncate(output_limit_bytes);
    }
    let output = String::from_utf8_lossy(&combined_output).to_string();
    let exit_code = status.code();
    let passed = status.success();

    ArtifactEvidence::TestResult {
        exit_code,
        passed,
        output,
        output_truncated,
        error: None,
    }
}

#[derive(Debug)]
pub struct EvidenceAcceptanceScorer;
impl RunAcceptanceScorer for EvidenceAcceptanceScorer {
    fn metadata(&self) -> EvaluationPluginMetadata {
        EvaluationPluginMetadata {
            id: "evidence-checks".into(),
            version: 1,
            configuration_fingerprint: crate::context::fingerprint(&"evidence-checks-v1"),
        }
    }
    fn score(
        &self,
        snapshot: &RunEvidenceSnapshot,
        specification: &AcceptanceSpec,
    ) -> Result<RunAcceptance, EvaluationError> {
        specification.validate()?;
        let mut results = Vec::new();
        for criterion in &specification.criteria {
            let (passed, evidence) = match &criterion.check {
                AcceptanceCheck::ArtifactContains { path, value } => (
                    snapshot
                        .artifacts
                        .get(path)
                        .and_then(|artifact| match artifact {
                            ArtifactEvidence::Captured { content } => Some(content.contains(value)),
                            ArtifactEvidence::Missing => Some(false),
                            ArtifactEvidence::Unavailable => None,
                            _ => None,
                        }),
                    "artifact_snapshot",
                ),
                AcceptanceCheck::ExactFileContent { path, expected } => (
                    snapshot
                        .artifacts
                        .get(path)
                        .and_then(|artifact| match artifact {
                            ArtifactEvidence::Captured { content } => Some(content == expected),
                            ArtifactEvidence::Missing => Some(false),
                            ArtifactEvidence::Unavailable => None,
                            _ => None,
                        }),
                    "exact_file_content",
                ),
                AcceptanceCheck::JsonField {
                    path,
                    pointer,
                    expected,
                } => (
                    snapshot
                        .artifacts
                        .get(path)
                        .and_then(|artifact| match artifact {
                            ArtifactEvidence::Captured { content } => {
                                match serde_json::from_str::<serde_json::Value>(content) {
                                    Ok(val) => {
                                        let actual = if pointer.is_empty() {
                                            Some(&val)
                                        } else {
                                            val.pointer(pointer)
                                        };
                                        Some(actual == Some(expected))
                                    }
                                    Err(_) => Some(false),
                                }
                            }
                            ArtifactEvidence::Missing => Some(false),
                            ArtifactEvidence::Unavailable => None,
                            _ => None,
                        }),
                    "json_field_assertion",
                ),
                AcceptanceCheck::JsonSchema {
                    path,
                    required_fields,
                } => (
                    snapshot
                        .artifacts
                        .get(path)
                        .and_then(|artifact| match artifact {
                            ArtifactEvidence::Captured { content } => {
                                match serde_json::from_str::<serde_json::Value>(content) {
                                    Ok(serde_json::Value::Object(map)) => Some(
                                        required_fields.iter().all(|field| map.contains_key(field)),
                                    ),
                                    _ => Some(false),
                                }
                            }
                            ArtifactEvidence::Missing => Some(false),
                            ArtifactEvidence::Unavailable => None,
                            _ => None,
                        }),
                    "json_schema_assertion",
                ),
                AcceptanceCheck::PermittedFiles { allowed_paths } => (
                    snapshot.artifacts.get("__modified_files__").and_then(
                        |artifact| match artifact {
                            ArtifactEvidence::ModifiedFiles { paths } => {
                                let allowed: BTreeSet<&str> =
                                    allowed_paths.iter().map(|p| p.as_str()).collect();
                                Some(paths.iter().all(|path| allowed.contains(path.as_str())))
                            }
                            ArtifactEvidence::Unavailable => None,
                            _ => None,
                        },
                    ),
                    "permitted_files_assertion",
                ),
                AcceptanceCheck::ControlledTest { id, .. } => {
                    let key = format!("__test__:{id}");
                    (
                        snapshot
                            .artifacts
                            .get(&key)
                            .and_then(|artifact| match artifact {
                                ArtifactEvidence::TestResult {
                                    exit_code,
                                    passed,
                                    error,
                                    ..
                                } => {
                                    if error.is_some() {
                                        // Evaluator execution fault: cannot execute test
                                        None
                                    } else if *passed && *exit_code == Some(0) {
                                        Some(true)
                                    } else {
                                        // Test assertions failed (non-zero exit code)
                                        Some(false)
                                    }
                                }
                                ArtifactEvidence::Missing => Some(false),
                                ArtifactEvidence::Unavailable => None,
                                _ => None,
                            }),
                        "controlled_test_execution",
                    )
                }
                AcceptanceCheck::OutputContains { value } => (
                    snapshot
                        .final_output
                        .as_ref()
                        .map(|output| output.contains(value)),
                    "final_output",
                ),
                AcceptanceCheck::OutputRegex { pattern } => {
                    let regex = Regex::new(pattern).map_err(|_| {
                        EvaluationError("invalid acceptance regular expression".into())
                    })?;
                    (
                        snapshot
                            .final_output
                            .as_ref()
                            .map(|output| regex.is_match(output)),
                        "final_output",
                    )
                }
                AcceptanceCheck::ToolSucceeded { name } => {
                    let calls: Vec<_> = snapshot
                        .tools
                        .iter()
                        .filter(|tool| tool.name == *name)
                        .collect();
                    let result = if calls.iter().any(|tool| tool.is_error == Some(false)) {
                        Some(true)
                    } else if calls.iter().any(|tool| tool.is_error.is_none()) {
                        None
                    } else {
                        Some(false)
                    };
                    (result, "tool_completion")
                }
            };
            results.push(AcceptanceResult {
                criterion_id: criterion.id.clone(),
                status: match passed {
                    Some(true) => AcceptanceStatus::Pass,
                    Some(false) => AcceptanceStatus::Fail,
                    None => AcceptanceStatus::Unknown,
                },
                evidence: evidence.into(),
            });
        }
        let known = results
            .iter()
            .filter(|result| {
                matches!(
                    result.status,
                    AcceptanceStatus::Pass | AcceptanceStatus::Fail
                )
            })
            .count();
        let passed = results
            .iter()
            .filter(|result| result.status == AcceptanceStatus::Pass)
            .count();
        let all_known = known == results.len() && !results.is_empty();
        let verified_success = if snapshot.terminal_status != "completed"
            || results
                .iter()
                .any(|result| result.status == AcceptanceStatus::Fail)
        {
            Some(false)
        } else {
            all_known.then_some(passed == known)
        };
        Ok(RunAcceptance {
            scorer: self.metadata(),
            task_id: specification.task_id.clone(),
            specification_fingerprint: specification.fingerprint(),
            snapshot_fingerprint: snapshot.fingerprint.clone(),
            results,
            completion_bps: (known > 0).then(|| (passed * 10_000 / known) as u32),
            coverage_bps: (!specification.criteria.is_empty())
                .then(|| (known * 10_000 / specification.criteria.len()) as u32),
            verified_success,
        })
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct RunEvaluation {
    pub snapshot: RunEvidenceSnapshot,
    pub acceptance: Option<RunAcceptance>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct PolicyComparisonTrial {
    pub candidate: String,
    pub repetition: u32,
    pub evaluation: RunEvaluation,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct PolicyComparisonSummary {
    pub candidate: String,
    pub runs: usize,
    pub scored_runs: usize,
    pub successful_runs: usize,
    pub success_rate: Option<f64>,
    pub wall_samples: usize,
    pub wall_median_ms: Option<u64>,
    pub wall_p95_ms: Option<u64>,
    pub active_median_ms: Option<u64>,
    pub input_tokens: u64,
    pub output_tokens: u64,
    pub usage_reported_calls: usize,
    pub model_calls: usize,
}

fn percentile(values: &mut [u64], percentile: usize) -> Option<u64> {
    values.sort_unstable();
    (!values.is_empty())
        .then(|| values[(values.len() * percentile).div_ceil(100).saturating_sub(1)])
}

/// Compare actual trials on identical task/specification/repetition sets.
/// Reject duplicate runs, mixed policy versions, and incomparable cohorts.
pub fn compare_policy_trials(
    trials: &[PolicyComparisonTrial],
) -> Result<Vec<PolicyComparisonSummary>, EvaluationError> {
    if trials.is_empty() {
        return Err(EvaluationError("comparison requires actual trials".into()));
    }
    let mut groups = BTreeMap::<String, Vec<&PolicyComparisonTrial>>::new();
    let mut seen_runs = BTreeSet::new();
    for trial in trials {
        let snapshot = &trial.evaluation.snapshot;
        if trial.candidate.trim().is_empty()
            || !seen_runs.insert((
                &snapshot.workspace_id,
                &snapshot.session_id,
                &snapshot.run_id,
            ))
        {
            return Err(EvaluationError(
                "comparison has an empty candidate or duplicate run".into(),
            ));
        }
        let acceptance = trial.evaluation.acceptance.as_ref().ok_or_else(|| {
            EvaluationError("comparison requires acceptance results for every run".into())
        })?;
        if acceptance.snapshot_fingerprint != snapshot.fingerprint {
            return Err(EvaluationError("acceptance evidence mismatch".into()));
        }
        groups
            .entry(trial.candidate.clone())
            .or_default()
            .push(trial);
    }
    if groups.len() < 2 {
        return Err(EvaluationError(
            "comparison requires at least two candidates".into(),
        ));
    }
    let mut expected_cohort = None;
    let mut expected_registry = None;
    let mut summaries = Vec::new();
    for (candidate, trials) in groups {
        let mut cohort = BTreeSet::new();
        let mut policies = BTreeSet::new();
        let mut registries = BTreeSet::new();
        let mut wall = Vec::new();
        let mut active = Vec::new();
        let mut scored = 0;
        let mut successful = 0;
        let mut input_tokens = 0_u64;
        let mut output_tokens = 0_u64;
        let mut usage_reported_calls = 0;
        let mut model_calls = 0;
        for trial in &trials {
            let snapshot = &trial.evaluation.snapshot;
            let score = trial
                .evaluation
                .acceptance
                .as_ref()
                .expect("validated acceptance");
            let workload = snapshot
                .workload_fingerprint
                .as_ref()
                .filter(|value| !value.is_empty())
                .ok_or_else(|| EvaluationError("comparison requires workload identity".into()))?;
            if !cohort.insert((
                workload.clone(),
                snapshot.fixture_fingerprint.clone(),
                score.task_id.clone(),
                score.specification_fingerprint.clone(),
                score.scorer.clone().id,
                score.scorer.version,
                score.scorer.configuration_fingerprint.clone(),
                trial.repetition,
            )) {
                return Err(EvaluationError(
                    "duplicate task/repetition within candidate".into(),
                ));
            }
            if snapshot.policy_fingerprints.len() != 1
                || snapshot.policy_fingerprints.contains("")
                || snapshot.model_registry_fingerprints.len() != 1
            {
                return Err(EvaluationError(
                    "missing or changing routing provenance".into(),
                ));
            }
            policies.extend(snapshot.policy_fingerprints.iter().cloned());
            registries.extend(snapshot.model_registry_fingerprints.iter().cloned());
            if let Some(success) = score.verified_success {
                scored += 1;
                successful += usize::from(success);
            }
            if let Some(ms) = snapshot.timing.wall_ms {
                wall.push(ms);
            }
            if let Some(ms) = snapshot.timing.active_ms {
                active.push(ms);
            }
            input_tokens = input_tokens.saturating_add(snapshot.timing.input_tokens);
            output_tokens = output_tokens.saturating_add(snapshot.timing.output_tokens);
            usage_reported_calls += snapshot.timing.usage_reported_calls;
            model_calls += snapshot.timing.model_calls;
        }
        if policies.len() != 1 || registries.len() != 1 {
            return Err(EvaluationError(
                "candidate mixes policy versions or model registries".into(),
            ));
        }
        if expected_cohort
            .as_ref()
            .is_some_and(|expected| *expected != cohort)
            || expected_registry
                .as_ref()
                .is_some_and(|expected| *expected != registries)
        {
            return Err(EvaluationError(
                "candidates must share task/specification/repetition and model registry".into(),
            ));
        }
        expected_cohort = Some(cohort);
        expected_registry = Some(registries);
        summaries.push(PolicyComparisonSummary {
            candidate,
            runs: trials.len(),
            scored_runs: scored,
            successful_runs: successful,
            success_rate: (scored > 0).then(|| successful as f64 / scored as f64),
            wall_samples: wall.len(),
            wall_median_ms: percentile(&mut wall, 50),
            wall_p95_ms: percentile(&mut wall, 95),
            active_median_ms: percentile(&mut active, 50),
            input_tokens,
            output_tokens,
            usage_reported_calls,
            model_calls,
        });
    }
    Ok(summaries)
}

#[cfg(test)]
mod tests {
    use super::*;
    use arabica_protocol::{
        CommandId, EventId, EventMetadata, ToolPermissionOutcome, ToolPermissionScope,
        ToolPermissionSource,
    };

    fn event(sequence: u64, ms: u64, event: Event) -> EventEnvelope {
        EventEnvelope::new(
            EventMetadata {
                event_id: EventId::new(format!("event-{sequence}")),
                command_id: CommandId::new("command"),
                workspace_id: WorkspaceId::new("workspace"),
                session_id: SessionId::new("session"),
                run_id: Some(RunId::new("run")),
                sequence,
                occurred_at_ms: ms,
            },
            event,
        )
    }
    fn completed(output: Option<String>) -> RunEvidenceSnapshot {
        run_evidence_snapshots(&[
            event(0, 0, Event::RunStarted),
            event(1, 50, Event::RunCompleted { output }),
        ])
        .pop()
        .unwrap()
    }
    fn specification() -> AcceptanceSpec {
        AcceptanceSpec {
            task_id: "task".into(),
            version: 1,
            criteria: vec![AcceptanceCriterion::new(
                "answer",
                AcceptanceCheck::OutputContains {
                    value: "ready".into(),
                },
            )],
        }
    }

    #[test]
    fn overlapping_and_unresolved_approval_waits_are_unioned() {
        let resolved = |call: &str| Event::ToolCallPermissionResolved {
            call_id: call.into(),
            outcome: ToolPermissionOutcome::Allowed,
            scope: ToolPermissionScope::Once,
            source: ToolPermissionSource::User,
        };
        let history = [
            event(0, 0, Event::RunScheduled),
            event(1, 5, Event::RunStarted),
            event(
                2,
                10,
                Event::ToolCallPermissionRequested {
                    call_id: "a".into(),
                },
            ),
            event(
                3,
                20,
                Event::ToolCallPermissionRequested {
                    call_id: "b".into(),
                },
            ),
            event(4, 30, resolved("a")),
            event(5, 40, resolved("b")),
            event(
                6,
                45,
                Event::ToolCallPermissionRequested {
                    call_id: "c".into(),
                },
            ),
            event(7, 50, Event::RunCancelled),
        ];
        let snapshot = run_evidence_snapshots(&history).pop().unwrap();
        assert_eq!(snapshot.timing.queue_ms, Some(5));
        assert_eq!(snapshot.timing.wall_ms, Some(45));
        assert_eq!(snapshot.timing.approval_wait_ms, Some(35));
        assert_eq!(snapshot.timing.active_ms, Some(10));
        assert_eq!(snapshot.timing.runner_ms_total, None);
    }

    #[test]
    fn snapshot_stops_at_terminal_and_separates_session_identities() {
        let mut history = vec![event(
            1,
            1,
            Event::RunCompleted {
                output: Some("ready".into()),
            },
        )];
        let first = run_evidence_snapshots(&history);
        history.push(event(
            2,
            2,
            Event::RunCompleted {
                output: Some("later".into()),
            },
        ));
        assert_eq!(run_evidence_snapshots(&history), first);
        let mut other = event(
            3,
            3,
            Event::RunFailed {
                message: "PRIVATE".into(),
            },
        );
        other.session_id = SessionId::new("other-session");
        history.push(other);
        let snapshots = run_evidence_snapshots(&history);
        assert_eq!(snapshots.len(), 2);
        assert!(
            !serde_json::to_string(&snapshots)
                .unwrap()
                .contains("PRIVATE")
        );
        assert!(run_evidence_snapshots(&[event(1, 1, Event::RunStarted)]).is_empty());
    }

    #[test]
    fn clock_regressions_do_not_invent_zero_duration() {
        let snapshot = run_evidence_snapshots(&[
            event(1, 100, Event::RunStarted),
            event(2, 99, Event::RunCancelled),
        ])
        .pop()
        .unwrap();
        assert_eq!(snapshot.timing.wall_ms, None);
        assert_eq!(snapshot.timing.approval_wait_ms, None);
        assert_eq!(snapshot.timing.active_ms, None);
    }

    #[test]
    fn unknown_evidence_never_receives_success_or_full_coverage() {
        let scorer = EvidenceAcceptanceScorer;
        let spec = specification();
        let unknown = scorer.score(&completed(None), &spec).unwrap();
        assert_eq!(unknown.verified_success, None);
        assert_eq!(unknown.completion_bps, None);
        assert_eq!(unknown.coverage_bps, Some(0));
        let oversized = completed(Some("x".repeat(MAX_OUTPUT_BYTES + 1)));
        assert!(oversized.output_omitted);
        assert!(oversized.final_output.is_none());
        assert_eq!(
            scorer.score(&oversized, &spec).unwrap().verified_success,
            None
        );
        let pass = scorer
            .score(&completed(Some("ready".into())), &spec)
            .unwrap();
        assert_eq!(pass.verified_success, Some(true));
        assert_eq!(pass.completion_bps, Some(10_000));
        let mut partial = spec.clone();
        partial.criteria.push(AcceptanceCriterion::new(
            "missing-tool",
            AcceptanceCheck::ToolSucceeded {
                name: "shell".into(),
            },
        ));
        assert_eq!(
            scorer
                .score(&completed(Some("ready".into())), &partial)
                .unwrap()
                .verified_success,
            Some(false)
        );
        let empty = AcceptanceSpec {
            criteria: Vec::new(),
            ..spec
        };
        assert_eq!(
            scorer
                .score(&completed(Some("ready".into())), &empty)
                .unwrap()
                .verified_success,
            None
        );
    }

    #[test]
    fn failed_terminal_cannot_pass_even_when_individual_check_passes() {
        let mut snapshot = completed(Some("ready".into()));
        snapshot.terminal_status = "failed".into();
        assert_eq!(
            EvidenceAcceptanceScorer
                .score(&snapshot, &specification())
                .unwrap()
                .verified_success,
            Some(false)
        );
    }

    #[test]
    fn invalid_specifications_and_unknown_scorers_fail_explicitly() {
        let mut spec = specification();
        spec.criteria.push(spec.criteria[0].clone());
        assert!(spec.validate().is_err());
        spec.criteria.pop();
        spec.criteria[0].check = AcceptanceCheck::OutputRegex {
            pattern: "[".into(),
        };
        assert!(spec.validate().is_err());
        let registry =
            crate::EvaluationRegistry::builtins(&crate::EvaluationConfig::default()).unwrap();
        assert!(
            registry
                .acceptance("missing", &completed(None), &specification())
                .is_err()
        );
    }

    fn trial(candidate: &str, repetition: u32) -> PolicyComparisonTrial {
        let mut snapshot = completed(Some("ready".into()));
        snapshot.run_id = RunId::new(format!("{candidate}-{repetition}"));
        snapshot.workload_fingerprint = Some("fixture".into());
        snapshot.policy_fingerprints.insert(candidate.into());
        snapshot
            .model_registry_fingerprints
            .insert("registry".into());
        snapshot.fingerprint.clear();
        snapshot.fingerprint = crate::context::fingerprint(&snapshot);
        let acceptance = EvidenceAcceptanceScorer
            .score(&snapshot, &specification())
            .unwrap();
        PolicyComparisonTrial {
            candidate: candidate.into(),
            repetition,
            evaluation: RunEvaluation {
                snapshot,
                acceptance: Some(acceptance),
            },
        }
    }

    #[test]
    fn policy_comparison_requires_matched_trials_and_provenance() {
        let trials = vec![
            trial("baseline", 0),
            trial("candidate", 0),
            trial("baseline", 1),
            trial("candidate", 1),
        ];
        let result = compare_policy_trials(&trials).unwrap();
        assert_eq!(result.len(), 2);
        assert_eq!(result[0].success_rate, Some(1.0));
        assert_eq!(result[0].wall_median_ms, Some(50));
        assert!(compare_policy_trials(&trials[..3]).is_err());
        let mut mixed = trials.clone();
        mixed[1]
            .evaluation
            .snapshot
            .model_registry_fingerprints
            .insert("other".into());
        assert!(compare_policy_trials(&mixed).is_err());
        mixed = trials.clone();
        mixed[1]
            .evaluation
            .acceptance
            .as_mut()
            .unwrap()
            .snapshot_fingerprint = "wrong".into();
        assert!(compare_policy_trials(&mixed).is_err());
        mixed = trials.clone();
        mixed.push(trials[0].clone());
        assert!(compare_policy_trials(&mixed).is_err());
    }

    #[test]
    fn exact_file_content_matches_only_exact_content_and_rejects_partial() {
        let scorer = EvidenceAcceptanceScorer;
        let spec = AcceptanceSpec {
            task_id: "exact_task".into(),
            version: 1,
            criteria: vec![AcceptanceCriterion::new(
                "exact_file",
                AcceptanceCheck::ExactFileContent {
                    path: "config.json".into(),
                    expected: "{\"mode\":\"prod\"}".into(),
                },
            )],
        };

        // Exact match passes
        let mut artifacts = BTreeMap::new();
        artifacts.insert(
            "config.json".into(),
            ArtifactEvidence::Captured {
                content: "{\"mode\":\"prod\"}".into(),
            },
        );
        let snap = completed(Some("done".into()))
            .with_artifacts(artifacts)
            .unwrap();
        let acc = scorer.score(&snap, &spec).unwrap();
        assert_eq!(acc.results[0].status, AcceptanceStatus::Pass);

        // Substring / partial match fails (unlike ArtifactContains!)
        let mut artifacts = BTreeMap::new();
        artifacts.insert(
            "config.json".into(),
            ArtifactEvidence::Captured {
                content: "{\"mode\":\"prod\",\"extra\":true}".into(),
            },
        );
        let snap = completed(Some("done".into()))
            .with_artifacts(artifacts)
            .unwrap();
        let acc = scorer.score(&snap, &spec).unwrap();
        assert_eq!(acc.results[0].status, AcceptanceStatus::Fail);

        // Missing file fails
        let mut artifacts = BTreeMap::new();
        artifacts.insert("config.json".into(), ArtifactEvidence::Missing);
        let snap = completed(Some("done".into()))
            .with_artifacts(artifacts)
            .unwrap();
        let acc = scorer.score(&snap, &spec).unwrap();
        assert_eq!(acc.results[0].status, AcceptanceStatus::Fail);

        // Unavailable file is unknown
        let mut artifacts = BTreeMap::new();
        artifacts.insert("config.json".into(), ArtifactEvidence::Unavailable);
        let snap = completed(Some("done".into()))
            .with_artifacts(artifacts)
            .unwrap();
        let acc = scorer.score(&snap, &spec).unwrap();
        assert_eq!(acc.results[0].status, AcceptanceStatus::Unknown);
    }

    #[test]
    fn json_field_and_schema_assertions() {
        let scorer = EvidenceAcceptanceScorer;
        let spec = AcceptanceSpec {
            task_id: "json_task".into(),
            version: 1,
            criteria: vec![
                AcceptanceCriterion::new(
                    "status_check",
                    AcceptanceCheck::JsonField {
                        path: "out.json".into(),
                        pointer: "/status".into(),
                        expected: serde_json::json!("success"),
                    },
                ),
                AcceptanceCriterion::new(
                    "schema_check",
                    AcceptanceCheck::JsonSchema {
                        path: "out.json".into(),
                        required_fields: vec!["status".into(), "count".into()],
                    },
                ),
            ],
        };

        // Valid JSON with correct fields passes both
        let mut artifacts = BTreeMap::new();
        artifacts.insert(
            "out.json".into(),
            ArtifactEvidence::Captured {
                content: r#"{"status":"success","count":42}"#.into(),
            },
        );
        let snap = completed(Some("done".into()))
            .with_artifacts(artifacts)
            .unwrap();
        let acc = scorer.score(&snap, &spec).unwrap();
        assert_eq!(acc.results[0].status, AcceptanceStatus::Pass);
        assert_eq!(acc.results[1].status, AcceptanceStatus::Pass);

        // Wrong field value fails field check, passes schema
        let mut artifacts = BTreeMap::new();
        artifacts.insert(
            "out.json".into(),
            ArtifactEvidence::Captured {
                content: r#"{"status":"failed","count":42}"#.into(),
            },
        );
        let snap = completed(Some("done".into()))
            .with_artifacts(artifacts)
            .unwrap();
        let acc = scorer.score(&snap, &spec).unwrap();
        assert_eq!(acc.results[0].status, AcceptanceStatus::Fail);
        assert_eq!(acc.results[1].status, AcceptanceStatus::Pass);

        // Missing schema field fails schema
        let mut artifacts = BTreeMap::new();
        artifacts.insert(
            "out.json".into(),
            ArtifactEvidence::Captured {
                content: r#"{"status":"success"}"#.into(),
            },
        );
        let snap = completed(Some("done".into()))
            .with_artifacts(artifacts)
            .unwrap();
        let acc = scorer.score(&snap, &spec).unwrap();
        assert_eq!(acc.results[0].status, AcceptanceStatus::Pass);
        assert_eq!(acc.results[1].status, AcceptanceStatus::Fail);

        // Malformed JSON syntax fails assertion
        let mut artifacts = BTreeMap::new();
        artifacts.insert(
            "out.json".into(),
            ArtifactEvidence::Captured {
                content: "not json at all".into(),
            },
        );
        let snap = completed(Some("done".into()))
            .with_artifacts(artifacts)
            .unwrap();
        let acc = scorer.score(&snap, &spec).unwrap();
        assert_eq!(acc.results[0].status, AcceptanceStatus::Fail);
        assert_eq!(acc.results[1].status, AcceptanceStatus::Fail);
    }

    #[test]
    fn permitted_files_rejects_unauthorized_changes() {
        let scorer = EvidenceAcceptanceScorer;
        let spec = AcceptanceSpec {
            task_id: "permit_task".into(),
            version: 1,
            criteria: vec![AcceptanceCriterion::new(
                "whitelist",
                AcceptanceCheck::PermittedFiles {
                    allowed_paths: vec!["src/main.rs".into(), "Cargo.toml".into()],
                },
            )],
        };

        // Allowed modifications pass
        let mut artifacts = BTreeMap::new();
        artifacts.insert(
            "__modified_files__".into(),
            ArtifactEvidence::ModifiedFiles {
                paths: vec!["src/main.rs".into()],
            },
        );
        let snap = completed(Some("done".into()))
            .with_artifacts(artifacts)
            .unwrap();
        let acc = scorer.score(&snap, &spec).unwrap();
        assert_eq!(acc.results[0].status, AcceptanceStatus::Pass);

        // Modifying test files or unauthorized files fails!
        let mut artifacts = BTreeMap::new();
        artifacts.insert(
            "__modified_files__".into(),
            ArtifactEvidence::ModifiedFiles {
                paths: vec!["src/main.rs".into(), "tests/acceptance_test.rs".into()],
            },
        );
        let snap = completed(Some("done".into()))
            .with_artifacts(artifacts)
            .unwrap();
        let acc = scorer.score(&snap, &spec).unwrap();
        assert_eq!(acc.results[0].status, AcceptanceStatus::Fail);
    }

    #[test]
    fn controlled_test_execution_distinguishes_assertion_failure_from_evaluator_fault() {
        let scorer = EvidenceAcceptanceScorer;
        let spec = AcceptanceSpec {
            task_id: "test_task".into(),
            version: 1,
            criteria: vec![AcceptanceCriterion::new(
                "unit_test",
                AcceptanceCheck::ControlledTest {
                    id: "t1".into(),
                    command: "cargo test".into(),
                    timeout_ms: 5000,
                    output_limit_bytes: 4096,
                },
            )],
        };

        // Test ran and passed (exit code 0)
        let mut artifacts = BTreeMap::new();
        artifacts.insert(
            "__test__:t1".into(),
            ArtifactEvidence::TestResult {
                exit_code: Some(0),
                passed: true,
                output: "test result: ok".into(),
                output_truncated: false,
                error: None,
            },
        );
        let snap = completed(Some("done".into()))
            .with_artifacts(artifacts)
            .unwrap();
        let acc = scorer.score(&snap, &spec).unwrap();
        assert_eq!(acc.results[0].status, AcceptanceStatus::Pass);

        // Test ran and failed assertions (exit code 1) -> Task failure
        let mut artifacts = BTreeMap::new();
        artifacts.insert(
            "__test__:t1".into(),
            ArtifactEvidence::TestResult {
                exit_code: Some(1),
                passed: false,
                output: "assertion failed: left == right".into(),
                output_truncated: false,
                error: None,
            },
        );
        let snap = completed(Some("done".into()))
            .with_artifacts(artifacts)
            .unwrap();
        let acc = scorer.score(&snap, &spec).unwrap();
        assert_eq!(acc.results[0].status, AcceptanceStatus::Fail);

        // Checker could not execute / timed out -> Evaluator fault -> Status is Unknown!
        let mut artifacts = BTreeMap::new();
        artifacts.insert(
            "__test__:t1".into(),
            ArtifactEvidence::TestResult {
                exit_code: None,
                passed: false,
                output: String::new(),
                output_truncated: false,
                error: Some("test execution timed out after 5000ms".into()),
            },
        );
        let snap = completed(Some("done".into()))
            .with_artifacts(artifacts)
            .unwrap();
        let acc = scorer.score(&snap, &spec).unwrap();
        assert_eq!(acc.results[0].status, AcceptanceStatus::Unknown);
    }

    #[test]
    fn execute_controlled_test_runner_bounded_and_isolated() {
        let temp_dir = std::env::temp_dir();

        // Successful command
        let res = execute_controlled_test(&temp_dir, "echo 'hello world'", 5000, 1024);
        match res {
            ArtifactEvidence::TestResult {
                exit_code,
                passed,
                output,
                error,
                ..
            } => {
                assert_eq!(exit_code, Some(0));
                assert!(passed);
                assert!(output.contains("hello world"));
                assert!(error.is_none());
            }
            _ => panic!("unexpected evidence type"),
        }

        // Failing command
        let res = execute_controlled_test(&temp_dir, "exit 42", 5000, 1024);
        match res {
            ArtifactEvidence::TestResult {
                exit_code,
                passed,
                error,
                ..
            } => {
                assert_eq!(exit_code, Some(42));
                assert!(!passed);
                assert!(error.is_none());
            }
            _ => panic!("unexpected evidence type"),
        }

        // Timing out command
        let res = execute_controlled_test(&temp_dir, "sleep 2", 100, 1024);
        match res {
            ArtifactEvidence::TestResult { passed, error, .. } => {
                assert!(!passed);
                assert!(error.unwrap().contains("timed out"));
            }
            _ => panic!("unexpected evidence type"),
        }
    }
}
