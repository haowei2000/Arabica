use crate::store::new_id;
use crate::types::{KnowledgeSource, LocalAgentMode, LocalToolCall, LocalToolResult, RunSummary};
use serde::{Deserialize, Serialize};
use std::env;
use std::path::PathBuf;
use std::time::Duration;

#[derive(Debug, Clone)]
pub struct ModelRequest {
    pub run: RunSummary,
    pub repo_root: PathBuf,
    pub knowledge: Vec<KnowledgeSource>,
    pub mode: LocalAgentMode,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ModelPlan {
    pub provider: String,
    pub tool_calls: Vec<LocalToolCall>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ModelOutput {
    pub provider: String,
    pub final_response: String,
}

pub trait LocalModelProvider {
    fn provider_id(&self) -> &'static str;
    fn plan(&self, request: &ModelRequest) -> Result<ModelPlan, String>;
    fn synthesize(
        &self,
        request: &ModelRequest,
        tool_results: &[LocalToolResult],
    ) -> Result<ModelOutput, String>;
}

#[derive(Debug, Default)]
pub struct DeterministicLocalModelProvider;

#[derive(Debug, Clone)]
pub struct EnvApiModelProvider {
    base_url: String,
    api_key: String,
    model: String,
}

impl EnvApiModelProvider {
    pub fn from_env() -> Result<Self, String> {
        let base_url =
            env::var("OPENAI__BASE_URL").map_err(|_| "OPENAI__BASE_URL is not set".to_string())?;
        let api_key =
            env::var("OPENAI__API_KEY").map_err(|_| "OPENAI__API_KEY is not set".to_string())?;
        let model =
            env::var("OPENAI__MODEL").map_err(|_| "OPENAI__MODEL is not set".to_string())?;
        if base_url.trim().is_empty() || api_key.trim().is_empty() || model.trim().is_empty() {
            return Err(
                "OPENAI__API_KEY, OPENAI__BASE_URL, and OPENAI__MODEL must not be empty"
                    .to_string(),
            );
        }
        Ok(Self {
            base_url,
            api_key,
            model,
        })
    }

    pub fn enabled_from_env() -> bool {
        env::var("OPENAI__API_KEY")
            .map(|value| !value.trim().is_empty())
            .unwrap_or(false)
    }
}

impl LocalModelProvider for DeterministicLocalModelProvider {
    fn provider_id(&self) -> &'static str {
        "local_deterministic"
    }

    fn plan(&self, request: &ModelRequest) -> Result<ModelPlan, String> {
        let mut tool_calls = vec![LocalToolCall {
            call_id: new_id("tool"),
            name: "list_workspace".to_string(),
            input: serde_json::json!({ "max_entries": 16 }),
        }];

        if matches!(request.mode, LocalAgentMode::CodeAgent) {
            let code_query = code_search_query(&request.run.prompt);
            tool_calls.push(LocalToolCall {
                call_id: new_id("tool"),
                name: "search_repo".to_string(),
                input: serde_json::json!({
                    "query": code_query,
                    "max_matches": 12,
                }),
            });
            tool_calls.push(LocalToolCall {
                call_id: new_id("tool"),
                name: "read_repo_file".to_string(),
                input: serde_json::json!({
                    "path": "core/structure_core.json",
                    "max_bytes": 8192,
                }),
            });
        }

        for source in request.knowledge.iter().take(3) {
            tool_calls.push(LocalToolCall {
                call_id: new_id("tool"),
                name: "read_knowledge_source".to_string(),
                input: serde_json::json!({
                    "source_id": source.source_id,
                    "path": source.path,
                    "max_bytes": 4096,
                }),
            });
        }

        Ok(ModelPlan {
            provider: self.provider_id().to_string(),
            tool_calls,
        })
    }

    fn synthesize(
        &self,
        request: &ModelRequest,
        tool_results: &[LocalToolResult],
    ) -> Result<ModelOutput, String> {
        let mut response = String::new();
        response.push_str("# Structure Local Agent Response\n\n");
        response.push_str("This response was produced by the embedded Structure event loop.\n\n");
        response.push_str(&format!("- Run: `{}`\n", request.run.run_id));
        response.push_str(&format!("- Workspace: `{}`\n", request.run.workspace_id));
        response.push_str(&format!("- Mode: `{}`\n", request.mode.as_str()));
        response.push_str(&format!(
            "- Repository: `{}`\n",
            request.repo_root.display()
        ));
        response.push_str(&format!(
            "- Knowledge sources: `{}`\n",
            request.knowledge.len()
        ));
        response.push_str(&format!("- Tool results: `{}`\n\n", tool_results.len()));

        response.push_str("## Prompt\n\n");
        response.push_str(&request.run.prompt);
        response.push_str("\n\n## Agent Loop\n\n");
        response.push_str("1. Open workspace and replay persisted context events.\n");
        response.push_str("2. Plan local tool calls through the shared Structure runtime.\n");
        response.push_str("3. Execute repository and knowledge tools inside the local process.\n");
        response.push_str("4. Persist the assistant response and evidence as immutable events.\n");
        response.push_str("\n\n## Retrieved Context\n\n");
        if request.knowledge.is_empty() {
            response.push_str("No knowledge sources are registered for this workspace yet.\n");
        } else {
            for source in &request.knowledge {
                response.push_str(&format!(
                    "- {} ({} bytes): `{}`\n",
                    source.title, source.size_bytes, source.path
                ));
            }
        }

        response.push_str("\n## Tool Evidence\n\n");
        for result in tool_results {
            response.push_str(&format!("### {} / `{}`\n\n", result.name, result.call_id));
            if result.success {
                response.push_str("```json\n");
                response.push_str(
                    &serde_json::to_string_pretty(&result.output)
                        .map_err(|err| format!("failed to render tool output: {err}"))?,
                );
                response.push_str("\n```\n\n");
            } else {
                response.push_str(result.error.as_deref().unwrap_or("tool failed"));
                response.push_str("\n\n");
            }
        }

        Ok(ModelOutput {
            provider: self.provider_id().to_string(),
            final_response: response,
        })
    }
}

impl LocalModelProvider for EnvApiModelProvider {
    fn provider_id(&self) -> &'static str {
        "local_env_api"
    }

    fn plan(&self, request: &ModelRequest) -> Result<ModelPlan, String> {
        DeterministicLocalModelProvider.plan(request)
    }

    fn synthesize(
        &self,
        request: &ModelRequest,
        tool_results: &[LocalToolResult],
    ) -> Result<ModelOutput, String> {
        let client = reqwest::blocking::Client::builder()
            .timeout(Duration::from_secs(90))
            .build()
            .map_err(|err| format!("failed to build API client: {err}"))?;
        let endpoint = format!("{}/chat/completions", self.base_url.trim_end_matches('/'));
        let prompt = render_api_prompt(request, tool_results)?;
        let response = client
            .post(endpoint)
            .bearer_auth(&self.api_key)
            .json(&serde_json::json!({
                "model": self.model,
                "messages": [
                    {
                        "role": "system",
                        "content": "You are Structure local code-agent. Use the provided event-loop evidence and tool outputs. Return a concise, reviewable answer. Do not claim repository files were modified."
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                "temperature": 0.2,
                "max_tokens": 900
            }))
            .send()
            .map_err(|err| format!("local LLM API request failed: {err}"))?;
        let status = response.status();
        let body = response
            .text()
            .map_err(|err| format!("failed to read API response body: {err}"))?;
        if !status.is_success() {
            return Err(format!(
                "local LLM API returned status {status}: {}",
                body.chars().take(500).collect::<String>()
            ));
        }
        let value = serde_json::from_str::<serde_json::Value>(&body)
            .map_err(|err| format!("failed to parse API response JSON: {err}"))?;
        let content = value
            .get("choices")
            .and_then(|choices| choices.as_array())
            .and_then(|choices| choices.first())
            .and_then(|choice| choice.get("message"))
            .and_then(|message| message.get("content"))
            .and_then(|content| content.as_str())
            .ok_or_else(|| "API response did not include choices[0].message.content".to_string())?;

        let mut final_response = String::new();
        final_response.push_str("# Structure Local Agent Response\n\n");
        final_response.push_str("This response was produced by the embedded Structure event loop with the environment API model.\n\n");
        final_response.push_str(&format!("- Provider: `{}`\n", self.provider_id()));
        final_response.push_str(&format!("- Model: `{}`\n", self.model));
        final_response.push_str(&format!("- Run: `{}`\n", request.run.run_id));
        final_response.push_str(&format!("- Workspace: `{}`\n", request.run.workspace_id));
        final_response.push_str(&format!("- Mode: `{}`\n\n", request.mode.as_str()));
        final_response.push_str(content.trim());
        final_response.push_str("\n\n## Tool Evidence Summary\n\n");
        for result in tool_results {
            final_response.push_str(&format!(
                "- {} / `{}`: {}\n",
                result.name,
                result.call_id,
                if result.success { "ok" } else { "failed" }
            ));
        }

        Ok(ModelOutput {
            provider: self.provider_id().to_string(),
            final_response,
        })
    }
}

pub fn selected_synthesis_provider() -> Result<Box<dyn LocalModelProvider>, String> {
    if EnvApiModelProvider::enabled_from_env() {
        return Ok(Box::new(EnvApiModelProvider::from_env()?));
    }
    Ok(Box::new(DeterministicLocalModelProvider))
}

fn render_api_prompt(
    request: &ModelRequest,
    tool_results: &[LocalToolResult],
) -> Result<String, String> {
    let evidence = serde_json::to_string_pretty(tool_results)
        .map_err(|err| format!("failed to render tool evidence: {err}"))?;
    Ok(format!(
        "Prompt:\n{prompt}\n\nRun: {run_id}\nWorkspace: {workspace_id}\nMode: {mode}\nRepo: {repo}\n\nTool evidence JSON:\n{evidence}",
        prompt = request.run.prompt,
        run_id = request.run.run_id,
        workspace_id = request.run.workspace_id,
        mode = request.mode.as_str(),
        repo = request.repo_root.display(),
        evidence = evidence.chars().take(20_000).collect::<String>()
    ))
}

fn code_search_query(prompt: &str) -> String {
    let lowered = prompt.to_lowercase();
    if lowered.contains("runtime") || lowered.contains("event loop") || lowered.contains("事件") {
        "LocalAgentRuntime".to_string()
    } else if lowered.contains("cli") || lowered.contains("tui") {
        "RunRequest".to_string()
    } else if lowered.contains("app") || lowered.contains("desktop") || lowered.contains("桌面") {
        "local_agent_run".to_string()
    } else if lowered.contains("workspace") || lowered.contains("工作空间") {
        "workspace".to_string()
    } else {
        prompt
            .split(|character: char| !character.is_alphanumeric() && character != '_')
            .find(|word| word.chars().count() >= 5)
            .unwrap_or("Structure")
            .to_string()
    }
}
