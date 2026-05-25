use crate::store::new_id;
use crate::types::{KnowledgeSource, LocalToolCall, LocalToolResult, RunSummary};
use serde::{Deserialize, Serialize};
use std::path::PathBuf;

#[derive(Debug, Clone)]
pub struct ModelRequest {
    pub run: RunSummary,
    pub repo_root: PathBuf,
    pub knowledge: Vec<KnowledgeSource>,
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
        response.push_str("This response was produced by the embedded local event loop.\n\n");
        response.push_str(&format!("- Run: `{}`\n", request.run.run_id));
        response.push_str(&format!("- Workspace: `{}`\n", request.run.workspace_id));
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
