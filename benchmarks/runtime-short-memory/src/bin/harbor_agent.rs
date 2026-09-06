use std::env;
use std::error::Error;
use std::fmt::Display;
use std::io::{BufRead, Write};
use std::path::{Path, PathBuf};
use std::time::Instant;

use serde::{Deserialize, Serialize};
use structure_model::{ContentBlock, ToolCallItem, ToolDefinition, ToolResultItem};
use structure_protocol::{
    Command, CommandEnvelope, CommandId, Event, EventEnvelope, RunId, TerminalControllerPolicy,
    TerminalControllerTransitionReason, ToolInteractionKind, WorkspaceId,
};
use structure_provider::{ApiModelProvider, ApiProviderConfig, ApiType};
use structure_runner::{
    RunnerEnvironment, RunnerError, RunnerOutput, ToolExecutionRequest, ToolExecutionResult,
};
use structure_runtime::{
    AutoHydrationObservation, CoreRuntime, PointerGcAdmissionObservation, PointerGcAdmissionPolicy,
    PointerGcObservationSink, RuntimeArchiveStore, RuntimeCompactionStrategy, ShortMemoryPolicy,
    runtime_complete_tool_definition,
};
use structure_session::SessionManager;
use structure_short_memory_benchmark::{
    ProviderCallObservation, ProviderRecorder, RecordingProvider,
};

const REPORT_SCHEMA: &str = "structure.harbor-agent/v13";
const DEFAULT_MAX_STEPS: usize = 128;
const DEFAULT_MAX_TOKENS: u32 = 8_192;
const DEFAULT_CHECKPOINT_BATCHES: usize = 8;
const DEFAULT_PGC_EFFORT: usize = 1;
const DEFAULT_PGC_CONTINUATION_PROBABILITY_BPS: u32 = 7_500;
const DEFAULT_PGC_CACHED_INPUT_COST_BPS: u32 = 0;
const GC_GATE_MAX_FIRST_ADMISSION_FRACTION_BPS: u32 = 6_000;
const GC_GATE_MIN_POST_ADMISSION_PROVIDER_CALLS: usize = 4;
const MAX_TOOL_OUTPUT_CHARS: usize = 8_000;
const PUBLIC_VALIDATE_TOOL_NAME: &str = "runtime_validate";
const INPUT_SNAPSHOT_PATH: &str = "/tmp/structure-input-snapshot";
const INPUT_SNAPSHOT_COMMAND: &str = "set -eu; snapshot=/tmp/structure-input-snapshot; mkdir -p \"$snapshot\"; find /app -maxdepth 1 -type f \\( -name '*.db' -o -name '*.db-*' -o -name '*.sqlite' -o -name '*.sqlite-*' -o -name '*.wal' \\) -exec cp -p {} \"$snapshot\"/ \\;";

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "UPPERCASE")]
enum Strategy {
    B0,
    B2,
    Pgc,
    Fbgc,
    Capc,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "kebab-case")]
enum PublicValidationProfile {
    BuildCythonExt,
    DbWalRecovery,
}

impl PublicValidationProfile {
    fn parse(raw: &str) -> Result<Self, Box<dyn Error>> {
        match raw {
            "build-cython-ext" => Ok(Self::BuildCythonExt),
            "db-wal-recovery" => Ok(Self::DbWalRecovery),
            _ => Err(format!(
                "invalid public validation profile {raw}; expected build-cython-ext or db-wal-recovery"
            )
            .into()),
        }
    }

    fn command(self) -> &'static str {
        match self {
            Self::BuildCythonExt => {
                r#"set -eu
cd /app/pyknotid
python3 -B - <<'PY'
import importlib.machinery
import numpy
import re
from pathlib import Path
assert numpy.__version__ == '2.3.0', numpy.__version__
from pyknotid.spacecurves import chelpers, ccomplexity
from pyknotid import cinvariants
suffixes = tuple(importlib.machinery.EXTENSION_SUFFIXES)
for module in (chelpers, ccomplexity, cinvariants):
    assert module.__file__.endswith(suffixes), module.__file__
deprecated = re.compile(r'\b(?:np|n)\.(?:bool|complex|float|int|object|str)\b')
for path in Path('pyknotid').rglob('*'):
    if path.suffix in {'.py', '.pyx', '.pxd'}:
        assert not deprecated.search(path.read_text(errors='replace')), path
import pyknotid.make as mk
import pyknotid.spacecurves as sp
k = sp.Knot(mk.three_twist(num_points=100))
k.alexander_polynomial(-1)
PY
python3 -m pytest -q tests --ignore=tests/test_random_curves.py --ignore=tests/test_catalogue.py"#
            }
            Self::DbWalRecovery => {
                r#"set -eu
python3 -B - <<'PY'
import json
from pathlib import Path
path = Path('/app/recovered.json')
assert path.is_file(), 'missing /app/recovered.json'
rows = json.loads(path.read_text())
assert isinstance(rows, list) and len(rows) == 11, len(rows) if isinstance(rows, list) else type(rows)
assert [row.get('id') for row in rows] == list(range(1, 12))
assert all(set(row) == {'id', 'name', 'value'} for row in rows)
assert all(isinstance(row['name'], str) and row['name'] for row in rows)
PY"#
            }
        }
    }
}

impl Strategy {
    fn parse(raw: &str) -> Result<Self, Box<dyn Error>> {
        match raw.to_ascii_uppercase().as_str() {
            "B0" => Ok(Self::B0),
            "B2" => Ok(Self::B2),
            "PGC" => Ok(Self::Pgc),
            "FBGC" | "FILE_BACKED_GC" => Ok(Self::Fbgc),
            "CAPC" => Ok(Self::Capc),
            _ => Err(format!("invalid strategy {raw}; expected B0, B2, PGC, FBGC, or CAPC").into()),
        }
    }
}

#[derive(Debug)]
struct Config {
    strategy: Strategy,
    report: PathBuf,
    model: String,
    base_url: String,
    api_key: String,
    api_type: ApiType,
    max_steps: usize,
    max_tokens: u32,
    checkpoint_batches: usize,
    pgc_effort: usize,
    pgc_continuation_probability_bps: u32,
    pgc_cached_input_cost_bps: u32,
    pointer_gc_admission_policy: PointerGcAdmissionPolicy,
    thinking_enabled: bool,
    terminal_controller_policy: TerminalControllerPolicy,
    public_validation_profile: Option<PublicValidationProfile>,
}

#[derive(Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
enum BridgeInput {
    Start {
        instruction: String,
    },
    ExecResult {
        id: String,
        stdout: Option<String>,
        stderr: Option<String>,
        return_code: i32,
    },
}

#[derive(Serialize)]
#[serde(tag = "type", rename_all = "snake_case")]
enum BridgeOutput<'a> {
    Ready,
    Exec {
        id: &'a str,
        command: &'a str,
        cwd: Option<&'a str>,
        timeout_sec: u64,
        strict_pipeline: bool,
    },
    Done {
        report: &'a str,
    },
}

#[derive(Debug, Default)]
struct HarborBridgeRunner {
    input_snapshot_initialized: bool,
    public_validation_profile: Option<PublicValidationProfile>,
    require_public_validation: bool,
}

#[derive(Debug)]
struct PointerGcPartialRecorder {
    path: PathBuf,
    observations: Vec<PointerGcAdmissionObservation>,
}

impl PointerGcPartialRecorder {
    fn new(path: PathBuf) -> Self {
        Self {
            path,
            observations: Vec::new(),
        }
    }
}

impl PointerGcObservationSink for PointerGcPartialRecorder {
    fn record(&mut self, observation: &PointerGcAdmissionObservation) {
        self.observations.push(observation.clone());
        if let Ok(encoded) = serde_json::to_vec_pretty(&self.observations) {
            let _ = std::fs::write(&self.path, encoded);
        }
    }
}

impl HarborBridgeRunner {
    fn exchange(&self, output: &BridgeOutput<'_>) -> Result<BridgeInput, RunnerError> {
        let encoded = serde_json::to_string(output)
            .map_err(|error| RunnerError::new(format!("bridge encode failed: {error}")))?;
        let mut stdout = std::io::stdout().lock();
        writeln!(stdout, "{encoded}")
            .and_then(|_| stdout.flush())
            .map_err(|error| RunnerError::new(format!("bridge write failed: {error}")))?;

        let mut line = String::new();
        std::io::stdin()
            .lock()
            .read_line(&mut line)
            .map_err(|error| RunnerError::new(format!("bridge read failed: {error}")))?;
        if line.is_empty() {
            return Err(RunnerError::new("bridge closed before returning a result"));
        }
        serde_json::from_str(&line)
            .map_err(|error| RunnerError::new(format!("invalid bridge response: {error}")))
    }
}

impl RunnerEnvironment for HarborBridgeRunner {
    fn classify(&self, call: &ToolCallItem) -> ToolInteractionKind {
        if call.name == PUBLIC_VALIDATE_TOOL_NAME && self.public_validation_profile.is_some() {
            return ToolInteractionKind::Validation;
        }
        let Some(command) = call
            .arguments
            .get("command")
            .and_then(serde_json::Value::as_str)
        else {
            return ToolInteractionKind::Generic;
        };
        classify_shell_interaction(command)
    }

    async fn execute(
        &mut self,
        request: ToolExecutionRequest,
    ) -> Result<ToolExecutionResult, RunnerError> {
        if request.call.name != "shell" && request.call.name != PUBLIC_VALIDATE_TOOL_NAME {
            return Err(RunnerError::new(format!(
                "unknown Harbor tool: {}",
                request.call.name
            )));
        }
        if !self.input_snapshot_initialized {
            let snapshot_id = "structure-input-snapshot";
            let snapshot = self.exchange(&BridgeOutput::Exec {
                id: snapshot_id,
                command: INPUT_SNAPSHOT_COMMAND,
                cwd: Some("/app"),
                timeout_sec: 120,
                strict_pipeline: false,
            })?;
            let BridgeInput::ExecResult {
                id,
                return_code,
                stderr,
                ..
            } = snapshot
            else {
                return Err(RunnerError::new(
                    "bridge returned start instead of snapshot result",
                ));
            };
            if id != snapshot_id {
                return Err(RunnerError::new(format!(
                    "snapshot result id {id} does not match {snapshot_id}"
                )));
            }
            if return_code != 0 {
                return Err(RunnerError::new(format!(
                    "input snapshot failed with exit code {return_code}: {}",
                    stderr.unwrap_or_default()
                )));
            }
            self.input_snapshot_initialized = true;
        }
        let (command, cwd, timeout_sec) = if request.call.name == PUBLIC_VALIDATE_TOOL_NAME {
            let profile = self.public_validation_profile.ok_or_else(|| {
                RunnerError::new("runtime_validate has no configured public validation profile")
            })?;
            (profile.command(), Some("/app"), 900)
        } else {
            let command = request
                .call
                .arguments
                .get("command")
                .and_then(serde_json::Value::as_str)
                .ok_or_else(|| RunnerError::new("shell.command must be a string"))?;
            let cwd = request
                .call
                .arguments
                .get("cwd")
                .and_then(serde_json::Value::as_str);
            let timeout_sec = request
                .call
                .arguments
                .get("timeout_sec")
                .and_then(serde_json::Value::as_u64)
                .unwrap_or(120)
                .clamp(1, 900);
            (command, cwd, timeout_sec)
        };
        let id = request.call.call_id.clone();
        let response = self.exchange(&BridgeOutput::Exec {
            id: &id,
            command,
            cwd,
            timeout_sec,
            strict_pipeline: shell_requires_strict_pipeline(command),
        })?;
        let BridgeInput::ExecResult {
            id: response_id,
            mut stdout,
            mut stderr,
            mut return_code,
        } = response
        else {
            return Err(RunnerError::new(
                "bridge returned start instead of exec_result",
            ));
        };
        if response_id != id {
            return Err(RunnerError::new(format!(
                "bridge result id {response_id} does not match {id}"
            )));
        }

        let shell_kind =
            (request.call.name == "shell").then(|| classify_shell_interaction(command));
        if self.require_public_validation
            && return_code == 0
            && shell_kind.is_some_and(tool_interaction_requests_validation)
        {
            let profile = self.public_validation_profile.ok_or_else(|| {
                RunnerError::new("public validation is required but no profile is configured")
            })?;
            let public_id = format!("{id}-public-validation");
            let public = self.exchange(&BridgeOutput::Exec {
                id: &public_id,
                command: profile.command(),
                cwd: Some("/app"),
                timeout_sec: 900,
                strict_pipeline: true,
            })?;
            let BridgeInput::ExecResult {
                id: public_response_id,
                stdout: public_stdout,
                stderr: public_stderr,
                return_code: public_return_code,
            } = public
            else {
                return Err(RunnerError::new(
                    "bridge returned start instead of public validation result",
                ));
            };
            if public_response_id != public_id {
                return Err(RunnerError::new(format!(
                    "public validation result id {public_response_id} does not match {public_id}"
                )));
            }
            append_public_validation_output(&mut stdout, public_stdout, "stdout");
            append_public_validation_output(&mut stderr, public_stderr, "stderr");
            return_code = public_return_code;
        }

        let stdout = truncate_output(stdout.unwrap_or_default());
        let stderr = truncate_output(stderr.unwrap_or_default());
        let mut output = Vec::new();
        if !stdout.is_empty() {
            output.push(RunnerOutput::Stdout(stdout.clone()));
        }
        if !stderr.is_empty() {
            output.push(RunnerOutput::Stderr(stderr.clone()));
        }
        let content = format!("exit_code: {return_code}\nstdout:\n{stdout}\nstderr:\n{stderr}");
        Ok(ToolExecutionResult {
            result: ToolResultItem {
                id: None,
                call_id: request.call.call_id,
                name: Some(request.call.name),
                content: vec![ContentBlock::text(content)],
                is_error: return_code != 0,
            },
            output,
        })
    }

    async fn cancel(&mut self, _run_id: &RunId) -> Result<bool, RunnerError> {
        Ok(false)
    }
}

fn tool_interaction_requests_validation(kind: ToolInteractionKind) -> bool {
    matches!(
        kind,
        ToolInteractionKind::Validation | ToolInteractionKind::MutationWithValidation
    )
}

fn append_public_validation_output(
    destination: &mut Option<String>,
    source: Option<String>,
    stream: &str,
) {
    let source = source.unwrap_or_default();
    let marker = format!("\n[public validation {stream}]\n");
    destination
        .get_or_insert_with(String::new)
        .push_str(&format!("{marker}{source}"));
}

fn classify_shell_interaction(command: &str) -> ToolInteractionKind {
    let command = command.to_ascii_lowercase();
    let dependency_position = last_pattern_position(
        &command,
        &[
            "pip install",
            "uv pip",
            "apt-get install",
            "npm install",
            "cargo add",
        ],
    );
    let mutation_position = last_command_position(
        &command,
        &[
            "sed -i",
            "perl -pi",
            "apply_patch",
            "tee ",
            "touch ",
            "mkdir ",
            "git clone",
            "cp ",
            "mv ",
            "rm ",
            "chmod ",
            "chown ",
            "ln ",
        ],
    )
    .into_iter()
    .chain(shell_output_redirection_position(&command))
    .chain(inline_mutation_position(&command))
    .max();
    let build_position = (!invokes_inline_program(&command))
        .then(|| last_pattern_position(&command, &["build_ext", "cargo build", "cmake ", "make "]))
        .flatten();
    let validation_position =
        last_command_position(&command, &["pytest", "cargo test", "npm test", "unittest"])
            .into_iter()
            .chain(inline_validation_position(&command))
            .max();
    let last_state_change = dependency_position
        .into_iter()
        .chain(mutation_position)
        .chain(build_position)
        .max();

    // A successful runner result validates the final state only when its last
    // recognized semantic operation is validation. This covers safe patterns
    // such as `rm stale && pytest`, while `pytest && rm stale` remains a
    // mutation that revokes completion eligibility.
    if matches!((last_state_change, validation_position), (Some(change), Some(check)) if check > change)
    {
        ToolInteractionKind::MutationWithValidation
    } else if dependency_position.is_some() {
        ToolInteractionKind::Dependency
    } else if mutation_position.is_some() {
        ToolInteractionKind::Mutation
    } else if validation_position.is_some() {
        // Inspect the executable shape before scanning the embedded program.
        // Inline validation source can legitimately contain strings such as
        // `import package.make as make`, which must not turn a read-only probe
        // into a state-changing build event.
        ToolInteractionKind::Validation
    } else if build_position.is_some() {
        ToolInteractionKind::Build
    } else if [
        "cat ",
        "grep ",
        "rg ",
        "find ",
        "ls ",
        "head ",
        "tail ",
        "sed -n",
        "git status",
        "git diff",
    ]
    .iter()
    .any(|pattern| command.contains(pattern))
    {
        ToolInteractionKind::Inspection
    } else {
        ToolInteractionKind::Generic
    }
}

fn last_command_position(command: &str, patterns: &[&str]) -> Option<usize> {
    patterns
        .iter()
        .flat_map(|pattern| command.match_indices(pattern))
        .filter_map(|(position, pattern)| {
            let prefix = &command[..position];
            let trimmed = prefix.trim_end();
            let command_boundary = trimmed.is_empty()
                || trimmed.ends_with("&&")
                || trimmed.ends_with("||")
                || trimmed.ends_with(';')
                || trimmed.ends_with('|')
                || trimmed.ends_with("-m");
            command_boundary.then_some(position + pattern.len())
        })
        .max()
}

fn last_pattern_position(command: &str, patterns: &[&str]) -> Option<usize> {
    patterns
        .iter()
        .filter_map(|pattern| command.rfind(pattern))
        .max()
}

#[cfg(test)]
fn has_shell_output_redirection(command: &str) -> bool {
    shell_output_redirection_position(command).is_some()
}

fn shell_output_redirection_position(command: &str) -> Option<usize> {
    let mut single_quoted = false;
    let mut double_quoted = false;
    let mut escaped = false;
    let chars: Vec<char> = command.chars().collect();
    for (index, character) in chars.iter().copied().enumerate() {
        if escaped {
            escaped = false;
            continue;
        }
        if character == '\\' && !single_quoted {
            escaped = true;
            continue;
        }
        if character == '\'' && !double_quoted {
            single_quoted = !single_quoted;
            continue;
        }
        if character == '"' && !single_quoted {
            double_quoted = !double_quoted;
            continue;
        }
        if character != '>' || single_quoted || double_quoted {
            continue;
        }
        // Descriptor duplication such as `2>&1` and discarding a stream to
        // `/dev/null` change routing, not workspace state. Other unquoted
        // output redirects can write a file and invalidate reusable results.
        let target = chars[index + 1..].iter().collect::<String>();
        if chars.get(index + 1) != Some(&'&') && !target.trim_start().starts_with("/dev/null") {
            return Some(index);
        }
    }
    None
}

fn inline_validation_position(command: &str) -> Option<usize> {
    invokes_inline_program(command)
        .then(|| {
            last_pattern_position(
                command,
                &[
                    "assert ",
                    "raise assertionerror",
                    "sys.exit(1)",
                    "sys.exit(false)",
                ],
            )
        })
        .flatten()
}

fn invokes_inline_program(command: &str) -> bool {
    (command.contains("python ") || command.contains("python3 "))
        && (command.contains(" -c") || command.contains("<<"))
}

fn inline_mutation_position(command: &str) -> Option<usize> {
    if !invokes_inline_program(command) {
        return None;
    }
    let mut position = last_pattern_position(
        command,
        &[
            ".write(",
            "write_text(",
            "write_bytes(",
            "unlink(",
            "remove(",
            "rename(",
            "mkdir(",
            "subprocess",
            "os.system",
            "shutil",
        ],
    );
    // `open(path)` and `open(path, 'rb')` are read-only. Only an explicit
    // write-capable mode makes open itself a mutation marker.
    let mut search_from = 0;
    while let Some(relative) = command[search_from..].find("open(") {
        let start = search_from + relative;
        let end = command[start..]
            .find(')')
            .map_or(command.len(), |offset| start + offset);
        let call = &command[start..end];
        if ["'w", "\"w", "'a", "\"a", "'x", "\"x", "'r+", "\"r+"]
            .iter()
            .any(|mode| call.contains(mode))
        {
            position = Some(position.map_or(start, |current| current.max(start)));
        }
        search_from = end.saturating_add(1);
        if search_from >= command.len() {
            break;
        }
    }
    position
}

fn shell_requires_strict_pipeline(command: &str) -> bool {
    let command = command.to_ascii_lowercase();
    [
        "pip install",
        "uv pip",
        "apt-get install",
        "npm install",
        "cargo add",
        "build_ext",
        "cargo build",
        "cmake ",
        "make ",
        "pytest",
        "cargo test",
        "npm test",
        "unittest",
    ]
    .iter()
    .any(|pattern| command.contains(pattern))
        || inline_validation_position(&command).is_some()
}

fn truncate_output(value: String) -> String {
    let chars = value.chars().count();
    if chars <= MAX_TOOL_OUTPUT_CHARS {
        return value;
    }
    let half = MAX_TOOL_OUTPUT_CHARS / 2;
    let head: String = value.chars().take(half).collect();
    let tail: String = value.chars().skip(chars.saturating_sub(half)).collect();
    format!(
        "{head}\n...[truncated {} characters; rerun a narrower command to inspect them]...\n{tail}",
        chars - MAX_TOOL_OUTPUT_CHARS
    )
}

#[derive(Serialize)]
struct GcQualityGate {
    applicable: bool,
    max_first_admission_fraction_bps: u32,
    min_post_admission_provider_calls: usize,
    require_post_admission_archive_read: bool,
    require_pointer_projection: bool,
    require_substitutive_projection: bool,
    first_admission_model_step: Option<usize>,
    first_admission_fraction_bps: Option<u32>,
    post_admission_provider_calls: usize,
    post_admission_archive_read_count: usize,
    first_post_admission_archive_read_model_step: Option<usize>,
    pointer_projection_provider_calls: usize,
    substitutive_projection_transitions: usize,
    early_admission_passed: bool,
    reuse_window_passed: bool,
    archive_reread_passed: bool,
    pointer_projection_passed: bool,
    substitutive_projection_passed: bool,
    passed: bool,
}

#[derive(Serialize)]
struct HarborAgentReport {
    schema_version: &'static str,
    strategy: Strategy,
    terminal_controller_policy: TerminalControllerPolicy,
    public_validation_profile: Option<PublicValidationProfile>,
    model: String,
    api_type: ApiType,
    compaction_strategy: RuntimeCompactionStrategy,
    pointer_gc_enabled: bool,
    pointer_gc_checkpoint_batches: usize,
    pointer_gc_admission_policy: PointerGcAdmissionPolicy,
    pgc_effort: usize,
    pgc_continuation_probability_bps: u32,
    pgc_cached_input_cost_bps: u32,
    pointer_gc_admission_checks: usize,
    pointer_gc_admissions: usize,
    pointer_gc_admission_observations: Vec<PointerGcAdmissionObservation>,
    auto_hydration_count: usize,
    auto_hydrated_bytes: usize,
    auto_hydration_observations: Vec<AutoHydrationObservation>,
    gc_quality_gate: GcQualityGate,
    max_model_steps: usize,
    elapsed_ms: u64,
    terminal_success: bool,
    terminal_event_valid: bool,
    final_output: Option<String>,
    provider_calls: Vec<ProviderCallObservation>,
    input_tokens: u64,
    uncached_input_tokens: u64,
    cached_input_tokens: u64,
    cache_creation_input_tokens: u64,
    output_tokens: u64,
    reasoning_output_tokens: u64,
    peak_input_tokens: u64,
    peak_model_input_bytes: usize,
    cache_reset_count: usize,
    pointer_transition_cache_reset_count: usize,
    provider_latency_ms: u64,
    tool_calls: usize,
    tool_errors: usize,
    memory_search_calls: usize,
    memory_read_calls: usize,
    memory_pointer_appearances: usize,
    events: Vec<EventEnvelope>,
}

#[tokio::main]
async fn main() {
    if let Err(error) = run().await {
        eprintln!("Structure Harbor agent failed: {error}");
        std::process::exit(1);
    }
}

async fn run() -> Result<(), Box<dyn Error>> {
    let config = parse_config()?;
    send_output(&BridgeOutput::Ready)?;
    let instruction = match read_input()? {
        BridgeInput::Start { instruction } if !instruction.trim().is_empty() => instruction,
        BridgeInput::Start { .. } => return Err("Harbor instruction must not be empty".into()),
        BridgeInput::ExecResult { .. } => return Err("expected start bridge message".into()),
    };

    if let Some(parent) = config.report.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let provider = ApiModelProvider::new(
        ApiProviderConfig::new(
            config.api_type,
            config.api_key.clone(),
            config.base_url.clone(),
            config.model.clone(),
        )
        .with_max_tokens(config.max_tokens)
        .with_thinking(config.thinking_enabled)
        .with_anthropic_cache_static_prefix(config.strategy == Strategy::Capc)
        .with_request_timeout_secs(480)
        .with_raw_exchange_dir(config.report.with_file_name("provider-raw")),
    )?;
    let recorder = ProviderRecorder::with_snapshot_path(
        config.report.with_file_name("provider-calls.partial.json"),
    );
    let (policy, compaction_strategy) = match config.strategy {
        Strategy::B0 => (
            ShortMemoryPolicy::full_replay(),
            RuntimeCompactionStrategy::Disabled,
        ),
        Strategy::B2 => (
            ShortMemoryPolicy::ttl_only(),
            RuntimeCompactionStrategy::Disabled,
        ),
        Strategy::Pgc => (
            ShortMemoryPolicy::ttl_only(),
            RuntimeCompactionStrategy::PointerGc,
        ),
        Strategy::Fbgc => (
            ShortMemoryPolicy::ttl_only(),
            RuntimeCompactionStrategy::FileBackedGc,
        ),
        Strategy::Capc => (
            ShortMemoryPolicy::full_replay(),
            RuntimeCompactionStrategy::Disabled,
        ),
    };
    let archive_root = config
        .report
        .parent()
        .unwrap_or_else(|| Path::new("."))
        .join("runtime-memory");
    let mut runtime = CoreRuntime::with_memory_configuration(
        RecordingProvider::new(provider, recorder.clone()),
        HarborBridgeRunner {
            input_snapshot_initialized: false,
            public_validation_profile: config.public_validation_profile,
            require_public_validation: config.terminal_controller_policy.is_typed()
                && config.public_validation_profile.is_some(),
        },
        policy,
        false,
        RuntimeArchiveStore::File { root: archive_root },
    );
    runtime.set_compaction_strategy(compaction_strategy);
    runtime.set_pointer_gc_checkpoint_batches(config.checkpoint_batches);
    runtime.set_pointer_gc_effort(config.pgc_effort);
    runtime.set_pointer_gc_continuation_probability_bps(config.pgc_continuation_probability_bps);
    runtime.set_pointer_gc_cached_input_cost_bps(config.pgc_cached_input_cost_bps);
    runtime.set_pointer_gc_admission_policy(config.pointer_gc_admission_policy);
    runtime.set_pointer_gc_observation_sink(PointerGcPartialRecorder::new(
        config
            .report
            .with_file_name("pointer-gc-admissions.partial.json"),
    ));
    runtime.set_max_model_steps_per_run(config.max_steps);
    runtime.set_terminal_controller_policy(config.terminal_controller_policy);
    let mut tools = vec![
        shell_definition(),
        memory_search_definition(),
        memory_read_definition(),
        runtime_complete_tool_definition(),
    ];
    if config.terminal_controller_policy.is_typed() && config.public_validation_profile.is_some() {
        tools.push(public_validation_definition());
    }
    runtime.set_tools(tools);

    let started = Instant::now();
    let mut manager = SessionManager::new(runtime);
    let create_events = manager
        .handle(CommandEnvelope::new(
            CommandId::new("harbor-session-create"),
            None,
            Command::SessionCreate {
                workspace_id: WorkspaceId::new("harbor-workspace"),
            },
        ))
        .await?;
    let session_id = create_events
        .first()
        .ok_or("session creation returned no event")?
        .session_id
        .clone();
    let mut events = create_events;
    events.extend(
        manager
            .handle(CommandEnvelope::new(
                CommandId::new("harbor-task"),
                Some(session_id),
                Command::MessageSend {
                    content: terminal_instruction(
                        &instruction,
                        config.terminal_controller_policy.is_typed()
                            && config.public_validation_profile.is_some(),
                    ),
                },
            ))
            .await?,
    );
    let provider_calls = recorder.from(0);
    let pointer_gc_admission_observations = manager
        .runtime()
        .pointer_gc_admission_observations()
        .to_vec();
    let auto_hydration_observations = manager.runtime().auto_hydration_observations().to_vec();
    let report = build_report(
        &config,
        started,
        events,
        provider_calls,
        pointer_gc_admission_observations,
        auto_hydration_observations,
    );
    std::fs::write(&config.report, serde_json::to_vec_pretty(&report)?)?;
    send_output(&BridgeOutput::Done {
        report: &config.report.to_string_lossy(),
    })?;
    Ok(())
}

fn build_report(
    config: &Config,
    started: Instant,
    events: Vec<EventEnvelope>,
    provider_calls: Vec<ProviderCallObservation>,
    pointer_gc_admission_observations: Vec<PointerGcAdmissionObservation>,
    auto_hydration_observations: Vec<AutoHydrationObservation>,
) -> HarborAgentReport {
    let final_output = events.iter().rev().find_map(|event| match &event.event {
        Event::RunCompleted { output } => output.clone(),
        _ => None,
    });
    let terminal_success = events.iter().any(|event| {
        matches!(
            &event.event,
            Event::RunCompleted {
                output: Some(output)
            } if !output.trim().is_empty()
        )
    }) && !events
        .iter()
        .any(|event| matches!(event.event, Event::RunFailed { .. }));
    let typed_completion_accepted = events.iter().any(|event| {
        matches!(
            event.event,
            Event::TerminalControlTransition {
                reason: TerminalControllerTransitionReason::CompletionAccepted,
                ..
            }
        )
    });
    let terminal_event_valid = terminal_success
        && (config.terminal_controller_policy == TerminalControllerPolicy::AdvisoryV18
            || typed_completion_accepted);
    let tool_calls = events
        .iter()
        .filter(|event| matches!(event.event, Event::ToolCallRequested { .. }))
        .count();
    let tool_errors = events
        .iter()
        .filter(|event| matches!(event.event, Event::ToolCallCompleted { is_error: true, .. }))
        .count();
    let named_calls = |name: &str| {
        events
            .iter()
            .filter(|event| {
                matches!(&event.event, Event::ToolCallRequested { name: item, .. } if item == name)
            })
            .count()
    };
    let input_tokens = provider_calls.iter().map(|call| call.input_tokens).sum();
    let cached_input_tokens = provider_calls
        .iter()
        .map(|call| call.cached_input_tokens)
        .sum();
    let cache_reset_count = provider_calls
        .windows(2)
        .filter(|calls| calls[0].cached_input_tokens > 0 && calls[1].cached_input_tokens == 0)
        .count();
    let pointer_transition_cache_reset_count = provider_calls
        .windows(2)
        .filter(|calls| {
            calls[0].cached_input_tokens > 0
                && calls[1].cached_input_tokens == 0
                && calls[1].memory_pointer_entries > calls[0].memory_pointer_entries
        })
        .count();
    let gc_quality_gate = build_gc_quality_gate(
        config.strategy,
        &provider_calls,
        &pointer_gc_admission_observations,
        &auto_hydration_observations,
    );
    HarborAgentReport {
        schema_version: REPORT_SCHEMA,
        strategy: config.strategy,
        terminal_controller_policy: config.terminal_controller_policy,
        public_validation_profile: config.public_validation_profile,
        model: config.model.clone(),
        api_type: config.api_type,
        compaction_strategy: match config.strategy {
            Strategy::B0 | Strategy::B2 => RuntimeCompactionStrategy::Disabled,
            Strategy::Pgc => RuntimeCompactionStrategy::PointerGc,
            Strategy::Fbgc => RuntimeCompactionStrategy::FileBackedGc,
            Strategy::Capc => RuntimeCompactionStrategy::Disabled,
        },
        pointer_gc_enabled: matches!(config.strategy, Strategy::Pgc | Strategy::Fbgc),
        pointer_gc_checkpoint_batches: config.checkpoint_batches,
        pointer_gc_admission_policy: config.pointer_gc_admission_policy,
        pgc_effort: config.pgc_effort,
        pgc_continuation_probability_bps: config.pgc_continuation_probability_bps,
        pgc_cached_input_cost_bps: config.pgc_cached_input_cost_bps,
        pointer_gc_admission_checks: pointer_gc_admission_observations.len(),
        pointer_gc_admissions: pointer_gc_admission_observations
            .iter()
            .filter(|observation| observation.admitted)
            .count(),
        pointer_gc_admission_observations,
        auto_hydration_count: auto_hydration_observations.len(),
        auto_hydrated_bytes: auto_hydration_observations
            .iter()
            .map(|observation| observation.hydrated_bytes)
            .sum(),
        auto_hydration_observations,
        gc_quality_gate,
        max_model_steps: config.max_steps,
        elapsed_ms: saturating_u64(started.elapsed().as_millis()),
        terminal_success,
        terminal_event_valid,
        final_output,
        input_tokens,
        uncached_input_tokens: input_tokens.saturating_sub(cached_input_tokens),
        cached_input_tokens,
        cache_creation_input_tokens: provider_calls
            .iter()
            .map(|call| call.cache_creation_input_tokens)
            .sum(),
        output_tokens: provider_calls.iter().map(|call| call.output_tokens).sum(),
        reasoning_output_tokens: provider_calls
            .iter()
            .map(|call| call.reasoning_output_tokens)
            .sum(),
        peak_input_tokens: provider_calls
            .iter()
            .map(|call| call.input_tokens)
            .max()
            .unwrap_or_default(),
        peak_model_input_bytes: provider_calls
            .iter()
            .map(|call| call.model_input_bytes)
            .max()
            .unwrap_or_default(),
        cache_reset_count,
        pointer_transition_cache_reset_count,
        provider_latency_ms: provider_calls.iter().map(|call| call.latency_ms).sum(),
        tool_calls,
        tool_errors,
        memory_search_calls: named_calls("memory_search"),
        memory_read_calls: named_calls("memory_read"),
        memory_pointer_appearances: provider_calls
            .iter()
            .map(|call| call.memory_pointer_entries)
            .sum(),
        provider_calls,
        events,
    }
}

fn build_gc_quality_gate(
    strategy: Strategy,
    provider_calls: &[ProviderCallObservation],
    admission_observations: &[PointerGcAdmissionObservation],
    hydration_observations: &[AutoHydrationObservation],
) -> GcQualityGate {
    let first_admission_model_step = admission_observations
        .iter()
        .filter(|observation| observation.admitted)
        .map(|observation| observation.model_step)
        .min();
    let hydration_steps = hydration_observations
        .iter()
        .map(|observation| observation.model_step)
        .collect::<Vec<_>>();
    let pointer_projection_provider_calls = provider_calls
        .iter()
        .filter(|call| call.memory_pointer_entries > 0)
        .count();
    let substitutive_projection_transitions = provider_calls
        .windows(2)
        .filter(|calls| {
            calls[1].memory_pointer_entries > calls[0].memory_pointer_entries
                && calls[1].continuation_item_bytes < calls[0].continuation_item_bytes
        })
        .count();
    evaluate_gc_quality_gate(
        strategy,
        provider_calls.len(),
        first_admission_model_step,
        &hydration_steps,
        pointer_projection_provider_calls,
        substitutive_projection_transitions,
    )
}

fn evaluate_gc_quality_gate(
    strategy: Strategy,
    total_provider_calls: usize,
    first_admission_model_step: Option<usize>,
    hydration_steps: &[usize],
    pointer_projection_provider_calls: usize,
    substitutive_projection_transitions: usize,
) -> GcQualityGate {
    let applicable = strategy == Strategy::Fbgc;
    let first_admission_fraction_bps = first_admission_model_step.and_then(|model_step| {
        (total_provider_calls > 0).then(|| {
            let numerator = model_step.saturating_mul(10_000);
            u32::try_from(numerator.div_ceil(total_provider_calls)).unwrap_or(u32::MAX)
        })
    });
    let post_admission_provider_calls = first_admission_model_step
        .map(|model_step| total_provider_calls.saturating_sub(model_step.saturating_sub(1)))
        .unwrap_or_default();
    let first_post_admission_archive_read_model_step =
        first_admission_model_step.and_then(|model_step| {
            hydration_steps
                .iter()
                .copied()
                .filter(|hydration_step| *hydration_step > model_step)
                .min()
        });
    let post_admission_archive_read_count = first_admission_model_step
        .map(|model_step| {
            hydration_steps
                .iter()
                .filter(|hydration_step| **hydration_step > model_step)
                .count()
        })
        .unwrap_or_default();
    let early_admission_passed = applicable
        && first_admission_fraction_bps
            .is_some_and(|fraction| fraction <= GC_GATE_MAX_FIRST_ADMISSION_FRACTION_BPS);
    let reuse_window_passed =
        applicable && post_admission_provider_calls >= GC_GATE_MIN_POST_ADMISSION_PROVIDER_CALLS;
    let archive_reread_passed = applicable && post_admission_archive_read_count > 0;
    let pointer_projection_passed = applicable && pointer_projection_provider_calls > 0;
    let substitutive_projection_passed = applicable && substitutive_projection_transitions > 0;

    GcQualityGate {
        applicable,
        max_first_admission_fraction_bps: GC_GATE_MAX_FIRST_ADMISSION_FRACTION_BPS,
        min_post_admission_provider_calls: GC_GATE_MIN_POST_ADMISSION_PROVIDER_CALLS,
        require_post_admission_archive_read: false,
        require_pointer_projection: true,
        require_substitutive_projection: true,
        first_admission_model_step,
        first_admission_fraction_bps,
        post_admission_provider_calls,
        post_admission_archive_read_count,
        first_post_admission_archive_read_model_step,
        pointer_projection_provider_calls,
        substitutive_projection_transitions,
        early_admission_passed,
        reuse_window_passed,
        archive_reread_passed,
        pointer_projection_passed,
        substitutive_projection_passed,
        passed: early_admission_passed
            && reuse_window_passed
            && pointer_projection_passed
            && substitutive_projection_passed,
    }
}

fn shell_definition() -> ToolDefinition {
    ToolDefinition {
        name: "shell".to_owned(),
        description: "Execute one shell command inside the isolated Terminal-Bench environment. The default working directory is /app. Use bounded inspection commands and run the task's tests before finishing.".to_owned(),
        input_schema: serde_json::json!({
            "type": "object",
            "properties": {
                "command": { "type": "string", "description": "Shell command to execute" },
                "cwd": { "type": "string", "description": "Absolute working directory, normally /app" },
                "timeout_sec": { "type": "integer", "minimum": 1, "maximum": 900 }
            },
            "required": ["command"],
            "additionalProperties": false
        }),
        strict: Some(true),
    }
}

fn public_validation_definition() -> ToolDefinition {
    ToolDefinition {
        name: PUBLIC_VALIDATE_TOOL_NAME.to_owned(),
        description: "Run the frozen public validation for this benchmark task. This is the only validation that grants typed completion eligibility. Fix any reported failure, then invoke this tool again."
            .to_owned(),
        input_schema: serde_json::json!({
            "type": "object",
            "properties": {},
            "additionalProperties": false
        }),
        strict: Some(true),
    }
}

fn memory_search_definition() -> ToolDefinition {
    ToolDefinition {
        name: "memory_search".to_owned(),
        description: "Search archived older tool evidence by command, path, error, or output fragment. Returns paths for memory_read.".to_owned(),
        input_schema: serde_json::json!({
            "type": "object",
            "properties": {
                "query": { "type": "string" },
                "limit": { "type": "integer", "minimum": 1, "maximum": 10 }
            },
            "required": ["query"],
            "additionalProperties": false
        }),
        strict: Some(true),
    }
}

fn memory_read_definition() -> ToolDefinition {
    ToolDefinition {
        name: "memory_read".to_owned(),
        description: "Load exact archived runtime evidence from a path returned by memory_search."
            .to_owned(),
        input_schema: serde_json::json!({
            "type": "object",
            "properties": { "path": { "type": "string" } },
            "required": ["path"],
            "additionalProperties": false
        }),
        strict: Some(true),
    }
}

fn terminal_instruction(instruction: &str, require_public_validation: bool) -> String {
    let validation_instruction = if require_public_validation {
        " Run your own focused checks while working. Every successful validation shell command is automatically followed by the same frozen public validation, so a successful validation result grants completion eligibility only when that public check also passes. You may also call runtime_validate with no arguments to run it directly."
    } else {
        ""
    };
    format!(
        "You are operating inside an isolated Linux task environment. Complete the task using the shell tool. Inspect existing files before editing, preserve task constraints, and run relevant validation. Before invoking any program that might mutate, normalize, checkpoint, or delete an input, make a byte-for-byte backup of the input and all related sidecar files; database clients are not guaranteed to be read-only even for SELECT queries. The shell boundary also creates an automatic safety copy of database inputs at {INPUT_SNAPSHOT_PATH} before the first command; if an original sidecar disappears, immediately restore or analyze its copy there instead of searching the filesystem. Keep commands and output bounded: inspect binary data with targeted byte ranges, metadata, or short scripts instead of dumping whole files. Treat investigation as a budget: after at most twelve read-only shell calls, make the best justified change or create the required deliverable, then test it and iterate from concrete failures. Do not merely explain a solution or spend the whole run investigating: make the required changes in the environment.{validation_instruction} After the task requirements and validation are complete, call runtime_complete by itself with a concise final status; do not continue optional investigation.\n\n{instruction}"
    )
}

fn send_output(output: &BridgeOutput<'_>) -> Result<(), Box<dyn Error>> {
    let mut stdout = std::io::stdout().lock();
    writeln!(stdout, "{}", serde_json::to_string(output)?)?;
    stdout.flush()?;
    Ok(())
}

fn read_input() -> Result<BridgeInput, Box<dyn Error>> {
    let mut line = String::new();
    std::io::stdin().lock().read_line(&mut line)?;
    if line.is_empty() {
        return Err("bridge closed before start".into());
    }
    Ok(serde_json::from_str(&line)?)
}

fn parse_config() -> Result<Config, Box<dyn Error>> {
    let arguments: Vec<String> = env::args().skip(1).collect();
    let mut strategy = None;
    let mut report = None;
    let mut model = None;
    let mut base_url = None;
    let mut api_type = public_env(["STRUCTURE_API_TYPE", "API_TYPE"])
        .map(|value| value.parse())
        .transpose()?
        .unwrap_or(ApiType::OpenAiChatCompletions);
    let mut max_steps = DEFAULT_MAX_STEPS;
    let mut max_tokens = DEFAULT_MAX_TOKENS;
    let mut checkpoint_batches = DEFAULT_CHECKPOINT_BATCHES;
    let mut pgc_effort = public_env(["COMPACTION_EFFORT", "PGC_EFFORT"])
        .map(|value| parse(&value, "COMPACTION_EFFORT"))
        .transpose()?
        .unwrap_or(DEFAULT_PGC_EFFORT);
    let mut pgc_continuation_probability_bps = public_env(["PGC_CONTINUATION_PROBABILITY_BPS"])
        .map(|value| parse(&value, "PGC_CONTINUATION_PROBABILITY_BPS"))
        .transpose()?
        .unwrap_or(DEFAULT_PGC_CONTINUATION_PROBABILITY_BPS);
    let mut pgc_cached_input_cost_bps = public_env(["PGC_CACHED_INPUT_COST_BPS"])
        .map(|value| parse(&value, "PGC_CACHED_INPUT_COST_BPS"))
        .transpose()?
        .unwrap_or(DEFAULT_PGC_CACHED_INPUT_COST_BPS);
    let mut pointer_gc_admission_policy = PointerGcAdmissionPolicy::Profitability;
    let mut thinking_enabled = public_env(["STRUCTURE_THINKING", "THINKING"])
        .map(|value| parse_bool(&value, "STRUCTURE_THINKING"))
        .transpose()?
        .unwrap_or(true);
    let mut terminal_controller_policy = TerminalControllerPolicy::AdvisoryV18;
    let mut public_validation_profile = None;
    let mut index = 0;
    while index < arguments.len() {
        let option = &arguments[index];
        match option.as_str() {
            "--strategy" => {
                strategy = Some(Strategy::parse(value(&arguments, &mut index, option)?)?)
            }
            "--report" => report = Some(PathBuf::from(value(&arguments, &mut index, option)?)),
            "--model" => model = Some(value(&arguments, &mut index, option)?.to_owned()),
            "--base-url" => base_url = Some(value(&arguments, &mut index, option)?.to_owned()),
            "--api-type" => api_type = value(&arguments, &mut index, option)?.parse()?,
            "--max-steps" => max_steps = parse(value(&arguments, &mut index, option)?, option)?,
            "--max-tokens" => max_tokens = parse(value(&arguments, &mut index, option)?, option)?,
            "--checkpoint-batches" => {
                checkpoint_batches = parse(value(&arguments, &mut index, option)?, option)?
            }
            "--pgc-effort" => pgc_effort = parse(value(&arguments, &mut index, option)?, option)?,
            "--pgc-continuation-probability-bps" => {
                pgc_continuation_probability_bps =
                    parse(value(&arguments, &mut index, option)?, option)?
            }
            "--pgc-cached-input-cost-bps" => {
                pgc_cached_input_cost_bps = parse(value(&arguments, &mut index, option)?, option)?
            }
            "--pointer-gc-admission-policy" => {
                pointer_gc_admission_policy = match value(&arguments, &mut index, option)? {
                    "profitability" => PointerGcAdmissionPolicy::Profitability,
                    "mechanism_qualification" => PointerGcAdmissionPolicy::MechanismQualification,
                    value => {
                        return Err(format!(
                            "invalid PointerGC admission policy {value}; expected profitability or mechanism_qualification"
                        )
                        .into());
                    }
                }
            }
            "--thinking" => {
                thinking_enabled = parse_bool(value(&arguments, &mut index, option)?, option)?
            }
            "--terminal-controller" => {
                terminal_controller_policy = match value(&arguments, &mut index, option)? {
                    "advisory_v18" => TerminalControllerPolicy::AdvisoryV18,
                    "typed_completion_v1" => TerminalControllerPolicy::TypedCompletionV1,
                    "typed_completion_auto_v1" => TerminalControllerPolicy::TypedCompletionAutoV1,
                    "typed_completion_auto_v2" => TerminalControllerPolicy::TypedCompletionAutoV2,
                    value => {
                        return Err(format!(
                            "invalid terminal controller {value}; expected advisory_v18, typed_completion_v1, typed_completion_auto_v1, or typed_completion_auto_v2"
                        )
                        .into());
                    }
                }
            }
            "--public-validation-profile" => {
                public_validation_profile = Some(PublicValidationProfile::parse(value(
                    &arguments, &mut index, option,
                )?)?)
            }
            "--no-thinking" => thinking_enabled = false,
            _ => return Err(format!("unknown option {option}").into()),
        }
        index += 1;
    }
    if max_steps == 0 || max_tokens == 0 || checkpoint_batches == 0 || pgc_effort == 0 {
        return Err(
            "max steps, max tokens, checkpoint batches, and PGC effort must be positive".into(),
        );
    }
    if pgc_continuation_probability_bps > 10_000 {
        return Err("PGC continuation probability must be between 0 and 10000 bps".into());
    }
    if pgc_cached_input_cost_bps > 10_000 {
        return Err("PGC cached-input cost must be between 0 and 10000 bps".into());
    }
    Ok(Config {
        strategy: strategy.ok_or("--strategy is required")?,
        report: report.ok_or("--report is required")?,
        model: model
            .or_else(|| match api_type {
                ApiType::AnthropicMessages => public_env(["ANTHROPIC_MODEL"]),
                _ => public_env(["OPENAI_MODEL", "OPENAI__MODEL"]),
            })
            .ok_or("model is required")?
            .trim_matches('"')
            .to_owned(),
        base_url: base_url
            .or_else(|| match api_type {
                ApiType::AnthropicMessages => public_env(["ANTHROPIC_BASE_URL"]),
                _ => public_env(["OPENAI_BASE_URL", "OPENAI__BASE_URL"]),
            })
            .ok_or("base URL is required")?
            .trim_matches('"')
            .to_owned(),
        api_key: match api_type {
            ApiType::AnthropicMessages => secret_env(["ANTHROPIC_API_KEY"])?,
            _ => secret_env(["LONGCAT_API_KEY", "OPENAI_API_KEY", "OPENAI__API_KEY"])?,
        }
        .trim_matches('"')
        .to_owned(),
        api_type,
        max_steps,
        max_tokens,
        checkpoint_batches,
        pgc_effort,
        pgc_continuation_probability_bps,
        pgc_cached_input_cost_bps,
        pointer_gc_admission_policy,
        thinking_enabled,
        terminal_controller_policy,
        public_validation_profile,
    })
}

fn parse_bool(raw: &str, option: &str) -> Result<bool, Box<dyn Error>> {
    match raw.trim().to_ascii_lowercase().as_str() {
        "1" | "true" | "yes" | "on" => Ok(true),
        "0" | "false" | "no" | "off" => Ok(false),
        _ => Err(format!("invalid boolean for {option}: {raw}").into()),
    }
}

fn public_env<const N: usize>(names: [&str; N]) -> Option<String> {
    names
        .into_iter()
        .find_map(|name| env::var(name).ok().filter(|value| !value.trim().is_empty()))
}

fn secret_env<const N: usize>(names: [&str; N]) -> Result<String, Box<dyn Error>> {
    public_env(names).ok_or_else(|| "provider API key is required in the environment".into())
}

fn value<'a>(
    arguments: &'a [String],
    index: &mut usize,
    option: &str,
) -> Result<&'a str, Box<dyn Error>> {
    *index += 1;
    arguments
        .get(*index)
        .map(String::as_str)
        .ok_or_else(|| format!("missing value for {option}").into())
}

fn parse<T>(raw: &str, option: &str) -> Result<T, Box<dyn Error>>
where
    T: std::str::FromStr,
    T::Err: Display,
{
    raw.parse()
        .map_err(|error| format!("invalid value for {option}: {error}").into())
}

fn saturating_u64(value: u128) -> u64 {
    u64::try_from(value).unwrap_or(u64::MAX)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn report_for_terminal_event(event: Event) -> HarborAgentReport {
        let config = Config {
            strategy: Strategy::B0,
            report: PathBuf::from("target/test-report.json"),
            model: "test-model".to_owned(),
            base_url: "https://example.invalid/v1".to_owned(),
            api_key: "test-key".to_owned(),
            api_type: ApiType::OpenAiChatCompletions,
            max_steps: 8,
            max_tokens: 128,
            checkpoint_batches: 4,
            pgc_effort: 1,
            pgc_continuation_probability_bps: 7_500,
            pgc_cached_input_cost_bps: 0,
            pointer_gc_admission_policy: PointerGcAdmissionPolicy::Profitability,
            thinking_enabled: false,
            terminal_controller_policy: TerminalControllerPolicy::AdvisoryV18,
            public_validation_profile: None,
        };
        build_report(
            &config,
            Instant::now(),
            vec![EventEnvelope::new(
                structure_protocol::EventMetadata {
                    event_id: structure_protocol::EventId::new("event-1"),
                    command_id: structure_protocol::CommandId::new("command-1"),
                    workspace_id: WorkspaceId::new("workspace-1"),
                    session_id: structure_protocol::SessionId::new("session-1"),
                    run_id: Some(RunId::new("run-1")),
                    sequence: 1,
                    occurred_at_ms: 0,
                },
                event,
            )],
            Vec::new(),
            Vec::new(),
            Vec::new(),
        )
    }

    #[test]
    fn output_truncation_keeps_both_ends() {
        let value = format!("HEAD{}TAIL", "x".repeat(MAX_TOOL_OUTPUT_CHARS));
        let truncated = truncate_output(value);
        assert!(truncated.starts_with("HEAD"));
        assert!(truncated.ends_with("TAIL"));
        assert!(truncated.contains("[truncated"));
    }

    #[test]
    fn shell_interactions_are_classified_before_memory_policy() {
        for (command, expected) in [
            ("cat pyproject.toml", ToolInteractionKind::Inspection),
            ("grep -R TODO src", ToolInteractionKind::Inspection),
            (
                "python setup.py build_ext --inplace",
                ToolInteractionKind::Build,
            ),
            (
                "python -m pip install cython",
                ToolInteractionKind::Dependency,
            ),
            ("pytest -q", ToolInteractionKind::Validation),
            (
                "python3 -c \"import package; assert package.check()\"",
                ToolInteractionKind::Validation,
            ),
            (
                "python3 -c \"from pathlib import Path; Path('x').write_text('x'); print('done')\"",
                ToolInteractionKind::Mutation,
            ),
            ("sed -i 's/old/new/' file", ToolInteractionKind::Mutation),
            ("python scripts/generate.py", ToolInteractionKind::Generic),
        ] {
            assert_eq!(classify_shell_interaction(command), expected, "{command}");
        }
    }

    #[test]
    fn shell_classification_uses_command_boundaries_not_argument_substrings() {
        assert_eq!(
            classify_shell_interaction(
                "pip install --no-cache-dir setuptools wheel 'cython<3.1' pytest"
            ),
            ToolInteractionKind::Dependency
        );
        assert_eq!(
            classify_shell_interaction("grep -rln TODO /app"),
            ToolInteractionKind::Inspection
        );
        assert_eq!(
            classify_shell_interaction("pip install pytest && python -m pytest -q"),
            ToolInteractionKind::MutationWithValidation
        );
    }

    #[test]
    fn typed_harbor_runner_routes_validation_through_the_public_hook() {
        let runner = HarborBridgeRunner {
            input_snapshot_initialized: false,
            public_validation_profile: Some(PublicValidationProfile::BuildCythonExt),
            require_public_validation: true,
        };
        let call = |name: &str, arguments| ToolCallItem {
            id: None,
            call_id: "call-1".to_owned(),
            name: name.to_owned(),
            arguments,
            provider_state: None,
        };
        assert_eq!(
            runner.classify(&call("shell", serde_json::json!({"command": "pytest -q"}))),
            ToolInteractionKind::Validation
        );
        assert_eq!(
            runner.classify(&call(PUBLIC_VALIDATE_TOOL_NAME, serde_json::json!({}))),
            ToolInteractionKind::Validation
        );
        assert!(tool_interaction_requests_validation(
            ToolInteractionKind::MutationWithValidation
        ));
        assert!(!tool_interaction_requests_validation(
            ToolInteractionKind::Dependency
        ));
    }

    #[test]
    fn read_only_inline_validation_and_ordered_pipelines_preserve_final_semantics() {
        let read_only = r#"python3 -B - <<'EOF'
import json
data = json.load(open('/app/recovered.json'))
blob = open('/app/main.db', 'rb').read()
assert len(data) == 11 and blob
EOF"#;
        assert_eq!(
            classify_shell_interaction(read_only),
            ToolInteractionKind::Validation
        );

        let sandbox_copy = r#"python3 -c "
import json, sqlite3, shutil
shutil.copytree('/app', '/tmp/validation', dirs_exist_ok=True)
rows = sqlite3.connect('/tmp/validation/main.db').execute('select 1').fetchall()
assert json.load(open('/app/recovered.json')) and rows
""#;
        assert_eq!(
            classify_shell_interaction(sandbox_copy),
            ToolInteractionKind::MutationWithValidation
        );
        assert_eq!(
            classify_shell_interaction("rm -f stale && pytest -q"),
            ToolInteractionKind::MutationWithValidation
        );
        assert_eq!(
            classify_shell_interaction("pytest -q && rm -f stale"),
            ToolInteractionKind::Mutation
        );
    }

    #[test]
    fn validation_build_and_dependency_pipelines_require_strict_status() {
        for command in [
            "pytest -q 2>&1 | tail -20",
            "python setup.py build_ext --inplace 2>&1 | tail -100",
            "pip install cython 2>&1 | tail -5",
            "sed -i 's/old/new/' file && pytest -q | tail -20",
        ] {
            assert!(shell_requires_strict_pipeline(command), "{command}");
        }
        assert!(!shell_requires_strict_pipeline("grep -R TODO src | head"));
    }

    #[test]
    fn inline_validation_source_does_not_match_build_substrings() {
        let command = r#"cd /app/pyknotid && python3 -c "
import pyknotid.make as mk
result = mk.three_twist(num_points=100)
assert result is not None
" 2>&1"#;

        assert_eq!(
            classify_shell_interaction(command),
            ToolInteractionKind::Validation
        );
    }

    #[test]
    fn python_heredoc_without_dash_is_classified_as_validation() {
        let command = r#"cd /app/pyknotid && python3 << 'EOF'
import planarity
assert planarity.PGraph() is not None
EOF"#;

        assert_eq!(
            classify_shell_interaction(command),
            ToolInteractionKind::Validation
        );
    }

    #[test]
    fn shell_file_redirection_is_mutation_but_descriptor_merging_is_not() {
        assert_eq!(
            classify_shell_interaction(
                "cat > /tmp/fix.py << 'EOF'\nprint('fix')\nEOF\npython /tmp/fix.py"
            ),
            ToolInteractionKind::Mutation
        );
        assert_eq!(
            classify_shell_interaction("pytest -q 2>&1 | tail -20"),
            ToolInteractionKind::Validation
        );
        assert!(!has_shell_output_redirection("python3 -c \"print(2 > 1)\""));
    }

    #[test]
    fn shell_classification_protects_real_mutations_not_dev_null_routing() {
        assert_eq!(
            classify_shell_interaction(
                "which sqlite3; sqlite3 --version 2>/dev/null; python3 -c \"import sqlite3; print(sqlite3.sqlite_version)\""
            ),
            ToolInteractionKind::Generic
        );
        assert_eq!(
            classify_shell_interaction("cp -a /app/main.db /tmp/main.db.bak && ls -la /tmp"),
            ToolInteractionKind::Mutation
        );
        assert_eq!(
            classify_shell_interaction(
                "python3 - <<'EOF'\ndata = open('/tmp/in','rb').read()\nopen('/app/out','wb').write(data)\nprint(len(data))\nEOF"
            ),
            ToolInteractionKind::Mutation
        );
        assert!(!has_shell_output_redirection("pytest -q 2>/dev/null"));
    }

    #[test]
    fn inline_version_probe_does_not_arm_typed_completion() {
        assert_eq!(
            classify_shell_interaction("python3 -c \"import numpy; print(numpy.__version__)\""),
            ToolInteractionKind::Generic
        );
        assert_eq!(
            classify_shell_interaction("python3 -c \"import numpy; assert numpy.__version__\""),
            ToolInteractionKind::Validation
        );
    }

    #[test]
    fn strategy_parser_accepts_only_the_experiment_arms() {
        assert_eq!(Strategy::parse("b0").expect("B0 is valid"), Strategy::B0);
        assert_eq!(Strategy::parse("b2").expect("B2 is valid"), Strategy::B2);
        assert_eq!(Strategy::parse("pgc").expect("PGC is valid"), Strategy::Pgc);
        assert_eq!(
            Strategy::parse("fbgc").expect("FBGC is valid"),
            Strategy::Fbgc
        );
        assert!(Strategy::parse("S").is_err());
    }

    #[test]
    fn gc_quality_gate_requires_early_reused_and_substitutive_pointer_projection() {
        let passed = evaluate_gc_quality_gate(Strategy::Fbgc, 10, Some(6), &[], 5, 2);
        assert_eq!(passed.first_admission_fraction_bps, Some(6_000));
        assert_eq!(passed.post_admission_provider_calls, 5);
        assert_eq!(passed.post_admission_archive_read_count, 0);
        assert_eq!(passed.first_post_admission_archive_read_model_step, None);
        assert!(passed.early_admission_passed);
        assert!(passed.reuse_window_passed);
        assert!(!passed.archive_reread_passed);
        assert!(passed.pointer_projection_passed);
        assert!(passed.substitutive_projection_passed);
        assert!(passed.passed);

        let late = evaluate_gc_quality_gate(Strategy::Fbgc, 10, Some(7), &[8], 4, 1);
        assert_eq!(late.first_admission_fraction_bps, Some(7_000));
        assert!(!late.early_admission_passed);
        assert!(late.reuse_window_passed);
        assert!(late.archive_reread_passed);
        assert!(!late.passed);

        let short = evaluate_gc_quality_gate(Strategy::Fbgc, 5, Some(3), &[4], 2, 1);
        assert!(short.early_admission_passed);
        assert_eq!(short.post_admission_provider_calls, 3);
        assert!(!short.reuse_window_passed);
        assert!(short.archive_reread_passed);
        assert!(!short.passed);

        let additive = evaluate_gc_quality_gate(Strategy::Fbgc, 10, Some(6), &[7], 5, 0);
        assert!(additive.pointer_projection_passed);
        assert!(!additive.substitutive_projection_passed);
        assert!(!additive.passed);
    }

    #[test]
    fn gc_quality_gate_is_not_applicable_without_file_backed_gc() {
        let disabled = evaluate_gc_quality_gate(Strategy::B0, 10, Some(2), &[3], 4, 1);
        assert!(!disabled.applicable);
        assert!(!disabled.passed);

        let missing = evaluate_gc_quality_gate(Strategy::Fbgc, 10, None, &[3], 0, 0);
        assert_eq!(missing.first_admission_fraction_bps, None);
        assert_eq!(missing.post_admission_provider_calls, 0);
        assert_eq!(missing.post_admission_archive_read_count, 0);
        assert!(!missing.passed);
    }

    #[test]
    fn harbor_report_rejects_failed_or_empty_terminal_events() {
        let failed = report_for_terminal_event(Event::RunFailed {
            message: "model_output_truncated: output limit reached".to_owned(),
        });
        assert!(!failed.terminal_success);
        assert!(failed.final_output.is_none());

        let empty = report_for_terminal_event(Event::RunCompleted { output: None });
        assert!(!empty.terminal_success);

        let completed = report_for_terminal_event(Event::RunCompleted {
            output: Some("done".to_owned()),
        });
        assert!(completed.terminal_success);
        assert_eq!(completed.final_output.as_deref(), Some("done"));
        assert_eq!(completed.schema_version, "structure.harbor-agent/v13");
        assert_eq!(completed.reasoning_output_tokens, 0);
    }
}
