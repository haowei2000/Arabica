use std::env;
use std::error::Error;
use std::fmt::Display;
use std::io::{self, Write};
use std::path::PathBuf;
use std::time::{SystemTime, UNIX_EPOCH};

use serde::Serialize;
use structure_provider::{ApiModelProvider, ApiProviderConfig, ApiType};
use structure_runtime::{KeyAdmissionPolicy, ShortMemoryPolicy};
use structure_short_memory_benchmark::{
    FixtureFileProvider, TierBEvidenceLevel, TierBProviderMetadata, TierBSuiteConfig, TierBTask,
    run_tier_b_suite,
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
    let mut suite_id = "tier-b-write-file".to_owned();
    let mut repetitions = 1_usize;
    let mut history_turns = 8_usize;
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
            "--suite-id" => suite_id = value(&arguments, &mut index, argument)?.to_owned(),
            "--repetitions" => {
                repetitions = parse(value(&arguments, &mut index, argument)?, argument)?;
            }
            "--history-turns" => {
                history_turns = parse(value(&arguments, &mut index, argument)?, argument)?;
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
                history_turns,
                key_admission,
            )
            .await?
        } else {
            let api_key = secret_env(["OPENAI_API_KEY", "OPENAI__API_KEY"])?;
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
                history_turns,
                key_admission,
                api_type,
                &api_key,
                &model,
                &base_url,
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

    let tasks = build_tasks(repetitions, &content)?;
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
                tasks,
            },
            FixtureFileProvider::default(),
        )
        .await?
    } else {
        let api_key = secret_env(["OPENAI_API_KEY", "OPENAI__API_KEY"])?;
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
    strategies: Vec<StrategyResult>,
}

#[derive(Serialize)]
struct StrategyResult {
    strategy: &'static str,
    description: &'static str,
    pass_rate_bps: u64,
    total_provider_calls: usize,
    total_input_tokens: u64,
    total_output_tokens: u64,
    total_cached_input_tokens: u64,
    total_provider_latency_ms: u64,
    total_tool_calls: usize,
    total_redundant_tool_calls: usize,
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
            total_output_tokens: aggregate.total_output_tokens,
            total_cached_input_tokens: aggregate.total_cached_input_tokens,
            total_provider_latency_ms: aggregate.total_provider_latency_ms,
            total_tool_calls: aggregate.total_tool_calls,
            total_redundant_tool_calls: aggregate.total_redundant_tool_calls,
            report,
        }
    }
}

fn comparison_policies(
    key_admission: KeyAdmissionPolicy,
) -> Vec<(&'static str, &'static str, ShortMemoryPolicy)> {
    let full_replay = ShortMemoryPolicy::batch_only(usize::MAX);
    let ttl_only = ShortMemoryPolicy {
        recent_turns_load_all: usize::MAX,
        ..ShortMemoryPolicy::default()
    };
    let mut batch_only = ShortMemoryPolicy::batch_only(2);
    batch_only.key_admission = key_admission;
    vec![
        (
            "B0",
            "full replay through Runtime with TTL and batch compaction disabled",
            full_replay,
        ),
        (
            "B2",
            "Runtime TTL and relation decay with batch compaction disabled",
            ttl_only,
        ),
        (
            "B3",
            "Runtime batch disclosure with TTL and relation decay disabled",
            batch_only,
        ),
        (
            "S",
            "production Runtime short-memory policy",
            structure_policy(key_admission),
        ),
    ]
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

async fn run_fixture_comparison(
    suite_id: &str,
    runner_root: &std::path::Path,
    repetitions: usize,
    content: &str,
    history_turns: usize,
    key_admission: KeyAdmissionPolicy,
) -> Result<StrategyComparisonReport, Box<dyn Error>> {
    let mut strategies = Vec::new();
    for (strategy, description, policy) in comparison_policies(key_admission) {
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
                tasks: build_comparison_tasks(repetitions, content, history_turns)?,
            },
            FixtureFileProvider::default(),
        )
        .await?;
        strategies.push(StrategyResult::new(strategy, description, report));
    }
    Ok(StrategyComparisonReport {
        schema_version: "structure.short-memory.tier-b-comparison/v1",
        suite_id: suite_id.to_owned(),
        evidence_level: TierBEvidenceLevel::Fixture,
        strategies,
    })
}

#[allow(clippy::too_many_arguments)]
async fn run_live_comparison(
    suite_id: &str,
    runner_root: &std::path::Path,
    repetitions: usize,
    content: &str,
    history_turns: usize,
    key_admission: KeyAdmissionPolicy,
    api_type: ApiType,
    api_key: &str,
    model: &str,
    base_url: &str,
) -> Result<StrategyComparisonReport, Box<dyn Error>> {
    let mut strategies = Vec::new();
    for (strategy, description, policy) in comparison_policies(key_admission) {
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
                tasks: build_comparison_tasks(repetitions, content, history_turns)?,
            },
            provider,
        )
        .await?;
        strategies.push(StrategyResult::new(strategy, description, report));
    }
    Ok(StrategyComparisonReport {
        schema_version: "structure.short-memory.tier-b-comparison/v1",
        suite_id: suite_id.to_owned(),
        evidence_level: TierBEvidenceLevel::LiveApi,
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
            "OpenAI API key is required via OPENAI_API_KEY or OPENAI__API_KEY"
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

fn print_help() {
    println!(
        "Structure short-memory Tier-B benchmark\n\
         \n\
         Usage:\n\
           cargo run -p structure-short-memory-benchmark --bin tier_b -- [options]\n\
         \n\
         Options:\n\
           --fixture                 Use the deterministic provider; no API call\n\
           --compare                 Run B0, B2, B3, and S through Runtime\n\
           --suite-id <ID>           Stable suite identifier\n\
           --repetitions <N>         Number of isolated file tasks (default: 1)\n\
           --history-turns <N>       Comparison distractor turns (default: 8)\n\
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
         Live credentials are read only from OPENAI_API_KEY or OPENAI__API_KEY."
    );
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn comparison_maps_only_runtime_executable_policies() {
        let policies = comparison_policies(KeyAdmissionPolicy::default());
        let ids: Vec<_> = policies.iter().map(|(id, _, _)| *id).collect();
        assert_eq!(ids, ["B0", "B2", "B3", "S"]);
        assert_eq!(policies[0].2.recent_turns_load_all, usize::MAX);
        assert_eq!(policies[1].2.recent_turns_load_all, usize::MAX);
        assert_eq!(policies[2].2.recent_turns_load_all, 2);
        assert_eq!(policies[3].2, ShortMemoryPolicy::default());
    }
}
