//! Typed ACP metadata operations backed by the canonical session store.

use agent_client_protocol::schema::v1::SessionId;
use agent_client_protocol::{
    Error, JsonRpcMessage, JsonRpcRequest, JsonRpcResponse, UntypedMessage,
};
use serde::{Deserialize, Serialize};

pub(super) const RENAME_METHOD: &str = "_arabica/session/rename";
pub(super) const MAX_TITLE_LENGTH: usize = 200;

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct RenameSessionRequest {
    pub session_id: SessionId,
    pub title: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct RenameSessionResponse {
    pub session_id: SessionId,
    pub title: String,
}

impl JsonRpcMessage for RenameSessionRequest {
    fn matches_method(method: &str) -> bool {
        method == RENAME_METHOD
    }

    fn method(&self) -> &str {
        RENAME_METHOD
    }

    fn to_untyped_message(&self) -> Result<UntypedMessage, Error> {
        UntypedMessage::new(RENAME_METHOD, self)
    }

    fn parse_message(method: &str, params: &impl Serialize) -> Result<Self, Error> {
        if !Self::matches_method(method) {
            return Err(Error::method_not_found());
        }
        let value = serde_json::to_value(params).map_err(|_| Error::invalid_params())?;
        serde_json::from_value(value).map_err(|_| Error::invalid_params())
    }
}

impl JsonRpcRequest for RenameSessionRequest {
    type Response = RenameSessionResponse;
}

impl JsonRpcResponse for RenameSessionResponse {
    fn into_json(self, _method: &str) -> Result<serde_json::Value, Error> {
        serde_json::to_value(self).map_err(|_| Error::internal_error())
    }

    fn from_value(_method: &str, value: serde_json::Value) -> Result<Self, Error> {
        serde_json::from_value(value).map_err(|_| Error::invalid_params())
    }
}

fn validate(request: &RenameSessionRequest) -> Result<&str, Error> {
    let session_id = request.session_id.to_string();
    if session_id.is_empty()
        || session_id == "."
        || session_id == ".."
        || session_id.contains(['/', '\\'])
        || session_id.chars().any(char::is_control)
    {
        return Err(Error::invalid_params().data("invalid sessionId"));
    }
    let title = request.title.trim();
    if title.is_empty()
        || title.chars().count() > MAX_TITLE_LENGTH
        || title.chars().any(char::is_control)
    {
        return Err(Error::invalid_params().data(format!(
            "title must contain 1 to {MAX_TITLE_LENGTH} characters without control characters"
        )));
    }
    Ok(title)
}

/// Renaming never acquires a session manager or starts a model. A registered
/// session already owns its writer; historical sessions acquire the same
/// exclusive store ownership used by load/resume, without activating a session.
pub(super) fn rename(
    state: &super::AcpState,
    request: RenameSessionRequest,
) -> Result<RenameSessionResponse, Error> {
    let title = validate(&request)?.to_owned();
    let writer = if let Some(entry) = state.entry(&request.session_id) {
        entry.store.clone()
    } else {
        let session_id = arabica_protocol::SessionId::new(request.session_id.to_string());
        let listings = state
            .session_store
            .list(None)
            .map_err(|error| Error::internal_error().data(error.to_string()))?;
        let mut matches = listings
            .into_iter()
            .filter(|listing| listing.header.id == session_id);
        let listing = matches
            .next()
            .ok_or_else(|| Error::invalid_params().data("unknown sessionId"))?;
        if matches.next().is_some() {
            return Err(Error::invalid_params().data("ambiguous sessionId"));
        }
        state
            .session_store
            .open(&listing.header.workspace_id, &session_id)
            .map_err(|error| Error::internal_error().data(error.to_string()))?
    };
    writer
        .set_title(&title)
        .map_err(|error| Error::internal_error().data(error.to_string()))?;
    Ok(RenameSessionResponse {
        session_id: request.session_id,
        title,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn request(session_id: &str, title: &str) -> RenameSessionRequest {
        RenameSessionRequest {
            session_id: SessionId::new(session_id.to_owned()),
            title: title.to_owned(),
        }
    }

    #[test]
    fn rename_params_are_typed_and_reject_unexpected_fields() {
        let params = serde_json::json!({"sessionId":"session-1","title":"New title"});
        let request = RenameSessionRequest::parse_message(RENAME_METHOD, &params).unwrap();
        assert_eq!(request.session_id.to_string(), "session-1");
        assert_eq!(request.title, "New title");
        assert_eq!(request.method(), RENAME_METHOD);
        assert!(RenameSessionRequest::parse_message("session/rename", &params).is_err());
        for params in [
            serde_json::json!({"sessionId":"session-1"}),
            serde_json::json!({"sessionId":"session-1","title":1}),
            serde_json::json!({"sessionId":"session-1","title":"Title","cwd":"/tmp"}),
        ] {
            assert!(RenameSessionRequest::parse_message(RENAME_METHOD, &params).is_err());
        }
    }

    #[test]
    fn rename_validation_trims_and_bounds_unicode_titles() {
        let valid = request("session-1", "  New title  ");
        assert_eq!(validate(&valid).unwrap(), "New title");
        assert!(validate(&request("session-1", &"中".repeat(MAX_TITLE_LENGTH))).is_ok());
        for title in ["", "  ", "Title\nsecond line", "Title\0", "Title\ttext"] {
            assert!(validate(&request("session-1", title)).is_err());
        }
        assert!(validate(&request("session-1", &"中".repeat(MAX_TITLE_LENGTH + 1))).is_err());
        for session_id in [
            "",
            ".",
            "..",
            "../escape",
            "folder/session",
            "folder\\session",
            "a\0b",
        ] {
            assert!(validate(&request(session_id, "Title")).is_err());
        }
    }
}
