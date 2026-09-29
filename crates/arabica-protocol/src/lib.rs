//! Canonical wire contract for Structure.
//!
//! Every UI sends [`CommandEnvelope`] values and observes [`EventEnvelope`]
//! values. Runtime, session, and runner modules must not invent parallel wire
//! types.

use arabica_model::{
    FinishReason, ProviderResponseState, ProviderState, RuntimeItem, RuntimeRequest, RuntimeUsage,
};
use schemars::{JsonSchema, Schema, schema_for};
use serde::{Deserialize, Serialize};

pub const PROTOCOL_VERSION: &str = "1.0";

macro_rules! string_id {
    ($name:ident) => {
        #[derive(
            Clone, Debug, Deserialize, Eq, Hash, JsonSchema, Ord, PartialEq, PartialOrd, Serialize,
        )]
        #[serde(transparent)]
        pub struct $name(pub String);

        impl $name {
            pub fn new(value: impl Into<String>) -> Self {
                Self(value.into())
            }
        }

        impl std::fmt::Display for $name {
            fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
                self.0.fmt(formatter)
            }
        }
    };
}

string_id!(CommandId);
string_id!(EventId);
string_id!(SessionId);
string_id!(RunId);
string_id!(WorkspaceId);

#[derive(Clone, Debug, Deserialize, JsonSchema, PartialEq, Serialize)]
pub struct CommandEnvelope {
    pub protocol_version: String,
    pub command_id: CommandId,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub session_id: Option<SessionId>,
    pub command: Command,
}

impl CommandEnvelope {
    pub fn new(command_id: CommandId, session_id: Option<SessionId>, command: Command) -> Self {
        Self {
            protocol_version: PROTOCOL_VERSION.to_owned(),
            command_id,
            session_id,
            command,
        }
    }
}

#[derive(Clone, Debug, Deserialize, JsonSchema, PartialEq, Serialize)]
#[serde(tag = "type", content = "payload")]
pub enum Command {
    #[serde(rename = "session.create")]
    SessionCreate { workspace_id: WorkspaceId },
    #[serde(rename = "session.fork")]
    SessionFork { source_session_id: SessionId },
    #[serde(rename = "session.resume")]
    SessionResume,
    #[serde(rename = "session.suspend")]
    SessionSuspend,
    #[serde(rename = "session.close")]
    SessionClose,
    #[serde(rename = "message.send")]
    MessageSend { content: String },
    #[serde(rename = "run.cancel")]
    RunCancel { run_id: RunId },
    #[serde(rename = "context.read")]
    ContextRead { path: String },
    #[serde(rename = "context.search")]
    ContextSearch { query: String },
    #[serde(rename = "context.update")]
    ContextUpdate { path: String, content: String },
    #[serde(rename = "context.delete")]
    ContextDelete { path: String },
    #[serde(rename = "context.set_disclosure")]
    ContextSetDisclosure { level: DisclosureLevel },
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, JsonSchema, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum DisclosureLevel {
    Glance,
    Overview,
    Detail,
}

#[derive(Clone, Debug, Deserialize, Eq, JsonSchema, PartialEq, Serialize)]
pub struct ContextEntry {
    pub path: String,
    pub content: String,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, JsonSchema, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum OutputStream {
    Stdout,
    Stderr,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, JsonSchema, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum SessionStatus {
    Active,
    Suspended,
    Closed,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, JsonSchema, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum RunStatus {
    Pending,
    Running,
    WaitingForTool,
    Finished,
    Failed,
    Cancelled,
}

/// Provider- and runner-neutral retention semantics for one tool interaction.
/// Concrete runners classify their own tools; Runtime memory policy consumes
/// this typed event and never parses tool arguments.
#[derive(Clone, Copy, Debug, Default, Deserialize, Eq, JsonSchema, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ToolInteractionKind {
    Inspection,
    Mutation,
    /// A single ordered runner action that changes state and then validates
    /// the resulting state before returning successfully.
    MutationWithValidation,
    Build,
    Dependency,
    Validation,
    #[default]
    Generic,
}

/// Whether a tool call was allowed to run.
#[derive(Clone, Copy, Debug, Deserialize, Eq, JsonSchema, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ToolPermissionOutcome {
    Allowed,
    Denied,
    /// The run was cancelled while the decision was pending.
    Cancelled,
}

/// How long a permission decision applies.
#[derive(Clone, Copy, Debug, Deserialize, Eq, JsonSchema, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ToolPermissionScope {
    /// This call only.
    Once,
    /// Every later call to the same tool in this Session.
    Session,
}

/// Who or what produced a permission decision.
#[derive(Clone, Copy, Debug, Deserialize, Eq, JsonSchema, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ToolPermissionSource {
    /// The host's configured policy, without asking anyone.
    Policy,
    /// A person, through the host's approver.
    User,
    /// An earlier `Session`-scoped decision for the same tool.
    SessionRule,
    /// No approver was configured or reachable, so the gate failed closed.
    ApproverUnavailable,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, JsonSchema, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ModelResponseRejectionReason {
    OutputLength,
    ContentFilter,
    ToolCallsWithoutItem,
    StopWithToolCall,
    EmptyOutput,
    TerminalControllerViolation,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, JsonSchema, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum AgentLoopTerminationReason {
    NoStateProgress,
    ModelStepLimit,
}

/// Runtime-owned terminal control policy, recorded in canonical Events so
/// experiments can prove that memory arms used identical control.
#[derive(Clone, Copy, Debug, Default, Deserialize, Eq, JsonSchema, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum TerminalControllerPolicy {
    #[default]
    AdvisoryV18,
    TypedCompletionV1,
    /// Strict typed completion for Providers that only guarantee
    /// `tool_choice=auto`. Runtime exposes only `runtime_complete` and adds a
    /// fixed control message, but never treats the call as Provider-forced.
    TypedCompletionAutoV1,
    /// Completion for Providers that only support `tool_choice=auto` and may
    /// emit assistant text beside one valid function call. The tool call is
    /// authoritative; co-emitted text is retained as evidence and ignored.
    TypedCompletionAutoV2,
}

impl TerminalControllerPolicy {
    pub const fn is_typed(self) -> bool {
        matches!(
            self,
            Self::TypedCompletionV1 | Self::TypedCompletionAutoV1 | Self::TypedCompletionAutoV2
        )
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, JsonSchema, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum TerminalControllerState {
    Working,
    CompletionRequired,
    Completed,
    Failed,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, JsonSchema, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum TerminalControllerTransitionReason {
    ValidationSucceeded,
    StateProgress,
    ToolError,
    ProviderError,
    DeterministicHardStop,
    CompletionAccepted,
    CompletionViolation,
}

#[derive(Clone, Debug, Deserialize, JsonSchema, PartialEq, Serialize)]
#[serde(tag = "type", content = "payload")]
pub enum Event {
    #[serde(rename = "session.created")]
    SessionCreated { workspace_id: WorkspaceId },
    #[serde(rename = "session.forked")]
    SessionForked { source_session_id: SessionId },
    #[serde(rename = "session.resumed")]
    SessionResumed,
    #[serde(rename = "session.suspended")]
    SessionSuspended,
    #[serde(rename = "session.closed")]
    SessionClosed,
    #[serde(rename = "run.scheduled")]
    RunScheduled,
    #[serde(rename = "run.started")]
    RunStarted,
    #[serde(rename = "message.accepted")]
    MessageAccepted { content: String },
    /// Model selected for one call, recorded before provider invocation.
    /// Contains only a public model identifier and bounded routing metadata.
    #[serde(rename = "model.route.selected")]
    ModelRouteSelected {
        model_step: usize,
        decision_id: String,
        policy_id: String,
        policy_version: u64,
        /// Content hash of the effective policy pinned for this run.
        #[serde(default)]
        policy_fingerprint: String,
        /// Stable aliases and model IDs; excludes credentials and endpoints.
        #[serde(default)]
        model_registry_snapshot: String,
        model_alias: Option<String>,
        reason: String,
    },
    /// Immediate call-level measurement. Run quality is evaluated separately
    /// after downstream tool outcomes or terminal task evidence are available.
    #[serde(rename = "model.call.observed")]
    ModelCallObserved {
        model_step: usize,
        decision_id: String,
        elapsed_ms: u64,
        provider_succeeded: bool,
        #[serde(default)]
        outcome: ModelCallOutcome,
        usage: Option<RuntimeUsage>,
    },
    /// Exact provider-neutral request immediately before wire encoding.
    #[serde(rename = "model.request.prepared")]
    ModelRequestPrepared {
        model_step: usize,
        request: RuntimeRequest,
    },
    /// One exact ordered item from a provider-neutral model response.
    #[serde(rename = "model.response.item")]
    ModelResponseItem {
        model_step: usize,
        item_index: usize,
        item: RuntimeItem,
    },
    /// Completion metadata for the preceding ordered response items.
    #[serde(rename = "model.response.completed")]
    ModelResponseCompleted {
        model_step: usize,
        finish_reason: Option<FinishReason>,
        usage: RuntimeUsage,
        /// Exact provider envelope. This remains private runtime evidence and
        /// is not required to participate in short-memory projection.
        #[serde(default, skip_serializing_if = "Option::is_none")]
        provider_state: Option<ProviderResponseState>,
    },
    /// Typed audit evidence explaining why an otherwise losslessly recorded
    /// provider response could not be executed or accepted as terminal.
    #[serde(rename = "model.response.rejected")]
    ModelResponseRejected {
        model_step: usize,
        reason: ModelResponseRejectionReason,
        finish_reason: Option<FinishReason>,
        tool_call_count: usize,
        final_output_present: bool,
    },
    /// Additive audit evidence for the narrow normalization permitted by
    /// `TypedCompletionAutoV2`. The structured completion call remains the
    /// only source of terminal success.
    #[serde(rename = "model.response.normalized")]
    ModelResponseNormalized {
        model_step: usize,
        policy: TerminalControllerPolicy,
        ignored_assistant_text: bool,
    },
    #[serde(rename = "tool.call.requested")]
    ToolCallRequested {
        call_id: String,
        name: String,
        arguments: serde_json::Value,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        provider_state: Option<ProviderState>,
    },
    #[serde(rename = "tool.call.classified")]
    ToolCallClassified {
        call_id: String,
        kind: ToolInteractionKind,
    },
    /// Runtime is asking the host's approver whether this call may run. The
    /// tool name and arguments are in the matching `tool.call.requested`.
    #[serde(rename = "tool.call.permission_requested")]
    ToolCallPermissionRequested { call_id: String },
    /// The decision for one call. It follows `tool.call.permission_requested`
    /// when someone was asked, and stands alone when policy decided directly.
    /// Policy is keyed by tool name, never by `tool.call.classified`.
    #[serde(rename = "tool.call.permission_resolved")]
    ToolCallPermissionResolved {
        call_id: String,
        outcome: ToolPermissionOutcome,
        scope: ToolPermissionScope,
        source: ToolPermissionSource,
    },
    #[serde(rename = "tool.call.reused")]
    ToolCallReused {
        call_id: String,
        source_call_id: String,
        fingerprint: String,
        repeat_count: usize,
    },
    #[serde(rename = "tool.call.loop_blocked")]
    ToolCallLoopBlocked {
        call_id: String,
        fingerprint: String,
        repeat_count: usize,
    },
    #[serde(rename = "tool.call.completed")]
    ToolCallCompleted {
        call_id: String,
        name: String,
        result: String,
        is_error: bool,
    },
    #[serde(rename = "agent.progress.advisory")]
    AgentProgressAdvisory {
        model_step: usize,
        consecutive_no_progress_steps: usize,
        message: String,
    },
    #[serde(rename = "agent.loop.terminated")]
    AgentLoopTerminated {
        model_step: usize,
        reason: AgentLoopTerminationReason,
        consecutive_no_progress_steps: usize,
    },
    /// Auditable Runtime terminal-controller state. This event is derived only
    /// from typed Provider and Runner outcomes, never an external verifier.
    #[serde(rename = "terminal.control.transition")]
    TerminalControlTransition {
        model_step: usize,
        policy: TerminalControllerPolicy,
        from: TerminalControllerState,
        to: TerminalControllerState,
        reason: TerminalControllerTransitionReason,
    },
    #[serde(rename = "command.output")]
    CommandOutput { stream: OutputStream, chunk: String },
    #[serde(rename = "run.completed")]
    RunCompleted { output: Option<String> },
    #[serde(rename = "run.failed")]
    RunFailed { message: String },
    #[serde(rename = "run.cancelled")]
    RunCancelled,
    #[serde(rename = "context.read")]
    ContextRead { entry: ContextEntry },
    #[serde(rename = "context.search.result")]
    ContextSearchResult { entries: Vec<ContextEntry> },
    #[serde(rename = "context.updated")]
    ContextUpdated { entry: ContextEntry },
    #[serde(rename = "context.deleted")]
    ContextDeleted { path: String },
    #[serde(rename = "context.disclosure.set")]
    ContextDisclosureSet { level: DisclosureLevel },
    #[serde(rename = "error")]
    Error { code: ErrorCode, message: String },
}

#[derive(Clone, Copy, Debug, Default, Deserialize, Eq, JsonSchema, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ModelCallOutcome {
    Succeeded,
    Failed,
    Cancelled,
    #[default]
    Unknown,
}

impl Event {
    /// Model exchange events are canonical audit/runtime facts but can contain
    /// system prompts, disclosed memory, and provider reasoning state. They
    /// must not cross the ordinary client event transport boundary.
    pub const fn is_client_visible(&self) -> bool {
        !matches!(
            self,
            Self::ModelRequestPrepared { .. }
                | Self::ModelRouteSelected { .. }
                | Self::ModelCallObserved { .. }
                | Self::ModelResponseItem { .. }
                | Self::ModelResponseCompleted { .. }
                | Self::ModelResponseRejected { .. }
                | Self::ModelResponseNormalized { .. }
        )
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, JsonSchema, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ErrorCode {
    InvalidCommand,
    ProtocolVersionMismatch,
    SessionNotFound,
    InvalidSessionState,
    RunNotFound,
    RuntimeFailure,
    RunnerFailure,
    /// A restore snapshot's Events do not form a session that can be
    /// reconstructed: a non-contiguous sequence, or a forked session (Phase
    /// 2 restores only sessions with no parent).
    InvalidSnapshot,
}

#[derive(Clone, Debug, Deserialize, JsonSchema, PartialEq, Serialize)]
pub struct EventEnvelope {
    pub protocol_version: String,
    pub event_id: EventId,
    pub command_id: CommandId,
    pub workspace_id: WorkspaceId,
    pub session_id: SessionId,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub run_id: Option<RunId>,
    pub sequence: u64,
    pub occurred_at_ms: u64,
    pub event: Event,
}

#[derive(Clone, Debug, Deserialize, Eq, JsonSchema, PartialEq, Serialize)]
pub struct EventMetadata {
    pub event_id: EventId,
    pub command_id: CommandId,
    pub workspace_id: WorkspaceId,
    pub session_id: SessionId,
    pub run_id: Option<RunId>,
    pub sequence: u64,
    pub occurred_at_ms: u64,
}

/// A command rejected before it can produce a session-scoped event.
#[derive(Clone, Debug, Deserialize, Eq, JsonSchema, PartialEq, Serialize)]
pub struct CommandFailure {
    pub protocol_version: String,
    pub command_id: CommandId,
    pub code: ErrorCode,
    pub message: String,
}

impl CommandFailure {
    pub fn new(command_id: CommandId, code: ErrorCode, message: impl Into<String>) -> Self {
        Self {
            protocol_version: PROTOCOL_VERSION.to_owned(),
            command_id,
            code,
            message: message.into(),
        }
    }
}

#[derive(JsonSchema)]
pub struct ProtocolSchemaDocument {
    pub command: CommandEnvelope,
    pub event: EventEnvelope,
    pub failure: CommandFailure,
}

/// Export the complete protocol schema used by code generators and hosts.
pub fn protocol_schema() -> Schema {
    schema_for!(ProtocolSchemaDocument)
}

impl EventEnvelope {
    pub fn new(metadata: EventMetadata, event: Event) -> Self {
        Self {
            protocol_version: PROTOCOL_VERSION.to_owned(),
            event_id: metadata.event_id,
            command_id: metadata.command_id,
            workspace_id: metadata.workspace_id,
            session_id: metadata.session_id,
            run_id: metadata.run_id,
            sequence: metadata.sequence,
            occurred_at_ms: metadata.occurred_at_ms,
            event,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn command_wire_shape_is_stable() {
        let command = CommandEnvelope::new(
            CommandId::new("command-1"),
            Some(SessionId::new("session-1")),
            Command::MessageSend {
                content: "hello".to_owned(),
            },
        );

        let value = serde_json::to_value(&command).expect("command serializes");
        assert_eq!(value["protocol_version"], "1.0");
        assert_eq!(value["command"]["type"], "message.send");
        assert_eq!(value["command"]["payload"]["content"], "hello");
        assert_eq!(
            serde_json::from_value::<CommandEnvelope>(value).expect("command deserializes"),
            command
        );
    }

    #[test]
    fn blend_events_deserialize_older_records_with_unknown_new_fields() {
        let route: Event = serde_json::from_value(serde_json::json!({
            "type": "model.route.selected",
            "payload": {
                "model_step": 1,
                "decision_id": "run:1",
                "policy_id": "legacy",
                "policy_version": 1,
                "model_alias": "fast",
                "reason": "default"
            }
        }))
        .expect("legacy route event deserializes");
        assert!(
            matches!(route, Event::ModelRouteSelected { policy_fingerprint, model_registry_snapshot, .. }
            if policy_fingerprint.is_empty() && model_registry_snapshot.is_empty())
        );

        let observed: Event = serde_json::from_value(serde_json::json!({
            "type": "model.call.observed",
            "payload": {
                "model_step": 1,
                "decision_id": "run:1",
                "elapsed_ms": 9,
                "provider_succeeded": true,
                "usage": null
            }
        }))
        .expect("legacy observation event deserializes");
        assert!(matches!(
            observed,
            Event::ModelCallObserved {
                outcome: ModelCallOutcome::Unknown,
                usage: None,
                ..
            }
        ));
    }

    #[test]
    fn event_carries_causation_and_ordering_metadata() {
        let event = EventEnvelope::new(
            EventMetadata {
                event_id: EventId::new("event-1"),
                command_id: CommandId::new("command-1"),
                workspace_id: WorkspaceId::new("workspace-1"),
                session_id: SessionId::new("session-1"),
                run_id: Some(RunId::new("run-1")),
                sequence: 3,
                occurred_at_ms: 1_000,
            },
            Event::CommandOutput {
                stream: OutputStream::Stdout,
                chunk: "done".to_owned(),
            },
        );

        let value = serde_json::to_value(event).expect("event serializes");
        assert_eq!(value["sequence"], 3);
        assert_eq!(value["event_id"], "event-1");
        assert_eq!(value["occurred_at_ms"], 1_000);
        assert_eq!(value["event"]["type"], "command.output");
    }
}
