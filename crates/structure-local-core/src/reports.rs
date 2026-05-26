use std::fs;
use std::path::Path;

pub fn read_repo_text_file(
    repo_root: impl AsRef<Path>,
    path: impl AsRef<Path>,
    max_bytes: u64,
) -> Result<String, String> {
    let repo_root = repo_root
        .as_ref()
        .canonicalize()
        .map_err(|err| format!("failed to canonicalize repo root: {err}"))?;
    let requested = path.as_ref();
    let full_path = if requested.is_absolute() {
        requested.to_path_buf()
    } else {
        repo_root.join(requested)
    };
    let canonical = full_path
        .canonicalize()
        .map_err(|err| format!("failed to resolve file: {err}"))?;
    if !canonical.starts_with(&repo_root) {
        return Err("file is outside the Structure repository".to_string());
    }
    let metadata = fs::metadata(&canonical).map_err(|err| format!("failed to stat file: {err}"))?;
    if metadata.len() > max_bytes {
        return Err(format!(
            "file is too large to preview: {} bytes > {} bytes",
            metadata.len(),
            max_bytes
        ));
    }
    fs::read_to_string(&canonical).map_err(|err| format!("failed to read file: {err}"))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::env;
    use std::path::PathBuf;
    use std::time::{SystemTime, UNIX_EPOCH};

    #[test]
    fn read_repo_text_file_rejects_outside_paths() {
        let root = unique_temp_dir("read");
        fs::create_dir_all(root.join("src/structure")).unwrap();
        fs::create_dir_all(root.join("frontend")).unwrap();
        fs::write(root.join("pyproject.toml"), "").unwrap();
        fs::write(root.join("inside.txt"), "ok").unwrap();
        let outside = root.parent().unwrap().join("outside-structure-local.txt");
        fs::write(&outside, "no").unwrap();

        assert_eq!(read_repo_text_file(&root, "inside.txt", 100).unwrap(), "ok");
        assert!(read_repo_text_file(&root, outside, 100).is_err());

        fs::remove_dir_all(root).unwrap();
    }

    pub(crate) fn unique_temp_dir(label: &str) -> PathBuf {
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let path = env::temp_dir().join(format!(
            "structure-local-core-{label}-{}-{nanos}",
            std::process::id()
        ));
        fs::create_dir_all(&path).unwrap();
        path
    }
}
