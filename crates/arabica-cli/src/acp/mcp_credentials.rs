//! `broker` MCP authentication over the ACP connection: Arabica asks the
//! client for the current access token of a binding it declared, each time
//! the cached token is missing, expiring, or rejected.

use agent_client_protocol::{
    Client, ConnectionTo, Error, JsonRpcMessage, JsonRpcRequest, JsonRpcResponse, UntypedMessage,
};
use serde::Serialize;

use crate::mcp::auth::{BrokerRequest, CREDENTIAL_METHOD, CredentialBroker, ProvidedCredential};
use crate::mcp::credentials::{CredentialError, CredentialFuture};

impl JsonRpcMessage for BrokerRequest {
    fn matches_method(method: &str) -> bool {
        method == CREDENTIAL_METHOD
    }

    fn method(&self) -> &str {
        CREDENTIAL_METHOD
    }

    fn to_untyped_message(&self) -> Result<UntypedMessage, Error> {
        UntypedMessage::new(CREDENTIAL_METHOD, self)
    }

    fn parse_message(method: &str, params: &impl Serialize) -> Result<Self, Error> {
        if !Self::matches_method(method) {
            return Err(Error::method_not_found());
        }
        let value = serde_json::to_value(params).map_err(|_| Error::invalid_params())?;
        serde_json::from_value(value).map_err(|_| Error::invalid_params())
    }
}

impl JsonRpcRequest for BrokerRequest {
    type Response = ProvidedCredential;
}

impl JsonRpcResponse for ProvidedCredential {
    fn into_json(self, _method: &str) -> Result<serde_json::Value, Error> {
        serde_json::to_value(self).map_err(|_| Error::internal_error())
    }

    fn from_value(_method: &str, value: serde_json::Value) -> Result<Self, Error> {
        serde_json::from_value(value).map_err(|_| Error::invalid_params())
    }
}

pub(super) struct AcpCredentialBroker {
    connection: ConnectionTo<Client>,
}

impl AcpCredentialBroker {
    pub(super) fn new(connection: ConnectionTo<Client>) -> Self {
        Self { connection }
    }
}

impl CredentialBroker for AcpCredentialBroker {
    fn fetch(&self, request: BrokerRequest) -> CredentialFuture<'static> {
        let connection = self.connection.clone();
        Box::pin(async move {
            // Callers run outside ACP message handlers (session setup and
            // prompts are spawned), so waiting for the reply cannot stall
            // the connection's dispatch loop.
            match connection.send_request(request).block_task().await {
                Ok(credential) => credential.into_credential("ACP client"),
                Err(error) => Err(CredentialError::Unavailable(format!(
                    "the ACP client did not answer {CREDENTIAL_METHOD} ({}: {})",
                    i32::from(error.code),
                    error.message
                ))),
            }
        })
    }
}
