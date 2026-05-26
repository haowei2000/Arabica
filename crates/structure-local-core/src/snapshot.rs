use crate::manifest::{structure_core_manifest, ProductSurface, StructureCoreManifest};
use serde::Serialize;
use std::env;
use std::fs;
use std::path::{Path, PathBuf};

#[derive(Debug, Clone, Serialize)]
pub struct LocalSnapshot {
    pub repo_root: String,
    pub runtime_dir: String,
    pub frontend_dir: String,
    pub core_manifest: StructureCoreManifest,
    pub product_surfaces: Vec<ProductSurface>,
    pub llm_config: LocalLlmConfigStatus,
    pub local_only: bool,
}

#[derive(Debug, Clone, Serialize)]
pub struct LocalLlmConfigStatus {
    pub provider: String,
    pub configured: bool,
    pub api_key: LocalEnvVarStatus,
    pub base_url: LocalEnvVarStatus,
    pub model: LocalEnvVarStatus,
    pub model_name: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
pub struct LocalEnvVarStatus {
    pub name: String,
    pub configured: bool,
    pub source: String,
}

pub fn collect_snapshot(repo_root: impl AsRef<Path>) -> Result<LocalSnapshot, String> {
    let repo_root = repo_root
        .as_ref()
        .canonicalize()
        .map_err(|err| format!("failed to canonicalize repo root: {err}"))?;
    let runtime_dir = env::var("STRUCTURE_LOCAL_RUNTIME_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|_| repo_root.join(".structure/local"));
    let frontend_dir = repo_root.join("frontend");
    let core_manifest = structure_core_manifest()?;
    let product_surfaces = core_manifest.surfaces.clone();
    let llm_config = collect_llm_config_status(&repo_root);

    Ok(LocalSnapshot {
        repo_root: repo_root.display().to_string(),
        runtime_dir: runtime_dir.display().to_string(),
        frontend_dir: frontend_dir.display().to_string(),
        core_manifest,
        product_surfaces,
        llm_config,
        local_only: true,
    })
}

pub fn snapshot_json(snapshot: &LocalSnapshot) -> Result<String, String> {
    serde_json::to_string_pretty(snapshot)
        .map_err(|err| format!("failed to serialize snapshot: {err}"))
}

fn collect_llm_config_status(repo_root: &Path) -> LocalLlmConfigStatus {
    let dotenv = read_dotenv_openai_values(repo_root);
    let api_key = env_status("OPENAI__API_KEY", &dotenv);
    let base_url = env_status("OPENAI__BASE_URL", &dotenv);
    let model = env_status("OPENAI__MODEL", &dotenv);
    let model_name = env::var("OPENAI__MODEL")
        .ok()
        .filter(|value| !value.trim().is_empty())
        .or_else(|| dotenv_value(&dotenv, "OPENAI__MODEL"));
    LocalLlmConfigStatus {
        provider: "openai_compatible".to_string(),
        configured: api_key.configured && base_url.configured && model.configured,
        api_key,
        base_url,
        model,
        model_name,
    }
}

fn env_status(name: &str, dotenv: &[(String, String)]) -> LocalEnvVarStatus {
    if let Ok(value) = env::var(name) {
        return LocalEnvVarStatus {
            name: name.to_string(),
            configured: !value.trim().is_empty(),
            source: if value.trim().is_empty() {
                "shell_empty".to_string()
            } else {
                "shell".to_string()
            },
        };
    }
    if dotenv_value(dotenv, name).is_some() {
        return LocalEnvVarStatus {
            name: name.to_string(),
            configured: true,
            source: ".env".to_string(),
        };
    }
    LocalEnvVarStatus {
        name: name.to_string(),
        configured: false,
        source: "missing".to_string(),
    }
}

fn read_dotenv_openai_values(repo_root: &Path) -> Vec<(String, String)> {
    let env_path = repo_root.join(".env");
    let Ok(text) = fs::read_to_string(env_path) else {
        return Vec::new();
    };
    text.lines()
        .filter_map(parse_dotenv_assignment)
        .filter(|(key, value)| key.starts_with("OPENAI__") && !value.trim().is_empty())
        .collect()
}

fn dotenv_value(dotenv: &[(String, String)], name: &str) -> Option<String> {
    dotenv
        .iter()
        .find(|(key, _)| key == name)
        .map(|(_, value)| value.clone())
}

fn parse_dotenv_assignment(line: &str) -> Option<(String, String)> {
    let trimmed = line.trim();
    if trimmed.is_empty() || trimmed.starts_with('#') {
        return None;
    }
    let trimmed = trimmed.strip_prefix("export ").unwrap_or(trimmed);
    let (key, value) = trimmed.split_once('=')?;
    let value = value
        .trim()
        .trim_matches('"')
        .trim_matches('\'')
        .to_string();
    Some((key.trim().to_string(), value))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::sync::{Mutex, MutexGuard};
    use std::time::{SystemTime, UNIX_EPOCH};

    static OPENAI_ENV_LOCK: Mutex<()> = Mutex::new(());

    struct OpenAiEnvGuard {
        _lock: MutexGuard<'static, ()>,
        previous: Vec<(&'static str, Option<String>)>,
    }

    impl OpenAiEnvGuard {
        fn clear() -> Self {
            let lock = OPENAI_ENV_LOCK.lock().unwrap();
            let previous = openai_env_keys()
                .iter()
                .map(|key| (*key, env::var(key).ok()))
                .collect::<Vec<_>>();
            for key in openai_env_keys() {
                env::remove_var(key);
            }
            Self {
                _lock: lock,
                previous,
            }
        }
    }

    impl Drop for OpenAiEnvGuard {
        fn drop(&mut self) {
            for (key, value) in &self.previous {
                if let Some(value) = value {
                    env::set_var(key, value);
                } else {
                    env::remove_var(key);
                }
            }
        }
    }

    fn openai_env_keys() -> [&'static str; 3] {
        ["OPENAI__API_KEY", "OPENAI__BASE_URL", "OPENAI__MODEL"]
    }

    #[test]
    fn snapshot_collects_local_runtime_metadata() {
        let _env = OpenAiEnvGuard::clear();
        let root = unique_temp_dir("snapshot");
        fs::create_dir_all(root.join("src/structure")).unwrap();
        fs::create_dir_all(root.join("frontend")).unwrap();
        fs::write(root.join("pyproject.toml"), "").unwrap();

        let snapshot = collect_snapshot(&root).unwrap();

        assert!(snapshot.local_only);
        assert_eq!(snapshot.product_surfaces.len(), 3);
        assert_eq!(
            snapshot.frontend_dir,
            root.canonicalize()
                .unwrap()
                .join("frontend")
                .display()
                .to_string()
        );

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn snapshot_reports_openai_dotenv_sources_without_secrets() {
        let _env = OpenAiEnvGuard::clear();
        let root = unique_temp_dir("snapshot-openai-dotenv");
        fs::create_dir_all(root.join("src/structure")).unwrap();
        fs::create_dir_all(root.join("frontend")).unwrap();
        fs::write(root.join("pyproject.toml"), "").unwrap();
        fs::write(
            root.join(".env"),
            "OPENAI__API_KEY=dotenv-secret\nOPENAI__BASE_URL=http://dotenv.test/v1\nOPENAI__MODEL=dotenv-model\n",
        )
        .unwrap();

        let snapshot = collect_snapshot(&root).unwrap();

        assert!(snapshot.llm_config.configured);
        assert_eq!(snapshot.llm_config.provider, "openai_compatible");
        assert_eq!(snapshot.llm_config.api_key.source, ".env");
        assert_eq!(snapshot.llm_config.base_url.source, ".env");
        assert_eq!(snapshot.llm_config.model.source, ".env");
        assert_eq!(
            snapshot.llm_config.model_name.as_deref(),
            Some("dotenv-model")
        );
        let json = snapshot_json(&snapshot).unwrap();
        assert!(!json.contains("dotenv-secret"));
        assert!(!json.contains("http://dotenv.test/v1"));

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn snapshot_reports_shell_precedence_for_openai_model() {
        let _env = OpenAiEnvGuard::clear();
        env::set_var("OPENAI__MODEL", "shell-model");
        let root = unique_temp_dir("snapshot-openai-shell");
        fs::create_dir_all(root.join("src/structure")).unwrap();
        fs::create_dir_all(root.join("frontend")).unwrap();
        fs::write(root.join("pyproject.toml"), "").unwrap();
        fs::write(
            root.join(".env"),
            "OPENAI__API_KEY=dotenv-secret\nOPENAI__BASE_URL=http://dotenv.test/v1\nOPENAI__MODEL=dotenv-model\n",
        )
        .unwrap();

        let snapshot = collect_snapshot(&root).unwrap();

        assert!(snapshot.llm_config.configured);
        assert_eq!(snapshot.llm_config.model.source, "shell");
        assert_eq!(
            snapshot.llm_config.model_name.as_deref(),
            Some("shell-model")
        );

        fs::remove_dir_all(root).unwrap();
    }

    fn unique_temp_dir(label: &str) -> std::path::PathBuf {
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let path = std::env::temp_dir().join(format!(
            "structure-local-core-{label}-{}-{nanos}",
            std::process::id()
        ));
        fs::create_dir_all(&path).unwrap();
        path
    }
}
