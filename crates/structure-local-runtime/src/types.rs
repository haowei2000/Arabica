use serde::{Deserialize, Serialize};
use structure_local_core::{LocalSnapshot, SurfaceParityReport};

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum RunStatus {
    Pending,
    Running,
    Finished,
    Failed,
}

impl RunStatus {
    pub(crate) fn as_str(&self) -> &'static str {
        match self {
            Self::Pending => "pending",
            Self::Running => "running",
            Self::Finished => "finished",
            Self::Failed => "failed",
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum RunEventKind {
    WorkspaceOpened,
    RunCreated,
    ChatMessageRecorded,
    PromptReceived,
    AgentStepPlanned,
    WorkspaceContextLoaded,
    KnowledgeRetrieved,
    SourceRated,
    ModelRequested,
    ModelResponded,
    ToolCallRequested,
    ToolCallCompleted,
    CodeChangeProposed,
    CodeChangeApplied,
    ArtifactWritten,
    RunFinished,
    RunFailed,
}

impl RunEventKind {
    pub(crate) fn as_str(&self) -> &'static str {
        match self {
            Self::WorkspaceOpened => "workspace_opened",
            Self::RunCreated => "run_created",
            Self::ChatMessageRecorded => "chat_message_recorded",
            Self::PromptReceived => "prompt_received",
            Self::AgentStepPlanned => "agent_step_planned",
            Self::WorkspaceContextLoaded => "workspace_context_loaded",
            Self::KnowledgeRetrieved => "knowledge_retrieved",
            Self::SourceRated => "source_rated",
            Self::ModelRequested => "model_requested",
            Self::ModelResponded => "model_responded",
            Self::ToolCallRequested => "tool_call_requested",
            Self::ToolCallCompleted => "tool_call_completed",
            Self::CodeChangeProposed => "code_change_proposed",
            Self::CodeChangeApplied => "code_change_applied",
            Self::ArtifactWritten => "artifact_written",
            Self::RunFinished => "run_finished",
            Self::RunFailed => "run_failed",
        }
    }
}

pub fn event_taxonomy_for_kind(kind: &str) -> (&'static str, &'static str) {
    match kind {
        "workspace_opened" | "workspace_context_loaded" => ("address", "path_addressing"),
        "prompt_received" | "run_created" => ("goal", "event_audit"),
        "knowledge_retrieved" => ("disclose", "multi_level_disclosure"),
        "source_rated" => ("feedback", "source_evaluation"),
        "artifact_written" | "code_change_proposed" | "run_finished" | "run_failed" => {
            ("evidence", "reproducible_evidence")
        }
        "code_change_applied" => ("feedback", "event_audit"),
        "chat_message_recorded"
        | "agent_step_planned"
        | "model_requested"
        | "model_responded"
        | "tool_call_requested"
        | "tool_call_completed" => ("event", "event_audit"),
        _ => ("event", "event_audit"),
    }
}

#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum LocalAgentMode {
    Chat,
    #[default]
    CodeAgent,
}

impl LocalAgentMode {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Chat => "chat",
            Self::CodeAgent => "code_agent",
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LocalEvent {
    pub sequence: i64,
    pub event_id: String,
    pub run_id: Option<String>,
    pub workspace_id: String,
    pub kind: String,
    pub canonical_flow_id: String,
    pub primitive_id: String,
    pub payload: serde_json::Value,
    pub created_at_ms: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct WorkspaceSummary {
    pub workspace_id: String,
    pub name: String,
    pub root_path: String,
    pub created_at_ms: i64,
    pub updated_at_ms: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct KnowledgeSource {
    pub source_id: String,
    pub workspace_id: String,
    pub path: String,
    pub title: String,
    pub size_bytes: u64,
    pub added_at_ms: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct KnowledgeSourcePreview {
    pub source: KnowledgeSource,
    pub preview: String,
    pub bytes_read: u64,
    pub truncated: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SourceRating {
    pub source_id: String,
    pub workspace_id: String,
    pub run_id: Option<String>,
    pub rating: u8,
    pub note: String,
    pub source_title: String,
    pub source_path: String,
    pub created_at_ms: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AgentInstruction {
    pub path: String,
    pub title: String,
    pub size_bytes: u64,
    pub content_preview: String,
    pub truncated: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct WorktreeChange {
    pub status: String,
    pub path: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct WorktreeSnapshot {
    pub available: bool,
    pub clean: bool,
    pub branch: Option<String>,
    pub changed_files: Vec<WorktreeChange>,
    pub error: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RunSummary {
    pub run_id: String,
    pub workspace_id: String,
    pub prompt: String,
    pub status: String,
    pub final_response: Option<String>,
    pub created_at_ms: i64,
    pub updated_at_ms: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ChatTurn {
    pub run_id: String,
    pub workspace_id: String,
    pub mode: String,
    pub user_message: String,
    pub assistant_message: Option<String>,
    pub status: String,
    pub event_count: usize,
    pub created_at_ms: i64,
    pub updated_at_ms: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ArtifactRecord {
    pub artifact_id: String,
    pub run_id: String,
    pub workspace_id: String,
    pub kind: String,
    pub path: String,
    pub size_bytes: u64,
    pub created_at_ms: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ArtifactPreview {
    pub artifact: ArtifactRecord,
    pub preview: String,
    pub bytes_read: u64,
    pub truncated: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProposalApplyResult {
    pub artifact: ArtifactRecord,
    pub target_path: String,
    pub applied: bool,
    pub dry_run: bool,
    pub added_lines: usize,
    pub bytes_written: u64,
    pub preview: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RunResult {
    pub run: RunSummary,
    pub events: Vec<LocalEvent>,
    pub final_response: String,
    pub artifact_path: String,
    pub artifact: ArtifactRecord,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LocalAgentContext {
    pub workspace_id: String,
    pub mode: String,
    pub repo_root: String,
    pub runtime_db: String,
    pub agent_instructions: Vec<AgentInstruction>,
    pub worktree: WorktreeSnapshot,
    pub knowledge_sources: Vec<KnowledgeSource>,
    pub recent_turns: Vec<ChatTurn>,
    pub context_replay_limit: usize,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RunAttempt {
    pub run: RunSummary,
    pub events: Vec<LocalEvent>,
    pub result: Option<RunResult>,
    pub error: Option<String>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq, Eq)]
pub struct ModelTokenUsage {
    pub prompt_tokens: u64,
    pub completion_tokens: u64,
    pub total_tokens: u64,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq, Eq)]
pub struct ModelUsageSummary {
    pub model_request_count: usize,
    pub model_response_count: usize,
    pub network_request_count: usize,
    pub prompt_tokens: u64,
    pub completion_tokens: u64,
    pub total_tokens: u64,
    pub response_chars: usize,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RunEvidenceSummary {
    pub run: RunSummary,
    pub event_count: usize,
    pub tool_call_count: usize,
    pub model_usage: ModelUsageSummary,
    pub agent_instruction_paths: Vec<String>,
    pub worktree: Option<WorktreeSnapshot>,
    pub prompt_references: Vec<String>,
    pub knowledge_sources: Vec<KnowledgeSource>,
    pub source_ratings: Vec<SourceRating>,
    pub artifact_paths: Vec<String>,
    pub artifacts: Vec<ArtifactRecord>,
    pub event_kinds: Vec<String>,
    pub canonical_flow_ids: Vec<String>,
    pub primitive_ids: Vec<String>,
    pub core_trace: CoreExecutionTrace,
    pub final_response_chars: usize,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CoreExecutionTrace {
    pub manifest_schema_version: String,
    pub event_count: usize,
    pub flow_ids: Vec<String>,
    pub primitive_ids: Vec<String>,
    pub invalid_flow_ids: Vec<String>,
    pub invalid_primitive_ids: Vec<String>,
    pub core_aligned: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RunTranscript {
    pub run: RunSummary,
    pub chat_turn: Option<ChatTurn>,
    pub events: Vec<LocalEvent>,
    pub evidence: RunEvidenceSummary,
    pub final_response: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct WorkspaceReplay {
    pub workspace_id: String,
    pub events: Vec<LocalEvent>,
    pub runs: Vec<RunSummary>,
    pub knowledge_sources: Vec<KnowledgeSource>,
    pub artifacts: Vec<ArtifactRecord>,
    pub last_sequence: Option<i64>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct WorkspaceEventFeed {
    pub workspace_id: String,
    pub after_sequence: i64,
    pub events: Vec<LocalEvent>,
    pub last_sequence: Option<i64>,
    pub next_after_sequence: i64,
}

#[derive(Debug, Clone, Serialize)]
pub struct LocalEvidenceBundle {
    pub schema_version: String,
    pub generated_at_ms: i64,
    pub workspace_id: String,
    pub snapshot: LocalSnapshot,
    pub parity_report: SurfaceParityReport,
    pub workspace_replay: WorkspaceReplay,
    pub run_evidence: Vec<RunEvidenceSummary>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LocalLlmDiagnostic {
    pub provider: String,
    pub configured: bool,
    pub ok: bool,
    pub model: Option<String>,
    pub endpoint: Option<String>,
    pub elapsed_ms: u128,
    pub response_preview: Option<String>,
    pub error: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LocalToolCall {
    pub call_id: String,
    pub name: String,
    pub input: serde_json::Value,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LocalToolResult {
    pub call_id: String,
    pub name: String,
    pub success: bool,
    pub output: serde_json::Value,
    pub error: Option<String>,
}
