//! Offline ideal KV prefix accounting over externally tokenized requests.
use serde::{Deserialize, Serialize};
use std::{error::Error, fs};
use structure_short_memory_benchmark::experiment::{PrefixCache, sha256, write_new};

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Input {
    tokenizer_sha256: String,
    chat_template_sha256: String,
    arms: Vec<Arm>,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Arm {
    name: String,
    requests: Vec<Vec<u32>>,
}
#[derive(Serialize)]
struct Counts {
    input: usize,
    hit: usize,
    fresh: usize,
}

fn main() -> Result<(), Box<dyn Error>> {
    let args: Vec<_> = std::env::args().skip(1).collect();
    if args.len() != 2 {
        return Err("usage: prefix_cache TOKENIZED_INPUT.json NEW_REPORT.json".into());
    }
    let bytes = fs::read(&args[0])?;
    let input: Input = serde_json::from_slice(&bytes)?;
    for h in [&input.tokenizer_sha256, &input.chat_template_sha256] {
        if h.len() != 64 || !h.bytes().all(|b| b.is_ascii_hexdigit()) {
            return Err("tokenizer and chat template SHA-256 identifiers are required".into());
        }
    }
    let mut names = std::collections::HashSet::new();
    let mut reports = Vec::new();
    for arm in input.arms {
        if arm.name.is_empty() || !names.insert(arm.name.clone()) || arm.requests.is_empty() {
            return Err("each arm needs a unique name and at least one tokenized request".into());
        }
        let mut cache = PrefixCache::default();
        let calls: Vec<_> = arm
            .requests
            .iter()
            .map(|tokens| {
                let (hit, fresh) = cache.observe(tokens);
                Counts {
                    input: tokens.len(),
                    hit,
                    fresh,
                }
            })
            .collect();
        reports.push(serde_json::json!({"arm": arm.name, "calls": calls,
            "total_input": calls.iter().map(|c| c.input).sum::<usize>(),
            "total_hit": calls.iter().map(|c| c.hit).sum::<usize>(),
            "total_fresh": calls.iter().map(|c| c.fresh).sum::<usize>()}));
    }
    let output = serde_json::json!({"schema":"structure.prefix-cache/v1", "input_sha256":sha256(&bytes),
        "tokenizer_sha256":input.tokenizer_sha256, "chat_template_sha256":input.chat_template_sha256,
        "policy":"cold per arm; retain prior input prefixes; no eviction; token-granular; outputs are not prefilled into cache; simulated, not billed usage",
        "arms":reports});
    write_new(
        std::path::Path::new(&args[1]),
        &serde_json::to_vec_pretty(&output)?,
    )?;
    Ok(())
}
