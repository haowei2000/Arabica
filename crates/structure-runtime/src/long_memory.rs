use std::collections::BTreeMap;
use std::error::Error;
use std::fmt::{Display, Formatter};

use structure_protocol::{ContextEntry, DisclosureLevel};

const GLANCE_LIMIT: usize = 160;
const OVERVIEW_LIMIT: usize = 1_024;
const MAX_CONTEXT_PATH_BYTES: usize = 1_024;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum LongMemoryErrorKind {
    InvalidPath,
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
#[derive(Clone, Debug, Default)]
pub struct LongMemoryManager {
    entries: BTreeMap<String, String>,
}

impl LongMemoryManager {
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
}
