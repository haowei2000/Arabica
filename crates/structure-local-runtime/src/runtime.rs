use crate::model::{
    selected_synthesis_provider, DeterministicLocalModelProvider, LocalModelProvider, ModelRequest,
};
use crate::store::{new_id, SqliteLocalStore};
use crate::tools::{BuiltinLocalToolRegistry, LocalToolRegistry};
use crate::types::{
    ArtifactPreview, ArtifactRecord, ChatTurn, KnowledgeSource, KnowledgeSourcePreview,
    LocalAgentMode, LocalEvidenceBundle, LocalToolResult, ProposalApplyResult, RunEventKind,
    RunEvidenceSummary, RunResult, RunStatus, RunSummary, WorkspaceReplay, WorkspaceSummary,
};
use serde::Serialize;
use std::env;
use std::fs;
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use structure_local_core::{
    collect_snapshot, default_repo_root, verify_structure_core_parity_for_repo,
};

#[derive(Debug, Clone)]
pub struct RunRequest {
    pub prompt: String,
    pub workspace_id: Option<String>,
    pub mode: Option<LocalAgentMode>,
}

pub struct LocalAgentRuntime {
    repo_root: PathBuf,
    runtime_dir: PathBuf,
    store: SqliteLocalStore,
}

impl LocalAgentRuntime {
    pub fn open(repo_root: impl AsRef<Path>) -> Result<Self, String> {
        let repo_root = repo_root
            .as_ref()
            .canonicalize()
            .map_err(|err| format!("failed to canonicalize repo root: {err}"))?;
        load_openai_env_file(&repo_root)?;
        let runtime_dir = env::var("STRUCTURE_LOCAL_RUNTIME_DIR")
            .map(PathBuf::from)
            .unwrap_or_else(|_| repo_root.join(".structure/local"));
        fs::create_dir_all(runtime_dir.join("artifacts"))
            .map_err(|err| format!("failed to create local artifact directory: {err}"))?;
        let store = SqliteLocalStore::open(&runtime_dir)?;
        Ok(Self {
            repo_root,
            runtime_dir,
            store,
        })
    }

    pub fn open_default() -> Result<Self, String> {
        let repo_root = default_repo_root()?;
        Self::open(repo_root)
    }

    pub fn db_path(&self) -> &Path {
        self.store.db_path()
    }

    pub fn repo_root(&self) -> &Path {
        &self.repo_root
    }

    pub fn ensure_workspace(
        &self,
        workspace_id: Option<String>,
    ) -> Result<WorkspaceSummary, String> {
        self.store.ensure_workspace(workspace_id, &self.repo_root)
    }

    pub fn list_workspaces(&self, limit: usize) -> Result<Vec<WorkspaceSummary>, String> {
        self.store.list_workspaces(limit)
    }

    pub fn workspace(&self, workspace_id: &str) -> Result<WorkspaceSummary, String> {
        self.store.workspace_by_id(workspace_id)
    }

    pub fn run_prompt(&self, request: RunRequest) -> Result<RunResult, String> {
        if request.prompt.trim().is_empty() {
            return Err("prompt must not be empty".to_string());
        }
        let mode = request.mode.unwrap_or_default();

        let workspace = self
            .store
            .ensure_workspace(request.workspace_id, &self.repo_root)?;
        let run_id = new_id("run");
        let mut run = self
            .store
            .create_run(&run_id, &workspace.workspace_id, &request.prompt)?;
        self.store.append_event(
            &workspace.workspace_id,
            Some(&run_id),
            RunEventKind::WorkspaceOpened,
            &workspace,
        )?;
        self.store.append_event(
            &workspace.workspace_id,
            Some(&run_id),
            RunEventKind::RunCreated,
            &run,
        )?;
        self.store.append_event(
            &workspace.workspace_id,
            Some(&run_id),
            RunEventKind::ChatMessageRecorded,
            &serde_json::json!({
                "role": "user",
                "mode": mode.as_str(),
                "content": request.prompt,
            }),
        )?;
        self.store.append_event(
            &workspace.workspace_id,
            Some(&run_id),
            RunEventKind::PromptReceived,
            &serde_json::json!({
                "prompt": request.prompt,
                "mode": mode.as_str(),
                "surface": "local_rust",
            }),
        )?;

        run = self.store.update_run(&run_id, RunStatus::Running, None)?;
        match self.execute_running_run(run.clone(), mode) {
            Ok(result) => Ok(result),
            Err(error) => {
                let failed_run = self
                    .store
                    .update_run(&run_id, RunStatus::Failed, Some(&error))?;
                self.store.append_event(
                    &workspace.workspace_id,
                    Some(&run_id),
                    RunEventKind::RunFailed,
                    &serde_json::json!({
                        "run": failed_run,
                        "error": error,
                    }),
                )?;
                Err(error)
            }
        }
    }

    pub fn add_knowledge_source(
        &self,
        workspace_id: Option<String>,
        path: impl AsRef<Path>,
    ) -> Result<KnowledgeSource, String> {
        let workspace = self.store.ensure_workspace(workspace_id, &self.repo_root)?;
        let source = self
            .store
            .add_knowledge_source(&workspace.workspace_id, path)?;
        self.store.append_event(
            &workspace.workspace_id,
            None,
            RunEventKind::WorkspaceContextLoaded,
            &serde_json::json!({
                "action": "knowledge_source_added",
                "source": source,
            }),
        )?;
        Ok(source)
    }

    pub fn knowledge_sources(
        &self,
        workspace_id: Option<&str>,
        limit: usize,
    ) -> Result<Vec<KnowledgeSource>, String> {
        let workspace_id = workspace_id.unwrap_or("default");
        self.store.list_knowledge_sources(workspace_id, limit)
    }

    pub fn knowledge_source(&self, source_id: &str) -> Result<KnowledgeSource, String> {
        self.store.knowledge_source_by_id(source_id)
    }

    pub fn read_knowledge_source(
        &self,
        source_id: &str,
        max_bytes: u64,
    ) -> Result<KnowledgeSourcePreview, String> {
        let source = self.knowledge_source(source_id)?;
        let (preview, bytes_read, truncated) =
            read_limited_text(&source.path, source.size_bytes, max_bytes)
                .map_err(|err| format!("failed to read knowledge source: {err}"))?;

        Ok(KnowledgeSourcePreview {
            source,
            preview,
            bytes_read,
            truncated,
        })
    }

    pub fn remove_knowledge_source(&self, source_id: &str) -> Result<KnowledgeSource, String> {
        let source = self.store.delete_knowledge_source(source_id)?;
        self.store.append_event(
            &source.workspace_id,
            None,
            RunEventKind::WorkspaceContextLoaded,
            &serde_json::json!({
                "action": "knowledge_source_removed",
                "source": source,
            }),
        )?;
        Ok(source)
    }

    pub fn list_runs(
        &self,
        workspace_id: Option<&str>,
        limit: usize,
    ) -> Result<Vec<RunSummary>, String> {
        self.store.list_runs(workspace_id, limit)
    }

    pub fn chat_turns(
        &self,
        workspace_id: Option<&str>,
        limit: usize,
    ) -> Result<Vec<ChatTurn>, String> {
        let runs = self.store.list_runs(workspace_id, limit)?;
        let mut turns = Vec::new();
        for run in runs {
            let events = self.store.run_events(&run.run_id)?;
            turns.push(ChatTurn {
                run_id: run.run_id,
                workspace_id: run.workspace_id,
                user_message: run.prompt,
                assistant_message: run.final_response,
                status: run.status,
                event_count: events.len(),
                created_at_ms: run.created_at_ms,
                updated_at_ms: run.updated_at_ms,
            });
        }
        Ok(turns)
    }

    pub fn workspace_replay(
        &self,
        workspace_id: Option<&str>,
        limit: usize,
    ) -> Result<WorkspaceReplay, String> {
        let workspace_id = workspace_id.unwrap_or("default");
        let events = self.store.workspace_events(workspace_id, limit)?;
        let runs = self.store.list_runs(Some(workspace_id), limit)?;
        let knowledge_sources = self.store.list_knowledge_sources(workspace_id, limit)?;
        let artifacts = self.store.list_artifacts(Some(workspace_id), None, limit)?;
        let last_sequence = events.last().map(|event| event.sequence);

        Ok(WorkspaceReplay {
            workspace_id: workspace_id.to_string(),
            events,
            runs,
            knowledge_sources,
            artifacts,
            last_sequence,
        })
    }

    pub fn local_evidence_bundle(
        &self,
        workspace_id: Option<&str>,
        limit: usize,
    ) -> Result<LocalEvidenceBundle, String> {
        let workspace_id = workspace_id.unwrap_or("default");
        let snapshot = collect_snapshot(&self.repo_root)?;
        let parity_report = verify_structure_core_parity_for_repo(&self.repo_root)?;
        let workspace_replay = self.workspace_replay(Some(workspace_id), limit)?;
        let run_evidence = workspace_replay
            .runs
            .iter()
            .filter_map(|run| self.run_evidence_summary(&run.run_id).ok())
            .collect::<Vec<_>>();

        Ok(LocalEvidenceBundle {
            schema_version: "local-evidence-bundle-v1".to_string(),
            generated_at_ms: crate::store::now_ms(),
            workspace_id: workspace_id.to_string(),
            snapshot,
            parity_report,
            workspace_replay,
            run_evidence,
        })
    }

    pub fn list_artifacts(
        &self,
        workspace_id: Option<&str>,
        run_id: Option<&str>,
        limit: usize,
    ) -> Result<Vec<ArtifactRecord>, String> {
        self.store.list_artifacts(workspace_id, run_id, limit)
    }

    pub fn artifact(&self, artifact_id: &str) -> Result<ArtifactRecord, String> {
        self.store.artifact_by_id(artifact_id)
    }

    pub fn read_artifact(
        &self,
        artifact_id: &str,
        max_bytes: u64,
    ) -> Result<ArtifactPreview, String> {
        let artifact = self.artifact(artifact_id)?;
        let (preview, bytes_read, truncated) =
            read_limited_text(&artifact.path, artifact.size_bytes, max_bytes)
                .map_err(|err| format!("failed to read artifact: {err}"))?;
        Ok(ArtifactPreview {
            artifact,
            preview,
            bytes_read,
            truncated,
        })
    }

    pub fn apply_code_change_proposal(
        &self,
        artifact_id: &str,
        dry_run: bool,
    ) -> Result<ProposalApplyResult, String> {
        let artifact = self.artifact(artifact_id)?;
        if artifact.kind != "code_change_proposal" {
            return Err(format!(
                "artifact {} is {}, not code_change_proposal",
                artifact.artifact_id, artifact.kind
            ));
        }
        let proposal = fs::read_to_string(&artifact.path)
            .map_err(|err| format!("failed to read proposal artifact: {err}"))?;
        let patch = parse_proposal_patch(&proposal)?;
        let target = safe_proposal_target_path(&self.repo_root, &patch.target_path)?;
        let patch_text = render_apply_patch_text(&patch.added_lines);
        let bytes_written = patch_text.len() as u64;

        if !dry_run {
            if let Some(parent) = target.parent() {
                fs::create_dir_all(parent)
                    .map_err(|err| format!("failed to create proposal target directory: {err}"))?;
            }
            let mut file = fs::OpenOptions::new()
                .create(true)
                .append(true)
                .open(&target)
                .map_err(|err| format!("failed to open proposal target for append: {err}"))?;
            file.write_all(patch_text.as_bytes())
                .map_err(|err| format!("failed to apply proposal patch: {err}"))?;
        }

        let result = ProposalApplyResult {
            artifact,
            target_path: target.display().to_string(),
            applied: !dry_run,
            dry_run,
            added_lines: patch.added_lines.len(),
            bytes_written: if dry_run { 0 } else { bytes_written },
            preview: patch_text.chars().take(4000).collect(),
        };
        self.store.append_event(
            &result.artifact.workspace_id,
            Some(&result.artifact.run_id),
            RunEventKind::CodeChangeApplied,
            &result,
        )?;
        Ok(result)
    }

    pub fn run_by_id(&self, run_id: &str) -> Result<RunSummary, String> {
        self.store.run_by_id(run_id)
    }

    pub fn run_events(&self, run_id: &str) -> Result<Vec<crate::types::LocalEvent>, String> {
        self.store.run_events(run_id)
    }

    pub fn run_evidence_summary(&self, run_id: &str) -> Result<RunEvidenceSummary, String> {
        let run = self.run_by_id(run_id)?;
        let events = self.run_events(run_id)?;
        let mut knowledge_sources = Vec::new();
        let mut artifact_paths = Vec::new();
        let mut event_kinds = Vec::new();
        let mut tool_call_count = 0;

        for event in &events {
            if !event_kinds.contains(&event.kind) {
                event_kinds.push(event.kind.clone());
            }
            match event.kind.as_str() {
                "knowledge_retrieved" => {
                    if let Some(sources) = event
                        .payload
                        .get("sources")
                        .and_then(|value| value.as_array())
                    {
                        for source in sources {
                            if let Ok(source) =
                                serde_json::from_value::<KnowledgeSource>(source.clone())
                            {
                                if !knowledge_sources.iter().any(|existing: &KnowledgeSource| {
                                    existing.source_id == source.source_id
                                }) {
                                    knowledge_sources.push(source);
                                }
                            }
                        }
                    }
                }
                "tool_call_completed" => {
                    tool_call_count += 1;
                }
                "artifact_written" => {
                    if let Some(path) = event.payload.get("path").and_then(|value| value.as_str()) {
                        artifact_paths.push(path.to_string());
                    }
                }
                _ => {}
            }
        }
        let artifacts = self.store.list_artifacts(None, Some(run_id), 32)?;
        for artifact in &artifacts {
            if !artifact_paths.contains(&artifact.path) {
                artifact_paths.push(artifact.path.clone());
            }
        }

        let final_response_chars = run
            .final_response
            .as_ref()
            .map(|response| response.chars().count())
            .unwrap_or_default();

        Ok(RunEvidenceSummary {
            run,
            event_count: events.len(),
            tool_call_count,
            knowledge_sources,
            artifact_paths,
            artifacts,
            event_kinds,
            final_response_chars,
        })
    }

    fn write_response_artifact(&self, run_id: &str, response: &str) -> Result<PathBuf, String> {
        let artifact_dir = self.runtime_dir.join("artifacts").join(run_id);
        fs::create_dir_all(&artifact_dir)
            .map_err(|err| format!("failed to create run artifact directory: {err}"))?;
        let path = artifact_dir.join("response.md");
        fs::write(&path, response)
            .map_err(|err| format!("failed to write local response artifact: {err}"))?;
        Ok(path)
    }

    fn write_code_change_proposal_artifact(
        &self,
        run: &RunSummary,
        tool_results: &[LocalToolResult],
    ) -> Result<(PathBuf, String), String> {
        let artifact_dir = self.runtime_dir.join("artifacts").join(&run.run_id);
        fs::create_dir_all(&artifact_dir)
            .map_err(|err| format!("failed to create run artifact directory: {err}"))?;
        let inspected_files = inspected_files_from_tools(tool_results);
        let proposal = render_code_change_proposal(run, &inspected_files);
        let path = artifact_dir.join("code_change_proposal.md");
        fs::write(&path, &proposal)
            .map_err(|err| format!("failed to write code change proposal artifact: {err}"))?;
        Ok((path, proposal))
    }

    fn execute_running_run(
        &self,
        run: RunSummary,
        mode: LocalAgentMode,
    ) -> Result<RunResult, String> {
        let knowledge = self.store.list_knowledge_sources(&run.workspace_id, 8)?;
        self.store.append_event(
            &run.workspace_id,
            Some(&run.run_id),
            RunEventKind::WorkspaceContextLoaded,
            &serde_json::json!({
                "workspace_id": run.workspace_id,
                "repo_root": self.repo_root,
                "runtime_db": self.db_path(),
                "knowledge_sources": knowledge.len(),
            }),
        )?;
        self.store.append_event(
            &run.workspace_id,
            Some(&run.run_id),
            RunEventKind::KnowledgeRetrieved,
            &KnowledgePayload {
                sources: knowledge.clone(),
            },
        )?;

        let model = DeterministicLocalModelProvider;
        let model_request = ModelRequest {
            run: run.clone(),
            repo_root: self.repo_root.clone(),
            knowledge,
            mode: mode.clone(),
        };
        self.store.append_event(
            &run.workspace_id,
            Some(&run.run_id),
            RunEventKind::ModelRequested,
            &serde_json::json!({
                "provider": model.provider_id(),
                "phase": "tool_planning",
                "network_required": false,
            }),
        )?;
        let plan = model.plan(&model_request)?;
        self.store.append_event(
            &run.workspace_id,
            Some(&run.run_id),
            RunEventKind::AgentStepPlanned,
            &serde_json::json!({
                "mode": mode.as_str(),
                "planner": "structure_local_runtime",
                "steps": [
                    "workspace_context_replay",
                    "tool_planning",
                    "local_tool_execution",
                    "response_synthesis",
                    "artifact_persistence"
                ],
                "tool_call_count": plan.tool_calls.len(),
            }),
        )?;
        self.store.append_event(
            &run.workspace_id,
            Some(&run.run_id),
            RunEventKind::ModelResponded,
            &serde_json::json!({
                "provider": plan.provider,
                "phase": "tool_planning",
                "tool_calls": plan.tool_calls,
            }),
        )?;

        let registry = BuiltinLocalToolRegistry::new(&self.repo_root);
        let mut tool_results = Vec::new();
        for call in &plan.tool_calls {
            self.store.append_event(
                &run.workspace_id,
                Some(&run.run_id),
                RunEventKind::ToolCallRequested,
                call,
            )?;
            let result = registry.execute(call);
            self.store.append_event(
                &run.workspace_id,
                Some(&run.run_id),
                RunEventKind::ToolCallCompleted,
                &result,
            )?;
            tool_results.push(result);
        }

        let synthesis_model = selected_synthesis_provider()?;
        self.store.append_event(
            &run.workspace_id,
            Some(&run.run_id),
            RunEventKind::ModelRequested,
            &serde_json::json!({
                "provider": synthesis_model.provider_id(),
                "phase": "response_synthesis",
                "tool_results": tool_results.len(),
                "network_required": synthesis_model.provider_id() != "local_deterministic",
            }),
        )?;
        let output = synthesis_model.synthesize(&model_request, &tool_results)?;
        let mut final_response = output.final_response;
        let proposal_path = if matches!(mode, LocalAgentMode::CodeAgent) {
            let (path, proposal) = self.write_code_change_proposal_artifact(&run, &tool_results)?;
            final_response.push_str("\n## Code Change Proposal\n\n");
            final_response.push_str("A proposed change artifact was produced for review. ");
            final_response.push_str("No repository files were modified by this run.\n\n");
            final_response.push_str(&format!("Artifact: `{}`\n\n", path.display()));
            final_response.push_str("```diff\n");
            final_response.push_str(&extract_patch_sketch(&proposal));
            final_response.push_str("\n```\n");
            Some(path)
        } else {
            None
        };
        self.store.append_event(
            &run.workspace_id,
            Some(&run.run_id),
            RunEventKind::ModelResponded,
            &serde_json::json!({
                "provider": output.provider,
                "phase": "response_synthesis",
                "response_chars": final_response.chars().count(),
                "response_preview": final_response.chars().take(240).collect::<String>(),
            }),
        )?;
        self.store.append_event(
            &run.workspace_id,
            Some(&run.run_id),
            RunEventKind::ChatMessageRecorded,
            &serde_json::json!({
                "role": "assistant",
                "mode": mode.as_str(),
                "content": final_response,
            }),
        )?;

        let artifact_path = self.write_response_artifact(&run.run_id, &final_response)?;
        let artifact = self.store.add_artifact(
            &run.run_id,
            &run.workspace_id,
            "assistant_response",
            &artifact_path,
        )?;
        self.store.append_event(
            &run.workspace_id,
            Some(&run.run_id),
            RunEventKind::ArtifactWritten,
            &artifact,
        )?;
        if let Some(proposal_path) = proposal_path {
            let proposal_artifact = self.store.add_artifact(
                &run.run_id,
                &run.workspace_id,
                "code_change_proposal",
                &proposal_path,
            )?;
            self.store.append_event(
                &run.workspace_id,
                Some(&run.run_id),
                RunEventKind::CodeChangeProposed,
                &serde_json::json!({
                    "artifact": proposal_artifact,
                    "status": "proposed_only",
                    "applied": false,
                }),
            )?;
            self.store.append_event(
                &run.workspace_id,
                Some(&run.run_id),
                RunEventKind::ArtifactWritten,
                &proposal_artifact,
            )?;
        }

        let run = self
            .store
            .update_run(&run.run_id, RunStatus::Finished, Some(&final_response))?;
        self.store.append_event(
            &run.workspace_id,
            Some(&run.run_id),
            RunEventKind::RunFinished,
            &run,
        )?;
        let events = self.store.run_events(&run.run_id)?;

        Ok(RunResult {
            run,
            events,
            final_response,
            artifact_path: artifact.path.clone(),
            artifact,
        })
    }
}

fn load_openai_env_file(repo_root: &Path) -> Result<(), String> {
    let env_path = repo_root.join(".env");
    if !env_path.exists() {
        return Ok(());
    }
    let entries = dotenvy::from_path_iter(&env_path)
        .map_err(|err| format!("failed to parse {}: {err}", env_path.display()))?;
    for entry in entries {
        let (key, value) =
            entry.map_err(|err| format!("failed to parse {}: {err}", env_path.display()))?;
        if matches!(
            key.as_str(),
            "OPENAI__API_KEY" | "OPENAI__BASE_URL" | "OPENAI__MODEL"
        ) && env::var_os(&key).is_none()
        {
            env::set_var(key, value);
        }
    }
    Ok(())
}

fn read_limited_text(
    path: impl AsRef<Path>,
    size_bytes: u64,
    max_bytes: u64,
) -> Result<(String, u64, bool), String> {
    let max_bytes = max_bytes.clamp(1, 1_000_000);
    let file = fs::File::open(path).map_err(|err| err.to_string())?;
    let mut reader = file.take(max_bytes);
    let mut buffer = Vec::new();
    reader
        .read_to_end(&mut buffer)
        .map_err(|err| err.to_string())?;
    let bytes_read = buffer.len() as u64;
    let truncated = size_bytes > bytes_read;
    let preview = String::from_utf8_lossy(&buffer).into_owned();
    Ok((preview, bytes_read, truncated))
}

#[derive(Debug, Clone, Serialize)]
struct KnowledgePayload {
    sources: Vec<KnowledgeSource>,
}

fn inspected_files_from_tools(tool_results: &[LocalToolResult]) -> Vec<String> {
    let mut files = Vec::new();
    for result in tool_results {
        if !result.success {
            continue;
        }
        if result.name == "search_repo" {
            if let Some(matches) = result
                .output
                .get("matches")
                .and_then(|value| value.as_array())
            {
                for item in matches {
                    if let Some(path) = item.get("path").and_then(|value| value.as_str()) {
                        push_unique_file(&mut files, path);
                    }
                }
            }
        }
        if result.name == "read_repo_file" {
            if let Some(path) = result.output.get("path").and_then(|value| value.as_str()) {
                push_unique_file(&mut files, path);
            }
        }
    }
    files.truncate(8);
    files
}

fn push_unique_file(files: &mut Vec<String>, path: &str) {
    if !files.iter().any(|existing| existing == path) {
        files.push(path.to_string());
    }
}

fn render_code_change_proposal(run: &RunSummary, inspected_files: &[String]) -> String {
    let target_file = inspected_files
        .first()
        .cloned()
        .unwrap_or_else(|| "docs/local-code-agent-proposal.md".to_string());
    let inspected = if inspected_files.is_empty() {
        "- No concrete source files were selected by the local search tool.\n".to_string()
    } else {
        inspected_files
            .iter()
            .map(|path| format!("- `{path}`\n"))
            .collect::<String>()
    };

    format!(
        r#"# Structure Code Change Proposal

Status: proposed_only
Applied: false
Run: `{run_id}`
Workspace: `{workspace_id}`

## Intent

{prompt}

## Files Inspected

{inspected}
## Review Notes

This artifact is generated inside the Rust local event loop. It is evidence for
the agent's proposed direction and does not modify repository files. A later
approval/apply step can turn a reviewed proposal into an actual patch.

## Patch Sketch

```diff
diff --git a/{target_file} b/{target_file}
--- a/{target_file}
+++ b/{target_file}
@@
+# Proposed Structure local code-agent change
+Run: {run_id}
+Workspace: {workspace_id}
+Intent: {escaped_prompt}
+Evidence: review the run events, tool results, and this proposal artifact before applying.
```
"#,
        run_id = run.run_id,
        workspace_id = run.workspace_id,
        prompt = run.prompt,
        inspected = inspected,
        target_file = target_file,
        escaped_prompt = run.prompt.replace('\n', " ")
    )
}

fn extract_patch_sketch(proposal: &str) -> String {
    let marker = "```diff\n";
    let Some(start) = proposal.find(marker) else {
        return proposal.chars().take(1000).collect();
    };
    let body = &proposal[start + marker.len()..];
    let end = body.find("\n```").unwrap_or(body.len());
    body[..end].to_string()
}

#[derive(Debug, Clone)]
struct ParsedProposalPatch {
    target_path: String,
    added_lines: Vec<String>,
}

fn parse_proposal_patch(proposal: &str) -> Result<ParsedProposalPatch, String> {
    let mut target_path = None;
    let mut in_patch = false;
    let mut added_lines = Vec::new();

    for line in proposal.lines() {
        if let Some(rest) = line.strip_prefix("diff --git a/") {
            if let Some((_, target)) = rest.split_once(" b/") {
                target_path = Some(target.trim().to_string());
            }
            continue;
        }
        if line.trim() == "```diff" {
            in_patch = true;
            continue;
        }
        if in_patch && line.trim() == "```" {
            break;
        }
        if !in_patch {
            continue;
        }
        if line.starts_with("+++") || line.starts_with("---") || line.starts_with("@@") {
            continue;
        }
        if let Some(added) = line.strip_prefix('+') {
            added_lines.push(added.to_string());
        }
    }

    let target_path = target_path.ok_or_else(|| {
        "proposal patch does not include a diff target path generated by Structure".to_string()
    })?;
    if added_lines.is_empty() {
        return Err("proposal patch does not contain any added lines".to_string());
    }

    Ok(ParsedProposalPatch {
        target_path,
        added_lines,
    })
}

fn render_apply_patch_text(lines: &[String]) -> String {
    let mut text = String::new();
    text.push('\n');
    for line in lines {
        text.push_str(line);
        text.push('\n');
    }
    text
}

fn safe_proposal_target_path(repo_root: &Path, relative_path: &str) -> Result<PathBuf, String> {
    let path = Path::new(relative_path);
    if path.is_absolute() {
        return Err("proposal target must be repo-relative".to_string());
    }
    if path
        .components()
        .any(|component| matches!(component, std::path::Component::ParentDir))
    {
        return Err("proposal target escapes the workspace root".to_string());
    }
    let candidate = repo_root.join(path);
    if is_sensitive_proposal_target(&candidate) {
        return Err("refusing to apply proposal to sensitive local configuration file".to_string());
    }
    if candidate.exists() {
        let canonical = candidate
            .canonicalize()
            .map_err(|err| format!("failed to canonicalize proposal target: {err}"))?;
        if !canonical.starts_with(repo_root) {
            return Err("proposal target escapes the workspace root".to_string());
        }
        return Ok(canonical);
    }
    let parent = candidate
        .parent()
        .ok_or_else(|| "proposal target does not have a parent directory".to_string())?;
    let canonical_parent = if parent.exists() {
        parent
            .canonicalize()
            .map_err(|err| format!("failed to canonicalize proposal target parent: {err}"))?
    } else {
        let nearest = existing_parent(parent)?;
        nearest
            .canonicalize()
            .map_err(|err| format!("failed to canonicalize proposal target parent: {err}"))?
    };
    if !canonical_parent.starts_with(repo_root) {
        return Err("proposal target escapes the workspace root".to_string());
    }
    Ok(candidate)
}

fn existing_parent(path: &Path) -> Result<PathBuf, String> {
    let mut current = path;
    loop {
        if current.exists() {
            return Ok(current.to_path_buf());
        }
        current = current
            .parent()
            .ok_or_else(|| "proposal target parent escapes the workspace root".to_string())?;
    }
}

fn is_sensitive_proposal_target(path: &Path) -> bool {
    let Some(name) = path.file_name().and_then(|name| name.to_str()) else {
        return false;
    };
    if matches!(name, ".env.example" | "example.env") {
        return false;
    }
    name == ".env"
        || name.starts_with(".env.")
        || name.ends_with(".env")
        || name.ends_with(".env.local")
        || name.contains("secret")
        || name.contains("credential")
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::test_env::OpenAiEnvGuard;

    #[test]
    fn local_runtime_runs_prompt_and_replays_events() {
        let _env = OpenAiEnvGuard::clear();
        let root = unique_repo("run-loop");
        let runtime = LocalAgentRuntime::open(&root).unwrap();
        let result = runtime
            .run_prompt(RunRequest {
                prompt: "Summarize this workspace".to_string(),
                workspace_id: None,
                mode: None,
            })
            .unwrap();

        assert_eq!(result.run.status, "finished");
        assert!(result
            .final_response
            .contains("embedded Structure event loop"));
        assert!(result.final_response.contains("Tool Evidence"));
        assert!(result.final_response.contains("Code Change Proposal"));
        assert!(Path::new(&result.artifact_path).exists());
        assert_eq!(
            result.events.last().map(|event| event.kind.as_str()),
            Some("run_finished")
        );
        assert!(result
            .events
            .iter()
            .any(|event| event.kind == "tool_call_completed"));
        assert!(result
            .events
            .iter()
            .any(|event| event.kind == "code_change_proposed"));
        assert_eq!(result.artifact.kind, "assistant_response");
        let artifacts = runtime
            .list_artifacts(None, Some(&result.run.run_id), 10)
            .unwrap();
        assert_eq!(artifacts.len(), 2);
        assert!(artifacts
            .iter()
            .any(|artifact| artifact.kind == "code_change_proposal"));

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn local_runtime_lists_explicit_workspaces() {
        let _env = OpenAiEnvGuard::clear();
        let root = unique_repo("workspaces");
        let runtime = LocalAgentRuntime::open(&root).unwrap();
        runtime
            .ensure_workspace(Some("paper".to_string()))
            .expect("workspace should be created");
        runtime
            .run_prompt(RunRequest {
                prompt: "Run inside the app workspace".to_string(),
                workspace_id: Some("app".to_string()),
                mode: None,
            })
            .unwrap();

        let workspaces = runtime.list_workspaces(10).unwrap();
        let app = runtime.workspace("app").unwrap();

        assert_eq!(app.workspace_id, "app");
        assert!(workspaces
            .iter()
            .any(|workspace| workspace.workspace_id == "paper"));
        assert!(workspaces
            .iter()
            .any(|workspace| workspace.workspace_id == "app"));

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn local_runtime_attaches_knowledge_to_context() {
        let _env = OpenAiEnvGuard::clear();
        let root = unique_repo("knowledge");
        let source_path = root.join("note.md");
        fs::write(&source_path, "local context").unwrap();
        let runtime = LocalAgentRuntime::open(&root).unwrap();
        runtime
            .add_knowledge_source(None, &source_path)
            .expect("knowledge source should be stored");
        let result = runtime
            .run_prompt(RunRequest {
                prompt: "Use the note".to_string(),
                workspace_id: None,
                mode: None,
            })
            .unwrap();

        assert!(result.final_response.contains("note.md"));
        assert!(result.final_response.contains("local context"));
        assert!(result
            .events
            .iter()
            .any(|event| event.kind == "knowledge_retrieved"));
        let evidence = runtime.run_evidence_summary(&result.run.run_id).unwrap();
        assert_eq!(evidence.knowledge_sources.len(), 1);
        assert_eq!(evidence.artifacts.len(), 2);
        assert!(evidence.tool_call_count >= 1);
        assert!(evidence
            .event_kinds
            .contains(&"knowledge_retrieved".to_string()));

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn local_runtime_replays_workspace_and_reads_artifacts() {
        let _env = OpenAiEnvGuard::clear();
        let root = unique_repo("replay");
        let runtime = LocalAgentRuntime::open(&root).unwrap();
        let result = runtime
            .run_prompt(RunRequest {
                prompt: "Create a replayable run".to_string(),
                workspace_id: None,
                mode: None,
            })
            .unwrap();

        let replay = runtime.workspace_replay(None, 50).unwrap();
        let artifact_preview = runtime
            .read_artifact(&result.artifact.artifact_id, 128)
            .unwrap();

        assert_eq!(replay.workspace_id, "default");
        assert!(replay.events.len() >= result.events.len());
        assert_eq!(replay.artifacts.len(), 2);
        assert_eq!(
            replay.last_sequence,
            replay.events.last().map(|event| event.sequence)
        );
        assert_eq!(
            artifact_preview.artifact.artifact_id,
            result.artifact.artifact_id
        );
        assert!(artifact_preview
            .preview
            .contains("Structure Local Agent Response"));

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn local_runtime_builds_evidence_bundle() {
        let _env = OpenAiEnvGuard::clear();
        let root = unique_repo("bundle");
        write_parity_marker_files(&root);
        let runtime = LocalAgentRuntime::open(&root).unwrap();
        let result = runtime
            .run_prompt(RunRequest {
                prompt: "Bundle this local evidence".to_string(),
                workspace_id: None,
                mode: None,
            })
            .unwrap();

        let bundle = runtime.local_evidence_bundle(None, 50).unwrap();

        assert_eq!(bundle.schema_version, "local-evidence-bundle-v1");
        assert_eq!(bundle.workspace_id, "default");
        assert!(bundle.parity_report.passed);
        assert!(bundle
            .workspace_replay
            .runs
            .iter()
            .any(|run| run.run_id == result.run.run_id));
        assert!(bundle
            .run_evidence
            .iter()
            .any(|evidence| evidence.run.run_id == result.run.run_id));

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn local_runtime_previews_and_removes_knowledge_source() {
        let _env = OpenAiEnvGuard::clear();
        let root = unique_repo("knowledge-preview");
        let source_path = root.join("source.md");
        fs::write(&source_path, "first line\nsecond line\nthird line").unwrap();
        let runtime = LocalAgentRuntime::open(&root).unwrap();
        let source = runtime
            .add_knowledge_source(None, &source_path)
            .expect("knowledge source should be stored");

        let preview = runtime
            .read_knowledge_source(&source.source_id, 12)
            .expect("knowledge source should be readable");
        let removed = runtime
            .remove_knowledge_source(&source.source_id)
            .expect("knowledge source should be removable");
        let remaining = runtime.knowledge_sources(None, 10).unwrap();

        assert_eq!(preview.source.source_id, source.source_id);
        assert_eq!(preview.preview, "first line\ns");
        assert_eq!(preview.bytes_read, 12);
        assert!(preview.truncated);
        assert_eq!(removed.source_id, source.source_id);
        assert!(remaining.is_empty());
        assert!(source_path.exists());

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn local_runtime_applies_reviewed_code_change_proposal() {
        let _env = OpenAiEnvGuard::clear();
        let root = unique_repo("proposal-apply");
        let runtime = LocalAgentRuntime::open(&root).unwrap();
        let result = runtime
            .run_prompt(RunRequest {
                prompt: "Create a local proposal for the code agent".to_string(),
                workspace_id: Some("apply".to_string()),
                mode: Some(LocalAgentMode::CodeAgent),
            })
            .unwrap();
        let proposal = runtime
            .list_artifacts(Some("apply"), Some(&result.run.run_id), 10)
            .unwrap()
            .into_iter()
            .find(|artifact| artifact.kind == "code_change_proposal")
            .expect("proposal artifact should exist");

        let dry_run = runtime
            .apply_code_change_proposal(&proposal.artifact_id, true)
            .unwrap();
        assert!(!dry_run.applied);
        assert!(dry_run.dry_run);
        assert_eq!(dry_run.bytes_written, 0);
        assert!(!Path::new(&dry_run.target_path).exists());

        let applied = runtime
            .apply_code_change_proposal(&proposal.artifact_id, false)
            .unwrap();
        assert!(applied.applied);
        assert!(!applied.dry_run);
        assert!(applied.added_lines >= 1);
        assert!(applied.bytes_written > 0);
        let target_text = fs::read_to_string(&applied.target_path).unwrap();
        assert!(target_text.contains("Proposed Structure local code-agent change"));
        let events = runtime.run_events(&result.run.run_id).unwrap();
        assert!(events
            .iter()
            .any(|event| event.kind == "code_change_applied"));

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn local_runtime_loads_openai_env_from_repo_dotenv_without_overriding_shell() {
        let _env = OpenAiEnvGuard::clear();
        env::set_var("OPENAI__MODEL", "shell-model");
        let root = unique_repo("dotenv");
        fs::write(
            root.join(".env"),
            "OPENAI__API_KEY=dotenv-key\nOPENAI__BASE_URL=http://dotenv.test/v1\nOPENAI__MODEL=dotenv-model\n",
        )
        .unwrap();

        let runtime = LocalAgentRuntime::open(&root).unwrap();

        assert_eq!(runtime.repo_root(), root.canonicalize().unwrap());
        assert_eq!(env::var("OPENAI__API_KEY").unwrap(), "dotenv-key");
        assert_eq!(
            env::var("OPENAI__BASE_URL").unwrap(),
            "http://dotenv.test/v1"
        );
        assert_eq!(env::var("OPENAI__MODEL").unwrap(), "shell-model");

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn local_runtime_persists_failed_run_when_openai_api_fails() {
        let _env = OpenAiEnvGuard::clear();
        let root = unique_repo("api-failure");
        fs::write(
            root.join(".env"),
            "OPENAI__API_KEY=test-key\nOPENAI__BASE_URL=http://127.0.0.1:9/v1\nOPENAI__MODEL=test-model\n",
        )
        .unwrap();
        let runtime = LocalAgentRuntime::open(&root).unwrap();

        let error = runtime
            .run_prompt(RunRequest {
                prompt: "This should record a failed API-backed run".to_string(),
                workspace_id: Some("api-failure".to_string()),
                mode: Some(LocalAgentMode::CodeAgent),
            })
            .unwrap_err();
        let runs = runtime.list_runs(Some("api-failure"), 1).unwrap();
        let run = runs.first().expect("failed run should be persisted");
        let events = runtime.run_events(&run.run_id).unwrap();

        assert!(error.contains("local LLM API request failed"));
        assert_eq!(run.status, "failed");
        assert!(run
            .final_response
            .as_deref()
            .unwrap_or_default()
            .contains("local LLM API request failed"));
        assert!(events.iter().any(|event| {
            event.kind == "model_requested"
                && event.payload.get("phase").and_then(|value| value.as_str())
                    == Some("response_synthesis")
                && event
                    .payload
                    .get("provider")
                    .and_then(|value| value.as_str())
                    == Some("local_env_api")
        }));
        assert!(events.iter().any(|event| event.kind == "run_failed"));

        fs::remove_dir_all(root).unwrap();
    }

    fn unique_repo(label: &str) -> PathBuf {
        let root = std::env::temp_dir().join(format!(
            "structure-local-runtime-repo-{label}-{}-{}",
            std::process::id(),
            crate::store::now_ms()
        ));
        fs::create_dir_all(root.join("src/structure")).unwrap();
        fs::create_dir_all(root.join("frontend")).unwrap();
        fs::write(root.join("pyproject.toml"), "").unwrap();
        root
    }

    fn write_parity_marker_files(root: &Path) {
        write_file(
            root,
            "crates/structure-local/src/cli.rs",
            r#"
            Command::Core Command::Surfaces Command::Chat Command::Run Command::Proposals Command::Tui
            run_core run_surfaces run_chat_agent run_local_agent
            WorkspaceCommand::Create WorkspaceCommand::List WorkspaceCommand::Show
            KnowledgeCommand::Add KnowledgeCommand::Show KnowledgeCommand::Remove
            RunsCommand::Events WorkspaceCommand::Replay
            ArtifactsCommand::List ArtifactsCommand::Show
            ProposalsCommand::List ProposalsCommand::Show ProposalsCommand::Apply
            "#,
        );
        write_file(
            root,
            "frontend/src-tauri/src/lib.rs",
            r#"
            fn local_snapshot() {} fn core_manifest() {} core_manifest,
            fn local_agent_run() {} fn local_chat_turns() {} local_chat_turns,
            fn create_local_workspace() {} fn local_workspaces() {}
            fn local_repo_entries() {} fn local_repo_search() {} fn read_local_repo_file() {}
            fn add_local_knowledge() {} fn read_local_knowledge_source() {}
            fn remove_local_knowledge() {} remove_local_knowledge,
            fn local_run_events() {} fn local_workspace_replay() {} local_workspace_replay,
            fn local_artifacts() {} fn read_local_artifact() {} read_local_artifact,
            fn apply_local_proposal() {}
            "#,
        );
        write_file(
            root,
            "frontend/src-tauri/local-ui/index.html",
            r#"Preview Latest Proposal Code-Agent Chat"#,
        );
        write_file(
            root,
            "frontend/src/core/structureCore.ts",
            "STRUCTURE_CORE_MANIFEST BenchmarkReportSchema CoreCapability benchmark_report_schema",
        );
        write_file(
            root,
            "src/structure/routers/runs/runs.py",
            "async def create_run(): pass",
        );
        write_file(
            root,
            "src/structure/services/runs/run_application_service.py",
            "async def start_user_run(): pass",
        );
        write_file(
            root,
            "src/structure/services/workspace_context/workspace_context_service.py",
            "class WorkspaceContextService: pass\nasync def get(): pass\nasync def list(): pass",
        );
        write_file(
            root,
            "src/structure/routers/workspaces/workspace.py",
            "async def list_workspace_contexts(): pass\nasync def remove_workspace_context(): pass",
        );
        write_file(
            root,
            "frontend/src/services/eventService.ts",
            "LIST_BY_WORKSPACE LIST_BY_RUN",
        );
        write_file(
            root,
            "src/structure/services/events/event_crud.py",
            "class EventCRUD: pass",
        );
        write_file(
            root,
            "src/structure/routers/runs/artifacts.py",
            "async def list_artifacts(): pass\nasync def get_artifact(): pass",
        );
        write_file(
            root,
            "src/structure/services/runs/artifact_crud.py",
            "class ArtifactCRUD: pass",
        );
        write_file(
            root,
            "benchmarks/adapters/structure_run.py",
            "class StructureRunBenchmarkAgent: pass",
        );
    }

    fn write_file(root: &Path, relative_path: &str, text: &str) {
        let path = root.join(relative_path);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, text).unwrap();
    }
}
