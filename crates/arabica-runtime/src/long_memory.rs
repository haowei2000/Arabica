use std::collections::BTreeMap;
use std::error::Error;
use std::fmt::{Display, Formatter};
use std::fs::{self, DirBuilder, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

use arabica_protocol::{ContextEntry, DisclosureLevel};
use rusqlite::{Connection, OptionalExtension, params};
use serde::{Deserialize, Serialize};

const GLANCE_LIMIT: usize = 160;
const OVERVIEW_LIMIT: usize = 1_024;
const MAX_CONTEXT_PATH_BYTES: usize = 1_024;
static TEMP_FILE_COUNTER: AtomicU64 = AtomicU64::new(0);

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum LongMemoryErrorKind {
    InvalidPath,
    Conflict,
    Storage,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct LongMemoryError {
    kind: LongMemoryErrorKind,
    message: String,
}

impl LongMemoryError {
    fn invalid_path(message: impl Into<String>) -> Self {
        Self {
            kind: LongMemoryErrorKind::InvalidPath,
            message: message.into(),
        }
    }

    fn conflict(message: impl Into<String>) -> Self {
        Self {
            kind: LongMemoryErrorKind::Conflict,
            message: message.into(),
        }
    }

    fn storage(message: impl Into<String>) -> Self {
        Self {
            kind: LongMemoryErrorKind::Storage,
            message: message.into(),
        }
    }

    pub fn kind(&self) -> LongMemoryErrorKind {
        self.kind
    }
}

impl Display for LongMemoryError {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        self.message.fmt(formatter)
    }
}

impl Error for LongMemoryError {}

/// Workspace-scoped durable memory.
///
/// The in-memory map is the local foundation implementation. A persistent
/// `LongMemoryStore` adapter can replace it without changing Runtime's
/// separation from session-derived short memory.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct ArchivedMemory {
    pub memory_id: String,
    pub content: String,
    pub content_hash: String,
}

/// Durable backing contract for recoverable short-memory pointers.
///
/// Implementations must make writes idempotent for identical content and
/// reject attempts to reuse an identifier for different evidence.
pub trait LongMemoryStore {
    fn put_archive(
        &mut self,
        memory_id: &str,
        content: String,
        content_hash: String,
    ) -> Result<(), LongMemoryError>;

    fn get_archive(&self, memory_id: &str) -> Result<Option<ArchivedMemory>, LongMemoryError>;

    fn contains_archive(&self, memory_id: &str) -> Result<bool, LongMemoryError> {
        Ok(self.get_archive(memory_id)?.is_some())
    }

    fn search_archives(
        &self,
        query: &str,
        limit: usize,
    ) -> Result<Vec<ArchivedMemory>, LongMemoryError>;

    fn archive_count(&self) -> Result<usize, LongMemoryError>;
}

/// Atomic filesystem archive. Each pointer is stored as one JSON envelope
/// beneath `root`, preserving the normalized memory-id hierarchy.
#[derive(Clone, Debug)]
pub struct FileArchiveStore {
    root: PathBuf,
}

impl FileArchiveStore {
    pub fn open(root: impl Into<PathBuf>) -> Result<Self, LongMemoryError> {
        let root = root.into();
        create_private_archive_dir(&root).map_err(|error| {
            LongMemoryError::storage(format!(
                "failed to create file archive {}: {error}",
                root.display()
            ))
        })?;
        Ok(Self { root })
    }

    pub fn root(&self) -> &Path {
        &self.root
    }

    fn archive_path(&self, memory_id: &str) -> Result<PathBuf, LongMemoryError> {
        let memory_id = normalize_path(memory_id)?;
        Ok(self.root.join(memory_id))
    }
}

impl LongMemoryStore for FileArchiveStore {
    fn put_archive(
        &mut self,
        memory_id: &str,
        content: String,
        content_hash: String,
    ) -> Result<(), LongMemoryError> {
        let memory_id = normalize_path(memory_id)?;
        let archive = ArchivedMemory {
            memory_id: memory_id.clone(),
            content,
            content_hash,
        };
        let path = self.archive_path(&memory_id)?;
        if let Some(parent) = path.parent() {
            create_private_archive_dir(parent).map_err(|error| {
                LongMemoryError::storage(format!(
                    "failed to create archive directory {}: {error}",
                    parent.display()
                ))
            })?;
        }
        if path.exists() {
            return verify_existing_archive(self.get_archive(&memory_id)?, &archive);
        }

        let bytes = serde_json::to_vec(&archive).map_err(|error| {
            LongMemoryError::storage(format!("failed to encode archive {memory_id}: {error}"))
        })?;
        let nonce = TEMP_FILE_COUNTER.fetch_add(1, Ordering::Relaxed);
        let temp_path = path.with_extension(format!("json.tmp-{}-{nonce}", std::process::id()));
        let write_result = (|| {
            let mut options = OpenOptions::new();
            options.write(true).create_new(true);
            #[cfg(unix)]
            {
                use std::os::unix::fs::OpenOptionsExt;
                options.mode(0o600);
            }
            let mut file = options.open(&temp_path)?;
            file.write_all(&bytes)?;
            file.sync_all()?;
            fs::hard_link(&temp_path, &path)
        })();
        let _ = fs::remove_file(&temp_path);
        match write_result {
            Ok(()) => Ok(()),
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {
                verify_existing_archive(self.get_archive(&memory_id)?, &archive)
            }
            Err(error) => Err(LongMemoryError::storage(format!(
                "failed to persist archive {}: {error}",
                path.display()
            ))),
        }
    }

    fn get_archive(&self, memory_id: &str) -> Result<Option<ArchivedMemory>, LongMemoryError> {
        let memory_id = normalize_path(memory_id)?;
        let path = self.archive_path(&memory_id)?;
        let bytes = match fs::read(&path) {
            Ok(bytes) => bytes,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
            Err(error) => {
                return Err(LongMemoryError::storage(format!(
                    "failed to read archive {}: {error}",
                    path.display()
                )));
            }
        };
        let archive: ArchivedMemory = serde_json::from_slice(&bytes).map_err(|error| {
            LongMemoryError::storage(format!(
                "archive {} is not valid JSON: {error}",
                path.display()
            ))
        })?;
        if archive.memory_id != memory_id {
            return Err(LongMemoryError::storage(format!(
                "archive {} contains mismatched memory id {}",
                path.display(),
                archive.memory_id
            )));
        }
        Ok(Some(archive))
    }

    fn contains_archive(&self, memory_id: &str) -> Result<bool, LongMemoryError> {
        Ok(self.archive_path(memory_id)?.is_file())
    }

    fn archive_count(&self) -> Result<usize, LongMemoryError> {
        count_json_files(&self.root)
    }

    fn search_archives(
        &self,
        query: &str,
        limit: usize,
    ) -> Result<Vec<ArchivedMemory>, LongMemoryError> {
        let mut matches = Vec::new();
        for path in json_files(&self.root)? {
            let relative = path.strip_prefix(&self.root).map_err(|error| {
                LongMemoryError::storage(format!(
                    "invalid archive path {}: {error}",
                    path.display()
                ))
            })?;
            let memory_id = relative.to_string_lossy().replace('\\', "/");
            let archive = self.get_archive(&memory_id)?.ok_or_else(|| {
                LongMemoryError::storage(format!("archive disappeared during search: {memory_id}"))
            })?;
            if archive_matches(&archive, query) {
                matches.push(archive);
            }
        }
        matches.sort_by(|left, right| left.memory_id.cmp(&right.memory_id));
        matches.truncate(limit);
        Ok(matches)
    }
}

fn create_private_archive_dir(path: &Path) -> std::io::Result<()> {
    let mut builder = DirBuilder::new();
    builder.recursive(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::{DirBuilderExt, PermissionsExt};
        builder.mode(0o700);
        builder.create(path)?;
        fs::set_permissions(path, fs::Permissions::from_mode(0o700))?;
    }
    #[cfg(not(unix))]
    builder.create(path)?;
    Ok(())
}

/// SQLite archive with an idempotent primary-key contract.
#[derive(Debug)]
pub struct SqliteArchiveStore {
    path: PathBuf,
    connection: Connection,
}

impl SqliteArchiveStore {
    pub fn open(path: impl Into<PathBuf>) -> Result<Self, LongMemoryError> {
        let path = path.into();
        if let Some(parent) = path
            .parent()
            .filter(|parent| !parent.as_os_str().is_empty())
        {
            fs::create_dir_all(parent).map_err(|error| {
                LongMemoryError::storage(format!(
                    "failed to create SQLite archive directory {}: {error}",
                    parent.display()
                ))
            })?;
        }
        let connection = Connection::open(&path).map_err(sqlite_error)?;
        connection
            .execute_batch(
                "PRAGMA journal_mode=WAL;\
                 PRAGMA synchronous=FULL;\
                 CREATE TABLE IF NOT EXISTS runtime_memory_archive (\
                   memory_id TEXT PRIMARY KEY NOT NULL,\
                   content TEXT NOT NULL,\
                   content_hash TEXT NOT NULL\
                 );",
            )
            .map_err(sqlite_error)?;
        Ok(Self { path, connection })
    }

    pub fn path(&self) -> &Path {
        &self.path
    }
}

impl LongMemoryStore for SqliteArchiveStore {
    fn put_archive(
        &mut self,
        memory_id: &str,
        content: String,
        content_hash: String,
    ) -> Result<(), LongMemoryError> {
        let memory_id = normalize_path(memory_id)?;
        let transaction = self.connection.transaction().map_err(sqlite_error)?;
        transaction
            .execute(
                "INSERT OR IGNORE INTO runtime_memory_archive (memory_id, content, content_hash) \
                 VALUES (?1, ?2, ?3)",
                params![memory_id, content, content_hash],
            )
            .map_err(sqlite_error)?;
        let existing = transaction
            .query_row(
                "SELECT content, content_hash FROM runtime_memory_archive WHERE memory_id = ?1",
                params![memory_id],
                |row| Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?)),
            )
            .map_err(sqlite_error)?;
        if existing != (content, content_hash) {
            return Err(LongMemoryError::conflict(format!(
                "SQLite archive id already contains different evidence: {memory_id}"
            )));
        }
        transaction.commit().map_err(sqlite_error)
    }

    fn get_archive(&self, memory_id: &str) -> Result<Option<ArchivedMemory>, LongMemoryError> {
        let memory_id = normalize_path(memory_id)?;
        self.connection
            .query_row(
                "SELECT content, content_hash FROM runtime_memory_archive WHERE memory_id = ?1",
                params![memory_id],
                |row| {
                    Ok(ArchivedMemory {
                        memory_id: memory_id.clone(),
                        content: row.get(0)?,
                        content_hash: row.get(1)?,
                    })
                },
            )
            .optional()
            .map_err(sqlite_error)
    }

    fn contains_archive(&self, memory_id: &str) -> Result<bool, LongMemoryError> {
        let memory_id = normalize_path(memory_id)?;
        self.connection
            .query_row(
                "SELECT EXISTS(SELECT 1 FROM runtime_memory_archive WHERE memory_id = ?1)",
                params![memory_id],
                |row| row.get(0),
            )
            .map_err(sqlite_error)
    }

    fn archive_count(&self) -> Result<usize, LongMemoryError> {
        self.connection
            .query_row("SELECT COUNT(*) FROM runtime_memory_archive", [], |row| {
                row.get(0)
            })
            .map_err(sqlite_error)
    }

    fn search_archives(
        &self,
        query: &str,
        limit: usize,
    ) -> Result<Vec<ArchivedMemory>, LongMemoryError> {
        let pattern = format!("%{}%", query.trim().to_lowercase());
        let mut statement = self
            .connection
            .prepare(
                "SELECT memory_id, content, content_hash FROM runtime_memory_archive \
                 WHERE lower(memory_id) LIKE ?1 OR lower(content) LIKE ?1 \
                 ORDER BY memory_id LIMIT ?2",
            )
            .map_err(sqlite_error)?;
        let rows = statement
            .query_map(params![pattern, limit], |row| {
                Ok(ArchivedMemory {
                    memory_id: row.get(0)?,
                    content: row.get(1)?,
                    content_hash: row.get(2)?,
                })
            })
            .map_err(sqlite_error)?;
        rows.collect::<Result<Vec<_>, _>>().map_err(sqlite_error)
    }
}

#[derive(Debug)]
enum ArchiveBackend {
    Memory(BTreeMap<String, ArchivedMemory>),
    File(FileArchiveStore),
    Sqlite(SqliteArchiveStore),
}

impl Default for ArchiveBackend {
    fn default() -> Self {
        Self::Memory(BTreeMap::new())
    }
}

#[derive(Debug, Default)]
pub struct LongMemoryManager {
    entries: BTreeMap<String, String>,
    archives: ArchiveBackend,
}

impl LongMemoryManager {
    pub fn with_file_archive(root: impl Into<PathBuf>) -> Result<Self, LongMemoryError> {
        Ok(Self {
            entries: BTreeMap::new(),
            archives: ArchiveBackend::File(FileArchiveStore::open(root)?),
        })
    }

    pub fn with_sqlite_archive(path: impl Into<PathBuf>) -> Result<Self, LongMemoryError> {
        Ok(Self {
            entries: BTreeMap::new(),
            archives: ArchiveBackend::Sqlite(SqliteArchiveStore::open(path)?),
        })
    }

    pub fn read(
        &self,
        path: &str,
        disclosure: DisclosureLevel,
    ) -> Result<Option<ContextEntry>, LongMemoryError> {
        let path = normalize_path(path)?;
        Ok(self.entries.get(&path).map(|content| ContextEntry {
            path,
            content: disclose(content, disclosure),
        }))
    }

    pub fn search(&self, query: &str, disclosure: DisclosureLevel) -> Vec<ContextEntry> {
        let normalized_query = query.trim().to_lowercase();
        self.entries
            .iter()
            .filter(|(path, content)| {
                normalized_query.is_empty()
                    || path.to_lowercase().contains(&normalized_query)
                    || content.to_lowercase().contains(&normalized_query)
            })
            .map(|(path, content)| ContextEntry {
                path: path.clone(),
                content: disclose(content, disclosure),
            })
            .collect()
    }

    pub fn entries(&self, disclosure: DisclosureLevel) -> Vec<ContextEntry> {
        self.search("", disclosure)
    }

    pub fn update(
        &mut self,
        path: String,
        content: String,
    ) -> Result<ContextEntry, LongMemoryError> {
        let path = normalize_path(&path)?;
        self.entries.insert(path.clone(), content.clone());
        Ok(ContextEntry { path, content })
    }

    pub fn delete(&mut self, path: &str) -> Result<Option<String>, LongMemoryError> {
        let path = normalize_path(path)?;
        Ok(self.entries.remove(&path).map(|_| path))
    }

    pub fn archived_count(&self) -> Result<usize, LongMemoryError> {
        self.archive_count()
    }
}

impl LongMemoryStore for LongMemoryManager {
    fn put_archive(
        &mut self,
        memory_id: &str,
        content: String,
        content_hash: String,
    ) -> Result<(), LongMemoryError> {
        let memory_id = normalize_path(memory_id)?;
        match &mut self.archives {
            ArchiveBackend::Memory(archives) => {
                let archive = ArchivedMemory {
                    memory_id: memory_id.clone(),
                    content,
                    content_hash,
                };
                if let Some(existing) = archives.get(&memory_id) {
                    return verify_existing_archive(Some(existing.clone()), &archive);
                }
                archives.insert(memory_id, archive);
                Ok(())
            }
            ArchiveBackend::File(store) => store.put_archive(&memory_id, content, content_hash),
            ArchiveBackend::Sqlite(store) => store.put_archive(&memory_id, content, content_hash),
        }
    }

    fn get_archive(&self, memory_id: &str) -> Result<Option<ArchivedMemory>, LongMemoryError> {
        match &self.archives {
            ArchiveBackend::Memory(archives) => {
                let memory_id = normalize_path(memory_id)?;
                Ok(archives.get(&memory_id).cloned())
            }
            ArchiveBackend::File(store) => store.get_archive(memory_id),
            ArchiveBackend::Sqlite(store) => store.get_archive(memory_id),
        }
    }

    fn contains_archive(&self, memory_id: &str) -> Result<bool, LongMemoryError> {
        match &self.archives {
            ArchiveBackend::Memory(archives) => {
                let memory_id = normalize_path(memory_id)?;
                Ok(archives.contains_key(&memory_id))
            }
            ArchiveBackend::File(store) => store.contains_archive(memory_id),
            ArchiveBackend::Sqlite(store) => store.contains_archive(memory_id),
        }
    }

    fn archive_count(&self) -> Result<usize, LongMemoryError> {
        match &self.archives {
            ArchiveBackend::Memory(archives) => Ok(archives.len()),
            ArchiveBackend::File(store) => store.archive_count(),
            ArchiveBackend::Sqlite(store) => store.archive_count(),
        }
    }

    fn search_archives(
        &self,
        query: &str,
        limit: usize,
    ) -> Result<Vec<ArchivedMemory>, LongMemoryError> {
        match &self.archives {
            ArchiveBackend::Memory(archives) => Ok(archives
                .values()
                .filter(|archive| archive_matches(archive, query))
                .take(limit)
                .cloned()
                .collect()),
            ArchiveBackend::File(store) => store.search_archives(query, limit),
            ArchiveBackend::Sqlite(store) => store.search_archives(query, limit),
        }
    }
}

fn verify_existing_archive(
    existing: Option<ArchivedMemory>,
    requested: &ArchivedMemory,
) -> Result<(), LongMemoryError> {
    match existing {
        Some(existing) if existing == *requested => Ok(()),
        Some(_) => Err(LongMemoryError::conflict(format!(
            "archive id already contains different evidence: {}",
            requested.memory_id
        ))),
        None => Err(LongMemoryError::storage(format!(
            "archive appeared concurrently but could not be read: {}",
            requested.memory_id
        ))),
    }
}

fn count_json_files(root: &Path) -> Result<usize, LongMemoryError> {
    Ok(json_files(root)?.len())
}

fn json_files(root: &Path) -> Result<Vec<PathBuf>, LongMemoryError> {
    let mut files = Vec::new();
    let mut pending = vec![root.to_path_buf()];
    while let Some(directory) = pending.pop() {
        let entries = fs::read_dir(&directory).map_err(|error| {
            LongMemoryError::storage(format!(
                "failed to list archive directory {}: {error}",
                directory.display()
            ))
        })?;
        for entry in entries {
            let entry = entry.map_err(|error| {
                LongMemoryError::storage(format!("failed to inspect archive entry: {error}"))
            })?;
            let file_type = entry.file_type().map_err(|error| {
                LongMemoryError::storage(format!(
                    "failed to inspect archive entry {}: {error}",
                    entry.path().display()
                ))
            })?;
            if file_type.is_dir() {
                pending.push(entry.path());
            } else if file_type.is_file()
                && entry.path().extension().and_then(|value| value.to_str()) == Some("json")
            {
                files.push(entry.path());
            }
        }
    }
    Ok(files)
}

fn archive_matches(archive: &ArchivedMemory, query: &str) -> bool {
    let query = query.trim().to_lowercase();
    query.is_empty()
        || archive.memory_id.to_lowercase().contains(&query)
        || archive.content.to_lowercase().contains(&query)
}

fn sqlite_error(error: rusqlite::Error) -> LongMemoryError {
    LongMemoryError::storage(format!("SQLite archive failed: {error}"))
}

fn normalize_path(path: &str) -> Result<String, LongMemoryError> {
    let path = path.trim();
    if path.is_empty() {
        return Err(LongMemoryError::invalid_path(
            "long-memory path must not be empty",
        ));
    }
    if path.len() > MAX_CONTEXT_PATH_BYTES {
        return Err(LongMemoryError::invalid_path(format!(
            "long-memory path must be at most {MAX_CONTEXT_PATH_BYTES} bytes"
        )));
    }
    if path.chars().any(char::is_control) {
        return Err(LongMemoryError::invalid_path(
            "long-memory path must not contain control characters",
        ));
    }
    if path.contains('\\') {
        return Err(LongMemoryError::invalid_path(
            "long-memory paths use forward slashes",
        ));
    }
    if path == "/" {
        return Ok(path.to_owned());
    }

    let path = path.trim_matches('/');
    let mut segments = Vec::new();
    for segment in path.split('/') {
        if segment.is_empty() {
            return Err(LongMemoryError::invalid_path(
                "long-memory path must not contain empty segments",
            ));
        }
        if matches!(segment, "." | "..") {
            return Err(LongMemoryError::invalid_path(
                "long-memory path must not contain '.' or '..' segments",
            ));
        }
        segments.push(segment);
    }
    Ok(segments.join("/"))
}

fn disclose(content: &str, level: DisclosureLevel) -> String {
    match level {
        DisclosureLevel::Glance => {
            let collapsed = content.split_whitespace().collect::<Vec<_>>().join(" ");
            truncate(&collapsed, GLANCE_LIMIT)
        }
        DisclosureLevel::Overview => truncate(content, OVERVIEW_LIMIT),
        DisclosureLevel::Detail => content.to_owned(),
    }
}

fn truncate(value: &str, limit: usize) -> String {
    if value.chars().count() <= limit {
        return value.to_owned();
    }

    let mut truncated: String = value.chars().take(limit.saturating_sub(1)).collect();
    truncated.push('…');
    truncated
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn search_matches_paths_and_full_content_case_insensitively() {
        let mut memory = LongMemoryManager::default();
        memory
            .update(
                "knowledge/Architecture".to_owned(),
                "Command bus".to_owned(),
            )
            .expect("valid path");
        memory
            .update(
                "memory/user".to_owned(),
                "Prefers concise output".to_owned(),
            )
            .expect("valid path");

        assert_eq!(
            memory.search("architecture", DisclosureLevel::Detail).len(),
            1
        );
        assert_eq!(
            memory.search("CONCISE", DisclosureLevel::Detail)[0].path,
            "memory/user"
        );
        assert_eq!(memory.entries(DisclosureLevel::Detail).len(), 2);
    }

    #[test]
    fn paths_are_canonical_and_cannot_traverse() {
        let mut memory = LongMemoryManager::default();
        let entry = memory
            .update("/knowledge/design/".to_owned(), "content".to_owned())
            .expect("leading and trailing slashes normalize");

        assert_eq!(entry.path, "knowledge/design");
        assert_eq!(
            memory
                .read("knowledge/design", DisclosureLevel::Detail)
                .expect("valid path")
                .expect("entry exists")
                .content,
            "content"
        );
        assert_eq!(
            memory
                .update("knowledge/../secret".to_owned(), "blocked".to_owned())
                .expect_err("traversal is rejected")
                .kind(),
            LongMemoryErrorKind::InvalidPath
        );
        assert_eq!(
            memory.delete("/knowledge/design/").expect("valid path"),
            Some("knowledge/design".to_owned())
        );
    }

    #[test]
    fn disclosure_is_a_read_projection_not_stored_state() {
        let mut memory = LongMemoryManager::default();
        let content = format!("first line\n{}", "detail ".repeat(200));
        let updated = memory
            .update("knowledge/long".to_owned(), content.clone())
            .expect("valid path");
        assert_eq!(
            updated.content, content,
            "mutation facts retain complete content for event replay"
        );

        let glance = memory
            .read("knowledge/long", DisclosureLevel::Glance)
            .expect("valid path")
            .expect("entry exists");
        assert!(!glance.content.contains('\n'));
        assert!(glance.content.chars().count() <= GLANCE_LIMIT);

        assert_eq!(
            memory
                .search("first line", DisclosureLevel::Detail)
                .first()
                .expect("search result")
                .content,
            content
        );
    }

    #[test]
    fn archive_writes_are_idempotent_and_conflicts_are_rejected() {
        let mut memory = LongMemoryManager::default();
        memory
            .put_archive(
                "runtime-memory/abc/1-2",
                "exact evidence".to_owned(),
                "fnv1a64:1234".to_owned(),
            )
            .expect("first archive write succeeds");
        memory
            .put_archive(
                "runtime-memory/abc/1-2",
                "exact evidence".to_owned(),
                "fnv1a64:1234".to_owned(),
            )
            .expect("identical archive write is idempotent");

        let error = memory
            .put_archive(
                "runtime-memory/abc/1-2",
                "different evidence".to_owned(),
                "fnv1a64:5678".to_owned(),
            )
            .expect_err("identifier reuse with different evidence is rejected");

        assert_eq!(error.kind(), LongMemoryErrorKind::Conflict);
        assert_eq!(memory.archived_count().expect("count succeeds"), 1);
    }

    #[test]
    fn archives_do_not_leak_into_normal_long_context_entries() {
        let mut memory = LongMemoryManager::default();
        memory
            .update("knowledge/design".to_owned(), "visible".to_owned())
            .expect("normal entry is valid");
        memory
            .put_archive(
                "runtime-memory/abc/1-2",
                "large exact evidence".to_owned(),
                "fnv1a64:1234".to_owned(),
            )
            .expect("archive write succeeds");

        assert_eq!(memory.entries(DisclosureLevel::Detail).len(), 1);
        assert_eq!(
            memory
                .get_archive("runtime-memory/abc/1-2")
                .expect("valid archive id")
                .expect("archive exists")
                .content,
            "large exact evidence"
        );
    }

    #[test]
    fn file_archive_survives_reopening_and_rejects_conflicts() {
        let root = unique_test_path("file-archive");
        {
            let mut store = FileArchiveStore::open(&root).expect("file archive opens");
            store
                .put_archive(
                    "m/tool/read/file-evidence.json",
                    "exact file evidence".to_owned(),
                    "sha256:file".to_owned(),
                )
                .expect("archive persists");
        }
        let mut reopened = FileArchiveStore::open(&root).expect("file archive reopens");
        assert_eq!(
            reopened
                .get_archive("m/tool/read/file-evidence.json")
                .expect("archive read succeeds")
                .expect("archive exists")
                .content,
            "exact file evidence"
        );
        assert_eq!(reopened.archive_count().expect("count succeeds"), 1);
        assert_eq!(
            reopened
                .search_archives("file evidence", 5)
                .expect("search succeeds")[0]
                .memory_id,
            "m/tool/read/file-evidence.json"
        );
        assert_eq!(
            reopened
                .put_archive(
                    "m/tool/read/file-evidence.json",
                    "different".to_owned(),
                    "sha256:different".to_owned(),
                )
                .expect_err("conflict is rejected")
                .kind(),
            LongMemoryErrorKind::Conflict
        );
        fs::remove_dir_all(root).expect("test archive cleanup succeeds");
    }

    #[cfg(unix)]
    #[test]
    fn file_archive_keeps_evidence_owner_only() {
        use std::os::unix::fs::PermissionsExt;

        let root = unique_test_path("private-archive");
        let mut store = FileArchiveStore::open(&root).expect("file archive opens");
        store
            .put_archive(
                "m/tool/read/evidence.json",
                "private evidence".to_owned(),
                "sha256:private".to_owned(),
            )
            .expect("archive persists");
        assert_eq!(
            fs::metadata(&root).unwrap().permissions().mode() & 0o777,
            0o700
        );
        let path = root.join("m/tool/read/evidence.json");
        assert_eq!(
            fs::metadata(path).unwrap().permissions().mode() & 0o777,
            0o600
        );
        fs::remove_dir_all(root).ok();
    }

    #[test]
    fn sqlite_archive_survives_reopening_and_is_idempotent() {
        let root = unique_test_path("sqlite-archive");
        let path = root.join("runtime-memory.sqlite3");
        {
            let mut store = SqliteArchiveStore::open(&path).expect("SQLite archive opens");
            store
                .put_archive(
                    "runtime-memory/sqlite/3-4",
                    "exact SQLite evidence".to_owned(),
                    "sha256:sqlite".to_owned(),
                )
                .expect("archive persists");
        }
        let mut reopened = SqliteArchiveStore::open(&path).expect("SQLite archive reopens");
        reopened
            .put_archive(
                "runtime-memory/sqlite/3-4",
                "exact SQLite evidence".to_owned(),
                "sha256:sqlite".to_owned(),
            )
            .expect("identical write remains idempotent after restart");
        assert_eq!(reopened.archive_count().expect("count succeeds"), 1);
        assert_eq!(
            reopened
                .search_archives("SQLite evidence", 5)
                .expect("search succeeds")[0]
                .memory_id,
            "runtime-memory/sqlite/3-4"
        );
        assert_eq!(
            reopened
                .get_archive("runtime-memory/sqlite/3-4")
                .expect("archive read succeeds")
                .expect("archive exists")
                .content,
            "exact SQLite evidence"
        );
        drop(reopened);
        fs::remove_dir_all(root).expect("test archive cleanup succeeds");
    }

    fn unique_test_path(label: &str) -> PathBuf {
        std::env::temp_dir().join(format!(
            "structure-{label}-{}-{}",
            std::process::id(),
            TEMP_FILE_COUNTER.fetch_add(1, Ordering::Relaxed)
        ))
    }
}
