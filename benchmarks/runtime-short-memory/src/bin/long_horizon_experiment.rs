use std::error::Error;
use std::fs;
use std::path::PathBuf;

use structure_short_memory_benchmark::{
    ExperimentPhase, LongHorizonArm, LongHorizonManifest, TrialLedgerEntry,
    summarize_core_ablation, summarize_primary, summarize_qualification,
};

fn main() -> Result<(), Box<dyn Error>> {
    let args: Vec<String> = std::env::args().skip(1).collect();
    match args.first().map(String::as_str) {
        Some("plan") => {
            let output = PathBuf::from(required(&args, "--output")?);
            let tasks = optional(&args, "--tasks")
                .unwrap_or_default()
                .split(',')
                .filter(|task| !task.trim().is_empty())
                .map(|task| task.trim().to_owned())
                .collect();
            let seed = required(&args, "--seed")?.parse()?;
            let provider = required(&args, "--provider")?.to_owned();
            let model = required(&args, "--model")?.to_owned();
            let mut manifest = match required(&args, "--phase")? {
                "qualification" => LongHorizonManifest::qualification(seed, provider, model),
                "primary" => LongHorizonManifest::core_primary(tasks, seed, provider, model),
                "replication" => LongHorizonManifest::cross_model_confirmation(
                    tasks, seed, provider, model,
                ),
                _ => return Err("--phase must be qualification, primary, or replication".into()),
            };
            manifest.thinking_enabled = optional(&args, "--thinking-enabled")
                .unwrap_or("false")
                .parse()?;
            manifest.max_output_tokens = optional(&args, "--max-output-tokens")
                .unwrap_or("8192")
                .parse()?;
            manifest.max_model_steps = optional(&args, "--max-model-steps")
                .unwrap_or("128")
                .parse()?;
            manifest.timeout_seconds = optional(&args, "--timeout-seconds")
                .unwrap_or("900")
                .parse()?;
            manifest.checkpoint_batches = optional(&args, "--checkpoint-batches")
                .unwrap_or("8")
                .parse()?;
            manifest.compaction_effort = optional(&args, "--compaction-effort")
                .unwrap_or("1")
                .parse()?;
            manifest.continuation_probability_bps = optional(
                &args,
                "--continuation-probability-bps",
            )
            .unwrap_or("7500")
            .parse()?;
            manifest.cached_input_cost_bps = optional(&args, "--cached-input-cost-bps")
                .unwrap_or("0")
                .parse()?;
            manifest.price_weighting_auditable = optional(
                &args,
                "--price-weighting-auditable",
            )
            .unwrap_or("false")
            .parse()?;
            manifest.price_weighting_source = optional(&args, "--price-weighting-source")
                .map(str::to_owned);
            manifest.validate()?;
            if output.exists() {
                return Err(format!("refusing to overwrite {}", output.display()).into());
            }
            if let Some(parent) = output.parent() {
                fs::create_dir_all(parent)?;
            }
            fs::write(output, serde_json::to_vec_pretty(&manifest)?)?;
        }
        Some("summarize") => {
            let manifest: LongHorizonManifest = serde_json::from_slice(&fs::read(required(&args, "--manifest")?)?)?;
            let ledger: Vec<TrialLedgerEntry> = serde_json::from_slice(&fs::read(required(&args, "--ledger")?)?)?;
            let qualification = manifest
                .trials
                .iter()
                .any(|trial| trial.phase == ExperimentPhase::Qualification);
            let summary = if qualification {
                serde_json::to_value(summarize_qualification(&manifest, &ledger)?)?
            } else if manifest
                .trials
                .iter()
                .any(|trial| trial.arm == LongHorizonArm::B2)
            {
                serde_json::to_value(summarize_core_ablation(&manifest, &ledger)?)?
            } else {
                serde_json::to_value(summarize_primary(&manifest, &ledger)?)?
            };
            let encoded = serde_json::to_vec_pretty(&summary)?;
            if let Some(output) = optional(&args, "--output") {
                let output = PathBuf::from(output);
                if output.exists() {
                    return Err(format!("refusing to overwrite {}", output.display()).into());
                }
                if let Some(parent) = output.parent() {
                    fs::create_dir_all(parent)?;
                }
                fs::write(output, encoded)?;
            } else {
                println!("{}", String::from_utf8(encoded)?);
            }
        }
        _ => return Err("usage: long_horizon_experiment plan --phase qualification|primary|replication --output FILE [--tasks A,B,C] --seed N --provider NAME --model NAME [--thinking-enabled true|false] [--max-output-tokens N] [--max-model-steps N] [--timeout-seconds N] [--checkpoint-batches N] [--compaction-effort N] [--continuation-probability-bps N] [--cached-input-cost-bps N] [--price-weighting-auditable true|false] [--price-weighting-source TEXT] | summarize --manifest FILE --ledger FILE [--output FILE]".into()),
    }
    Ok(())
}

fn optional<'a>(args: &'a [String], name: &str) -> Option<&'a str> {
    args.iter()
        .position(|arg| arg == name)
        .and_then(|index| args.get(index + 1))
        .map(String::as_str)
}

fn required<'a>(args: &'a [String], name: &str) -> Result<&'a str, Box<dyn Error>> {
    args.iter()
        .position(|arg| arg == name)
        .and_then(|index| args.get(index + 1))
        .map(String::as_str)
        .ok_or_else(|| format!("{name} is required").into())
}
