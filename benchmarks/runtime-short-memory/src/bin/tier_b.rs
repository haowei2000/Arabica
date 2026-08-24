use std::env;
use std::error::Error;
use std::fmt::Display;
use std::io::{self, Write};
use std::path::PathBuf;
use std::time::{SystemTime, UNIX_EPOCH};

use serde::Serialize;
use structure_provider::{ApiModelProvider, ApiProviderConfig, ApiType};
use structure_runtime::{KeyAdmissionPolicy, RuntimeCompactionStrategy, ShortMemoryPolicy};
use structure_short_memory_benchmark::{
    ExpectedFile, FixtureFileProvider, TierBEvidenceLevel, TierBProviderMetadata, TierBSuiteConfig,
    TierBTask, run_tier_b_suite,
};

#[tokio::main]
async fn main() {
    if let Err(error) = run().await {
        eprintln!("Tier-B benchmark failed: {error}");
        std::process::exit(1);
    }
}

async fn run() -> Result<(), Box<dyn Error>> {
    let mut fixture = false;
    let mut compare = false;
    let mut strategies = None;
    let mut suite_id = "tier-b-write-file".to_owned();
    let mut repetitions = 1_usize;
    let mut history_turns = 8_usize;
    let mut history_turns_was_set = false;
    let mut single_message_tools = None;
    let mut payload_bytes = 0_usize;
    let mut model = None;
    let mut base_url = None;
    let mut api_type = ApiType::OpenAiChatCompletions;
    let mut runner_root = None;
    let mut output = None;
    let mut content = "STRUCTURE_TIER_B_OK".to_owned();
    let mut max_key_batches = None;
    let mut max_key_content_bytes = None;
    let mut pretty = false;
    let mut fail_on_task = false;
    let arguments: Vec<String> = env::args().skip(1).collect();
    let mut index = 0_usize;

    while index < arguments.len() {
        let argument = &arguments[index];
        match argument.as_str() {
            "--fixture" => fixture = true,
            "--compare" => compare = true,
            "--strategies" => {
                strategies = Some(parse_strategies(value(&arguments, &mut index, argument)?)?);
            }
            "--suite-id" => suite_id = value(&arguments, &mut index, argument)?.to_owned(),
            "--repetitions" => {
                repetitions = parse(value(&arguments, &mut index, argument)?, argument)?;
            }
            "--history-turns" => {
                history_turns = parse(value(&arguments, &mut index, argument)?, argument)?;
                history_turns_was_set = true;
            }
            "--single-message-tools" => {
                single_message_tools =
                    Some(parse(value(&arguments, &mut index, argument)?, argument)?);
            }
            "--payload-bytes" => {
                payload_bytes = parse(value(&arguments, &mut index, argument)?, argument)?;
            }
            "--model" => model = Some(value(&arguments, &mut index, argument)?.to_owned()),
            "--base-url" => {
                base_url = Some(value(&arguments, &mut index, argument)?.to_owned());
            }
            "--api-type" => {
                api_type = value(&arguments, &mut index, argument)?.parse()?;
            }
            "--runner-root" => {
                runner_root = Some(PathBuf::from(value(&arguments, &mut index, argument)?));
            }
            "--output" => {
                output = Some(PathBuf::from(value(&arguments, &mut index, argument)?));
            }
            "--content" => content = value(&arguments, &mut index, argument)?.to_owned(),
            "--max-key-batches" => {
                max_key_batches = Some(parse(value(&arguments, &mut index, argument)?, argument)?);
            }
            "--max-key-bytes" => {
                max_key_content_bytes =
                    Some(parse(value(&arguments, &mut index, argument)?, argument)?);
            }
            "--pretty" => pretty = true,
            "--fail-on-task" => fail_on_task = true,
            "--help" | "-h" => {
                print_help();
                return Ok(());
            }
            _ => return Err(format!("unknown argument {argument}; use --help").into()),
        }
        index += 1;
    }
    if repetitions == 0 {
        return Err("--repetitions must be greater than zero".into());
    }
    if strategies.is_some() && !compare {
        return Err("--strategies requires --compare".into());
    }
    let workload = if let Some(tool_calls) = single_message_tools {
        if tool_calls == 0 {
            return Err("--single-message-tools must be greater than zero".into());
        }
        if history_turns_was_set {
            return Err(
                "--single-message-tools cannot be combined with --history-turns; they model different workloads"
                    .into(),
            );
        }
        TierBWorkload::SingleMessageAgent {
            tool_calls,
            payload_bytes,
        }
    } else {
        if payload_bytes > 0 {
            return Err("--payload-bytes requires --single-message-tools".into());
        }
        TierBWorkload::MemoryRecall { history_turns }
    };

    let run_stamp = SystemTime::now().duration_since(UNIX_EPOCH)?.as_millis();
    let runner_root = runner_root.unwrap_or_else(|| {
        PathBuf::from("target/tier-b-runs").join(format!("{suite_id}-{run_stamp}"))
    });
    let output = output.unwrap_or_else(|| runner_root.join("report.json"));
    let key_admission = KeyAdmissionPolicy {
        max_key_batches,
        max_key_content_bytes,
    };

    if compare {
        let report = if fixture {
            run_fixture_comparison(
                &suite_id,
                &runner_root,
                repetitions,
                &content,
                workload,
                key_admission,
                strategies.as_deref(),
            )
            .await?
        } else {
            let api_key = secret_env(["LONGCAT_API_KEY", "OPENAI_API_KEY", "OPENAI__API_KEY"])?;
            let model = model
                .or_else(|| public_env(["OPENAI_MODEL", "OPENAI__MODEL"]))
                .ok_or("OpenAI model is required via --model, OPENAI_MODEL, or OPENAI__MODEL")?;
            let base_url = base_url
                .or_else(|| public_env(["OPENAI_BASE_URL", "OPENAI__BASE_URL"]))
                .unwrap_or_else(|| "https://api.openai.com/v1".to_owned());
            run_live_comparison(
                &suite_id,
                &runner_root,
                repetitions,
                &content,
                workload,
                key_admission,
                api_type,
                &api_key,
                &model,
                &base_url,
                strategies.as_deref(),
            )
            .await?
        };
        write_report(&output, &report, pretty).await?;
        if fail_on_task
            && report.strategies.iter().any(|strategy| {
                strategy.report.aggregate.passed_run_count != strategy.report.aggregate.run_count
            })
        {
            return Err("one or more Tier-B strategy tasks failed".into());
        }
        return Ok(());
    }

    let tasks = build_workload_tasks(repetitions, &content, workload)?;
    let short_memory_policy = structure_policy(key_admission);

    let report = if fixture {
        run_tier_b_suite(
            TierBSuiteConfig {
                suite_id,
                evidence_level: TierBEvidenceLevel::Fixture,
                provider: TierBProviderMetadata {
                    api_type: "fixture".to_owned(),
                    model: "fixture-file-provider".to_owned(),
                    base_url: "fixture://local".to_owned(),
                },
                runner_root,
                short_memory_policy,
                compaction_strategy: RuntimeCompactionStrategy::FileBackedGc,
                pointer_gc_checkpoint_batches: 4,
                max_model_steps_per_run: 32,
                tasks,
            },
            FixtureFileProvider::default(),
        )
        .await?
    } else {
        let api_key = secret_env(["LONGCAT_API_KEY", "OPENAI_API_KEY", "OPENAI__API_KEY"])?;
        let model = model
            .or_else(|| public_env(["OPENAI_MODEL", "OPENAI__MODEL"]))
            .ok_or("OpenAI model is required via --model, OPENAI_MODEL, or OPENAI__MODEL")?;
        let base_url = base_url
            .or_else(|| public_env(["OPENAI_BASE_URL", "OPENAI__BASE_URL"]))
            .unwrap_or_else(|| "https://api.openai.com/v1".to_owned());
        let provider = ApiModelProvider::new(ApiProviderConfig::new(
            api_type,
            api_key,
            base_url.clone(),
            model.clone(),
        ))?;
        run_tier_b_suite(
            TierBSuiteConfig {
                suite_id,
                evidence_level: TierBEvidenceLevel::LiveApi,
                provider: TierBProviderMetadata {
                    api_type: api_type.to_string(),
                    model,
                    base_url,
                },
                runner_root,
                short_memory_policy,
                compaction_strategy: RuntimeCompactionStrategy::FileBackedGc,
                pointer_gc_checkpoint_batches: 4,
                max_model_steps_per_run: 32,
                tasks,
            },
            provider,
        )
        .await?
    };

    write_report(&output, &report, pretty).await?;
    if fail_on_task && report.aggregate.passed_run_count != report.aggregate.run_count {
        return Err("one or more Tier-B tasks failed".into());
    }
    Ok(())
}

#[derive(Serialize)]
struct StrategyComparisonReport {
    schema_version: &'static str,
    suite_id: String,
    evidence_level: TierBEvidenceLevel,
    workload: &'static str,
    strategies: Vec<StrategyResult>,
}

#[derive(Serialize)]
struct StrategyResult {
    strategy: &'static str,
    description: &'static str,
    pass_rate_bps: u64,
    total_provider_calls: usize,
    total_input_tokens: u64,
    total_uncached_input_tokens: u64,
    total_output_tokens: u64,
    total_cached_input_tokens: u64,
    total_model_input_bytes: usize,
    total_batch_key_entries: usize,
    total_memory_pointer_entries: usize,
    total_provider_latency_ms: u64,
    total_tool_calls: usize,
    total_redundant_tool_calls: usize,
    total_user_messages: usize,
    report: structure_short_memory_benchmark::TierBReport,
}

impl StrategyResult {
    fn new(
        strategy: &'static str,
        description: &'static str,
        report: structure_short_memory_benchmark::TierBReport,
    ) -> Self {
        let aggregate = &report.aggregate;
        Self {
            strategy,
            description,
            pass_rate_bps: aggregate.pass_rate_bps,
            total_provider_calls: aggregate.total_provider_calls,
            total_input_tokens: aggregate.total_input_tokens,
            total_uncached_input_tokens: aggregate.total_uncached_input_tokens,
            total_output_tokens: aggregate.total_output_tokens,
            total_cached_input_tokens: aggregate.total_cached_input_tokens,
            total_model_input_bytes: aggregate.total_model_input_bytes,
            total_batch_key_entries: aggregate.total_batch_key_entries,
            total_memory_pointer_entries: aggregate.total_memory_pointer_entries,
            total_provider_latency_ms: aggregate.total_provider_latency_ms,
            total_tool_calls: aggregate.total_tool_calls,
            total_redundant_tool_calls: aggregate.total_redundant_tool_calls,
            total_user_messages: aggregate.total_user_messages,
            report,
        }
    }
}

fn comparison_policies(
    key_admission: KeyAdmissionPolicy,
    selected: Option<&[String]>,
) -> Vec<(
    &'static str,
    &'static str,
    ShortMemoryPolicy,
    RuntimeCompactionStrategy,
)> {
    let full_replay = ShortMemoryPolicy::full_replay();
    let ttl_only = ShortMemoryPolicy::ttl_only();
    let mut batch_only = ShortMemoryPolicy::batch_only(2);
    batch_only.key_admission = key_admission;
    let policies = vec![
        (
            "B0",
            "full replay through Runtime with TTL and batch compaction disabled",
            full_replay,
            RuntimeCompactionStrategy::Disabled,
        ),
        (
            "B2",
            "Runtime TTL and relation decay with batch compaction disabled",
            ttl_only,
            RuntimeCompactionStrategy::Disabled,
        ),
        (
            "B3",
            "Runtime batch disclosure with TTL and relation decay disabled",
            batch_only,
            RuntimeCompactionStrategy::Disabled,
        ),
        (
            "S",
            "production Runtime short-memory policy",
            structure_policy(key_admission),
            RuntimeCompactionStrategy::Disabled,
        ),
        (
            "PGC",
            "TTL event GC with exact recoverable pointers and no BatchKey",
            ShortMemoryPolicy::ttl_only(),
            RuntimeCompactionStrategy::PointerGc,
        ),
        (
            "FBGC",
            "lossless file-backed compact for fully TTL-expired closed batches",
            ShortMemoryPolicy::ttl_only(),
            RuntimeCompactionStrategy::FileBackedGc,
        ),
    ];
    policies
        .into_iter()
        .filter(|(strategy, _, _, _)| {
            selected.is_none_or(|selected| selected.iter().any(|item| item == strategy))
        })
        .collect()
}

fn structure_policy(key_admission: KeyAdmissionPolicy) -> ShortMemoryPolicy {
    ShortMemoryPolicy {
        key_admission,
        ..ShortMemoryPolicy::default()
    }
}

fn build_tasks(repetitions: usize, content: &str) -> Result<Vec<TierBTask>, Box<dyn Error>> {
    (1..=repetitions)
        .map(|repetition| {
            TierBTask::recall_write_file(
                format!("write-file-{repetition:04}"),
                format!("run-{repetition:04}/result.txt"),
                format!("tier-b-evidence-{repetition:04}"),
                content.to_owned(),
            )
            .map_err(Into::into)
        })
        .collect()
}

fn build_comparison_tasks(
    repetitions: usize,
    content: &str,
    history_turns: usize,
) -> Result<Vec<TierBTask>, Box<dyn Error>> {
    let mut tasks = build_tasks(repetitions, content)?;
    for task in &mut tasks {
        task.setup_prompts.extend((1..=history_turns).map(|turn| {
            format!(
                "Acknowledge this unrelated history item.\n<memory_distractor_json>{{\"turn\":{turn},\"value\":\"DISTRACTOR_{turn:04}\"}}</memory_distractor_json>"
            )
        }));
    }
    Ok(tasks)
}

#[derive(Clone, Copy)]
enum TierBWorkload {
    MemoryRecall {
        history_turns: usize,
    },
    SingleMessageAgent {
        tool_calls: usize,
        payload_bytes: usize,
    },
}

impl TierBWorkload {
    fn id(self) -> &'static str {
        match self {
            Self::MemoryRecall { .. } => "memory_recall_chat_history",
            Self::SingleMessageAgent { .. } => "single_message_agent",
        }
    }

    fn max_model_steps(self) -> usize {
        match self {
            Self::MemoryRecall { history_turns } => (history_turns + 8).max(32),
            Self::SingleMessageAgent { tool_calls, .. } => (tool_calls + 8).max(32),
        }
    }
}

fn build_workload_tasks(
    repetitions: usize,
    content: &str,
    workload: TierBWorkload,
) -> Result<Vec<TierBTask>, Box<dyn Error>> {
    match workload {
        TierBWorkload::MemoryRecall { history_turns } => {
            build_comparison_tasks(repetitions, content, history_turns)
        }
        TierBWorkload::SingleMessageAgent {
            tool_calls,
            payload_bytes,
        } => (1..=repetitions)
            .map(|repetition| {
                let files = (1..=tool_calls)
                    .map(|tool_index| {
                        let marker = format!("{content}:{tool_index:04}");
                        let exact_content = if payload_bytes == 0 {
                            marker
                        } else {
                            let pad_len = payload_bytes.saturating_sub(marker.len());
                            let pad = "X".repeat(pad_len);
                            format!("{pad}{marker}")
                        };
                        ExpectedFile {
                            path: format!("run-{repetition:04}/single-message-{tool_index:04}.txt"),
                            exact_content,
                        }
                    })
                    .collect();
                TierBTask::write_files(format!("single-message-{repetition:04}"), files)
                    .map_err(Into::into)
            })
            .collect(),
    }
}

async fn run_fixture_comparison(
    suite_id: &str,
    runner_root: &std::path::Path,
    repetitions: usize,
    content: &str,
    workload: TierBWorkload,
    key_admission: KeyAdmissionPolicy,
    selected: Option<&[String]>,
) -> Result<StrategyComparisonReport, Box<dyn Error>> {
    let mut strategies = Vec::new();
    for (strategy, description, policy, compaction_strategy) in
        comparison_policies(key_admission, selected)
    {
        let report = run_tier_b_suite(
            TierBSuiteConfig {
                suite_id: format!("{suite_id}-{strategy}"),
                evidence_level: TierBEvidenceLevel::Fixture,
                provider: TierBProviderMetadata {
                    api_type: "fixture".to_owned(),
                    model: "fixture-file-provider".to_owned(),
                    base_url: "fixture://local".to_owned(),
                },
                runner_root: runner_root.join(strategy.to_ascii_lowercase()),
                short_memory_policy: policy,
                compaction_strategy,
                pointer_gc_checkpoint_batches: 4,
                max_model_steps_per_run: workload.max_model_steps(),
                tasks: build_workload_tasks(repetitions, content, workload)?,
            },
            FixtureFileProvider::default(),
        )
        .await?;
        strategies.push(StrategyResult::new(strategy, description, report));
    }
    Ok(StrategyComparisonReport {
        schema_version: "structure.short-memory.tier-b-comparison/v5",
        suite_id: suite_id.to_owned(),
        evidence_level: TierBEvidenceLevel::Fixture,
        workload: workload.id(),
        strategies,
    })
}

#[allow(clippy::too_many_arguments)]
async fn run_live_comparison(
    suite_id: &str,
    runner_root: &std::path::Path,
    repetitions: usize,
    content: &str,
    workload: TierBWorkload,
    key_admission: KeyAdmissionPolicy,
    api_type: ApiType,
    api_key: &str,
    model: &str,
    base_url: &str,
    selected: Option<&[String]>,
) -> Result<StrategyComparisonReport, Box<dyn Error>> {
    let mut strategies = Vec::new();
    for (strategy, description, policy, compaction_strategy) in
        comparison_policies(key_admission, selected)
    {
        let provider = ApiModelProvider::new(ApiProviderConfig::new(
            api_type,
            api_key.to_owned(),
            base_url.to_owned(),
            model.to_owned(),
        ))?;
        let report = run_tier_b_suite(
            TierBSuiteConfig {
                suite_id: format!("{suite_id}-{strategy}"),
                evidence_level: TierBEvidenceLevel::LiveApi,
                provider: TierBProviderMetadata {
                    api_type: api_type.to_string(),
                    model: model.to_owned(),
                    base_url: base_url.to_owned(),
                },
                runner_root: runner_root.join(strategy.to_ascii_lowercase()),
                short_memory_policy: policy,
                compaction_strategy,
                pointer_gc_checkpoint_batches: 4,
                max_model_steps_per_run: workload.max_model_steps(),
                tasks: build_workload_tasks(repetitions, content, workload)?,
            },
            provider,
        )
        .await?;
        strategies.push(StrategyResult::new(strategy, description, report));
    }
    Ok(StrategyComparisonReport {
        schema_version: "structure.short-memory.tier-b-comparison/v5",
        suite_id: suite_id.to_owned(),
        evidence_level: TierBEvidenceLevel::LiveApi,
        workload: workload.id(),
        strategies,
    })
}

async fn write_report(
    output: &std::path::Path,
    report: &impl Serialize,
    pretty: bool,
) -> Result<(), Box<dyn Error>> {
    if let Some(parent) = output.parent() {
        tokio::fs::create_dir_all(parent).await?;
    }
    let rendered = if pretty {
        serde_json::to_string_pretty(report)?
    } else {
        serde_json::to_string(report)?
    };
    tokio::fs::write(output, format!("{rendered}\n")).await?;
    writeln!(io::stdout().lock(), "{rendered}")?;
    Ok(())
}

fn secret_env<const N: usize>(names: [&str; N]) -> Result<String, Box<dyn Error>> {
    names
        .into_iter()
        .find_map(|name| env::var(name).ok().filter(|value| !value.trim().is_empty()))
        .ok_or_else(|| {
            "API key is required via LONGCAT_API_KEY, OPENAI_API_KEY, or OPENAI__API_KEY"
                .to_owned()
                .into()
        })
}

fn public_env<const N: usize>(names: [&str; N]) -> Option<String> {
    names
        .into_iter()
        .find_map(|name| env::var(name).ok().filter(|value| !value.trim().is_empty()))
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

fn parse_strategies(raw: &str) -> Result<Vec<String>, Box<dyn Error>> {
    const VALID: [&str; 6] = ["B0", "B2", "B3", "S", "PGC", "FBGC"];
    let mut selected = Vec::new();
    for item in raw
        .split(',')
        .map(str::trim)
        .filter(|item| !item.is_empty())
    {
        let normalized = item.to_ascii_uppercase();
        if !VALID.contains(&normalized.as_str()) {
            return Err(format!(
                "invalid strategy {item}; expected a comma-separated subset of B0,B2,B3,S,PGC,FBGC"
            )
            .into());
        }
        if !selected.contains(&normalized) {
            selected.push(normalized);
        }
    }
    if selected.is_empty() {
        return Err("--strategies must select at least one strategy".into());
    }
    Ok(selected)
}

fn print_help() {
    println!(
        "Structure short-memory Tier-B benchmark\n\
         \n\
         Usage:\n\
           cargo run -p structure-short-memory-benchmark --bin tier_b -- [options]\n\
         \n\
         Options:\n\
           --fixture                 Use the deterministic provider; no API call\n\
           --compare                 Run B0, B2, B3, S, PGC, and FBGC through Runtime\n\
           --strategies <CSV>        With --compare, run only this subset (for example B0,FBGC)\n\
           --suite-id <ID>           Stable suite identifier\n\
           --repetitions <N>         Number of isolated file tasks (default: 1)\n\
           --history-turns <N>       Extra user-message turns for memory-recall (default: 8)\n\
           --single-message-tools <N> One user message requiring N exact write_file calls\n\
           --payload-bytes <N>       Pad each write_file content to N bytes (single-message only)\n\
           --api-type <TYPE>         Provider wire API (default: open_ai_chat_completions)\n\
           --model <MODEL>           Live model; or OPENAI_MODEL / OPENAI__MODEL\n\
           --base-url <URL>          Live endpoint; or OPENAI_BASE_URL / OPENAI__BASE_URL\n\
           --runner-root <PATH>      Isolated LocalRunner root\n\
           --output <PATH>           JSON artifact path\n\
           --content <TEXT>          Exact expected file content\n\
           --max-key-batches <N>     Historical key batch budget\n\
           --max-key-bytes <N>       Historical key-content byte budget\n\
           --pretty                  Pretty-print report JSON\n\
           --fail-on-task            Exit non-zero if any task fails\n\
           --help                    Show this help\n\
         \n\
         Live credentials are read only from LONGCAT_API_KEY, OPENAI_API_KEY, or OPENAI__API_KEY."
    );
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn comparison_maps_only_runtime_executable_policies() {
        let policies = comparison_policies(KeyAdmissionPolicy::default(), None);
        let ids: Vec<_> = policies.iter().map(|(id, _, _, _)| *id).collect();
        assert_eq!(ids, ["B0", "B2", "B3", "S", "PGC", "FBGC"]);
        assert_eq!(policies[0].2.recent_turns_load_all, usize::MAX);
        assert!(!policies[0].2.batch_compaction_enabled);
        assert!(!policies[1].2.batch_compaction_enabled);
        assert_eq!(policies[1].2.recent_turns_load_all, 2);
        assert_eq!(policies[2].2.recent_turns_load_all, 2);
        assert!(policies[2].2.batch_compaction_enabled);
        assert_eq!(policies[3].2, ShortMemoryPolicy::default());
        assert_eq!(policies[3].3, RuntimeCompactionStrategy::Disabled);
        assert!(!policies[4].2.batch_compaction_enabled);
        assert_eq!(policies[4].3, RuntimeCompactionStrategy::PointerGc);
        assert!(!policies[5].2.batch_compaction_enabled);
        assert_eq!(policies[5].3, RuntimeCompactionStrategy::FileBackedGc);
    }

    #[test]
    fn comparison_strategy_filter_is_validated_and_ordered_canonically() {
        let selected = parse_strategies("fbgc,b0,FBGC").expect("selection is valid");
        assert_eq!(selected, ["FBGC", "B0"]);

        let policies = comparison_policies(KeyAdmissionPolicy::default(), Some(&selected));
        let ids: Vec<_> = policies.iter().map(|(id, _, _, _)| *id).collect();
        assert_eq!(ids, ["B0", "FBGC"]);
        assert!(parse_strategies("B9").is_err());
        assert!(parse_strategies(",").is_err());
    }

    #[test]
    fn single_message_workload_has_one_prompt_and_no_setup_messages() {
        let tasks = build_workload_tasks(
            1,
            "EXPECTED",
            TierBWorkload::SingleMessageAgent {
                tool_calls: 6,
                payload_bytes: 0,
            },
        )
        .expect("workload is valid");

        assert_eq!(tasks.len(), 1);
        assert!(tasks[0].setup_prompts.is_empty());
        assert_eq!(tasks[0].expected_files.len(), 6);
        assert_eq!(tasks[0].max_tool_calls, 6);
    }
}
