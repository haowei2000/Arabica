//! Authentication modes for remote MCP servers.
//!
//! Static `headers` remain the default. A server may instead declare a
//! dynamic mode under the `arabica.auth` key of its `_meta` (ACP) or in an
//! `[mcp.auth]` table (user config):
//!
//! - `broker`: the ACP client resolves each token through the
//!   `_arabica/mcp/credential` request. The client keeps refresh tokens and
//!   upstream secrets; Arabica only sees short-lived access tokens.
//! - `command`: an absolute program prints the token as JSON (user config
//!   only, because it executes a local program).
//! - `oauth`: standard MCP OAuth, with tokens stored under `$ARABICA_HOME`
//!   by `arabica mcp login`.

use std::path::PathBuf;
use std::process::Stdio;
use std::sync::Arc;
use std::time::{Duration, SystemTime};

use serde::{Deserialize, Serialize};
use tokio::io::AsyncReadExt;

use super::credentials::{
    Credential, CredentialError, CredentialFuture, CredentialSource, FetchReason, FetchRequest,
    Secret,
};

/// The `_meta` key on an ACP `McpServerHttp` that selects a dynamic mode.
pub const META_KEY: &str = "arabica.auth";

/// The request Arabica sends to the ACP client in `broker` mode.
pub const CREDENTIAL_METHOD: &str = "_arabica/mcp/credential";

/// Binding used when a mode does not name one.
pub const DEFAULT_BINDING: &str = "default";

const MAX_MESSAGE_LENGTH: usize = 200;
const MAX_COMMAND_OUTPUT: u64 = 64 * 1024;

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(tag = "mode", rename_all = "camelCase", deny_unknown_fields)]
pub enum McpAuth {
    Broker {
        binding: String,
    },
    Command {
        command: Vec<String>,
        #[serde(default)]
        binding: Option<String>,
    },
    #[serde(rename_all = "camelCase")]
    Oauth {
        #[serde(default)]
        binding: Option<String>,
        #[serde(default)]
        client_id: Option<String>,
        /// An HTTPS Client ID Metadata Document, preferred over dynamic
        /// registration when no client ID is given.
        #[serde(default, skip_serializing_if = "Option::is_none")]
        client_metadata_url: Option<String>,
        #[serde(default)]
        scopes: Vec<String>,
    },
}

impl McpAuth {
    /// Read the mode from a server's `_meta`. No key means static headers.
    pub fn from_meta(
        server: &str,
        meta: Option<&serde_json::Map<String, serde_json::Value>>,
    ) -> Result<Option<Self>, String> {
        let Some(value) = meta.and_then(|meta| meta.get(META_KEY)) else {
            return Ok(None);
        };
        let auth = serde_json::from_value::<Self>(value.clone())
            .map_err(|_| format!("MCP server '{server}' has an invalid {META_KEY} declaration"))?;
        auth.validate(server)?;
        Ok(Some(auth))
    }

    /// The `_meta` map that declares this mode on an ACP `McpServerHttp`.
    pub fn to_meta(&self) -> serde_json::Map<String, serde_json::Value> {
        serde_json::Map::from_iter([(
            META_KEY.to_owned(),
            serde_json::to_value(self).expect("an auth declaration serializes"),
        )])
    }

    pub fn validate(&self, server: &str) -> Result<(), String> {
        let binding = match self {
            Self::Broker { binding } => Some(binding),
            Self::Command { command, binding } => {
                if !command
                    .first()
                    .is_some_and(|program| std::path::Path::new(program).is_absolute())
                    || command.iter().any(|part| part.contains('\0'))
                {
                    return Err(format!(
                        "MCP server '{server}' credential command must start with an absolute path"
                    ));
                }
                binding.as_ref()
            }
            Self::Oauth {
                binding,
                client_id,
                client_metadata_url,
                ..
            } => {
                if client_id.as_ref().is_some_and(|id| id.trim().is_empty()) {
                    return Err(format!(
                        "MCP server '{server}' OAuth client_id must not be empty"
                    ));
                }
                if client_metadata_url.as_ref().is_some_and(|url| {
                    url::Url::parse(url)
                        .map(|url| url.scheme() != "https" || url.path() == "/")
                        .unwrap_or(true)
                }) {
                    return Err(format!(
                        "MCP server '{server}' OAuth client_metadata_url must be an HTTPS URL with a path"
                    ));
                }
                binding.as_ref()
            }
        };
        if binding.is_some_and(|binding| binding.trim().is_empty() || binding.len() > 200) {
            return Err(format!(
                "MCP server '{server}' auth binding must be 1 to 200 characters"
            ));
        }
        Ok(())
    }

    pub fn binding(&self) -> &str {
        match self {
            Self::Broker { binding } => binding,
            Self::Command { binding, .. } | Self::Oauth { binding, .. } => {
                binding.as_deref().unwrap_or(DEFAULT_BINDING)
            }
        }
    }

    pub fn mode(&self) -> &'static str {
        match self {
            Self::Broker { .. } => "broker",
            Self::Command { .. } => "command",
            Self::Oauth { .. } => "oauth",
        }
    }
}

/// Who declared a server, which limits the modes it may use.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Origin {
    /// Supplied by the ACP client in `session/new`, `load`, or `resume`.
    Client,
    /// Declared in `$ARABICA_HOME/config.toml`.
    Configured,
}

/// What a dynamic mode needs from its host.
#[derive(Clone, Default)]
pub struct AuthEnvironment {
    /// Where `oauth` tokens are stored; `None` disables the mode.
    pub arabica_home: Option<PathBuf>,
    /// The ACP client connection for `broker` mode; `None` disables it.
    pub broker: Option<Arc<dyn CredentialBroker>>,
}

/// One credential request to an external broker.
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct BrokerRequest {
    pub server: String,
    pub url: String,
    pub binding: String,
    pub reason: FetchReason,
}

/// The ACP-side implementation lives with the ACP transport; this module
/// stays independent of it.
pub trait CredentialBroker: Send + Sync {
    fn fetch(&self, request: BrokerRequest) -> CredentialFuture<'static>;
}

/// The answer from a broker or credential command.
#[derive(Clone, Deserialize, Serialize)]
#[serde(tag = "outcome", rename_all = "camelCase", deny_unknown_fields)]
pub enum ProvidedCredential {
    #[serde(rename_all = "camelCase")]
    Granted {
        access_token: String,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        expires_in_seconds: Option<u64>,
    },
    NeedsAuthorization {
        #[serde(default, skip_serializing_if = "Option::is_none")]
        message: Option<String>,
    },
    Unavailable {
        #[serde(default, skip_serializing_if = "Option::is_none")]
        message: Option<String>,
    },
}

impl std::fmt::Debug for ProvidedCredential {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Granted {
                expires_in_seconds, ..
            } => formatter
                .debug_struct("Granted")
                .field("access_token", &"[REDACTED]")
                .field("expires_in_seconds", expires_in_seconds)
                .finish(),
            Self::NeedsAuthorization { message } => formatter
                .debug_struct("NeedsAuthorization")
                .field("message", message)
                .finish(),
            Self::Unavailable { message } => formatter
                .debug_struct("Unavailable")
                .field("message", message)
                .finish(),
        }
    }
}

impl ProvidedCredential {
    pub fn into_credential(self, provider: &str) -> Result<Credential, CredentialError> {
        let describe = |message: Option<String>, fallback: &str| {
            let message = message
                .filter(|message| !message.trim().is_empty())
                .unwrap_or_else(|| fallback.to_owned());
            message.chars().take(MAX_MESSAGE_LENGTH).collect::<String>()
        };
        match self {
            Self::Granted {
                access_token,
                expires_in_seconds,
            } => Ok(Credential {
                access_token: Secret::new(access_token),
                expires_at: expires_in_seconds
                    .map(|seconds| SystemTime::now() + Duration::from_secs(seconds)),
            }),
            Self::NeedsAuthorization { message } => {
                Err(CredentialError::NeedsAuthorization(describe(
                    message,
                    &format!("the {provider} requires a new authorization"),
                )))
            }
            Self::Unavailable { message } => Err(CredentialError::Unavailable(describe(
                message,
                &format!("the {provider} has no credential"),
            ))),
        }
    }
}

/// Build the source for a declared mode, or explain why it cannot be used.
pub(crate) async fn source_for(
    auth: &McpAuth,
    server: &str,
    url: &str,
    origin: Origin,
    environment: &AuthEnvironment,
) -> Result<Box<dyn CredentialSource>, String> {
    match auth {
        McpAuth::Broker { binding } => {
            if origin != Origin::Client {
                return Err(format!(
                    "MCP server '{server}' uses broker authentication, which only an ACP client can supply"
                ));
            }
            let broker = environment.broker.clone().ok_or_else(|| {
                format!("MCP server '{server}' uses broker authentication, which this host does not provide")
            })?;
            Ok(Box::new(BrokerSource {
                broker,
                server: server.to_owned(),
                url: url.to_owned(),
                binding: binding.clone(),
            }))
        }
        McpAuth::Command { command, .. } => {
            if origin != Origin::Configured {
                return Err(format!(
                    "MCP server '{server}' credential commands are accepted only from the user config file"
                ));
            }
            Ok(Box::new(CommandSource {
                command: command.clone(),
                server: server.to_owned(),
                url: url.to_owned(),
                binding: auth.binding().to_owned(),
            }))
        }
        McpAuth::Oauth { .. } => {
            let home = environment.arabica_home.as_deref().ok_or_else(|| {
                format!("MCP server '{server}' uses OAuth, which this host does not provide")
            })?;
            let target = super::oauth::OAuthTarget {
                server: server.to_owned(),
                url: url.to_owned(),
                binding: auth.binding().to_owned(),
                configured: origin == Origin::Configured,
            };
            super::oauth::OAuthSource::open(target, home)
                .await
                .map(|source| Box::new(source) as Box<dyn CredentialSource>)
                .map_err(|error| format!("MCP server '{server}' {error}"))
        }
    }
}

struct BrokerSource {
    broker: Arc<dyn CredentialBroker>,
    server: String,
    url: String,
    binding: String,
}

impl CredentialSource for BrokerSource {
    fn fetch<'a>(&'a self, request: FetchRequest<'a>) -> CredentialFuture<'a> {
        // The rejected token stays here: the broker identifies the grant by
        // its binding and never needs the token back.
        self.broker.fetch(BrokerRequest {
            server: self.server.clone(),
            url: self.url.clone(),
            binding: self.binding.clone(),
            reason: request.reason,
        })
    }
}

struct CommandSource {
    command: Vec<String>,
    server: String,
    url: String,
    binding: String,
}

impl CredentialSource for CommandSource {
    fn fetch<'a>(&'a self, request: FetchRequest<'a>) -> CredentialFuture<'a> {
        Box::pin(async move {
            let reason = serde_json::to_value(request.reason)
                .ok()
                .and_then(|value| value.as_str().map(str::to_owned))
                .unwrap_or_default();
            let mut command = tokio::process::Command::new(&self.command[0]);
            command
                .args(&self.command[1..])
                .env_clear()
                .stdin(Stdio::null())
                .stdout(Stdio::piped())
                // A helper's diagnostics may echo secrets; they are not kept.
                .stderr(Stdio::null())
                .kill_on_drop(true);
            for key in ["PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "SYSTEMROOT"] {
                if let Some(value) = std::env::var_os(key) {
                    command.env(key, value);
                }
            }
            command
                .env("ARABICA_MCP_SERVER", &self.server)
                .env("ARABICA_MCP_URL", &self.url)
                .env("ARABICA_MCP_BINDING", &self.binding)
                .env("ARABICA_MCP_REASON", reason);
            let unavailable = |message: &str| CredentialError::Unavailable(message.to_owned());
            let mut child = command
                .spawn()
                .map_err(|_| unavailable("the credential command could not start"))?;
            let mut output = Vec::new();
            child
                .stdout
                .take()
                .ok_or_else(|| unavailable("the credential command has no output"))?
                .take(MAX_COMMAND_OUTPUT)
                .read_to_end(&mut output)
                .await
                .map_err(|_| unavailable("the credential command output could not be read"))?;
            let status = child
                .wait()
                .await
                .map_err(|_| unavailable("the credential command did not finish"))?;
            if !status.success() {
                return Err(CredentialError::Unavailable(format!(
                    "the credential command exited with {status}"
                )));
            }
            serde_json::from_slice::<ProvidedCredential>(&output)
                .map_err(|_| unavailable("the credential command printed an invalid response"))?
                .into_credential("credential command")
        })
    }
}
