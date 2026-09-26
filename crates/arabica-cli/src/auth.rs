//! User-owned credential storage for the terminal CLI.

use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};

use clap::Subcommand;
use serde::{Deserialize, Serialize};

#[derive(Debug, Subcommand)]
pub enum AuthAction {
    /// Prompt for an API key without echoing it and save it locally.
    Login,
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

pub fn run(action: AuthAction) -> i32 {
    let home = match arabica_adapters::default_arabica_home() {
        Ok(home) => home,
        Err(error) => {
            eprintln!("error: {error}");
            return 2;
        }
    };
    let result = match action {
        AuthAction::Login => {
            let key = match rpassword::prompt_password("OpenAI API key: ") {
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
        AuthAction::Status => {
            if std::env::var("OPENAI__API_KEY").is_ok_and(|key| !key.trim().is_empty()) {
                println!("API key: environment (OPENAI__API_KEY)");
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
}
