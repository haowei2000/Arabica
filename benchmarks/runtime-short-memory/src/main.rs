use std::env;
use std::error::Error;
use std::fmt::Display;
use std::io::{self, Write};

use structure_runtime::{KeyAdmissionPolicy, ShortMemoryPolicy};
use structure_short_memory_benchmark::{
    Baseline, BenchmarkRun, ScalingConfig, ScalingRunner, SyntheticTraceConfig,
    SyntheticTraceGenerator,
};

fn main() {
    if let Err(error) = run() {
        eprintln!("short-memory benchmark failed: {error}");
        std::process::exit(1);
    }
}

fn run() -> Result<(), Box<dyn Error>> {
    let mut config = SyntheticTraceConfig::default();
    let mut baseline_name = "b0".to_owned();
    let mut baseline_was_set = false;
    let mut tail_k = 128_usize;
    let mut pretty = false;
    let mut fail_on_gate = false;
    let mut scale_event_counts = None;
    let mut warmup_iterations = 5_usize;
    let mut measured_iterations = 20_usize;
    let mut max_key_batches = None;
    let mut max_key_content_bytes = None;
    let arguments: Vec<String> = env::args().skip(1).collect();
    let mut index = 0_usize;

    while index < arguments.len() {
        let argument = &arguments[index];
        match argument.as_str() {
            "--baseline" => {
                baseline_name = value(&arguments, &mut index, argument)?.to_owned();
                baseline_was_set = true;
            }
            "--tail-k" => tail_k = parse(value(&arguments, &mut index, argument)?, argument)?,
            "--turns" => {
                config.turn_count = parse(value(&arguments, &mut index, argument)?, argument)?;
            }
            "--tools-per-turn" => {
                config.tool_calls_per_turn =
                    parse(value(&arguments, &mut index, argument)?, argument)?;
            }
            "--chunks-per-tool" => {
                config.command_output_chunks_per_tool =
                    parse(value(&arguments, &mut index, argument)?, argument)?;
            }
            "--payload-chars" => {
                config.payload_chars = parse(value(&arguments, &mut index, argument)?, argument)?;
            }
            "--seed" => config.seed = parse(value(&arguments, &mut index, argument)?, argument)?,
            "--trace-id" => config.trace_id = value(&arguments, &mut index, argument)?.to_owned(),
            "--failure-every" => {
                let raw = value(&arguments, &mut index, argument)?;
                config.failure_every = if raw == "none" {
                    None
                } else {
                    Some(parse(raw, argument)?)
                };
            }
            "--fork-after-turn" => {
                let raw = value(&arguments, &mut index, argument)?;
                config.fork_after_turn = if raw == "none" {
                    None
                } else {
                    Some(parse(raw, argument)?)
                };
            }
            "--evidence-horizon-turns" => {
                config.evidence_horizon_turns =
                    parse(value(&arguments, &mut index, argument)?, argument)?;
            }
            "--scale-events" => {
                scale_event_counts = Some(parse_csv(value(&arguments, &mut index, argument)?)?);
            }
            "--warmup" => {
                warmup_iterations = parse(value(&arguments, &mut index, argument)?, argument)?;
            }
            "--iterations" => {
                measured_iterations = parse(value(&arguments, &mut index, argument)?, argument)?;
            }
            "--max-key-batches" => {
                max_key_batches = Some(parse(value(&arguments, &mut index, argument)?, argument)?);
            }
            "--max-key-bytes" => {
                max_key_content_bytes =
                    Some(parse(value(&arguments, &mut index, argument)?, argument)?);
            }
            "--pretty" => pretty = true,
            "--fail-on-gate" => fail_on_gate = true,
            "--help" | "-h" => {
                print_help();
                return Ok(());
            }
            _ => return Err(format!("unknown argument {argument}; use --help").into()),
        }
        index += 1;
    }

    if let Some(target_event_counts) = scale_event_counts {
        if cfg!(debug_assertions) {
            return Err(
                "scaling requires a release build; use cargo run --release -p structure-short-memory-benchmark"
                    .into(),
            );
        }
        let key_admission = KeyAdmissionPolicy {
            max_key_batches,
            max_key_content_bytes,
        };
        let baselines = if baseline_was_set && baseline_name != "all" {
            vec![baseline_from_name(&baseline_name, tail_k, key_admission)?]
        } else {
            Baseline::default_suite(tail_k)?
                .into_iter()
                .map(|baseline| baseline.with_key_admission(key_admission))
                .collect()
        };
        let report = ScalingRunner::run(&ScalingConfig {
            target_event_counts,
            warmup_iterations,
            measured_iterations,
            synthetic: config,
            baselines,
        })?;
        print_json(&report, pretty)?;
        if fail_on_gate && !report.all_correctness_gates_passed() {
            return Err("one or more scaling correctness gates failed".into());
        }
        return Ok(());
    }

    if baseline_name == "all" {
        return Err("--baseline all requires --scale-events".into());
    }
    let baseline = baseline_from_name(
        &baseline_name,
        tail_k,
        KeyAdmissionPolicy {
            max_key_batches,
            max_key_content_bytes,
        },
    )?;
    let trace = SyntheticTraceGenerator::generate(&config)?;
    let report = BenchmarkRun::execute(&trace, baseline)?;
    print_json(&report, pretty)?;

    if fail_on_gate && !report.correctness.passed() {
        return Err("one or more correctness gates failed".into());
    }
    Ok(())
}

fn baseline_from_name(
    name: &str,
    tail_k: usize,
    key_admission: KeyAdmissionPolicy,
) -> Result<Baseline, Box<dyn Error>> {
    match name {
        "b0" | "full-replay" => Ok(Baseline::FullReplay),
        "b1" | "tail-k" => Ok(Baseline::TailK {
            entry_limit: tail_k,
        }),
        "b2" | "ttl-only" => Ok(Baseline::TtlOnly {
            policy: ShortMemoryPolicy::default(),
        }),
        "b3" | "batch-only" => Ok(Baseline::BatchOnly {
            recent_turns_load_all: 2,
            key_admission,
        }),
        "s" | "structure" => {
            let policy = ShortMemoryPolicy {
                key_admission,
                ..ShortMemoryPolicy::default()
            };
            Ok(Baseline::Structure { policy })
        }
        _ => Err(format!("unknown baseline {name}; expected b0, b1, b2, b3, s, or all").into()),
    }
}

fn print_json(value: &impl serde::Serialize, pretty: bool) -> Result<(), Box<dyn Error>> {
    let output = if pretty {
        serde_json::to_string_pretty(value)?
    } else {
        serde_json::to_string(value)?
    };
    writeln!(io::stdout().lock(), "{output}")?;
    Ok(())
}

fn parse_csv(raw: &str) -> Result<Vec<usize>, Box<dyn Error>> {
    let values: Vec<usize> = raw
        .split(',')
        .map(|value| parse(value.trim(), "--scale-events"))
        .collect::<Result<_, _>>()?;
    if values.is_empty() {
        return Err("--scale-events requires at least one count".into());
    }
    Ok(values)
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
        "Rust-native Structure short-memory Tier-A benchmark\n\
         \n\
         Usage:\n\
           cargo run -p structure-short-memory-benchmark -- [options]\n\
         \n\
         Options:\n\
           --baseline <METHOD>      b0, b1, b2, b3, s, or all for scaling\n\
           --tail-k <N>             B1 entry limit (default: 128)\n\
           --turns <N>              Synthetic turn count\n\
           --tools-per-turn <N>     Tool calls per turn\n\
           --chunks-per-tool <N>    Command-output chunks per tool\n\
           --payload-chars <N>      Generated payload characters\n\
           --seed <N>               Deterministic generator seed\n\
           --trace-id <ID>          Stable trace identifier\n\
           --failure-every <N|none> Make every Nth tool result an error\n\
           --fork-after-turn <N|none> Continue in a child Session after turn N\n\
           --evidence-horizon-turns <N> Gold evidence recency horizon\n\
           --scale-events <CSV>     Release-mode target event counts\n\
           --warmup <N>             Scaling warm-up iterations\n\
           --iterations <N>         Scaling measured iterations\n\
           --max-key-batches <N>    Maximum admitted historical key batches\n\
           --max-key-bytes <N>      Maximum total admitted key-content bytes\n\
           --pretty                 Pretty-print the JSON report\n\
           --fail-on-gate           Exit non-zero if a correctness gate fails\n\
           --help                    Show this help"
    );
}
