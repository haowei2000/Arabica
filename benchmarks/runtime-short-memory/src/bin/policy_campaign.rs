//! Real, isolated policy trials composed from the production Rust host.
use std::collections::{BTreeMap, BTreeSet};
use std::error::Error;
use std::io::Read;
use std::path::{Component, Path, PathBuf};
use std::sync::Arc;

use arabica_adapters::{NewSession, SqliteEvaluationStore, SqliteSessionRepository};
use arabica_cli::config::resolve_cli_runtime_config;
use arabica_cli::host::{
    HostConfigArgs, HostModelCatalog, UuidIds, build_host_runtime, process_environment,
    workspace_id_for,
};
use arabica_protocol::{Command, CommandEnvelope, CommandId};
use arabica_runtime::{
    AcceptanceCheck, AcceptanceSpec, ArtifactEvidence, AvailableModelCatalog, BlendRoutingPolicy,
    CandidateAggregateReport, CategoryWeights, EvidenceAcceptanceScorer, GeneratedPolicyRecord,
    PolicyComparisonSummary, PolicyComparisonTrial, PriceCard, RecommendationGatesConfig,
    RecommendationReport, RunAcceptanceScorer, RunCancellation, RunControl, RunCostAccounting,
    RunEvaluation, ScoringProfile, TaskResourceBudget, TaskScore, TaskSpec, TaskTrialScore,
    account_run_cost_and_time, aggregate_candidate_scores, compute_paired_comparison,
    evaluate_recommendation, generate_bounded_proposals, run_evidence_snapshots,
    score_run_evaluation,
};
use arabica_session::{
    DispatchControl, EventVisibility, SessionEventObserver, SessionManager, SessionStore,
};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use structure_short_memory_benchmark::{
    FrozenTask, canonical_price_card, development_task_suite, frozen_task_suite,
    held_out_task_suite, policy_fixed_inexpensive, policy_fixed_stage_blend, policy_fixed_strong,
    policy_state_aware_blend, smoke_task_subset,
};

type Result<T> = std::result::Result<T, Box<dyn Error + Send + Sync>>;

#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Campaign {
    #[serde(default)]
    candidates: Vec<Candidate>,
    #[serde(default)]
    tasks: Vec<Task>,
    #[serde(default = "default_repetitions")]
    repetitions: u32,
    #[serde(default = "default_max_model_steps")]
    max_model_steps: usize,
    #[serde(default = "default_timeout_seconds")]
    timeout_seconds: u64,
    #[serde(default = "default_seed")]
    seed: u64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    max_budget: Option<f64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    price_card: Option<PriceCard>,
}

fn default_repetitions() -> u32 {
    3
}
fn default_max_model_steps() -> usize {
    15
}
fn default_timeout_seconds() -> u64 {
    120
}
fn default_seed() -> u64 {
    42
}
fn default_ref_cost() -> f64 {
    0.10
}
fn default_ref_time() -> u64 {
    30_000
}

#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Candidate {
    label: String,
    policy_id: String,
    #[serde(default)]
    version: Option<u64>,
}

#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Task {
    prompt: String,
    acceptance: AcceptanceSpec,
    #[serde(default = "default_category")]
    category: String,
    #[serde(default)]
    initial_files: BTreeMap<String, String>,
    #[serde(default = "default_ref_cost")]
    reference_cost: f64,
    #[serde(default = "default_ref_time")]
    reference_time_ms: u64,
}

fn default_category() -> String {
    "general".into()
}

impl From<FrozenTask> for Task {
    fn from(f: FrozenTask) -> Self {
        let cat = match f.category {
            structure_short_memory_benchmark::FrozenTaskCategory::ExactFileOperations => {
                "exact_file_operations"
            }
            structure_short_memory_benchmark::FrozenTaskCategory::StructuredExtraction => {
                "structured_extraction"
            }
            structure_short_memory_benchmark::FrozenTaskCategory::SmallCodeFixes => {
                "small_code_fixes"
            }
            structure_short_memory_benchmark::FrozenTaskCategory::MultistepWorkAndRecovery => {
                "multistep_work_and_recovery"
            }
        };
        Self {
            prompt: f.prompt,
            acceptance: f.acceptance,
            category: cat.to_string(),
            initial_files: f.initial_files,
            reference_cost: f.reference_cost,
            reference_time_ms: f.reference_time_ms,
        }
    }
}

fn relative_path(value: &str) -> Result<PathBuf> {
    let path = Path::new(value);
    if value.is_empty()
        || !path
            .components()
            .all(|component| matches!(component, Component::Normal(_)))
    {
        return Err("campaign paths must be non-empty confined relative paths".into());
    }
    Ok(path.to_path_buf())
}

impl Campaign {
    fn validate(&self) -> Result<()> {
        if self.candidates.len() < 2
            || self.candidates.len() > 16
            || self.tasks.is_empty()
            || self.tasks.len() > 100
            || !(1..=20).contains(&self.repetitions)
            || !(1..=100).contains(&self.max_model_steps)
            || !(1..=3600).contains(&self.timeout_seconds)
        {
            return Err("invalid campaign bounds".into());
        }
        let mut labels = BTreeSet::new();
        for candidate in &self.candidates {
            if candidate.label.trim().is_empty()
                || candidate.policy_id.trim().is_empty()
                || !labels.insert(&candidate.label)
            {
                return Err("candidate labels must be unique and policy IDs non-empty".into());
            }
        }
        let mut tasks = BTreeSet::new();
        for task in &self.tasks {
            task.acceptance.validate()?;
            if task.prompt.trim().is_empty()
                || task.acceptance.criteria.is_empty()
                || !tasks.insert(&task.acceptance.task_id)
            {
                return Err("campaign tasks need a prompt, criteria, and unique task ID".into());
            }
            let mut bytes = 0_usize;
            for (path, content) in &task.initial_files {
                relative_path(path)?;
                bytes = bytes.saturating_add(content.len());
            }
            if task.initial_files.len() > 128 || bytes > 1024 * 1024 {
                return Err("campaign fixture exceeds bounds".into());
            }
            for criterion in &task.acceptance.criteria {
                match &criterion.check {
                    AcceptanceCheck::ArtifactContains { path, .. }
                    | AcceptanceCheck::ExactFileContent { path, .. }
                    | AcceptanceCheck::JsonField { path, .. }
                    | AcceptanceCheck::JsonSchema { path, .. } => {
                        relative_path(path)?;
                    }
                    AcceptanceCheck::PermittedFiles { allowed_paths } => {
                        for path in allowed_paths {
                            relative_path(path)?;
                        }
                    }
                    _ => {}
                }
            }
        }
        Ok(())
    }
}

fn scan_modified_files(
    root: &Path,
    initial_files: &BTreeMap<String, String>,
) -> Result<Vec<String>> {
    let mut modified = BTreeSet::new();
    let mut stack = vec![PathBuf::new()];

    while let Some(rel_dir) = stack.pop() {
        let current_dir = if rel_dir.as_os_str().is_empty() {
            root.to_path_buf()
        } else {
            root.join(&rel_dir)
        };

        let entries = match std::fs::read_dir(&current_dir) {
            Ok(entries) => entries,
            Err(_) => continue,
        };

        for entry in entries.flatten() {
            let file_type = match entry.file_type() {
                Ok(ft) => ft,
                Err(_) => continue,
            };
            let name = entry.file_name();
            let name_str = match name.to_str() {
                Some(s) => s,
                None => continue,
            };
            if name_str.starts_with('.') {
                continue;
            }
            let rel_path = if rel_dir.as_os_str().is_empty() {
                PathBuf::from(name_str)
            } else {
                rel_dir.join(name_str)
            };

            if file_type.is_dir() {
                stack.push(rel_path);
            } else if file_type.is_file() {
                let rel_str = rel_path.to_string_lossy().to_string();
                match initial_files.get(&rel_str) {
                    Some(initial_content) => {
                        if let Ok(content) = std::fs::read_to_string(entry.path()) {
                            if content != *initial_content {
                                modified.insert(rel_str);
                            }
                        } else {
                            modified.insert(rel_str);
                        }
                    }
                    None => {
                        modified.insert(rel_str);
                    }
                }
            }
        }
    }

    for initial_path in initial_files.keys() {
        if !root.join(initial_path).exists() {
            modified.insert(initial_path.clone());
        }
    }

    Ok(modified.into_iter().collect())
}

// Capture only explicitly requested files. Reject symlinks at every component
// and bound reads; task failures and unavailable evidence remain distinct.
fn capture_artifacts(
    root: &Path,
    spec: &AcceptanceSpec,
    initial_files: &BTreeMap<String, String>,
) -> Result<BTreeMap<String, ArtifactEvidence>> {
    let mut artifacts = BTreeMap::new();
    let mut remaining = 64 * 1024_usize;

    for criterion in &spec.criteria {
        let path = match &criterion.check {
            AcceptanceCheck::ArtifactContains { path, .. }
            | AcceptanceCheck::ExactFileContent { path, .. }
            | AcceptanceCheck::JsonField { path, .. }
            | AcceptanceCheck::JsonSchema { path, .. } => path,
            _ => continue,
        };
        if artifacts.contains_key(path) {
            continue;
        }
        let relative = relative_path(path)?;
        let capture = || -> std::io::Result<ArtifactEvidence> {
            let root = rustix::fs::open(
                root,
                rustix::fs::OFlags::RDONLY
                    | rustix::fs::OFlags::DIRECTORY
                    | rustix::fs::OFlags::NOFOLLOW,
                rustix::fs::Mode::empty(),
            )?;
            let components: Vec<_> = relative.components().collect();
            let mut directory = root;
            for component in &components[..components.len() - 1] {
                directory = rustix::fs::openat(
                    &directory,
                    component.as_os_str(),
                    rustix::fs::OFlags::RDONLY
                        | rustix::fs::OFlags::DIRECTORY
                        | rustix::fs::OFlags::NOFOLLOW,
                    rustix::fs::Mode::empty(),
                )?;
            }
            let descriptor = rustix::fs::openat(
                &directory,
                components.last().unwrap().as_os_str(),
                rustix::fs::OFlags::RDONLY
                    | rustix::fs::OFlags::NOFOLLOW
                    | rustix::fs::OFlags::NONBLOCK,
                rustix::fs::Mode::empty(),
            )?;
            let file = std::fs::File::from(descriptor);
            if !file.metadata()?.is_file() {
                return Ok(ArtifactEvidence::Unavailable);
            }
            let mut bytes = Vec::new();
            file.take(remaining.saturating_sub(path.len()) as u64 + 1)
                .read_to_end(&mut bytes)?;
            if path.len() + bytes.len() > remaining {
                return Ok(ArtifactEvidence::Unavailable);
            }
            Ok(match String::from_utf8(bytes) {
                Ok(content) => ArtifactEvidence::Captured { content },
                Err(_) => ArtifactEvidence::Unavailable,
            })
        };
        let evidence = match capture() {
            Ok(evidence) => evidence,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => ArtifactEvidence::Missing,
            Err(_) => ArtifactEvidence::Unavailable,
        };
        remaining = remaining.saturating_sub(
            path.len()
                + match &evidence {
                    ArtifactEvidence::Captured { content } => content.len(),
                    _ => 0,
                },
        );
        artifacts.insert(path.clone(), evidence);
    }

    if spec
        .criteria
        .iter()
        .any(|c| matches!(c.check, AcceptanceCheck::PermittedFiles { .. }))
    {
        let modified = scan_modified_files(root, initial_files)?;
        artifacts.insert(
            "__modified_files__".into(),
            ArtifactEvidence::ModifiedFiles { paths: modified },
        );
    }

    for criterion in &spec.criteria {
        if let AcceptanceCheck::ControlledTest {
            id,
            command,
            timeout_ms,
            output_limit_bytes,
        } = &criterion.check
        {
            let key = format!("__test__:{id}");
            artifacts.entry(key).or_insert_with(|| {
                arabica_runtime::execute_controlled_test(
                    root,
                    command,
                    *timeout_ms,
                    *output_limit_bytes,
                )
            });
        }
    }

    Ok(artifacts)
}

/// Record of an executed trial, persisting evaluation, score, and billing facts.
#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct CampaignTrialRecord {
    pub candidate: String,
    pub task_id: String,
    pub category: String,
    pub repetition: u32,
    pub evaluation: RunEvaluation,
    pub score: TaskScore,
    pub accounting: Option<RunCostAccounting>,
}

struct TrialExecutionParams<'a> {
    catalog: &'a HostModelCatalog,
    policy: BlendRoutingPolicy,
    candidate_label: &'a str,
    task: &'a Task,
    repetition: u32,
    root: &'a Path,
    storage_home: &'a Path,
    config: &'a Campaign,
    price_card: &'a PriceCard,
    allow_shell: bool,
}

async fn execute_trial(params: TrialExecutionParams<'_>) -> Result<CampaignTrialRecord> {
    // 1. Check for completed trial record to support idempotent resumption without duplicate billing
    let record_file = params.root.join("trial_record.json");
    if record_file.exists()
        && let Ok(bytes) = std::fs::read(&record_file)
        && let Ok(record) = serde_json::from_slice::<CampaignTrialRecord>(&bytes)
    {
        eprintln!(
            "resuming completed trial for candidate '{}' on task '{}' (repetition {}) without re-billing",
            record.candidate, record.task_id, record.repetition
        );
        return Ok(record);
    }

    // 2. Fresh trial setup
    if params.root.exists() {
        let _ = std::fs::remove_dir_all(params.root);
    }
    std::fs::create_dir_all(params.root)?;
    for (path, content) in &params.task.initial_files {
        let target = params.root.join(relative_path(path)?);
        if let Some(parent) = target.parent() {
            std::fs::create_dir_all(parent)?;
        }
        std::fs::write(target, content)?;
    }

    let mut catalog = params.catalog.clone();
    catalog.blend_policy = Some(params.policy.clone());
    let model = catalog.build_model()?;
    let tool_policy = arabica_cli::print::tool_policy(params.allow_shell, false)?;
    let mut runtime = build_host_runtime(model, params.root, tool_policy, params.storage_home);
    runtime.set_blend_policy(Some(params.policy))?;
    runtime.set_max_model_steps_per_run(params.config.max_model_steps);
    let mut manager = SessionManager::with_ids(runtime, Box::new(UuidIds));
    let workspace = workspace_id_for(params.root);
    let created = manager
        .handle(CommandEnvelope::new(
            CommandId::new("create"),
            None,
            Command::SessionCreate {
                workspace_id: workspace.clone(),
            },
        ))
        .await?;
    let session = created
        .first()
        .ok_or("session creation returned no event")?
        .session_id
        .clone();
    let repository = SqliteSessionRepository::new(params.storage_home);
    let store = repository.create(NewSession {
        session_id: &session,
        workspace_id: &workspace,
        cwd: params.root,
        profile: None,
        instructions_sha256: None,
    })?;
    for event in &created {
        store.observe(event, EventVisibility::Client);
    }
    let cancellation = RunCancellation::new();
    let timer = tokio::spawn({
        let cancellation = cancellation.clone();
        let seconds = params.config.timeout_seconds;
        async move {
            tokio::time::sleep(std::time::Duration::from_secs(seconds)).await;
            cancellation.cancel();
        }
    });
    let observer: Arc<dyn SessionEventObserver> = store;
    let result = manager
        .dispatch(
            CommandEnvelope::new(
                CommandId::new("task"),
                Some(session.clone()),
                Command::MessageSend {
                    content: params.task.prompt.clone(),
                },
            ),
            DispatchControl {
                run: RunControl {
                    cancellation: Some(cancellation),
                    ..RunControl::default()
                },
                observer: Some(observer),
            },
        )
        .await;
    timer.abort();
    result?;

    let stored = repository.read(&workspace, &session)?;
    let mut snapshot = run_evidence_snapshots(&stored.events)
        .pop()
        .ok_or("trial produced no terminal evidence")?
        .with_artifacts(capture_artifacts(
            params.root,
            &params.task.acceptance,
            &params.task.initial_files,
        )?)?;

    snapshot.fixture_fingerprint = Some(hex::encode(Sha256::digest(serde_json::to_vec(&(
        &params.task.initial_files,
        params.config.max_model_steps,
        params.config.timeout_seconds,
        params.allow_shell,
    ))?)));
    let artifacts = snapshot.artifacts.clone();
    let snapshot = snapshot.with_artifacts(artifacts)?;

    // Cost and time accounting
    let accounting =
        account_run_cost_and_time(&stored.events, Some(&snapshot.run_id), params.price_card).ok();
    let actual_cost = accounting.as_ref().and_then(|a| a.total_agent_cost);

    let acceptance = EvidenceAcceptanceScorer.score(&snapshot, &params.task.acceptance)?;
    let mut db = SqliteEvaluationStore::open(&params.storage_home.join("evaluation.sqlite3"))?;
    db.save_acceptance(&snapshot, &params.task.acceptance, &acceptance)?;

    // Calculate versioned task score
    let spec = TaskSpec {
        task_id: params.task.acceptance.task_id.clone(),
        version: params.task.acceptance.version,
        environment_fingerprint: snapshot.fixture_fingerprint.clone(),
        criteria: params.task.acceptance.criteria.clone(),
        budgets: TaskResourceBudget::default(),
    };
    let profile = ScoringProfile::new(params.task.reference_cost, params.task.reference_time_ms)
        .map_err(|e| e.0)?;
    let score = score_run_evaluation(
        &spec,
        &profile,
        1,
        &snapshot,
        Some(&acceptance),
        actual_cost,
    )
    .map_err(|e| e.0)?;

    let record = CampaignTrialRecord {
        candidate: params.candidate_label.to_string(),
        task_id: params.task.acceptance.task_id.clone(),
        category: params.task.category.clone(),
        repetition: params.repetition,
        evaluation: RunEvaluation {
            snapshot,
            acceptance: Some(acceptance),
        },
        score,
        accounting,
    };

    // Immediately persist trial record for crash recovery and audit
    std::fs::write(&record_file, serde_json::to_vec_pretty(&record)?)?;

    Ok(record)
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct CampaignSummaryReport {
    pub manifest_fingerprint: String,
    pub price_card_fingerprint: String,
    pub seed: u64,
    pub total_trials_attempted: usize,
    pub total_trials_completed: usize,
    pub cumulative_cost: f64,
    pub budget_exhausted: bool,
    pub candidate_reports: BTreeMap<String, CandidateAggregateReport>,
    pub comparisons: Vec<PolicyComparisonSummary>,
    pub recommendations: Vec<RecommendationReport>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub proposals: Vec<GeneratedPolicyRecord>,
}

async fn run() -> Result<()> {
    let mut args = std::env::args().skip(1);
    let mut manifest = None;
    let mut suite_arg = None;
    let mut output = None;
    let mut seed = None;
    let mut max_budget = None;
    let mut allow_shell = false;

    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--manifest" => {
                manifest = Some(PathBuf::from(args.next().ok_or("--manifest needs a path")?))
            }
            "--suite" => suite_arg = Some(args.next().ok_or("--suite needs 'smoke' or 'full'")?),
            "--output" => {
                output = Some(PathBuf::from(
                    args.next().ok_or("--output needs a new directory")?,
                ))
            }
            "--seed" => {
                let s: u64 = args.next().ok_or("--seed needs a number")?.parse()?;
                seed = Some(s);
            }
            "--max-budget" => {
                let b: f64 = args.next().ok_or("--max-budget needs a number")?.parse()?;
                max_budget = Some(b);
            }
            "--allow-shell" => allow_shell = true,
            "--help" | "-h" => {
                println!(
                    "policy_campaign [--manifest FILE | --suite smoke|full] --output DIR [--seed N] [--max-budget DOLLARS] [--allow-shell]\n\
                     Runs real provider calls in isolated trial directories, scoring each trial against versioned criteria,\n\
                     pricing, and budgets, with crash recovery and resume support."
                );
                return Ok(());
            }
            _ => return Err("unknown policy campaign argument".into()),
        }
    }

    let output = output.ok_or("--output is required")?;

    // Load or generate campaign
    let mut campaign: Campaign = match manifest {
        Some(path) => {
            let mut bytes = Vec::new();
            std::fs::File::open(path)?
                .take(1024 * 1024 + 1)
                .read_to_end(&mut bytes)?;
            if bytes.len() > 1024 * 1024 {
                return Err("campaign manifest exceeds one MiB".into());
            }
            serde_json::from_slice(&bytes)?
        }
        None => match suite_arg.as_deref() {
            Some("smoke") => Campaign {
                candidates: Vec::new(),
                tasks: smoke_task_subset().into_iter().map(Task::from).collect(),
                repetitions: 2,
                max_model_steps: 10,
                timeout_seconds: 60,
                seed: seed.unwrap_or(42),
                max_budget,
                price_card: None,
            },
            Some("dev") => Campaign {
                candidates: Vec::new(),
                tasks: development_task_suite()
                    .into_iter()
                    .map(Task::from)
                    .collect(),
                repetitions: 3,
                max_model_steps: 15,
                timeout_seconds: 120,
                seed: seed.unwrap_or(42),
                max_budget,
                price_card: None,
            },
            Some("held-out") => Campaign {
                candidates: Vec::new(),
                tasks: held_out_task_suite().into_iter().map(Task::from).collect(),
                repetitions: 3,
                max_model_steps: 15,
                timeout_seconds: 120,
                seed: seed.unwrap_or(42),
                max_budget,
                price_card: None,
            },
            Some("full") | None => Campaign {
                candidates: Vec::new(),
                tasks: frozen_task_suite().into_iter().map(Task::from).collect(),
                repetitions: 3,
                max_model_steps: 15,
                timeout_seconds: 120,
                seed: seed.unwrap_or(42),
                max_budget,
                price_card: None,
            },
            Some(other) => {
                return Err(format!(
                    "unknown suite: '{other}'; use 'smoke', 'dev', 'held-out', or 'full'"
                )
                .into());
            }
        },
    };

    if let Some(s) = seed {
        campaign.seed = s;
    }
    if let Some(b) = max_budget {
        campaign.max_budget = Some(b);
    }

    let home = arabica_adapters::default_arabica_home()?;
    let resolved = resolve_cli_runtime_config(
        &HostConfigArgs::default(),
        &home,
        &std::env::current_dir()?,
        process_environment,
    )
    .map_err(|error| error.to_string())?;
    let catalog = resolved.model_catalog();

    // Default the 4 canonical policies if candidate list is empty
    if campaign.candidates.is_empty() {
        campaign.candidates = vec![
            Candidate {
                label: "fixed_strong".into(),
                policy_id: "fixed_strong".into(),
                version: None,
            },
            Candidate {
                label: "fixed_inexpensive".into(),
                policy_id: "fixed_inexpensive".into(),
                version: None,
            },
            Candidate {
                label: "fixed_stage_blend".into(),
                policy_id: "fixed_stage_blend".into(),
                version: None,
            },
            Candidate {
                label: "state_aware_blend".into(),
                policy_id: "state_aware_blend".into(),
                version: None,
            },
        ];
    }

    campaign.validate()?;

    let price_card = campaign
        .price_card
        .clone()
        .unwrap_or_else(canonical_price_card);

    let mut policies = Vec::new();
    for candidate in &campaign.candidates {
        let policy = match candidate.policy_id.as_str() {
            "fixed_strong" => policy_fixed_strong("strong"),
            "fixed_inexpensive" => policy_fixed_inexpensive("inexpensive"),
            "fixed_stage_blend" => policy_fixed_stage_blend("strong", "inexpensive"),
            "state_aware_blend" => policy_state_aware_blend("strong", "inexpensive"),
            _ => match candidate.version {
                Some(version) => {
                    SqliteEvaluationStore::read_policy_versions(
                        &home.join("evaluation.sqlite3"),
                        Some(&candidate.policy_id),
                    )?
                    .into_iter()
                    .find(|record| record.version == version)
                    .ok_or("candidate policy version not found")?
                    .policy
                }
                None => resolved
                    .blend_policies
                    .get(&candidate.policy_id)
                    .cloned()
                    .ok_or("configured candidate policy not found")?,
            },
        };
        policies.push(policy);
    }

    if !output.exists() {
        std::fs::create_dir_all(&output)?;
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&output, std::fs::Permissions::from_mode(0o700))?;
    }
    let output = std::fs::canonicalize(output)?;
    let storage_home = output.join("store");

    let manifest_fingerprint = hex::encode(Sha256::digest(serde_json::to_vec(&campaign)?));
    let pricing_fingerprint = price_card.fingerprint();

    let mut trials = Vec::new();
    let mut cumulative_cost = 0.0;
    let mut budget_exhausted = false;

    'task_loop: for (task_index, task) in campaign.tasks.iter().enumerate() {
        for repetition in 0..campaign.repetitions {
            for position in 0..campaign.candidates.len() {
                // Enforce campaign budget
                if let Some(budget) = campaign.max_budget
                    && cumulative_cost >= budget
                {
                    eprintln!(
                        "campaign budget reached ({cumulative_cost:.4} >= {budget:.4}); cleanly terminating remaining trials"
                    );
                    budget_exhausted = true;
                    break 'task_loop;
                }

                // Deterministic pseudo-random rotation by (seed + task_index + repetition)
                let shift = ((campaign
                    .seed
                    .wrapping_add(task_index as u64)
                    .wrapping_add(repetition as u64))
                    % campaign.candidates.len() as u64) as usize;
                let index = (position + shift) % campaign.candidates.len();
                let candidate = &campaign.candidates[index];

                eprintln!(
                    "policy campaign: task {}/{} repetition {}/{} candidate {} ({})",
                    task_index + 1,
                    campaign.tasks.len(),
                    repetition + 1,
                    campaign.repetitions,
                    index + 1,
                    candidate.label
                );

                let root = output.join(format!("trial-{task_index:03}-{repetition:02}-{index:02}"));
                let trial_record = execute_trial(TrialExecutionParams {
                    catalog: &catalog,
                    policy: policies[index].clone(),
                    candidate_label: &candidate.label,
                    task,
                    repetition,
                    root: &root,
                    storage_home: &storage_home,
                    config: &campaign,
                    price_card: &price_card,
                    allow_shell,
                })
                .await?;

                if let Some(cost) = trial_record.accounting.as_ref().and_then(|a| a.total_cost) {
                    cumulative_cost += cost;
                }

                trials.push(trial_record);
                std::fs::write(
                    output.join("trials.json"),
                    serde_json::to_vec_pretty(&trials)?,
                )?;
            }
        }
    }

    // Convert trials for comparison
    let comparison_trials: Vec<PolicyComparisonTrial> = trials
        .iter()
        .map(|t| PolicyComparisonTrial {
            candidate: t.candidate.clone(),
            repetition: t.repetition,
            evaluation: t.evaluation.clone(),
        })
        .collect();

    let mut db = SqliteEvaluationStore::open(&storage_home.join("evaluation.sqlite3"))?;
    let comparisons = db.save_policy_comparison(&comparison_trials)?;

    // Aggregate candidate scores across task categories
    let trial_scores: Vec<TaskTrialScore> = trials
        .iter()
        .map(|t| TaskTrialScore {
            candidate: t.candidate.clone(),
            task_id: t.task_id.clone(),
            category: t.category.clone(),
            repetition: t.repetition,
            score: Some(t.score.clone()),
            cost: t.accounting.as_ref().and_then(|a| a.total_cost),
            wall_ms: t.evaluation.snapshot.timing.wall_ms,
            active_ms: t.evaluation.snapshot.timing.active_ms,
            has_usage: t.evaluation.snapshot.timing.usage_reported_calls > 0,
        })
        .collect();

    let categories: BTreeSet<&str> = trials.iter().map(|t| t.category.as_str()).collect();
    let weights = CategoryWeights::equal_split(categories).unwrap_or_else(|_| {
        CategoryWeights::new(BTreeMap::from([("general".into(), 1.0)])).unwrap()
    });

    let candidate_reports = aggregate_candidate_scores(&trial_scores, &weights)?;

    // Evaluate recommendation gates against baseline
    let baseline_label = campaign
        .candidates
        .iter()
        .find(|c| c.label == "baseline" || c.label == "fixed_strong")
        .map(|c| c.label.as_str())
        .unwrap_or(&campaign.candidates[0].label);

    let mut recommendations = Vec::new();
    let gates_config = RecommendationGatesConfig {
        overall_success_floor: 0.70,
        min_score_improvement: 1.0,
        ..Default::default()
    };

    for candidate in &campaign.candidates {
        if candidate.label != baseline_label
            && let (Some(cand_rep), Some(base_rep)) = (
                candidate_reports.get(&candidate.label),
                candidate_reports.get(baseline_label),
            )
            && let Ok(paired) =
                compute_paired_comparison(&candidate.label, baseline_label, &trial_scores)
        {
            let rec = evaluate_recommendation(cand_rep, base_rep, &paired, &gates_config);
            recommendations.push(rec);
        }
    }

    // Generate bounded proposals based on trial outcomes and evidence
    let mut proposals = Vec::new();
    let model_catalog = AvailableModelCatalog::new(
        vec!["haiku".into(), "gpt-4o-mini".into(), "fast".into()],
        vec!["opus".into(), "gpt-4o".into(), "strong".into()],
        vec!["opus".into(), "gpt-4o".into(), "recovery".into()],
    );

    let mut tool_errors = Vec::new();
    for t in &trials {
        if let Some(acc) = &t.evaluation.acceptance {
            for res in &acc.results {
                if res.status != arabica_runtime::AcceptanceStatus::Pass {
                    tool_errors.push((res.criterion_id.clone(), 1_u64));
                }
            }
        }
    }

    for (index, _candidate) in campaign.candidates.iter().enumerate() {
        let base_policy = &policies[index];
        let cand_proposals = generate_bounded_proposals(
            base_policy,
            &model_catalog,
            &trial_scores,
            None,
            &tool_errors,
            Some(manifest_fingerprint.clone()),
        );
        for prop in cand_proposals {
            let rec = GeneratedPolicyRecord::new(
                prop.base_policy_id.clone(),
                prop.candidate_version,
                "candidate",
                prop.proposed_policy.clone(),
                prop.evidence.source_fingerprint.clone(),
                prop.rationale.clone(),
            )
            .with_proposal(prop);
            let _ = db.save_policy_version(&rec);
            proposals.push(rec);
        }
    }

    let summary = CampaignSummaryReport {
        manifest_fingerprint,
        price_card_fingerprint: pricing_fingerprint,
        seed: campaign.seed,
        total_trials_attempted: campaign.tasks.len()
            * campaign.candidates.len()
            * campaign.repetitions as usize,
        total_trials_completed: trials.len(),
        cumulative_cost,
        budget_exhausted,
        candidate_reports,
        comparisons,
        recommendations,
        proposals,
    };

    std::fs::write(
        output.join("campaign_summary.json"),
        serde_json::to_vec_pretty(&summary)?,
    )?;

    println!("{}", serde_json::to_string_pretty(&summary)?);
    Ok(())
}

#[tokio::main]
async fn main() {
    if let Err(error) = run().await {
        eprintln!("policy campaign failed: {error}");
        std::process::exit(1);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fixture_paths_cannot_escape_trial_roots() {
        for path in ["", "/etc/passwd", "../outside", "a/../../outside", "."] {
            assert!(relative_path(path).is_err());
        }
        assert!(relative_path("src/main.rs").is_ok());
    }

    #[test]
    fn capture_does_not_follow_symlinks_or_read_unbounded_files() {
        let root = std::env::temp_dir().join(format!("policy-capture-{}", std::process::id()));
        std::fs::create_dir(&root).unwrap();
        std::fs::write(root.join("large.txt"), "x".repeat(65537)).unwrap();
        #[cfg(unix)]
        std::os::unix::fs::symlink("/etc/passwd", root.join("link")).unwrap();
        let spec = AcceptanceSpec {
            task_id: "capture".into(),
            version: 1,
            criteria: ["large.txt", "missing.txt", "link"]
                .into_iter()
                .enumerate()
                .map(|(index, path)| {
                    arabica_runtime::AcceptanceCriterion::new(
                        index.to_string(),
                        AcceptanceCheck::ArtifactContains {
                            path: path.into(),
                            value: "x".into(),
                        },
                    )
                })
                .collect(),
        };
        let captured = capture_artifacts(&root, &spec, &BTreeMap::new()).unwrap();
        assert_eq!(captured["large.txt"], ArtifactEvidence::Unavailable);
        assert_eq!(captured["missing.txt"], ArtifactEvidence::Missing);
        #[cfg(unix)]
        assert_eq!(captured["link"], ArtifactEvidence::Unavailable);
        std::fs::remove_dir_all(root).unwrap();
    }

    #[tokio::test]
    async fn real_host_trials_capture_artifacts_and_compare_without_paid_calls() {
        use arabica_provider::{ApiProviderConfig, ApiType};
        use axum::{Json, Router, routing::post};
        use std::sync::atomic::{AtomicUsize, Ordering};

        let calls = Arc::new(AtomicUsize::new(0));
        let router = Router::new().route("/v1/chat/completions", post({
            let calls = calls.clone();
            move || {
                let calls = calls.clone();
                async move {
                    let tool_turn = calls.fetch_add(1, Ordering::SeqCst).is_multiple_of(2);
                    let message = if tool_turn {
                        serde_json::json!({
                            "role":"assistant",
                            "content":null,
                            "tool_calls":[{"id":"write", "type":"function", "function":{"name":"write_file", "arguments":"{\"path\":\"answer.txt\",\"content\":\"ready\"}"}}]
                        })
                    } else {
                        serde_json::json!({"role":"assistant", "content":"ready"})
                    };
                    Json(serde_json::json!({
                        "id":"response",
                        "object":"chat.completion",
                        "choices":[{"index":0,"message":message,"finish_reason":if tool_turn {"tool_calls"} else {"stop"}}],
                        "usage":{"prompt_tokens":10,"completion_tokens":5}
                    }))
                }
            }
        }));

        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let server = tokio::spawn(async move {
            axum::serve(listener, router).await.unwrap();
        });

        let provider = ApiProviderConfig::new(
            ApiType::OpenAiChatCompletions,
            "test-key",
            format!("http://{address}/v1"),
            "fake-model",
        );
        let catalog = HostModelCatalog {
            provider,
            models: BTreeMap::from([("default".into(), "fake-model".into())]),
            blend_policy: None,
            model_configs: BTreeMap::new(),
        };

        let policy: BlendRoutingPolicy = serde_json::from_value(serde_json::json!({
            "policy_id":"baseline", "version":1, "default_model":"default", "recovery_after_no_progress_steps":2, "minimum_model_dwell_steps":1, "tool_call_capable_models":["default"]
        })).unwrap();

        let task = Task {
            prompt: "Write ready to answer.txt and finish.".into(),
            category: "general".into(),
            initial_files: BTreeMap::from([("answer.txt".into(), "pending".into())]),
            reference_cost: 0.05,
            reference_time_ms: 5000,
            acceptance: AcceptanceSpec {
                task_id: "write".into(),
                version: 1,
                criteria: vec![arabica_runtime::AcceptanceCriterion::new(
                    "file",
                    AcceptanceCheck::ArtifactContains {
                        path: "answer.txt".into(),
                        value: "ready".into(),
                    },
                )],
            },
        };

        let config = Campaign {
            candidates: Vec::new(),
            tasks: Vec::new(),
            repetitions: 1,
            max_model_steps: 5,
            timeout_seconds: 10,
            seed: 42,
            max_budget: None,
            price_card: None,
        };

        let price_card = canonical_price_card();

        let root = std::env::temp_dir().join(format!(
            "policy-host-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        std::fs::create_dir(&root).unwrap();
        let storage = root.join("store");

        let first = execute_trial(TrialExecutionParams {
            catalog: &catalog,
            policy: policy.clone(),
            candidate_label: "baseline",
            task: &task,
            repetition: 0,
            root: &root.join("first"),
            storage_home: &storage,
            config: &config,
            price_card: &price_card,
            allow_shell: false,
        })
        .await
        .unwrap();

        assert_eq!(
            first
                .evaluation
                .acceptance
                .as_ref()
                .unwrap()
                .verified_success,
            Some(true)
        );
        assert_eq!(first.evaluation.snapshot.timing.runner_calls, 1);
        assert_eq!(first.evaluation.snapshot.timing.model_calls, 2);
        assert_eq!(first.evaluation.snapshot.timing.input_tokens, 20);

        // Verification of PR 1 & PR 3 integration:
        assert!(first.score.quality.is_some());
        assert!(first.accounting.is_some());

        // Test idempotent resumption: running again on same root loads cached trial record
        let resumed = execute_trial(TrialExecutionParams {
            catalog: &catalog,
            policy: policy.clone(),
            candidate_label: "baseline",
            task: &task,
            repetition: 0,
            root: &root.join("first"),
            storage_home: &storage,
            config: &config,
            price_card: &price_card,
            allow_shell: false,
        })
        .await
        .unwrap();
        assert_eq!(resumed.candidate, first.candidate);
        assert_eq!(resumed.score.total, first.score.total);

        // Run second candidate
        let mut candidate = policy;
        candidate.policy_id = "candidate".into();
        let second = execute_trial(TrialExecutionParams {
            catalog: &catalog,
            policy: candidate,
            candidate_label: "candidate",
            task: &task,
            repetition: 0,
            root: &root.join("second"),
            storage_home: &storage,
            config: &config,
            price_card: &price_card,
            allow_shell: false,
        })
        .await
        .unwrap();

        assert_ne!(
            first.evaluation.snapshot.workspace_id,
            second.evaluation.snapshot.workspace_id
        );

        let mut db = SqliteEvaluationStore::open(&storage.join("evaluation.sqlite3")).unwrap();
        let summary = db
            .save_policy_comparison(&[
                PolicyComparisonTrial {
                    candidate: "baseline".into(),
                    repetition: 0,
                    evaluation: first.evaluation.clone(),
                },
                PolicyComparisonTrial {
                    candidate: "candidate".into(),
                    repetition: 0,
                    evaluation: second.evaluation,
                },
            ])
            .unwrap();
        assert!(summary.iter().all(|row| row.success_rate == Some(1.0)));

        server.abort();
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn campaign_validation_and_smoke_suite_conversion() {
        let smoke_tasks = smoke_task_subset();
        let tasks: Vec<Task> = smoke_tasks.into_iter().map(Task::from).collect();
        let campaign = Campaign {
            candidates: vec![
                Candidate {
                    label: "strong".into(),
                    policy_id: "strong".into(),
                    version: None,
                },
                Candidate {
                    label: "inexpensive".into(),
                    policy_id: "inexpensive".into(),
                    version: None,
                },
            ],
            tasks,
            repetitions: 2,
            max_model_steps: 10,
            timeout_seconds: 60,
            seed: 123,
            max_budget: Some(5.0),
            price_card: Some(canonical_price_card()),
        };
        assert!(campaign.validate().is_ok());
    }
}
