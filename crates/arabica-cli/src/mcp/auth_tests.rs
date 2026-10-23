//! Dynamic MCP authentication against wire-level fixtures: a raw MCP
//! resource server that checks bearer tokens, and an OAuth authorization
//! server, both independent of rmcp's server implementation.

use std::collections::{HashMap, HashSet};
use std::sync::{Arc, Mutex};
use std::time::{Duration, SystemTime};

use agent_client_protocol::schema::v1::{HttpHeader, McpServerHttp};
use arabica_model::ToolCallItem;
use axum::Json;
use axum::body::Bytes;
use axum::extract::{Query, State};
use axum::http::{HeaderMap, HeaderValue, Method, StatusCode, header};
use axum::response::{IntoResponse, Response};
use axum::routing::{any, get, post};
use base64::Engine;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};

use super::auth::{AuthEnvironment, BrokerRequest, CredentialBroker, McpAuth};
use super::credentials::{Credential, CredentialError, CredentialFuture, FetchReason, Secret};
use super::*;

const MODERN_VERSION: &str = "2026-07-28";

#[derive(Clone, Copy, PartialEq, Eq)]
pub(crate) enum Lifecycle {
    Modern,
    Legacy,
}

#[derive(Clone, Debug)]
pub(crate) struct Observed {
    pub(crate) rpc_method: String,
    pub(crate) authorization: Option<String>,
    has_session_id: bool,
    accepted: bool,
}

#[derive(Clone, Copy, Default, PartialEq, Eq)]
enum IssuerParameter {
    #[default]
    Omit,
    Correct,
    Wrong,
}

#[derive(Default)]
struct OAuthServerState {
    /// Authorization code to (PKCE challenge, requested scope).
    codes: HashMap<String, (String, String)>,
    /// Refresh token to the scope it grants.
    refresh_tokens: HashMap<String, String>,
    refreshes: usize,
    issued: usize,
    withhold_resource_metadata: bool,
    advertise_issuer_parameter: bool,
    issuer_parameter: IssuerParameter,
    /// `scope` in the resource server's 401 challenge.
    challenge_scope: Option<String>,
    /// A scope `tools/call` demands, answered with 403 when missing.
    required_call_scope: Option<String>,
    requested_scopes: Vec<String>,
    token_scopes: HashMap<String, String>,
}

struct FixtureState {
    base: String,
    lifecycle: Lifecycle,
    /// Send `WWW-Authenticate` with a 401, as the MCP spec requires.
    challenge: bool,
    valid_tokens: Mutex<HashSet<String>>,
    requests: Mutex<Vec<Observed>>,
    oauth: Mutex<OAuthServerState>,
}

pub(crate) struct Fixture {
    pub(crate) url: String,
    state: Arc<FixtureState>,
    task: tokio::task::JoinHandle<()>,
}

impl Drop for Fixture {
    fn drop(&mut self) {
        self.task.abort();
    }
}

impl Fixture {
    pub(crate) async fn start(lifecycle: Lifecycle, challenge: bool, tokens: &[&str]) -> Self {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
            .await
            .expect("bind fixture");
        let base = format!("http://{}", listener.local_addr().expect("address"));
        let state = Arc::new(FixtureState {
            base: base.clone(),
            lifecycle,
            challenge,
            valid_tokens: Mutex::new(tokens.iter().map(|token| (*token).to_owned()).collect()),
            requests: Mutex::new(Vec::new()),
            oauth: Mutex::new(OAuthServerState::default()),
        });
        let app = axum::Router::new()
            .route("/mcp", any(mcp_request))
            .route(
                "/.well-known/oauth-protected-resource/mcp",
                get(protected_resource),
            )
            .route(
                "/.well-known/oauth-protected-resource",
                get(protected_resource),
            )
            .route(
                "/.well-known/oauth-authorization-server",
                get(authorization_server),
            )
            .route("/register", post(register))
            .route("/authorize", get(authorize))
            .route("/token", post(token))
            .with_state(state.clone());
        let task = tokio::spawn(async move {
            axum::serve(listener, app).await.expect("serve fixture");
        });
        Self {
            url: format!("{base}/mcp"),
            state,
            task,
        }
    }

    pub(crate) fn rotate(&self, revoke: &str, accept: &str) {
        let mut tokens = self.state.valid_tokens.lock().unwrap();
        tokens.remove(revoke);
        tokens.insert(accept.to_owned());
    }

    pub(crate) fn observed(&self) -> Vec<Observed> {
        self.state.requests.lock().unwrap().clone()
    }

    fn executed_calls(&self) -> usize {
        self.observed()
            .iter()
            .filter(|request| request.rpc_method == "tools/call" && request.accepted)
            .count()
    }

    fn server(&self, name: &str, auth: &McpAuth) -> McpServer {
        McpServer::Http(McpServerHttp::new(name, self.url.clone()).meta(auth.to_meta()))
    }
}

async fn mcp_request(
    State(state): State<Arc<FixtureState>>,
    method: Method,
    headers: HeaderMap,
    bytes: Bytes,
) -> Response {
    let request = serde_json::from_slice::<Value>(&bytes).unwrap_or(Value::Null);
    let rpc_method = request["method"].as_str().unwrap_or_default().to_owned();
    let authorization = headers
        .get(header::AUTHORIZATION)
        .and_then(|value| value.to_str().ok())
        .map(str::to_owned);
    let accepted = authorization
        .as_deref()
        .and_then(|value| value.strip_prefix("Bearer "))
        .is_some_and(|token| state.valid_tokens.lock().unwrap().contains(token));
    state.requests.lock().unwrap().push(Observed {
        rpc_method: rpc_method.clone(),
        authorization,
        has_session_id: headers.contains_key("mcp-session-id"),
        accepted,
    });
    if !accepted {
        let mut response = (StatusCode::UNAUTHORIZED, "invalid_token").into_response();
        if state.challenge {
            let scope = state
                .oauth
                .lock()
                .unwrap()
                .challenge_scope
                .as_ref()
                .map(|scope| format!(", scope=\"{scope}\""))
                .unwrap_or_default();
            let challenge = format!(
                "Bearer error=\"invalid_token\", resource_metadata=\"{}/.well-known/oauth-protected-resource/mcp\"{scope}",
                state.base
            );
            response.headers_mut().insert(
                header::WWW_AUTHENTICATE,
                HeaderValue::from_str(&challenge).unwrap(),
            );
        }
        return response;
    }
    if method != Method::POST {
        return StatusCode::METHOD_NOT_ALLOWED.into_response();
    }
    if rpc_method == "tools/call" {
        let oauth = state.oauth.lock().unwrap();
        let token = headers[header::AUTHORIZATION]
            .to_str()
            .unwrap()
            .trim_start_matches("Bearer ");
        if let Some(required) = &oauth.required_call_scope
            && !oauth
                .token_scopes
                .get(token)
                .is_some_and(|granted| granted.split(' ').any(|scope| scope == required))
        {
            let challenge = format!(
                "Bearer error=\"insufficient_scope\", scope=\"{required}\", resource_metadata=\"{}/.well-known/oauth-protected-resource/mcp\"",
                state.base
            );
            return (
                StatusCode::FORBIDDEN,
                [(header::WWW_AUTHENTICATE, challenge)],
            )
                .into_response();
        }
    }
    if request.get("id").is_none() {
        return StatusCode::ACCEPTED.into_response();
    }
    let result = match (state.lifecycle, rpc_method.as_str()) {
        (Lifecycle::Legacy, "server/discover") => {
            return (
                StatusCode::NOT_FOUND,
                "Legacy endpoint requires initialization",
            )
                .into_response();
        }
        (Lifecycle::Modern, "server/discover") => json!({
            "resultType": "complete", "supportedVersions": [MODERN_VERSION],
            "capabilities": {"tools": {}}, "ttlMs": 0, "cacheScope": "private",
            "_meta": {"io.modelcontextprotocol/serverInfo": {"name": "auth-fixture", "version": "1"}}
        }),
        (Lifecycle::Legacy, "initialize") => json!({
            "protocolVersion": request["params"]["protocolVersion"],
            "capabilities": {"tools": {}}, "serverInfo": {"name": "legacy-auth-fixture", "version": "1"}
        }),
        (_, "tools/list") => json!({"resultType": "complete", "tools": [{
            "name": "echo", "inputSchema": {"type": "object", "properties": {"value": {"type": "string"}}}
        }]}),
        (_, "tools/call") => json!({
            "resultType": "complete",
            "content": [{"type": "text", "text": request["params"]["arguments"]["value"]}],
            "isError": false
        }),
        _ => return StatusCode::NOT_FOUND.into_response(),
    };
    Json(json!({"jsonrpc": "2.0", "id": request["id"], "result": result})).into_response()
}

async fn protected_resource(State(state): State<Arc<FixtureState>>) -> Response {
    if state.oauth.lock().unwrap().withhold_resource_metadata {
        return StatusCode::NOT_FOUND.into_response();
    }
    Json(json!({
        "resource": format!("{}/mcp", state.base),
        "authorization_servers": [state.base],
        "scopes_supported": ["mcp"],
        "bearer_methods_supported": ["header"]
    }))
    .into_response()
}

async fn authorization_server(State(state): State<Arc<FixtureState>>) -> Json<Value> {
    let base = &state.base;
    let advertise = state.oauth.lock().unwrap().advertise_issuer_parameter;
    Json(json!({
        "authorization_response_iss_parameter_supported": advertise,
        "issuer": base,
        "authorization_endpoint": format!("{base}/authorize"),
        "token_endpoint": format!("{base}/token"),
        "registration_endpoint": format!("{base}/register"),
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "scopes_supported": ["mcp"]
    }))
}

async fn register(Json(request): Json<Value>) -> Response {
    (
        StatusCode::CREATED,
        Json(json!({
            "client_id": "registered-client",
            "redirect_uris": request["redirect_uris"],
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"]
        })),
    )
        .into_response()
}

async fn authorize(
    State(state): State<Arc<FixtureState>>,
    Query(query): Query<HashMap<String, String>>,
) -> Response {
    assert_eq!(query["response_type"], "code");
    assert_eq!(query["code_challenge_method"], "S256");
    assert_eq!(query["resource"], format!("{}/mcp", state.base));
    let mut oauth = state.oauth.lock().unwrap();
    let code = format!("code-{}", oauth.codes.len() + oauth.issued);
    let scope = query.get("scope").cloned().unwrap_or_default();
    oauth.requested_scopes.push(scope.clone());
    oauth
        .codes
        .insert(code.clone(), (query["code_challenge"].clone(), scope));
    let issuer = match oauth.issuer_parameter {
        IssuerParameter::Omit => String::new(),
        IssuerParameter::Correct => format!("&iss={}", encode(&state.base)),
        IssuerParameter::Wrong => format!("&iss={}", encode("https://attacker.example")),
    };
    let location = format!(
        "{}?code={code}&state={}{issuer}",
        query["redirect_uri"],
        encode(&query["state"])
    );
    (StatusCode::FOUND, [(header::LOCATION, location)]).into_response()
}

fn encode(value: &str) -> String {
    url::form_urlencoded::byte_serialize(value.as_bytes()).collect()
}

async fn token(State(state): State<Arc<FixtureState>>, body: Bytes) -> Response {
    let form: HashMap<String, String> = url::form_urlencoded::parse(&body).into_owned().collect();
    assert_eq!(
        form.get("resource"),
        Some(&format!("{}/mcp", state.base)),
        "token requests carry the RFC 8707 resource"
    );
    let invalid_grant = || {
        (
            StatusCode::BAD_REQUEST,
            Json(json!({"error": "invalid_grant"})),
        )
            .into_response()
    };
    let mut oauth = state.oauth.lock().unwrap();
    let scope = match form.get("grant_type").map(String::as_str) {
        Some("authorization_code") => {
            let Some((challenge, scope)) = oauth.codes.remove(&form["code"]) else {
                return invalid_grant();
            };
            let verifier = form.get("code_verifier").expect("PKCE verifier");
            let computed = base64::engine::general_purpose::URL_SAFE_NO_PAD
                .encode(Sha256::digest(verifier.as_bytes()));
            if computed != challenge {
                return invalid_grant();
            }
            scope
        }
        Some("refresh_token") => {
            let Some(scope) = oauth.refresh_tokens.remove(&form["refresh_token"]) else {
                return invalid_grant();
            };
            oauth.refreshes += 1;
            scope
        }
        _ => return invalid_grant(),
    };
    oauth.issued += 1;
    let access = format!("oauth-access-{}", oauth.issued);
    let refresh = format!("oauth-refresh-{}", oauth.issued);
    oauth.refresh_tokens.insert(refresh.clone(), scope.clone());
    oauth.token_scopes.insert(access.clone(), scope.clone());
    state.valid_tokens.lock().unwrap().insert(access.clone());
    Json(json!({
        "access_token": access, "token_type": "Bearer", "expires_in": 3600,
        "refresh_token": refresh, "scope": scope
    }))
    .into_response()
}

type Respond = dyn Fn(&BrokerRequest, usize) -> Result<Credential, CredentialError> + Send + Sync;

/// Stands in for the ACP client: answers each credential request from a
/// script and records what it was asked.
struct TestBroker {
    calls: Mutex<Vec<(String, FetchReason)>>,
    respond: Box<Respond>,
    delay: Duration,
}

impl TestBroker {
    fn new(
        respond: impl Fn(&BrokerRequest, usize) -> Result<Credential, CredentialError>
        + Send
        + Sync
        + 'static,
    ) -> Arc<Self> {
        Arc::new(Self {
            calls: Mutex::new(Vec::new()),
            respond: Box::new(respond),
            delay: Duration::ZERO,
        })
    }

    fn reasons(&self) -> Vec<FetchReason> {
        self.calls
            .lock()
            .unwrap()
            .iter()
            .map(|(_, reason)| *reason)
            .collect()
    }
}

impl CredentialBroker for TestBroker {
    fn fetch(&self, request: BrokerRequest) -> CredentialFuture<'static> {
        let index = {
            let mut calls = self.calls.lock().unwrap();
            calls.push((request.binding.clone(), request.reason));
            calls.len() - 1
        };
        let result = (self.respond)(&request, index);
        let delay = self.delay;
        Box::pin(async move {
            tokio::time::sleep(delay).await;
            result
        })
    }
}

fn granted(token: &str) -> Result<Credential, CredentialError> {
    Ok(Credential {
        access_token: Secret::new(token),
        expires_at: None,
    })
}

fn broker_auth(binding: &str) -> McpAuth {
    McpAuth::Broker {
        binding: binding.to_owned(),
    }
}

fn broker_environment(broker: Arc<TestBroker>) -> AuthEnvironment {
    AuthEnvironment {
        arabica_home: None,
        broker: Some(broker),
    }
}

async fn connect_with(
    servers: Vec<McpServer>,
    environment: &AuthEnvironment,
) -> Result<McpTools, String> {
    tokio::time::timeout(
        Duration::from_secs(10),
        McpTools::connect(servers, Path::new("/"), environment),
    )
    .await
    .expect("connection timed out")
}

async fn echo(tools: &mut McpTools, server: &str, value: &str) -> Result<String, String> {
    let response = tokio::time::timeout(
        Duration::from_secs(10),
        tools.execute(ToolExecutionRequest {
            run_id: RunId::new("auth-run"),
            call: ToolCallItem {
                id: None,
                call_id: format!("call-{value}"),
                name: format!("mcp__{server}__echo"),
                arguments: json!({"value": value}),
                provider_state: None,
            },
        }),
    )
    .await
    .expect("tool call timed out")
    .map_err(|error| error.to_string())?;
    let content = serde_json::to_string(&response.result.content).unwrap();
    // A refused call is a failed tool result the model sees.
    if response.result.is_error {
        return Err(content);
    }
    Ok(content)
}

#[tokio::test]
async fn modern_lifecycle_sends_the_resolved_bearer_on_every_request() {
    let fixture = Fixture::start(Lifecycle::Modern, true, &["token-a"]).await;
    let broker = TestBroker::new(|_, _| granted("token-a"));
    let mut tools = connect_with(
        vec![fixture.server("remote", &broker_auth("account-a"))],
        &broker_environment(broker.clone()),
    )
    .await
    .expect("connects");
    assert!(
        echo(&mut tools, "remote", "hello")
            .await
            .unwrap()
            .contains("hello")
    );
    let requests = fixture.observed();
    let methods = requests
        .iter()
        .map(|request| request.rpc_method.as_str())
        .collect::<Vec<_>>();
    assert_eq!(methods, ["server/discover", "tools/list", "tools/call"]);
    assert!(requests.iter().all(|request| {
        request.authorization.as_deref() == Some("Bearer token-a")
            && request.accepted
            && !request.has_session_id
    }));
    assert_eq!(broker.reasons(), [FetchReason::Initial]);
}

#[tokio::test]
async fn a_rotated_token_is_picked_up_on_the_same_connection_after_one_401() {
    for challenge in [true, false] {
        let fixture = Fixture::start(Lifecycle::Modern, challenge, &["token-1"]).await;
        let broker = TestBroker::new(|_, index| granted(&format!("token-{}", index + 1)));
        let mut tools = connect_with(
            vec![fixture.server("remote", &broker_auth("account"))],
            &broker_environment(broker.clone()),
        )
        .await
        .expect("connects");
        echo(&mut tools, "remote", "before").await.unwrap();
        fixture.rotate("token-1", "token-2");
        echo(&mut tools, "remote", "after").await.unwrap();
        assert_eq!(
            broker.reasons(),
            [FetchReason::Initial, FetchReason::Rejected]
        );
        let calls = fixture
            .observed()
            .into_iter()
            .filter(|request| request.rpc_method == "tools/call")
            .collect::<Vec<_>>();
        // The rejected attempt was refused before execution; the retry is
        // the only one the server ran.
        assert_eq!(calls.len(), 3);
        assert!(!calls[1].accepted);
        assert_eq!(calls[2].authorization.as_deref(), Some("Bearer token-2"));
        assert_eq!(fixture.executed_calls(), 2);
    }
}

#[tokio::test]
async fn six_concurrent_calls_with_an_expired_token_cause_exactly_one_fetch() {
    let fixture = Fixture::start(Lifecycle::Modern, true, &["short-lived", "renewed"]).await;
    let broker = TestBroker::new(|_, index| {
        Ok(Credential {
            access_token: Secret::new(if index == 0 { "short-lived" } else { "renewed" }),
            // Usable for connection setup, then inside the expiry skew.
            expires_at: Some(SystemTime::now() + Duration::from_secs(32)),
        })
    });
    let tools = connect_with(
        vec![fixture.server("remote", &broker_auth("account"))],
        &broker_environment(broker.clone()),
    )
    .await
    .expect("connects");
    tokio::time::sleep(Duration::from_millis(2500)).await;
    let client = &tools.clients[0];
    let calls = (0..6).map(|index| {
        client.call_tool(
            CallToolRequestParams::new("echo").with_arguments(
                json!({"value": format!("call-{index}")})
                    .as_object()
                    .unwrap()
                    .clone(),
            ),
        )
    });
    let results = futures_util::future::join_all(calls).await;
    assert!(results.iter().all(Result::is_ok), "{results:?}");
    assert_eq!(
        broker.reasons(),
        [FetchReason::Initial, FetchReason::Expiring]
    );
    let calls = fixture
        .observed()
        .into_iter()
        .filter(|request| request.rpc_method == "tools/call")
        .collect::<Vec<_>>();
    assert_eq!(calls.len(), 6);
    assert!(
        calls
            .iter()
            .all(|request| request.authorization.as_deref() == Some("Bearer renewed"))
    );
}

#[tokio::test]
async fn concurrent_rejections_share_one_replacement() {
    let fixture = Fixture::start(Lifecycle::Modern, true, &["old"]).await;
    let broker = TestBroker::new(|_, index| granted(if index == 0 { "old" } else { "new" }));
    let tools = connect_with(
        vec![fixture.server("remote", &broker_auth("account"))],
        &broker_environment(broker.clone()),
    )
    .await
    .expect("connects");
    fixture.rotate("old", "new");
    let client = &tools.clients[0];
    let calls = (0..6).map(|index| {
        client.call_tool(
            CallToolRequestParams::new("echo").with_arguments(
                json!({"value": format!("call-{index}")})
                    .as_object()
                    .unwrap()
                    .clone(),
            ),
        )
    });
    let results = futures_util::future::join_all(calls).await;
    assert!(results.iter().all(Result::is_ok), "{results:?}");
    assert_eq!(
        broker.reasons(),
        [FetchReason::Initial, FetchReason::Rejected]
    );
    assert_eq!(fixture.executed_calls(), 6);
}

#[tokio::test]
async fn revocation_fails_visibly_without_unauthenticated_requests_or_loops() {
    let fixture = Fixture::start(Lifecycle::Modern, true, &["granted"]).await;
    let broker = TestBroker::new(|request, _| match request.reason {
        FetchReason::Rejected => Err(CredentialError::NeedsAuthorization(
            "the account was disconnected".to_owned(),
        )),
        _ => granted("granted"),
    });
    let mut tools = connect_with(
        vec![fixture.server("remote", &broker_auth("account"))],
        &broker_environment(broker.clone()),
    )
    .await
    .expect("connects");
    fixture.rotate("granted", "unrelated");
    let error = echo(&mut tools, "remote", "revoked").await.unwrap_err();
    assert!(error.contains("authorization required"), "{error}");
    assert!(error.contains("the account was disconnected"), "{error}");
    assert!(!error.contains("granted"), "{error}");
    let calls = fixture
        .observed()
        .into_iter()
        .filter(|request| request.rpc_method == "tools/call")
        .collect::<Vec<_>>();
    assert_eq!(calls.len(), 1, "no retry without a replacement credential");
    assert!(
        fixture
            .observed()
            .iter()
            .all(|request| request.authorization.is_some())
    );
    // A provider that keeps handing back the rejected token is no better.
    let broker = TestBroker::new(|_, _| granted("stale"));
    let fixture = Fixture::start(Lifecycle::Modern, true, &["stale"]).await;
    let mut tools = connect_with(
        vec![fixture.server("remote", &broker_auth("account"))],
        &broker_environment(broker.clone()),
    )
    .await
    .expect("connects");
    fixture.rotate("stale", "unrelated");
    let error = echo(&mut tools, "remote", "stale").await.unwrap_err();
    assert!(error.contains("no replacement is available"), "{error}");
    assert_eq!(
        broker.reasons(),
        [FetchReason::Initial, FetchReason::Rejected]
    );
}

#[tokio::test]
async fn a_missing_credential_fails_before_any_request() {
    let fixture = Fixture::start(Lifecycle::Modern, true, &[]).await;
    let broker = TestBroker::new(|_, _| {
        Err(CredentialError::Unavailable(
            "the vault is sealed".to_owned(),
        ))
    });
    let error = connect_with(
        vec![fixture.server("remote", &broker_auth("account"))],
        &broker_environment(broker),
    )
    .await
    .expect_err("connection must fail");
    assert!(
        error.contains("credential unavailable: the vault is sealed"),
        "{error}"
    );
    assert!(fixture.observed().is_empty());
}

#[tokio::test]
async fn two_bindings_for_the_same_url_never_share_a_credential() {
    let fixture = Fixture::start(Lifecycle::Modern, true, &["token-for-a", "token-for-b"]).await;
    let broker = TestBroker::new(|request, _| granted(&format!("token-for-{}", request.binding)));
    let environment = broker_environment(broker.clone());
    let mut first = connect_with(
        vec![fixture.server("remote", &broker_auth("a"))],
        &environment,
    )
    .await
    .expect("first binding connects");
    let mut second = connect_with(
        vec![fixture.server("remote", &broker_auth("b"))],
        &environment,
    )
    .await
    .expect("second binding connects");
    echo(&mut first, "remote", "first").await.unwrap();
    echo(&mut second, "remote", "second").await.unwrap();
    // Revoking one account leaves the other connection untouched.
    fixture.rotate("token-for-a", "unrelated");
    echo(&mut second, "remote", "still-second").await.unwrap();
    let tokens = fixture
        .observed()
        .into_iter()
        .filter(|request| request.rpc_method == "tools/call")
        .map(|request| request.authorization.unwrap())
        .collect::<Vec<_>>();
    assert_eq!(
        tokens,
        [
            "Bearer token-for-a",
            "Bearer token-for-b",
            "Bearer token-for-b"
        ]
    );
    let calls = broker.calls.lock().unwrap().clone();
    assert_eq!(
        calls,
        [
            ("a".to_owned(), FetchReason::Initial),
            ("b".to_owned(), FetchReason::Initial)
        ]
    );
}

#[tokio::test]
async fn cross_origin_redirects_do_not_receive_the_bearer() {
    let target = Fixture::start(Lifecycle::Modern, true, &["secret-bearer"]).await;
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let location = target.url.clone();
    let app = axum::Router::new().route(
        "/mcp",
        any(move || {
            let location = location.clone();
            async move {
                (
                    StatusCode::TEMPORARY_REDIRECT,
                    [(header::LOCATION, location)],
                )
            }
        }),
    );
    let task = tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
    let broker = TestBroker::new(|_, _| granted("secret-bearer"));
    let server = McpServer::Http(
        McpServerHttp::new("remote", format!("http://{address}/mcp"))
            .meta(broker_auth("account").to_meta()),
    );
    let error = connect_with(vec![server], &broker_environment(broker))
        .await
        .expect_err("cross-origin redirect must stop");
    assert!(
        target.observed().is_empty(),
        "another origin received a request"
    );
    assert!(!error.contains("secret-bearer"), "{error}");
    task.abort();
}

#[test]
fn credentials_are_redacted_from_debug_output() {
    let credential = Credential {
        access_token: Secret::new("very-secret"),
        expires_at: None,
    };
    assert!(!format!("{credential:?}").contains("very-secret"));
    let provided = super::auth::ProvidedCredential::Granted {
        access_token: "very-secret".to_owned(),
        expires_in_seconds: Some(60),
    };
    assert!(!format!("{provided:?}").contains("very-secret"));
}

#[tokio::test]
async fn legacy_servers_keep_working_with_dynamic_credentials() {
    let fixture = Fixture::start(Lifecycle::Legacy, true, &["legacy-token"]).await;
    let broker = TestBroker::new(|_, _| granted("legacy-token"));
    let mut tools = connect_with(
        vec![fixture.server("remote", &broker_auth("account"))],
        &broker_environment(broker),
    )
    .await
    .expect("legacy server connects");
    echo(&mut tools, "remote", "legacy").await.unwrap();
    let requests = fixture.observed();
    assert_eq!(requests[0].rpc_method, "server/discover");
    assert!(
        requests
            .iter()
            .any(|request| request.rpc_method == "initialize")
    );
    assert!(
        requests
            .iter()
            .all(|request| request.authorization.as_deref() == Some("Bearer legacy-token"))
    );
}

#[tokio::test]
async fn an_authorization_failure_during_discovery_never_downgrades() {
    for challenge in [true, false] {
        let fixture = Fixture::start(Lifecycle::Modern, challenge, &[]).await;
        let broker = TestBroker::new(|_, index| granted(&format!("never-accepted-{index}")));
        let error = connect_with(
            vec![fixture.server("remote", &broker_auth("account"))],
            &broker_environment(broker.clone()),
        )
        .await
        .expect_err("rejected discovery must fail");
        assert!(!error.contains("never-accepted"), "{error}");
        let methods = fixture
            .observed()
            .into_iter()
            .map(|request| request.rpc_method)
            .collect::<Vec<_>>();
        // One retry with the replacement, then the failure surfaces: a 401
        // never identifies a legacy server.
        assert_eq!(methods, ["server/discover", "server/discover"]);
        assert_eq!(
            broker.reasons(),
            [FetchReason::Initial, FetchReason::Rejected]
        );
    }
}

#[tokio::test]
async fn dynamic_modes_reject_conflicting_or_unauthorized_declarations() {
    let fixture = Fixture::start(Lifecycle::Modern, true, &[]).await;
    let broker = TestBroker::new(|_, _| granted("unused"));
    let environment = broker_environment(broker.clone());
    let with_header = McpServer::Http(
        McpServerHttp::new("remote", fixture.url.clone())
            .headers(vec![HttpHeader::new("Authorization", "Bearer static")])
            .meta(broker_auth("account").to_meta()),
    );
    let error = connect_with(vec![with_header], &environment)
        .await
        .err()
        .unwrap();
    assert!(
        error.contains("cannot combine an Authorization header"),
        "{error}"
    );
    // Only the ACP client can be a broker, and only the user config may
    // name a program to run.
    let (_, diagnostics) = McpTools::connect_lenient(
        vec![fixture.server("remote", &broker_auth("account"))],
        Path::new("/"),
        &environment,
    )
    .await;
    assert!(
        diagnostics[0].contains("only an ACP client can supply"),
        "{diagnostics:?}"
    );
    let command = McpAuth::Command {
        command: vec!["/bin/echo".to_owned()],
        binding: None,
    };
    let error = connect_with(vec![fixture.server("remote", &command)], &environment)
        .await
        .err()
        .unwrap();
    assert!(
        error.contains("accepted only from the user config file"),
        "{error}"
    );
    let invalid = McpServer::Http(McpServerHttp::new("remote", fixture.url.clone()).meta(
        serde_json::Map::from_iter([(auth::META_KEY.to_owned(), json!({"mode": "magic"}))]),
    ));
    let error = connect_with(vec![invalid], &environment)
        .await
        .err()
        .unwrap();
    assert!(
        error.contains("invalid arabica.auth declaration"),
        "{error}"
    );
    assert!(broker.reasons().is_empty());
    assert!(fixture.observed().is_empty());
}

#[cfg(unix)]
#[tokio::test]
async fn a_configured_credential_command_supplies_the_token() {
    use std::os::unix::fs::PermissionsExt;

    let fixture = Fixture::start(Lifecycle::Modern, true, &["from-command"]).await;
    let root = tempfile::tempdir().unwrap();
    let script = root.path().join("credential.sh");
    std::fs::write(
        &script,
        "#!/bin/sh\n[ \"$ARABICA_MCP_BINDING\" = work ] || exit 3\n[ \"$ARABICA_MCP_REASON\" = initial ] || exit 4\nprintf '{\"outcome\":\"granted\",\"accessToken\":\"from-command\",\"expiresInSeconds\":3600}'\n",
    )
    .unwrap();
    std::fs::set_permissions(&script, std::fs::Permissions::from_mode(0o700)).unwrap();
    let auth = McpAuth::Command {
        command: vec![script.display().to_string()],
        binding: Some("work".to_owned()),
    };
    let (mut tools, diagnostics) = McpTools::connect_lenient(
        vec![fixture.server("remote", &auth)],
        Path::new("/"),
        &AuthEnvironment::default(),
    )
    .await;
    assert!(diagnostics.is_empty(), "{diagnostics:?}");
    echo(&mut tools, "remote", "command").await.unwrap();
    assert!(
        fixture
            .observed()
            .iter()
            .all(|request| request.authorization.as_deref() == Some("Bearer from-command"))
    );
}

/// Follow the authorization URL like a browser that the person approves.
fn approve_in_browser(authorization_url: &str) {
    let url = authorization_url.to_owned();
    tokio::spawn(async move {
        let client = reqwest::Client::builder()
            .redirect(reqwest::redirect::Policy::none())
            .build()
            .unwrap();
        let response = client.get(&url).send().await.expect("authorize");
        assert_eq!(response.status(), reqwest::StatusCode::FOUND);
        let callback = response.headers()[reqwest::header::LOCATION]
            .to_str()
            .unwrap()
            .to_owned();
        let page = client.get(&callback).send().await.expect("callback");
        assert!(page.status().is_success());
    });
}

fn oauth_target(fixture: &Fixture, binding: &str) -> oauth::OAuthTarget {
    oauth::OAuthTarget {
        server: "remote".to_owned(),
        url: fixture.url.clone(),
        binding: binding.to_owned(),
        configured: true,
    }
}

fn oauth_auth(binding: &str) -> McpAuth {
    McpAuth::Oauth {
        binding: Some(binding.to_owned()),
        client_id: None,
        client_metadata_url: None,
        scopes: Vec::new(),
    }
}

fn grant_files(home: &Path) -> Vec<std::path::PathBuf> {
    std::fs::read_dir(home.join("mcp-oauth"))
        .map(|entries| {
            entries
                .map(|entry| entry.unwrap().path())
                .filter(|path| {
                    path.extension()
                        .is_some_and(|extension| extension == "json")
                })
                .collect()
        })
        .unwrap_or_default()
}

#[tokio::test]
async fn initial_oauth_login_uses_pkce_and_stores_an_owner_only_grant() {
    let fixture = Fixture::start(Lifecycle::Modern, true, &[]).await;
    let home = tempfile::tempdir().unwrap();
    let environment = AuthEnvironment {
        arabica_home: Some(home.path().to_path_buf()),
        broker: None,
    };
    // Before login the server is unusable, and says how to fix it.
    let (_, diagnostics) = McpTools::connect_lenient(
        vec![fixture.server("remote", &oauth_auth("work"))],
        Path::new("/"),
        &environment,
    )
    .await;
    assert!(
        diagnostics[0].contains("run `arabica mcp login remote`"),
        "{diagnostics:?}"
    );
    oauth::login(
        oauth::LoginRequest {
            target: oauth_target(&fixture, "work"),
            client_id: None,
            client_metadata_url: None,
            scopes: Vec::new(),
        },
        home.path(),
        approve_in_browser,
    )
    .await
    .expect("login completes");
    let files = grant_files(home.path());
    assert_eq!(files.len(), 1);
    let stored = std::fs::read_to_string(&files[0]).unwrap();
    assert!(stored.contains("oauth-access-1"));
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        assert_eq!(
            std::fs::metadata(&files[0]).unwrap().permissions().mode() & 0o777,
            0o600
        );
    }
    let (mut tools, diagnostics) = McpTools::connect_lenient(
        vec![fixture.server("remote", &oauth_auth("work"))],
        Path::new("/"),
        &environment,
    )
    .await;
    assert!(diagnostics.is_empty(), "{diagnostics:?}");
    echo(&mut tools, "remote", "authorized").await.unwrap();
    assert!(
        fixture
            .observed()
            .iter()
            .filter(|request| request.accepted)
            .all(|request| request.authorization.as_deref() == Some("Bearer oauth-access-1"))
    );
    // Another binding for the same URL has no grant of its own.
    let (_, diagnostics) = McpTools::connect_lenient(
        vec![fixture.server("remote", &oauth_auth("personal"))],
        Path::new("/"),
        &environment,
    )
    .await;
    assert!(
        diagnostics[0].contains("has no OAuth authorization"),
        "{diagnostics:?}"
    );
    assert!(oauth::logout(&oauth_target(&fixture, "work"), home.path()).unwrap());
    assert!(grant_files(home.path()).is_empty());
}

#[tokio::test]
async fn an_existing_oauth_grant_refreshes_on_expiry_and_rejection_until_revoked() {
    let fixture = Fixture::start(Lifecycle::Modern, true, &[]).await;
    let home = tempfile::tempdir().unwrap();
    oauth::login(
        oauth::LoginRequest {
            target: oauth_target(&fixture, "work"),
            client_id: None,
            client_metadata_url: None,
            scopes: Vec::new(),
        },
        home.path(),
        approve_in_browser,
    )
    .await
    .expect("login completes");
    // Age the stored grant past its lifetime.
    let file = grant_files(home.path()).remove(0);
    let mut grant: Value = serde_json::from_str(&std::fs::read_to_string(&file).unwrap()).unwrap();
    grant["credentials"]["token_received_at"] = json!(0);
    std::fs::write(&file, serde_json::to_vec(&grant).unwrap()).unwrap();

    let environment = AuthEnvironment {
        arabica_home: Some(home.path().to_path_buf()),
        broker: None,
    };
    let (mut tools, diagnostics) = McpTools::connect_lenient(
        vec![fixture.server("remote", &oauth_auth("work"))],
        Path::new("/"),
        &environment,
    )
    .await;
    assert!(diagnostics.is_empty(), "{diagnostics:?}");
    assert_eq!(fixture.state.oauth.lock().unwrap().refreshes, 1);
    echo(&mut tools, "remote", "refreshed").await.unwrap();

    // The resource server revokes the access token: one refresh, one retry.
    fixture.rotate("oauth-access-2", "unrelated");
    echo(&mut tools, "remote", "rejected-then-refreshed")
        .await
        .unwrap();
    assert_eq!(fixture.state.oauth.lock().unwrap().refreshes, 2);

    // The grant is revoked at the authorization server as well.
    fixture.state.oauth.lock().unwrap().refresh_tokens.clear();
    fixture.rotate("oauth-access-3", "unrelated");
    let error = echo(&mut tools, "remote", "revoked").await.unwrap_err();
    assert!(error.contains("authorization required"), "{error}");
    assert!(error.contains("arabica mcp login remote"), "{error}");
    assert!(!error.contains("oauth-access"), "{error}");
    assert!(!error.contains("oauth-refresh"), "{error}");
    assert_eq!(fixture.state.oauth.lock().unwrap().refreshes, 2);
}

fn login_request(fixture: &Fixture, scopes: &[&str]) -> oauth::LoginRequest {
    oauth::LoginRequest {
        target: oauth_target(fixture, "work"),
        client_id: None,
        client_metadata_url: None,
        scopes: scopes.iter().map(|scope| (*scope).to_owned()).collect(),
    }
}

#[tokio::test]
async fn oauth_login_validates_the_authorization_response_issuer() {
    for (advertise, issuer, accepted) in [
        (true, IssuerParameter::Correct, true),
        (false, IssuerParameter::Correct, true),
        (false, IssuerParameter::Omit, true),
        // RFC 9207 as applied by MCP: an advertised `iss` must be present,
        // and a present one must match whether or not it was advertised.
        (true, IssuerParameter::Omit, false),
        (true, IssuerParameter::Wrong, false),
        (false, IssuerParameter::Wrong, false),
    ] {
        let fixture = Fixture::start(Lifecycle::Modern, true, &[]).await;
        {
            let mut oauth = fixture.state.oauth.lock().unwrap();
            oauth.advertise_issuer_parameter = advertise;
            oauth.issuer_parameter = issuer;
        }
        let home = tempfile::tempdir().unwrap();
        let result = oauth::login(
            login_request(&fixture, &[]),
            home.path(),
            approve_in_browser,
        )
        .await;
        assert_eq!(
            result.is_ok(),
            accepted,
            "advertise={advertise}: {result:?}"
        );
        assert_eq!(grant_files(home.path()).len(), usize::from(accepted));
        if !accepted {
            assert_eq!(
                fixture.state.oauth.lock().unwrap().issued,
                0,
                "the code must not reach the token endpoint"
            );
        }
    }
}

#[tokio::test]
async fn oauth_login_requires_protected_resource_metadata() {
    let fixture = Fixture::start(Lifecycle::Modern, true, &[]).await;
    fixture
        .state
        .oauth
        .lock()
        .unwrap()
        .withhold_resource_metadata = true;
    let home = tempfile::tempdir().unwrap();
    let error = oauth::login(login_request(&fixture, &[]), home.path(), |_| {
        panic!("no authorization may start without RFC 9728 discovery")
    })
    .await
    .unwrap_err();
    assert!(
        error.contains("Protected Resource Metadata (RFC 9728)"),
        "{error}"
    );
    assert!(grant_files(home.path()).is_empty());
}

#[tokio::test]
async fn oauth_scopes_follow_the_challenge_and_step_up_keeps_granted_ones() {
    let fixture = Fixture::start(Lifecycle::Modern, true, &[]).await;
    fixture.state.oauth.lock().unwrap().challenge_scope = Some("files:read".to_owned());
    let home = tempfile::tempdir().unwrap();
    oauth::login(
        login_request(&fixture, &[]),
        home.path(),
        approve_in_browser,
    )
    .await
    .expect("login completes");
    // The challenge is authoritative over `scopes_supported` ("mcp").
    assert_eq!(
        fixture.state.oauth.lock().unwrap().requested_scopes,
        ["files:read"]
    );

    fixture.state.oauth.lock().unwrap().required_call_scope = Some("files:write".to_owned());
    let environment = AuthEnvironment {
        arabica_home: Some(home.path().to_path_buf()),
        broker: None,
    };
    let (mut tools, diagnostics) = McpTools::connect_lenient(
        vec![fixture.server("remote", &oauth_auth("work"))],
        Path::new("/"),
        &environment,
    )
    .await;
    assert!(diagnostics.is_empty(), "{diagnostics:?}");
    let error = echo(&mut tools, "remote", "write").await.unwrap_err();
    assert!(
        error.contains("requires OAuth scope 'files:write'"),
        "{error}"
    );
    assert!(
        error.contains("arabica mcp login remote --scope 'files:write'"),
        "{error}"
    );
    let attempts = fixture
        .observed()
        .into_iter()
        .filter(|request| request.rpc_method == "tools/call")
        .count();
    assert_eq!(attempts, 1, "a 403 is not replayed with the same grant");

    // Step-up: the new authorization asks for the union of scopes.
    oauth::login(
        login_request(&fixture, &["files:write"]),
        home.path(),
        approve_in_browser,
    )
    .await
    .expect("step-up completes");
    let requested = fixture.state.oauth.lock().unwrap().requested_scopes[1].clone();
    let mut requested = requested.split(' ').collect::<Vec<_>>();
    requested.sort_unstable();
    assert_eq!(requested, ["files:read", "files:write"]);
    let (mut tools, diagnostics) = McpTools::connect_lenient(
        vec![fixture.server("remote", &oauth_auth("work"))],
        Path::new("/"),
        &environment,
    )
    .await;
    assert!(diagnostics.is_empty(), "{diagnostics:?}");
    echo(&mut tools, "remote", "write").await.unwrap();
}

#[test]
fn client_metadata_documents_must_be_https_urls_with_a_path() {
    for (url, valid) in [
        ("https://arabica.example/client.json", true),
        ("http://arabica.example/client.json", false),
        ("https://arabica.example/", false),
    ] {
        let auth = McpAuth::Oauth {
            binding: None,
            client_id: None,
            client_metadata_url: Some(url.to_owned()),
            scopes: Vec::new(),
        };
        assert_eq!(auth.validate("remote").is_ok(), valid, "{url}");
    }
}
