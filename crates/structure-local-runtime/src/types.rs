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
    TaskCreated,
    TaskUpdated,
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
    CodeChangeReverted,
    ArtifactWritten,
    RunFinished,
    RunFailed,
}

impl RunEventKind {
    pub(crate) fn as_str(&self) -> &'static str {
        match self {
            Self::WorkspaceOpened => "workspace_opened",
            Self::RunCreated => "run_created",
            Self::TaskCreated => "task_created",
            Self::TaskUpdated => "task_updated",
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
            Self::CodeChangeReverted => "code_change_reverted",
            Self::ArtifactWritten => "artifact_written",
            Self::RunFinished => "run_finished",
            Self::RunFailed => "run_failed",
        }
    }
}

pub fn event_taxonomy_for_kind(kind: &str) -> (&'static str, &'static str) {
    match kind {
        "workspace_opened" | "workspace_context_loaded" => ("address", "path_addressing"),
        "prompt_received" | "run_created" | "task_created" => ("goal", "event_audit"),
        "knowledge_retrieved" => ("disclose", "multi_level_disclosure"),
        "source_rated" => ("feedback", "source_evaluation"),
        "artifact_written" | "code_change_proposed" | "run_finished" | "run_failed" => {
            ("evidence", "reproducible_evidence")
        }
        "code_change_applied" | "code_change_reverted" | "task_updated" => {
            ("feedback", "event_audit")
        }
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
pub struct EventGcSummary {
    pub policy_id: String,
    pub retain_last: usize,
    pub retained_event_count: usize,
    pub filtered_event_count: usize,
    pub retained_sequences: Vec<i64>,
    pub filtered_sequences: Vec<i64>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EventGcPreview {
    pub run: RunSummary,
    pub summary: EventGcSummary,
    pub retained_events: Vec<LocalEvent>,
    pub filtered_events: Vec<LocalEvent>,
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
pub struct LocalTaskRecord {
    pub task_id: String,
    pub workspace_id: String,
    pub run_id: Option<String>,
    pub title: String,
    pub status: String,
    pub priority: String,
    pub created_at_ms: i64,
    pub updated_at_ms: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProposalApplyResult {
    pub artifact: ArtifactRecord,
    pub backup_artifact: Option<ArtifactRecord>,
    pub target_path: String,
    pub target_existed: bool,
    pub applied: bool,
    pub dry_run: bool,
    pub added_lines: usize,
    pub bytes_written: u64,
    pub preview: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProposalRollbackResult {
    pub artifact: ArtifactRecord,
    pub backup_artifact: ArtifactRecord,
    pub target_path: String,
    pub restored: bool,
    pub target_existed: bool,
    pub bytes_written: u64,
    pub preview: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProposalReview {
    pub artifact: ArtifactRecord,
    pub target_path: String,
    pub target_exists: bool,
    pub new_file: bool,
    pub hunk_count: usize,
    pub added_lines: usize,
    pub removed_lines: usize,
    pub risk_level: String,
    pub can_apply: bool,
    pub dry_run_required: bool,
    pub checks: Vec<ProposalReviewCheck>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProposalReviewCheck {
    pub id: String,
    pub status: String,
    pub message: String,
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
    pub event_gc: EventGcSummary,
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
pub struct LocalRunCoreTrace {
    pub run: RunSummary,
    pub manifest_schema_version: String,
    pub core_aligned: bool,
    pub event_count: usize,
    pub flow_path: Vec<String>,
    pub primitive_path: Vec<String>,
    pub steps: Vec<LocalRunCoreTraceStep>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LocalRunCoreTraceStep {
    pub sequence: i64,
    pub kind: String,
    pub canonical_flow_id: String,
    pub primitive_id: String,
    pub payload_summary: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LocalRunPlan {
    pub run: RunSummary,
    pub status: String,
    pub step_count: usize,
    pub completed_step_count: usize,
    pub model_request_count: usize,
    pub tool_call_count: usize,
    pub steps: Vec<LocalRunPlanStep>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LocalRunPlanStep {
    pub sequence: i64,
    pub title: String,
    pub status: String,
    pub canonical_flow_id: String,
    pub primitive_id: String,
    pub iteration: Option<u64>,
    pub tool_call_count: usize,
    pub total_tool_results: usize,
    pub prompt_references: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LocalRunReview {
    pub run: RunSummary,
    pub status: String,
    pub core_aligned: bool,
    pub flow_path: Vec<String>,
    pub primitive_path: Vec<String>,
    pub event_count: usize,
    pub tool_call_count: usize,
    pub failed_tool_call_count: usize,
    pub model_usage: ModelUsageSummary,
    pub proposal_artifact: Option<ArtifactRecord>,
    pub response_artifact: Option<ArtifactRecord>,
    pub final_response_chars: usize,
    pub next_actions: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LocalRunStatusSnapshot {
    pub run: RunSummary,
    pub generated_at_ms: i64,
    pub terminal: bool,
    pub core_aligned: bool,
    pub event_count: usize,
    pub latest_event: Option<LocalRunStatusEvent>,
    pub model_usage: ModelUsageSummary,
    pub tool_call_count: usize,
    pub failed_tool_call_count: usize,
    pub pending_tool_call_count: usize,
    pub artifact_count: usize,
    pub response_artifact: Option<ArtifactRecord>,
    pub proposal_artifact: Option<ArtifactRecord>,
    pub latest_error: Option<String>,
    pub flow_path: Vec<String>,
    pub primitive_path: Vec<String>,
    pub next_actions: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LocalRunStatusEvent {
    pub sequence: i64,
    pub kind: String,
    pub canonical_flow_id: String,
    pub primitive_id: String,
    pub summary: String,
    pub created_at_ms: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LocalRunCompact {
    pub run: RunSummary,
    pub status: String,
    pub core_aligned: bool,
    pub event_count: usize,
    pub tool_call_count: usize,
    pub model_usage: ModelUsageSummary,
    pub artifact_paths: Vec<String>,
    pub summary: String,
    pub carry_forward_items: Vec<String>,
    pub next_actions: Vec<String>,
    pub continuation_context: String,
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
pub struct WorkspaceCompact {
    pub workspace_id: String,
    pub generated_at_ms: i64,
    pub event_count: usize,
    pub run_count: usize,
    pub knowledge_source_count: usize,
    pub artifact_count: usize,
    pub last_sequence: Option<i64>,
    pub core_aligned: bool,
    pub flow_path: Vec<String>,
    pub primitive_path: Vec<String>,
    pub recent_runs: Vec<WorkspaceCompactRun>,
    pub summary: String,
    pub carry_forward_items: Vec<String>,
    pub next_actions: Vec<String>,
    pub continuation_context: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct WorkspaceCompactRun {
    pub run_id: String,
    pub status: String,
    pub prompt_summary: String,
    pub response_summary: Option<String>,
    pub event_count: usize,
    pub tool_call_count: usize,
    pub model_usage: ModelUsageSummary,
    pub artifact_paths: Vec<String>,
    pub core_aligned: bool,
    pub updated_at_ms: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct WorkspaceUsageSummary {
    pub workspace_id: String,
    pub generated_at_ms: i64,
    pub run_count: usize,
    pub event_count: usize,
    pub tool_call_count: usize,
    pub artifact_count: usize,
    pub knowledge_source_count: usize,
    pub model_usage: ModelUsageSummary,
    pub core_aligned: bool,
    pub flow_path: Vec<String>,
    pub primitive_path: Vec<String>,
    pub runs: Vec<WorkspaceUsageRun>,
    pub summary: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct WorkspaceUsageRun {
    pub run_id: String,
    pub status: String,
    pub event_count: usize,
    pub tool_call_count: usize,
    pub artifact_count: usize,
    pub model_usage: ModelUsageSummary,
    pub core_aligned: bool,
    pub updated_at_ms: i64,
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

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LocalToolTraceEntry {
    pub call_id: String,
    pub name: String,
    pub requested_sequence: Option<i64>,
    pub completed_sequence: Option<i64>,
    pub input: serde_json::Value,
    pub success: Option<bool>,
    pub output: Option<serde_json::Value>,
    pub error: Option<String>,
}
