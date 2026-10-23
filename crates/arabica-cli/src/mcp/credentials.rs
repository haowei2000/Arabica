//! Per-request credentials for remote MCP servers.
//!
//! A [`CredentialSource`] knows how to obtain the access token one server
//! binding expects; [`Credentials`] decides when to ask it. The token is
//! resolved for every HTTP request, so a rotated or refreshed credential is
//! used by the next request on the same MCP connection without a new ACP
//! session. A missing credential is an error: nothing here ever falls back
//! to an unauthenticated request.

use std::collections::HashMap;
use std::fmt;
use std::future::Future;
use std::pin::Pin;
use std::sync::Arc;
use std::time::{Duration, SystemTime};

use reqwest::header::{HeaderName, HeaderValue};
use rmcp::model::ClientJsonRpcMessage;
use rmcp::transport::streamable_http_client::{
    StreamableHttpClient, StreamableHttpError, StreamableHttpPostResponse,
};
use serde::{Deserialize, Serialize};

/// A token is refreshed this long before its stated expiry, so a request
/// never leaves with a credential that lapses in flight.
const EXPIRY_SKEW: Duration = Duration::from_secs(30);

/// Upper bound on one call to a credential source. Connection setup resolves
/// the first credential before the MCP lifecycle starts, so this deadline
/// never runs inside the SDK's discovery probe.
const FETCH_TIMEOUT: Duration = Duration::from_secs(30);

/// A bearer token. Its `Debug` form never shows the value.
#[derive(Clone, PartialEq, Eq)]
pub struct Secret(Arc<str>);

impl Secret {
    pub fn new(value: impl Into<Arc<str>>) -> Self {
        Self(value.into())
    }

    pub fn expose(&self) -> &str {
        &self.0
    }
}

impl fmt::Debug for Secret {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("Secret([REDACTED])")
    }
}

#[derive(Clone, Debug)]
pub struct Credential {
    pub access_token: Secret,
    /// `None` means the source did not say; the token is used until the
    /// server rejects it.
    pub expires_at: Option<SystemTime>,
}

impl Credential {
    fn usable_at(&self, now: SystemTime) -> bool {
        self.expires_at.is_none_or(|at| at > now + EXPIRY_SKEW)
    }
}

/// Why a credential is being requested; sent to external providers so they
/// can tell a routine expiry from a server-side rejection.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub enum FetchReason {
    Initial,
    Expiring,
    Rejected,
}

#[derive(Clone, Copy, Debug)]
pub struct FetchRequest<'a> {
    pub reason: FetchReason,
    /// The token the server just rejected, for sources that can tell
    /// whether someone else already replaced it. Never forwarded to an
    /// external provider.
    pub rejected: Option<&'a Secret>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum CredentialError {
    /// The grant is missing, revoked, or rejected: a person must authorize
    /// again before the server can be used.
    NeedsAuthorization(String),
    /// The provider could not produce a credential right now.
    Unavailable(String),
}

impl fmt::Display for CredentialError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::NeedsAuthorization(message) => {
                write!(formatter, "MCP authorization required: {message}")
            }
            Self::Unavailable(message) => {
                write!(formatter, "MCP credential unavailable: {message}")
            }
        }
    }
}

impl std::error::Error for CredentialError {}

pub type CredentialFuture<'a> =
    Pin<Box<dyn Future<Output = Result<Credential, CredentialError>> + Send + 'a>>;

/// One way of obtaining the token for one server binding.
pub trait CredentialSource: Send + Sync {
    fn fetch<'a>(&'a self, request: FetchRequest<'a>) -> CredentialFuture<'a>;

    /// The error for an HTTP 403 `insufficient_scope` challenge. Retrying
    /// with the same grant cannot succeed, so the request is not replayed;
    /// sources that can re-authorize say how.
    fn insufficient_scope(&self, scope: Option<&str>) -> CredentialError {
        CredentialError::NeedsAuthorization(match scope {
            Some(scope) => format!(
                "the server requires scope '{scope}', which the credential provider must grant"
            ),
            None => "the server requires more scope than the credential grants".to_owned(),
        })
    }
}

/// A credential handed to one request, with the cache generation it came
/// from so a later rejection can tell whether it is already stale.
#[derive(Clone, Debug)]
pub struct Lease {
    pub token: Secret,
    generation: u64,
}

struct CacheState {
    current: Option<Credential>,
    generation: u64,
}

/// The cached credential for one server connection. Every fetch happens
/// under one lock, so concurrent requests that find the token expired or
/// rejected cause a single fetch and all reuse its result.
pub struct Credentials {
    source: Box<dyn CredentialSource>,
    state: tokio::sync::Mutex<CacheState>,
}

impl Credentials {
    pub fn new(source: Box<dyn CredentialSource>) -> Self {
        Self {
            source,
            state: tokio::sync::Mutex::new(CacheState {
                current: None,
                generation: 0,
            }),
        }
    }

    fn insufficient_scope(&self, scope: Option<&str>) -> CredentialError {
        self.source.insufficient_scope(scope)
    }

    /// The credential for the next request, fetching one when there is none
    /// or the cached one is about to expire.
    pub async fn current(&self) -> Result<Lease, CredentialError> {
        let mut state = self.state.lock().await;
        let reason = match &state.current {
            Some(credential) if credential.usable_at(SystemTime::now()) => {
                return Ok(Lease {
                    token: credential.access_token.clone(),
                    generation: state.generation,
                });
            }
            Some(_) => FetchReason::Expiring,
            None => FetchReason::Initial,
        };
        self.fetch_locked(&mut state, reason, None).await
    }

    /// Replace a credential the server answered with 401. When another
    /// request already replaced it, that replacement is reused instead of
    /// asking the source again.
    pub async fn after_rejection(&self, rejected: &Lease) -> Result<Lease, CredentialError> {
        let mut state = self.state.lock().await;
        if state.generation != rejected.generation
            && let Some(credential) = &state.current
            && credential.access_token != rejected.token
            && credential.usable_at(SystemTime::now())
        {
            return Ok(Lease {
                token: credential.access_token.clone(),
                generation: state.generation,
            });
        }
        state.current = None;
        let lease = self
            .fetch_locked(&mut state, FetchReason::Rejected, Some(&rejected.token))
            .await?;
        if lease.token == rejected.token {
            state.current = None;
            return Err(CredentialError::NeedsAuthorization(
                "the server rejected the credential and no replacement is available".to_owned(),
            ));
        }
        Ok(lease)
    }

    async fn fetch_locked(
        &self,
        state: &mut CacheState,
        reason: FetchReason,
        rejected: Option<&Secret>,
    ) -> Result<Lease, CredentialError> {
        state.current = None;
        let credential = tokio::time::timeout(
            FETCH_TIMEOUT,
            self.source.fetch(FetchRequest { reason, rejected }),
        )
        .await
        .map_err(|_| {
            CredentialError::Unavailable("the credential provider timed out".to_owned())
        })??;
        let token = credential.access_token.expose();
        if token.is_empty() || HeaderValue::from_str(&format!("Bearer {token}")).is_err() {
            return Err(CredentialError::Unavailable(
                "the credential provider returned an unusable access token".to_owned(),
            ));
        }
        state.generation += 1;
        let lease = Lease {
            token: credential.access_token.clone(),
            generation: state.generation,
        };
        state.current = Some(credential);
        Ok(lease)
    }
}

type HttpError = StreamableHttpError<reqwest::Error>;
type SseStream =
    futures_util::stream::BoxStream<'static, Result<sse_stream::Sse, sse_stream::Error>>;

/// The rmcp HTTP client with a credential resolved for every request. Its
/// redirect policy matches the SDK default: only same-origin 307 and 308
/// redirects are followed, so the bearer token never reaches another origin.
#[derive(Clone)]
pub(crate) struct CredentialedClient {
    http: reqwest::Client,
    credentials: Arc<Credentials>,
}

impl CredentialedClient {
    pub(crate) fn new(credentials: Arc<Credentials>) -> Self {
        let http = reqwest::Client::builder()
            .pool_max_idle_per_host(0)
            .redirect(same_origin_redirect_policy())
            .build()
            .expect("the MCP HTTP client configuration is valid");
        Self { http, credentials }
    }

    /// Run one HTTP exchange with the current credential. Only an HTTP 401
    /// is retried, once, with a replacement credential: the server refused
    /// that request before handling it. Timeouts, dropped connections, other
    /// statuses, and stream failures are never replayed, because the server
    /// may already have executed the request.
    async fn authorized<T, F, Fut>(&self, call: F) -> Result<T, HttpError>
    where
        F: Fn(String) -> Fut,
        Fut: Future<Output = Result<T, HttpError>>,
    {
        let lease = self
            .credentials
            .current()
            .await
            .map_err(credential_failure)?;
        let result = match call(lease.token.expose().to_owned()).await {
            Err(error) if is_rejection(&error) => {
                let replacement = self
                    .credentials
                    .after_rejection(&lease)
                    .await
                    .map_err(credential_failure)?;
                call(replacement.token.expose().to_owned()).await
            }
            result => result,
        };
        match result {
            Err(StreamableHttpError::InsufficientScope(error)) => Err(credential_failure(
                self.credentials
                    .insufficient_scope(error.get_required_scope()),
            )),
            result => result,
        }
    }
}

fn credential_failure(error: CredentialError) -> HttpError {
    let kind = match error {
        CredentialError::NeedsAuthorization(_) => std::io::ErrorKind::PermissionDenied,
        CredentialError::Unavailable(_) => std::io::ErrorKind::Other,
    };
    StreamableHttpError::Io(std::io::Error::new(kind, error))
}

fn is_rejection(error: &HttpError) -> bool {
    match error {
        StreamableHttpError::AuthRequired(_) => true,
        StreamableHttpError::Client(error) => {
            error.status() == Some(reqwest::StatusCode::UNAUTHORIZED)
        }
        // rmcp reports a 401 without a `WWW-Authenticate` challenge as an
        // unexpected response; the status leads the message.
        StreamableHttpError::UnexpectedServerResponse(message) => message.starts_with("HTTP 401 "),
        _ => false,
    }
}

const MAX_REDIRECTS: usize = 10;

fn same_origin_redirect_policy() -> reqwest::redirect::Policy {
    reqwest::redirect::Policy::custom(|attempt| {
        let preserves_body = matches!(
            attempt.status(),
            reqwest::StatusCode::TEMPORARY_REDIRECT | reqwest::StatusCode::PERMANENT_REDIRECT
        );
        let same_origin = attempt
            .previous()
            .first()
            .is_some_and(|original| original.origin() == attempt.url().origin());
        if !preserves_body || !same_origin {
            attempt.stop()
        } else if attempt.previous().len() > MAX_REDIRECTS {
            attempt.error(format!("exceeded {MAX_REDIRECTS} redirects"))
        } else {
            attempt.follow()
        }
    })
}

// The transport passes no `auth_header` of its own (none is configured), so
// every method ignores it and supplies the resolved credential instead.
impl StreamableHttpClient for CredentialedClient {
    type Error = reqwest::Error;

    async fn post_message(
        &self,
        uri: Arc<str>,
        message: ClientJsonRpcMessage,
        session_id: Option<Arc<str>>,
        _auth_header: Option<String>,
        custom_headers: HashMap<HeaderName, HeaderValue>,
    ) -> Result<StreamableHttpPostResponse, HttpError> {
        self.authorized(|token| {
            self.http.post_message(
                uri.clone(),
                message.clone(),
                session_id.clone(),
                Some(token),
                custom_headers.clone(),
            )
        })
        .await
    }

    async fn post_message_with_max_sse_event_size(
        &self,
        uri: Arc<str>,
        message: ClientJsonRpcMessage,
        session_id: Option<Arc<str>>,
        _auth_header: Option<String>,
        custom_headers: HashMap<HeaderName, HeaderValue>,
        max_sse_event_size: usize,
    ) -> Result<StreamableHttpPostResponse, HttpError> {
        self.authorized(|token| {
            self.http.post_message_with_max_sse_event_size(
                uri.clone(),
                message.clone(),
                session_id.clone(),
                Some(token),
                custom_headers.clone(),
                max_sse_event_size,
            )
        })
        .await
    }

    async fn delete_session(
        &self,
        uri: Arc<str>,
        session_id: Arc<str>,
        _auth_header: Option<String>,
        custom_headers: HashMap<HeaderName, HeaderValue>,
    ) -> Result<(), HttpError> {
        self.authorized(|token| {
            self.http.delete_session(
                uri.clone(),
                session_id.clone(),
                Some(token),
                custom_headers.clone(),
            )
        })
        .await
    }

    async fn get_stream(
        &self,
        uri: Arc<str>,
        session_id: Option<Arc<str>>,
        last_event_id: Option<String>,
        _auth_header: Option<String>,
        custom_headers: HashMap<HeaderName, HeaderValue>,
    ) -> Result<SseStream, HttpError> {
        self.authorized(|token| {
            self.http.get_stream(
                uri.clone(),
                session_id.clone(),
                last_event_id.clone(),
                Some(token),
                custom_headers.clone(),
            )
        })
        .await
    }

    async fn get_stream_with_max_sse_event_size(
        &self,
        uri: Arc<str>,
        session_id: Option<Arc<str>>,
        last_event_id: Option<String>,
        _auth_header: Option<String>,
        custom_headers: HashMap<HeaderName, HeaderValue>,
        max_sse_event_size: usize,
    ) -> Result<SseStream, HttpError> {
        self.authorized(|token| {
            self.http.get_stream_with_max_sse_event_size(
                uri.clone(),
                session_id.clone(),
                last_event_id.clone(),
                Some(token),
                custom_headers.clone(),
                max_sse_event_size,
            )
        })
        .await
    }
}
