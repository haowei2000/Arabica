//! Controlled common-control comparison harness for five native agent stacks.
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

use arabica_protocol::{Command as RuntimeCommand, CommandEnvelope, CommandId, Event, WorkspaceId};
use arabica_provider::{ApiModelProvider, ApiProviderConfig, ApiType};
use arabica_runner::LocalRunner;
use arabica_runtime::{
    CoreRuntime, RuntimeArchiveStore, RuntimeCompactionStrategy, ShortMemoryPolicy,
};
use arabica_session::SessionManager;

use crate::tier_b::{ProviderCallObservation, ProviderRecorder, RecordingProvider};

pub const CLI_COMPARISON_SUITE_SCHEMA: &str = "structure.cli-comparison-suite/2026-08";
pub const CLI_COMPARISON_MANIFEST_SCHEMA: &str = "structure.cli-comparison-manifest/2026-09-v4";
pub const CLI_COMPARISON_REPORT_SCHEMA: &str = "structure.cli-comparison-report/2026-09-v4";
pub const PINNED_CODEX_VERSION: &str = "0.151.0";
pub const PINNED_PIAGENT_VERSION: &str = "1.6.1";
pub const PINNED_PIAGENT_REVISION_PREFIX: &str = "4aa91ef";
pub const PINNED_OPENCODE_VERSION: &str = "1.18.25";
pub const PINNED_AIDER_VERSION: &str = "0.86.0";
const MIN_FORMAL_PAIRED_RUNS: usize = 5;
const COMPARISON_BOOTSTRAP_RESAMPLES: usize = 10_000;
static PROCESS_CAPTURE_SEQUENCE: AtomicU64 = AtomicU64::new(0);

#[derive(Clone, Copy, Debug, Deserialize, Eq, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum CliSurface {
    PiAgent,
    CodexCli,
    Structure,
    OpenCode,
    Aider,
    StructureFullReplay,
    StructureShortMemory,
    StructureFileBackedGc,
}

impl CliSurface {
    pub const fn name(self) -> &'static str {
        match self {
            Self::PiAgent => "piagent",
            Self::CodexCli => "codex-cli",
            Self::Structure => "arabica",
            Self::OpenCode => "opencode",
            Self::Aider => "aider",
            Self::StructureFullReplay => "structure-full-replay",
            Self::StructureShortMemory => "structure-short-memory",
            Self::StructureFileBackedGc => "structure-file-backed-gc",
        }
    }

    const fn is_structure(self) -> bool {
        matches!(
            self,
            Self::Structure
                | Self::StructureFullReplay
                | Self::StructureShortMemory
                | Self::StructureFileBackedGc
        )
    }

    pub fn parse(value: &str) -> Result<Self, String> {
        match value {
            "piagent" => Ok(Self::PiAgent),
            "codex-cli" => Ok(Self::CodexCli),
            "arabica" => Ok(Self::Structure),
            "opencode" => Ok(Self::OpenCode),
            "aider" => Ok(Self::Aider),
            "structure-full-replay" => Ok(Self::StructureFullReplay),
            "structure-short-memory" => Ok(Self::StructureShortMemory),
            "structure-file-backed-gc" => Ok(Self::StructureFileBackedGc),
            _ => Err(format!(
                "unknown comparison surface {value}; expected piagent, codex-cli, opencode, aider, structure, structure-full-replay, structure-short-memory, or structure-file-backed-gc"
            )),
        }
    }
}

#[derive(Clone, Copy, Debug, Default, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum CliComparisonMode {
    #[default]
    AgentStack,
    ContextPolicy,
}

impl CliComparisonMode {
    pub const fn claim_tier(self) -> &'static str {
        match self {
            Self::AgentStack => "agent-stack-pilot",
            Self::ContextPolicy => "context-policy-ab",
        }
    }

    pub fn parse(value: &str) -> Result<Self, String> {
        match value {
            "agent-stack" => Ok(Self::AgentStack),
            "context-policy" => Ok(Self::ContextPolicy),
            _ => Err(format!(
                "unknown comparison mode {value}; expected agent-stack or context-policy"
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
    pub benchmark_mode: CliComparisonMode,
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
    pub opencode_program: String,
    pub aider_program: String,
    pub piagent_package_root: String,
    #[serde(default)]
    pub provider_base_url: Option<String>,
    #[serde(default)]
    pub provider_api_key_env: Option<String>,
    pub trials: Vec<CliTrial>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CliComparisonPlan {
    pub benchmark_mode: CliComparisonMode,
    pub model: String,
    pub thinking: String,
    pub repetitions: usize,
    pub timeout_seconds: u64,
    pub seed: u64,
    pub codex_program: String,
    pub pi_program: String,
    pub opencode_program: String,
    pub aider_program: String,
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
    let unique_surfaces: BTreeSet<_> = plan.surfaces.iter().copied().collect();
    let required_count = match plan.benchmark_mode {
        CliComparisonMode::AgentStack => 5,
        CliComparisonMode::ContextPolicy => 2,
    };
    if plan.surfaces.len() != required_count || unique_surfaces.len() != required_count {
        return Err(format!(
            "{} distinct comparison surfaces are required",
            required_count
        ));
    }
    validate_mode_surfaces(plan.benchmark_mode, &plan.surfaces)?;
    if plan.surfaces.iter().any(|surface| surface.is_structure())
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
            let rotation = (stable_seed(plan.seed, &scenario.id, 0) as usize + repeat - 1)
                % plan.surfaces.len();
            for offset in 0..plan.surfaces.len() {
                let surface = plan.surfaces[(rotation + offset) % plan.surfaces.len()];
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
        benchmark_mode: plan.benchmark_mode,
        claim_tier: plan.benchmark_mode.claim_tier().to_owned(),
        suite_path: suite_path.to_string_lossy().into_owned(),
        suite_digest,
        model: plan.model,
        thinking: plan.thinking,
        repetitions: plan.repetitions,
        timeout_seconds: plan.timeout_seconds,
        seed: plan.seed,
        codex_program: plan.codex_program,
        pi_program: plan.pi_program,
        opencode_program: plan.opencode_program,
        aider_program: plan.aider_program,
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
        if self.claim_tier != self.benchmark_mode.claim_tier() {
            return Err(format!(
                "claim tier {} does not match benchmark mode",
                self.claim_tier
            ));
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
        let surfaces: BTreeSet<_> = self.trials.iter().map(|trial| trial.surface).collect();
        validate_mode_surfaces(
            self.benchmark_mode,
            &surfaces.iter().copied().collect::<Vec<_>>(),
        )?;
        for trial in &self.trials {
            if !ids.insert(&trial.trial_id) || !artifacts.insert(&trial.artifact_path) {
                return Err("trial ids and artifact paths must be unique".to_owned());
            }
        }
        Ok(())
    }
}

fn validate_mode_surfaces(mode: CliComparisonMode, surfaces: &[CliSurface]) -> Result<(), String> {
    let actual: BTreeSet<_> = surfaces.iter().copied().collect();
    match mode {
        CliComparisonMode::AgentStack => {
            let expected = BTreeSet::from([
                CliSurface::Structure,
                CliSurface::CodexCli,
                CliSurface::PiAgent,
                CliSurface::OpenCode,
                CliSurface::Aider,
            ]);
            let legacy_pair = actual.len() == 2
                && !actual.iter().any(|surface| {
                    matches!(
                        surface,
                        CliSurface::StructureFullReplay
                            | CliSurface::StructureShortMemory
                            | CliSurface::StructureFileBackedGc
                    )
                });
            if actual != expected && !legacy_pair {
                return Err(
                    "agent-stack mode requires Structure, Codex CLI, PiAgent, OpenCode, and Aider"
                        .to_owned(),
                );
            }
        }
        CliComparisonMode::ContextPolicy => {
            let expected = BTreeSet::from([
                CliSurface::StructureFullReplay,
                CliSurface::StructureFileBackedGc,
            ]);
            if actual != expected {
                return Err(
                    "context-policy mode requires exactly structure-full-replay and structure-file-backed-gc"
                        .to_owned(),
                );
            }
        }
    }
    Ok(())
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct ExecutableProbe {
    pub program: String,
    pub resolved_path: Option<String>,
    pub sha256: Option<String>,
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
    pub opencode: ExecutableProbe,
    pub aider: ExecutableProbe,
    pub piagent_package_ready: bool,
    pub piagent_declared_version: Option<String>,
    pub piagent_git_revision: Option<String>,
    pub piagent_git_clean: bool,
    pub piagent_dirty_digest: Option<String>,
    pub structure_git_revision: Option<String>,
    pub structure_git_clean: bool,
    pub structure_dirty_digest: Option<String>,
    pub harness_executable_sha256: Option<String>,
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
    let opencode = probe(&manifest.opencode_program).await;
    let aider = probe(&manifest.aider_program).await;
    let package_root = Path::new(&manifest.piagent_package_root);
    let piagent_package_ready = [
        "scripts/init-project.sh",
        "packages/piagent-core/extensions/piagent-guard.ts",
        "packages/piagent-core/skills",
    ]
    .iter()
    .all(|path| package_root.join(path).exists());
    let piagent_declared_version = std::fs::read(package_root.join("package.json"))
        .ok()
        .and_then(|bytes| serde_json::from_slice::<Value>(&bytes).ok())
        .and_then(|value| {
            value
                .get("version")
                .and_then(Value::as_str)
                .map(str::to_owned)
        });
    let (piagent_git_revision, piagent_git_clean, piagent_dirty_digest) =
        probe_git_checkout(package_root).await;
    let structure_root = Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
    let (structure_git_revision, structure_git_clean, structure_dirty_digest) =
        probe_git_checkout(&structure_root).await;
    let harness_executable_sha256 = std::env::current_exe()
        .ok()
        .and_then(|path| std::fs::read(path).ok())
        .map(|bytes| sha256(&bytes));
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
    if surfaces.contains(&CliSurface::PiAgent)
        && piagent_declared_version.as_deref() != Some(PINNED_PIAGENT_VERSION)
    {
        blockers.push("piagent-version-mismatch".to_owned());
    }
    if surfaces.contains(&CliSurface::OpenCode) && !opencode.available {
        blockers.push("opencode-unavailable".to_owned());
    }
    if surfaces.contains(&CliSurface::Aider) && !aider.available {
        blockers.push("aider-unavailable".to_owned());
    }
    if surfaces.contains(&CliSurface::CodexCli)
        && !codex
            .version
            .as_deref()
            .is_some_and(|value| version_matches(value, PINNED_CODEX_VERSION))
    {
        blockers.push("codex-cli-version-mismatch".to_owned());
    }
    if surfaces.contains(&CliSurface::OpenCode)
        && !opencode
            .version
            .as_deref()
            .is_some_and(|value| version_matches(value, PINNED_OPENCODE_VERSION))
    {
        blockers.push("opencode-version-mismatch".to_owned());
    }
    if surfaces.contains(&CliSurface::Aider)
        && !aider
            .version
            .as_deref()
            .is_some_and(|value| version_matches(value, PINNED_AIDER_VERSION))
    {
        blockers.push("aider-version-mismatch".to_owned());
    }
    if surfaces.contains(&CliSurface::PiAgent) && !piagent_package_ready {
        blockers.push("piagent-package-root-not-ready".to_owned());
    }
    if surfaces.contains(&CliSurface::PiAgent) && !piagent_git_clean {
        blockers.push("piagent-package-root-not-clean".to_owned());
    }
    if surfaces.contains(&CliSurface::PiAgent)
        && !piagent_git_revision
            .as_deref()
            .is_some_and(|value| value.starts_with(PINNED_PIAGENT_REVISION_PREFIX))
    {
        blockers.push("piagent-revision-mismatch".to_owned());
    }
    if surfaces.iter().any(|surface| surface.is_structure())
        && (manifest.provider_base_url.is_none() || manifest.provider_api_key_env.is_none())
    {
        blockers.push("arabica-provider-configuration-missing".to_owned());
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
        opencode,
        aider,
        piagent_package_ready,
        piagent_declared_version,
        piagent_git_revision,
        piagent_git_clean,
        piagent_dirty_digest,
        structure_git_revision,
        structure_git_clean,
        structure_dirty_digest,
        harness_executable_sha256,
        ready: blockers.is_empty(),
        blockers,
    }
}

fn version_matches(observed: &str, pinned: &str) -> bool {
    observed
        .split(|character: char| character.is_whitespace() || character == ',')
        .any(|token| token == pinned || token == format!("v{pinned}"))
}

async fn probe(program: &str) -> ExecutableProbe {
    let resolved = resolve_executable(program);
    let resolved_path = resolved
        .as_ref()
        .map(|path| path.to_string_lossy().into_owned());
    let executable_sha256 = resolved
        .as_ref()
        .and_then(|path| std::fs::read(path).ok())
        .map(|bytes| sha256(&bytes));
    match Command::new(program).arg("--version").output().await {
        Ok(output) if output.status.success() => ExecutableProbe {
            program: program.to_owned(),
            resolved_path,
            sha256: executable_sha256,
            available: true,
            version: Some(String::from_utf8_lossy(&output.stdout).trim().to_owned()),
            error: None,
        },
        Ok(output) => ExecutableProbe {
            program: program.to_owned(),
            resolved_path,
            sha256: executable_sha256,
            available: false,
            version: None,
            error: Some(String::from_utf8_lossy(&output.stderr).trim().to_owned()),
        },
        Err(error) => ExecutableProbe {
            program: program.to_owned(),
            resolved_path,
            sha256: executable_sha256,
            available: false,
            version: None,
            error: Some(error.to_string()),
        },
    }
}

fn resolve_executable(program: &str) -> Option<PathBuf> {
    let requested = Path::new(program);
    if requested.components().count() > 1 {
        return requested.canonicalize().ok();
    }
    std::env::var_os("PATH")
        .into_iter()
        .flat_map(|value| std::env::split_paths(&value).collect::<Vec<_>>())
        .map(|directory| directory.join(program))
        .find(|candidate| candidate.is_file())
        .and_then(|candidate| candidate.canonicalize().ok())
}

async fn probe_git_checkout(root: &Path) -> (Option<String>, bool, Option<String>) {
    let revision = Command::new("git")
        .args(["-C", &root.to_string_lossy(), "rev-parse", "HEAD"])
        .output()
        .await
        .ok()
        .filter(|output| output.status.success())
        .map(|output| String::from_utf8_lossy(&output.stdout).trim().to_owned());
    let status = Command::new("git")
        .args([
            "-C",
            &root.to_string_lossy(),
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        ])
        .output()
        .await
        .ok()
        .filter(|output| output.status.success());
    let clean = status
        .as_ref()
        .is_some_and(|output| output.stdout.is_empty());
    let dirty_digest = if clean {
        None
    } else if let Some(status) = status {
        let mut digest = Sha256::new();
        digest.update(&status.stdout);
        if let Ok(diff) = Command::new("git")
            .args(["-C", &root.to_string_lossy(), "diff", "--binary", "HEAD"])
            .output()
            .await
            && diff.status.success()
        {
            digest.update(&diff.stdout);
        }
        if let Ok(untracked) = Command::new("git")
            .args([
                "-C",
                &root.to_string_lossy(),
                "ls-files",
                "--others",
                "--exclude-standard",
                "-z",
            ])
            .output()
            .await
            && untracked.status.success()
        {
            for relative in untracked.stdout.split(|byte| *byte == 0) {
                if relative.is_empty() {
                    continue;
                }
                digest.update((relative.len() as u64).to_be_bytes());
                digest.update(relative);
                if let Ok(bytes) =
                    std::fs::read(root.join(String::from_utf8_lossy(relative).as_ref()))
                {
                    digest.update((bytes.len() as u64).to_be_bytes());
                    digest.update(bytes);
                }
            }
        }
        Some(format!("sha256:{}", hex::encode(digest.finalize())))
    } else {
        None
    };
    (revision, clean, dirty_digest)
}

#[derive(Clone, Debug, Default, Deserialize, Eq, PartialEq, Serialize)]
pub struct CliUsage {
    pub input_tokens: u64,
    pub cached_input_tokens: u64,
    pub cache_write_input_tokens: u64,
    pub output_tokens: u64,
    pub reasoning_output_tokens: u64,
    /// Provider input that was not served from a read cache. This excludes
    /// model output so context-policy claims cannot be won by shorter answers.
    #[serde(default)]
    pub fresh_input_tokens: u64,
    /// Fresh input plus model output, used as the agent-stack economic metric.
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
    pub gc_admission_checks: u64,
    pub gc_admissions: u64,
    pub memory_pointer_provider_calls: u64,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct CliTrialReport {
    pub schema_version: String,
    pub benchmark_mode: CliComparisonMode,
    pub claim_tier: String,
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
    #[serde(default)]
    pub stdout_artifact: String,
    #[serde(default)]
    pub stderr_artifact: String,
    pub stdout_sha256: String,
    pub stderr_sha256: String,
}

pub async fn run_cli_trial(
    manifest: &CliComparisonManifest,
    trial_id: &str,
    output_root: &Path,
) -> Result<CliTrialReport, String> {
    let output_root = if output_root.is_absolute() {
        output_root.to_path_buf()
    } else {
        std::env::current_dir()
            .map_err(|error| format!("cannot resolve current directory: {error}"))?
            .join(output_root)
    };
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
            let env = prepare_codex_runtime(&workspace_root)?;
            run_process(
                &manifest.codex_program,
                &codex_args(manifest, &workspace),
                &workspace,
                Some(prompt.as_bytes()),
                &env,
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
        CliSurface::OpenCode => {
            let env = prepare_external_runtime(manifest, &workspace_root, "opencode")?;
            run_process(
                &manifest.opencode_program,
                &opencode_args(manifest, &prompt),
                &workspace,
                None,
                &env,
                manifest.timeout_seconds,
            )
            .await?
        }
        CliSurface::Aider => {
            let env = prepare_external_runtime(manifest, &workspace_root, "aider")?;
            run_process(
                &manifest.aider_program,
                &aider_args(manifest, &workspace_root, &prompt),
                &workspace,
                None,
                &env,
                manifest.timeout_seconds,
            )
            .await?
        }
        CliSurface::Structure
        | CliSurface::StructureFullReplay
        | CliSurface::StructureShortMemory
        | CliSurface::StructureFileBackedGc => {
            match tokio::time::timeout(
                Duration::from_secs(manifest.timeout_seconds),
                run_structure_trial(
                    manifest,
                    trial.surface,
                    &workspace_root,
                    &workspace,
                    &prompt,
                ),
            )
            .await
            {
                Ok(result) => result?,
                Err(_) => structure_timeout_result(&workspace_root)?,
            }
        }
    };
    let duration_ms = started.elapsed().as_millis().try_into().unwrap_or(u64::MAX);
    let process_artifact_root = workspace_root.join("process-output");
    std::fs::create_dir(&process_artifact_root)
        .map_err(|error| format!("cannot create process artifact directory: {error}"))?;
    let stdout_path = process_artifact_root.join("stdout.bin");
    let stderr_path = process_artifact_root.join("stderr.bin");
    std::fs::write(&stdout_path, &process.stdout)
        .map_err(|error| format!("cannot persist process stdout: {error}"))?;
    std::fs::write(&stderr_path, &process.stderr)
        .map_err(|error| format!("cannot persist process stderr: {error}"))?;
    let stdout_artifact = stdout_path
        .strip_prefix(&output_root)
        .map_err(|error| format!("cannot relativize stdout artifact: {error}"))?
        .to_string_lossy()
        .into_owned();
    let stderr_artifact = stderr_path
        .strip_prefix(&output_root)
        .map_err(|error| format!("cannot relativize stderr artifact: {error}"))?
        .to_string_lossy()
        .into_owned();
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
        CliSurface::OpenCode => parse_openai_compatible_usage(&process.stdout, "opencode-json"),
        CliSurface::Aider => parse_aider_usage(&process.stdout, &process.stderr),
        CliSurface::Structure
        | CliSurface::StructureFullReplay
        | CliSurface::StructureShortMemory
        | CliSurface::StructureFileBackedGc => process.usage.clone().unwrap_or_default(),
    };
    let progress = match trial.surface {
        CliSurface::PiAgent => parse_pi_progress(&process.stdout),
        CliSurface::CodexCli => CliProgressDiagnostics::default(),
        CliSurface::OpenCode | CliSurface::Aider => CliProgressDiagnostics::default(),
        CliSurface::Structure
        | CliSurface::StructureFullReplay
        | CliSurface::StructureShortMemory
        | CliSurface::StructureFileBackedGc => process.progress.clone().unwrap_or_default(),
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
        benchmark_mode: manifest.benchmark_mode,
        claim_tier: manifest.claim_tier.clone(),
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
        stdout_artifact,
        stderr_artifact,
        stdout_sha256: sha256(&process.stdout),
        stderr_sha256: sha256(&process.stderr),
    })
}

fn prepare_codex_runtime(workspace_root: &Path) -> Result<Vec<(String, String)>, String> {
    // Ignore-user-config alone still reads personal auth, history, and state.
    // Keep the actual Codex home outside the task's scored file tree.
    let codex_home = workspace_root.join("codex-home");
    std::fs::create_dir(&codex_home)
        .map_err(|error| format!("cannot create isolated Codex home: {error}"))?;
    let codex_home = codex_home
        .canonicalize()
        .map_err(|error| format!("cannot resolve isolated Codex home: {error}"))?;
    Ok(vec![(
        "CODEX_HOME".to_owned(),
        codex_home.to_string_lossy().into_owned(),
    )])
}

fn prepare_external_runtime(
    manifest: &CliComparisonManifest,
    workspace_root: &Path,
    agent: &str,
) -> Result<Vec<(String, String)>, String> {
    let config_root = workspace_root.join(format!("{agent}-config"));
    let data_root = workspace_root.join(format!("{agent}-data"));
    let cache_root = workspace_root.join(format!("{agent}-cache"));
    for path in [&config_root, &data_root, &cache_root] {
        std::fs::create_dir(path)
            .map_err(|error| format!("cannot create isolated {agent} directory: {error}"))?;
    }
    let mut environment = vec![
        (
            "XDG_CONFIG_HOME".to_owned(),
            config_root.to_string_lossy().into_owned(),
        ),
        (
            "XDG_DATA_HOME".to_owned(),
            data_root.to_string_lossy().into_owned(),
        ),
        (
            "XDG_CACHE_HOME".to_owned(),
            cache_root.to_string_lossy().into_owned(),
        ),
        ("NO_COLOR".to_owned(), "1".to_owned()),
    ];
    if let (Some(base_url), Some(api_key_env)) =
        (&manifest.provider_base_url, &manifest.provider_api_key_env)
    {
        let api_key = std::env::var(api_key_env)
            .map_err(|_| format!("provider key environment {api_key_env} is unavailable"))?;
        environment.push(("OPENAI_BASE_URL".to_owned(), base_url.clone()));
        environment.push(("OPENAI_API_KEY".to_owned(), api_key));
    }
    Ok(environment)
}

fn opencode_args(manifest: &CliComparisonManifest, prompt: &str) -> Vec<String> {
    vec![
        "run".to_owned(),
        "--format".to_owned(),
        "json".to_owned(),
        "--model".to_owned(),
        manifest.model.clone(),
        prompt.to_owned(),
    ]
}

fn aider_args(
    manifest: &CliComparisonManifest,
    workspace_root: &Path,
    prompt: &str,
) -> Vec<String> {
    let model = manifest
        .model
        .split_once('/')
        .map_or(manifest.model.as_str(), |(_, model)| model);
    vec![
        "--model".to_owned(),
        format!("openai/{model}"),
        "--message".to_owned(),
        prompt.to_owned(),
        "--yes-always".to_owned(),
        "--no-auto-commits".to_owned(),
        "--no-gitignore".to_owned(),
        "--no-check-update".to_owned(),
        "--analytics-disable".to_owned(),
        "--map-tokens".to_owned(),
        "0".to_owned(),
        "--chat-history-file".to_owned(),
        workspace_root
            .join("aider-chat-history.md")
            .to_string_lossy()
            .into_owned(),
    ]
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
    surface: CliSurface,
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
    let mut provider_config =
        ApiProviderConfig::new(ApiType::OpenAiResponses, api_key, base_url, model)
            .with_thinking(manifest.thinking != "off")
            .with_request_timeout_secs(manifest.timeout_seconds)
            .with_raw_exchange_dir(workspace_root.join("provider-raw"));
    if manifest.thinking != "off" {
        provider_config = provider_config.with_reasoning_effort(manifest.thinking.clone());
    }
    let provider = ApiModelProvider::new(provider_config)
        .map_err(|error| format!("cannot initialize Structure provider: {error}"))?;
    let recorder =
        ProviderRecorder::with_snapshot_path(workspace_root.join("provider-calls.partial.json"));
    let (short_memory_policy, compaction_strategy) = match surface {
        CliSurface::StructureFullReplay => (
            ShortMemoryPolicy::full_replay(),
            RuntimeCompactionStrategy::Disabled,
        ),
        CliSurface::StructureFileBackedGc => (
            ShortMemoryPolicy::ttl_only(),
            RuntimeCompactionStrategy::FileBackedGc,
        ),
        CliSurface::Structure | CliSurface::StructureShortMemory => (
            ShortMemoryPolicy::default(),
            RuntimeCompactionStrategy::Disabled,
        ),
        CliSurface::PiAgent | CliSurface::CodexCli | CliSurface::OpenCode | CliSurface::Aider => {
            return Err("non-Structure surface reached Structure runtime".to_owned());
        }
    };
    let mut runtime = CoreRuntime::with_memory_configuration(
        RecordingProvider::new(provider, recorder.clone()),
        LocalRunner::new(workspace),
        short_memory_policy,
        false,
        RuntimeArchiveStore::File {
            root: workspace_root.join("runtime-memory"),
        },
    );
    runtime.set_compaction_strategy(compaction_strategy);
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
    let gc_admission_checks = manager.runtime().pointer_gc_admission_observations().len() as u64;
    let gc_admissions = manager
        .runtime()
        .pointer_gc_admission_observations()
        .iter()
        .filter(|observation| observation.admitted)
        .count() as u64;
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
                call.finish_reason
                    .as_ref()
                    .is_some_and(|reason| matches!(reason, arabica_model::FinishReason::ToolCalls))
            })
            .count() as u64,
        tool_use_without_observed_call_turns: u64::from(protocol_empty_tool_use),
        max_consecutive_tool_use_without_observed_call_turns: u64::from(protocol_empty_tool_use),
        failed_tool_results: events
            .iter()
            .filter(|event| matches!(event.event, Event::ToolCallCompleted { is_error: true, .. }))
            .count() as u64,
        gc_admission_checks,
        gc_admissions,
        memory_pointer_provider_calls: calls
            .iter()
            .filter(|call| call.memory_pointer_entries > 0)
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
    events: &[arabica_protocol::EventEnvelope],
) -> CliUsage {
    let mut usage = CliUsage {
        input_tokens: calls.iter().map(|call| call.input_tokens).sum(),
        cached_input_tokens: calls.iter().map(|call| call.cached_input_tokens).sum(),
        cache_write_input_tokens: calls
            .iter()
            .map(|call| call.cache_creation_input_tokens)
            .sum(),
        output_tokens: calls.iter().map(|call| call.output_tokens).sum(),
        reasoning_output_tokens: calls.iter().map(|call| call.reasoning_output_tokens).sum(),
        fresh_input_tokens: 0,
        fresh_tokens: 0,
        tool_calls: events
            .iter()
            .filter(|event| matches!(event.event, Event::ToolCallRequested { .. }))
            .count() as u64,
        event_count: events.len() as u64,
        source: "arabica-provider-observations".to_owned(),
    };
    usage.fresh_tokens = usage
        .input_tokens
        .saturating_sub(usage.cached_input_tokens)
        .saturating_add(usage.output_tokens);
    usage.fresh_input_tokens = usage.input_tokens.saturating_sub(usage.cached_input_tokens);
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
            memory_pointer_provider_calls: calls
                .iter()
                .filter(|call| call.memory_pointer_entries > 0)
                .count() as u64,
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
        "arabica-cli-capture-{}-{capture_id}",
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
    usage.fresh_input_tokens = usage.input_tokens.saturating_sub(usage.cached_input_tokens);
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
    usage.fresh_input_tokens = usage.input_tokens;
    usage
}

fn parse_openai_compatible_usage(bytes: &[u8], source: &str) -> CliUsage {
    let mut usage = CliUsage {
        source: source.to_owned(),
        ..CliUsage::default()
    };
    for value in json_lines(bytes) {
        usage.event_count += 1;
        let observed = value
            .get("usage")
            .or_else(|| value.pointer("/part/usage"))
            .or_else(|| value.pointer("/message/usage"));
        let Some(observed) = observed else { continue };
        usage.input_tokens += field(observed, "input_tokens").max(field(observed, "input"));
        usage.cached_input_tokens +=
            field(observed, "cached_input_tokens").max(field(observed, "cache_read_input_tokens"));
        usage.output_tokens += field(observed, "output_tokens").max(field(observed, "output"));
        usage.reasoning_output_tokens +=
            field(observed, "reasoning_output_tokens").max(field(observed, "reasoning_tokens"));
    }
    usage.cached_input_tokens = usage.cached_input_tokens.min(usage.input_tokens);
    usage.fresh_input_tokens = usage.input_tokens.saturating_sub(usage.cached_input_tokens);
    usage.fresh_tokens = usage.fresh_input_tokens.saturating_add(usage.output_tokens);
    usage
}

fn parse_aider_usage(stdout: &[u8], stderr: &[u8]) -> CliUsage {
    let mut combined = stdout.to_vec();
    combined.extend_from_slice(stderr);
    let mut usage = parse_openai_compatible_usage(&combined, "aider-output");
    if usage.event_count == 0 {
        usage.event_count = combined.split(|byte| *byte == b'\n').count() as u64;
    }
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
    pub benchmark_mode: CliComparisonMode,
    pub claim_tier: String,
    pub cost_metric: String,
    pub paired_runs: usize,
    pub mechanism_activated_pairs: usize,
    pub mechanism_gate_passed: bool,
    pub surface_a: CliSurface,
    pub surface_a_resolved: usize,
    pub surface_b: CliSurface,
    pub surface_b_resolved: usize,
    pub surface_a_fresh_token_wins: usize,
    pub surface_b_fresh_token_wins: usize,
    /// Geometric mean of surface A fresh tokens divided by surface B.
    pub geometric_mean_fresh_token_ratio_a_over_b: Option<f64>,
    pub fresh_token_ratio_ci95_lower_a_over_b: Option<f64>,
    pub fresh_token_ratio_ci95_upper_a_over_b: Option<f64>,
    /// Mean of surface A duration divided by surface B.
    pub mean_duration_ratio_a_over_b: Option<f64>,
    pub surface_a_quality_non_inferior: bool,
    pub surface_a_token_improvement: bool,
    /// Set only when one surface is quality-non-inferior and its paired fresh
    /// token ratio is significant under the frozen 10,000-resample bootstrap.
    pub overall_winner: Option<CliSurface>,
    /// Deprecated PiAgent-oriented compatibility fields.
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

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct StructureOpponentSummary {
    pub opponent: CliSurface,
    pub paired_runs: usize,
    pub comparable_success_pairs: usize,
    pub structure_resolved: usize,
    pub opponent_resolved: usize,
    pub structure_quality_non_inferior: bool,
    pub structure_fresh_token_wins: usize,
    pub opponent_fresh_token_wins: usize,
    pub geometric_mean_fresh_token_ratio_structure_over_opponent: Option<f64>,
    pub fresh_token_ratio_ci95_lower_structure_over_opponent: Option<f64>,
    pub fresh_token_ratio_ci95_upper_structure_over_opponent: Option<f64>,
    pub structure_token_improvement: bool,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct CliMultiAgentSummary {
    pub benchmark_mode: CliComparisonMode,
    pub claim_tier: String,
    pub cost_metric: String,
    pub comparisons: Vec<StructureOpponentSummary>,
}

pub fn summarize_cli_multi_agent(
    manifest: &CliComparisonManifest,
    reports: &[CliTrialReport],
) -> Result<CliMultiAgentSummary, String> {
    manifest.validate()?;
    let expected = BTreeSet::from([
        CliSurface::Structure,
        CliSurface::CodexCli,
        CliSurface::PiAgent,
        CliSurface::OpenCode,
        CliSurface::Aider,
    ]);
    let observed_surfaces = manifest
        .trials
        .iter()
        .map(|trial| trial.surface)
        .collect::<BTreeSet<_>>();
    if manifest.benchmark_mode != CliComparisonMode::AgentStack || observed_surfaces != expected {
        return Err("multi-agent summary requires the frozen five-agent manifest".to_owned());
    }
    let mut comparisons = Vec::new();
    for opponent in [
        CliSurface::CodexCli,
        CliSurface::PiAgent,
        CliSurface::OpenCode,
        CliSurface::Aider,
    ] {
        let mut pair_manifest = manifest.clone();
        pair_manifest.trials.retain(|trial| {
            matches!(trial.surface, CliSurface::Structure) || trial.surface == opponent
        });
        let pair_reports = reports
            .iter()
            .filter(|report| {
                matches!(report.surface, CliSurface::Structure) || report.surface == opponent
            })
            .cloned()
            .collect::<Vec<_>>();
        let pair = summarize_cli_comparison(&pair_manifest, &pair_reports)?;
        let structure_is_a = pair.surface_a == CliSurface::Structure;
        let ratio = pair
            .geometric_mean_fresh_token_ratio_a_over_b
            .map(|value| if structure_is_a { value } else { 1.0 / value });
        let (ci_lower, ci_upper) = if structure_is_a {
            (
                pair.fresh_token_ratio_ci95_lower_a_over_b,
                pair.fresh_token_ratio_ci95_upper_a_over_b,
            )
        } else {
            (
                pair.fresh_token_ratio_ci95_upper_a_over_b
                    .map(|value| 1.0 / value),
                pair.fresh_token_ratio_ci95_lower_a_over_b
                    .map(|value| 1.0 / value),
            )
        };
        let structure_resolved = if structure_is_a {
            pair.surface_a_resolved
        } else {
            pair.surface_b_resolved
        };
        let opponent_resolved = if structure_is_a {
            pair.surface_b_resolved
        } else {
            pair.surface_a_resolved
        };
        let structure_quality_non_inferior = structure_resolved >= opponent_resolved;
        comparisons.push(StructureOpponentSummary {
            opponent,
            paired_runs: pair.paired_runs,
            comparable_success_pairs: pair.comparable_success_pairs,
            structure_resolved,
            opponent_resolved,
            structure_quality_non_inferior,
            structure_fresh_token_wins: if structure_is_a {
                pair.surface_a_fresh_token_wins
            } else {
                pair.surface_b_fresh_token_wins
            },
            opponent_fresh_token_wins: if structure_is_a {
                pair.surface_b_fresh_token_wins
            } else {
                pair.surface_a_fresh_token_wins
            },
            geometric_mean_fresh_token_ratio_structure_over_opponent: ratio,
            fresh_token_ratio_ci95_lower_structure_over_opponent: ci_lower,
            fresh_token_ratio_ci95_upper_structure_over_opponent: ci_upper,
            structure_token_improvement: structure_quality_non_inferior
                && pair.comparable_success_pairs >= MIN_FORMAL_PAIRED_RUNS
                && ci_upper.is_some_and(|upper| upper < 1.0),
        });
    }
    Ok(CliMultiAgentSummary {
        benchmark_mode: manifest.benchmark_mode,
        claim_tier: manifest.claim_tier.clone(),
        cost_metric: "fresh_input_plus_output_tokens".to_owned(),
        comparisons,
    })
}

pub fn summarize_cli_comparison(
    manifest: &CliComparisonManifest,
    reports: &[CliTrialReport],
) -> Result<CliComparisonSummary, String> {
    manifest.validate()?;
    for report in reports {
        if report.schema_version != CLI_COMPARISON_REPORT_SCHEMA
            || report.benchmark_mode != manifest.benchmark_mode
            || report.claim_tier != manifest.claim_tier
            || report.requested_model != manifest.model
            || report.requested_thinking != manifest.thinking
        {
            return Err(format!(
                "report {} metadata does not match manifest",
                report.trial_id
            ));
        }
        let Some(trial) = manifest
            .trials
            .iter()
            .find(|trial| trial.trial_id == report.trial_id)
        else {
            return Err(format!("report {} is not in manifest", report.trial_id));
        };
        if report.surface != trial.surface
            || report.scenario_id != trial.scenario_id
            || report.repeat != trial.repeat
        {
            return Err(format!(
                "report {} treatment does not match manifest",
                report.trial_id
            ));
        }
    }
    let observed: BTreeMap<_, _> = reports
        .iter()
        .map(|report| (report.trial_id.as_str(), report))
        .collect();
    if observed.len() != reports.len() {
        return Err("duplicate trial reports are forbidden".to_owned());
    }
    let surfaces = manifest
        .trials
        .iter()
        .map(|trial| trial.surface)
        .collect::<BTreeSet<_>>();
    if surfaces.len() != 2 {
        return Err("comparison manifest must include exactly two surfaces".to_owned());
    }
    let mut surfaces = surfaces.into_iter();
    let surface_a = surfaces.next().expect("two surfaces were checked");
    let surface_b = surfaces.next().expect("two surfaces were checked");
    let counterpart = surface_b;
    let mut surface_a_resolved = 0usize;
    let mut surface_b_resolved = 0usize;
    for report in reports {
        if report.resolved {
            match report.surface {
                surface if surface == surface_a => surface_a_resolved += 1,
                surface if surface == surface_b => surface_b_resolved += 1,
                _ => {}
            }
        }
    }
    let mut ratios = Vec::new();
    let mut duration_ratios = Vec::new();
    let mut surface_a_wins = 0usize;
    let mut surface_b_wins = 0usize;
    let mut ties = 0usize;
    let mut paired_runs = 0usize;
    let mut mechanism_activated_pairs = 0usize;
    for first_trial in manifest
        .trials
        .iter()
        .filter(|trial| trial.surface == surface_a)
    {
        let second_trial = manifest.trials.iter().find(|trial| {
            trial.surface == surface_b
                && trial.scenario_id == first_trial.scenario_id
                && trial.repeat == first_trial.repeat
        });
        let (Some(first), Some(second)) = (
            observed.get(first_trial.trial_id.as_str()),
            second_trial.and_then(|trial| observed.get(trial.trial_id.as_str())),
        ) else {
            continue;
        };
        paired_runs += 1;
        let first_cost = comparison_cost(manifest.benchmark_mode, &first.usage);
        let second_cost = comparison_cost(manifest.benchmark_mode, &second.usage);
        if first.resolved && second.resolved && first_cost > 0 && second_cost > 0 {
            let mechanism_activated = manifest.benchmark_mode == CliComparisonMode::AgentStack
                || [*first, *second].into_iter().any(|report| {
                    report.surface == CliSurface::StructureFileBackedGc
                        && report.progress.gc_admissions > 0
                        && report.progress.memory_pointer_provider_calls > 0
                });
            mechanism_activated_pairs += usize::from(mechanism_activated);
            ratios.push(first_cost as f64 / second_cost as f64);
            if first.duration_ms > 0 && second.duration_ms > 0 {
                duration_ratios.push(first.duration_ms as f64 / second.duration_ms as f64);
            }
            match first_cost.cmp(&second_cost) {
                std::cmp::Ordering::Less => surface_a_wins += 1,
                std::cmp::Ordering::Greater => surface_b_wins += 1,
                std::cmp::Ordering::Equal => ties += 1,
            }
        }
    }
    let geometric_mean = (!ratios.is_empty())
        .then(|| (ratios.iter().map(|ratio| ratio.ln()).sum::<f64>() / ratios.len() as f64).exp());
    let (ratio_ci95_lower, ratio_ci95_upper) =
        bootstrap_ratio_ci95(&ratios, manifest.seed ^ 0x434c_4943_4f4d_5032);
    let mean_duration_ratio = (!duration_ratios.is_empty())
        .then(|| duration_ratios.iter().sum::<f64>() / duration_ratios.len() as f64);
    let quality_non_inferior = surface_a_resolved >= surface_b_resolved;
    let surface_b_quality_non_inferior = surface_b_resolved >= surface_a_resolved;
    let sufficient_pairs = ratios.len() >= MIN_FORMAL_PAIRED_RUNS;
    let mechanism_gate_passed = manifest.benchmark_mode == CliComparisonMode::AgentStack
        || (!ratios.is_empty() && mechanism_activated_pairs == ratios.len());
    let surface_a_token_improvement = quality_non_inferior
        && sufficient_pairs
        && mechanism_gate_passed
        && ratio_ci95_upper.is_some_and(|upper| upper < 1.0);
    let surface_b_token_improvement = surface_b_quality_non_inferior
        && sufficient_pairs
        && mechanism_gate_passed
        && ratio_ci95_lower.is_some_and(|lower| lower > 1.0);
    let overall_winner = if surface_a_token_improvement {
        Some(surface_a)
    } else if surface_b_token_improvement {
        Some(surface_b)
    } else {
        None
    };
    let piagent_is_a = surface_a == CliSurface::PiAgent;
    let piagent_is_b = surface_b == CliSurface::PiAgent;
    Ok(CliComparisonSummary {
        benchmark_mode: manifest.benchmark_mode,
        claim_tier: manifest.claim_tier.clone(),
        cost_metric: match manifest.benchmark_mode {
            CliComparisonMode::AgentStack => "fresh_input_plus_output_tokens",
            CliComparisonMode::ContextPolicy => "fresh_input_tokens",
        }
        .to_owned(),
        paired_runs,
        mechanism_activated_pairs,
        mechanism_gate_passed,
        surface_a,
        surface_a_resolved,
        surface_b,
        surface_b_resolved,
        surface_a_fresh_token_wins: surface_a_wins,
        surface_b_fresh_token_wins: surface_b_wins,
        geometric_mean_fresh_token_ratio_a_over_b: geometric_mean,
        fresh_token_ratio_ci95_lower_a_over_b: ratio_ci95_lower,
        fresh_token_ratio_ci95_upper_a_over_b: ratio_ci95_upper,
        mean_duration_ratio_a_over_b: mean_duration_ratio,
        surface_a_quality_non_inferior: quality_non_inferior,
        surface_a_token_improvement,
        overall_winner,
        piagent_resolved: if piagent_is_a {
            surface_a_resolved
        } else if piagent_is_b {
            surface_b_resolved
        } else {
            0
        },
        counterpart,
        counterpart_resolved: surface_b_resolved,
        counterpart_fresh_token_wins: surface_b_wins,
        codex_resolved: if surface_a == CliSurface::CodexCli {
            surface_a_resolved
        } else if surface_b == CliSurface::CodexCli {
            surface_b_resolved
        } else {
            0
        },
        comparable_success_pairs: ratios.len(),
        piagent_fresh_token_wins: if piagent_is_a {
            surface_a_wins
        } else if piagent_is_b {
            surface_b_wins
        } else {
            0
        },
        codex_fresh_token_wins: if surface_a == CliSurface::CodexCli {
            surface_a_wins
        } else if surface_b == CliSurface::CodexCli {
            surface_b_wins
        } else {
            0
        },
        ties,
        geometric_mean_fresh_token_ratio: geometric_mean,
        mean_duration_ratio,
        quality_non_inferior,
        token_improvement: surface_a_token_improvement,
    })
}

fn comparison_cost(mode: CliComparisonMode, usage: &CliUsage) -> u64 {
    match mode {
        CliComparisonMode::AgentStack => usage.fresh_tokens,
        CliComparisonMode::ContextPolicy => usage.fresh_input_tokens,
    }
}

fn bootstrap_ratio_ci95(values: &[f64], mut state: u64) -> (Option<f64>, Option<f64>) {
    if values.is_empty() {
        return (None, None);
    }
    let mut estimates = Vec::with_capacity(COMPARISON_BOOTSTRAP_RESAMPLES);
    for _ in 0..COMPARISON_BOOTSTRAP_RESAMPLES {
        let mut log_sum = 0.0;
        for _ in 0..values.len() {
            state ^= state << 13;
            state ^= state >> 7;
            state ^= state << 17;
            log_sum += values[(state as usize) % values.len()].ln();
        }
        estimates.push((log_sum / values.len() as f64).exp());
    }
    estimates.sort_by(f64::total_cmp);
    let lower = estimates[COMPARISON_BOOTSTRAP_RESAMPLES * 25 / 1_000];
    let upper = estimates[COMPARISON_BOOTSTRAP_RESAMPLES * 975 / 1_000];
    (Some(lower), Some(upper))
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
    Ok(format!("sha256:{}", hex::encode(digest.finalize())))
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
    format!("sha256:{}", hex::encode(Sha256::digest(bytes)))
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
    fn codex_home_is_fresh_and_outside_the_scored_workspace() {
        let root = std::env::temp_dir().join(format!(
            "structure-codex-isolation-test-{}-{}",
            std::process::id(),
            PROCESS_CAPTURE_SEQUENCE.fetch_add(1, Ordering::Relaxed)
        ));
        std::fs::create_dir(&root).unwrap();
        let env = prepare_codex_runtime(&root).unwrap();
        assert_eq!(env.len(), 1);
        assert_eq!(env[0].0, "CODEX_HOME");
        let isolated_home = Path::new(&env[0].1);
        assert_eq!(
            isolated_home,
            root.join("codex-home").canonicalize().unwrap()
        );
        assert!(!isolated_home.starts_with(root.canonicalize().unwrap().join("project")));
        assert_eq!(std::fs::read_dir(isolated_home).unwrap().count(), 0);
        assert!(prepare_codex_runtime(&root).is_err());
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn codex_usage_uses_uncached_input_plus_output() {
        let bytes = br#"{"type":"item.completed","item":{"type":"command_execution"}}
{"type":"turn.completed","usage":{"input_tokens":100,"cached_input_tokens":60,"cache_write_input_tokens":5,"output_tokens":20,"reasoning_output_tokens":7}}
"#;
        let usage = parse_codex_usage(bytes);
        assert_eq!(usage.fresh_tokens, 60);
        assert_eq!(usage.fresh_input_tokens, 40);
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
        assert_eq!(usage.fresh_input_tokens, 45);
        assert_eq!(usage.cached_input_tokens, 30);
        assert_eq!(usage.tool_calls, 1);
    }

    #[test]
    fn summary_is_paired_and_success_conditioned() {
        let manifest = CliComparisonManifest {
            schema_version: CLI_COMPARISON_MANIFEST_SCHEMA.to_owned(),
            benchmark_mode: CliComparisonMode::AgentStack,
            claim_tier: CliComparisonMode::AgentStack.claim_tier().to_owned(),
            suite_path: "/tmp/suite.json".to_owned(),
            suite_digest: "sha256:test".to_owned(),
            model: "model".to_owned(),
            thinking: "high".to_owned(),
            repetitions: 1,
            timeout_seconds: 1,
            seed: 1,
            codex_program: "codex".to_owned(),
            pi_program: "pi".to_owned(),
            opencode_program: "opencode".to_owned(),
            aider_program: "aider".to_owned(),
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
        let args = codex_args(&manifest, Path::new("/tmp/task/project"));
        assert!(args.iter().any(|arg| arg == "--ignore-user-config"));
        assert!(args.iter().any(|arg| arg == "--ignore-rules"));
        let report = |trial_id: &str, surface, fresh_tokens| CliTrialReport {
            schema_version: CLI_COMPARISON_REPORT_SCHEMA.to_owned(),
            benchmark_mode: CliComparisonMode::AgentStack,
            claim_tier: CliComparisonMode::AgentStack.claim_tier().to_owned(),
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
            stdout_artifact: "stdout.bin".to_owned(),
            stderr_artifact: "stderr.bin".to_owned(),
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
        assert!(!summary.token_improvement);
        assert_eq!(summary.overall_winner, None);

        let mut codex_structure_manifest = manifest;
        codex_structure_manifest.trials[0].trial_id = "task-r01-structure".to_owned();
        codex_structure_manifest.trials[0].surface = CliSurface::Structure;
        let generic_summary = summarize_cli_comparison(
            &codex_structure_manifest,
            &[
                report("task-r01-structure", CliSurface::Structure, 50),
                report("task-r01-codex-cli", CliSurface::CodexCli, 100),
            ],
        )
        .expect("Codex and Structure can be summarized directly");
        assert_eq!(generic_summary.surface_a, CliSurface::CodexCli);
        assert_eq!(generic_summary.surface_b, CliSurface::Structure);
        assert_eq!(
            generic_summary.geometric_mean_fresh_token_ratio_a_over_b,
            Some(2.0)
        );
        assert_eq!(generic_summary.surface_b_fresh_token_wins, 1);
        assert_eq!(generic_summary.overall_winner, None);
    }

    #[test]
    fn bootstrap_ratio_is_deterministic_for_repeated_pairs() {
        let ratios = vec![0.5; MIN_FORMAL_PAIRED_RUNS];
        let (lower, upper) = bootstrap_ratio_ci95(&ratios, 7);
        assert_eq!(lower, Some(0.5));
        assert_eq!(upper, Some(0.5));
    }

    #[test]
    fn benchmark_modes_reject_mixed_treatments() {
        assert!(
            validate_mode_surfaces(
                CliComparisonMode::AgentStack,
                &[CliSurface::CodexCli, CliSurface::Structure]
            )
            .is_ok()
        );
        assert!(
            validate_mode_surfaces(
                CliComparisonMode::AgentStack,
                &[CliSurface::CodexCli, CliSurface::StructureShortMemory,]
            )
            .is_err()
        );
        assert!(
            validate_mode_surfaces(
                CliComparisonMode::ContextPolicy,
                &[
                    CliSurface::StructureFullReplay,
                    CliSurface::StructureFileBackedGc,
                ]
            )
            .is_ok()
        );
        assert!(
            validate_mode_surfaces(
                CliComparisonMode::ContextPolicy,
                &[CliSurface::Structure, CliSurface::StructureFullReplay]
            )
            .is_err()
        );
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
