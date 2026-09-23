//! User-owned CLI settings. Credentials stay in the process environment.

use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use structure_provider::{ApiProviderConfig, ApiType};

use crate::host::{HostConfigArgs, resolve_provider_config, workspace_id_for};

#[derive(Debug, Default, Deserialize)]
#[serde(deny_unknown_fields)]
struct UserConfig {
    #[serde(default)]
    provider: UserProvider,
}

#[derive(Debug, Default, Deserialize)]
#[serde(deny_unknown_fields)]
struct UserProvider {
    api_type: Option<String>,
    base_url: Option<String>,
    model: Option<String>,
    thinking: Option<String>,
}

#[derive(Debug, Default, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct WorkspaceSettings {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub model: Option<String>,
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
        Ok(text) => Ok(toml::from_str(&text)
            .map_err(|error| format!("invalid {}: {error}", path.display()))?),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(T::default()),
        Err(error) => Err(format!("cannot read {}: {error}", path.display()).into()),
    }
}

/// Resolve CLI settings. Explicit flags win, then saved workspace choices,
/// then environment variables, then user defaults. The API key is read only
/// from `OPENAI__API_KEY`. ACP keeps its existing environment-only behavior.
pub fn resolve_cli_config(
    args: &HostConfigArgs,
    home: &Path,
    cwd: &Path,
    lookup: impl Fn(&str) -> Option<String>,
) -> Result<ApiProviderConfig, Box<dyn std::error::Error>> {
    let user: UserConfig = read_toml(&user_config_path(home))?;
    let workspace: WorkspaceSettings = read_toml(&workspace_config_path(home, cwd))?;
    let selected = HostConfigArgs {
        model: args
            .model
            .clone()
            .or(workspace.model)
            .or_else(|| lookup("OPENAI__MODEL"))
            .or(user.provider.model),
        api_type: args
            .api_type
            .clone()
            .or_else(|| lookup("STRUCTURE__API_TYPE"))
            .or(user.provider.api_type),
        base_url: args
            .base_url
            .clone()
            .or_else(|| lookup("OPENAI__BASE_URL"))
            .or(user.provider.base_url),
    };
    let mut config = resolve_provider_config(&selected, lookup)?;
    if let Some(thinking) = workspace.thinking.or(user.provider.thinking) {
        set_thinking(&mut config, &thinking)?;
    }
    Ok(config)
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
            "[provider]\nbase_url = 'https://example.test/v1'\nmodel = 'user-model'\nthinking = 'on'\n",
        )
        .unwrap();
        let env = |name: &str| match name {
            "OPENAI__API_KEY" => Some("secret".to_owned()),
            "OPENAI__MODEL" => Some("env-model".to_owned()),
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
    fn invalid_saved_thinking_is_reported() {
        let root = std::env::temp_dir().join(format!("structure-config-{}", uuid::Uuid::now_v7()));
        let home = root.join("home");
        let cwd = root.join("project");
        fs::create_dir_all(&home).unwrap();
        fs::write(user_config_path(&home), "[provider]\nthinking = 'high'\n").unwrap();
        let result = resolve_cli_config(
            &HostConfigArgs {
                model: Some("model".to_owned()),
                base_url: Some("https://example.test/v1".to_owned()),
                ..HostConfigArgs::default()
            },
            &home,
            &cwd,
            |name| (name == "OPENAI__API_KEY").then(|| "secret".to_owned()),
        );
        assert!(
            result
                .unwrap_err()
                .to_string()
                .contains("unsupported thinking level")
        );
        fs::remove_dir_all(root).unwrap();
    }
}
