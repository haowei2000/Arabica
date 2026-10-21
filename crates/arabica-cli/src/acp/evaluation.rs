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

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub(super) struct PolicyActionRequest {
    pub session_id: SessionId,
    pub action: String,
    pub policy_id: Option<String>,
    pub version: Option<u64>,
    pub target_version: Option<u64>,
}

impl JsonRpcMessage for PolicyActionRequest {
    fn matches_method(method: &str) -> bool {
        method == "_arabica/evaluation/policy/action"
    }
    fn method(&self) -> &str {
        "_arabica/evaluation/policy/action"
    }
    fn to_untyped_message(&self) -> Result<UntypedMessage, Error> {
        UntypedMessage::new("_arabica/evaluation/policy/action", self)
    }
    fn parse_message(method: &str, params: &impl Serialize) -> Result<Self, Error> {
        if !Self::matches_method(method) {
            return Err(Error::method_not_found());
        }
        let value = serde_json::to_value(params).map_err(|_| Error::invalid_params())?;
        serde_json::from_value(value).map_err(|_| Error::invalid_params())
    }
}

impl JsonRpcRequest for PolicyActionRequest {
    type Response = serde_json::Value;
}

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

pub(super) async fn policy_action(
    state: &super::AcpState,
    req: &PolicyActionRequest,
) -> Result<serde_json::Value, Error> {
    let entry = state
        .entry(&req.session_id)
        .ok_or_else(|| Error::invalid_params().data("unknown session"))?;
    let home = &state.arabica_home;
    let mut db = arabica_adapters::SqliteEvaluationStore::open(&home.join("evaluation.sqlite3"))
        .map_err(|e| Error::internal_error().data(e.to_string()))?;

    match req.action.as_str() {
        "approve" => {
            let pid = req
                .policy_id
                .as_deref()
                .ok_or_else(|| Error::invalid_params().data("missing policyId"))?;
            let v = req
                .version
                .ok_or_else(|| Error::invalid_params().data("missing version"))?;
            db.approve_policy(pid, v)
                .map_err(|e| Error::internal_error().data(e.to_string()))?;
        }
        "reject" => {
            let pid = req
                .policy_id
                .as_deref()
                .ok_or_else(|| Error::invalid_params().data("missing policyId"))?;
            let v = req
                .version
                .ok_or_else(|| Error::invalid_params().data("missing version"))?;
            db.reject_policy(pid, v)
                .map_err(|e| Error::internal_error().data(e.to_string()))?;
        }
        "activate" => {
            let pid = req
                .policy_id
                .as_deref()
                .ok_or_else(|| Error::invalid_params().data("missing policyId"))?;
            let v = req
                .version
                .ok_or_else(|| Error::invalid_params().data("missing version"))?;
            db.activate_policy_version(pid, v)
                .map_err(|e| Error::internal_error().data(e.to_string()))?;
        }
        "rollback" => {
            let pid = req
                .policy_id
                .as_deref()
                .ok_or_else(|| Error::invalid_params().data("missing policyId"))?;
            db.rollback_policy(pid, req.target_version)
                .map_err(|e| Error::internal_error().data(e.to_string()))?;
        }
        "propose" => {
            let _ = crate::evaluation::run(crate::evaluation::EvaluationAction::Propose {
                session_id: Some(entry.arabica_session_id.0.clone()),
                policy_id: req.policy_id.clone(),
                workspace_id: Some(crate::host::workspace_id_for(&entry.cwd).0),
                json: false,
            })
            .await;
        }
        _ => return Err(Error::invalid_params().data("unsupported policy action")),
    }

    query(state, &req.session_id, false)
}
