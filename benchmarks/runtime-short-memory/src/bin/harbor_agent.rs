use std::env;
use std::error::Error;
use std::fmt::Display;
use std::io::{BufRead, Write};
use std::path::{Path, PathBuf};
use std::time::Instant;

use serde::{Deserialize, Serialize};
use structure_model::{ContentBlock, ToolDefinition, ToolResultItem};
use structure_protocol::{
    Command, CommandEnvelope, CommandId, Event, EventEnvelope, RunId, WorkspaceId,
};
use structure_provider::{ApiModelProvider, ApiProviderConfig, ApiType};
use structure_runner::{
    RunnerEnvironment, RunnerError, RunnerOutput, ToolExecutionRequest, ToolExecutionResult,
};
use structure_runtime::{
    AutoHydrationObservation, CoreRuntime, PointerGcAdmissionObservation, PointerGcObservationSink,
    RuntimeArchiveStore, ShortMemoryPolicy,
};
use structure_session::SessionManager;
use structure_short_memory_benchmark::{
    ProviderCallObservation, ProviderRecorder, RecordingProvider,
};

const REPORT_SCHEMA: &str = "structure.harbor-agent/v5";
const DEFAULT_MAX_STEPS: usize = 128;
const DEFAULT_MAX_TOKENS: u32 = 8_192;
const DEFAULT_CHECKPOINT_BATCHES: usize = 8;
const DEFAULT_PGC_EFFORT: usize = 1;
const DEFAULT_PGC_CONTINUATION_PROBABILITY_BPS: u32 = 7_500;
const MAX_TOOL_OUTPUT_CHARS: usize = 8_000;
const INPUT_SNAPSHOT_PATH: &str = "/tmp/structure-input-snapshot";
const INPUT_SNAPSHOT_COMMAND: &str = "set -eu; snapshot=/tmp/structure-input-snapshot; mkdir -p \"$snapshot\"; find /app -maxdepth 1 -type f \\( -name '*.db' -o -name '*.db-*' -o -name '*.sqlite' -o -name '*.sqlite-*' -o -name '*.wal' \\) -exec cp -p {} \"$snapshot\"/ \\;";

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "UPPERCASE")]
enum Strategy {
    B0,
    Pgc,
}

impl Strategy {
    fn parse(raw: &str) -> Result<Self, Box<dyn Error>> {
        match raw.to_ascii_uppercase().as_str() {
            "B0" => Ok(Self::B0),
            "PGC" => Ok(Self::Pgc),
            _ => Err(format!("invalid strategy {raw}; expected B0 or PGC").into()),
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
    max_steps: usize,
    max_tokens: u32,
    checkpoint_batches: usize,
    pgc_effort: usize,
    pgc_continuation_probability_bps: u32,
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
    },
    Done {
        report: &'a str,
    },
}

#[derive(Debug, Default)]
struct HarborBridgeRunner {
    input_snapshot_initialized: bool,
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
    async fn execute(
        &mut self,
        request: ToolExecutionRequest,
    ) -> Result<ToolExecutionResult, RunnerError> {
        if request.call.name != "shell" {
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
        let id = request.call.call_id.clone();
        let response = self.exchange(&BridgeOutput::Exec {
            id: &id,
            command,
            cwd,
            timeout_sec,
        })?;
        let BridgeInput::ExecResult {
            id: response_id,
            stdout,
            stderr,
            return_code,
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
struct HarborAgentReport {
    schema_version: &'static str,
    strategy: Strategy,
    model: String,
    pointer_gc_enabled: bool,
    pointer_gc_checkpoint_batches: usize,
    pgc_effort: usize,
    pgc_continuation_probability_bps: u32,
    pointer_gc_admission_checks: usize,
    pointer_gc_admissions: usize,
    pointer_gc_admission_observations: Vec<PointerGcAdmissionObservation>,
    auto_hydration_count: usize,
    auto_hydrated_bytes: usize,
    auto_hydration_observations: Vec<AutoHydrationObservation>,
    max_model_steps: usize,
    elapsed_ms: u64,
    terminal_success: bool,
    final_output: Option<String>,
    provider_calls: Vec<ProviderCallObservation>,
    input_tokens: u64,
    uncached_input_tokens: u64,
    cached_input_tokens: u64,
    output_tokens: u64,
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
            ApiType::OpenAiChatCompletions,
            config.api_key.clone(),
            config.base_url.clone(),
            config.model.clone(),
        )
        .with_max_tokens(config.max_tokens)
        .with_thinking(true)
        .with_request_timeout_secs(480)
        .with_raw_exchange_dir(config.report.with_file_name("provider-raw")),
    )?;
    let recorder = ProviderRecorder::with_snapshot_path(
        config.report.with_file_name("provider-calls.partial.json"),
    );
    let (policy, pointer_gc_enabled) = match config.strategy {
        Strategy::B0 => (ShortMemoryPolicy::full_replay(), false),
        Strategy::Pgc => (ShortMemoryPolicy::ttl_only(), true),
    };
    let archive_root = config
        .report
        .parent()
        .unwrap_or_else(|| Path::new("."))
        .join("runtime-memory");
    let mut runtime = CoreRuntime::with_memory_configuration(
        RecordingProvider::new(provider, recorder.clone()),
        HarborBridgeRunner::default(),
        policy,
        pointer_gc_enabled,
        RuntimeArchiveStore::File { root: archive_root },
    );
    runtime.set_pointer_gc_checkpoint_batches(config.checkpoint_batches);
    runtime.set_pointer_gc_effort(config.pgc_effort);
    runtime.set_pointer_gc_continuation_probability_bps(config.pgc_continuation_probability_bps);
    runtime.set_pointer_gc_observation_sink(PointerGcPartialRecorder::new(
        config
            .report
            .with_file_name("pointer-gc-admissions.partial.json"),
    ));
    runtime.set_max_model_steps_per_run(config.max_steps);
    runtime.set_tools(vec![
        shell_definition(),
        memory_search_definition(),
        memory_read_definition(),
    ]);

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
                    content: terminal_instruction(&instruction),
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
    HarborAgentReport {
        schema_version: REPORT_SCHEMA,
        strategy: config.strategy,
        model: config.model.clone(),
        pointer_gc_enabled: config.strategy == Strategy::Pgc,
        pointer_gc_checkpoint_batches: config.checkpoint_batches,
        pgc_effort: config.pgc_effort,
        pgc_continuation_probability_bps: config.pgc_continuation_probability_bps,
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
        max_model_steps: config.max_steps,
        elapsed_ms: saturating_u64(started.elapsed().as_millis()),
        terminal_success,
        final_output,
        input_tokens,
        uncached_input_tokens: input_tokens.saturating_sub(cached_input_tokens),
        cached_input_tokens,
        output_tokens: provider_calls.iter().map(|call| call.output_tokens).sum(),
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

fn terminal_instruction(instruction: &str) -> String {
    format!(
        "You are operating inside an isolated Linux task environment. Complete the task using the shell tool. Inspect existing files before editing, preserve task constraints, and run relevant validation. Before invoking any program that might mutate, normalize, checkpoint, or delete an input, make a byte-for-byte backup of the input and all related sidecar files; database clients are not guaranteed to be read-only even for SELECT queries. The shell boundary also creates an automatic safety copy of database inputs at {INPUT_SNAPSHOT_PATH} before the first command; if an original sidecar disappears, immediately restore or analyze its copy there instead of searching the filesystem. Keep commands and output bounded: inspect binary data with targeted byte ranges, metadata, or short scripts instead of dumping whole files. Treat investigation as a budget: after at most twelve read-only shell calls, make the best justified change or create the required deliverable, then test it and iterate from concrete failures. Do not merely explain a solution or spend the whole run investigating: make the required changes in the environment. When the task is complete, return a concise final status.\n\n{instruction}"
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
    let mut max_steps = DEFAULT_MAX_STEPS;
    let mut max_tokens = DEFAULT_MAX_TOKENS;
    let mut checkpoint_batches = DEFAULT_CHECKPOINT_BATCHES;
    let mut pgc_effort = public_env(["PGC_EFFORT"])
        .map(|value| parse(&value, "PGC_EFFORT"))
        .transpose()?
        .unwrap_or(DEFAULT_PGC_EFFORT);
    let mut pgc_continuation_probability_bps = public_env(["PGC_CONTINUATION_PROBABILITY_BPS"])
        .map(|value| parse(&value, "PGC_CONTINUATION_PROBABILITY_BPS"))
        .transpose()?
        .unwrap_or(DEFAULT_PGC_CONTINUATION_PROBABILITY_BPS);
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
    Ok(Config {
        strategy: strategy.ok_or("--strategy is required")?,
        report: report.ok_or("--report is required")?,
        model: model
            .or_else(|| public_env(["OPENAI_MODEL", "OPENAI__MODEL"]))
            .ok_or("model is required")?
            .trim_matches('"')
            .to_owned(),
        base_url: base_url
            .or_else(|| public_env(["OPENAI_BASE_URL", "OPENAI__BASE_URL"]))
            .ok_or("base URL is required")?
            .trim_matches('"')
            .to_owned(),
        api_key: secret_env(["LONGCAT_API_KEY", "OPENAI_API_KEY", "OPENAI__API_KEY"])?
            .trim_matches('"')
            .to_owned(),
        max_steps,
        max_tokens,
        checkpoint_batches,
        pgc_effort,
        pgc_continuation_probability_bps,
    })
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
            max_steps: 8,
            max_tokens: 128,
            checkpoint_batches: 4,
            pgc_effort: 1,
            pgc_continuation_probability_bps: 7_500,
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
    fn strategy_parser_accepts_only_the_experiment_arms() {
        assert_eq!(Strategy::parse("b0").expect("B0 is valid"), Strategy::B0);
        assert_eq!(Strategy::parse("pgc").expect("PGC is valid"), Strategy::Pgc);
        assert!(Strategy::parse("S").is_err());
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
    }
}
