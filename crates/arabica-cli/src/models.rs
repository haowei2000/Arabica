//! Dynamic model discovery and catalog synchronization for CLI and Desktop hosts.

use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::Path;

use arabica_provider::{ApiProviderConfig, ApiType, DiscoveredModel};
use clap::Subcommand;
use toml_edit::{Array, DocumentMut, Item, Table, value};

#[derive(Subcommand, Debug)]
pub enum ModelsAction {
    /// List configured model aliases and their active routing policies.
    List,
    /// Query upstream provider endpoints and discover remote available models.
    Discover {
        /// Optional provider name to query (defaults to all configured providers).
        #[arg(long)]
        provider: Option<String>,
    },
    /// Query upstream providers and automatically register candidate models in config.toml.
    Sync {
        /// Optional provider name to sync (defaults to all configured providers).
        #[arg(long)]
        provider: Option<String>,
    },
}

pub async fn run(action: ModelsAction) -> i32 {
    let home = match arabica_adapters::default_arabica_home() {
        Ok(h) => h,
        Err(error) => {
            eprintln!("error: cannot determine Arabica home directory: {error}");
            return 2;
        }
    };
    match action {
        ModelsAction::List => list_models(&home),
        ModelsAction::Discover { provider } => discover_models(&home, provider.as_deref()).await,
        ModelsAction::Sync { provider } => sync_models(&home, provider.as_deref()).await,
    }
}

fn list_models(home: &Path) -> i32 {
    let config_path = crate::config::user_config_path(home);
    let source = match fs::read_to_string(&config_path) {
        Ok(s) => s,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            println!("No user configuration found at {}", config_path.display());
            return 0;
        }
        Err(error) => {
            eprintln!("error: cannot read {}: {error}", config_path.display());
            return 2;
        }
    };
    let doc: DocumentMut = match source.parse() {
        Ok(d) => d,
        Err(error) => {
            eprintln!("error: invalid TOML in {}: {error}", config_path.display());
            return 2;
        }
    };

    println!("Configured providers:");
    if let Some(providers) = doc.get("providers").and_then(Item::as_table_like) {
        for (name, item) in providers.iter() {
            let api_type = item
                .get("api_type")
                .and_then(Item::as_str)
                .unwrap_or("open_ai_chat_completions");
            let base_url = item
                .get("base_url")
                .and_then(Item::as_str)
                .unwrap_or("default");
            let auth = item.get("auth").and_then(Item::as_str).unwrap_or("api_key");
            println!("  • {name} (api_type: {api_type}, auth: {auth}, base_url: {base_url})");
        }
    } else {
        println!("  (none)");
    }

    println!("\nConfigured model aliases:");
    if let Some(models) = doc.get("models").and_then(Item::as_table_like) {
        for (alias, item) in models.iter() {
            let provider = item
                .get("provider")
                .and_then(Item::as_str)
                .unwrap_or("unknown");
            let model_id = item
                .get("model_id")
                .and_then(Item::as_str)
                .unwrap_or("unknown");
            println!("  • {alias} -> {provider}/{model_id}");
        }
    } else {
        println!("  (none)");
    }

    if let Some(blend) = doc.get("blend").and_then(Item::as_table_like) {
        if let Some(default_model) = blend.get("default_model").and_then(Item::as_str) {
            println!("\nBlend default model: {default_model}");
        }
        if let Some(policy) = blend.get("default_policy").and_then(Item::as_str) {
            println!("Blend default policy: {policy}");
        }
    }

    0
}

async fn discover_models(home: &Path, filter_provider: Option<&str>) -> i32 {
    let providers = match load_provider_configs(home) {
        Ok(p) => p,
        Err(error) => {
            eprintln!("error: {error}");
            return 2;
        }
    };

    if providers.is_empty() {
        eprintln!("No providers found in configuration.");
        return 1;
    }

    if let Some(filter) = filter_provider
        && !providers.iter().any(|(name, _)| name == filter)
    {
        eprintln!("error: provider '{filter}' not found in configuration.");
        return 1;
    }

    let mut any_success = false;
    for (name, config) in providers {
        if let Some(filter) = filter_provider
            && name != filter
        {
            continue;
        }

        println!(
            "Querying models from provider '{name}' ({} @ {})...",
            config.api_type, config.base_url
        );
        match config.discover_models().await {
            Ok(models) => {
                any_success = true;
                let chat_count = models
                    .iter()
                    .filter(|m| m.is_chat_completion_candidate())
                    .count();
                println!(
                    "  Discovered {} models ({} chat/coding candidates):",
                    models.len(),
                    chat_count
                );
                for model in models {
                    let kind = if model.is_chat_completion_candidate() {
                        "chat"
                    } else {
                        "utility"
                    };
                    let display = model.display_name.as_deref().unwrap_or(&model.id);
                    if display != model.id {
                        println!("    • {} [{}] ({kind})", model.id, display);
                    } else {
                        println!("    • {} ({kind})", model.id);
                    }
                }
            }
            Err(error) => {
                eprintln!("  error: discovery failed for '{name}': {error}");
            }
        }
    }

    if any_success { 0 } else { 1 }
}

async fn sync_models(home: &Path, filter_provider: Option<&str>) -> i32 {
    let providers = match load_provider_configs(home) {
        Ok(p) => p,
        Err(error) => {
            eprintln!("error: {error}");
            return 2;
        }
    };

    if providers.is_empty() {
        eprintln!("No providers found in configuration.");
        return 1;
    }

    if let Some(filter) = filter_provider
        && !providers.iter().any(|(name, _)| name == filter)
    {
        eprintln!("error: provider '{filter}' not found in configuration.");
        return 1;
    }

    let config_path = crate::config::user_config_path(home);
    let source = fs::read_to_string(&config_path).unwrap_or_default();
    let mut document: DocumentMut = source.parse().unwrap_or_else(|_| DocumentMut::new());

    let mut total_added = 0;
    for (name, config) in &providers {
        if let Some(filter) = filter_provider
            && name != filter
        {
            continue;
        }

        println!("Syncing models from provider '{name}'...");
        let discovered = match config.discover_models().await {
            Ok(models) => models,
            Err(error) => {
                eprintln!("  warning: could not discover models from '{name}': {error}");
                continue;
            }
        };

        let chat_candidates: Vec<DiscoveredModel> = discovered
            .into_iter()
            .filter(|m| m.is_chat_completion_candidate())
            .collect();

        if chat_candidates.is_empty() {
            println!("  No candidate completion models returned by provider '{name}'.");
            continue;
        }

        let providers_item = document["providers"].or_insert(Item::Table(Table::new()));
        if let Some(providers_table) = providers_item.as_table_like_mut()
            && providers_table.get(name).is_none()
        {
            let mut prov_entry = Table::new();
            if config.chatgpt_credentials.is_some() {
                prov_entry.insert("auth", value("chatgpt"));
                prov_entry.insert("api_type", value("open_ai_responses"));
            } else {
                prov_entry.insert("api_type", value(config.api_type.to_string()));
                prov_entry.insert("base_url", value(config.base_url.clone()));
            }
            providers_table.insert(name, Item::Table(prov_entry));
        }

        let models = document["models"].or_insert(Item::Table(Table::new()));
        let Some(models_table) = models.as_table_like_mut() else {
            eprintln!("error: [models] must be a TOML table");
            return 2;
        };

        let mut added_for_provider = 0;
        let mut new_aliases = Vec::new();

        // Ensure default alias exists if no models are configured
        if models_table.get("default").is_none() {
            let mut default_entry = Table::new();
            default_entry.insert("provider", value(name.clone()));
            default_entry.insert("model_id", value(chat_candidates[0].id.clone()));
            models_table.insert("default", Item::Table(default_entry));
            new_aliases.push("default".to_owned());
            added_for_provider += 1;
        }

        for candidate in chat_candidates {
            if models_table.get(&candidate.id).is_none() {
                let mut entry = Table::new();
                entry.insert("provider", value(name.clone()));
                entry.insert("model_id", value(&candidate.id));
                models_table.insert(&candidate.id, Item::Table(entry));
                new_aliases.push(candidate.id.clone());
                added_for_provider += 1;
            }
        }

        // Enable capabilities for newly added models in blend
        if !new_aliases.is_empty() {
            if document.get("blend").is_none() {
                let mut blend_table = Table::new();
                blend_table.insert("default_model", value("default"));
                document["blend"] = Item::Table(blend_table);
            }
            if let Some(blend) = document["blend"].as_table_like_mut() {
                if let Some(policies) = blend.get_mut("policies") {
                    if let Some(policies_table) = policies.as_table_like_mut() {
                        for (_, policy) in policies_table.iter_mut() {
                            enable_aliases_in_policy(policy, &new_aliases);
                        }
                    }
                } else {
                    enable_aliases_in_policy(&mut document["blend"], &new_aliases);
                }
            }
        }

        println!("  Added {added_for_provider} new model(s) for provider '{name}'.");
        total_added += added_for_provider;
    }

    if total_added > 0 {
        if let Err(error) = save_config(home, &document) {
            eprintln!("error: cannot save updated config: {error}");
            return 2;
        }
        println!(
            "\nSuccessfully updated {} with {total_added} new model(s).",
            config_path.display()
        );
    } else {
        println!("\nConfiguration is already up to date.");
    }

    0
}

fn enable_aliases_in_policy(policy: &mut Item, aliases: &[String]) {
    for key in [
        "tool_call_capable_models",
        "typed_completion_capable_models",
    ] {
        let array = match policy.get_mut(key) {
            Some(Item::Value(toml_edit::Value::Array(array))) => array,
            _ => {
                let mut array = Array::new();
                if let Some(existing) = policy.get(key).and_then(Item::as_array) {
                    for item in existing {
                        if let Some(s) = item.as_str() {
                            array.push(s);
                        }
                    }
                }
                policy[key] = Item::Value(toml_edit::Value::Array(array));
                match policy.get_mut(key) {
                    Some(Item::Value(toml_edit::Value::Array(array))) => array,
                    _ => continue,
                }
            }
        };

        for alias in aliases {
            if !array.iter().any(|v| v.as_str() == Some(alias.as_str())) {
                array.push(alias.as_str());
            }
        }
    }
}

fn load_provider_configs(
    home: &Path,
) -> Result<Vec<(String, ApiProviderConfig)>, Box<dyn std::error::Error>> {
    let config_path = crate::config::user_config_path(home);
    let source = match fs::read_to_string(&config_path) {
        Ok(s) => s,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(Vec::new()),
        Err(e) => return Err(e.into()),
    };

    let doc: DocumentMut = source.parse()?;
    let mut providers = Vec::new();

    let Some(providers_table) = doc.get("providers").and_then(Item::as_table_like) else {
        return Ok(providers);
    };

    for (name, item) in providers_table.iter() {
        let api_type_str = item
            .get("api_type")
            .and_then(Item::as_str)
            .unwrap_or("open_ai_chat_completions");
        let api_type: ApiType = api_type_str.parse()?;
        let auth = item.get("auth").and_then(Item::as_str).unwrap_or("api_key");

        let default_base_url = match api_type {
            ApiType::AnthropicMessages => "https://api.anthropic.com/v1",
            ApiType::GeminiGenerateContent | ApiType::GeminiInteractions => {
                "https://generativelanguage.googleapis.com"
            }
            ApiType::OpenAiChatCompletions | ApiType::OpenAiResponses => {
                "https://api.openai.com/v1"
            }
        };
        let base_url = item
            .get("base_url")
            .and_then(Item::as_str)
            .unwrap_or(default_base_url);

        let api_key = if let Some(key) = item.get("api_key").and_then(Item::as_str) {
            key.to_owned()
        } else if let Some(env_name) = item.get("api_key_env").and_then(Item::as_str) {
            std::env::var(env_name).unwrap_or_default()
        } else if auth.eq_ignore_ascii_case("chatgpt") {
            "chatgpt-oauth-managed".to_owned()
        } else {
            crate::auth::read_saved_key(home)?.unwrap_or_default()
        };

        let mut config = ApiProviderConfig::new(api_type, api_key, base_url, "default");
        if auth.eq_ignore_ascii_case("chatgpt") {
            config.chatgpt_credentials = Some(crate::auth::chatgpt_credentials_path(home));
        }

        providers.push((name.to_owned(), config));
    }

    let chatgpt_path = crate::auth::chatgpt_credentials_path(home);
    let has_chatgpt = providers
        .iter()
        .any(|(_, c)| c.chatgpt_credentials.is_some());
    if chatgpt_path.is_file() && !has_chatgpt {
        let mut chatgpt_config = ApiProviderConfig::new(
            ApiType::OpenAiResponses,
            "chatgpt-oauth-managed",
            "https://api.openai.com/v1",
            "gpt-6.1-sol",
        );
        chatgpt_config.chatgpt_credentials = Some(chatgpt_path);
        providers.push(("chatgpt".to_owned(), chatgpt_config));
    }

    Ok(providers)
}

fn save_config(home: &Path, document: &DocumentMut) -> Result<(), Box<dyn std::error::Error>> {
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
        fs::rename(&temporary, crate::config::user_config_path(home))?;
        let _ = fs::set_permissions(
            crate::config::user_config_path(home),
            fs::Permissions::from_mode(0o600),
        );
    }
    #[cfg(not(unix))]
    {
        fs::create_dir_all(home)?;
        let target = crate::config::user_config_path(home);
        fs::write(&target, document.to_string().as_bytes())?;
    }
    Ok(())
}
