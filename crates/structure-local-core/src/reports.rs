use serde::Serialize;
use std::fs;
use std::path::Path;
use std::time::UNIX_EPOCH;

const REPORT_EXTENSIONS: &[&str] = &["json", "md", "markdown"];

#[derive(Debug, Clone, Serialize)]
pub struct LocalFile {
    pub path: String,
    pub relative_path: String,
    pub size_bytes: u64,
    pub modified_unix_seconds: u64,
}

pub fn recent_benchmark_reports(
    repo_root: impl AsRef<Path>,
    limit: usize,
) -> Result<Vec<LocalFile>, String> {
    let repo_root = repo_root.as_ref();
    let mut files = Vec::new();
    for dir in ["benchmark_runs", "reports"] {
        let scan_root = repo_root.join(dir);
        collect_report_files(repo_root, &scan_root, &scan_root, &mut files)?;
    }
    files.sort_by(|left, right| {
        right
            .modified_unix_seconds
            .cmp(&left.modified_unix_seconds)
            .then_with(|| left.relative_path.cmp(&right.relative_path))
    });
    files.truncate(limit);
    Ok(files)
}

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

fn collect_report_files(
    repo_root: &Path,
    scan_root: &Path,
    dir: &Path,
    files: &mut Vec<LocalFile>,
) -> Result<(), String> {
    if !dir.exists() {
        return Ok(());
    }
    let entries = fs::read_dir(dir).map_err(|err| format!("failed to read {dir:?}: {err}"))?;
    for entry in entries {
        let entry = entry.map_err(|err| format!("failed to read directory entry: {err}"))?;
        let path = entry.path();
        let metadata = entry
            .metadata()
            .map_err(|err| format!("failed to stat {path:?}: {err}"))?;
        if metadata.is_dir() {
            collect_report_files(repo_root, scan_root, &path, files)?;
            continue;
        }
        if !metadata.is_file() || !is_report_file(scan_root, &path) {
            continue;
        }
        let modified_unix_seconds = metadata
            .modified()
            .ok()
            .and_then(|time| time.duration_since(UNIX_EPOCH).ok())
            .map(|duration| duration.as_secs())
            .unwrap_or_default();
        let relative_path = path
            .strip_prefix(repo_root)
            .unwrap_or(path.as_path())
            .display()
            .to_string();
        files.push(LocalFile {
            path: path.display().to_string(),
            relative_path,
            size_bytes: metadata.len(),
            modified_unix_seconds,
        });
    }
    Ok(())
}

fn is_report_file(scan_root: &Path, path: &Path) -> bool {
    let relative = path.strip_prefix(scan_root).unwrap_or(path);
    if relative
        .components()
        .any(|component| component.as_os_str() == "artifacts")
    {
        return false;
    }
    if path
        .file_name()
        .and_then(|name| name.to_str())
        .map(|name| name.starts_with('.'))
        .unwrap_or(false)
    {
        return false;
    }
    path.extension()
        .and_then(|extension| extension.to_str())
        .map(|extension| {
            REPORT_EXTENSIONS
                .iter()
                .any(|allowed| extension.eq_ignore_ascii_case(allowed))
        })
        .unwrap_or(false)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::env;
    use std::path::PathBuf;
    use std::time::SystemTime;

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
