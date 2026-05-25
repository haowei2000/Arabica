use crate::manifest::{structure_core_manifest, ProductSurface, StructureCoreManifest};
use crate::reports::{count_benchmark_reports, recent_benchmark_reports, LocalFile};
use serde::Serialize;
use std::env;
use std::path::{Path, PathBuf};

#[derive(Debug, Clone, Serialize)]
pub struct LocalSnapshot {
    pub repo_root: String,
    pub runtime_dir: String,
    pub frontend_dir: String,
    pub benchmark_report_count: usize,
    pub recent_benchmark_reports: Vec<LocalFile>,
    pub core_manifest: StructureCoreManifest,
    pub product_surfaces: Vec<ProductSurface>,
    pub local_only: bool,
}

pub fn collect_snapshot(repo_root: impl AsRef<Path>) -> Result<LocalSnapshot, String> {
    let repo_root = repo_root
        .as_ref()
        .canonicalize()
        .map_err(|err| format!("failed to canonicalize repo root: {err}"))?;
    let runtime_dir = env::var("STRUCTURE_LOCAL_RUNTIME_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|_| repo_root.join(".structure/local"));
    let frontend_dir = repo_root.join("frontend");
    let recent = recent_benchmark_reports(&repo_root, 8)?;
    let benchmark_report_count = count_benchmark_reports(&repo_root)?;
    let core_manifest = structure_core_manifest()?;
    let product_surfaces = core_manifest.surfaces.clone();

    Ok(LocalSnapshot {
        repo_root: repo_root.display().to_string(),
        runtime_dir: runtime_dir.display().to_string(),
        frontend_dir: frontend_dir.display().to_string(),
        benchmark_report_count,
        recent_benchmark_reports: recent,
        core_manifest,
        product_surfaces,
        local_only: true,
    })
}

pub fn snapshot_json(snapshot: &LocalSnapshot) -> Result<String, String> {
    serde_json::to_string_pretty(snapshot)
        .map_err(|err| format!("failed to serialize snapshot: {err}"))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::time::{SystemTime, UNIX_EPOCH};

    #[test]
    fn snapshot_collects_local_reports() {
        let root = unique_temp_dir("snapshot");
        fs::create_dir_all(root.join("src/structure")).unwrap();
        fs::create_dir_all(root.join("frontend")).unwrap();
        fs::create_dir_all(root.join("benchmark_runs")).unwrap();
        fs::create_dir_all(root.join("benchmark_runs/artifacts/chunk")).unwrap();
        fs::write(root.join("pyproject.toml"), "").unwrap();
        fs::write(root.join("benchmark_runs/run.json"), "{}").unwrap();
        fs::write(
            root.join("benchmark_runs/artifacts/chunk/.metadata.json"),
            "{}",
        )
        .unwrap();

        let snapshot = collect_snapshot(&root).unwrap();

        assert!(snapshot.local_only);
        assert_eq!(snapshot.benchmark_report_count, 1);
        assert_eq!(snapshot.product_surfaces.len(), 3);
        assert_eq!(
            snapshot.recent_benchmark_reports[0].relative_path,
            "benchmark_runs/run.json"
        );

        fs::remove_dir_all(root).unwrap();
    }

    fn unique_temp_dir(label: &str) -> std::path::PathBuf {
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let path = std::env::temp_dir().join(format!(
            "structure-local-core-{label}-{}-{nanos}",
            std::process::id()
        ));
        fs::create_dir_all(&path).unwrap();
        path
    }
}
