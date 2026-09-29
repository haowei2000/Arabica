use std::error::Error;
use std::fs;
use std::path::PathBuf;

use structure_short_memory_benchmark::{
    BlendCampaignManifest, BlendTask, BlendTrialResult, MemoryEvidenceManifest,
    MemoryEvidenceTrialResult, summarize_blend_campaign, summarize_memory_evidence,
};

fn main() -> Result<(), Box<dyn Error>> {
    let args = std::env::args().skip(1).collect::<Vec<_>>();
    match args.first().map(String::as_str) {
        Some("plan") => {
            let tasks = required(&args, "--tasks")?
                .split(',')
                .map(|item| {
                    let mut parts = item.split(':');
                    let id = parts.next().unwrap_or_default().trim();
                    let stratum = parts.next().unwrap_or_default().trim();
                    let continuity_challenge = parts.next().unwrap_or("false").parse::<bool>()?;
                    if parts.next().is_some() {
                        return Err("task format is id:stratum:continuity".into());
                    }
                    Ok(BlendTask {
                        id: id.to_owned(),
                        stratum: stratum.to_owned(),
                        continuity_challenge,
                    })
                })
                .collect::<Result<Vec<_>, Box<dyn Error>>>()?;
            let manifest = BlendCampaignManifest::new(
                required(&args, "--seed")?.parse()?,
                required(&args, "--strong-model")?.to_owned(),
                required(&args, "--inexpensive-model")?.to_owned(),
                required(&args, "--fixed-phase-policy")?.to_owned(),
                required(&args, "--adaptive-policy")?.to_owned(),
                required(&args, "--pricing-version")?.to_owned(),
                required(&args, "--success-oracle-version")?.to_owned(),
                required(&args, "--repetitions")?.parse()?,
                tasks,
            )?;
            manifest.validate()?;
            write_new(PathBuf::from(required(&args, "--output")?), &manifest)?;
        }
        Some("summarize") => {
            let manifest: BlendCampaignManifest =
                serde_json::from_slice(&fs::read(required(&args, "--manifest")?)?)?;
            let results: Vec<BlendTrialResult> =
                serde_json::from_slice(&fs::read(required(&args, "--results")?)?)?;
            let summary = summarize_blend_campaign(&manifest, &results)?;
            write_new(PathBuf::from(required(&args, "--output")?), &summary)?;
        }
        Some("plan-memory") => {
            let manifest = MemoryEvidenceManifest::new(
                required(&args, "--seed")?.parse()?,
                required(&args, "--fixed-model")?.to_owned(),
                required(&args, "--fixed-routing-policy")?.to_owned(),
                required(&args, "--baseline-memory-policy")?.to_owned(),
                required(&args, "--enhanced-memory-policy")?.to_owned(),
                required(&args, "--success-oracle-version")?.to_owned(),
                required(&args, "--evidence-oracle-version")?.to_owned(),
                required(&args, "--repetitions")?.parse()?,
                parse_tasks(required(&args, "--tasks")?)?,
            )?;
            manifest.validate()?;
            write_new(PathBuf::from(required(&args, "--output")?), &manifest)?;
        }
        Some("summarize-memory") => {
            let manifest: MemoryEvidenceManifest =
                serde_json::from_slice(&fs::read(required(&args, "--manifest")?)?)?;
            let results: Vec<MemoryEvidenceTrialResult> =
                serde_json::from_slice(&fs::read(required(&args, "--results")?)?)?;
            let summary = summarize_memory_evidence(&manifest, &results)?;
            write_new(PathBuf::from(required(&args, "--output")?), &summary)?;
        }
        _ => {
            return Err(
                "usage: blend_campaign plan|summarize|plan-memory|summarize-memory --...".into(),
            );
        }
    }
    Ok(())
}

fn parse_tasks(value: &str) -> Result<Vec<BlendTask>, Box<dyn Error>> {
    value
        .split(',')
        .map(|item| {
            let mut parts = item.split(':');
            let id = parts.next().unwrap_or_default().trim();
            let stratum = parts.next().unwrap_or_default().trim();
            let continuity_challenge = parts.next().unwrap_or("false").parse::<bool>()?;
            if parts.next().is_some() {
                return Err("task format is id:stratum:continuity".into());
            }
            Ok(BlendTask {
                id: id.to_owned(),
                stratum: stratum.to_owned(),
                continuity_challenge,
            })
        })
        .collect()
}

fn required<'a>(args: &'a [String], name: &str) -> Result<&'a str, Box<dyn Error>> {
    let index = args
        .iter()
        .position(|arg| arg == name)
        .ok_or_else(|| format!("missing {name}"))?;
    args.get(index + 1)
        .map(String::as_str)
        .ok_or_else(|| format!("missing value for {name}").into())
}

fn write_new<T: serde::Serialize>(path: PathBuf, value: &T) -> Result<(), Box<dyn Error>> {
    if path.exists() {
        return Err(format!("refusing to overwrite {}", path.display()).into());
    }
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    fs::write(path, serde_json::to_vec_pretty(value)?)?;
    Ok(())
}
