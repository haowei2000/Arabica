//! Loop–Runner Protocol (LRP) wire types.
//!
//! LRP carries exactly what `RunnerEnvironment` exchanges in process between
//! an agent loop and a separate runner process: tool advertisement and
//! classification, typed tool calls, ordered stdout/stderr, results, and
//! cancellation. It never carries conversation history or model credentials.
//! See `docs/loop-runner-protocol.md` for the normative description.

use std::collections::BTreeMap;

use arabica_model::{ToolCallItem, ToolDefinition, ToolResultItem};
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

use crate::{RunId, SessionId, ToolInteractionKind, WorkspaceId};

/// Wire version carried in every frame and negotiated in the handshake.
pub const LRP_VERSION: &str = "lrp.v1";
/// WebSocket subprotocol both sides must negotiate.
pub const LRP_SUBPROTOCOL: &str = "arabica.lrp.v1";
/// The only dynamic classifier defined by `lrp.v1`, backed by the shell
/// classifier shipped in `arabica-runner`.
pub const SHELL_CLASSIFIER: &str = "shell";
/// Largest WebSocket message either side accepts.
pub const MAX_MESSAGE_BYTES: usize = 4 * 1024 * 1024;
/// Largest WebSocket frame either side accepts.
pub const MAX_FRAME_BYTES: usize = 1024 * 1024;
/// Optional feature a loop lists in `welcome.features` to receive
/// `tools.changed`. A runner never sends it to a loop that did not ask.
pub const FEATURE_TOOLS_CHANGED: &str = "tools.changed";

/// One WebSocket text frame. Requests carry `id` and, from the loop, `seq`;
/// responses carry `reply_to`; notifications carry neither.
#[derive(Clone, Debug, Deserialize, Eq, JsonSchema, PartialEq, Serialize)]
pub struct Frame {
    pub v: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub reply_to: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub seq: Option<u64>,
    #[serde(flatten)]
    pub message: Message,
}

impl Frame {
    pub fn request(id: impl Into<String>, seq: Option<u64>, message: Message) -> Self {
        Self {
            v: LRP_VERSION.to_owned(),
            id: Some(id.into()),
            reply_to: None,
            seq,
            message,
        }
    }

    pub fn reply(reply_to: impl Into<String>, message: Message) -> Self {
        Self {
            v: LRP_VERSION.to_owned(),
            id: None,
            reply_to: Some(reply_to.into()),
            seq: None,
            message,
        }
    }

    pub fn notification(message: Message) -> Self {
        Self {
            v: LRP_VERSION.to_owned(),
            id: None,
            reply_to: None,
            seq: None,
            message,
        }
    }
}

#[derive(Clone, Debug, Deserialize, Eq, JsonSchema, PartialEq, Serialize)]
#[serde(tag = "type", content = "body")]
pub enum Message {
    /// Runner → loop, first frame on every connection.
    #[serde(rename = "hello")]
    Hello(Hello),
    /// Loop → runner, reply to `hello`.
    #[serde(rename = "welcome")]
    Welcome(Welcome),
    /// Runner → loop notification: the runner's tool list changed.
    #[serde(rename = "tools.changed")]
    ToolsChanged(ToolsChanged),
    #[serde(rename = "lease.open")]
    LeaseOpen(LeaseOpen),
    #[serde(rename = "lease.opened")]
    LeaseOpened(LeaseOpened),
    #[serde(rename = "lease.close")]
    LeaseClose(LeaseClose),
    #[serde(rename = "lease.closed")]
    LeaseClosed(LeaseClose),
    #[serde(rename = "call.start")]
    CallStart(CallStart),
    #[serde(rename = "call.accepted")]
    CallAccepted(CallRef),
    #[serde(rename = "call.output")]
    CallOutput(CallOutput),
    #[serde(rename = "call.completed")]
    CallCompleted(CallCompleted),
    #[serde(rename = "call.failed")]
    CallFailed(CallFailed),
    #[serde(rename = "call.cancel")]
    CallCancel(CallCancel),
    #[serde(rename = "call.cancel_ack")]
    CallCancelAck(CallCancelAck),
    #[serde(rename = "heartbeat")]
    Heartbeat,
    /// Reply to a request that could not be served.
    #[serde(rename = "error")]
    Error(LrpError),
}

#[derive(Clone, Debug, Deserialize, Eq, JsonSchema, PartialEq, Serialize)]
pub struct Hello {
    pub runner_id: String,
    pub versions: Vec<String>,
    pub runner_version: String,
    #[serde(default)]
    pub labels: BTreeMap<String, String>,
    pub capacity: Capacity,
    pub workspaces: Vec<WorkspaceId>,
    pub tools: Vec<RemoteTool>,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, JsonSchema, PartialEq, Serialize)]
pub struct Capacity {
    pub max_concurrent_calls: u32,
}

/// One tool a runner offers, with how the loop must classify its calls.
#[derive(Clone, Debug, Deserialize, Eq, JsonSchema, PartialEq, Serialize)]
pub struct RemoteTool {
    pub definition: ToolDefinition,
    pub classification: ToolClassification,
}

#[derive(Clone, Debug, Deserialize, Eq, JsonSchema, PartialEq, Serialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum ToolClassification {
    /// Every call maps to one interaction kind.
    Static { interaction: ToolInteractionKind },
    /// Calls are classified on the loop by a named pure classifier. A
    /// classifier the loop does not know classifies as `generic`.
    Dynamic { classifier: String },
}

#[derive(Clone, Debug, Deserialize, Eq, JsonSchema, PartialEq, Serialize)]
pub struct Welcome {
    pub version: String,
    pub connection_id: String,
    pub heartbeat_interval_ms: u64,
    pub heartbeat_timeout_ms: u64,
    /// Optional features this loop understands, such as
    /// [`FEATURE_TOOLS_CHANGED`].
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub features: Vec<String>,
}

/// Replaces the tool list from `hello` and every open lease's effective
/// tools on this connection. Calls already accepted keep running.
#[derive(Clone, Debug, Deserialize, Eq, JsonSchema, PartialEq, Serialize)]
pub struct ToolsChanged {
    /// Every tool the runner now offers, as `hello.tools` would list it.
    pub tools: Vec<RemoteTool>,
    /// The effective tool names of each open lease, keyed by `lease_id`.
    pub leases: BTreeMap<String, Vec<String>>,
}

/// Lease modes narrow a runner's policy; they never widen it.
#[derive(Clone, Copy, Debug, Default, Deserialize, Eq, JsonSchema, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum LeaseMode {
    #[default]
    Coding,
    ReadOnly,
}

#[derive(Clone, Debug, Deserialize, Eq, JsonSchema, PartialEq, Serialize)]
pub struct LeaseOpen {
    pub lease_id: String,
    pub session_id: Option<SessionId>,
    pub workspace_id: WorkspaceId,
    #[serde(default)]
    pub mode: LeaseMode,
    /// Tools the loop wants; `None` asks for every tool the mode allows.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub tools: Option<Vec<String>>,
}

#[derive(Clone, Debug, Deserialize, Eq, JsonSchema, PartialEq, Serialize)]
pub struct LeaseOpened {
    pub lease_id: String,
    /// The effective tool names for this lease.
    pub tools: Vec<String>,
}

#[derive(Clone, Debug, Deserialize, Eq, JsonSchema, PartialEq, Serialize)]
pub struct LeaseClose {
    pub lease_id: String,
}

#[derive(Clone, Debug, Deserialize, Eq, JsonSchema, PartialEq, Serialize)]
pub struct CallStart {
    pub lease_id: String,
    pub run_id: RunId,
    pub call: ToolCallItem,
    pub deadline_ms: u64,
}

#[derive(Clone, Debug, Deserialize, Eq, JsonSchema, PartialEq, Serialize)]
pub struct CallRef {
    pub call_id: String,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, JsonSchema, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum OutputStream {
    Stdout,
    Stderr,
}

#[derive(Clone, Debug, Deserialize, Eq, JsonSchema, PartialEq, Serialize)]
pub struct CallOutput {
    pub call_id: String,
    /// Strictly increasing per call, starting at zero.
    pub chunk: u64,
    pub stream: OutputStream,
    pub text: String,
}

#[derive(Clone, Debug, Deserialize, Eq, JsonSchema, PartialEq, Serialize)]
pub struct CallCompleted {
    pub call_id: String,
    pub result: ToolResultItem,
}

#[derive(Clone, Debug, Deserialize, Eq, JsonSchema, PartialEq, Serialize)]
pub struct CallFailed {
    pub call_id: String,
    pub error: LrpError,
}

/// Cancels one call, or every call of a run when `call_id` is absent.
#[derive(Clone, Debug, Deserialize, Eq, JsonSchema, PartialEq, Serialize)]
pub struct CallCancel {
    pub run_id: RunId,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub call_id: Option<String>,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, JsonSchema, PartialEq, Serialize)]
pub struct CallCancelAck {
    pub cancelled: bool,
}

#[derive(Clone, Debug, Deserialize, Eq, JsonSchema, PartialEq, Serialize)]
pub struct LrpError {
    pub code: LrpErrorCode,
    pub message: String,
    #[serde(default)]
    pub retryable: bool,
}

impl LrpError {
    pub fn new(code: LrpErrorCode, message: impl Into<String>) -> Self {
        Self {
            retryable: matches!(code, LrpErrorCode::CapacityExhausted),
            code,
            message: message.into(),
        }
    }
}

impl std::fmt::Display for LrpError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(formatter, "{}: {}", self.code.as_str(), self.message)
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, JsonSchema, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum LrpErrorCode {
    Unauthenticated,
    Forbidden,
    VersionUnsupported,
    ProtocolError,
    WorkspaceUnknown,
    ToolUnavailable,
    CapacityExhausted,
    DeadlineExceeded,
    Cancelled,
    Conflict,
    RunnerLost,
    Internal,
    /// A code from a newer protocol revision.
    #[serde(other)]
    Unknown,
}

impl LrpErrorCode {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Unauthenticated => "unauthenticated",
            Self::Forbidden => "forbidden",
            Self::VersionUnsupported => "version_unsupported",
            Self::ProtocolError => "protocol_error",
            Self::WorkspaceUnknown => "workspace_unknown",
            Self::ToolUnavailable => "tool_unavailable",
            Self::CapacityExhausted => "capacity_exhausted",
            Self::DeadlineExceeded => "deadline_exceeded",
            Self::Cancelled => "cancelled",
            Self::Conflict => "conflict",
            Self::RunnerLost => "runner_lost",
            Self::Internal => "internal",
            Self::Unknown => "unknown",
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn a_request_frame_matches_the_documented_envelope() {
        let frame = Frame::request(
            "m-42",
            Some(42),
            Message::CallCancel(CallCancel {
                run_id: RunId::new("r-1"),
                call_id: Some("call_abc".to_owned()),
            }),
        );
        let value = serde_json::to_value(&frame).expect("frame serializes");
        assert_eq!(
            value,
            json!({
                "v": "lrp.v1",
                "id": "m-42",
                "seq": 42,
                "type": "call.cancel",
                "body": { "run_id": "r-1", "call_id": "call_abc" }
            })
        );
        let back: Frame = serde_json::from_value(value).expect("frame parses");
        assert_eq!(back, frame);
    }

    #[test]
    fn unit_messages_and_replies_round_trip() {
        let heartbeat = serde_json::to_value(Frame::notification(Message::Heartbeat)).unwrap();
        assert_eq!(heartbeat, json!({ "v": "lrp.v1", "type": "heartbeat" }));
        let parsed: Frame = serde_json::from_value(heartbeat).unwrap();
        assert_eq!(parsed.message, Message::Heartbeat);

        let reply = Frame::reply(
            "m-1",
            Message::Error(LrpError::new(LrpErrorCode::CapacityExhausted, "busy")),
        );
        let value = serde_json::to_value(&reply).unwrap();
        assert_eq!(value["reply_to"], "m-1");
        assert_eq!(value["body"]["code"], "capacity_exhausted");
        assert_eq!(value["body"]["retryable"], true);
    }

    #[test]
    fn unknown_error_codes_and_fields_are_tolerated() {
        let frame: Frame = serde_json::from_value(json!({
            "v": "lrp.v1",
            "reply_to": "m-3",
            "type": "error",
            "body": { "code": "quota_exceeded", "message": "later", "extra": 1 }
        }))
        .expect("newer error code parses");
        let Message::Error(error) = frame.message else {
            panic!("expected an error");
        };
        assert_eq!(error.code, LrpErrorCode::Unknown);
        assert!(!error.retryable);
    }

    #[test]
    fn tool_classification_is_tagged_by_kind() {
        let dynamic = serde_json::to_value(ToolClassification::Dynamic {
            classifier: SHELL_CLASSIFIER.to_owned(),
        })
        .unwrap();
        assert_eq!(dynamic, json!({ "kind": "dynamic", "classifier": "shell" }));
        let fixed = serde_json::to_value(ToolClassification::Static {
            interaction: ToolInteractionKind::Inspection,
        })
        .unwrap();
        assert_eq!(
            fixed,
            json!({ "kind": "static", "interaction": "inspection" })
        );
    }

    #[test]
    fn tools_changed_and_welcome_features_round_trip() {
        let welcome: Message = serde_json::from_value(json!({
            "type": "welcome",
            "body": {
                "version": "lrp.v1",
                "connection_id": "c-1",
                "heartbeat_interval_ms": 15000,
                "heartbeat_timeout_ms": 45000
            }
        }))
        .expect("a welcome without features parses");
        let Message::Welcome(welcome) = welcome else {
            panic!("expected a welcome");
        };
        assert!(welcome.features.is_empty());

        let changed = Frame::notification(Message::ToolsChanged(ToolsChanged {
            tools: Vec::new(),
            leases: BTreeMap::from([("l-1".to_owned(), vec!["read_file".to_owned()])]),
        }));
        let value = serde_json::to_value(&changed).unwrap();
        assert_eq!(value["type"], FEATURE_TOOLS_CHANGED);
        assert_eq!(value["body"]["leases"]["l-1"], json!(["read_file"]));
        assert_eq!(serde_json::from_value::<Frame>(value).unwrap(), changed);
    }

    #[test]
    fn unknown_message_types_are_rejected() {
        let parsed = serde_json::from_value::<Frame>(json!({
            "v": "lrp.v1",
            "type": "runner.reboot",
            "body": {}
        }));
        assert!(parsed.is_err());
    }
}
