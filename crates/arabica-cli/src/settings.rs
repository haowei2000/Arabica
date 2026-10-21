//! Structured, credential-redacted editing of the user-owned configuration.
//!
//! Requests arrive over stdin so credentials never become process arguments.
//! The TOML editing tree retains comments and untouched advanced settings.

use std::fs::{self, File, OpenOptions};
use std::io::{Read, Write};
use std::path::Path;

use clap::Subcommand;
use serde::{Deserialize, Serialize};
use serde_json::Value as Json;
use sha2::{Digest, Sha256};
use toml_edit::{Array, ArrayOfTables, DocumentMut, InlineTable, Item, Table, Value};

const MAX_REQUEST_BYTES: u64 = 4 * 1024 * 1024;

#[derive(Debug, Subcommand)]
pub enum SettingsAction {
    /// Inspect config.toml as JSON, replacing credential values with null.
    Inspect,
    /// Apply a revision-checked JSON edit request read from stdin.
    Apply,
}

#[derive(Debug, Serialize)]
pub struct SettingsSnapshot {
    pub revision: String,
    pub document: Json,
    pub secret_paths: Vec<Vec<String>>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ApplyRequest {
    pub expected_revision: String,
    pub edits: Vec<Edit>,
}

#[derive(Debug, Deserialize)]
#[serde(tag = "op", rename_all = "snake_case", deny_unknown_fields)]
pub enum Edit {
    Set { path: Vec<String>, value: Json },
    Remove { path: Vec<String> },
    Append { path: Vec<String>, value: Json },
}

impl Edit {
    fn path(&self) -> &[String] {
        match self {
            Self::Set { path, .. } | Self::Remove { path } | Self::Append { path, .. } => path,
        }
    }

    fn creates_parents(&self) -> bool {
        !matches!(self, Self::Remove { .. })
    }
}

/// Messages are fixed descriptions, never parser diagnostics or input values.
#[derive(Debug, Serialize)]
pub struct SettingsError {
    pub code: &'static str,
    pub message: &'static str,
    pub path: Vec<String>,
}

impl SettingsError {
    fn new(code: &'static str, message: &'static str, path: &[String]) -> Self {
        Self {
            code,
            message,
            path: path.to_vec(),
        }
    }

    fn invalid(message: &'static str, path: &[String]) -> Self {
        Self::new("invalid_request", message, path)
    }

    fn io(message: &'static str) -> Self {
        Self::new("io", message, &[])
    }
}

pub fn run(action: SettingsAction) -> i32 {
    let result = (|| {
        let home = arabica_adapters::default_arabica_home()
            .map_err(|_| SettingsError::io("Cannot locate the Arabica configuration directory."))?;
        match action {
            SettingsAction::Inspect => inspect(&home),
            SettingsAction::Apply => {
                let mut bytes = Vec::new();
                std::io::stdin()
                    .take(MAX_REQUEST_BYTES + 1)
                    .read_to_end(&mut bytes)
                    .map_err(|_| {
                        SettingsError::io("Cannot read the configuration edit request.")
                    })?;
                if bytes.len() as u64 > MAX_REQUEST_BYTES {
                    return Err(SettingsError::invalid(
                        "The configuration edit request is too large.",
                        &[],
                    ));
                }
                let request = serde_json::from_slice(&bytes).map_err(|_| {
                    SettingsError::invalid(
                        "Expected a JSON object with expected_revision and edits.",
                        &[],
                    )
                })?;
                apply(&home, request)
            }
        }
    })();
    match result {
        Ok(snapshot) => {
            println!(
                "{}",
                serde_json::to_string(&snapshot).expect("redacted configuration is JSON")
            );
            0
        }
        Err(error) => {
            println!("{}", serde_json::json!({"error": error}));
            2
        }
    }
}

pub fn inspect(home: &Path) -> Result<SettingsSnapshot, SettingsError> {
    snapshot(&read_source(&crate::config::user_config_path(home))?)
}

pub fn apply(home: &Path, request: ApplyRequest) -> Result<SettingsSnapshot, SettingsError> {
    if request.edits.len() > 1024 {
        return Err(SettingsError::invalid(
            "At most 1024 edits may be applied together.",
            &[],
        ));
    }
    for edit in &request.edits {
        let path = edit.path();
        if path.is_empty()
            || path.len() > 32
            || path.iter().any(|key| key.is_empty() || key.len() > 512)
        {
            return Err(SettingsError::invalid(
                "Use a non-empty configuration field path with at most 32 components.",
                path,
            ));
        }
    }
    fs::create_dir_all(home)
        .map_err(|_| SettingsError::io("Cannot create the configuration directory."))?;
    let lock_path = home.join("config.toml.lock");
    check_regular(&lock_path)?;
    let mut options = OpenOptions::new();
    options.read(true).write(true).create(true).truncate(false);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let lock = options
        .open(&lock_path)
        .map_err(|_| SettingsError::io("Cannot open the configuration edit lock."))?;
    check_open_file(&lock_path, &lock)?;
    fs2::FileExt::lock_exclusive(&lock)
        .map_err(|_| SettingsError::io("Cannot lock the configuration for editing."))?;

    let path = crate::config::user_config_path(home);
    let source = read_source(&path)?;
    check_revision(&source, &request.expected_revision)?;
    let mut document = source.parse::<DocumentMut>().map_err(|_| {
        SettingsError::new(
            "validation",
            "config.toml contains invalid TOML. Correct its syntax before saving form changes.",
            &[],
        )
    })?;
    for edit in &request.edits {
        edit_item(document.as_item_mut(), edit.path(), edit)?;
    }
    let updated = document.to_string();
    let parsed: toml::Value = toml::from_str(&updated).map_err(|_| {
        SettingsError::new(
            "validation",
            "The edited configuration cannot be represented as TOML.",
            &[],
        )
    })?;
    crate::config::validate_settings_document(&parsed)
        .map_err(|error| SettingsError::new("validation", error.message, &error.path))?;
    let result = snapshot(&updated)?;

    let mut temporary = tempfile::NamedTempFile::new_in(home)
        .map_err(|_| SettingsError::io("Cannot create a temporary configuration file."))?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        temporary
            .as_file()
            .set_permissions(fs::Permissions::from_mode(0o600))
            .map_err(|_| SettingsError::io("Cannot protect the configuration file permissions."))?;
    }
    temporary
        .write_all(updated.as_bytes())
        .and_then(|()| temporary.flush())
        .and_then(|()| temporary.as_file().sync_all())
        .map_err(|_| SettingsError::io("Cannot write the temporary configuration file."))?;
    // Other writers may not use our sidecar lock. Check the exact bytes again
    // immediately before the atomic replacement rather than overwriting them.
    check_revision(&read_source(&path)?, &request.expected_revision)?;
    temporary
        .persist(&path)
        .map_err(|_| SettingsError::io("Cannot replace config.toml atomically."))?;
    #[cfg(unix)]
    {
        File::open(home).and_then(|directory| directory.sync_all())
            .map_err(|_| SettingsError::io("The configuration was saved, but its directory could not be synchronized. Reload settings."))?;
    }
    Ok(result)
}

fn check_revision(source: &str, expected: &str) -> Result<(), SettingsError> {
    if revision(source) != expected {
        return Err(SettingsError::new(
            "conflict",
            "config.toml changed outside this form. Reload settings before saving again.",
            &[],
        ));
    }
    Ok(())
}

fn revision(source: &str) -> String {
    hex::encode(Sha256::digest(source.as_bytes()))
}

fn check_regular(path: &Path) -> Result<(), SettingsError> {
    match fs::symlink_metadata(path) {
        Ok(metadata) if metadata.file_type().is_file() => Ok(()),
        Ok(_) => Err(SettingsError::io(
            "Configuration and lock paths must be regular files, not symbolic links or directories.",
        )),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(_) => Err(SettingsError::io("Cannot inspect the configuration file.")),
    }
}

fn check_open_file(path: &Path, file: &File) -> Result<(), SettingsError> {
    check_regular(path)?;
    let opened = file
        .metadata()
        .map_err(|_| SettingsError::io("Cannot inspect the opened configuration file."))?;
    if !opened.is_file() {
        return Err(SettingsError::io(
            "The configuration must be a regular file.",
        ));
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        let current = fs::symlink_metadata(path).map_err(|_| {
            SettingsError::io("The configuration changed while it was being opened.")
        })?;
        if (opened.dev(), opened.ino()) != (current.dev(), current.ino()) {
            return Err(SettingsError::io(
                "The configuration changed while it was being opened. Reload settings.",
            ));
        }
    }
    Ok(())
}

fn read_source(path: &Path) -> Result<String, SettingsError> {
    check_regular(path)?;
    let mut file = match File::open(path) {
        Ok(file) => file,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(String::new()),
        Err(_) => {
            return Err(SettingsError::io(
                "Cannot read config.toml. Check its file permissions.",
            ));
        }
    };
    check_open_file(path, &file)?;
    let mut source = String::new();
    file.read_to_string(&mut source)
        .map_err(|_| SettingsError::io("Cannot read config.toml as UTF-8 text."))?;
    check_open_file(path, &file)?;
    Ok(source)
}

fn snapshot(source: &str) -> Result<SettingsSnapshot, SettingsError> {
    let value: toml::Value = toml::from_str(source).map_err(|_| {
        SettingsError::new(
            "validation",
            "config.toml contains invalid TOML. Correct its syntax before opening the form.",
            &[],
        )
    })?;
    let mut document = serde_json::to_value(value).map_err(|_| {
        SettingsError::new(
            "validation",
            "config.toml contains a value that cannot be represented in the settings form.",
            &[],
        )
    })?;
    let mut secret_paths = Vec::new();
    redact(&mut document, &mut Vec::new(), &mut secret_paths);
    Ok(SettingsSnapshot {
        revision: revision(source),
        document,
        secret_paths,
    })
}

fn redact(value: &mut Json, path: &mut Vec<String>, secrets: &mut Vec<Vec<String>>) {
    match value {
        Json::Object(values) => {
            for (key, value) in values {
                path.push(key.clone());
                let normalized = key.to_ascii_lowercase().replace(['_', '-'], "");
                let credential = matches!(
                    normalized.as_str(),
                    "apikey"
                        | "accesstoken"
                        | "refreshtoken"
                        | "token"
                        | "clientsecret"
                        | "privatekey"
                        | "password"
                        | "secret"
                        | "authorization"
                );
                let mcp_credentials = path.first().is_some_and(|root| root == "mcp")
                    && matches!(key.as_str(), "env" | "headers");
                if mcp_credentials {
                    // Inspect also accepts malformed configuration for repair.
                    // Never depend on a valid server-array shape to hide its
                    // credential containers, including scalar or array values.
                    if let Json::Object(entries) = value {
                        for (name, value) in entries {
                            path.push(name.clone());
                            *value = Json::Null;
                            secrets.push(path.clone());
                            path.pop();
                        }
                    } else {
                        *value = Json::Null;
                        secrets.push(path.clone());
                    }
                } else if credential {
                    *value = Json::Null;
                    secrets.push(path.clone());
                } else {
                    redact(value, path, secrets);
                }
                path.pop();
            }
        }
        Json::Array(values) => {
            for (index, value) in values.iter_mut().enumerate() {
                path.push(index.to_string());
                redact(value, path, secrets);
                path.pop();
            }
        }
        _ => {}
    }
}

fn edit_item(item: &mut Item, remaining: &[String], edit: &Edit) -> Result<(), SettingsError> {
    if remaining.is_empty() {
        return match edit {
            Edit::Set { value, .. } => {
                let mut replacement = json_item(value, edit.path())?;
                if let (Some(old), Some(new)) = (item.as_value(), replacement.as_value_mut()) {
                    *new.decor_mut() = old.decor().clone();
                }
                *item = replacement;
                Ok(())
            }
            Edit::Append { value, .. } => match item {
                Item::ArrayOfTables(array) => {
                    array.push(json_table(value, edit.path())?);
                    Ok(())
                }
                Item::Value(value_item) => append_value(value_item, value, edit.path()),
                _ => Err(SettingsError::invalid(
                    "Append requires an array field.",
                    edit.path(),
                )),
            },
            Edit::Remove { .. } => Err(SettingsError::invalid(
                "The root configuration cannot be removed.",
                edit.path(),
            )),
        };
    }
    match item {
        Item::Table(table) => edit_table(table, remaining, edit),
        Item::Value(value) => edit_value(value, remaining, edit),
        Item::ArrayOfTables(array) => {
            let index = index(&remaining[0], array.len(), edit.path())?;
            if remaining.len() == 1 {
                return match edit {
                    Edit::Remove { .. } => {
                        array.remove(index);
                        Ok(())
                    }
                    Edit::Set { value, .. } => {
                        *array.get_mut(index).expect("checked index") =
                            json_table(value, edit.path())?;
                        Ok(())
                    }
                    Edit::Append { .. } => Err(SettingsError::invalid(
                        "Append requires an array field.",
                        edit.path(),
                    )),
                };
            }
            edit_table(
                array.get_mut(index).expect("checked index"),
                &remaining[1..],
                edit,
            )
        }
        Item::None => Err(SettingsError::invalid(
            "The configuration path does not exist.",
            edit.path(),
        )),
    }
}

fn edit_table(table: &mut Table, remaining: &[String], edit: &Edit) -> Result<(), SettingsError> {
    let key = &remaining[0];
    if remaining.len() == 1 && matches!(edit, Edit::Remove { .. }) {
        return table.remove(key).map(|_| ()).ok_or_else(|| {
            SettingsError::invalid("The configuration field does not exist.", edit.path())
        });
    }
    if table.get(key).is_none() && edit.creates_parents() {
        let child = if remaining.len() == 1 {
            match edit {
                Edit::Set { value, .. } => {
                    table.insert(key, json_item(value, edit.path())?);
                    return Ok(());
                }
                Edit::Append { value, .. } if value.is_object() => {
                    Item::ArrayOfTables(ArrayOfTables::new())
                }
                Edit::Append { .. } => Item::Value(Value::Array(Array::new())),
                Edit::Remove { .. } => unreachable!(),
            }
        } else {
            Item::Table(Table::new())
        };
        table.insert(key, child);
    }
    let child = table.get_mut(key).ok_or_else(|| {
        SettingsError::invalid("The configuration path does not exist.", edit.path())
    })?;
    edit_item(child, &remaining[1..], edit)
}

fn edit_value(value: &mut Value, remaining: &[String], edit: &Edit) -> Result<(), SettingsError> {
    if remaining.is_empty() {
        return match edit {
            Edit::Set {
                value: replacement, ..
            } => {
                let mut replacement = json_value(replacement, edit.path())?;
                *replacement.decor_mut() = value.decor().clone();
                *value = replacement;
                Ok(())
            }
            Edit::Append {
                value: appended, ..
            } => append_value(value, appended, edit.path()),
            Edit::Remove { .. } => Err(SettingsError::invalid(
                "Remove requires a parent field or array index.",
                edit.path(),
            )),
        };
    }
    match value {
        Value::InlineTable(table) => {
            let key = &remaining[0];
            if remaining.len() == 1 && matches!(edit, Edit::Remove { .. }) {
                return table.remove(key).map(|_| ()).ok_or_else(|| {
                    SettingsError::invalid("The configuration field does not exist.", edit.path())
                });
            }
            if table.get(key).is_none() && edit.creates_parents() {
                let child = if remaining.len() == 1 {
                    match edit {
                        Edit::Set { value, .. } => {
                            table.insert(key, json_value(value, edit.path())?);
                            return Ok(());
                        }
                        Edit::Append { .. } => Value::Array(Array::new()),
                        Edit::Remove { .. } => unreachable!(),
                    }
                } else {
                    Value::InlineTable(InlineTable::new())
                };
                table.insert(key, child);
            }
            let child = table.get_mut(key).ok_or_else(|| {
                SettingsError::invalid("The configuration path does not exist.", edit.path())
            })?;
            edit_value(child, &remaining[1..], edit)
        }
        Value::Array(array) => {
            let index = index(&remaining[0], array.len(), edit.path())?;
            if remaining.len() == 1 && matches!(edit, Edit::Remove { .. }) {
                array.remove(index);
                return Ok(());
            }
            edit_value(
                array.get_mut(index).expect("checked index"),
                &remaining[1..],
                edit,
            )
        }
        _ => Err(SettingsError::invalid(
            "The configuration path crosses a scalar value.",
            edit.path(),
        )),
    }
}

fn append_value(target: &mut Value, appended: &Json, path: &[String]) -> Result<(), SettingsError> {
    let array = target
        .as_array_mut()
        .ok_or_else(|| SettingsError::invalid("Append requires an array field.", path))?;
    array.push_formatted(json_value(appended, path)?);
    Ok(())
}

fn index(key: &str, length: usize, path: &[String]) -> Result<usize, SettingsError> {
    key.parse::<usize>()
        .ok()
        .filter(|index| *index < length)
        .ok_or_else(|| {
            SettingsError::invalid(
                "The array index is invalid or outside the current array.",
                path,
            )
        })
}

fn json_table(value: &Json, path: &[String]) -> Result<Table, SettingsError> {
    let object = value.as_object().ok_or_else(|| {
        SettingsError::invalid("An array of tables accepts object values only.", path)
    })?;
    let mut table = Table::new();
    for (key, value) in object {
        table.insert(key, json_item(value, path)?);
    }
    Ok(table)
}

fn json_item(value: &Json, path: &[String]) -> Result<Item, SettingsError> {
    if value.is_object() {
        return Ok(Item::Table(json_table(value, path)?));
    }
    if let Some(values) = value
        .as_array()
        .filter(|values| !values.is_empty() && values.iter().all(Json::is_object))
    {
        let mut array = ArrayOfTables::new();
        for value in values {
            array.push(json_table(value, path)?);
        }
        return Ok(Item::ArrayOfTables(array));
    }
    Ok(Item::Value(json_value(value, path)?))
}

fn json_value(value: &Json, path: &[String]) -> Result<Value, SettingsError> {
    Ok(match value {
        Json::Null => {
            return Err(SettingsError::invalid(
                "Null is a redacted placeholder, not a TOML value. Omit unchanged secrets or remove the field explicitly.",
                path,
            ));
        }
        Json::Bool(value) => Value::from(*value),
        Json::String(value) => Value::from(value.as_str()),
        Json::Number(number) => {
            if let Some(value) = number.as_i64() {
                Value::from(value)
            } else if let Some(value) = number
                .as_f64()
                .filter(|value| number.is_f64() && value.is_finite())
            {
                Value::from(value)
            } else {
                return Err(SettingsError::invalid(
                    "The number is outside TOML's supported range.",
                    path,
                ));
            }
        }
        Json::Array(values) => {
            let mut array = Array::new();
            for value in values {
                array.push_formatted(json_value(value, path)?);
            }
            Value::Array(array)
        }
        Json::Object(values) => {
            let mut table = InlineTable::new();
            for (key, value) in values {
                table.insert(key, json_value(value, path)?);
            }
            Value::InlineTable(table)
        }
    })
}

#[cfg(test)]
mod tests;
