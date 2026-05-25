use std::env;
use std::path::{Path, PathBuf};

pub fn find_repo_root(start: impl AsRef<Path>) -> Result<PathBuf, String> {
    let mut current = start.as_ref().to_path_buf();
    if current.is_file() {
        current.pop();
    }

    loop {
        if current.join("pyproject.toml").exists()
            && current.join("frontend").exists()
            && current.join("src/structure").exists()
        {
            return current
                .canonicalize()
                .map_err(|err| format!("failed to canonicalize repo root: {err}"));
        }
        if !current.pop() {
            return Err("could not find Structure repository root".to_string());
        }
    }
}

pub fn default_repo_root() -> Result<PathBuf, String> {
    if let Ok(value) = env::var("STRUCTURE_REPO_ROOT") {
        return find_repo_root(value);
    }
    let current = env::current_dir().map_err(|err| format!("failed to read cwd: {err}"))?;
    find_repo_root(current)
}
