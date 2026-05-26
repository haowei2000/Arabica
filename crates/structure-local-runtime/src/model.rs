use crate::store::new_id;
use crate::types::{
    AgentInstruction, ChatTurn, KnowledgeSource, LocalAgentMode, LocalLlmDiagnostic, LocalToolCall,
    LocalToolResult, RunSummary, WorktreeSnapshot,
};
use serde::{Deserialize, Serialize};
use std::env;
use std::path::PathBuf;
use std::time::{Duration, Instant};

#[derive(Debug, Clone)]
pub struct ModelRequest {
    pub run: RunSummary,
    pub repo_root: PathBuf,
    pub agent_instructions: Vec<AgentInstruction>,
    pub worktree: WorktreeSnapshot,
    pub knowledge: Vec<KnowledgeSource>,
    pub recent_turns: Vec<ChatTurn>,
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
    fn plan_next(
        &self,
        request: &ModelRequest,
        tool_results: &[LocalToolResult],
    ) -> Result<ModelPlan, String> {
        if tool_results.is_empty() {
            return self.plan(request);
        }
        Ok(ModelPlan {
            provider: self.provider_id().to_string(),
            tool_calls: Vec::new(),
        })
    }
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

    fn chat_completion(&self, payload: serde_json::Value) -> Result<serde_json::Value, String> {
        let client = reqwest::blocking::Client::builder()
            .timeout(Duration::from_secs(90))
            .build()
            .map_err(|err| format!("failed to build API client: {err}"))?;
        let endpoint = format!("{}/chat/completions", self.base_url.trim_end_matches('/'));
        let response = client
            .post(endpoint)
            .bearer_auth(&self.api_key)
            .json(&payload)
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
        serde_json::from_str::<serde_json::Value>(&body)
            .map_err(|err| format!("failed to parse API response JSON: {err}"))
    }

    pub fn diagnose_from_env() -> LocalLlmDiagnostic {
        let started = Instant::now();
        let provider = "local_env_api".to_string();
        let configured = env::var("OPENAI__API_KEY")
            .map(|value| !value.trim().is_empty())
            .unwrap_or(false)
            && env::var("OPENAI__BASE_URL")
                .map(|value| !value.trim().is_empty())
                .unwrap_or(false)
            && env::var("OPENAI__MODEL")
                .map(|value| !value.trim().is_empty())
                .unwrap_or(false);
        let model = env::var("OPENAI__MODEL")
            .ok()
            .filter(|value| !value.trim().is_empty());
        let endpoint = env::var("OPENAI__BASE_URL")
            .ok()
            .filter(|value| !value.trim().is_empty())
            .map(|base_url| format!("{}/chat/completions", base_url.trim_end_matches('/')));
        let provider_instance = match Self::from_env() {
            Ok(provider_instance) => provider_instance,
            Err(error) => {
                return LocalLlmDiagnostic {
                    provider,
                    configured,
                    ok: false,
                    model,
                    endpoint,
                    elapsed_ms: started.elapsed().as_millis(),
                    response_preview: None,
                    error: Some(error),
                };
            }
        };
        let result = provider_instance.chat_completion(serde_json::json!({
            "model": provider_instance.model,
            "messages": [
                {
                    "role": "system",
                    "content": "You are a Structure local runtime health check. Reply with a short plain-text acknowledgement."
                },
                {
                    "role": "user",
                    "content": "Reply with: structure-local-ok"
                }
            ],
            "temperature": 0.0,
            "max_tokens": 16
        }));
        match result {
            Ok(value) => {
                let response_preview = value
                    .get("choices")
                    .and_then(|choices| choices.as_array())
                    .and_then(|choices| choices.first())
                    .and_then(|choice| choice.get("message"))
                    .and_then(|message| message.get("content"))
                    .and_then(|content| content.as_str())
                    .map(|content| content.chars().take(240).collect::<String>());
                LocalLlmDiagnostic {
                    provider,
                    configured,
                    ok: response_preview.is_some(),
                    model,
                    endpoint,
                    elapsed_ms: started.elapsed().as_millis(),
                    response_preview,
                    error: None,
                }
            }
            Err(error) => LocalLlmDiagnostic {
                provider,
                configured,
                ok: false,
                model,
                endpoint,
                elapsed_ms: started.elapsed().as_millis(),
                response_preview: None,
                error: Some(error),
            },
        }
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
            "- Agent instructions: `{}`\n",
            request.agent_instructions.len()
        ));
        response.push_str(&format!(
            "- Worktree: `{}` / {} changed files\n",
            if request.worktree.clean {
                "clean"
            } else {
                "dirty"
            },
            request.worktree.changed_files.len()
        ));
        response.push_str(&format!(
            "- Knowledge sources: `{}`\n",
            request.knowledge.len()
        ));
        response.push_str(&format!(
            "- Replayed chat turns: `{}`\n",
            request.recent_turns.len()
        ));
        response.push_str(&format!("- Tool results: `{}`\n\n", tool_results.len()));

        response.push_str("## Prompt\n\n");
        response.push_str(&request.run.prompt);
        response.push_str("\n\n## Agent Loop\n\n");
        response.push_str("1. Open workspace and replay persisted context events.\n");
        response.push_str("2. Plan local tool calls through the shared Structure runtime.\n");
        response.push_str("3. Execute repository and knowledge tools inside the local process.\n");
        response.push_str("4. Persist the assistant response and evidence as immutable events.\n");
        response.push_str("\n\n## Agent Instructions\n\n");
        if request.agent_instructions.is_empty() {
            response.push_str("No AGENTS.md instruction file was found for this workspace.\n");
        } else {
            for instruction in &request.agent_instructions {
                response.push_str(&format!(
                    "### {}\n\nPath: `{}` / {} bytes / truncated: {}\n\n{}\n\n",
                    instruction.title,
                    instruction.path,
                    instruction.size_bytes,
                    instruction.truncated,
                    instruction.content_preview
                ));
            }
        }

        response.push_str("\n## Worktree\n\n");
        response.push_str(&render_worktree_for_response(&request.worktree));
        response.push('\n');

        response.push_str("\n## Retrieved Context\n\n");
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

        response.push_str("\n## Replayed Conversation\n\n");
        if request.recent_turns.is_empty() {
            response.push_str("No previous chat turns were replayed for this workspace.\n");
        } else {
            for turn in request.recent_turns.iter().rev() {
                response.push_str(&format!(
                    "- User: {}\n  Assistant: {}\n",
                    summarize_line(&turn.user_message, 240),
                    summarize_line(
                        turn.assistant_message
                            .as_deref()
                            .unwrap_or("<no assistant response>"),
                        240
                    )
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
        self.plan_next(request, &[])
    }

    fn plan_next(
        &self,
        request: &ModelRequest,
        tool_results: &[LocalToolResult],
    ) -> Result<ModelPlan, String> {
        let response = self.chat_completion(serde_json::json!({
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": "You are the Structure local agent planner. Choose local tools that should run before the final answer. Use only the provided tools. Prefer read-only inspection. Do not modify files."
                },
                {
                    "role": "user",
                    "content": render_planning_prompt(request, tool_results)
                }
            ],
            "tools": local_tool_schemas(request),
            "tool_choice": "auto",
            "temperature": 0.0,
            "max_tokens": 300
        }))?;
        let tool_calls = parse_api_tool_calls(&response)?;
        if tool_calls.is_empty() && tool_results.is_empty() {
            let mut fallback = DeterministicLocalModelProvider.plan(request)?;
            fallback.provider = "local_env_api_fallback".to_string();
            return Ok(fallback);
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
        let prompt = render_api_prompt(request, tool_results)?;
        let value = self.chat_completion(serde_json::json!({
                "model": self.model,
                "messages": [
                    {
                        "role": "system",
                        "content": "You are Structure local code-agent. Use the provided event-loop evidence and tool outputs. Return a concise, reviewable answer. Do not claim repository files were modified. When the user asks for a code change, include at most one fenced ```diff unified diff for a repo-relative target so the local runtime can persist it as a reviewed proposal artifact."
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                "temperature": 0.2,
                "max_tokens": 900
            }))?;
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
        final_response.push_str(&format!(
            "- Agent instructions: `{}`\n",
            request.agent_instructions.len()
        ));
        final_response.push_str(&format!(
            "- Worktree: `{}` / {} changed files\n",
            if request.worktree.clean {
                "clean"
            } else {
                "dirty"
            },
            request.worktree.changed_files.len()
        ));
        final_response.push_str(&format!(
            "- Replayed chat turns: `{}`\n",
            request.recent_turns.len()
        ));
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

pub fn selected_planning_provider() -> Result<Box<dyn LocalModelProvider>, String> {
    if EnvApiModelProvider::enabled_from_env() {
        return Ok(Box::new(EnvApiModelProvider::from_env()?));
    }
    Ok(Box::new(DeterministicLocalModelProvider))
}

fn render_planning_prompt(request: &ModelRequest, tool_results: &[LocalToolResult]) -> String {
    let knowledge = if request.knowledge.is_empty() {
        "No knowledge sources registered.".to_string()
    } else {
        request
            .knowledge
            .iter()
            .map(|source| {
                format!(
                    "- source_id={} path={} title={}",
                    source.source_id, source.path, source.title
                )
            })
            .collect::<Vec<_>>()
            .join("\n")
    };
    let evidence = if tool_results.is_empty() {
        "No tool results yet.".to_string()
    } else {
        serde_json::to_string_pretty(tool_results)
            .unwrap_or_else(|_| "Tool results could not be rendered.".to_string())
            .chars()
            .take(16_000)
            .collect::<String>()
    };
    format!(
        "Prompt:\n{prompt}\n\nMode: {mode}\nRepo: {repo}\n\nAgent instructions:\n{instructions}\n\nWorktree snapshot:\n{worktree}\n\nRecent workspace conversation:\n{recent_turns}\n\nKnowledge sources:\n{knowledge}\n\nTool results so far:\n{evidence}\n\nReturn additional tool calls only if more local inspection is needed. If the current evidence is enough, return no tool calls.",
        prompt = request.run.prompt,
        mode = request.mode.as_str(),
        repo = request.repo_root.display(),
        instructions = render_agent_instructions_for_api(&request.agent_instructions),
        worktree = render_worktree_for_api(&request.worktree),
        recent_turns = render_recent_turns_for_api(&request.recent_turns),
    )
}

fn local_tool_schemas(request: &ModelRequest) -> Vec<serde_json::Value> {
    let mut tools = vec![
        serde_json::json!({
            "type": "function",
            "function": {
                "name": "list_workspace",
                "description": "List non-sensitive top-level repository entries.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "max_entries": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 64
                        }
                    }
                }
            }
        }),
        serde_json::json!({
            "type": "function",
            "function": {
                "name": "search_repo",
                "description": "Search repository text for a literal query.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": { "type": "string" },
                        "max_matches": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 40
                        }
                    },
                    "required": ["query"]
                }
            }
        }),
        serde_json::json!({
            "type": "function",
            "function": {
                "name": "read_repo_file",
                "description": "Read a safe repository-relative text file.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": { "type": "string" },
                        "max_bytes": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 131072
                        }
                    },
                    "required": ["path"]
                }
            }
        }),
        serde_json::json!({
            "type": "function",
            "function": {
                "name": "run_local_command",
                "description": "Run an allowlisted local verification or search command without a shell. Use for tests, type checks, lint checks, git inspection, and ripgrep evidence.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "argv": {
                            "type": "array",
                            "items": { "type": "string" },
                            "description": "Command argv. Allowed examples: cargo check --workspace, cargo test --workspace, cargo clippy --workspace --all-targets -- -D warnings, uv run pytest tests/unit -q, uv run ruff check src tests, npm run build, npm run desktop:build, git status --short, git diff --stat, rg pattern path."
                        },
                        "cwd": {
                            "type": "string",
                            "description": "Repository-relative working directory."
                        },
                        "timeout_ms": {
                            "type": "integer",
                            "minimum": 1000,
                            "maximum": 120000
                        },
                        "max_output_chars": {
                            "type": "integer",
                            "minimum": 1000,
                            "maximum": 40000
                        }
                    },
                    "required": ["argv"]
                }
            }
        }),
    ];
    if !request.knowledge.is_empty() {
        tools.push(serde_json::json!({
            "type": "function",
            "function": {
                "name": "read_knowledge_source",
                "description": "Read a registered workspace knowledge source by path.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "source_id": { "type": "string" },
                        "path": { "type": "string" },
                        "max_bytes": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 65536
                        }
                    },
                    "required": ["source_id", "path"]
                }
            }
        }));
    }
    tools
}

fn parse_api_tool_calls(value: &serde_json::Value) -> Result<Vec<LocalToolCall>, String> {
    let Some(tool_calls) = value
        .get("choices")
        .and_then(|choices| choices.as_array())
        .and_then(|choices| choices.first())
        .and_then(|choice| choice.get("message"))
        .and_then(|message| message.get("tool_calls"))
        .and_then(|tool_calls| tool_calls.as_array())
    else {
        return Ok(Vec::new());
    };

    let mut calls = Vec::new();
    for tool_call in tool_calls {
        let Some(function) = tool_call.get("function") else {
            continue;
        };
        let Some(name) = function.get("name").and_then(|value| value.as_str()) else {
            continue;
        };
        if !matches!(
            name,
            "list_workspace"
                | "search_repo"
                | "read_repo_file"
                | "read_knowledge_source"
                | "run_local_command"
        ) {
            continue;
        }
        let input = function
            .get("arguments")
            .and_then(|value| value.as_str())
            .filter(|arguments| !arguments.trim().is_empty())
            .map(
                |arguments| match serde_json::from_str::<serde_json::Value>(arguments) {
                    Ok(input) => input,
                    Err(error) => serde_json::json!({
                        "_invalid_tool_arguments": arguments,
                        "_parse_error": error.to_string(),
                    }),
                },
            )
            .unwrap_or_else(|| serde_json::json!({}));
        calls.push(LocalToolCall {
            call_id: tool_call
                .get("id")
                .and_then(|value| value.as_str())
                .map(str::to_string)
                .unwrap_or_else(|| new_id("tool")),
            name: name.to_string(),
            input,
        });
    }
    Ok(calls)
}

fn render_api_prompt(
    request: &ModelRequest,
    tool_results: &[LocalToolResult],
) -> Result<String, String> {
    let evidence = serde_json::to_string_pretty(tool_results)
        .map_err(|err| format!("failed to render tool evidence: {err}"))?;
    Ok(format!(
        "Prompt:\n{prompt}\n\nRun: {run_id}\nWorkspace: {workspace_id}\nMode: {mode}\nRepo: {repo}\n\nAgent instructions:\n{instructions}\n\nWorktree snapshot:\n{worktree}\n\nRecent workspace conversation:\n{recent_turns}\n\nTool evidence JSON:\n{evidence}",
        prompt = request.run.prompt,
        run_id = request.run.run_id,
        workspace_id = request.run.workspace_id,
        mode = request.mode.as_str(),
        repo = request.repo_root.display(),
        instructions = render_agent_instructions_for_api(&request.agent_instructions),
        worktree = render_worktree_for_api(&request.worktree),
        recent_turns = render_recent_turns_for_api(&request.recent_turns),
        evidence = evidence.chars().take(20_000).collect::<String>()
    ))
}

fn render_worktree_for_response(worktree: &WorktreeSnapshot) -> String {
    if !worktree.available {
        return format!(
            "Worktree status unavailable: {}",
            worktree.error.as_deref().unwrap_or("unknown")
        );
    }
    let mut text = String::new();
    text.push_str(&format!(
        "Branch: {}\n",
        worktree.branch.as_deref().unwrap_or("unknown")
    ));
    text.push_str(&format!("Clean: {}\n", worktree.clean));
    if worktree.changed_files.is_empty() {
        text.push_str("No changed files were reported.\n");
    } else {
        text.push_str("Changed files:\n");
        for change in worktree.changed_files.iter().take(24) {
            text.push_str(&format!("- {} {}\n", change.status, change.path));
        }
    }
    text
}

fn render_worktree_for_api(worktree: &WorktreeSnapshot) -> String {
    render_worktree_for_response(worktree)
        .chars()
        .take(4_000)
        .collect()
}

fn render_agent_instructions_for_api(instructions: &[AgentInstruction]) -> String {
    if instructions.is_empty() {
        return "No AGENTS.md instruction file found.".to_string();
    }
    instructions
        .iter()
        .map(|instruction| {
            format!(
                "File: {}\nTitle: {}\nTruncated: {}\n{}",
                instruction.path,
                instruction.title,
                instruction.truncated,
                summarize_line(&instruction.content_preview, 4_000)
            )
        })
        .collect::<Vec<_>>()
        .join("\n\n")
}

fn render_recent_turns_for_api(turns: &[ChatTurn]) -> String {
    if turns.is_empty() {
        return "No previous turns replayed.".to_string();
    }
    turns
        .iter()
        .rev()
        .map(|turn| {
            format!(
                "User: {}\nAssistant: {}",
                summarize_line(&turn.user_message, 500),
                summarize_line(
                    turn.assistant_message
                        .as_deref()
                        .unwrap_or("<no assistant response>"),
                    500
                )
            )
        })
        .collect::<Vec<_>>()
        .join("\n\n")
}

fn summarize_line(value: &str, max_chars: usize) -> String {
    let clean = value
        .chars()
        .map(|character| {
            if character.is_control() && character != '\n' && character != '\t' {
                ' '
            } else {
                character
            }
        })
        .collect::<String>()
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ");
    if clean.chars().count() <= max_chars {
        return clean;
    }
    let mut summary = clean
        .chars()
        .take(max_chars.saturating_sub(3))
        .collect::<String>();
    summary.push_str("...");
    summary
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_openai_tool_calls_into_local_tool_plan() {
        let value = serde_json::json!({
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "id": "call_search",
                                "type": "function",
                                "function": {
                                    "name": "search_repo",
                                    "arguments": "{\"query\":\"LocalAgentRuntime\",\"max_matches\":3}"
                                }
                            },
                            {
                                "id": "call_unknown",
                                "type": "function",
                                "function": {
                                    "name": "write_file",
                                    "arguments": "{\"path\":\"src/lib.rs\"}"
                                }
                            },
                            {
                                "id": "call_check",
                                "type": "function",
                                "function": {
                                    "name": "run_local_command",
                                    "arguments": "{\"argv\":[\"cargo\",\"check\",\"--workspace\"],\"timeout_ms\":120000}"
                                }
                            }
                        ]
                    }
                }
            ]
        });

        let calls = parse_api_tool_calls(&value).unwrap();

        assert_eq!(calls.len(), 2);
        assert_eq!(calls[0].call_id, "call_search");
        assert_eq!(calls[0].name, "search_repo");
        assert_eq!(calls[0].input["query"], "LocalAgentRuntime");
        assert_eq!(calls[0].input["max_matches"], 3);
        assert_eq!(calls[1].call_id, "call_check");
        assert_eq!(calls[1].name, "run_local_command");
        assert_eq!(calls[1].input["argv"][0], "cargo");
    }

    #[test]
    fn malformed_openai_tool_arguments_become_auditable_tool_input() {
        let value = serde_json::json!({
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "id": "call_bad_read",
                                "type": "function",
                                "function": {
                                    "name": "read_repo_file",
                                    "arguments": "{\"path\":\"src/lib.rs\""
                                }
                            }
                        ]
                    }
                }
            ]
        });

        let calls = parse_api_tool_calls(&value).unwrap();

        assert_eq!(calls.len(), 1);
        assert_eq!(calls[0].name, "read_repo_file");
        assert_eq!(
            calls[0].input["_invalid_tool_arguments"],
            "{\"path\":\"src/lib.rs\""
        );
        assert!(calls[0].input["_parse_error"]
            .as_str()
            .unwrap_or_default()
            .contains("EOF"));
    }

    #[test]
    fn planning_tool_schemas_include_knowledge_reader_only_with_sources() {
        let mut request = ModelRequest {
            run: RunSummary {
                run_id: "run_test".to_string(),
                workspace_id: "default".to_string(),
                prompt: "inspect".to_string(),
                status: "running".to_string(),
                final_response: None,
                created_at_ms: 0,
                updated_at_ms: 0,
            },
            repo_root: PathBuf::from("/tmp/repo"),
            agent_instructions: Vec::new(),
            worktree: WorktreeSnapshot {
                available: true,
                clean: true,
                branch: Some("main".to_string()),
                changed_files: Vec::new(),
                error: None,
            },
            knowledge: Vec::new(),
            recent_turns: Vec::new(),
            mode: LocalAgentMode::CodeAgent,
        };

        let tool_names = local_tool_schemas(&request)
            .into_iter()
            .filter_map(|tool| {
                tool.get("function")
                    .and_then(|function| function.get("name"))
                    .and_then(serde_json::Value::as_str)
                    .map(str::to_string)
            })
            .collect::<Vec<_>>();
        assert!(!tool_names.contains(&"read_knowledge_source".to_string()));

        request.knowledge.push(KnowledgeSource {
            source_id: "src_1".to_string(),
            workspace_id: "default".to_string(),
            path: "/tmp/repo/note.md".to_string(),
            title: "note.md".to_string(),
            size_bytes: 12,
            added_at_ms: 0,
        });
        let tool_names = local_tool_schemas(&request)
            .into_iter()
            .filter_map(|tool| {
                tool.get("function")
                    .and_then(|function| function.get("name"))
                    .and_then(serde_json::Value::as_str)
                    .map(str::to_string)
            })
            .collect::<Vec<_>>();

        assert!(tool_names.contains(&"read_knowledge_source".to_string()));
    }
}
