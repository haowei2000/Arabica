//! Controlled, paired comparison harness for PiAgent and Codex CLI.
//!
//! The harness keeps model execution opt-in. Planning, validation, preflight,
//! and summary generation are provider-free. Each paid trial runs in a fresh
//! fixture copy and retains hashes plus typed usage instead of raw transcripts.

use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};
use tokio::io::AsyncWriteExt;
use tokio::process::Command;

use structure_protocol::{
    Command as RuntimeCommand, CommandEnvelope, CommandId, Event, WorkspaceId,
};
use structure_provider::{ApiModelProvider, ApiProviderConfig, ApiType};
use structure_runner::LocalRunner;
use structure_runtime::CoreRuntime;
use structure_session::SessionManager;

use crate::tier_b::{ProviderCallObservation, ProviderRecorder, RecordingProvider};

pub const CLI_COMPARISON_SUITE_SCHEMA: &str = "structure.cli-comparison-suite/2026-08";
pub const CLI_COMPARISON_MANIFEST_SCHEMA: &str = "structure.cli-comparison-manifest/2026-08";
pub const CLI_COMPARISON_REPORT_SCHEMA: &str = "structure.cli-comparison-report/2026-08";
static PROCESS_CAPTURE_SEQUENCE: AtomicU64 = AtomicU64::new(0);

#[derive(Clone, Copy, Debug, Deserialize, Eq, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum CliSurface {
    PiAgent,
    CodexCli,
    Structure,
}

impl CliSurface {
    pub const fn name(self) -> &'static str {
        match self {
            Self::PiAgent => "piagent",
            Self::CodexCli => "codex-cli",
            Self::Structure => "structure",
        }
    }

    pub fn parse(value: &str) -> Result<Self, String> {
        match value {
            "piagent" => Ok(Self::PiAgent),
            "codex-cli" => Ok(Self::CodexCli),
            "structure" => Ok(Self::Structure),
            _ => Err(format!(
                "unknown comparison surface {value}; expected piagent, codex-cli, or structure"
            )),
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
    #[serde(default)]
    pub provider_base_url: Option<String>,
    #[serde(default)]
    pub provider_api_key_env: Option<String>,
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
    pub provider_base_url: Option<String>,
    pub provider_api_key_env: Option<String>,
    pub surfaces: Vec<CliSurface>,
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
    if plan.surfaces.len() != 2 || plan.surfaces[0] == plan.surfaces[1] {
        return Err("exactly two distinct comparison surfaces are required".to_owned());
    }
    if plan.surfaces.contains(&CliSurface::Structure)
        && (plan.provider_base_url.is_none() || plan.provider_api_key_env.is_none())
    {
        return Err(
            "Structure comparison requires provider base URL and API key environment".to_owned(),
        );
    }
    let mut trials = Vec::new();
    let mut sequence = 0usize;
    for repeat in 1..=plan.repetitions {
        for scenario in &suite.scenarios {
            let reverse = stable_seed(plan.seed, &scenario.id, repeat) & 1 == 1;
            let surfaces = if reverse {
                [plan.surfaces[1], plan.surfaces[0]]
            } else {
                [plan.surfaces[0], plan.surfaces[1]]
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
        provider_base_url: plan.provider_base_url,
        provider_api_key_env: plan.provider_api_key_env,
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
        if self.provider_base_url.is_some() != self.provider_api_key_env.is_some() {
            return Err(
                "provider base URL and API key environment must be set together".to_owned(),
            );
        }
        if let Some(name) = &self.provider_api_key_env
            && (name.is_empty()
                || !name
                    .bytes()
                    .all(|byte| byte.is_ascii_uppercase() || byte.is_ascii_digit() || byte == b'_'))
        {
            return Err("provider API key environment name is invalid".to_owned());
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
    let surfaces: BTreeSet<_> = manifest.trials.iter().map(|trial| trial.surface).collect();
    if surfaces.contains(&CliSurface::CodexCli) && !codex.available {
        blockers.push("codex-cli-unavailable".to_owned());
    }
    if surfaces.contains(&CliSurface::PiAgent) && !pi.available {
        blockers.push("pi-unavailable".to_owned());
    }
    if surfaces.contains(&CliSurface::PiAgent) && !piagent_package_ready {
        blockers.push("piagent-package-root-not-ready".to_owned());
    }
    if surfaces.contains(&CliSurface::Structure)
        && (manifest.provider_base_url.is_none() || manifest.provider_api_key_env.is_none())
    {
        blockers.push("structure-provider-configuration-missing".to_owned());
    }
    if let Some(name) = &manifest.provider_api_key_env
        && std::env::var_os(name).is_none()
    {
        blockers.push(format!("provider-api-key-environment-missing:{name}"));
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

#[derive(Clone, Debug, Default, Deserialize, Eq, PartialEq, Serialize)]
pub struct CliProgressDiagnostics {
    pub model_turns: u64,
    pub tool_use_turns: u64,
    pub tool_use_without_observed_call_turns: u64,
    pub max_consecutive_tool_use_without_observed_call_turns: u64,
    pub failed_tool_results: u64,
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
    #[serde(default)]
    pub progress: CliProgressDiagnostics,
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
        initialize_git_baseline(&workspace).await?;
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
            let mut env = vec![
                ("PIAGENT_NO_UPDATE_CHECK".to_owned(), "1".to_owned()),
                (
                    "PIAGENT_PERMISSION_PROFILE".to_owned(),
                    "workspace-write".to_owned(),
                ),
            ];
            if let Some(pi_home) = prepare_pi_runtime(manifest, &workspace_root)? {
                env.push((
                    "PI_CODING_AGENT_DIR".to_owned(),
                    pi_home.to_string_lossy().into_owned(),
                ));
            }
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
        CliSurface::Structure => {
            match tokio::time::timeout(
                Duration::from_secs(manifest.timeout_seconds),
                run_structure_trial(manifest, &workspace_root, &workspace, &prompt),
            )
            .await
            {
                Ok(result) => result?,
                Err(_) => structure_timeout_result(&workspace_root)?,
            }
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
        CliSurface::Structure => process.usage.clone().unwrap_or_default(),
    };
    let progress = match trial.surface {
        CliSurface::PiAgent => parse_pi_progress(&process.stdout),
        CliSurface::CodexCli => CliProgressDiagnostics::default(),
        CliSurface::Structure => process.progress.clone().unwrap_or_default(),
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
        progress,
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
    let mut args = vec![
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
    ];
    if let (Some(base_url), Some(api_key_env)) =
        (&manifest.provider_base_url, &manifest.provider_api_key_env)
    {
        args.splice(
            args.len() - 1..args.len() - 1,
            [
                "--config".to_owned(),
                "model_provider=\"benchmark_provider\"".to_owned(),
                "--config".to_owned(),
                format!(
                    "model_providers.benchmark_provider={{ name=\"Benchmark provider\", base_url=\"{base_url}\", env_key=\"{api_key_env}\", wire_api=\"responses\", requires_openai_auth=false }}"
                ),
                "--config".to_owned(),
                "disable_response_storage=true".to_owned(),
                "--config".to_owned(),
                "model_supports_reasoning_summaries=true".to_owned(),
            ],
        );
    }
    args
}

fn prepare_pi_runtime(
    manifest: &CliComparisonManifest,
    workspace_root: &Path,
) -> Result<Option<PathBuf>, String> {
    let (Some(base_url), Some(api_key_env)) =
        (&manifest.provider_base_url, &manifest.provider_api_key_env)
    else {
        return Ok(None);
    };
    let (provider, model) = manifest
        .model
        .split_once('/')
        .unwrap_or(("benchmark-provider", &manifest.model));
    validate_identifier(provider, "provider id")?;
    let pi_home = workspace_root.join("pi-home");
    std::fs::create_dir_all(&pi_home)
        .map_err(|error| format!("cannot create isolated Pi home: {error}"))?;
    let models = serde_json::json!({
        "providers": {
            provider: {
                "baseUrl": base_url,
                "api": "openai-responses",
                "apiKey": format!("${api_key_env}"),
                "models": [{
                    "id": model,
                    "name": model,
                    "reasoning": true,
                    "input": ["text"],
                    "contextWindow": 1_048_576,
                    "maxTokens": 131_072,
                    "compat": { "supportsStore": false }
                }]
            }
        }
    });
    std::fs::write(
        pi_home.join("models.json"),
        serde_json::to_vec_pretty(&models)
            .map_err(|error| format!("cannot encode Pi models: {error}"))?,
    )
    .map_err(|error| format!("cannot write Pi models: {error}"))?;
    Ok(Some(pi_home))
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

async fn initialize_git_baseline(workspace: &Path) -> Result<(), String> {
    for args in [
        vec!["init".to_owned(), "--quiet".to_owned()],
        vec!["add".to_owned(), "--all".to_owned()],
        vec![
            "-c".to_owned(),
            "user.name=Structure Benchmark".to_owned(),
            "-c".to_owned(),
            "user.email=benchmark@invalid.example".to_owned(),
            "commit".to_owned(),
            "--quiet".to_owned(),
            "-m".to_owned(),
            "benchmark baseline".to_owned(),
        ],
    ] {
        let output = run_process("git", &args, workspace, None, &[], 30).await?;
        if output.exit_code != Some(0) || output.timed_out {
            return Err(format!(
                "PiAgent Git baseline failed for `git {}`: {}",
                args.join(" "),
                String::from_utf8_lossy(&output.stderr).trim()
            ));
        }
    }
    Ok(())
}

async fn run_structure_trial(
    manifest: &CliComparisonManifest,
    workspace_root: &Path,
    workspace: &Path,
    prompt: &str,
) -> Result<ProcessResult, String> {
    let base_url = manifest
        .provider_base_url
        .as_deref()
        .ok_or_else(|| "Structure provider base URL is missing".to_owned())?;
    let key_env = manifest
        .provider_api_key_env
        .as_deref()
        .ok_or_else(|| "Structure provider API key environment is missing".to_owned())?;
    let api_key = std::env::var(key_env)
        .map_err(|_| format!("Structure provider API key environment is missing: {key_env}"))?;
    let model = manifest
        .model
        .split_once('/')
        .map_or(manifest.model.as_str(), |(_, model)| model);
    let provider = ApiModelProvider::new(
        ApiProviderConfig::new(ApiType::OpenAiChatCompletions, api_key, base_url, model)
            .with_thinking(manifest.thinking != "off")
            .with_request_timeout_secs(manifest.timeout_seconds)
            .with_raw_exchange_dir(workspace_root.join("provider-raw")),
    )
    .map_err(|error| format!("cannot initialize Structure provider: {error}"))?;
    let recorder =
        ProviderRecorder::with_snapshot_path(workspace_root.join("provider-calls.partial.json"));
    let mut runtime = CoreRuntime::new(
        RecordingProvider::new(provider, recorder.clone()),
        LocalRunner::new(workspace),
    );
    runtime.set_max_model_steps_per_run(32);
    runtime.set_max_model_steps_without_progress(8);
    let mut manager = SessionManager::new(runtime);
    let create_events = manager
        .handle(CommandEnvelope::new(
            CommandId::new("cli-comparison-session"),
            None,
            RuntimeCommand::SessionCreate {
                workspace_id: WorkspaceId::new("cli-comparison-workspace"),
            },
        ))
        .await
        .map_err(|error| format!("Structure session creation failed: {error}"))?;
    let session_id = create_events
        .first()
        .ok_or_else(|| "Structure session creation returned no event".to_owned())?
        .session_id
        .clone();
    let events = manager
        .handle(CommandEnvelope::new(
            CommandId::new("cli-comparison-task"),
            Some(session_id),
            RuntimeCommand::MessageSend {
                content: prompt.to_owned(),
            },
        ))
        .await
        .map_err(|error| format!("Structure task failed: {error}"))?;
    let calls = recorder.from(0);
    let terminal_output = events.iter().rev().find_map(|event| match &event.event {
        Event::RunCompleted { output } => output.clone(),
        _ => None,
    });
    let run_failed = events
        .iter()
        .any(|event| matches!(event.event, Event::RunFailed { .. }));
    let usage = structure_usage(&calls, &events);
    let protocol_empty_tool_use = events.iter().any(|event| {
        matches!(
            &event.event,
            Event::RunFailed { message }
                if message.starts_with("model_protocol_error: provider reported tool_calls without")
        )
    });
    let progress = CliProgressDiagnostics {
        model_turns: calls.len() as u64,
        tool_use_turns: calls
            .iter()
            .filter(|call| {
                call.finish_reason.as_ref().is_some_and(|reason| {
                    matches!(reason, structure_model::FinishReason::ToolCalls)
                })
            })
            .count() as u64,
        tool_use_without_observed_call_turns: u64::from(protocol_empty_tool_use),
        max_consecutive_tool_use_without_observed_call_turns: u64::from(protocol_empty_tool_use),
        failed_tool_results: events
            .iter()
            .filter(|event| matches!(event.event, Event::ToolCallCompleted { is_error: true, .. }))
            .count() as u64,
    };
    Ok(ProcessResult {
        exit_code: Some(if run_failed { 1 } else { 0 }),
        timed_out: false,
        stdout: terminal_output.unwrap_or_default().into_bytes(),
        stderr: events
            .iter()
            .filter_map(|event| match &event.event {
                Event::RunFailed { message } => Some(message.as_str()),
                _ => None,
            })
            .collect::<Vec<_>>()
            .join("\n")
            .into_bytes(),
        usage: Some(usage),
        progress: Some(progress),
    })
}

fn structure_usage(
    calls: &[ProviderCallObservation],
    events: &[structure_protocol::EventEnvelope],
) -> CliUsage {
    let mut usage = CliUsage {
        input_tokens: calls.iter().map(|call| call.input_tokens).sum(),
        cached_input_tokens: calls.iter().map(|call| call.cached_input_tokens).sum(),
        cache_write_input_tokens: calls
            .iter()
            .map(|call| call.cache_creation_input_tokens)
            .sum(),
        output_tokens: calls.iter().map(|call| call.output_tokens).sum(),
        reasoning_output_tokens: 0,
        fresh_tokens: 0,
        tool_calls: events
            .iter()
            .filter(|event| matches!(event.event, Event::ToolCallRequested { .. }))
            .count() as u64,
        event_count: events.len() as u64,
        source: "structure-provider-observations".to_owned(),
    };
    usage.fresh_tokens = usage
        .input_tokens
        .saturating_sub(usage.cached_input_tokens)
        .saturating_add(usage.output_tokens);
    usage
}

fn structure_timeout_result(workspace_root: &Path) -> Result<ProcessResult, String> {
    let calls: Vec<ProviderCallObservation> =
        std::fs::read(workspace_root.join("provider-calls.partial.json"))
            .ok()
            .and_then(|bytes| serde_json::from_slice(&bytes).ok())
            .unwrap_or_default();
    Ok(ProcessResult {
        exit_code: None,
        timed_out: true,
        stdout: Vec::new(),
        stderr: b"Structure trial exceeded the external timeout".to_vec(),
        usage: Some(structure_usage(&calls, &[])),
        progress: Some(CliProgressDiagnostics {
            model_turns: calls.len() as u64,
            ..CliProgressDiagnostics::default()
        }),
    })
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
    usage: Option<CliUsage>,
    progress: Option<CliProgressDiagnostics>,
}

async fn run_process(
    program: &str,
    args: &[String],
    cwd: &Path,
    input: Option<&[u8]>,
    env: &[(String, String)],
    timeout_seconds: u64,
) -> Result<ProcessResult, String> {
    let capture_id = PROCESS_CAPTURE_SEQUENCE.fetch_add(1, Ordering::Relaxed);
    let capture_root = std::env::temp_dir().join(format!(
        "structure-cli-capture-{}-{capture_id}",
        std::process::id()
    ));
    std::fs::create_dir(&capture_root)
        .map_err(|error| format!("cannot create process capture directory: {error}"))?;
    let stdout_path = capture_root.join("stdout");
    let stderr_path = capture_root.join("stderr");
    let stdout_file = std::fs::File::create(&stdout_path)
        .map_err(|error| format!("cannot create stdout capture: {error}"))?;
    let stderr_file = std::fs::File::create(&stderr_path)
        .map_err(|error| format!("cannot create stderr capture: {error}"))?;
    let mut command = Command::new(program);
    command
        .args(args)
        .current_dir(cwd)
        .stdin(if input.is_some() {
            Stdio::piped()
        } else {
            Stdio::null()
        })
        .stdout(Stdio::from(stdout_file))
        .stderr(Stdio::from(stderr_file))
        .kill_on_drop(true);
    for (name, value) in env {
        command.env(name, value);
    }
    let mut child = match command.spawn() {
        Ok(child) => child,
        Err(error) => {
            let _ = std::fs::remove_dir_all(&capture_root);
            return Err(format!("cannot start {program}: {error}"));
        }
    };
    if let Some(input) = input
        && let Some(mut stdin) = child.stdin.take()
    {
        stdin
            .write_all(input)
            .await
            .map_err(|error| format!("cannot write {program} stdin: {error}"))?;
    }
    let (exit_code, timed_out) =
        match tokio::time::timeout(Duration::from_secs(timeout_seconds), child.wait()).await {
            Ok(Ok(status)) => (status.code(), false),
            Ok(Err(error)) => {
                let _ = std::fs::remove_dir_all(&capture_root);
                return Err(format!("cannot wait for {program}: {error}"));
            }
            Err(_) => {
                let _ = child.kill().await;
                let _ = child.wait().await;
                (None, true)
            }
        };
    let stdout = std::fs::read(&stdout_path)
        .map_err(|error| format!("cannot read stdout capture: {error}"))?;
    let stderr = std::fs::read(&stderr_path)
        .map_err(|error| format!("cannot read stderr capture: {error}"))?;
    std::fs::remove_dir_all(&capture_root)
        .map_err(|error| format!("cannot remove process capture directory: {error}"))?;
    Ok(ProcessResult {
        exit_code,
        timed_out,
        stdout,
        stderr,
        usage: None,
        progress: None,
    })
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

fn parse_pi_progress(bytes: &[u8]) -> CliProgressDiagnostics {
    let mut diagnostics = CliProgressDiagnostics::default();
    let mut consecutive_empty_tool_use = 0u64;
    for value in json_lines(bytes) {
        if value.get("type").and_then(Value::as_str) == Some("tool_execution_end")
            && value
                .get("isError")
                .or_else(|| value.get("is_error"))
                .and_then(Value::as_bool)
                == Some(true)
        {
            diagnostics.failed_tool_results += 1;
        }
        if value.get("type").and_then(Value::as_str) != Some("message_end")
            || value.pointer("/message/role").and_then(Value::as_str) != Some("assistant")
        {
            continue;
        }
        diagnostics.model_turns += 1;
        let stop_reason = value
            .pointer("/message/stopReason")
            .or_else(|| value.pointer("/message/stop_reason"))
            .and_then(Value::as_str);
        if !matches!(stop_reason, Some("toolUse" | "tool_use" | "tool_calls")) {
            consecutive_empty_tool_use = 0;
            continue;
        }
        diagnostics.tool_use_turns += 1;
        let has_call = value
            .pointer("/message/content")
            .and_then(Value::as_array)
            .is_some_and(|items| {
                items.iter().any(|item| {
                    matches!(
                        item.get("type").and_then(Value::as_str),
                        Some("toolCall" | "tool_call" | "tool_use")
                    )
                })
            });
        if has_call {
            consecutive_empty_tool_use = 0;
        } else {
            diagnostics.tool_use_without_observed_call_turns += 1;
            consecutive_empty_tool_use = consecutive_empty_tool_use.saturating_add(1);
            diagnostics.max_consecutive_tool_use_without_observed_call_turns = diagnostics
                .max_consecutive_tool_use_without_observed_call_turns
                .max(consecutive_empty_tool_use);
        }
    }
    diagnostics
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
    pub counterpart: CliSurface,
    pub counterpart_resolved: usize,
    pub counterpart_fresh_token_wins: usize,
    /// Compatibility field populated only when the counterpart is Codex CLI.
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
    let counterpart = manifest
        .trials
        .iter()
        .map(|trial| trial.surface)
        .find(|surface| *surface != CliSurface::PiAgent)
        .ok_or_else(|| "comparison manifest must include PiAgent and one counterpart".to_owned())?;
    if !manifest
        .trials
        .iter()
        .any(|trial| trial.surface == CliSurface::PiAgent)
    {
        return Err("comparison manifest must include PiAgent".to_owned());
    }
    let mut piagent_resolved = 0usize;
    let mut counterpart_resolved = 0usize;
    for report in reports {
        if report.resolved {
            match report.surface {
                CliSurface::PiAgent => piagent_resolved += 1,
                surface if surface == counterpart => counterpart_resolved += 1,
                _ => {}
            }
        }
    }
    let mut ratios = Vec::new();
    let mut duration_ratios = Vec::new();
    let mut piagent_wins = 0usize;
    let mut counterpart_wins = 0usize;
    let mut ties = 0usize;
    let mut paired_runs = 0usize;
    for pi_trial in manifest
        .trials
        .iter()
        .filter(|trial| trial.surface == CliSurface::PiAgent)
    {
        let counterpart_trial = manifest.trials.iter().find(|trial| {
            trial.surface == counterpart
                && trial.scenario_id == pi_trial.scenario_id
                && trial.repeat == pi_trial.repeat
        });
        let (Some(pi), Some(other)) = (
            observed.get(pi_trial.trial_id.as_str()),
            counterpart_trial.and_then(|trial| observed.get(trial.trial_id.as_str())),
        ) else {
            continue;
        };
        paired_runs += 1;
        if pi.resolved
            && other.resolved
            && pi.usage.fresh_tokens > 0
            && other.usage.fresh_tokens > 0
        {
            ratios.push(pi.usage.fresh_tokens as f64 / other.usage.fresh_tokens as f64);
            if pi.duration_ms > 0 && other.duration_ms > 0 {
                duration_ratios.push(pi.duration_ms as f64 / other.duration_ms as f64);
            }
            match pi.usage.fresh_tokens.cmp(&other.usage.fresh_tokens) {
                std::cmp::Ordering::Less => piagent_wins += 1,
                std::cmp::Ordering::Greater => counterpart_wins += 1,
                std::cmp::Ordering::Equal => ties += 1,
            }
        }
    }
    let geometric_mean = (!ratios.is_empty())
        .then(|| (ratios.iter().map(|ratio| ratio.ln()).sum::<f64>() / ratios.len() as f64).exp());
    let mean_duration_ratio = (!duration_ratios.is_empty())
        .then(|| duration_ratios.iter().sum::<f64>() / duration_ratios.len() as f64);
    let quality_non_inferior = piagent_resolved >= counterpart_resolved;
    Ok(CliComparisonSummary {
        paired_runs,
        piagent_resolved,
        counterpart,
        counterpart_resolved,
        counterpart_fresh_token_wins: counterpart_wins,
        codex_resolved: if counterpart == CliSurface::CodexCli {
            counterpart_resolved
        } else {
            0
        },
        comparable_success_pairs: ratios.len(),
        piagent_fresh_token_wins: piagent_wins,
        codex_fresh_token_wins: if counterpart == CliSurface::CodexCli {
            counterpart_wins
        } else {
            0
        },
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
        if kind.is_dir() && entry.file_name() == ".git" {
            continue;
        }
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
            provider_base_url: None,
            provider_api_key_env: None,
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
            progress: CliProgressDiagnostics::default(),
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

    #[test]
    fn pi_progress_detects_tool_use_without_a_dispatchable_call() {
        let bytes = br#"{"type":"message_end","message":{"role":"assistant","stopReason":"toolUse","content":[]}}
{"type":"message_end","message":{"role":"assistant","stopReason":"toolUse","content":[{"type":"text","text":"retry"}]}}
{"type":"message_end","message":{"role":"assistant","stopReason":"toolUse","content":[{"type":"toolCall","name":"read"}]}}
"#;
        let progress = parse_pi_progress(bytes);
        assert_eq!(progress.model_turns, 3);
        assert_eq!(progress.tool_use_turns, 3);
        assert_eq!(progress.tool_use_without_observed_call_turns, 2);
        assert_eq!(
            progress.max_consecutive_tool_use_without_observed_call_turns,
            2
        );
    }

    #[tokio::test]
    async fn timed_out_process_retains_partial_output() {
        let args = vec![
            "-c".to_owned(),
            "printf 'before-timeout\\n'; while :; do :; done".to_owned(),
        ];
        let result = run_process("sh", &args, Path::new("/tmp"), None, &[], 1)
            .await
            .expect("process result is retained");
        assert!(result.timed_out);
        assert_eq!(result.stdout, b"before-timeout\n");
    }
}
