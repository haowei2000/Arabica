//! User-owned CLI settings and optional user-file credentials.

use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};

use std::collections::{BTreeMap, BTreeSet};

use agent_client_protocol::schema::v1::{
    EnvVariable, HttpHeader, McpServer, McpServerHttp, McpServerStdio,
};
use arabica_provider::{ApiProviderConfig, ApiType};
use arabica_runtime::{BlendRoutingPolicy, BlendToolRoute};
use serde::{Deserialize, Serialize};

use crate::host::{HostConfigArgs, workspace_id_for};

#[derive(Debug, Default, Deserialize)]
#[serde(deny_unknown_fields)]
struct UserConfig {
    #[serde(default)]
    providers: BTreeMap<String, UserProvider>,
    #[serde(default)]
    mcp: Vec<UserMcpServer>,
    #[serde(default)]
    models: BTreeMap<String, UserModel>,
    #[serde(default)]
    blend: Option<UserBlend>,
    #[serde(default)]
    context: Option<arabica_runtime::ContextPolicy>,
    #[serde(default)]
    skills: UserSkills,
    #[serde(default)]
    evaluation: Option<toml::Value>,
}

#[derive(Debug, Default, Deserialize)]
#[serde(deny_unknown_fields)]
struct UserSkills {
    #[serde(default)]
    roots: Vec<PathBuf>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct UserModel {
    provider: String,
    model_id: String,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct UserBlend {
    policy_id: Option<String>,
    version: Option<u64>,
    default_model: Option<String>,
    after_tool_success: Option<String>,
    after_tool_error: Option<String>,
    recovery_model: Option<String>,
    #[serde(default)]
    planning_model: Option<String>,
    #[serde(default)]
    tool_routes: Vec<BlendToolRoute>,
    recovery_after_no_progress_steps: Option<usize>,
    minimum_model_dwell_steps: Option<usize>,
    #[serde(default)]
    tool_call_capable_models: BTreeSet<String>,
    #[serde(default)]
    typed_completion_capable_models: BTreeSet<String>,
    #[serde(default)]
    default_policy: Option<String>,
    #[serde(default)]
    policies: BTreeMap<String, UserBlendPolicy>,
}

impl UserBlend {
    fn configured_default_policy_id(&self) -> Option<&str> {
        self.default_policy
            .as_deref()
            .or(self.policy_id.as_deref())
            .or_else(|| self.policies.keys().next().map(String::as_str))
            .or_else(|| self.default_model.as_ref().map(|_| "cli-config"))
    }

    fn configured_default_model(&self) -> Option<&str> {
        if let Some(default_model) = self.default_model.as_deref() {
            return Some(default_model);
        }
        self.configured_default_policy_id()
            .and_then(|id| self.policies.get(id))
            .map(|policy| policy.default_model.as_str())
    }
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct UserBlendPolicy {
    #[serde(default)]
    version: Option<u64>,
    default_model: String,
    #[serde(default)]
    after_tool_success: Option<String>,
    #[serde(default)]
    after_tool_error: Option<String>,
    #[serde(default)]
    recovery_model: Option<String>,
    #[serde(default)]
    planning_model: Option<String>,
    #[serde(default)]
    tool_routes: Vec<BlendToolRoute>,
    #[serde(default = "default_recovery_threshold")]
    recovery_after_no_progress_steps: usize,
    #[serde(default = "default_model_dwell_steps")]
    minimum_model_dwell_steps: usize,
    #[serde(default)]
    tool_call_capable_models: BTreeSet<String>,
    #[serde(default)]
    typed_completion_capable_models: BTreeSet<String>,
}

fn default_recovery_threshold() -> usize {
    2
}

fn default_model_dwell_steps() -> usize {
    1
}

#[derive(Debug)]
pub struct ResolvedCliConfig {
    /// Configuration for the default model alias, retained for host settings.
    pub provider: ApiProviderConfig,
    pub blend_policy: Option<BlendRoutingPolicy>,
    /// Named routing policies offered to ACP clients.
    pub blend_policies: BTreeMap<String, BlendRoutingPolicy>,
    pub models: BTreeMap<String, String>,
    pub model_providers: BTreeMap<String, String>,
    /// Resolved provider configuration for each model alias.
    pub model_configs: BTreeMap<String, ApiProviderConfig>,
}

impl ResolvedCliConfig {
    pub fn model_catalog(&self) -> crate::host::HostModelCatalog {
        crate::host::HostModelCatalog {
            provider: self.provider.clone(),
            models: self.models.clone(),
            blend_policy: self.blend_policy.clone(),
            model_configs: self.model_configs.clone(),
        }
    }

    pub fn build_model(&self) -> Result<crate::host::HostModel, Box<dyn std::error::Error>> {
        Ok(self.model_catalog().build_model()?)
    }
}

/// One MCP server declared in the user config file as an `[[mcp]]` table.
/// Exactly one of `command` (stdio transport) or `url` (HTTP transport)
/// selects the transport. `env` and `headers` may carry credentials, so a
/// config containing them must be owner-only, like an API key.
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct UserMcpServer {
    name: String,
    command: Option<String>,
    #[serde(default)]
    args: Vec<String>,
    env: Option<BTreeMap<String, String>>,
    url: Option<String>,
    headers: Option<BTreeMap<String, String>>,
}

impl UserMcpServer {
    fn has_credentials(&self) -> bool {
        self.env.as_ref().is_some_and(|map| !map.is_empty())
            || self.headers.as_ref().is_some_and(|map| !map.is_empty())
    }

    /// Convert to the shared `McpServer` type every surface connects
    /// through. Errors name the server, never a credential value.
    fn to_mcp_server(&self) -> Result<McpServer, String> {
        match (&self.command, &self.url) {
            (Some(command), None) => {
                let env = self
                    .env
                    .clone()
                    .unwrap_or_default()
                    .into_iter()
                    .map(|(name, value)| EnvVariable::new(name, value))
                    .collect::<Vec<_>>();
                Ok(McpServer::Stdio(
                    McpServerStdio::new(self.name.clone(), command.clone())
                        .args(self.args.clone())
                        .env(env),
                ))
            }
            (None, Some(url)) => {
                let headers = self
                    .headers
                    .clone()
                    .unwrap_or_default()
                    .into_iter()
                    .map(|(name, value)| HttpHeader::new(name, value))
                    .collect::<Vec<_>>();
                Ok(McpServer::Http(
                    McpServerHttp::new(self.name.clone(), url.clone()).headers(headers),
                ))
            }
            (Some(_), Some(_)) => Err(format!(
                "MCP server '{}' declares both command and url; pick one transport",
                self.name
            )),
            (None, None) => Err(format!(
                "MCP server '{}' declares neither command nor url",
                self.name
            )),
        }
    }
}

#[derive(Debug, Default, Deserialize)]
#[serde(deny_unknown_fields)]
struct UserProvider {
    api_type: Option<String>,
    api_key: Option<String>,
    api_key_env: Option<String>,
    base_url: Option<String>,
    thinking: Option<String>,
    reasoning_effort: Option<String>,
    max_tokens: Option<u32>,
    request_timeout_secs: Option<u64>,
    anthropic_cache_static_prefix: Option<bool>,
}

#[derive(Debug, Default, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct WorkspaceSettings {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub model: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub model_alias: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub thinking: Option<String>,
}

pub fn user_config_path(home: &Path) -> PathBuf {
    home.join("config.toml")
}

pub fn workspace_config_path(home: &Path, cwd: &Path) -> PathBuf {
    home.join("workspaces")
        .join(workspace_id_for(cwd).to_string())
        .join("config.toml")
}

fn read_toml<T: for<'de> Deserialize<'de> + Default>(
    path: &Path,
) -> Result<T, Box<dyn std::error::Error>> {
    match fs::read_to_string(path) {
        // TOML parser errors can include source excerpts containing credentials.
        // Report only the source location so startup diagnostics stay useful
        // without exposing configuration values.
        Ok(text) => Ok(toml::from_str(&text).map_err(|error: toml::de::Error| {
            let location = error
                .span()
                .map(|span| {
                    let prefix = &text[..span.start.min(text.len())];
                    let line = prefix.bytes().filter(|byte| *byte == b'\n').count() + 1;
                    let column = prefix
                        .rsplit('\n')
                        .next()
                        .map_or(1, |row| row.chars().count() + 1);
                    format!(" at line {line}, column {column}")
                })
                .unwrap_or_default();
            format!("invalid TOML in {}{location}", path.display())
        })?),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(T::default()),
        Err(error) => Err(format!("cannot read {}: {error}", path.display()).into()),
    }
}

fn read_user_config(home: &Path) -> Result<UserConfig, Box<dyn std::error::Error>> {
    let path = user_config_path(home);
    let config: UserConfig = read_toml(&path)?;
    for (name, provider) in &config.providers {
        if name.trim().is_empty() {
            return Err(format!("{} contains an empty provider name", path.display()).into());
        }
        if provider
            .api_key
            .as_ref()
            .is_some_and(|key| key.trim().is_empty())
        {
            return Err(format!(
                "{} contains an empty API key for provider {name:?}",
                path.display()
            )
            .into());
        }
        if provider
            .api_key_env
            .as_ref()
            .is_some_and(|key| key.trim().is_empty())
        {
            return Err(format!(
                "{} contains an empty api_key_env for provider {name:?}",
                path.display()
            )
            .into());
        }
    }
    // MCP `env` and `headers` can carry secrets just like an API key, so
    // they earn the same file-safety requirements.
    let has_credentials = config
        .providers
        .values()
        .any(|provider| provider.api_key.is_some())
        || config.mcp.iter().any(UserMcpServer::has_credentials);
    if has_credentials {
        let metadata = fs::symlink_metadata(&path)?;
        if !metadata.file_type().is_file() {
            return Err(format!(
                "{} must be a regular file when it contains credentials",
                path.display()
            )
            .into());
        }
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            if metadata.permissions().mode() & 0o077 != 0 {
                return Err(format!(
                    "{} contains credentials and must be readable only by its owner (chmod 600)",
                    path.display()
                )
                .into());
            }
        }
    }
    Ok(config)
}

pub fn user_config_key(home: &Path) -> Result<Option<String>, Box<dyn std::error::Error>> {
    Ok(read_user_config(home)?
        .providers
        .values()
        .find_map(|provider| provider.api_key.clone()))
}

pub fn user_config_api_key_env(home: &Path) -> Result<Option<String>, Box<dyn std::error::Error>> {
    let config = read_user_config(home)?;
    let Some(alias) = config
        .blend
        .as_ref()
        .and_then(UserBlend::configured_default_model)
    else {
        return Ok(None);
    };
    let Some(model) = config.models.get(alias) else {
        return Ok(None);
    };
    let Some(provider) = config.providers.get(&model.provider) else {
        return Ok(None);
    };
    Ok(Some(
        provider
            .api_key_env
            .clone()
            .unwrap_or_else(|| provider_api_key_env(&model.provider)),
    ))
}

/// The MCP servers declared in the user config file, as the shared
/// `McpServer` type every surface connects through. A server that declares
/// no transport, or both, is a configuration error.
pub fn user_config_mcp(home: &Path) -> Result<Vec<McpServer>, Box<dyn std::error::Error>> {
    let config = read_user_config(home)?;
    config
        .mcp
        .iter()
        .map(|server| server.to_mcp_server().map_err(Into::into))
        .collect()
}

/// Independent context governance; live tool references resolve after MCP discovery.
pub fn user_config_context(
    home: &Path,
) -> Result<Option<arabica_runtime::ContextPolicy>, Box<dyn std::error::Error>> {
    let policy = read_user_config(home)?.context;
    if let Some(policy) = &policy {
        policy.validate()?;
    }
    Ok(policy)
}

pub fn user_config_evaluation(
    home: &Path,
) -> Result<arabica_runtime::EvaluationConfig, Box<dyn std::error::Error>> {
    let config: arabica_runtime::EvaluationConfig = read_user_config(home)?
        .evaluation
        .map(toml::Value::try_into)
        .transpose()?
        .unwrap_or_default();
    let registry = arabica_runtime::EvaluationRegistry::builtins(&config)?;
    let evidence = arabica_runtime::EvaluationEvidence::from_history(&[]);
    registry.context(&config.context_strategy, &evidence)?;
    registry.model(&config.model_strategy, &evidence)?;
    Ok(config)
}

pub fn user_config_blend_policies(
    home: &Path,
    cwd: &Path,
) -> Result<BTreeMap<String, BlendRoutingPolicy>, Box<dyn std::error::Error>> {
    let args = crate::host::HostConfigArgs {
        model: None,
        base_url: None,
        api_type: None,
    };
    let resolved = resolve_cli_runtime_config(&args, home, cwd, |_| None)?;
    let mut policies = resolved.blend_policies;
    if let Some(default_policy) = resolved.blend_policy {
        policies
            .entry(default_policy.policy_id.clone())
            .or_insert(default_policy);
    }
    Ok(policies)
}

/// Relative skill roots resolve against ARABICA_HOME, never the model's input.
pub fn user_config_skill_roots(home: &Path) -> Result<Vec<PathBuf>, Box<dyn std::error::Error>> {
    Ok(read_user_config(home)?
        .skills
        .roots
        .into_iter()
        .map(|root| {
            if root.is_absolute() {
                root
            } else {
                home.join(root)
            }
        })
        .collect())
}

/// Resolve all named providers and Blend aliases from the user configuration.
pub fn resolve_cli_config(
    args: &HostConfigArgs,
    home: &Path,
    cwd: &Path,
    lookup: impl Fn(&str) -> Option<String>,
) -> Result<ApiProviderConfig, Box<dyn std::error::Error>> {
    Ok(resolve_cli_runtime_config(args, home, cwd, lookup)?.provider)
}

pub fn resolve_cli_runtime_config(
    args: &HostConfigArgs,
    home: &Path,
    cwd: &Path,
    lookup: impl Fn(&str) -> Option<String>,
) -> Result<ResolvedCliConfig, Box<dyn std::error::Error>> {
    let user = read_user_config(home)?;
    let workspace: WorkspaceSettings = read_toml(&workspace_config_path(home, cwd))?;
    if user.models.is_empty() && user.blend.is_some() {
        return Err("[blend] requires at least one [models.<alias>] entry".into());
    }
    if !user.models.is_empty() && user.blend.is_none() {
        return Err(
            "[models] requires a [blend] table with a default_model or named policy".into(),
        );
    }
    if user.models.is_empty() {
        return Err(
            "configure [providers.<name>], [models.<alias>], and [blend] in ~/.arabica/config.toml"
                .into(),
        );
    }
    let models = user
        .models
        .iter()
        .map(|(alias, model)| {
            if alias.trim().is_empty()
                || model.model_id.trim().is_empty()
                || model.provider.trim().is_empty()
            {
                return Err(format!(
                    "model alias, provider, and model_id must not be empty ({alias:?})"
                ));
            }
            Ok((alias.clone(), model.model_id.clone()))
        })
        .collect::<Result<BTreeMap<_, _>, String>>()?;
    let configured_default_alias = user
        .blend
        .as_ref()
        .and_then(UserBlend::configured_default_model)
        .map(str::to_owned)
        .ok_or("[blend].default_model or a named [blend.policies.<name>] policy is required")?;
    if !user.models.contains_key(&configured_default_alias) {
        return Err(format!(
            "[blend].default_model alias {configured_default_alias:?} is not configured"
        )
        .into());
    }
    let default_alias = workspace
        .model_alias
        .clone()
        .unwrap_or(configured_default_alias);
    if !user.models.contains_key(&default_alias) {
        return Err(format!("workspace model alias {default_alias:?} is not configured").into());
    }
    let default_model = user
        .models
        .get(&default_alias)
        .ok_or("[blend].default_model must reference a configured model alias")?;
    let default_provider_name = default_model.provider.clone();
    let mut model_configs = BTreeMap::new();
    for (alias, model) in &user.models {
        let user_provider = user.providers.get(&model.provider).ok_or_else(|| {
            format!(
                "model alias {alias:?} references unknown provider {:?}",
                model.provider
            )
        })?;
        let same_as_default_provider = model.provider == default_provider_name;
        let api_type = if same_as_default_provider {
            args.api_type
                .clone()
                .or_else(|| lookup("ARABICA__API_TYPE"))
                .or_else(|| user_provider.api_type.clone())
        } else {
            user_provider.api_type.clone()
        }
        .unwrap_or_else(|| ApiType::OpenAiChatCompletions.to_string())
        .parse::<ApiType>()?;
        let base_url = if same_as_default_provider {
            args.base_url
                .clone()
                .or_else(|| lookup("ARABICA__BASE_URL"))
                .or_else(|| user_provider.base_url.clone())
        } else {
            user_provider.base_url.clone()
        }
        .ok_or_else(|| format!("base_url is required for provider {:?}", model.provider))?;
        let env_name = user_provider
            .api_key_env
            .clone()
            .unwrap_or_else(|| provider_api_key_env(&model.provider));
        let environment_key = lookup(&env_name).filter(|key| !key.trim().is_empty());
        let saved_key = if same_as_default_provider
            && environment_key.is_none()
            && user_provider.api_key.is_none()
        {
            crate::auth::read_saved_key(home)?
        } else {
            None
        };
        let api_key = environment_key
            .or_else(|| user_provider.api_key.clone())
            .or(saved_key)
            .ok_or_else(|| {
                format!(
                    "API key is required for provider {:?}; set {env_name} or api_key",
                    model.provider
                )
            })?;
        if user_provider.max_tokens == Some(0) {
            return Err(format!(
                "max_tokens must be positive for provider {:?}",
                model.provider
            )
            .into());
        }
        if user_provider.request_timeout_secs == Some(0) {
            return Err(format!(
                "request_timeout_secs must be positive for provider {:?}",
                model.provider
            )
            .into());
        }
        if user_provider.anthropic_cache_static_prefix == Some(true)
            && api_type != ApiType::AnthropicMessages
        {
            return Err(format!(
                "anthropic_cache_static_prefix is only supported for Anthropic provider {:?}",
                model.provider
            )
            .into());
        }
        if user_provider.reasoning_effort.is_some() && user_provider.thinking.is_some() {
            return Err(format!(
                "configure only one of thinking or reasoning_effort for provider {:?}",
                model.provider
            )
            .into());
        }
        let mut config =
            ApiProviderConfig::new(api_type, api_key, base_url, model.model_id.clone());
        config.max_tokens = user_provider.max_tokens;
        config.request_timeout_secs = user_provider.request_timeout_secs.unwrap_or(300);
        config.anthropic_cache_static_prefix =
            user_provider.anthropic_cache_static_prefix.unwrap_or(false);
        config.thinking_enabled = false;
        if let Some(effort) = &user_provider.reasoning_effort {
            set_thinking(&mut config, effort)?;
        }
        if let Some(thinking) = user_provider.thinking.as_deref() {
            set_thinking(&mut config, thinking)?;
        }
        if alias == &default_alias {
            if let Some(thinking) = workspace.thinking.as_deref() {
                set_thinking(&mut config, thinking)?;
            }
            if let Some(model_id) = args
                .model
                .clone()
                .or(workspace.model.clone())
                .or_else(|| lookup("ARABICA__MODEL"))
            {
                config.model = model_id;
            }
        }
        model_configs.insert(alias.clone(), config);
    }
    let (blend_policy, blend_policies) = match user.blend.as_ref() {
        None => (None, BTreeMap::new()),
        Some(blend) => {
            let named_policies = !blend.policies.is_empty();
            let mut policies = BTreeMap::new();
            if named_policies {
                if blend.policy_id.is_some()
                    || blend.version.is_some()
                    || blend.default_model.is_some()
                    || blend.after_tool_success.is_some()
                    || blend.after_tool_error.is_some()
                    || blend.recovery_model.is_some()
                    || blend.planning_model.is_some()
                    || !blend.tool_routes.is_empty()
                    || blend.recovery_after_no_progress_steps.is_some()
                    || blend.minimum_model_dwell_steps.is_some()
                    || !blend.tool_call_capable_models.is_empty()
                    || !blend.typed_completion_capable_models.is_empty()
                {
                    return Err(
                        "configure either the single [blend] policy or [blend.policies.<name>] entries, not both".into(),
                    );
                }
                for (id, configured) in &blend.policies {
                    let policy = BlendRoutingPolicy {
                        policy_id: id.clone(),
                        version: configured.version.unwrap_or(1),
                        default_model: configured.default_model.clone(),
                        after_tool_success: configured.after_tool_success.clone(),
                        after_tool_error: configured.after_tool_error.clone(),
                        recovery_model: configured.recovery_model.clone(),
                        planning_model: configured.planning_model.clone(),
                        tool_routes: configured.tool_routes.clone(),
                        recovery_after_no_progress_steps: configured
                            .recovery_after_no_progress_steps,
                        minimum_model_dwell_steps: configured.minimum_model_dwell_steps,
                        tool_call_capable_models: configured.tool_call_capable_models.clone(),
                        typed_completion_capable_models: configured
                            .typed_completion_capable_models
                            .clone(),
                    };
                    validate_blend_policy(&policy, &models)?;
                    policies.insert(id.clone(), policy);
                }
            } else {
                let id = blend
                    .policy_id
                    .clone()
                    .or_else(|| blend.default_policy.clone())
                    .unwrap_or_else(|| "cli-config".to_owned());
                let policy = BlendRoutingPolicy {
                    policy_id: id.clone(),
                    version: blend.version.unwrap_or(1),
                    default_model: blend
                        .default_model
                        .clone()
                        .ok_or("[blend].default_model is required")?,
                    after_tool_success: blend.after_tool_success.clone(),
                    after_tool_error: blend.after_tool_error.clone(),
                    recovery_model: blend.recovery_model.clone(),
                    planning_model: blend.planning_model.clone(),
                    tool_routes: blend.tool_routes.clone(),
                    recovery_after_no_progress_steps: blend
                        .recovery_after_no_progress_steps
                        .unwrap_or_else(default_recovery_threshold),
                    minimum_model_dwell_steps: blend
                        .minimum_model_dwell_steps
                        .unwrap_or_else(default_model_dwell_steps),
                    tool_call_capable_models: blend.tool_call_capable_models.clone(),
                    typed_completion_capable_models: blend.typed_completion_capable_models.clone(),
                };
                validate_blend_policy(&policy, &models)?;
                policies.insert(id, policy);
            }

            let selected_id = blend
                .configured_default_policy_id()
                .ok_or("[blend].default_policy must name one of the configured policies")?;
            let mut selected = policies.get(selected_id).cloned().ok_or_else(|| {
                format!("[blend].default_policy {selected_id:?} is not configured")
            })?;
            if let Some(workspace_alias) = workspace.model_alias.as_deref() {
                selected.default_model = workspace_alias.to_owned();
                validate_blend_policy(&selected, &models)?;
                policies.insert(selected_id.to_owned(), selected.clone());
            }
            (Some(selected), policies)
        }
    };
    let provider = model_configs
        .get(&default_alias)
        .expect("validated default alias")
        .clone();
    Ok(ResolvedCliConfig {
        provider,
        blend_policy,
        blend_policies,
        models,
        model_providers: user
            .models
            .iter()
            .map(|(alias, model)| (alias.clone(), model.provider.clone()))
            .collect(),
        model_configs,
    })
}

fn validate_blend_policy(
    policy: &BlendRoutingPolicy,
    models: &BTreeMap<String, String>,
) -> Result<(), String> {
    let aliases = std::iter::once(policy.default_model.as_str())
        .chain(policy.after_tool_success.as_deref())
        .chain(policy.after_tool_error.as_deref())
        .chain(policy.recovery_model.as_deref())
        .chain(policy.planning_model.as_deref())
        .chain(policy.tool_routes.iter().map(|route| route.model.as_str()))
        .chain(policy.tool_call_capable_models.iter().map(String::as_str))
        .chain(
            policy
                .typed_completion_capable_models
                .iter()
                .map(String::as_str),
        );
    if policy.policy_id.trim().is_empty()
        || policy.version == 0
        || policy.recovery_after_no_progress_steps == 0
        || policy.minimum_model_dwell_steps == 0
        || aliases.into_iter().any(|alias| !models.contains_key(alias))
    {
        return Err(format!(
            "Blend policy {:?} has an invalid value or references an undefined model alias",
            policy.policy_id
        ));
    }
    let mut route_ids = BTreeSet::new();
    for route in &policy.tool_routes {
        if route.id.trim().is_empty() || !route_ids.insert(route.id.as_str()) {
            return Err(format!(
                "Blend policy {:?} has an empty or duplicate tool route id",
                policy.policy_id
            ));
        }
        if let arabica_runtime::BlendToolMatcher::RegexName(pattern) = &route.matcher {
            regex::Regex::new(pattern).map_err(|error| {
                format!(
                    "Blend policy {:?} has invalid tool route regex {:?}: {error}",
                    policy.policy_id, pattern
                )
            })?;
        }
    }
    Ok(())
}

fn provider_api_key_env(provider: &str) -> String {
    let suffix = provider
        .chars()
        .map(|character| {
            if character.is_ascii_alphanumeric() {
                character.to_ascii_uppercase()
            } else {
                '_'
            }
        })
        .collect::<String>();
    format!("ARABICA_PROVIDER_{suffix}_API_KEY")
}

pub fn set_thinking(config: &mut ApiProviderConfig, value: &str) -> Result<(), String> {
    match (config.api_type, value) {
        (_, "off") => {
            config.thinking_enabled = false;
            config.reasoning_effort = None;
        }
        (ApiType::OpenAiChatCompletions, "on") => {
            config.thinking_enabled = true;
            config.reasoning_effort = None;
        }
        (ApiType::OpenAiResponses, "low" | "medium" | "high") => {
            config.thinking_enabled = true;
            config.reasoning_effort = Some(value.to_owned());
        }
        _ => {
            return Err(format!(
                "unsupported thinking level {value:?} for {}",
                config.api_type
            ));
        }
    }
    Ok(())
}

pub fn save_workspace_settings(
    home: &Path,
    cwd: &Path,
    edit: impl FnOnce(&mut WorkspaceSettings),
) -> Result<(), Box<dyn std::error::Error>> {
    let path = workspace_config_path(home, cwd);
    let mut settings: WorkspaceSettings = read_toml(&path)?;
    edit(&mut settings);
    let body = toml::to_string_pretty(&settings)?;
    let parent = path.parent().expect("config path has parent");
    #[cfg(unix)]
    {
        use std::os::unix::fs::DirBuilderExt;
        let mut builder = fs::DirBuilder::new();
        builder.recursive(true).mode(0o700).create(parent)?;
    }
    #[cfg(not(unix))]
    fs::create_dir_all(parent)?;
    let temporary = parent.join(format!(".config-{}.tmp", uuid::Uuid::now_v7()));
    let result = (|| -> Result<(), Box<dyn std::error::Error>> {
        let mut options = OpenOptions::new();
        options.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        let mut file = options.open(&temporary)?;
        file.write_all(body.as_bytes())?;
        file.sync_all()?;
        fs::rename(&temporary, &path)?;
        Ok(())
    })();
    if result.is_err() {
        let _ = fs::remove_file(&temporary);
    }
    result
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn user_defaults_workspace_choice_and_flags_have_stable_precedence() {
        let root = std::env::temp_dir().join(format!("structure-config-{}", uuid::Uuid::now_v7()));
        let home = root.join("home");
        let cwd = root.join("project");
        fs::create_dir_all(&home).unwrap();
        fs::create_dir_all(&cwd).unwrap();
        fs::write(
            user_config_path(&home),
            "[providers.test]\nbase_url = 'https://example.test/v1'\nthinking = 'on'\n\n[models.default]\nprovider = 'test'\nmodel_id = 'user-model'\n\n[blend]\ndefault_model = 'default'\n",
        )
        .unwrap();
        let env = |name: &str| match name {
            "ARABICA_PROVIDER_TEST_API_KEY" => Some("secret".to_owned()),
            "ARABICA__MODEL" => Some("env-model".to_owned()),
            _ => None,
        };
        let defaults = resolve_cli_config(&HostConfigArgs::default(), &home, &cwd, env).unwrap();
        assert_eq!(defaults.model, "env-model");
        assert_eq!(defaults.base_url, "https://example.test/v1");
        assert!(defaults.thinking_enabled);

        save_workspace_settings(&home, &cwd, |settings| {
            settings.model = Some("workspace-model".to_owned());
            settings.thinking = Some("off".to_owned());
        })
        .unwrap();
        let workspace = resolve_cli_config(&HostConfigArgs::default(), &home, &cwd, env).unwrap();
        assert_eq!(workspace.model, "workspace-model");
        assert!(!workspace.thinking_enabled);
        let flags = HostConfigArgs {
            model: Some("flag-model".to_owned()),
            ..HostConfigArgs::default()
        };
        let selected = resolve_cli_config(&flags, &home, &cwd, env).unwrap();
        assert_eq!(selected.model, "flag-model");
        assert!(
            !fs::read_to_string(workspace_config_path(&home, &cwd))
                .unwrap()
                .contains("secret")
        );
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn model_aliases_and_blend_policy_resolve_from_user_toml() {
        let root =
            std::env::temp_dir().join(format!("structure-blend-config-{}", uuid::Uuid::now_v7()));
        let home = root.join("home");
        let cwd = root.join("project");
        fs::create_dir_all(&home).unwrap();
        fs::create_dir_all(&cwd).unwrap();
        fs::write(
            user_config_path(&home),
            r#"
[providers.test]
base_url = "https://example.test/v1"

[models.fast]
provider = "test"
model_id = "model-mini"

[models.strong]
provider = "test"
model_id = "model-pro"

[blend]
policy_id = "coding"
version = 3
default_model = "fast"
after_tool_error = "strong"
recovery_model = "strong"
recovery_after_no_progress_steps = 2
tool_call_capable_models = ["fast", "strong"]
typed_completion_capable_models = ["strong"]
"#,
        )
        .unwrap();
        let resolved =
            resolve_cli_runtime_config(&HostConfigArgs::default(), &home, &cwd, |name| {
                (name == "ARABICA_PROVIDER_TEST_API_KEY").then(|| "test-key".to_owned())
            })
            .unwrap();
        assert_eq!(resolved.provider.model, "model-mini");
        assert_eq!(resolved.models["strong"], "model-pro");
        assert_eq!(resolved.blend_policy.as_ref().unwrap().version, 3);
        assert_eq!(resolved.blend_policies.len(), 1);
        assert_eq!(
            resolved
                .blend_policy
                .as_ref()
                .unwrap()
                .typed_completion_capable_models,
            BTreeSet::from(["strong".to_owned()])
        );
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn named_blend_policies_resolve_and_select_the_configured_default() {
        let root =
            std::env::temp_dir().join(format!("structure-policy-config-{}", uuid::Uuid::now_v7()));
        let home = root.join("home");
        let cwd = root.join("project");
        fs::create_dir_all(&home).unwrap();
        fs::create_dir_all(&cwd).unwrap();
        fs::write(
            user_config_path(&home),
            r#"
[providers.test]
base_url = "https://example.test/v1"

[models.fast]
provider = "test"
model_id = "model-mini"

[models.strong]
provider = "test"
model_id = "model-pro"

[blend]
default_policy = "coding"

[blend.policies.coding]
default_model = "fast"
after_tool_error = "strong"

[blend.policies.precise]
default_model = "strong"
minimum_model_dwell_steps = 3
"#,
        )
        .unwrap();

        let resolved =
            resolve_cli_runtime_config(&HostConfigArgs::default(), &home, &cwd, |name| {
                (name == "ARABICA_PROVIDER_TEST_API_KEY").then(|| "test-key".to_owned())
            })
            .unwrap();

        assert_eq!(resolved.blend_policy.as_ref().unwrap().policy_id, "coding");
        assert_eq!(
            resolved.blend_policy.as_ref().unwrap().default_model,
            "fast"
        );
        assert_eq!(resolved.blend_policies.len(), 2);
        assert_eq!(resolved.blend_policies["precise"].default_model, "strong");
        assert_eq!(
            resolved.blend_policies["precise"].minimum_model_dwell_steps,
            3
        );
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn skill_roots_and_initial_modes_resolve_from_user_configuration() {
        let root =
            std::env::temp_dir().join(format!("structure-skill-config-{}", uuid::Uuid::now_v7()));
        fs::create_dir_all(&root).unwrap();
        fs::write(
            user_config_path(&root),
            r#"
[skills]
roots = ["skills"]
[context]
policy_id = "test"
version = 1
include = ["skill:review"]
unfolded = ["skill:review"]
"#,
        )
        .unwrap();
        assert_eq!(
            user_config_skill_roots(&root).unwrap(),
            vec![root.join("skills")]
        );
        let policy = user_config_context(&root).unwrap().unwrap();
        assert_eq!(policy.default_mode, arabica_protocol::ContextMode::Folded);
        assert!(policy.unfolded.contains("skill:review"));
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn aliases_resolve_distinct_provider_dialects_and_credentials() {
        let root =
            std::env::temp_dir().join(format!("structure-multi-provider-{}", uuid::Uuid::now_v7()));
        let home = root.join("home");
        let cwd = root.join("project");
        fs::create_dir_all(&home).unwrap();
        fs::create_dir_all(&cwd).unwrap();
        fs::write(
            user_config_path(&home),
            r#"
[providers.openai]
api_type = "open_ai_responses"
base_url = "https://openai.example/v1"
max_tokens = 4096
thinking = "high"

[providers.anthropic]
api_type = "anthropic_messages"
base_url = "https://anthropic.example/v1"
api_key_env = "ANTHROPIC_KEY"
api_key = "config-secret"
request_timeout_secs = 90
anthropic_cache_static_prefix = true

[models.fast]
provider = "openai"
model_id = "gpt-mini"

[models.strong]
provider = "anthropic"
model_id = "claude-opus"

[blend]
default_model = "fast"
"#,
        )
        .unwrap();
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(user_config_path(&home), fs::Permissions::from_mode(0o600))
                .unwrap();
        }
        let resolved =
            resolve_cli_runtime_config(&HostConfigArgs::default(), &home, &cwd, |name| {
                (name == "ARABICA_PROVIDER_OPENAI_API_KEY").then(|| "env-openai-secret".to_owned())
            })
            .unwrap();

        assert_eq!(
            resolved.model_configs["fast"].api_type,
            ApiType::OpenAiResponses
        );
        assert_eq!(resolved.model_configs["fast"].api_key, "env-openai-secret");
        assert_eq!(resolved.model_configs["fast"].max_tokens, Some(4096));
        assert_eq!(
            resolved.model_configs["fast"].reasoning_effort.as_deref(),
            Some("high")
        );
        assert_eq!(
            resolved.model_configs["strong"].api_type,
            ApiType::AnthropicMessages
        );
        assert_eq!(resolved.model_configs["strong"].api_key, "config-secret");
        assert_eq!(resolved.model_configs["strong"].request_timeout_secs, 90);
        assert!(resolved.model_configs["strong"].anthropic_cache_static_prefix);
        let env_precedence = resolve_cli_runtime_config(
            &HostConfigArgs::default(),
            &home,
            &cwd,
            |name| match name {
                "ARABICA_PROVIDER_OPENAI_API_KEY" => Some("env-openai-secret".to_owned()),
                "ANTHROPIC_KEY" => Some("env-anthropic-secret".to_owned()),
                _ => None,
            },
        )
        .unwrap();
        assert_eq!(
            env_precedence.model_configs["strong"].api_key,
            "env-anthropic-secret"
        );
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn context_policy_parses_independently_and_rejects_invalid_sets() {
        let root =
            std::env::temp_dir().join(format!("structure-context-config-{}", uuid::Uuid::now_v7()));
        fs::create_dir_all(&root).unwrap();
        fs::write(
            user_config_path(&root),
            r#"
[context]
policy_id = "read-only"
version = 1
base_sets = ["files"]
exclude = ["tool:write_file"]
disabled_sources = ["mcp"]
[context.sets]
files = ["tool:read_file", "tool:write_file"]
"#,
        )
        .unwrap();
        let policy = user_config_context(&root).unwrap().unwrap();
        assert!(policy.sets["files"].contains("tool:read_file"));
        assert!(
            policy
                .disabled_sources
                .contains(&arabica_protocol::ContextSourceKind::Mcp)
        );
        fs::write(
            user_config_path(&root),
            "[context]\npolicy_id='x'\nversion=1\nbase_sets=['missing']\n",
        )
        .unwrap();
        assert!(user_config_context(&root).is_err());
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn model_alias_rejects_an_unknown_provider_before_resolving_credentials() {
        let root = std::env::temp_dir().join(format!(
            "structure-unknown-provider-{}",
            uuid::Uuid::now_v7()
        ));
        let home = root.join("home");
        let cwd = root.join("project");
        fs::create_dir_all(&home).unwrap();
        fs::create_dir_all(&cwd).unwrap();
        fs::write(
            user_config_path(&home),
            "[models.default]\nprovider = 'missing'\nmodel_id = 'model'\n\n[blend]\ndefault_model = 'default'\n",
        )
        .unwrap();
        let error = resolve_cli_runtime_config(&HostConfigArgs::default(), &home, &cwd, |_| None)
            .unwrap_err()
            .to_string();
        assert!(error.contains("unknown provider"), "{error}");
        fs::write(
            user_config_path(&home),
            "[providers.p]\nbase_url = 'https://provider.example/v1'\n\n[models.valid]\nprovider = 'p'\nmodel_id = 'model'\n\n[blend]\ndefault_model = 'missing'\n",
        )
        .unwrap();
        let error = resolve_cli_runtime_config(&HostConfigArgs::default(), &home, &cwd, |_| None)
            .unwrap_err()
            .to_string();
        assert!(error.contains("default_model alias"), "{error}");
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn evaluation_config_selects_independent_strategies_and_rejects_unknown_ids() {
        let root = std::env::temp_dir().join(format!(
            "structure-evaluation-config-{}",
            uuid::Uuid::now_v7()
        ));
        fs::create_dir_all(&root).unwrap();
        assert_eq!(
            user_config_evaluation(&root).unwrap(),
            arabica_runtime::EvaluationConfig::default()
        );
        fs::write(user_config_path(&root), "[evaluation]\ncontext_strategy='behavior-clusters'\nmodel_strategy='observational'\nmax_clusters=2\nmin_cluster_runs=4\n").unwrap();
        let config = user_config_evaluation(&root).unwrap();
        assert_eq!(config.context_strategy, "behavior-clusters");
        assert_eq!(config.model_strategy, "observational");
        assert_eq!(config.max_clusters, 2);
        fs::write(
            user_config_path(&root),
            "[evaluation]\nmodel_strategy='missing'\n",
        )
        .unwrap();
        assert!(user_config_evaluation(&root).is_err());
        fs::write(user_config_path(&root), "[evaluation]\nmax_clusters=0\n").unwrap();
        assert!(user_config_evaluation(&root).is_err());
        fs::write(
            user_config_path(&root),
            "[evaluation]\nmax_clusters='invalid-type'\nunknown_field=true\n",
        )
        .unwrap();
        assert!(user_config_evaluation(&root).is_err());
        assert!(user_config_context(&root).unwrap().is_none());
        assert!(user_config_skill_roots(&root).unwrap().is_empty());
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn invalid_saved_thinking_is_reported() {
        let root = std::env::temp_dir().join(format!("structure-config-{}", uuid::Uuid::now_v7()));
        let home = root.join("home");
        let cwd = root.join("project");
        fs::create_dir_all(&home).unwrap();
        fs::write(
            user_config_path(&home),
            "[providers.test]\nbase_url = 'https://example.test/v1'\nthinking = 'high'\n\n[models.default]\nprovider = 'test'\nmodel_id = 'model'\n\n[blend]\ndefault_model = 'default'\n",
        )
        .unwrap();
        let result = resolve_cli_config(
            &HostConfigArgs {
                model: Some("model".to_owned()),
                base_url: Some("https://example.test/v1".to_owned()),
                ..HostConfigArgs::default()
            },
            &home,
            &cwd,
            |name| (name == "ARABICA_PROVIDER_TEST_API_KEY").then(|| "secret".to_owned()),
        );
        assert!(
            result
                .unwrap_err()
                .to_string()
                .contains("unsupported thinking level")
        );
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn saved_auth_supplies_key_and_environment_takes_precedence() {
        let root = std::env::temp_dir().join(format!("structure-config-{}", uuid::Uuid::now_v7()));
        let home = root.join("home");
        let cwd = root.join("project");
        crate::auth::save_key(&home, "saved-secret").unwrap();
        fs::create_dir_all(&home).unwrap();
        fs::write(
            user_config_path(&home),
            "[providers.default]\nbase_url = 'https://example.test/v1'\n\n[models.default]\nprovider = 'default'\nmodel_id = 'configured-model'\n\n[blend]\ndefault_model = 'default'\n",
        )
        .unwrap();
        let args = HostConfigArgs {
            model: Some("model".to_owned()),
            base_url: Some("https://example.test/v1".to_owned()),
            ..HostConfigArgs::default()
        };
        let saved = resolve_cli_config(&args, &home, &cwd, |_| None).unwrap();
        assert_eq!(saved.api_key, "saved-secret");
        let env = resolve_cli_config(&args, &home, &cwd, |name| {
            (name == "ARABICA_PROVIDER_DEFAULT_API_KEY").then(|| "env-secret".to_owned())
        })
        .unwrap();
        assert_eq!(env.api_key, "env-secret");
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn user_config_key_requires_private_file_and_environment_still_wins() {
        let root = std::env::temp_dir().join(format!("structure-config-{}", uuid::Uuid::now_v7()));
        let home = root.join("home");
        let cwd = root.join("project");
        fs::create_dir_all(&home).unwrap();
        fs::write(
            user_config_path(&home),
            "[providers.test]\napi_key = 'config-secret'\nbase_url = 'https://example.test/v1'\n\n[models.default]\nprovider = 'test'\nmodel_id = 'model'\n\n[blend]\ndefault_model = 'default'\n",
        )
        .unwrap();
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(user_config_path(&home), fs::Permissions::from_mode(0o644))
                .unwrap();
            let error =
                resolve_cli_config(&HostConfigArgs::default(), &home, &cwd, |_| None).unwrap_err();
            assert!(error.to_string().contains("chmod 600"));
            assert!(!error.to_string().contains("config-secret"));
            fs::set_permissions(user_config_path(&home), fs::Permissions::from_mode(0o600))
                .unwrap();
        }
        let config = resolve_cli_config(&HostConfigArgs::default(), &home, &cwd, |_| None).unwrap();
        assert_eq!(config.api_key, "config-secret");
        let env = resolve_cli_config(&HostConfigArgs::default(), &home, &cwd, |name| {
            (name == "ARABICA_PROVIDER_TEST_API_KEY").then(|| "env-secret".to_owned())
        })
        .unwrap();
        assert_eq!(env.api_key, "env-secret");
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn user_config_mcp_parses_stdio_and_http_servers() {
        let root = std::env::temp_dir().join(format!("structure-config-{}", uuid::Uuid::now_v7()));
        fs::create_dir_all(&root).unwrap();
        fs::write(
            user_config_path(&root),
            "[[mcp]]\nname = 'alpha'\ncommand = '/usr/bin/env'\nargs = ['python', '-m', 'fixture']\n\n[mcp.env]\nTOKEN = 'alpha-secret'\n\n[[mcp]]\nname = 'remote'\nurl = 'https://mcp.example.test/mcp'\n\n[mcp.headers]\nAuthorization = 'Bearer http-secret'\n",
        )
        .unwrap();
        // The env and headers above are credentials, so the file must be
        // owner-only for the read to be allowed at all.
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(user_config_path(&root), fs::Permissions::from_mode(0o600))
                .unwrap();
        }
        let servers = user_config_mcp(&root).unwrap();
        assert_eq!(servers.len(), 2);
        let stdio = match &servers[0] {
            McpServer::Stdio(config) => config,
            other => panic!("expected stdio server, got {other:?}"),
        };
        assert_eq!(stdio.name, "alpha");
        assert_eq!(stdio.args, vec!["python", "-m", "fixture"]);
        assert_eq!(stdio.env[0].name, "TOKEN");
        let http = match &servers[1] {
            McpServer::Http(config) => config,
            other => panic!("expected http server, got {other:?}"),
        };
        assert_eq!(http.url, "https://mcp.example.test/mcp");
        assert_eq!(http.headers[0].name, "Authorization");
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn user_config_mcp_rejects_missing_and_double_transports() {
        let root = std::env::temp_dir().join(format!("structure-config-{}", uuid::Uuid::now_v7()));
        fs::create_dir_all(&root).unwrap();
        fs::write(user_config_path(&root), "[[mcp]]\nname = 'broken'\n").unwrap();
        let error = user_config_mcp(&root).unwrap_err().to_string();
        assert!(error.contains("neither command nor url"), "{error}");
        fs::write(
            user_config_path(&root),
            "[[mcp]]\nname = 'broken'\ncommand = '/bin/true'\nurl = 'https://mcp.example.test'\n",
        )
        .unwrap();
        let error = user_config_mcp(&root).unwrap_err().to_string();
        assert!(error.contains("both command and url"), "{error}");
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn mcp_credentials_require_an_owner_only_config_file() {
        let root = std::env::temp_dir().join(format!("structure-config-{}", uuid::Uuid::now_v7()));
        fs::create_dir_all(&root).unwrap();
        fs::write(
            user_config_path(&root),
            "[[mcp]]\nname = 'remote'\nurl = 'https://mcp.example.test'\n\n[mcp.headers]\nAuthorization = 'Bearer secret'\n",
        )
        .unwrap();
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(user_config_path(&root), fs::Permissions::from_mode(0o644))
                .unwrap();
            let error = user_config_mcp(&root).unwrap_err().to_string();
            assert!(error.contains("chmod 600"), "{error}");
            // The diagnostic names the header, never its value.
            assert!(!error.contains("secret"), "{error}");
            fs::set_permissions(user_config_path(&root), fs::Permissions::from_mode(0o600))
                .unwrap();
        }
        assert_eq!(user_config_mcp(&root).unwrap().len(), 1);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn user_config_mcp_is_empty_when_absent_and_unknown_fields_are_rejected() {
        let root = std::env::temp_dir().join(format!("structure-config-{}", uuid::Uuid::now_v7()));
        fs::create_dir_all(&root).unwrap();
        assert_eq!(user_config_mcp(&root).unwrap().len(), 0);
        fs::write(
            user_config_path(&root),
            "[[mcp]]\nname = 'x'\ncommand = '/bin/true'\ntypo = true\n",
        )
        .unwrap();
        let error = user_config_mcp(&root).unwrap_err().to_string();
        assert!(error.contains("invalid TOML"), "{error}");
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn malformed_user_config_does_not_echo_credential() {
        let root = std::env::temp_dir().join(format!("structure-config-{}", uuid::Uuid::now_v7()));
        fs::create_dir_all(&root).unwrap();
        fs::write(
            user_config_path(&root),
            "[providers.test]\napi_key = 'secret-value'\ninvalid = [\n",
        )
        .unwrap();
        let error = user_config_key(&root).unwrap_err().to_string();
        assert!(error.contains("invalid TOML"));
        assert!(!error.contains("secret-value"));
        fs::remove_dir_all(root).unwrap();
    }
}
