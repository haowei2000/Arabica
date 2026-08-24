use std::error::Error;
use std::fs;
use std::path::{Path, PathBuf};

use structure_short_memory_benchmark::{
    CliComparisonManifest, CliComparisonPlan, CliTrialReport, create_cli_comparison_manifest,
    preflight_cli_comparison, run_cli_trial, summarize_cli_comparison,
};

const USAGE: &str = "usage:
  cli_comparison plan --suite FILE --output FILE --model PROVIDER/MODEL --thinking LEVEL --piagent-package-root DIR [--provider-base-url URL --provider-api-key-env NAME] [--repeats N] [--timeout-seconds N] [--seed N] [--codex-program PATH] [--pi-program PATH]
  cli_comparison preflight --manifest FILE
  cli_comparison run --manifest FILE --trial-id ID --output-root DIR --yes
  cli_comparison summarize --manifest FILE --reports-root DIR";

#[tokio::main]
async fn main() -> Result<(), Box<dyn Error>> {
    let args: Vec<String> = std::env::args().skip(1).collect();
    match args.first().map(String::as_str) {
        Some("plan") => plan(&args)?,
        Some("preflight") => {
            let manifest = read_manifest(required(&args, "--manifest")?)?;
            let report = preflight_cli_comparison(&manifest).await;
            println!("{}", serde_json::to_string_pretty(&report)?);
            if !report.ready {
                return Err("preflight is not ready".into());
            }
        }
        Some("run") => {
            if !args.iter().any(|arg| arg == "--yes") {
                return Err("run can invoke paid providers; pass --yes explicitly".into());
            }
            let manifest = read_manifest(required(&args, "--manifest")?)?;
            let trial_id = required(&args, "--trial-id")?;
            let output_root = PathBuf::from(required(&args, "--output-root")?);
            let trial = manifest
                .trials
                .iter()
                .find(|trial| trial.trial_id == trial_id)
                .ok_or_else(|| format!("unknown trial {trial_id}"))?;
            let artifact = output_root.join(&trial.artifact_path);
            if artifact.exists() {
                return Err(
                    format!("trial artifact already exists: {}", artifact.display()).into(),
                );
            }
            let report = run_cli_trial(&manifest, trial_id, &output_root).await?;
            if let Some(parent) = artifact.parent() {
                fs::create_dir_all(parent)?;
            }
            fs::write(&artifact, serde_json::to_vec_pretty(&report)?)?;
            println!("{}", serde_json::to_string_pretty(&report)?);
        }
        Some("summarize") => {
            let manifest = read_manifest(required(&args, "--manifest")?)?;
            let reports_root = Path::new(required(&args, "--reports-root")?);
            let mut reports: Vec<CliTrialReport> = Vec::new();
            for path in manifest
                .trials
                .iter()
                .map(|trial| reports_root.join(&trial.artifact_path))
                .filter(|path| path.exists())
            {
                reports.push(serde_json::from_slice(&fs::read(path)?)?);
            }
            println!(
                "{}",
                serde_json::to_string_pretty(&summarize_cli_comparison(&manifest, &reports)?)?
            );
        }
        _ => return Err(USAGE.into()),
    }
    Ok(())
}

fn plan(args: &[String]) -> Result<(), Box<dyn Error>> {
    let suite = Path::new(required(args, "--suite")?);
    let output = PathBuf::from(required(args, "--output")?);
    if output.exists() {
        return Err(format!("manifest already exists: {}", output.display()).into());
    }
    let manifest = create_cli_comparison_manifest(
        suite,
        CliComparisonPlan {
            model: required(args, "--model")?.to_owned(),
            thinking: required(args, "--thinking")?.to_owned(),
            repetitions: optional(args, "--repeats").unwrap_or("3").parse()?,
            timeout_seconds: optional(args, "--timeout-seconds")
                .unwrap_or("900")
                .parse()?,
            seed: optional(args, "--seed").unwrap_or("20260824").parse()?,
            codex_program: optional(args, "--codex-program")
                .unwrap_or("codex")
                .to_owned(),
            pi_program: optional(args, "--pi-program").unwrap_or("pi").to_owned(),
            piagent_package_root: required(args, "--piagent-package-root")?.to_owned(),
            provider_base_url: optional(args, "--provider-base-url").map(str::to_owned),
            provider_api_key_env: optional(args, "--provider-api-key-env").map(str::to_owned),
        },
    )?;
    if let Some(parent) = output.parent() {
        fs::create_dir_all(parent)?;
    }
    fs::write(output, serde_json::to_vec_pretty(&manifest)?)?;
    Ok(())
}

fn read_manifest(path: &str) -> Result<CliComparisonManifest, Box<dyn Error>> {
    Ok(serde_json::from_slice(&fs::read(path)?)?)
}

fn optional<'a>(args: &'a [String], name: &str) -> Option<&'a str> {
    args.iter()
        .position(|arg| arg == name)
        .and_then(|index| args.get(index + 1))
        .map(String::as_str)
}

fn required<'a>(args: &'a [String], name: &str) -> Result<&'a str, Box<dyn Error>> {
    optional(args, name).ok_or_else(|| format!("{name} is required").into())
}
