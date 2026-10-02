//! Local skill discovery. Runtime receives immutable text, never filesystem paths.

use std::collections::{BTreeMap, BTreeSet};
use std::io::Read;
use std::path::{Component, Path, PathBuf};

use arabica_protocol::{ContextIdentity, ContextSourceKind};
use arabica_runtime::{RuntimeError, RuntimeErrorKind, SkillDefinition, SkillSource};
use serde::Deserialize;
use sha2::{Digest, Sha256};

const MAX_FILE_BYTES: usize = 1024 * 1024;
const MAX_SKILL_BYTES: usize = 4 * MAX_FILE_BYTES;
const MAX_SKILLS: usize = 256;

#[derive(Debug)]
pub struct LocalSkillSource {
    roots: Vec<PathBuf>,
}

#[derive(Default, Deserialize)]
struct Metadata {
    name: Option<String>,
    id: Option<String>,
    version: Option<String>,
    #[serde(default, alias = "requires")]
    requirements: BTreeSet<String>,
    #[serde(default)]
    resources: BTreeSet<String>,
}

fn invalid(message: &'static str) -> RuntimeError {
    RuntimeError::new(RuntimeErrorKind::InvalidConfiguration, message)
}

fn read_text(path: &Path, root: &Path) -> Result<String, RuntimeError> {
    let path = path
        .canonicalize()
        .map_err(|_| invalid("skill file is unavailable"))?;
    if !path.starts_with(root) {
        return Err(invalid("skill file escapes its configured directory"));
    }
    let file = std::fs::File::open(path).map_err(|_| invalid("skill file cannot be opened"))?;
    let mut bytes = Vec::new();
    file.take((MAX_FILE_BYTES + 1) as u64)
        .read_to_end(&mut bytes)
        .map_err(|_| invalid("skill file cannot be read"))?;
    if bytes.len() > MAX_FILE_BYTES {
        return Err(invalid("skill file exceeds the size limit"));
    }
    String::from_utf8(bytes).map_err(|_| invalid("skill files must contain UTF-8 text"))
}

fn parse(text: &str) -> Result<(Metadata, String), RuntimeError> {
    let normalized = text.trim_start_matches('\u{feff}').replace("\r\n", "\n");
    let Some(rest) = normalized.strip_prefix("---\n") else {
        return Ok((Metadata::default(), normalized));
    };
    let mut offset = 0;
    for line in rest.split_inclusive('\n') {
        if matches!(line.trim_end_matches('\n'), "---" | "...") {
            let header = &rest[..offset];
            let metadata = serde_saphyr::from_str(header)
                .map_err(|_| invalid("invalid skill YAML frontmatter"))?;
            return Ok((metadata, rest[offset + line.len()..].to_owned()));
        }
        offset += line.len();
    }
    Err(invalid("skill YAML frontmatter has no closing delimiter"))
}

impl LocalSkillSource {
    pub fn new(roots: Vec<PathBuf>) -> Self {
        Self { roots }
    }

    fn load_directory(directory: &Path) -> Result<SkillDefinition, RuntimeError> {
        let source = read_text(&directory.join("SKILL.md"), directory)?;
        let (metadata, instructions) = parse(&source)?;
        let name = metadata
            .name
            .or_else(|| {
                directory
                    .file_name()
                    .and_then(|name| name.to_str())
                    .map(str::to_owned)
            })
            .ok_or_else(|| invalid("skill requires a name"))?;
        if name.trim().is_empty()
            || name.len() > 128
            || name.chars().any(char::is_control)
            || instructions.trim().is_empty()
        {
            return Err(invalid(
                "skill requires a valid name and non-empty instructions",
            ));
        }
        let id = metadata.id.unwrap_or_else(|| format!("skill:{name}"));
        if !id.starts_with("skill:")
            || id.trim_start_matches("skill:").trim().is_empty()
            || metadata.requirements.iter().any(|id| id.trim().is_empty())
        {
            return Err(invalid("invalid skill ID or requirement"));
        }
        let mut resources = BTreeMap::new();
        let mut bytes = source.len();
        for resource in metadata.resources {
            let relative = Path::new(&resource);
            if relative.as_os_str().is_empty()
                || relative
                    .components()
                    .any(|component| !matches!(component, Component::Normal(_)))
            {
                return Err(invalid(
                    "skill resources must be relative paths without traversal",
                ));
            }
            let content = read_text(&directory.join(relative), directory)?;
            bytes += content.len();
            if bytes > MAX_SKILL_BYTES {
                return Err(invalid("skill snapshot exceeds the size limit"));
            }
            resources.insert(resource, content);
        }
        let fingerprint = format!(
            "{:x}",
            Sha256::digest(serde_json::to_vec(&(&source, &resources)).expect("skill snapshot"))
        );
        let version = metadata.version.map_or(fingerprint.clone(), |version| {
            format!("{version}:{fingerprint}")
        });
        Ok(SkillDefinition {
            identity: ContextIdentity {
                id,
                kind: ContextSourceKind::Skill,
                version: Some(version),
                display_name: name,
                server_id: None,
                tool_id: None,
            },
            instructions,
            requirements: metadata.requirements,
            resources,
        })
    }
}

impl SkillSource for LocalSkillSource {
    fn load(&self) -> Result<Vec<SkillDefinition>, RuntimeError> {
        let mut directories = BTreeSet::new();
        for root in &self.roots {
            let root = root
                .canonicalize()
                .map_err(|_| invalid("configured skill root is unavailable"))?;
            if !root.is_dir() {
                return Err(invalid("configured skill root must be a directory"));
            }
            if root.join("SKILL.md").exists() {
                directories.insert(root);
                continue;
            }
            for entry in
                std::fs::read_dir(&root).map_err(|_| invalid("skill root cannot be scanned"))?
            {
                let entry = entry.map_err(|_| invalid("skill root cannot be scanned"))?;
                let directory = entry.path();
                if directory.is_dir() && directory.join("SKILL.md").exists() {
                    let directory = directory
                        .canonicalize()
                        .map_err(|_| invalid("skill directory is unavailable"))?;
                    if !directory.starts_with(&root) {
                        return Err(invalid("skill directory escapes its configured root"));
                    }
                    directories.insert(directory);
                }
            }
        }
        if directories.len() > MAX_SKILLS {
            return Err(invalid("too many skills in the configured roots"));
        }
        let mut skills = Vec::new();
        let mut total_bytes = 0;
        let mut ids = BTreeSet::new();
        let mut names = BTreeSet::new();
        for directory in directories {
            let skill = Self::load_directory(&directory)?;
            total_bytes +=
                skill.instructions.len() + skill.resources.values().map(String::len).sum::<usize>();
            if total_bytes > 16 * MAX_FILE_BYTES {
                return Err(invalid("skill catalog exceeds the size limit"));
            }
            if !ids.insert(skill.identity.id.clone())
                || !names.insert(skill.identity.display_name.clone())
            {
                return Err(invalid("duplicate skill ID or name"));
            }
            skills.push(skill);
        }
        skills.sort_by(|a, b| a.identity.id.cmp(&b.identity.id));
        Ok(skills)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn root() -> PathBuf {
        let root = std::env::temp_dir().join(format!("structure-skills-{}", uuid::Uuid::now_v7()));
        std::fs::create_dir_all(root.join("review/references")).unwrap();
        root
    }

    #[test]
    fn loads_yaml_and_snapshots_declared_resources_without_executing_them() {
        let root = root();
        std::fs::write(root.join("review/SKILL.md"), "---\nname: paper-review\ndescription: >\n  Review a paper.\nrequires: [tool:read_file]\nresources: [references/checklist.md]\n---\nFollow the review checklist.").unwrap();
        std::fs::write(
            root.join("review/references/checklist.md"),
            "Check evidence.",
        )
        .unwrap();
        let source = LocalSkillSource::new(vec![root.clone()]);
        let first = source.load().unwrap();
        assert_eq!(first[0].identity.id, "skill:paper-review");
        assert_eq!(
            first[0].resources["references/checklist.md"],
            "Check evidence."
        );
        std::fs::write(
            root.join("review/references/checklist.md"),
            "New checklist.",
        )
        .unwrap();
        let second = source.load().unwrap();
        assert_ne!(first[0].identity.version, second[0].identity.version);
        assert_eq!(
            first[0].resources["references/checklist.md"],
            "Check evidence."
        );
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn rejects_traversal_duplicate_names_and_invalid_frontmatter() {
        let root = root();
        let path = root.join("review/SKILL.md");
        std::fs::write(
            &path,
            "---\nname: review\nresources: [../private.md]\n---\nReview.",
        )
        .unwrap();
        let source = LocalSkillSource::new(vec![root.clone()]);
        assert!(source.load().is_err());
        std::fs::write(&path, "---\nname: [broken\n---\nReview.").unwrap();
        assert!(source.load().is_err());
        std::fs::write(&path, "---\nname: same\n---\nReview.").unwrap();
        std::fs::create_dir_all(root.join("other")).unwrap();
        std::fs::write(root.join("other/SKILL.md"), "---\nname: same\n---\nReview.").unwrap();
        assert!(source.load().is_err());
        std::fs::remove_dir_all(root).unwrap();
    }

    #[cfg(unix)]
    #[test]
    fn rejects_resources_linked_outside_the_skill_directory() {
        let root = root();
        std::fs::write(root.join("outside.md"), "private").unwrap();
        std::os::unix::fs::symlink(root.join("outside.md"), root.join("review/link.md")).unwrap();
        std::fs::write(
            root.join("review/SKILL.md"),
            "---\nname: review\nresources: [link.md]\n---\nReview.",
        )
        .unwrap();
        assert!(LocalSkillSource::new(vec![root.clone()]).load().is_err());
        std::fs::remove_dir_all(root).unwrap();
    }
}
