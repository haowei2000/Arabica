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
    classify_shell_interaction, shell_classifier::inline_validation_position, truncate_output,
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
#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "UPPERCASE")]
enum Strategy {
    B0,
    B2,
    Pgc,
    Fbgc,
    Capc,
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
struct HarborBridgeRunner;

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
        if request.call.name != "shell" {
            return Err(RunnerError::new(format!(
                "unknown Harbor tool: {}",
                request.call.name
            )));
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
            strict_pipeline: shell_requires_strict_pipeline(command),
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
        HarborBridgeRunner,
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
    let tools = vec![
        shell_definition(),
        memory_search_definition(),
        memory_read_definition(),
        runtime_complete_tool_definition(),
    ];
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
        "You are operating inside an isolated Linux task environment. Complete the task using the shell tool. Inspect existing files before editing, preserve task constraints, and run relevant validation. Keep commands and output bounded. After the task requirements and validation are complete, call runtime_complete by itself with a concise final status.\n\n{instruction}"
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
                    value => {
                        return Err(format!(
                            "invalid PointerGC admission policy {value}; expected profitability"
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
