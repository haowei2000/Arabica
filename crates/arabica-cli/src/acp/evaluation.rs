//! Typed ACP extensions consumed by desktop/editor clients. No session lock.

use agent_client_protocol::schema::v1::SessionId;
use agent_client_protocol::{Error, JsonRpcMessage, JsonRpcRequest, UntypedMessage};
use serde::{Deserialize, Serialize};

macro_rules! request {
    ($name:ident, $method:literal) => {
        #[derive(Clone, Debug, Serialize, Deserialize)]
        #[serde(rename_all = "camelCase", deny_unknown_fields)]
        pub(super) struct $name {
            pub session_id: SessionId,
        }
        impl JsonRpcMessage for $name {
            fn matches_method(method: &str) -> bool {
                method == $method
            }
            fn method(&self) -> &str {
                $method
            }
            fn to_untyped_message(&self) -> Result<UntypedMessage, Error> {
                UntypedMessage::new($method, self)
            }
            fn parse_message(method: &str, params: &impl Serialize) -> Result<Self, Error> {
                if !Self::matches_method(method) {
                    return Err(Error::method_not_found());
                }
                let value = serde_json::to_value(params).map_err(|_| Error::invalid_params())?;
                serde_json::from_value(value).map_err(|_| Error::invalid_params())
            }
        }
        impl JsonRpcRequest for $name {
            type Response = serde_json::Value;
        }
    };
}
request!(GetEvaluationRequest, "_arabica/evaluation/get");
request!(RefreshEvaluationRequest, "_arabica/evaluation/refresh");

pub(super) fn query(
    state: &super::AcpState,
    session: &SessionId,
    refresh: bool,
) -> Result<serde_json::Value, Error> {
    let entry = state
        .entry(session)
        .ok_or_else(|| Error::invalid_params().data("unknown session"))?;
    let view = crate::evaluation::view(
        &state.arabica_home,
        crate::host::workspace_id_for(&entry.cwd),
        entry.arabica_session_id.clone(),
        refresh,
    );
    serde_json::to_value(view).map_err(|_| Error::internal_error())
}
