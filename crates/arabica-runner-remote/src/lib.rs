//! Loop–Runner Protocol (LRP) transport.
//!
//! [`RemoteRunner`] is the loop side: a `RunnerEnvironment` whose tool calls
//! execute on another machine. [`RunnerHost`] is the runner side: it serves
//! a confined `LocalRunner` to authenticated loops. Both speak `lrp.v1` JSON
//! frames over one WebSocket on mutually authenticated TLS 1.3; see
//! `docs/loop-runner-protocol.md`.

pub mod client;
pub mod server;
pub mod tls;

pub use client::{RemoteRunner, RemoteRunnerConfig};
pub use server::{RunnerHost, RunnerHostConfig};
pub use tls::TlsIdentity;

use std::time::Duration;

use arabica_protocol::lrp::{Frame, LRP_SUBPROTOCOL, MAX_FRAME_BYTES, MAX_MESSAGE_BYTES};
use arabica_runner::RunnerError;
use tokio_tungstenite::tungstenite::Message as WsMessage;
use tokio_tungstenite::tungstenite::http::{HeaderMap, header};
use tokio_tungstenite::tungstenite::protocol::WebSocketConfig;

/// Bound on each handshake step: TLS, WebSocket upgrade, hello/welcome.
pub(crate) const HANDSHAKE_TIMEOUT: Duration = Duration::from_secs(10);

pub(crate) fn ws_config() -> WebSocketConfig {
    WebSocketConfig::default()
        .max_message_size(Some(MAX_MESSAGE_BYTES))
        .max_frame_size(Some(MAX_FRAME_BYTES))
}

pub(crate) fn offers_subprotocol(headers: &HeaderMap) -> bool {
    headers
        .get_all(header::SEC_WEBSOCKET_PROTOCOL)
        .iter()
        .filter_map(|value| value.to_str().ok())
        .flat_map(|value| value.split(','))
        .any(|protocol| protocol.trim() == LRP_SUBPROTOCOL)
}

pub(crate) fn encode(frame: &Frame) -> Result<WsMessage, RunnerError> {
    serde_json::to_string(frame)
        .map(WsMessage::text)
        .map_err(|error| RunnerError::new(format!("cannot encode frame: {error}")))
}

pub(crate) fn decode(text: &str) -> Result<Frame, RunnerError> {
    serde_json::from_str(text)
        .map_err(|error| RunnerError::new(format!("malformed frame: {error}")))
}
