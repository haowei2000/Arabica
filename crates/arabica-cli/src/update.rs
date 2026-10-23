//! Signed release updates for standalone CLI installations.

use std::fs::{self, File, OpenOptions};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use base64::Engine;
use clap::{Args, ValueEnum};
use ed25519_dalek::{Signature, VerifyingKey};
use semver::Version;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

type Result<T> = std::result::Result<T, Box<dyn std::error::Error + Send + Sync>>;
const REPOSITORY: &str = "https://github.com/haowei2000/Arabica";
const RELEASE_API: &str = "https://api.github.com/repos/haowei2000/Arabica/releases/latest";
const MAX_ARCHIVE: usize = 128 * 1024 * 1024;
const MAX_BINARY: u64 = 256 * 1024 * 1024;
const CHECK_INTERVAL: u64 = 24 * 60 * 60;
const PUBLIC_KEY: &str = include_str!("update-public-key.txt");

#[derive(Args, Debug)]
pub struct UpdateArgs {
    /// Report the latest stable version without installing it.
    #[arg(long, conflicts_with_all = ["auto", "background"])]
    pub check: bool,
    /// Enable or disable daily background updates for interactive CLI sessions.
    #[arg(long, value_enum, conflicts_with = "background")]
    pub auto: Option<AutomaticUpdates>,
    /// Output a machine-readable result.
    #[arg(long, conflicts_with = "background")]
    pub json: bool,
    #[arg(long, hide = true)]
    pub background: bool,
}

#[derive(Clone, Copy, Debug, ValueEnum)]
pub enum AutomaticUpdates {
    On,
    Off,
}

#[derive(Deserialize, Serialize)]
#[serde(default)]
struct UpdateState {
    enabled: bool,
    last_checked_at: u64,
    installed_version: Option<String>,
    last_error: Option<String>,
}

impl Default for UpdateState {
    fn default() -> Self {
        Self {
            enabled: true,
            last_checked_at: 0,
            installed_version: None,
            last_error: None,
        }
    }
}

#[derive(Deserialize)]
struct Release {
    tag_name: String,
    draft: bool,
    prerelease: bool,
}

#[derive(Deserialize)]
struct UpdateManifest {
    version: String,
    archive: String,
    sha256: String,
    size: usize,
}

#[derive(Serialize)]
struct UpdateResult {
    current_version: &'static str,
    latest_version: String,
    update_available: bool,
    updated: bool,
    automatic_updates: bool,
}

fn now() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs()
}

fn read_state(home: &Path) -> Result<UpdateState> {
    match fs::read(home.join("updates.json")) {
        Ok(bytes) => Ok(serde_json::from_slice(&bytes)?),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(UpdateState::default()),
        Err(error) => Err(error.into()),
    }
}

fn write_state(home: &Path, state: &UpdateState) -> Result<()> {
    let mut temporary = tempfile::NamedTempFile::new_in(home)?;
    serde_json::to_writer_pretty(temporary.as_file_mut(), state)?;
    temporary.as_file_mut().sync_all()?;
    temporary.persist(home.join("updates.json"))?;
    Ok(())
}

fn lock(home: &Path) -> Result<File> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::DirBuilderExt;
        fs::DirBuilder::new()
            .recursive(true)
            .mode(0o700)
            .create(home)?;
    }
    #[cfg(not(unix))]
    fs::create_dir_all(home)?;
    let mut options = OpenOptions::new();
    options.read(true).write(true).create(true).truncate(false);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let file = options.open(home.join("updates.lock"))?;
    fs2::FileExt::try_lock_exclusive(&file).map_err(|_| "another update operation is running")?;
    Ok(file)
}

fn installation_error(executable: &Path) -> Option<&'static str> {
    let text = executable.to_string_lossy().replace('\\', "/");
    let lower = text.to_ascii_lowercase();
    if text.contains(".app/Contents/") {
        Some("the desktop app manages its bundled CLI; use Check for Updates in the app")
    } else if text.contains("/Cellar/")
        || text.contains("/Caskroom/")
        || text.contains("/nix/store/")
        || lower.contains("/scoop/apps/")
        || lower.contains("/microsoft/winget/")
        || lower.contains("/chocolatey/")
    {
        Some("this installation is managed by a package manager; update it with that manager")
    } else if text.contains("/target/debug/") || text.contains("/target/release/") {
        Some("this is a development build; rebuild it from source")
    } else {
        let cargo_bin = std::env::var_os("CARGO_HOME")
            .map(PathBuf::from)
            .or_else(|| {
                std::env::var_os("HOME")
                    .or_else(|| std::env::var_os("USERPROFILE"))
                    .map(|home| PathBuf::from(home).join(".cargo"))
            })
            .map(|home| home.join("bin"));
        let cargo_receipt = executable
            .parent()
            .and_then(Path::parent)
            .is_some_and(|root| {
                root.join(".crates.toml").is_file() || root.join(".crates2.json").is_file()
            });
        if cargo_bin.as_deref() == executable.parent() || cargo_receipt {
            Some("this is a Cargo installation; run `cargo install arabica-cli --locked --force`")
        } else if matches!(
            executable.parent().and_then(Path::to_str),
            Some("/usr/bin" | "/bin")
        ) {
            Some("this is a system installation; update it with your package manager")
        } else {
            None
        }
    }
}

fn lock_installation(executable: &Path) -> Result<File> {
    let parent = executable
        .parent()
        .ok_or("executable has no installation directory")?;
    let mut options = OpenOptions::new();
    options.read(true).write(true).create(true).truncate(false);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let file = options.open(parent.join(".arabica-update.lock"))?;
    fs2::FileExt::try_lock_exclusive(&file)
        .map_err(|_| "another process is updating this installation")?;
    Ok(file)
}

/// Launch a separate, quiet updater so downloads never block or redraw a TUI.
/// Only the interactive entry point calls this; ACP and scripts do not.
pub fn schedule_background_update() {
    if cfg!(debug_assertions)
        || std::env::var_os("ARABICA_NO_UPDATE").is_some()
        || std::env::var_os("CI").is_some()
    {
        return;
    }
    let Ok(home) = arabica_adapters::default_arabica_home() else {
        return;
    };
    let Ok(state) = read_state(&home) else { return };
    if !state.enabled || now().saturating_sub(state.last_checked_at) < CHECK_INTERVAL {
        return;
    }
    let Ok(executable) = std::env::current_exe().and_then(fs::canonicalize) else {
        return;
    };
    if installation_error(&executable).is_some() {
        return;
    }
    let mut command = Command::new(executable);
    command
        .args(["update", "--background"])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000); // CREATE_NO_WINDOW
    }
    if let Ok(mut child) = command.spawn() {
        // Reap the helper when the interactive process remains alive.
        let _ = std::thread::Builder::new()
            .name("arabica-update".into())
            .spawn(move || {
                let _ = child.wait();
            });
    }
}

pub async fn run(args: UpdateArgs) -> i32 {
    let result = run_inner(&args).await;
    match result {
        Ok(()) => 0,
        Err(error) => {
            if !args.background {
                eprintln!("error: {error}");
            }
            1
        }
    }
}

async fn run_inner(args: &UpdateArgs) -> Result<()> {
    let home = arabica_adapters::default_arabica_home()?;
    let _lock = lock(&home)?;
    let mut state = read_state(&home)?;
    if let Some(automatic) = args.auto {
        state.enabled = matches!(automatic, AutomaticUpdates::On);
        write_state(&home, &state)?;
        if args.json {
            println!(
                "{}",
                serde_json::json!({"automatic_updates": state.enabled})
            );
        } else {
            println!(
                "Automatic CLI updates: {}",
                if state.enabled { "on" } else { "off" }
            );
        }
        return Ok(());
    }
    if args.background
        && (!state.enabled || now().saturating_sub(state.last_checked_at) < CHECK_INTERVAL)
    {
        return Ok(());
    }
    let _installation_lock = if !args.check {
        let executable = fs::canonicalize(std::env::current_exe()?)?;
        if let Some(message) = installation_error(&executable) {
            return Err(message.into());
        }
        Some(lock_installation(&executable)?)
    } else {
        None
    };
    let current = Version::parse(env!("CARGO_PKG_VERSION"))?;
    let client = reqwest::Client::builder()
        .user_agent(concat!("Arabica/", env!("CARGO_PKG_VERSION")))
        .connect_timeout(Duration::from_secs(5))
        .timeout(Duration::from_secs(120))
        .build()?;
    state.last_checked_at = now();
    state.last_error = None;
    write_state(&home, &state)?;
    let outcome = perform_update(&client, &current, args.check, state.enabled).await;
    match outcome {
        Ok(result) => {
            if result.updated {
                state.installed_version = Some(result.latest_version.clone());
            }
            write_state(&home, &state)?;
            if !args.background {
                if args.json {
                    println!("{}", serde_json::to_string(&result)?);
                } else if result.updated {
                    println!(
                        "Updated Arabica to {}. New sessions will use it.",
                        result.latest_version
                    );
                } else if result.update_available {
                    println!(
                        "Arabica {} is available (installed: {}). Run `arabica update`.",
                        result.latest_version, result.current_version
                    );
                } else {
                    println!("Arabica {} is up to date.", result.current_version);
                }
            }
            Ok(())
        }
        Err(error) => {
            state.last_error = Some(error.to_string().chars().take(512).collect());
            let _ = write_state(&home, &state);
            Err(error)
        }
    }
}

async fn download(client: &reqwest::Client, url: &str, maximum: usize) -> Result<Vec<u8>> {
    let mut response = client.get(url).send().await?.error_for_status()?;
    if response
        .content_length()
        .is_some_and(|length| length > maximum as u64)
    {
        return Err("update download exceeds its size limit".into());
    }
    let mut bytes = Vec::new();
    while let Some(chunk) = response.chunk().await? {
        if chunk.len() > maximum.saturating_sub(bytes.len()) {
            return Err("update download exceeds its size limit".into());
        }
        bytes.extend_from_slice(&chunk);
    }
    Ok(bytes)
}

fn platform() -> Result<(&'static str, &'static str)> {
    match (std::env::consts::OS, std::env::consts::ARCH) {
        ("macos", "aarch64") => Ok((
            "structure-macos-aarch64.tar.gz",
            "structure-aarch64-apple-darwin/arabica",
        )),
        ("macos", "x86_64") => Ok((
            "structure-macos-x86_64.tar.gz",
            "structure-x86_64-apple-darwin/arabica",
        )),
        ("linux", "aarch64") => Ok((
            "structure-linux-aarch64.tar.gz",
            "structure-aarch64-unknown-linux-gnu/arabica",
        )),
        ("linux", "x86_64") => Ok((
            "structure-linux-x86_64.tar.gz",
            "structure-x86_64-unknown-linux-gnu/arabica",
        )),
        ("windows", "x86_64") => Ok((
            "structure-windows-x86_64.zip",
            "structure-x86_64-pc-windows-msvc/arabica.exe",
        )),
        _ => Err("no official update archive exists for this platform".into()),
    }
}

async fn perform_update(
    client: &reqwest::Client,
    current: &Version,
    check: bool,
    automatic: bool,
) -> Result<UpdateResult> {
    let release: Release =
        serde_json::from_slice(&download(client, RELEASE_API, 2 * 1024 * 1024).await?)?;
    let version = Version::parse(
        release
            .tag_name
            .strip_prefix('v')
            .ok_or("invalid release tag")?,
    )?;
    if release.draft || release.prerelease || !version.pre.is_empty() {
        return Err("latest release is not a stable release".into());
    }
    let mut result = UpdateResult {
        current_version: env!("CARGO_PKG_VERSION"),
        latest_version: version.to_string(),
        update_available: version > *current,
        updated: false,
        automatic_updates: automatic,
    };
    if check || !result.update_available {
        return Ok(result);
    }
    let (archive_name, binary_path) = platform()?;
    let root = format!("{REPOSITORY}/releases/download/v{version}/{archive_name}");
    let manifest = download(client, &format!("{root}.update.json"), 4096).await?;
    let signature = download(client, &format!("{root}.sig"), 4096).await?;
    let public_key = base64::engine::general_purpose::STANDARD.decode(PUBLIC_KEY.trim())?;
    let key = VerifyingKey::from_bytes(
        &public_key
            .try_into()
            .map_err(|_| "invalid update public key")?,
    )?;
    let signature = base64::engine::general_purpose::STANDARD
        .decode(std::str::from_utf8(&signature)?.trim())?;
    key.verify_strict(&manifest, &Signature::from_slice(&signature)?)
        .map_err(|_| "update signature verification failed")?;
    let manifest: UpdateManifest = serde_json::from_slice(&manifest)?;
    if manifest.version != version.to_string()
        || manifest.archive != archive_name
        || manifest.size == 0
        || manifest.size > MAX_ARCHIVE
        || manifest.sha256.len() != 64
        || !manifest.sha256.bytes().all(|byte| byte.is_ascii_hexdigit())
    {
        return Err("signed update manifest does not match the requested release".into());
    }
    let archive = download(client, &root, manifest.size).await?;
    if archive.len() != manifest.size
        || hex::encode(Sha256::digest(&archive)) != manifest.sha256.to_ascii_lowercase()
    {
        return Err("update checksum verification failed".into());
    }
    let temporary = tempfile::tempdir()?;
    let binary = temporary.path().join(if cfg!(windows) {
        "arabica.exe"
    } else {
        "arabica"
    });
    extract_binary(
        &archive,
        archive_name.ends_with(".zip"),
        binary_path,
        &binary,
    )?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(&binary, fs::Permissions::from_mode(0o755))?;
    }
    self_replace::self_replace(&binary)?;
    result.updated = true;
    Ok(result)
}

fn extract_binary(archive: &[u8], zip: bool, expected_path: &str, output: &Path) -> Result<()> {
    let mut destination = File::create(output)?;
    if zip {
        let mut archive = zip::ZipArchive::new(std::io::Cursor::new(archive))?;
        let mut entry = archive.by_name(expected_path)?;
        if !entry.is_file()
            || entry
                .unix_mode()
                .is_some_and(|mode| mode & 0o170000 != 0 && mode & 0o170000 != 0o100000)
            || entry.size() > MAX_BINARY
        {
            return Err("update archive contains an invalid binary".into());
        }
        std::io::copy(&mut entry.by_ref().take(MAX_BINARY + 1), &mut destination)?;
    } else {
        let decoder = flate2::read::GzDecoder::new(archive).take(MAX_BINARY * 2);
        let mut archive = tar::Archive::new(decoder);
        let mut found = false;
        for entry in archive.entries()?.take(1000) {
            let mut entry = entry?;
            if entry.path()?.as_ref() != Path::new(expected_path) {
                continue;
            }
            if !entry.header().entry_type().is_file() || entry.size() > MAX_BINARY {
                return Err("update archive contains an invalid binary".into());
            }
            std::io::copy(&mut entry.by_ref().take(MAX_BINARY + 1), &mut destination)?;
            found = true;
            break;
        }
        if !found {
            return Err("update archive does not contain the expected binary".into());
        }
    }
    if destination.metadata()?.len() == 0 || destination.metadata()?.len() > MAX_BINARY {
        return Err("update binary has an invalid size".into());
    }
    destination.flush()?;
    destination.sync_all()?;
    Ok(())
}
