//! Controlled, paired comparison harness for PiAgent and Codex CLI.
//!
//! The harness keeps model execution opt-in. Planning, validation, preflight,
//! and summary generation are provider-free. Each paid trial runs in a fresh
//! fixture copy and retains hashes plus typed usage instead of raw transcripts.

use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};
use tokio::io::AsyncWriteExt;
use tokio::process::Command;

pub const CLI_COMPARISON_SUITE_SCHEMA: &str = "structure.cli-comparison-suite/2026-08";
pub const CLI_COMPARISON_MANIFEST_SCHEMA: &str = "structure.cli-comparison-manifest/2026-08";
pub const CLI_COMPARISON_REPORT_SCHEMA: &str = "structure.cli-comparison-report/2026-08";

#[derive(Clone, Copy, Debug, Deserialize, Eq, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum CliSurface {
    PiAgent,
    CodexCli,
}

impl CliSurface {
    const fn name(self) -> &'static str {
        match self {
            Self::PiAgent => "piagent",
            Self::CodexCli => "codex-cli",
        }
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct CliScenario {
    pub id: String,
    pub title: String,
    pub fixture: String,
    pub prompt: String,
    pub verifier: Vec<String>,
    #[serde(default)]
    pub allowed_changes: Vec<String>,
    #[serde(default)]
    pub required_output_markers: Vec<String>,
    #[serde(default)]
    pub forbidden_output_markers: Vec<String>,
    #[serde(default = "default_profile")]
    pub piagent_profile: String,
}

fn default_profile() -> String {
    "node-typescript".to_owned()
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct CliSuite {
    pub schema_version: String,
    pub suite_id: String,
    pub scenarios: Vec<CliScenario>,
}

impl CliSuite {
    pub fn validate(&self, suite_root: &Path) -> Result<(), String> {
        if self.schema_version != CLI_COMPARISON_SUITE_SCHEMA {
            return Err(format!("unsupported suite schema {}", self.schema_version));
        }
        if self.suite_id.trim().is_empty() || self.scenarios.is_empty() {
            return Err("suite id and at least one scenario are required".to_owned());
        }
        let mut ids = BTreeSet::new();
        for scenario in &self.scenarios {
            validate_identifier(&scenario.id, "scenario id")?;
            if !ids.insert(&scenario.id) {
                return Err(format!("duplicate scenario id {}", scenario.id));
            }
            if scenario.verifier.is_empty() {
                return Err(format!("scenario {} has no verifier", scenario.id));
            }
            resolve_inside(suite_root, &scenario.fixture, true)?;
            resolve_inside(suite_root, &scenario.prompt, false)?;
            let verifier = &scenario.verifier[0];
            if verifier.contains('/') {
                resolve_inside(suite_root, verifier, false)?;
            }
            for path in &scenario.allowed_changes {
                validate_relative_pattern(path)?;
            }
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct CliTrial {
    pub trial_id: String,
    pub scenario_id: String,
    pub surface: CliSurface,
    pub repeat: usize,
    pub sequence: usize,
    pub artifact_path: String,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct CliComparisonManifest {
    pub schema_version: String,
    pub claim_tier: String,
    pub suite_path: String,
    pub suite_digest: String,
    pub model: String,
    pub thinking: String,
    pub repetitions: usize,
    pub timeout_seconds: u64,
    pub seed: u64,
    pub codex_program: String,
    pub pi_program: String,
    pub piagent_package_root: String,
    pub trials: Vec<CliTrial>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CliComparisonPlan {
    pub model: String,
    pub thinking: String,
    pub repetitions: usize,
    pub timeout_seconds: u64,
    pub seed: u64,
    pub codex_program: String,
    pub pi_program: String,
    pub piagent_package_root: String,
}

pub fn create_cli_comparison_manifest(
    suite_path: &Path,
    plan: CliComparisonPlan,
) -> Result<CliComparisonManifest, String> {
    if plan.repetitions == 0 || plan.timeout_seconds == 0 {
        return Err("repetitions and timeout must be positive".to_owned());
    }
    let suite_path = suite_path
        .canonicalize()
        .map_err(|error| format!("cannot resolve suite path: {error}"))?;
    let suite_root = suite_path
        .parent()
        .ok_or_else(|| "suite path has no parent".to_owned())?;
    let suite: CliSuite = serde_json::from_slice(
        &std::fs::read(&suite_path).map_err(|error| format!("cannot read suite: {error}"))?,
    )
    .map_err(|error| format!("invalid suite JSON: {error}"))?;
    suite.validate(suite_root)?;
    let suite_digest = digest_tree(suite_root)?;
    let mut trials = Vec::new();
    let mut sequence = 0usize;
    for repeat in 1..=plan.repetitions {
        for scenario in &suite.scenarios {
            let reverse = stable_seed(plan.seed, &scenario.id, repeat) & 1 == 1;
            let surfaces = if reverse {
                [CliSurface::CodexCli, CliSurface::PiAgent]
            } else {
                [CliSurface::PiAgent, CliSurface::CodexCli]
            };
            for surface in surfaces {
                sequence += 1;
                let trial_id = format!("{}-r{repeat:02}-{}", scenario.id, surface.name());
                trials.push(CliTrial {
                    artifact_path: format!("trials/{trial_id}.json"),
                    trial_id,
                    scenario_id: scenario.id.clone(),
                    surface,
                    repeat,
                    sequence,
                });
            }
        }
    }
    Ok(CliComparisonManifest {
        schema_version: CLI_COMPARISON_MANIFEST_SCHEMA.to_owned(),
        claim_tier: "controlled-local-pilot".to_owned(),
        suite_path: suite_path.to_string_lossy().into_owned(),
        suite_digest,
        model: plan.model,
        thinking: plan.thinking,
        repetitions: plan.repetitions,
        timeout_seconds: plan.timeout_seconds,
        seed: plan.seed,
        codex_program: plan.codex_program,
        pi_program: plan.pi_program,
        piagent_package_root: plan.piagent_package_root,
        trials,
    })
}

impl CliComparisonManifest {
    pub fn validate(&self) -> Result<(), String> {
        if self.schema_version != CLI_COMPARISON_MANIFEST_SCHEMA {
            return Err(format!(
                "unsupported manifest schema {}",
                self.schema_version
            ));
        }
        if self.model.trim().is_empty() || self.thinking.trim().is_empty() {
            return Err("model and thinking level are required".to_owned());
        }
        let mut ids = BTreeSet::new();
        let mut artifacts = BTreeSet::new();
        for trial in &self.trials {
            if !ids.insert(&trial.trial_id) || !artifacts.insert(&trial.artifact_path) {
                return Err("trial ids and artifact paths must be unique".to_owned());
            }
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct ExecutableProbe {
    pub program: String,
    pub available: bool,
    pub version: Option<String>,
    pub error: Option<String>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct CliPreflightReport {
    pub manifest_valid: bool,
    pub suite_digest_matches: bool,
    pub codex: ExecutableProbe,
    pub pi: ExecutableProbe,
    pub piagent_package_ready: bool,
    pub ready: bool,
    pub blockers: Vec<String>,
}

pub async fn preflight_cli_comparison(manifest: &CliComparisonManifest) -> CliPreflightReport {
    let manifest_valid = manifest.validate().is_ok();
    let suite_digest_matches = Path::new(&manifest.suite_path)
        .parent()
        .and_then(|root| digest_tree(root).ok())
        .is_some_and(|digest| digest == manifest.suite_digest);
    let codex = probe(&manifest.codex_program).await;
    let pi = probe(&manifest.pi_program).await;
    let package_root = Path::new(&manifest.piagent_package_root);
    let piagent_package_ready = [
        "scripts/init-project.sh",
        "packages/piagent-core/extensions/piagent-guard.ts",
        "packages/piagent-core/skills",
    ]
    .iter()
    .all(|path| package_root.join(path).exists());
    let mut blockers = Vec::new();
    if !manifest_valid {
        blockers.push("manifest-invalid".to_owned());
    }
    if !suite_digest_matches {
        blockers.push("suite-digest-mismatch".to_owned());
    }
    if !codex.available {
        blockers.push("codex-cli-unavailable".to_owned());
    }
    if !pi.available {
        blockers.push("pi-unavailable".to_owned());
    }
    if !piagent_package_ready {
        blockers.push("piagent-package-root-not-ready".to_owned());
    }
    CliPreflightReport {
        manifest_valid,
        suite_digest_matches,
        codex,
        pi,
        piagent_package_ready,
        ready: blockers.is_empty(),
        blockers,
    }
}

async fn probe(program: &str) -> ExecutableProbe {
    match Command::new(program).arg("--version").output().await {
        Ok(output) if output.status.success() => ExecutableProbe {
            program: program.to_owned(),
            available: true,
            version: Some(String::from_utf8_lossy(&output.stdout).trim().to_owned()),
            error: None,
        },
        Ok(output) => ExecutableProbe {
            program: program.to_owned(),
            available: false,
            version: None,
            error: Some(String::from_utf8_lossy(&output.stderr).trim().to_owned()),
        },
        Err(error) => ExecutableProbe {
            program: program.to_owned(),
            available: false,
            version: None,
            error: Some(error.to_string()),
        },
    }
}

#[derive(Clone, Debug, Default, Deserialize, Eq, PartialEq, Serialize)]
pub struct CliUsage {
    pub input_tokens: u64,
    pub cached_input_tokens: u64,
    pub cache_write_input_tokens: u64,
    pub output_tokens: u64,
    pub reasoning_output_tokens: u64,
    pub fresh_tokens: u64,
    pub tool_calls: u64,
    pub event_count: u64,
    pub source: String,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct CliVerifierResult {
    pub passed: bool,
    pub score_bps: u32,
    #[serde(default)]
    pub checks: Vec<String>,
    #[serde(default)]
    pub error: Option<String>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct CliTrialReport {
    pub schema_version: String,
    pub trial_id: String,
    pub scenario_id: String,
    pub surface: CliSurface,
    pub repeat: usize,
    pub requested_model: String,
    pub requested_thinking: String,
    pub exit_code: Option<i32>,
    pub timed_out: bool,
    pub duration_ms: u64,
    pub resolved: bool,
    pub verifier: CliVerifierResult,
    pub scope_passed: bool,
    pub changed_files: Vec<String>,
    pub outside_scope: Vec<String>,
    pub required_output_passed: bool,
    pub forbidden_output_passed: bool,
    pub usage: CliUsage,
    pub stdout_sha256: String,
    pub stderr_sha256: String,
}

pub async fn run_cli_trial(
    manifest: &CliComparisonManifest,
    trial_id: &str,
    output_root: &Path,
) -> Result<CliTrialReport, String> {
    manifest.validate()?;
    let preflight = preflight_cli_comparison(manifest).await;
    if !preflight.ready {
        return Err(format!(
            "preflight failed: {}",
            preflight.blockers.join(", ")
        ));
    }
    let trial = manifest
        .trials
        .iter()
        .find(|trial| trial.trial_id == trial_id)
        .ok_or_else(|| format!("unknown trial {trial_id}"))?;
    let suite_path = Path::new(&manifest.suite_path);
    let suite_root = suite_path
        .parent()
        .ok_or_else(|| "suite path has no parent".to_owned())?;
    let suite: CliSuite = serde_json::from_slice(
        &std::fs::read(suite_path).map_err(|error| format!("cannot read suite: {error}"))?,
    )
    .map_err(|error| format!("invalid suite: {error}"))?;
    suite.validate(suite_root)?;
    let scenario = suite
        .scenarios
        .iter()
        .find(|scenario| scenario.id == trial.scenario_id)
        .ok_or_else(|| format!("missing scenario {}", trial.scenario_id))?;
    let workspace_root = output_root.join("workspaces").join(&trial.trial_id);
    let workspace = workspace_root.join("project");
    if workspace_root.exists() {
        return Err(format!(
            "trial workspace already exists: {}",
            workspace_root.display()
        ));
    }
    std::fs::create_dir_all(&workspace_root)
        .map_err(|error| format!("cannot create workspace root: {error}"))?;
    copy_tree(
        &resolve_inside(suite_root, &scenario.fixture, true)?,
        &workspace,
    )?;
    if trial.surface == CliSurface::PiAgent {
        initialize_piagent(manifest, scenario, &workspace).await?;
    }
    // Bootstrap files are part of the PiAgent treatment, not task edits.
    let before = tree_snapshot(&workspace)?;
    let prompt = std::fs::read_to_string(resolve_inside(suite_root, &scenario.prompt, false)?)
        .map_err(|error| format!("cannot read prompt: {error}"))?;
    let started = Instant::now();
    let process = match trial.surface {
        CliSurface::CodexCli => {
            run_process(
                &manifest.codex_program,
                &codex_args(manifest, &workspace),
                &workspace,
                Some(prompt.as_bytes()),
                &[],
                manifest.timeout_seconds,
            )
            .await?
        }
        CliSurface::PiAgent => {
            let args = piagent_args(manifest, scenario, &workspace, &prompt);
            let env = vec![
                ("PIAGENT_NO_UPDATE_CHECK".to_owned(), "1".to_owned()),
                (
                    "PIAGENT_PERMISSION_PROFILE".to_owned(),
                    "workspace-write".to_owned(),
                ),
            ];
            run_process(
                &manifest.pi_program,
                &args,
                &workspace,
                None,
                &env,
                manifest.timeout_seconds,
            )
            .await?
        }
    };
    let duration_ms = started.elapsed().as_millis().try_into().unwrap_or(u64::MAX);
    let after = tree_snapshot(&workspace)?;
    let changed_files = changed_paths(&before, &after);
    let outside_scope: Vec<_> = changed_files
        .iter()
        .filter(|path| {
            !(scenario
                .allowed_changes
                .iter()
                .any(|rule| matches_path(path, rule))
                || trial.surface == CliSurface::PiAgent && piagent_runtime_managed(path))
        })
        .cloned()
        .collect();
    let verifier = run_verifier(suite_root, scenario, &workspace).await;
    let combined_output = String::from_utf8_lossy(&process.stdout).to_string()
        + &String::from_utf8_lossy(&process.stderr);
    let required_output_passed = scenario
        .required_output_markers
        .iter()
        .all(|marker| combined_output.contains(marker));
    let forbidden_output_passed = scenario
        .forbidden_output_markers
        .iter()
        .all(|marker| !combined_output.contains(marker));
    let usage = match trial.surface {
        CliSurface::CodexCli => parse_codex_usage(&process.stdout),
        CliSurface::PiAgent => parse_pi_usage(&process.stdout),
    };
    let scope_passed = outside_scope.is_empty();
    let resolved = process.exit_code == Some(0)
        && !process.timed_out
        && verifier.passed
        && scope_passed
        && required_output_passed
        && forbidden_output_passed;
    Ok(CliTrialReport {
        schema_version: CLI_COMPARISON_REPORT_SCHEMA.to_owned(),
        trial_id: trial.trial_id.clone(),
        scenario_id: trial.scenario_id.clone(),
        surface: trial.surface,
        repeat: trial.repeat,
        requested_model: manifest.model.clone(),
        requested_thinking: manifest.thinking.clone(),
        exit_code: process.exit_code,
        timed_out: process.timed_out,
        duration_ms,
        resolved,
        verifier,
        scope_passed,
        changed_files,
        outside_scope,
        required_output_passed,
        forbidden_output_passed,
        usage,
        stdout_sha256: sha256(&process.stdout),
        stderr_sha256: sha256(&process.stderr),
    })
}

fn codex_args(manifest: &CliComparisonManifest, workspace: &Path) -> Vec<String> {
    let model = manifest
        .model
        .split_once('/')
        .map_or(manifest.model.as_str(), |(_, model)| model);
    let thinking = if manifest.thinking == "off" {
        "none"
    } else {
        &manifest.thinking
    };
    vec![
        "exec".to_owned(),
        "--json".to_owned(),
        "--ephemeral".to_owned(),
        "--color".to_owned(),
        "never".to_owned(),
        "--ignore-user-config".to_owned(),
        "--ignore-rules".to_owned(),
        "--skip-git-repo-check".to_owned(),
        "--sandbox".to_owned(),
        "workspace-write".to_owned(),
        "--cd".to_owned(),
        workspace.to_string_lossy().into_owned(),
        "--model".to_owned(),
        model.to_owned(),
        "--config".to_owned(),
        format!("model_reasoning_effort=\"{thinking}\""),
        "-".to_owned(),
    ]
}

fn piagent_args(
    manifest: &CliComparisonManifest,
    scenario: &CliScenario,
    workspace: &Path,
    prompt: &str,
) -> Vec<String> {
    let root = Path::new(&manifest.piagent_package_root);
    vec![
        "--print".to_owned(),
        "--mode".to_owned(),
        "json".to_owned(),
        "--no-session".to_owned(),
        "--approve".to_owned(),
        "--no-skills".to_owned(),
        "--no-prompt-templates".to_owned(),
        "--no-extensions".to_owned(),
        "--no-context-files".to_owned(),
        "--append-system-prompt".to_owned(),
        workspace.join("AGENTS.md").to_string_lossy().into_owned(),
        "--extension".to_owned(),
        root.join("packages/piagent-core/extensions/piagent-guard.ts")
            .to_string_lossy()
            .into_owned(),
        "--skill".to_owned(),
        root.join("packages/piagent-core/skills")
            .to_string_lossy()
            .into_owned(),
        "--model".to_owned(),
        manifest.model.clone(),
        "--thinking".to_owned(),
        manifest.thinking.clone(),
        "--name".to_owned(),
        format!("STRUCTURE BENCH {}", scenario.id),
        prompt.to_owned(),
    ]
}

async fn initialize_piagent(
    manifest: &CliComparisonManifest,
    scenario: &CliScenario,
    workspace: &Path,
) -> Result<(), String> {
    let script = Path::new(&manifest.piagent_package_root).join("scripts/init-project.sh");
    let args = vec![
        script.to_string_lossy().into_owned(),
        workspace.to_string_lossy().into_owned(),
        "--profile".to_owned(),
        scenario.piagent_profile.clone(),
        "--package-source".to_owned(),
        manifest.piagent_package_root.clone(),
    ];
    let output = run_process("bash", &args, workspace, None, &[], 60).await?;
    if output.exit_code != Some(0) {
        return Err(format!(
            "PiAgent initialization failed: {}",
            String::from_utf8_lossy(&output.stderr).trim()
        ));
    }
    Ok(())
}

async fn run_verifier(
    suite_root: &Path,
    scenario: &CliScenario,
    workspace: &Path,
) -> CliVerifierResult {
    let program = if scenario.verifier[0].contains('/') {
        match resolve_inside(suite_root, &scenario.verifier[0], false) {
            Ok(path) => path.to_string_lossy().into_owned(),
            Err(error) => {
                return CliVerifierResult {
                    passed: false,
                    score_bps: 0,
                    checks: Vec::new(),
                    error: Some(error),
                };
            }
        }
    } else {
        scenario.verifier[0].clone()
    };
    let args: Vec<_> = scenario
        .verifier
        .iter()
        .skip(1)
        .map(|arg| {
            let expanded = arg.replace("{workspace}", &workspace.to_string_lossy());
            if expanded == *arg && arg.contains('/') {
                resolve_inside(suite_root, arg, false)
                    .map(|path| path.to_string_lossy().into_owned())
                    .unwrap_or(expanded)
            } else {
                expanded
            }
        })
        .collect();
    match run_process(&program, &args, suite_root, None, &[], 60).await {
        Ok(output) if output.exit_code == Some(0) && !output.timed_out => {
            serde_json::from_slice(&output.stdout).unwrap_or_else(|error| CliVerifierResult {
                passed: false,
                score_bps: 0,
                checks: Vec::new(),
                error: Some(format!("invalid verifier JSON: {error}")),
            })
        }
        Ok(output) => CliVerifierResult {
            passed: false,
            score_bps: 0,
            checks: Vec::new(),
            error: Some(if output.timed_out {
                "verifier-timeout".to_owned()
            } else {
                format!("verifier-exit-{}", output.exit_code.unwrap_or(-1))
            }),
        },
        Err(error) => CliVerifierResult {
            passed: false,
            score_bps: 0,
            checks: Vec::new(),
            error: Some(error),
        },
    }
}

struct ProcessResult {
    exit_code: Option<i32>,
    timed_out: bool,
    stdout: Vec<u8>,
    stderr: Vec<u8>,
}

async fn run_process(
    program: &str,
    args: &[String],
    cwd: &Path,
    input: Option<&[u8]>,
    env: &[(String, String)],
    timeout_seconds: u64,
) -> Result<ProcessResult, String> {
    let mut command = Command::new(program);
    command
        .args(args)
        .current_dir(cwd)
        .stdin(if input.is_some() {
            Stdio::piped()
        } else {
            Stdio::null()
        })
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .kill_on_drop(true);
    for (name, value) in env {
        command.env(name, value);
    }
    let mut child = command
        .spawn()
        .map_err(|error| format!("cannot start {program}: {error}"))?;
    if let Some(input) = input
        && let Some(mut stdin) = child.stdin.take()
    {
        stdin
            .write_all(input)
            .await
            .map_err(|error| format!("cannot write {program} stdin: {error}"))?;
    }
    match tokio::time::timeout(
        Duration::from_secs(timeout_seconds),
        child.wait_with_output(),
    )
    .await
    {
        Ok(Ok(output)) => Ok(ProcessResult {
            exit_code: output.status.code(),
            timed_out: false,
            stdout: output.stdout,
            stderr: output.stderr,
        }),
        Ok(Err(error)) => Err(format!("cannot wait for {program}: {error}")),
        Err(_) => Ok(ProcessResult {
            exit_code: None,
            timed_out: true,
            stdout: Vec::new(),
            stderr: Vec::new(),
        }),
    }
}

fn parse_codex_usage(bytes: &[u8]) -> CliUsage {
    let mut usage = CliUsage {
        source: "codex-exec-turn.completed".to_owned(),
        ..CliUsage::default()
    };
    for value in json_lines(bytes) {
        usage.event_count += 1;
        if value.get("type").and_then(Value::as_str) == Some("turn.completed") {
            let observed = value.get("usage").unwrap_or(&Value::Null);
            usage.input_tokens = field(observed, "input_tokens");
            usage.cached_input_tokens = field(observed, "cached_input_tokens");
            usage.cache_write_input_tokens = field(observed, "cache_write_input_tokens");
            usage.output_tokens = field(observed, "output_tokens");
            usage.reasoning_output_tokens = field(observed, "reasoning_output_tokens");
        }
        if value.get("type").and_then(Value::as_str) == Some("item.completed")
            && matches!(
                value.pointer("/item/type").and_then(Value::as_str),
                Some("command_execution" | "file_change" | "mcp_tool_call")
            )
        {
            usage.tool_calls += 1;
        }
    }
    usage.fresh_tokens = usage
        .input_tokens
        .saturating_sub(usage.cached_input_tokens)
        .saturating_add(usage.output_tokens);
    usage
}

fn parse_pi_usage(bytes: &[u8]) -> CliUsage {
    let mut usage = CliUsage {
        source: "pi-json-message_end".to_owned(),
        ..CliUsage::default()
    };
    for value in json_lines(bytes) {
        usage.event_count += 1;
        if value.get("type").and_then(Value::as_str) == Some("message_end")
            && value.pointer("/message/role").and_then(Value::as_str) == Some("assistant")
        {
            let observed = value.pointer("/message/usage").unwrap_or(&Value::Null);
            usage.input_tokens += field(observed, "input");
            usage.cached_input_tokens += field(observed, "cacheRead");
            usage.cache_write_input_tokens += field(observed, "cacheWrite");
            usage.output_tokens += field(observed, "output");
            usage.reasoning_output_tokens += field(observed, "reasoning");
        }
        if value.get("type").and_then(Value::as_str) == Some("tool_execution_end") {
            usage.tool_calls += 1;
        }
    }
    usage.fresh_tokens = usage.input_tokens.saturating_add(usage.output_tokens);
    usage
}

fn json_lines(bytes: &[u8]) -> impl Iterator<Item = Value> + '_ {
    bytes
        .split(|byte| *byte == b'\n')
        .filter(|line| !line.is_empty())
        .filter_map(|line| serde_json::from_slice(line).ok())
}

fn field(value: &Value, name: &str) -> u64 {
    value.get(name).and_then(Value::as_u64).unwrap_or(0)
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct CliComparisonSummary {
    pub paired_runs: usize,
    pub piagent_resolved: usize,
    pub codex_resolved: usize,
    pub comparable_success_pairs: usize,
    pub piagent_fresh_token_wins: usize,
    pub codex_fresh_token_wins: usize,
    pub ties: usize,
    pub geometric_mean_fresh_token_ratio: Option<f64>,
    pub mean_duration_ratio: Option<f64>,
    pub quality_non_inferior: bool,
    pub token_improvement: bool,
}

pub fn summarize_cli_comparison(
    manifest: &CliComparisonManifest,
    reports: &[CliTrialReport],
) -> Result<CliComparisonSummary, String> {
    manifest.validate()?;
    let observed: BTreeMap<_, _> = reports
        .iter()
        .map(|report| (report.trial_id.as_str(), report))
        .collect();
    let mut piagent_resolved = 0usize;
    let mut codex_resolved = 0usize;
    for report in reports {
        if report.resolved {
            match report.surface {
                CliSurface::PiAgent => piagent_resolved += 1,
                CliSurface::CodexCli => codex_resolved += 1,
            }
        }
    }
    let mut ratios = Vec::new();
    let mut duration_ratios = Vec::new();
    let mut piagent_wins = 0usize;
    let mut codex_wins = 0usize;
    let mut ties = 0usize;
    let mut paired_runs = 0usize;
    for pi_trial in manifest
        .trials
        .iter()
        .filter(|trial| trial.surface == CliSurface::PiAgent)
    {
        let codex_trial = manifest.trials.iter().find(|trial| {
            trial.surface == CliSurface::CodexCli
                && trial.scenario_id == pi_trial.scenario_id
                && trial.repeat == pi_trial.repeat
        });
        let (Some(pi), Some(codex)) = (
            observed.get(pi_trial.trial_id.as_str()),
            codex_trial.and_then(|trial| observed.get(trial.trial_id.as_str())),
        ) else {
            continue;
        };
        paired_runs += 1;
        if pi.resolved
            && codex.resolved
            && pi.usage.fresh_tokens > 0
            && codex.usage.fresh_tokens > 0
        {
            ratios.push(pi.usage.fresh_tokens as f64 / codex.usage.fresh_tokens as f64);
            if pi.duration_ms > 0 && codex.duration_ms > 0 {
                duration_ratios.push(pi.duration_ms as f64 / codex.duration_ms as f64);
            }
            match pi.usage.fresh_tokens.cmp(&codex.usage.fresh_tokens) {
                std::cmp::Ordering::Less => piagent_wins += 1,
                std::cmp::Ordering::Greater => codex_wins += 1,
                std::cmp::Ordering::Equal => ties += 1,
            }
        }
    }
    let geometric_mean = (!ratios.is_empty())
        .then(|| (ratios.iter().map(|ratio| ratio.ln()).sum::<f64>() / ratios.len() as f64).exp());
    let mean_duration_ratio = (!duration_ratios.is_empty())
        .then(|| duration_ratios.iter().sum::<f64>() / duration_ratios.len() as f64);
    let quality_non_inferior = piagent_resolved >= codex_resolved;
    Ok(CliComparisonSummary {
        paired_runs,
        piagent_resolved,
        codex_resolved,
        comparable_success_pairs: ratios.len(),
        piagent_fresh_token_wins: piagent_wins,
        codex_fresh_token_wins: codex_wins,
        ties,
        geometric_mean_fresh_token_ratio: geometric_mean,
        mean_duration_ratio,
        quality_non_inferior,
        token_improvement: quality_non_inferior && geometric_mean.is_some_and(|ratio| ratio < 1.0),
    })
}

fn validate_identifier(value: &str, label: &str) -> Result<(), String> {
    if value.is_empty()
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || byte == b'-')
    {
        return Err(format!(
            "{label} must contain lowercase ASCII, digits, or hyphens"
        ));
    }
    Ok(())
}

fn validate_relative_pattern(value: &str) -> Result<(), String> {
    let path = value.strip_suffix("/**").unwrap_or(value);
    if path.is_empty()
        || Path::new(path).is_absolute()
        || Path::new(path)
            .components()
            .any(|component| matches!(component, std::path::Component::ParentDir))
    {
        return Err(format!("unsafe relative path pattern {value}"));
    }
    Ok(())
}

fn resolve_inside(root: &Path, relative: &str, directory: bool) -> Result<PathBuf, String> {
    validate_relative_pattern(relative)?;
    let root = root
        .canonicalize()
        .map_err(|error| format!("cannot resolve suite root: {error}"))?;
    let path = root
        .join(relative)
        .canonicalize()
        .map_err(|error| format!("cannot resolve suite entry {relative}: {error}"))?;
    if !path.starts_with(&root) || directory != path.is_dir() {
        return Err(format!(
            "suite entry has wrong type or escapes root: {relative}"
        ));
    }
    Ok(path)
}

fn matches_path(path: &str, pattern: &str) -> bool {
    pattern
        .strip_suffix("/**")
        .is_some_and(|prefix| path == prefix || path.starts_with(&format!("{prefix}/")))
        || path == pattern
}

fn piagent_runtime_managed(path: &str) -> bool {
    matches!(path, ".pi/project-context.md" | ".pi/context-index.json")
        || path.starts_with(".pi/piagent-state/")
        || path.starts_with(".pi-subagents/")
}

fn copy_tree(source: &Path, target: &Path) -> Result<(), String> {
    std::fs::create_dir_all(target)
        .map_err(|error| format!("cannot create fixture copy: {error}"))?;
    for entry in
        std::fs::read_dir(source).map_err(|error| format!("cannot read fixture: {error}"))?
    {
        let entry = entry.map_err(|error| format!("cannot read fixture entry: {error}"))?;
        let kind = entry
            .file_type()
            .map_err(|error| format!("cannot inspect fixture entry: {error}"))?;
        let destination = target.join(entry.file_name());
        if kind.is_symlink() {
            return Err(format!(
                "fixture symlinks are forbidden: {}",
                entry.path().display()
            ));
        }
        if kind.is_dir() {
            copy_tree(&entry.path(), &destination)?;
        } else if kind.is_file() {
            std::fs::copy(entry.path(), destination)
                .map_err(|error| format!("cannot copy fixture file: {error}"))?;
        }
    }
    Ok(())
}

fn digest_tree(root: &Path) -> Result<String, String> {
    let snapshot = tree_snapshot(root)?;
    let mut digest = Sha256::new();
    for (path, file_digest) in snapshot {
        digest.update((path.len() as u64).to_be_bytes());
        digest.update(path.as_bytes());
        digest.update(file_digest.as_bytes());
    }
    Ok(format!("sha256:{:x}", digest.finalize()))
}

fn tree_snapshot(root: &Path) -> Result<BTreeMap<String, String>, String> {
    let mut files = BTreeMap::new();
    collect_files(root, root, &mut files)?;
    Ok(files)
}

fn collect_files(
    root: &Path,
    current: &Path,
    files: &mut BTreeMap<String, String>,
) -> Result<(), String> {
    for entry in std::fs::read_dir(current).map_err(|error| format!("cannot read tree: {error}"))? {
        let entry = entry.map_err(|error| format!("cannot read tree entry: {error}"))?;
        let kind = entry
            .file_type()
            .map_err(|error| format!("cannot inspect tree: {error}"))?;
        if kind.is_symlink() {
            return Err(format!("symlink is forbidden: {}", entry.path().display()));
        }
        if kind.is_dir() {
            collect_files(root, &entry.path(), files)?;
        } else if kind.is_file() {
            let relative = entry
                .path()
                .strip_prefix(root)
                .map_err(|error| format!("cannot relativize tree path: {error}"))?
                .to_string_lossy()
                .replace('\\', "/");
            let bytes = std::fs::read(entry.path())
                .map_err(|error| format!("cannot read tree file: {error}"))?;
            files.insert(relative, sha256(&bytes));
        }
    }
    Ok(())
}

fn changed_paths(
    before: &BTreeMap<String, String>,
    after: &BTreeMap<String, String>,
) -> Vec<String> {
    before
        .keys()
        .chain(after.keys())
        .collect::<BTreeSet<_>>()
        .into_iter()
        .filter(|path| before.get(*path) != after.get(*path))
        .cloned()
        .collect()
}

fn sha256(bytes: &[u8]) -> String {
    format!("sha256:{:x}", Sha256::digest(bytes))
}

fn stable_seed(seed: u64, scenario: &str, repeat: usize) -> u64 {
    scenario.bytes().fold(seed ^ repeat as u64, |state, byte| {
        state
            .wrapping_mul(1_099_511_628_211)
            .wrapping_add(byte as u64)
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn codex_usage_uses_uncached_input_plus_output() {
        let bytes = br#"{"type":"item.completed","item":{"type":"command_execution"}}
{"type":"turn.completed","usage":{"input_tokens":100,"cached_input_tokens":60,"cache_write_input_tokens":5,"output_tokens":20,"reasoning_output_tokens":7}}
"#;
        let usage = parse_codex_usage(bytes);
        assert_eq!(usage.fresh_tokens, 60);
        assert_eq!(usage.tool_calls, 1);
        assert_eq!(usage.reasoning_output_tokens, 7);
    }

    #[test]
    fn pi_usage_sums_authoritative_assistant_messages() {
        let bytes = br#"{"type":"message_end","message":{"role":"assistant","usage":{"input":30,"output":10,"cacheRead":20,"cacheWrite":2,"reasoning":4}}}
{"type":"tool_execution_end"}
{"type":"message_end","message":{"role":"assistant","usage":{"input":15,"output":5,"cacheRead":10,"cacheWrite":0,"reasoning":1}}}
"#;
        let usage = parse_pi_usage(bytes);
        assert_eq!(usage.fresh_tokens, 60);
        assert_eq!(usage.cached_input_tokens, 30);
        assert_eq!(usage.tool_calls, 1);
    }

    #[test]
    fn summary_is_paired_and_success_conditioned() {
        let manifest = CliComparisonManifest {
            schema_version: CLI_COMPARISON_MANIFEST_SCHEMA.to_owned(),
            claim_tier: "test".to_owned(),
            suite_path: "/tmp/suite.json".to_owned(),
            suite_digest: "sha256:test".to_owned(),
            model: "model".to_owned(),
            thinking: "high".to_owned(),
            repetitions: 1,
            timeout_seconds: 1,
            seed: 1,
            codex_program: "codex".to_owned(),
            pi_program: "pi".to_owned(),
            piagent_package_root: "/tmp/piagent".to_owned(),
            trials: vec![
                CliTrial {
                    trial_id: "task-r01-piagent".to_owned(),
                    scenario_id: "task".to_owned(),
                    surface: CliSurface::PiAgent,
                    repeat: 1,
                    sequence: 1,
                    artifact_path: "pi.json".to_owned(),
                },
                CliTrial {
                    trial_id: "task-r01-codex-cli".to_owned(),
                    scenario_id: "task".to_owned(),
                    surface: CliSurface::CodexCli,
                    repeat: 1,
                    sequence: 2,
                    artifact_path: "codex.json".to_owned(),
                },
            ],
        };
        let report = |trial_id: &str, surface, fresh_tokens| CliTrialReport {
            schema_version: CLI_COMPARISON_REPORT_SCHEMA.to_owned(),
            trial_id: trial_id.to_owned(),
            scenario_id: "task".to_owned(),
            surface,
            repeat: 1,
            requested_model: "model".to_owned(),
            requested_thinking: "high".to_owned(),
            exit_code: Some(0),
            timed_out: false,
            duration_ms: 100,
            resolved: true,
            verifier: CliVerifierResult {
                passed: true,
                score_bps: 10_000,
                checks: vec![],
                error: None,
            },
            scope_passed: true,
            changed_files: vec![],
            outside_scope: vec![],
            required_output_passed: true,
            forbidden_output_passed: true,
            usage: CliUsage {
                fresh_tokens,
                ..CliUsage::default()
            },
            stdout_sha256: "x".to_owned(),
            stderr_sha256: "y".to_owned(),
        };
        let summary = summarize_cli_comparison(
            &manifest,
            &[
                report("task-r01-piagent", CliSurface::PiAgent, 50),
                report("task-r01-codex-cli", CliSurface::CodexCli, 100),
            ],
        )
        .expect("summary");
        assert_eq!(summary.comparable_success_pairs, 1);
        assert_eq!(summary.piagent_fresh_token_wins, 1);
        assert_eq!(summary.geometric_mean_fresh_token_ratio, Some(0.5));
        assert!(summary.token_improvement);
    }
}
