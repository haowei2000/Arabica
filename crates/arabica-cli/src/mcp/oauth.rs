//! MCP OAuth: authorization through `arabica mcp login`, then refresh for
//! the lifetime of every session that uses the server.
//!
//! Discovery, resource indicators, PKCE, state, and token exchange belong to
//! rmcp's `AuthorizationManager`. This module owns where the grant lives:
//! one owner-only file per (resource, binding) under
//! `$ARABICA_HOME/mcp-oauth`, so two bindings for the same URL never share a
//! token, and a lock file that keeps concurrent processes from refreshing
//! the same grant at once.

use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use async_trait::async_trait;
use rmcp::transport::auth::{
    AuthError, AuthorizationManager, AuthorizationMetadataSource, AuthorizationRequest,
    AuthorizationSession, CredentialRefreshGuard, CredentialStore, StoredCredentials,
    WWWAuthenticateParams,
};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use tokio::io::{AsyncReadExt, AsyncWriteExt};

use super::credentials::{
    Credential, CredentialError, CredentialFuture, CredentialSource, FetchReason, FetchRequest,
    Secret,
};

const STORE_DIRECTORY: &str = "mcp-oauth";
const LOGIN_TIMEOUT: Duration = Duration::from_secs(300);

/// The grant one server binding uses.
#[derive(Clone, Debug)]
pub struct OAuthTarget {
    pub server: String,
    pub url: String,
    pub binding: String,
    /// Whether the server is in the user config, which decides the login
    /// command a diagnostic suggests.
    pub configured: bool,
}

impl OAuthTarget {
    fn login_command(&self) -> String {
        if self.configured {
            format!("arabica mcp login {}", self.server)
        } else {
            format!(
                "arabica mcp login --url {} --binding {}",
                self.url, self.binding
            )
        }
    }

    fn login_hint(&self) -> String {
        format!("run `{}`", self.login_command())
    }

    /// Re-authorization keeps the grant's existing scopes, so naming only
    /// the newly required one is enough.
    fn scope_hint(&self, scope: &str) -> String {
        format!("run `{} --scope '{scope}'`", self.login_command())
    }
}

/// The resource identity a grant is stored under: scheme and host
/// lowercased, no fragment, no trailing slash.
pub fn canonical_resource(url: &str) -> Result<String, String> {
    let mut parsed = url::Url::parse(url).map_err(|_| "has an invalid URL".to_owned())?;
    if !matches!(parsed.scheme(), "http" | "https") {
        return Err("must use an HTTP or HTTPS URL".to_owned());
    }
    parsed.set_fragment(None);
    let mut canonical = parsed.to_string();
    if canonical.ends_with('/') && parsed.path() != "/" {
        canonical.pop();
    }
    Ok(canonical)
}

#[derive(Serialize, Deserialize)]
struct StoredGrant {
    resource: String,
    binding: String,
    credentials: StoredCredentials,
}

/// The owner-only file holding one grant.
#[derive(Clone)]
pub struct FileCredentialStore {
    path: PathBuf,
    resource: String,
    binding: String,
}

impl FileCredentialStore {
    pub fn new(arabica_home: &Path, resource: &str, binding: &str) -> Self {
        let digest = Sha256::digest(format!("{resource}\n{binding}").as_bytes());
        let path = arabica_home
            .join(STORE_DIRECTORY)
            .join(format!("{}.json", hex::encode(digest)));
        Self {
            path,
            resource: resource.to_owned(),
            binding: binding.to_owned(),
        }
    }

    fn lock_path(&self) -> PathBuf {
        self.path.with_extension("lock")
    }

    fn ensure_directory(&self) -> Result<(), AuthError> {
        let directory = self.path.parent().expect("grant files live in a directory");
        fs::create_dir_all(directory).map_err(store_error)?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(directory, fs::Permissions::from_mode(0o700))
                .map_err(store_error)?;
        }
        Ok(())
    }

    fn read(&self) -> Result<Option<StoredGrant>, AuthError> {
        let bytes = match fs::read(&self.path) {
            Ok(bytes) => bytes,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
            Err(error) => return Err(store_error(error)),
        };
        let grant = serde_json::from_slice::<StoredGrant>(&bytes).map_err(|_| {
            AuthError::CredentialStoreError("the stored grant is unreadable".into())
        })?;
        // A hash collision or a hand-copied file must not lend one binding's
        // token to another.
        if grant.resource != self.resource || grant.binding != self.binding {
            return Err(AuthError::CredentialStoreError(
                "the stored grant belongs to another resource or binding".into(),
            ));
        }
        Ok(Some(grant))
    }

    pub fn remove(&self) -> std::io::Result<bool> {
        match fs::remove_file(&self.path) {
            Ok(()) => Ok(true),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(false),
            Err(error) => Err(error),
        }
    }

    fn granted_scopes(&self) -> Vec<String> {
        self.read()
            .ok()
            .flatten()
            .map(|grant| grant.credentials.granted_scopes)
            .unwrap_or_default()
    }

    fn stored_token(&self) -> Result<Option<(Secret, Option<SystemTime>)>, AuthError> {
        let Some(grant) = self.read()? else {
            return Ok(None);
        };
        let Some(response) = grant.credentials.token_response.as_ref() else {
            return Ok(None);
        };
        // Read the standard token-response fields from their wire form, which
        // avoids depending on the oauth2 crate's accessor traits.
        let response = serde_json::to_value(response).map_err(|_| {
            AuthError::CredentialStoreError("the stored grant is unreadable".into())
        })?;
        let Some(token) = response["access_token"].as_str() else {
            return Ok(None);
        };
        let expires_at = match (
            response["expires_in"].as_u64(),
            grant.credentials.token_received_at,
        ) {
            (Some(expires_in), Some(received_at)) => {
                Some(UNIX_EPOCH + Duration::from_secs(received_at.saturating_add(expires_in)))
            }
            _ => None,
        };
        Ok(Some((Secret::new(token), expires_at)))
    }
}

fn store_error(error: std::io::Error) -> AuthError {
    AuthError::CredentialStoreError(error.kind().to_string())
}

#[async_trait]
impl CredentialStore for FileCredentialStore {
    async fn load(&self) -> Result<Option<StoredCredentials>, AuthError> {
        Ok(self.read()?.map(|grant| grant.credentials))
    }

    async fn save(&self, credentials: StoredCredentials) -> Result<(), AuthError> {
        self.ensure_directory()?;
        let grant = StoredGrant {
            resource: self.resource.clone(),
            binding: self.binding.clone(),
            credentials,
        };
        let bytes = serde_json::to_vec_pretty(&grant).map_err(|_| {
            AuthError::CredentialStoreError("the grant could not be encoded".into())
        })?;
        let directory = self.path.parent().expect("grant files live in a directory");
        let mut file = tempfile::NamedTempFile::new_in(directory).map_err(store_error)?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            file.as_file()
                .set_permissions(fs::Permissions::from_mode(0o600))
                .map_err(store_error)?;
        }
        file.write_all(&bytes).map_err(store_error)?;
        file.as_file().sync_all().map_err(store_error)?;
        file.persist(&self.path)
            .map_err(|error| store_error(error.error))?;
        Ok(())
    }

    async fn clear(&self) -> Result<(), AuthError> {
        self.remove().map(|_| ()).map_err(store_error)
    }

    async fn acquire_refresh_guard(&self) -> Result<Option<CredentialRefreshGuard>, AuthError> {
        self.ensure_directory()?;
        let lock_path = self.lock_path();
        let file = tokio::task::spawn_blocking(move || -> std::io::Result<fs::File> {
            let file = OpenOptions::new()
                .create(true)
                .truncate(false)
                .write(true)
                .open(lock_path)?;
            fs2::FileExt::lock_exclusive(&file)?;
            Ok(file)
        })
        .await
        .map_err(|_| AuthError::CredentialStoreError("the refresh lock task failed".into()))?
        .map_err(store_error)?;
        // Closing the file releases the lock.
        Ok(Some(CredentialRefreshGuard::new(file)))
    }
}

fn credential_error(target: &OAuthTarget, error: AuthError) -> CredentialError {
    match error {
        AuthError::AuthorizationRequired
        | AuthError::TokenRefreshRejected(_)
        | AuthError::TokenExpired => CredentialError::NeedsAuthorization(format!(
            "the OAuth grant for '{}' is missing, expired, or revoked; {}",
            target.server,
            target.login_hint()
        )),
        other => CredentialError::Unavailable(format!("OAuth failed: {other}")),
    }
}

/// Supplies the stored grant's access token, refreshing through the
/// authorization server when it expires or the MCP server rejects it.
pub struct OAuthSource {
    target: OAuthTarget,
    manager: AuthorizationManager,
    store: FileCredentialStore,
}

impl OAuthSource {
    pub async fn open(target: OAuthTarget, arabica_home: &Path) -> Result<Self, String> {
        let resource = canonical_resource(&target.url)?;
        let store = FileCredentialStore::new(arabica_home, &resource, &target.binding);
        let has_grant = store
            .stored_token()
            .map_err(|error| format!("OAuth store failed: {error}"))?
            .is_some();
        if !has_grant {
            return Err(format!(
                "has no OAuth authorization; {}",
                target.login_hint()
            ));
        }
        let mut manager = AuthorizationManager::new(target.url.as_str())
            .await
            .map_err(|error| format!("OAuth setup failed: {error}"))?;
        manager.set_credential_store(store.clone());
        // Resolves authorization server metadata and binds the stored client.
        // A changed issuer discards the grant rather than reuse its tokens.
        if !manager
            .initialize_from_store()
            .await
            .map_err(|error| format!("OAuth setup failed: {error}"))?
        {
            return Err(format!(
                "has no usable OAuth authorization; {}",
                target.login_hint()
            ));
        }
        Ok(Self {
            target,
            manager,
            store,
        })
    }

    async fn stored(&self) -> Result<Credential, CredentialError> {
        match self.store.stored_token() {
            Ok(Some((access_token, expires_at))) => Ok(Credential {
                access_token,
                expires_at,
            }),
            Ok(None) => Err(credential_error(
                &self.target,
                AuthError::AuthorizationRequired,
            )),
            Err(error) => Err(credential_error(&self.target, error)),
        }
    }
}

impl CredentialSource for OAuthSource {
    fn insufficient_scope(&self, scope: Option<&str>) -> CredentialError {
        CredentialError::NeedsAuthorization(match scope {
            Some(scope) => format!(
                "'{}' requires OAuth scope '{scope}'; {}",
                self.target.server,
                self.target.scope_hint(scope)
            ),
            None => format!(
                "'{}' requires more OAuth scope; {}",
                self.target.server,
                self.target.login_hint()
            ),
        })
    }

    fn fetch<'a>(&'a self, request: FetchRequest<'a>) -> CredentialFuture<'a> {
        Box::pin(async move {
            if request.reason == FetchReason::Rejected {
                // Another process sharing this grant may already have
                // refreshed it; its token is used before refreshing again.
                if let Ok(stored) = self.stored().await
                    && Some(&stored.access_token) != request.rejected
                    && stored
                        .expires_at
                        .is_none_or(|at| at > SystemTime::now() + Duration::from_secs(30))
                {
                    return Ok(stored);
                }
                self.manager
                    .refresh_token()
                    .await
                    .map_err(|error| credential_error(&self.target, error))?;
                return self.stored().await;
            }
            // Refreshes when the stored token is within the SDK's expiry
            // buffer; otherwise returns it unchanged.
            self.manager
                .get_access_token()
                .await
                .map_err(|error| credential_error(&self.target, error))?;
            self.stored().await
        })
    }
}

/// What `arabica mcp login` authorizes.
#[derive(Clone, Debug)]
pub struct LoginRequest {
    pub target: OAuthTarget,
    pub client_id: Option<String>,
    /// An HTTPS Client ID Metadata Document URL, used when the authorization
    /// server supports them and no client ID is given.
    pub client_metadata_url: Option<String>,
    pub scopes: Vec<String>,
}

/// Run the authorization-code flow with PKCE through a loopback redirect,
/// then store the grant. `show_url` receives the authorization URL; the
/// person opens it in a browser.
pub async fn login(
    request: LoginRequest,
    arabica_home: &Path,
    show_url: impl FnOnce(&str),
) -> Result<(), String> {
    let resource = canonical_resource(&request.target.url)
        .map_err(|error| format!("MCP server '{}' {error}", request.target.server))?;
    let store = FileCredentialStore::new(arabica_home, &resource, &request.target.binding);
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
        .await
        .map_err(|error| format!("could not open the OAuth callback listener: {error}"))?;
    let port = listener
        .local_addr()
        .map_err(|error| format!("could not open the OAuth callback listener: {error}"))?
        .port();
    let redirect_uri = format!("http://127.0.0.1:{port}/callback");

    let mut manager = AuthorizationManager::new(request.target.url.as_str())
        .await
        .map_err(|error| format!("OAuth setup failed: {error}"))?;
    let previous_scopes = store.granted_scopes();
    manager.set_credential_store(store);
    // The flow starts from the server's own 401 challenge, whose
    // `resource_metadata` and `scope` take precedence over well-known probing.
    let challenge = probe_challenge(&request.target.url).await;
    let resolution = manager
        .resolve_metadata_from_challenge(challenge.as_deref())
        .await
        .map_err(|error| format!("OAuth discovery failed: {error}"))?;
    // MCP 2026-07-28 requires authorization server discovery through RFC 9728
    // Protected Resource Metadata; endpoints are never guessed.
    if !matches!(
        resolution.source,
        AuthorizationMetadataSource::ProtectedResourceMetadata
    ) {
        return Err(format!(
            "MCP server '{}' does not publish OAuth Protected Resource Metadata (RFC 9728)",
            request.target.server
        ));
    }
    manager.set_metadata(resolution.metadata);
    // Scopes the challenge names are required for this request; scopes the
    // grant already holds are kept so re-authorization loses nothing.
    let mut scopes = previous_scopes;
    let challenged = challenge
        .as_deref()
        .and_then(|challenge| {
            let base = url::Url::parse(&request.target.url).ok()?;
            WWWAuthenticateParams::parse(challenge, &base).scope
        })
        .unwrap_or_default();
    for scope in challenged
        .split_whitespace()
        .map(str::to_owned)
        .chain(request.scopes.iter().cloned())
    {
        if !scopes.contains(&scope) {
            scopes.push(scope);
        }
    }
    let mut authorization = AuthorizationRequest::new(redirect_uri.clone())
        .with_client_name("Arabica")
        .with_initial_scope_selection();
    if !scopes.is_empty() {
        authorization = authorization.with_scopes(scopes);
    }
    if let Some(client_id) = &request.client_id {
        authorization = authorization.with_preregistered_client(client_id.clone());
    } else if let Some(url) = &request.client_metadata_url {
        authorization = authorization.with_client_metadata_url(url.clone());
    }
    let session = AuthorizationSession::new(manager, authorization)
        .await
        .map_err(|(_, error)| format!("OAuth authorization could not start: {error}"))?;
    show_url(session.get_authorization_url());

    let callback = tokio::time::timeout(LOGIN_TIMEOUT, accept_callback(&listener, port))
        .await
        .map_err(|_| "OAuth authorization timed out".to_owned())??;
    // rmcp validates `state` against the PKCE verifier it issued and the
    // optional RFC 9207 issuer before exchanging the code.
    session
        .handle_callback_url(&callback)
        .await
        .map_err(|error| format!("OAuth authorization failed: {error}"))?;
    Ok(())
}

/// Send one unauthenticated request so a protected server answers with its
/// `WWW-Authenticate` challenge. Any other answer means no challenge.
async fn probe_challenge(url: &str) -> Option<String> {
    let client = reqwest::Client::builder()
        .redirect(reqwest::redirect::Policy::none())
        .timeout(Duration::from_secs(15))
        .build()
        .ok()?;
    let response = client
        .post(url)
        .header(
            reqwest::header::ACCEPT,
            "application/json, text/event-stream",
        )
        .json(&serde_json::json!({
            "jsonrpc": "2.0", "id": 1, "method": "server/discover", "params": {}
        }))
        .send()
        .await
        .ok()?;
    if response.status() != reqwest::StatusCode::UNAUTHORIZED {
        return None;
    }
    response
        .headers()
        .get(reqwest::header::WWW_AUTHENTICATE)?
        .to_str()
        .ok()
        .map(str::to_owned)
}

/// Accept loopback requests until one reaches `/callback`, answer it, and
/// return its full URL.
async fn accept_callback(listener: &tokio::net::TcpListener, port: u16) -> Result<String, String> {
    loop {
        let (mut stream, _) = listener
            .accept()
            .await
            .map_err(|error| format!("the OAuth callback failed: {error}"))?;
        let mut buffer = vec![0; 8192];
        let mut length = 0;
        while length < buffer.len() {
            let read = stream
                .read(&mut buffer[length..])
                .await
                .map_err(|error| format!("the OAuth callback failed: {error}"))?;
            if read == 0 {
                break;
            }
            length += read;
            if buffer[..length]
                .windows(4)
                .any(|window| window == b"\r\n\r\n")
            {
                break;
            }
        }
        let head = String::from_utf8_lossy(&buffer[..length]);
        let target = head
            .lines()
            .next()
            .and_then(|line| line.strip_prefix("GET "))
            .and_then(|line| line.split(' ').next())
            .unwrap_or_default()
            .to_owned();
        let found = target.starts_with("/callback?");
        let (status, body) = if found {
            (
                "200 OK",
                "Arabica received the authorization. You can close this window.",
            )
        } else {
            ("404 Not Found", "Not found.")
        };
        let response = format!(
            "HTTP/1.1 {status}\r\nContent-Type: text/plain; charset=utf-8\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
            body.len()
        );
        let _ = stream.write_all(response.as_bytes()).await;
        let _ = stream.shutdown().await;
        if found {
            return Ok(format!("http://127.0.0.1:{port}{target}"));
        }
    }
}

/// Delete one stored grant. Returns whether a grant existed.
pub fn logout(target: &OAuthTarget, arabica_home: &Path) -> Result<bool, String> {
    let resource = canonical_resource(&target.url)
        .map_err(|error| format!("MCP server '{}' {error}", target.server))?;
    FileCredentialStore::new(arabica_home, &resource, &target.binding)
        .remove()
        .map_err(|error| format!("could not remove the OAuth grant: {error}"))
}
