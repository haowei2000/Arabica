//! Bridges Structure's runtime-owned permission gate to ACP's
//! `session/request_permission`.
//!
//! The gate (`arabica_runtime::control`) hands every `Ask`-routed call to
//! an `mpsc` channel and waits on a `oneshot` reply, racing that wait against
//! the run's own cancellation -- see `crates/arabica-runtime/src/lib.rs`'s
//! `request_permission`. This module is the task that drains that channel
//! for one run and turns each request into a real `session/request_permission`
//! round trip with the ACP client.

use std::collections::BTreeMap;
use std::path::PathBuf;

use agent_client_protocol::schema::v1::{
    PermissionOption, PermissionOptionId, PermissionOptionKind, RequestPermissionOutcome,
    RequestPermissionRequest, SessionId as AcpSessionId, ToolCallUpdateFields,
};
use agent_client_protocol::{Client, ConnectionTo};
use arabica_runtime::{
    PermissionDecision, PermissionRequest, ToolPermissionPolicy, ToolPermissionRule,
};

use super::mapping::{acp_tool_call_id, diff_content, tool_call_title, tool_kind};

mod option_id {
    pub const ALLOW_ONCE: &str = "allow_once";
    pub const ALLOW_ALWAYS: &str = "allow_always";
    pub const REJECT_ONCE: &str = "reject_once";
    pub const REJECT_ALWAYS: &str = "reject_always";
}

/// Read tools stay silently allowed; anything that writes, deletes, or runs
/// a command asks. Matches the ACP surface design note in
/// `docs/runtime_core_architecture.md` §9: Zed would otherwise pop a dialog
/// for every exploratory `read_file` a coding turn makes.
pub fn default_policy() -> ToolPermissionPolicy {
    let mut by_tool = BTreeMap::new();
    for tool in [
        "read_file",
        "list_dir",
        "grep",
        "find_files",
        "memory_search",
        "memory_read",
    ] {
        by_tool.insert(tool.to_owned(), ToolPermissionRule::Allow);
    }
    ToolPermissionPolicy {
        default: ToolPermissionRule::Ask,
        by_tool,
        argument_rules: Vec::new(),
    }
}

fn permission_options() -> Vec<PermissionOption> {
    vec![
        PermissionOption::new(
            option_id::ALLOW_ONCE,
            "Allow",
            PermissionOptionKind::AllowOnce,
        ),
        PermissionOption::new(
            option_id::ALLOW_ALWAYS,
            "Allow Always",
            PermissionOptionKind::AllowAlways,
        ),
        PermissionOption::new(
            option_id::REJECT_ONCE,
            "Reject",
            PermissionOptionKind::RejectOnce,
        ),
        PermissionOption::new(
            option_id::REJECT_ALWAYS,
            "Reject Always",
            PermissionOptionKind::RejectAlways,
        ),
    ]
}

fn decision_for(option_id: &PermissionOptionId) -> PermissionDecision {
    match option_id.to_string().as_str() {
        option_id::ALLOW_ONCE => PermissionDecision::allow_once(),
        option_id::ALLOW_ALWAYS => PermissionDecision::allow_for_session(),
        option_id::REJECT_ONCE => PermissionDecision::deny_once(),
        option_id::REJECT_ALWAYS => PermissionDecision {
            outcome: arabica_protocol::ToolPermissionOutcome::Denied,
            scope: arabica_protocol::ToolPermissionScope::Session,
            source: arabica_protocol::ToolPermissionSource::User,
        },
        // A client that echoes back an option id we never offered is
        // protocol-invalid; fail closed rather than guess.
        _ => connection_unavailable(),
    }
}

/// The gate's own `approver_unavailable` constructor is crate-private (a
/// deliberate seam: only Runtime decides what "nobody answered" means for
/// its own audit trail), so this mirrors its exact shape for the cases that
/// are this bridge's own version of the same situation -- a connection
/// error, or a client naming an option it was never offered.
fn connection_unavailable() -> PermissionDecision {
    PermissionDecision {
        outcome: arabica_protocol::ToolPermissionOutcome::Denied,
        scope: arabica_protocol::ToolPermissionScope::Once,
        source: arabica_protocol::ToolPermissionSource::ApproverUnavailable,
    }
}

/// Drain one run's permission requests, asking the ACP client for each and
/// replying with its answer. Returns once `requests` closes, which happens
/// when `dispatch` returns and drops its clone of the sender -- there is
/// nothing left to ask for once the run this channel belongs to has ended.
pub async fn bridge(
    mut requests: tokio::sync::mpsc::UnboundedReceiver<PermissionRequest>,
    connection: ConnectionTo<Client>,
    session_id: AcpSessionId,
    workspace_root: PathBuf,
) {
    while let Some(request) = requests.recv().await {
        let PermissionRequest {
            run_id,
            call,
            reply,
        } = request;
        let tool_call_id = acp_tool_call_id(&Some(run_id), &call.call_id);
        let mut fields = ToolCallUpdateFields::new()
            .title(tool_call_title(&call.name, &call.arguments))
            .kind(tool_kind(&call.name))
            .raw_input(call.arguments.clone());
        let diff = diff_content(&workspace_root, &call.name, &call.arguments);
        if !diff.is_empty() {
            fields = fields.content(diff);
        }
        let tool_call =
            agent_client_protocol::schema::v1::ToolCallUpdate::new(tool_call_id, fields);
        let response = connection
            .send_request(RequestPermissionRequest::new(
                session_id.clone(),
                tool_call,
                permission_options(),
            ))
            .block_task()
            .await;
        let decision = match response {
            Ok(response) => match response.outcome {
                RequestPermissionOutcome::Cancelled => PermissionDecision::cancelled(),
                RequestPermissionOutcome::Selected(selected) => decision_for(&selected.option_id),
                // `RequestPermissionOutcome` is `#[non_exhaustive]`: fail
                // closed on any future outcome this agent does not know how
                // to interpret yet.
                _ => connection_unavailable(),
            },
            Err(_) => connection_unavailable(),
        };
        let _ = reply.send(decision);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn default_policy_allows_only_the_read_only_tools() {
        let policy = default_policy();
        assert_eq!(
            policy.rule_for("read_file", &serde_json::Value::Null),
            ToolPermissionRule::Allow
        );
        assert_eq!(
            policy.rule_for("list_dir", &serde_json::Value::Null),
            ToolPermissionRule::Allow
        );
        assert_eq!(
            policy.rule_for("grep", &serde_json::Value::Null),
            ToolPermissionRule::Allow
        );
        assert_eq!(
            policy.rule_for("find_files", &serde_json::Value::Null),
            ToolPermissionRule::Allow
        );
        assert_eq!(
            policy.rule_for("write_file", &serde_json::Value::Null),
            ToolPermissionRule::Ask
        );
        assert_eq!(
            policy.rule_for("edit_files", &serde_json::Value::Null),
            ToolPermissionRule::Ask
        );
        assert_eq!(
            policy.rule_for("delete_file", &serde_json::Value::Null),
            ToolPermissionRule::Ask
        );
        assert_eq!(
            policy.rule_for("shell", &serde_json::Value::Null),
            ToolPermissionRule::Ask
        );
    }

    #[test]
    fn each_offered_option_id_maps_to_the_matching_decision() {
        let allow_once = decision_for(&PermissionOptionId::new(option_id::ALLOW_ONCE));
        assert_eq!(
            allow_once.outcome,
            arabica_protocol::ToolPermissionOutcome::Allowed
        );
        assert_eq!(
            allow_once.scope,
            arabica_protocol::ToolPermissionScope::Once
        );

        let allow_always = decision_for(&PermissionOptionId::new(option_id::ALLOW_ALWAYS));
        assert_eq!(
            allow_always.outcome,
            arabica_protocol::ToolPermissionOutcome::Allowed
        );
        assert_eq!(
            allow_always.scope,
            arabica_protocol::ToolPermissionScope::Session
        );

        let reject_once = decision_for(&PermissionOptionId::new(option_id::REJECT_ONCE));
        assert_eq!(
            reject_once.outcome,
            arabica_protocol::ToolPermissionOutcome::Denied
        );
        assert_eq!(
            reject_once.scope,
            arabica_protocol::ToolPermissionScope::Once
        );

        let reject_always = decision_for(&PermissionOptionId::new(option_id::REJECT_ALWAYS));
        assert_eq!(
            reject_always.outcome,
            arabica_protocol::ToolPermissionOutcome::Denied
        );
        assert_eq!(
            reject_always.scope,
            arabica_protocol::ToolPermissionScope::Session
        );
    }

    #[test]
    fn an_unrecognized_option_id_fails_closed() {
        let decision = decision_for(&PermissionOptionId::new("not_an_option_we_offered"));
        assert_eq!(
            decision.outcome,
            arabica_protocol::ToolPermissionOutcome::Denied
        );
        assert_eq!(
            decision.source,
            arabica_protocol::ToolPermissionSource::ApproverUnavailable
        );
    }

    #[test]
    fn the_four_offered_options_match_their_declared_kinds() {
        let options = permission_options();
        assert_eq!(options.len(), 4);
        assert_eq!(options[0].kind, PermissionOptionKind::AllowOnce);
        assert_eq!(options[1].kind, PermissionOptionKind::AllowAlways);
        assert_eq!(options[2].kind, PermissionOptionKind::RejectOnce);
        assert_eq!(options[3].kind, PermissionOptionKind::RejectAlways);
    }
}
