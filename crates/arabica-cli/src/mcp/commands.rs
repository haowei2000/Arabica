//! `arabica mcp`: manage OAuth grants for remote MCP servers.

use agent_client_protocol::schema::v1::McpServer;
use clap::{Args, Subcommand};

use super::auth::{DEFAULT_BINDING, McpAuth};
use super::oauth::{LoginRequest, OAuthTarget};

#[derive(Subcommand, Debug)]
pub enum McpAction {
    /// Authorize an OAuth MCP server and store its grant under $ARABICA_HOME.
    Login(TargetArgs),
    /// Delete the stored OAuth grant for a server.
    Logout(TargetArgs),
}

#[derive(Args, Debug)]
pub struct TargetArgs {
    /// A config.toml server whose `[mcp.auth]` table sets mode = "oauth".
    #[arg(required_unless_present = "url", conflicts_with = "url")]
    name: Option<String>,
    /// The URL of a server an ACP client supplies with OAuth authentication.
    #[arg(long)]
    url: Option<String>,
    /// The binding the client declares for that server.
    #[arg(long, requires = "url")]
    binding: Option<String>,
    /// A client ID registered with the authorization server in advance.
    #[arg(long, requires = "url")]
    client_id: Option<String>,
    /// A scope to request; repeat for several.
    #[arg(long = "scope", requires = "url")]
    scopes: Vec<String>,
}

fn resolve(args: TargetArgs, arabica_home: &std::path::Path) -> Result<LoginRequest, String> {
    if let Some(url) = args.url {
        let binding = args.binding.unwrap_or_else(|| DEFAULT_BINDING.to_owned());
        let auth = McpAuth::Oauth {
            binding: Some(binding.clone()),
            client_id: args.client_id.clone(),
            scopes: args.scopes.clone(),
        };
        auth.validate(&url)?;
        return Ok(LoginRequest {
            target: OAuthTarget {
                server: url.clone(),
                url,
                binding,
                configured: false,
            },
            client_id: args.client_id,
            scopes: args.scopes,
        });
    }
    let name = args.name.expect("clap requires a name or --url");
    let servers =
        crate::config::user_config_mcp(arabica_home).map_err(|error| error.to_string())?;
    let config = servers
        .into_iter()
        .find_map(|server| match server {
            McpServer::Http(config) if config.name == name => Some(config),
            _ => None,
        })
        .ok_or_else(|| format!("config.toml declares no HTTP MCP server named '{name}'"))?;
    match McpAuth::from_meta(&config.name, config.meta.as_ref())? {
        Some(
            ref auth @ McpAuth::Oauth {
                ref client_id,
                ref scopes,
                ..
            },
        ) => Ok(LoginRequest {
            target: OAuthTarget {
                server: config.name.clone(),
                url: config.url.clone(),
                binding: auth.binding().to_owned(),
                configured: true,
            },
            client_id: client_id.clone(),
            scopes: scopes.clone(),
        }),
        _ => Err(format!(
            "MCP server '{name}' does not use OAuth; add an [mcp.auth] table with mode = \"oauth\""
        )),
    }
}

pub async fn run(action: McpAction) -> i32 {
    let result: Result<(), String> = async {
        let home = arabica_adapters::default_arabica_home().map_err(|error| error.to_string())?;
        match action {
            McpAction::Login(args) => {
                let request = resolve(args, &home)?;
                let server = request.target.server.clone();
                super::oauth::login(request, &home, |url| {
                    eprintln!("Open this URL to authorize MCP server '{server}':\n\n  {url}\n");
                    open_browser(url);
                })
                .await?;
                eprintln!("Authorized MCP server '{server}'.");
                Ok(())
            }
            McpAction::Logout(args) => {
                let request = resolve(args, &home)?;
                if super::oauth::logout(&request.target, &home)? {
                    eprintln!(
                        "Removed the OAuth grant for MCP server '{}'.",
                        request.target.server
                    );
                } else {
                    eprintln!(
                        "MCP server '{}' has no stored OAuth grant.",
                        request.target.server
                    );
                }
                Ok(())
            }
        }
    }
    .await;
    match result {
        Ok(()) => 0,
        Err(error) => {
            eprintln!("error: {error}");
            1
        }
    }
}

/// Best effort: the URL is printed either way.
fn open_browser(url: &str) {
    let program = if cfg!(target_os = "macos") {
        "open"
    } else if cfg!(target_os = "windows") {
        return;
    } else {
        "xdg-open"
    };
    let _ = std::process::Command::new(program)
        .arg(url)
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .spawn();
}
