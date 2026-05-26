use crate::runtime::{LocalAgentRuntime, RunRequest};
use crate::types::RunEvidenceSummary;
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::time::Instant;
use structure_local_core::structure_core_manifest;

#[derive(Debug, Clone, Default)]
pub struct LocalBenchmarkRequest {
    pub benchmark: Option<String>,
    pub max_cases: Option<usize>,
    pub output_dir: Option<PathBuf>,
    pub workspace_id: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LocalBenchmarkRunResult {
    pub report: LocalBenchmarkReport,
    pub json_path: String,
    pub markdown_path: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct LocalBenchmarkEvidence {
    pub schema_version: String,
    pub report_path: String,
    pub report: LocalBenchmarkReport,
    pub run_evidence: Vec<RunEvidenceSummary>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LocalBenchmarkReport {
    pub report_schema_version: String,
    pub benchmark: String,
    pub adapter: String,
    pub n_cases: usize,
    pub overall_score: f64,
    pub per_ability_score: BTreeMap<String, f64>,
    pub mean_cost: LocalBenchmarkCost,
    pub total_cost: LocalBenchmarkCost,
    pub per_case: Vec<LocalBenchmarkPerCase>,
    pub per_case_diagnostics: Vec<LocalBenchmarkCaseDiagnostic>,
    pub evidence_summary: BTreeMap<String, usize>,
    pub diagnostic_summary: BTreeMap<String, serde_json::Value>,
    pub total_events: usize,
    pub total_tool_calls: usize,
    pub total_latency_seconds: f64,
    pub generated_at_ms: i64,
    pub cases: Vec<LocalBenchmarkCaseResult>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct LocalBenchmarkCost {
    pub tokens_prompt: u64,
    pub tokens_completion: u64,
    pub tokens_cached: u64,
    pub cache_creation_tokens: u64,
    pub cache_read_tokens: u64,
    pub steps: u64,
    pub tool_calls: u64,
    pub latency_seconds: f64,
    pub usd_cost: f64,
}

impl LocalBenchmarkCost {
    fn tokens_total(&self) -> u64 {
        self.tokens_prompt + self.tokens_completion
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LocalBenchmarkPerCase {
    pub task_id: String,
    pub score: f64,
    pub tokens_prompt: u64,
    pub tokens_completion: u64,
    pub tokens_total: u64,
    pub tool_calls: u64,
    pub steps: u64,
    pub latency_seconds: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LocalBenchmarkCaseDiagnostic {
    pub task_id: String,
    pub score: f64,
    pub adapter: String,
    pub run_id: String,
    pub run_status: String,
    pub event_count: usize,
    pub tool_calls: usize,
    pub latency_seconds: f64,
    pub artifact_path: String,
    pub evidence_status: String,
    pub expected_terms: Vec<String>,
    pub matched_terms: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LocalBenchmarkCaseResult {
    pub task_id: String,
    pub prompt: String,
    pub response: String,
    pub score: f64,
    pub expected_terms: Vec<String>,
    pub matched_terms: Vec<String>,
    pub run_id: String,
    pub event_count: usize,
    pub tool_calls: usize,
    pub latency_seconds: f64,
    pub artifact_path: String,
}

#[derive(Debug, Clone)]
struct LocalBenchmarkCase {
    task_id: &'static str,
    prompt: &'static str,
    expected_terms: &'static [&'static str],
}

impl LocalAgentRuntime {
    pub fn run_local_benchmark(
        &self,
        request: LocalBenchmarkRequest,
    ) -> Result<LocalBenchmarkRunResult, String> {
        let benchmark = request
            .benchmark
            .unwrap_or_else(|| "local-agent-smoke".to_string());
        let mut cases = benchmark_cases(&benchmark)?;
        if let Some(max_cases) = request.max_cases {
            cases.truncate(max_cases);
        }
        if cases.is_empty() {
            return Err("local benchmark selected zero cases".to_string());
        }

        let mut results = Vec::new();
        for case in cases {
            let started = Instant::now();
            let result = self.run_prompt(RunRequest {
                prompt: case.prompt.to_string(),
                workspace_id: request.workspace_id.clone(),
                mode: Some(crate::types::LocalAgentMode::Benchmark),
            })?;
            let latency_seconds = started.elapsed().as_secs_f64();
            let response_lower = result.final_response.to_lowercase();
            let matched_terms = case
                .expected_terms
                .iter()
                .filter(|term| response_lower.contains(&term.to_lowercase()))
                .map(|term| (*term).to_string())
                .collect::<Vec<_>>();
            let score = matched_terms.len() as f64 / case.expected_terms.len().max(1) as f64;
            let tool_calls = result
                .events
                .iter()
                .filter(|event| event.kind == "tool_call_completed")
                .count();

            results.push(LocalBenchmarkCaseResult {
                task_id: case.task_id.to_string(),
                prompt: case.prompt.to_string(),
                response: result.final_response,
                score,
                expected_terms: case
                    .expected_terms
                    .iter()
                    .map(|term| (*term).to_string())
                    .collect(),
                matched_terms,
                run_id: result.run.run_id,
                event_count: result.events.len(),
                tool_calls,
                latency_seconds,
                artifact_path: result.artifact_path,
            });
        }

        let n_cases = results.len();
        let overall_score = results.iter().map(|case| case.score).sum::<f64>() / n_cases as f64;
        let total_events = results.iter().map(|case| case.event_count).sum();
        let total_tool_calls = results.iter().map(|case| case.tool_calls).sum();
        let total_latency_seconds = results.iter().map(|case| case.latency_seconds).sum();
        let total_cost = LocalBenchmarkCost {
            steps: total_events as u64,
            tool_calls: total_tool_calls as u64,
            latency_seconds: total_latency_seconds,
            ..LocalBenchmarkCost::default()
        };
        let mean_cost = LocalBenchmarkCost {
            steps: total_cost.steps / n_cases as u64,
            tool_calls: total_cost.tool_calls / n_cases as u64,
            latency_seconds: total_latency_seconds / n_cases as f64,
            ..LocalBenchmarkCost::default()
        };
        let per_case = results
            .iter()
            .map(|case| LocalBenchmarkPerCase {
                task_id: case.task_id.clone(),
                score: round4(case.score),
                tokens_prompt: 0,
                tokens_completion: 0,
                tokens_total: 0,
                tool_calls: case.tool_calls as u64,
                steps: case.event_count as u64,
                latency_seconds: round3(case.latency_seconds),
            })
            .collect::<Vec<_>>();
        let per_case_diagnostics = results
            .iter()
            .map(|case| LocalBenchmarkCaseDiagnostic {
                task_id: case.task_id.clone(),
                score: round4(case.score),
                adapter: "structure-local-runtime".to_string(),
                run_id: case.run_id.clone(),
                run_status: "finished".to_string(),
                event_count: case.event_count,
                tool_calls: case.tool_calls,
                latency_seconds: round3(case.latency_seconds),
                artifact_path: case.artifact_path.clone(),
                evidence_status: if case.score >= 1.0 { "pass" } else { "unknown" }.to_string(),
                expected_terms: case.expected_terms.clone(),
                matched_terms: case.matched_terms.clone(),
            })
            .collect::<Vec<_>>();
        let mut evidence_summary = BTreeMap::new();
        evidence_summary.insert(
            "pass".to_string(),
            results.iter().filter(|case| case.score >= 1.0).count(),
        );
        let unknown = results.iter().filter(|case| case.score < 1.0).count();
        if unknown > 0 {
            evidence_summary.insert("unknown".to_string(), unknown);
        }
        let mut per_ability_score = BTreeMap::new();
        per_ability_score.insert("_all".to_string(), round4(overall_score));
        let mut diagnostic_summary = BTreeMap::new();
        diagnostic_summary.insert(
            "mean_events".to_string(),
            serde_json::json!(round3(total_events as f64 / n_cases as f64)),
        );
        diagnostic_summary.insert(
            "mean_tool_calls".to_string(),
            serde_json::json!(round3(total_tool_calls as f64 / n_cases as f64)),
        );
        diagnostic_summary.insert(
            "report_family".to_string(),
            serde_json::json!("BenchmarkReport"),
        );
        let report_schema_version = structure_core_manifest()
            .map(|manifest| manifest.benchmark_report_schema.id)
            .unwrap_or_else(|_| "benchmark-report-v1".to_string());
        let report = LocalBenchmarkReport {
            report_schema_version,
            benchmark,
            adapter: "structure-local-runtime".to_string(),
            n_cases,
            overall_score: round4(overall_score),
            per_ability_score,
            mean_cost,
            total_cost,
            per_case,
            per_case_diagnostics,
            evidence_summary,
            diagnostic_summary,
            total_events,
            total_tool_calls,
            total_latency_seconds: round3(total_latency_seconds),
            generated_at_ms: crate::store::now_ms(),
            cases: results,
        };

        let output_dir = request.output_dir.unwrap_or_else(|| {
            self.repo_root()
                .join("benchmark_runs")
                .join(format!("local-agent-{}", report.generated_at_ms))
        });
        fs::create_dir_all(&output_dir)
            .map_err(|err| format!("failed to create benchmark output directory: {err}"))?;
        let json_path = output_dir.join("summary.json");
        let markdown_path = output_dir.join("summary.md");
        fs::write(
            &json_path,
            serde_json::to_string_pretty(&report)
                .map_err(|err| format!("failed to render benchmark json: {err}"))?,
        )
        .map_err(|err| format!("failed to write benchmark json: {err}"))?;
        fs::write(&markdown_path, render_markdown(&report))
            .map_err(|err| format!("failed to write benchmark markdown: {err}"))?;

        Ok(LocalBenchmarkRunResult {
            report,
            json_path: json_path.display().to_string(),
            markdown_path: markdown_path.display().to_string(),
        })
    }

    pub fn local_benchmark_evidence(
        &self,
        path: impl AsRef<Path>,
    ) -> Result<LocalBenchmarkEvidence, String> {
        let report_path = resolve_repo_file(self.repo_root(), path)?;
        let report_text = fs::read_to_string(&report_path)
            .map_err(|err| format!("failed to read benchmark report: {err}"))?;
        let report = serde_json::from_str::<LocalBenchmarkReport>(&report_text)
            .map_err(|err| format!("failed to parse benchmark report json: {err}"))?;
        let run_evidence = report
            .cases
            .iter()
            .filter_map(|case| self.run_evidence_summary(&case.run_id).ok())
            .collect::<Vec<_>>();

        Ok(LocalBenchmarkEvidence {
            schema_version: "local-benchmark-evidence-v1".to_string(),
            report_path: report_path.display().to_string(),
            report,
            run_evidence,
        })
    }
}

fn resolve_repo_file(repo_root: &Path, path: impl AsRef<Path>) -> Result<PathBuf, String> {
    let repo_root = repo_root
        .canonicalize()
        .map_err(|err| format!("failed to canonicalize repo root: {err}"))?;
    let requested = path.as_ref();
    let full_path = if requested.is_absolute() {
        requested.to_path_buf()
    } else {
        repo_root.join(requested)
    };
    let canonical = full_path
        .canonicalize()
        .map_err(|err| format!("failed to resolve benchmark report: {err}"))?;
    if !canonical.starts_with(&repo_root) {
        return Err("benchmark report is outside the Structure repository".to_string());
    }
    Ok(canonical)
}

fn benchmark_cases(name: &str) -> Result<Vec<LocalBenchmarkCase>, String> {
    match name {
        "local-agent-smoke" => Ok(vec![
            LocalBenchmarkCase {
                task_id: "workspace-inventory",
                prompt: "Inspect the local workspace and identify the core repository structure.",
                expected_terms: &["workspace", "Tool Evidence", "repo_root"],
            },
            LocalBenchmarkCase {
                task_id: "runtime-events",
                prompt: "Explain the local agent event loop and mention tool evidence.",
                expected_terms: &["event loop", "Tool Evidence", "tool results"],
            },
        ]),
        "local-knowledge-smoke" => Ok(vec![LocalBenchmarkCase {
            task_id: "knowledge-grounding",
            prompt: "Use registered knowledge sources to summarize local context.",
            expected_terms: &["Knowledge sources", "Retrieved Context", "Tool Evidence"],
        }]),
        other => Err(format!("unsupported local benchmark: {other}")),
    }
}

fn render_markdown(report: &LocalBenchmarkReport) -> String {
    let mut lines = vec![
        format!("# {}", report.benchmark),
        String::new(),
        "| Cases | Score | Events | Tool calls | Latency (s) |".to_string(),
        "|---:|---:|---:|---:|---:|".to_string(),
        format!(
            "| {} | {:.4} | {} | {} | {:.3} |",
            report.n_cases,
            report.overall_score,
            report.total_events,
            report.total_tool_calls,
            report.total_latency_seconds,
        ),
        String::new(),
        "## Cost".to_string(),
        String::new(),
        "| Mean tokens | Total tokens | Mean tool calls | Total tool calls | Mean latency (s) |"
            .to_string(),
        "|---:|---:|---:|---:|---:|".to_string(),
        format!(
            "| {} | {} | {} | {} | {:.3} |",
            report.mean_cost.tokens_total(),
            report.total_cost.tokens_total(),
            report.mean_cost.tool_calls,
            report.total_cost.tool_calls,
            report.mean_cost.latency_seconds,
        ),
        String::new(),
        "## Cases".to_string(),
    ];

    for case in &report.cases {
        lines.extend([
            String::new(),
            format!("### {}", case.task_id),
            String::new(),
            format!("- Run: `{}`", case.run_id),
            format!("- Score: `{:.4}`", case.score),
            format!("- Tool calls: `{}`", case.tool_calls),
            format!("- Artifact: `{}`", case.artifact_path),
        ]);
    }

    lines.push(String::new());
    lines.join("\n")
}

fn round3(value: f64) -> f64 {
    (value * 1000.0).round() / 1000.0
}

fn round4(value: f64) -> f64 {
    (value * 10000.0).round() / 10000.0
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::runtime::LocalAgentRuntime;
    use crate::test_env::OpenAiEnvGuard;
    use std::time::{SystemTime, UNIX_EPOCH};

    #[test]
    fn local_benchmark_writes_json_and_markdown_reports() {
        let _env = OpenAiEnvGuard::clear();
        let root = unique_repo("benchmark");
        let runtime = LocalAgentRuntime::open(&root).unwrap();
        let output_dir = root.join("benchmark_runs/local-test");

        let result = runtime
            .run_local_benchmark(LocalBenchmarkRequest {
                max_cases: Some(1),
                output_dir: Some(output_dir),
                ..LocalBenchmarkRequest::default()
            })
            .unwrap();

        assert_eq!(result.report.n_cases, 1);
        assert!(result.report.total_tool_calls >= 1);
        assert_eq!(result.report.report_schema_version, "benchmark-report-v1");
        assert_eq!(result.report.per_case.len(), 1);
        assert_eq!(result.report.per_case_diagnostics.len(), 1);
        assert!(result.report.evidence_summary.contains_key("pass"));
        assert!(result.report.total_cost.tool_calls >= 1);
        assert!(PathBuf::from(&result.json_path).exists());
        assert!(PathBuf::from(&result.markdown_path).exists());

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn local_benchmark_evidence_relinks_report_to_run_evidence() {
        let _env = OpenAiEnvGuard::clear();
        let root = unique_repo("benchmark-evidence");
        let runtime = LocalAgentRuntime::open(&root).unwrap();
        let output_dir = root.join("benchmark_runs/local-evidence");
        let result = runtime
            .run_local_benchmark(LocalBenchmarkRequest {
                max_cases: Some(1),
                output_dir: Some(output_dir),
                ..LocalBenchmarkRequest::default()
            })
            .unwrap();

        let relative_report = Path::new(&result.json_path)
            .strip_prefix(&root)
            .unwrap()
            .to_path_buf();
        let evidence = runtime.local_benchmark_evidence(relative_report).unwrap();

        assert_eq!(evidence.schema_version, "local-benchmark-evidence-v1");
        assert_eq!(evidence.report.n_cases, 1);
        assert_eq!(evidence.run_evidence.len(), 1);
        assert_eq!(
            evidence.run_evidence[0].run.run_id,
            result.report.cases[0].run_id
        );

        fs::remove_dir_all(root).unwrap();
    }

    fn unique_repo(label: &str) -> PathBuf {
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let root = std::env::temp_dir().join(format!(
            "structure-local-runtime-{label}-{}-{nanos}",
            std::process::id()
        ));
        fs::create_dir_all(root.join("src/structure")).unwrap();
        fs::create_dir_all(root.join("frontend")).unwrap();
        fs::write(root.join("pyproject.toml"), "").unwrap();
        root
    }
}
