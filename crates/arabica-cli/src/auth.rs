//! User-owned credential storage for the terminal CLI.

use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::{SystemTime, UNIX_EPOCH};

use base64::Engine;
use clap::Subcommand;
use jsonwebtoken::{DecodingKey, Validation, decode};
use rand::RngCore;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpListener;
use toml_edit::{Array, DocumentMut, Item, Table, value};
use url::Url;

#[derive(Debug, Subcommand)]
pub enum AuthAction {
    /// Prompt for an API key without echoing it and save it locally.
    Login,
    /// Sign in with ChatGPT and enable eligible ChatGPT plan usage.
    ChatgptLogin,
    /// Show whether a ChatGPT plan connection is stored.
    ChatgptStatus,
    /// Remove the saved ChatGPT plan connection.
    ChatgptLogout,
    /// Report which credential source the terminal CLI will use.
    Status,
    /// Remove the saved API key.
    Logout,
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct AuthFile {
    openai_api_key: String,
}

pub fn auth_path(home: &Path) -> PathBuf {
    home.join("auth.json")
}

pub fn chatgpt_credentials_path(home: &Path) -> PathBuf {
    home.join("chatgpt-auth.json")
}

pub fn read_chatgpt_credentials(
    home: &Path,
) -> Result<Option<arabica_provider::ChatGptCredentials>, Box<dyn std::error::Error>> {
    let path = chatgpt_credentials_path(home);
    let metadata = match fs::symlink_metadata(&path) {
        Ok(metadata) => metadata,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(error.into()),
    };
    if !metadata.file_type().is_file() {
        return Err("ChatGPT credentials must be a regular file".into());
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        if metadata.permissions().mode() & 0o077 != 0 {
            return Err(format!(
                "{} must be readable only by its owner (chmod 600)",
                path.display()
            )
            .into());
        }
    }
    let credentials = serde_json::from_slice(&fs::read(path)?)?;
    Ok(Some(credentials))
}

pub fn read_saved_key(home: &Path) -> Result<Option<String>, Box<dyn std::error::Error>> {
    let path = auth_path(home);
    let metadata = match fs::symlink_metadata(&path) {
        Ok(metadata) => metadata,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(format!("cannot inspect {}: {error}", path.display()).into()),
    };
    if !metadata.file_type().is_file() {
        return Err(format!("{} must be a regular file", path.display()).into());
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        if metadata.permissions().mode() & 0o077 != 0 {
            return Err(format!(
                "{} must be readable only by its owner (chmod 600)",
                path.display()
            )
            .into());
        }
    }
    let auth: AuthFile = serde_json::from_str(&fs::read_to_string(&path)?)
        .map_err(|error| format!("invalid {}: {error}", path.display()))?;
    if auth.openai_api_key.trim().is_empty() {
        return Err(format!("{} contains an empty API key", path.display()).into());
    }
    Ok(Some(auth.openai_api_key))
}

pub fn save_key(home: &Path, key: &str) -> Result<(), Box<dyn std::error::Error>> {
    let key = key.trim();
    if key.is_empty() {
        return Err("API key cannot be empty".into());
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::DirBuilderExt;
        let mut builder = fs::DirBuilder::new();
        builder.recursive(true).mode(0o700).create(home)?;
    }
    #[cfg(not(unix))]
    fs::create_dir_all(home)?;

    let path = auth_path(home);
    let temporary = home.join(format!(".auth-{}.tmp", uuid::Uuid::now_v7()));
    let result = (|| -> Result<(), Box<dyn std::error::Error>> {
        let mut options = OpenOptions::new();
        options.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        let mut file = options.open(&temporary)?;
        serde_json::to_writer(
            &mut file,
            &AuthFile {
                openai_api_key: key.to_owned(),
            },
        )?;
        file.write_all(b"\n")?;
        file.sync_all()?;
        fs::rename(&temporary, &path)?;
        Ok(())
    })();
    if result.is_err() {
        let _ = fs::remove_file(&temporary);
    }
    result
}

pub async fn run(action: AuthAction) -> i32 {
    let home = match arabica_adapters::default_arabica_home() {
        Ok(home) => home,
        Err(error) => {
            eprintln!("error: {error}");
            return 2;
        }
    };
    let result = match action {
        AuthAction::Login => {
            let key = match rpassword::prompt_password("Default provider API key: ") {
                Ok(key) => key,
                Err(error) => {
                    eprintln!("error: reading API key: {error}");
                    return 2;
                }
            };
            save_key(&home, &key).map(|()| {
                println!("API key saved to {}", auth_path(&home).display());
            })
        }
        AuthAction::Status => crate::config::user_config_api_key_env(&home).and_then(|name| {
            if let Some(name) = name
                && std::env::var(&name).is_ok_and(|key| !key.trim().is_empty())
            {
                println!("API key: environment ({name})");
                Ok(())
            } else {
                crate::config::user_config_key(&home).and_then(|key| {
                    if key.is_some() {
                        println!(
                            "API key: user config ({})",
                            crate::config::user_config_path(&home).display()
                        );
                        Ok(())
                    } else {
                        read_saved_key(&home).map(|key| {
                            if key.is_some() {
                                println!("API key: saved ({})", auth_path(&home).display());
                            } else {
                                println!("API key: not configured");
                            }
                        })
                    }
                })
            }
        }),
        AuthAction::ChatgptLogin => {
            return match chatgpt_login(&home).await {
                Ok(email) => {
                    if let Err(error) = configure_chatgpt_default(&home) {
                        eprintln!(
                            "error: ChatGPT is signed in, but Arabica config was not updated: {error}"
                        );
                        return 2;
                    }
                    println!(
                        "Connected to ChatGPT plan{} and configured Arabica's default model",
                        email.map(|e| format!(" as {e}")).unwrap_or_default()
                    );
                    println!("Run `arabica` to start.");
                    0
                }
                Err(error) => {
                    eprintln!("error: {error}");
                    2
                }
            };
        }
        AuthAction::ChatgptStatus => {
            let path = chatgpt_credentials_path(&home);
            match std::fs::read(&path) {
                Ok(bytes) => {
                    match serde_json::from_slice::<arabica_provider::ChatGptCredentials>(&bytes) {
                        Ok(credentials) if !credentials.access_token.is_empty() => {
                            println!(
                                "ChatGPT plan: connected{}",
                                credentials
                                    .email
                                    .map(|e| format!(" ({e})"))
                                    .unwrap_or_default()
                            );
                            return 0;
                        }
                        Ok(credentials) => {
                            println!(
                                "ChatGPT plan: signed out{}",
                                credentials
                                    .email
                                    .map(|e| format!(" ({e})"))
                                    .unwrap_or_default()
                            );
                            return 0;
                        }
                        Err(error) => Err(format!("invalid {}: {error}", path.display()).into()),
                    }
                }
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                    println!("ChatGPT plan: not connected");
                    return 0;
                }
                Err(error) => Err(error.into()),
            }
        }
        AuthAction::ChatgptLogout => {
            return match chatgpt_logout(&home).await {
                Ok(()) => 0,
                Err(error) => {
                    eprintln!("error: {error}");
                    2
                }
            };
        }
        AuthAction::Logout => match fs::remove_file(auth_path(&home)) {
            Ok(()) => {
                println!("Saved API key removed");
                Ok(())
            }
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                println!("No saved API key");
                Ok(())
            }
            Err(error) => Err(error.into()),
        },
    };
    match result {
        Ok(()) => 0,
        Err(error) => {
            eprintln!("error: {error}");
            2
        }
    }
}

/// Point the configured default model at the ChatGPT Responses provider while
/// preserving unrelated user settings and TOML comments.
fn configure_chatgpt_default(home: &Path) -> Result<(), Box<dyn std::error::Error>> {
    let path = crate::config::user_config_path(home);
    let source = match fs::read_to_string(&path) {
        Ok(source) => source,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => String::new(),
        Err(error) => return Err(error.into()),
    };
    let mut document = if source.trim().is_empty() {
        DocumentMut::new()
    } else {
        source.parse::<DocumentMut>()?
    };

    let default_alias = document
        .get("blend")
        .and_then(Item::as_table_like)
        .and_then(|blend| blend.get("default_model"))
        .and_then(Item::as_str)
        .map(str::to_owned)
        .or_else(|| {
            let blend = document.get("blend")?.as_table_like()?;
            let policies = blend.get("policies")?.as_table_like()?;
            let policy_name = blend
                .get("default_policy")
                .and_then(Item::as_str)
                .or_else(|| policies.iter().next().map(|(name, _)| name))?;
            policies
                .get(policy_name)?
                .get("default_model")?
                .as_str()
                .map(str::to_owned)
        })
        .unwrap_or_else(|| "default".to_owned());

    let providers = document["providers"].or_insert(Item::Table(Table::new()));
    let providers = providers
        .as_table_like_mut()
        .ok_or("[providers] must be a TOML table")?;
    let provider_name = if providers
        .get("chatgpt")
        .and_then(Item::as_table_like)
        .and_then(|provider| provider.get("auth"))
        .and_then(Item::as_str)
        .is_some_and(|auth| auth.eq_ignore_ascii_case("chatgpt"))
        || providers.get("chatgpt").is_none()
    {
        "chatgpt".to_owned()
    } else {
        let mut suffix = 1;
        loop {
            let candidate = format!("arabica_chatgpt_{suffix}");
            if providers.get(&candidate).is_none() {
                break candidate;
            }
            suffix += 1;
        }
    };
    let provider = providers
        .entry(&provider_name)
        .or_insert(Item::Table(Table::new()))
        .as_table_like_mut()
        .ok_or("ChatGPT provider entry must be a TOML table")?;
    provider.insert("auth", value("chatgpt"));
    provider.insert("api_type", value("open_ai_responses"));
    provider.remove("api_key");
    provider.remove("api_key_env");
    provider.remove("base_url");

    let models = document["models"].or_insert(Item::Table(Table::new()));
    let models = models
        .as_table_like_mut()
        .ok_or("[models] must be a TOML table")?;
    let model = models
        .entry(&default_alias)
        .or_insert(Item::Table(Table::new()))
        .as_table_like_mut()
        .ok_or("default model entry must be a TOML table")?;
    model.insert("provider", value(provider_name));
    let model_id_is_openai = model
        .get("model_id")
        .and_then(Item::as_str)
        .is_some_and(|id| id.starts_with("gpt-") || id == "chat-latest");
    if !model_id_is_openai {
        model.insert("model_id", value("gpt-6.1-sol"));
    }

    if document.get("blend").is_none() {
        document["blend"] = Item::Table(Table::new());
        document["blend"]["default_model"] = value(&default_alias);
    } else if document["blend"].as_table_like().is_some_and(|blend| {
        blend.get("default_model").is_none() && blend.get("policies").is_none()
    }) {
        document["blend"]["default_model"] = value(&default_alias);
    }

    let blend = document["blend"]
        .as_table_like_mut()
        .ok_or("[blend] must be a TOML table")?;
    if let Some(policies) = blend.get_mut("policies") {
        for (_, policy) in policies
            .as_table_like_mut()
            .ok_or("[blend.policies] must be a TOML table")?
            .iter_mut()
        {
            enable_chatgpt_capabilities(policy, &default_alias)?;
        }
    } else {
        enable_chatgpt_capabilities(&mut document["blend"], &default_alias)?;
    }

    #[cfg(unix)]
    {
        use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
        fs::create_dir_all(home)?;
        let temporary = home.join(format!(".config-{}.tmp", uuid::Uuid::now_v7()));
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .open(&temporary)?;
        file.write_all(document.to_string().as_bytes())?;
        file.sync_all()?;
        fs::rename(&temporary, &path)?;
        fs::set_permissions(&path, fs::Permissions::from_mode(0o600))?;
    }
    #[cfg(not(unix))]
    {
        fs::create_dir_all(home)?;
        fs::write(&path, document.to_string())?;
    }
    Ok(())
}

fn enable_chatgpt_capabilities(
    policy: &mut Item,
    alias: &str,
) -> Result<(), Box<dyn std::error::Error>> {
    let policy = policy
        .as_table_like_mut()
        .ok_or("Blend policy must be a TOML table")?;
    for key in [
        "tool_call_capable_models",
        "typed_completion_capable_models",
    ] {
        let aliases = policy
            .entry(key)
            .or_insert(value(Array::new()))
            .as_array_mut()
            .ok_or("Blend model capabilities must be arrays")?;
        if !aliases.iter().any(|value| value.as_str() == Some(alias)) {
            aliases.push(alias);
        }
    }
    Ok(())
}

#[derive(Deserialize)]
struct OAuthTokenResponse {
    access_token: String,
    refresh_token: String,
    id_token: String,
    expires_in: u64,
    scope: String,
}

#[derive(Clone, Deserialize)]
struct IdTokenClaims {
    sub: String,
    iss: String,
    nonce: String,
    email: Option<String>,
}

async fn chatgpt_login(home: &Path) -> Result<Option<String>, Box<dyn std::error::Error>> {
    const CALLBACK: &str = "http://127.0.0.1:1455/auth/callback";
    const RESOURCE: &str = "https://api.openai.com/v1";
    const AUTHORIZE: &str = "https://auth.openai.com/api/accounts/authorize";
    const TOKEN: &str = "https://auth.openai.com/api/accounts/oauth/token";

    let existing = match std::fs::read(chatgpt_credentials_path(home)) {
        Ok(bytes) => Some(serde_json::from_slice::<
            arabica_provider::ChatGptCredentials,
        >(&bytes)?),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => None,
        Err(error) => return Err(error.into()),
    };
    let listener = TcpListener::bind("127.0.0.1:1455").await?;
    let callback_uri = CALLBACK.to_owned();
    let host_id = existing
        .as_ref()
        .map(|c| c.ext_agent_host_id.clone())
        .unwrap_or_else(|| format!("urn:uuid:{}", uuid::Uuid::now_v7()));
    let client_id = existing
        .as_ref()
        .map(|c| c.client_id.as_str())
        .unwrap_or("dynamic_agent_client");
    let state = random_url_token();
    let nonce = random_url_token();
    let verifier = random_url_token() + &random_url_token();
    let challenge = base64::engine::general_purpose::URL_SAFE_NO_PAD
        .encode(Sha256::digest(verifier.as_bytes()));
    let mut url = Url::parse(AUTHORIZE)?;
    {
        let mut q = url.query_pairs_mut();
        q.append_pair("client_id", client_id)
            .append_pair("response_type", "code")
            .append_pair("redirect_uri", &callback_uri)
            .append_pair(
                "scope",
                "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct",
            )
            .append_pair("resource", RESOURCE)
            .append_pair("state", &state)
            .append_pair("nonce", &nonce)
            .append_pair("code_challenge_method", "S256")
            .append_pair("code_challenge", &challenge)
            .append_pair("ext_agent_host_id", &host_id);
        if existing.is_none() {
            q.append_pair("agent_name_hint", "Arabica");
        } else if let Some(credentials) = &existing {
            if !credentials.id_token.is_empty() {
                q.append_pair("id_token_hint", &credentials.id_token);
            }
            if let Some(email) = &credentials.email {
                q.append_pair("login_hint", email);
            }
        }
    }
    println!("Opening ChatGPT sign-in in your browser…");
    if !open_url(url.as_str()) {
        return Err(
            "could not launch a browser for ChatGPT sign-in; open a desktop session and retry"
                .into(),
        );
    }
    println!("Waiting for the ChatGPT authorization callback on 127.0.0.1:1455…");
    let (mut socket, _) =
        tokio::time::timeout(std::time::Duration::from_secs(600), listener.accept()).await??;
    let mut request = vec![0u8; 8192];
    let read = socket.read(&mut request).await?;
    let request = String::from_utf8_lossy(&request[..read]);
    let target = request
        .lines()
        .next()
        .and_then(|line| line.split_whitespace().nth(1))
        .ok_or("invalid OAuth callback request")?;
    let callback = Url::parse(&format!("http://127.0.0.1:1455{target}"))?;
    if callback.path() != "/auth/callback" {
        return Err("unexpected OAuth callback path".into());
    }
    let params = callback
        .query_pairs()
        .into_owned()
        .collect::<std::collections::HashMap<_, _>>();
    let html = "<!doctype html><title>Arabica</title><p>ChatGPT authorization received. You can return to Arabica.</p>";
    let response = format!(
        "HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
        html.len(),
        html
    );
    socket.write_all(response.as_bytes()).await?;
    if params.get("state") != Some(&state) {
        return Err("OAuth state mismatch".into());
    }
    if let Some(error) = params.get("error") {
        return Err(format!("ChatGPT authorization failed: {error}").into());
    }
    let code = params
        .get("code")
        .ok_or("OAuth callback did not include an authorization code")?;
    let issued_client_id = params
        .get("client_id")
        .map(String::as_str)
        .or_else(|| existing.as_ref().map(|c| c.client_id.as_str()))
        .ok_or("new ChatGPT registration did not return its issued client ID")?;
    if existing
        .as_ref()
        .is_some_and(|c| c.client_id != issued_client_id)
    {
        return Err(
            "OAuth callback client ID did not match the selected ChatGPT registration".into(),
        );
    }
    let client = reqwest::Client::new();
    let exchange_body = encode_form_pairs(&[
        ("grant_type", "authorization_code"),
        ("client_id", issued_client_id),
        ("code", code),
        ("code_verifier", &verifier),
        ("redirect_uri", &callback_uri),
        ("resource", RESOURCE),
    ]);
    let token_response = client
        .post(TOKEN)
        .header(
            reqwest::header::CONTENT_TYPE,
            "application/x-www-form-urlencoded",
        )
        .body(exchange_body)
        .send()
        .await?;
    if !token_response.status().is_success() {
        let status = token_response.status();
        let body = token_response.text().await.unwrap_or_default();
        return Err(format!(
            "ChatGPT token exchange failed (HTTP {status}): {}",
            safe_oauth_error(&body)
        )
        .into());
    }
    let token: OAuthTokenResponse = token_response.json().await?;
    let scopes = token
        .scope
        .split_whitespace()
        .map(str::to_owned)
        .collect::<Vec<_>>();
    if !scopes
        .iter()
        .any(|scope| scope == "chatgpt.tokens.use.direct")
    {
        return Err(
            "ChatGPT plan usage permission was not granted; reconnect and allow plan usage".into(),
        );
    }
    let header = jsonwebtoken::decode_header(&token.id_token)?;
    let jwks: jsonwebtoken::jwk::JwkSet = client
        .get("https://auth.openai.com/.well-known/jwks.json")
        .send()
        .await?
        .error_for_status()?
        .json()
        .await?;
    let jwk = jwks
        .find(&header.kid.ok_or("ID token has no signing key ID")?)
        .ok_or("OpenAI signing key was not found")?;
    let key = DecodingKey::from_jwk(jwk)?;
    let mut validation = Validation::new(header.alg);
    validation.set_issuer(&["https://auth.openai.com"]);
    validation.set_audience(&[issued_client_id]);
    let claims = decode::<IdTokenClaims>(&token.id_token, &key, &validation)?.claims;
    if claims.iss != "https://auth.openai.com" || claims.nonce != nonce {
        return Err("ChatGPT ID token validation failed".into());
    }
    if existing
        .as_ref()
        .is_some_and(|old| old.subject != claims.sub)
    {
        return Err("The authorized ChatGPT account does not match the selected account".into());
    }
    let now = SystemTime::now().duration_since(UNIX_EPOCH)?.as_secs();
    let credentials = arabica_provider::ChatGptCredentials {
        email: claims.email,
        subject: claims.sub,
        client_id: issued_client_id.to_owned(),
        ext_agent_host_id: host_id,
        id_token: token.id_token,
        access_token: token.access_token,
        refresh_token: token.refresh_token,
        scopes,
        expires_at_unix: now.saturating_add(token.expires_in),
    };
    let _credential_lock =
        arabica_provider::lock_chatgpt_credentials(&chatgpt_credentials_path(home)).await?;
    save_chatgpt_credentials(home, &credentials)?;
    Ok(credentials.email)
}

async fn chatgpt_logout(home: &Path) -> Result<(), Box<dyn std::error::Error>> {
    let _credential_lock =
        arabica_provider::lock_chatgpt_credentials(&chatgpt_credentials_path(home)).await?;
    let Some(mut credentials) = read_chatgpt_credentials(home)? else {
        println!("No saved ChatGPT plan connection");
        return Ok(());
    };
    if credentials.refresh_token.is_empty() {
        println!("ChatGPT plan is already signed out");
        return Ok(());
    }
    #[derive(Deserialize)]
    struct Discovery {
        revocation_endpoint: String,
    }
    let client = reqwest::Client::new();
    let discovery: Discovery = client
        .get("https://auth.openai.com/.well-known/openid-configuration")
        .send()
        .await?
        .error_for_status()?
        .json()
        .await?;
    let revoke_body = encode_form_pairs(&[
        ("token", &credentials.refresh_token),
        ("token_type_hint", "refresh_token"),
        ("client_id", &credentials.client_id),
    ]);
    let response = client
        .post(discovery.revocation_endpoint)
        .header(
            reqwest::header::CONTENT_TYPE,
            "application/x-www-form-urlencoded",
        )
        .body(revoke_body)
        .send()
        .await?;
    if response.status() != reqwest::StatusCode::OK {
        return Err(format!(
            "ChatGPT session revocation was not confirmed (HTTP {})",
            response.status()
        )
        .into());
    }
    credentials.access_token.clear();
    credentials.refresh_token.clear();
    credentials.id_token.clear();
    credentials.expires_at_unix = 0;
    save_chatgpt_credentials(home, &credentials)?;
    println!("ChatGPT session revoked; local tokens cleared");
    Ok(())
}

fn random_url_token() -> String {
    let mut bytes = [0u8; 32];
    rand::rng().fill_bytes(&mut bytes);
    base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(bytes)
}

fn encode_form_pairs(pairs: &[(&str, &str)]) -> String {
    let mut serializer = url::form_urlencoded::Serializer::new(String::new());
    for (key, value) in pairs {
        serializer.append_pair(key, value);
    }
    serializer.finish()
}

fn open_url(url: &str) -> bool {
    #[cfg(target_os = "macos")]
    let result = Command::new("open").arg(url).spawn();
    #[cfg(target_os = "linux")]
    let result = Command::new("xdg-open").arg(url).spawn();
    #[cfg(target_os = "windows")]
    let result = Command::new("rundll32")
        .arg("url.dll,FileProtocolHandler")
        .arg(url)
        .spawn();
    result.is_ok()
}

fn safe_oauth_error(body: &str) -> &str {
    // Do not print token endpoint bodies: they can contain sensitive details.
    if body.contains("invalid_grant") {
        "authorization code expired or was already used"
    } else if body.contains("invalid_client") {
        "OAuth client registration was rejected"
    } else {
        "the authorization server rejected the request"
    }
}

fn save_chatgpt_credentials(
    home: &Path,
    credentials: &arabica_provider::ChatGptCredentials,
) -> Result<(), Box<dyn std::error::Error>> {
    use std::io::Write;
    #[cfg(unix)]
    {
        use std::os::unix::fs::DirBuilderExt;
        let mut builder = std::fs::DirBuilder::new();
        builder.recursive(true).mode(0o700).create(home)?;
    }
    #[cfg(not(unix))]
    std::fs::create_dir_all(home)?;
    let path = chatgpt_credentials_path(home);
    let temporary = home.join(format!(".chatgpt-auth-{}.tmp", uuid::Uuid::now_v7()));
    let result = (|| -> Result<(), Box<dyn std::error::Error>> {
        let mut options = OpenOptions::new();
        options.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        let mut file = options.open(&temporary)?;
        serde_json::to_writer(&mut file, credentials)?;
        file.write_all(b"\n")?;
        file.sync_all()?;
        fs::rename(&temporary, &path)?;
        Ok(())
    })();
    if result.is_err() {
        let _ = fs::remove_file(temporary);
    }
    result
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn saved_key_is_private_and_can_be_removed() {
        let home = std::env::temp_dir().join(format!("structure-auth-{}", uuid::Uuid::now_v7()));
        save_key(&home, "test-secret").unwrap();
        assert_eq!(
            read_saved_key(&home).unwrap().as_deref(),
            Some("test-secret")
        );
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            assert_eq!(
                fs::metadata(auth_path(&home)).unwrap().permissions().mode() & 0o777,
                0o600
            );
        }
        fs::remove_file(auth_path(&home)).unwrap();
        assert!(read_saved_key(&home).unwrap().is_none());
        fs::remove_dir_all(home).unwrap();
    }

    #[test]
    fn chatgpt_credentials_are_stored_with_private_permissions() {
        let home =
            std::env::temp_dir().join(format!("arabica-chatgpt-auth-{}", uuid::Uuid::now_v7()));
        let credentials = arabica_provider::ChatGptCredentials {
            email: Some("user@example.test".to_owned()),
            subject: "subject-1".to_owned(),
            client_id: "oaiapp_test".to_owned(),
            ext_agent_host_id: "urn:uuid:test".to_owned(),
            id_token: "id-token".to_owned(),
            access_token: "access-token".to_owned(),
            refresh_token: "refresh-token".to_owned(),
            scopes: vec!["chatgpt.tokens.use.direct".to_owned()],
            expires_at_unix: 10,
        };
        save_chatgpt_credentials(&home, &credentials).unwrap();
        assert_eq!(
            read_chatgpt_credentials(&home).unwrap().unwrap().client_id,
            "oaiapp_test"
        );
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            assert_eq!(
                fs::metadata(chatgpt_credentials_path(&home))
                    .unwrap()
                    .permissions()
                    .mode()
                    & 0o777,
                0o600
            );
        }
        fs::remove_dir_all(home).unwrap();
    }

    #[test]
    fn oauth_state_and_verifier_tokens_are_url_safe_and_long_enough() {
        let state = random_url_token();
        let verifier = random_url_token() + &random_url_token();
        assert_eq!(state.len(), 43);
        assert!((43..=128).contains(&verifier.len()));
        assert!(
            state
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || byte == b'-' || byte == b'_')
        );
    }

    #[test]
    fn chatgpt_login_setup_creates_a_default_responses_config() {
        let home =
            std::env::temp_dir().join(format!("arabica-chatgpt-config-{}", uuid::Uuid::now_v7()));
        configure_chatgpt_default(&home).unwrap();
        let config = fs::read_to_string(crate::config::user_config_path(&home)).unwrap();
        assert!(config.contains("auth = \"chatgpt\""));
        assert!(config.contains("api_type = \"open_ai_responses\""));
        assert!(config.contains("model_id = \"gpt-6.1-sol\""));
        assert!(config.contains("default_model = \"default\""));
        assert!(config.contains("tool_call_capable_models = [\"default\"]"));
        assert!(config.contains("typed_completion_capable_models = [\"default\"]"));
        fs::remove_dir_all(home).unwrap();
    }

    #[test]
    fn chatgpt_login_setup_preserves_other_settings_and_existing_gpt_model() {
        let home =
            std::env::temp_dir().join(format!("arabica-chatgpt-config-{}", uuid::Uuid::now_v7()));
        fs::create_dir_all(&home).unwrap();
        let path = crate::config::user_config_path(&home);
        fs::write(
            &path,
            "# Keep this note\n[providers.anthropic]\nbase_url = 'https://example.test'\n\n[models.default]\nprovider = 'anthropic'\nmodel_id = 'gpt-6-astra'\n\n[blend]\ndefault_model = 'default'\n",
        )
        .unwrap();
        configure_chatgpt_default(&home).unwrap();
        let config = fs::read_to_string(path).unwrap();
        assert!(config.contains("# Keep this note"));
        assert!(config.contains("base_url = 'https://example.test'"));
        assert!(config.contains("model_id = 'gpt-6-astra'"));
        assert!(config.contains("provider = \"chatgpt\""));
        fs::remove_dir_all(home).unwrap();
    }

    #[test]
    fn chatgpt_login_setup_updates_named_policy_capabilities_idempotently() {
        let home =
            std::env::temp_dir().join(format!("arabica-chatgpt-config-{}", uuid::Uuid::now_v7()));
        fs::create_dir_all(&home).unwrap();
        let path = crate::config::user_config_path(&home);
        fs::write(&path, "[blend]\ndefault_policy = 'daily'\n[blend.policies.daily]\ndefault_model = 'fast'\ntool_call_capable_models = ['other']\n[blend.policies.focused]\ndefault_model = 'fast'\n").unwrap();
        configure_chatgpt_default(&home).unwrap();
        let first = fs::read_to_string(&path).unwrap();
        configure_chatgpt_default(&home).unwrap();
        assert_eq!(first, fs::read_to_string(&path).unwrap());
        let document = first.parse::<DocumentMut>().unwrap();
        assert!(document["blend"].get("default_model").is_none());
        for name in ["daily", "focused"] {
            for key in [
                "tool_call_capable_models",
                "typed_completion_capable_models",
            ] {
                assert!(
                    document["blend"]["policies"][name][key]
                        .as_array()
                        .unwrap()
                        .iter()
                        .any(|value| value.as_str() == Some("fast"))
                );
            }
        }
        assert_eq!(
            document["blend"]["policies"]["daily"]["tool_call_capable_models"][0].as_str(),
            Some("other")
        );
        fs::remove_dir_all(home).unwrap();
    }
}
