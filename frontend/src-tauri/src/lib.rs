use structure_local_core::{collect_snapshot, default_repo_root, read_repo_text_file};

#[tauri::command]
fn local_snapshot() -> Result<structure_local_core::LocalSnapshot, String> {
    let repo_root = default_repo_root()?;
    collect_snapshot(repo_root)
}

#[tauri::command]
fn read_local_report(path: String) -> Result<String, String> {
    let repo_root = default_repo_root()?;
    read_repo_text_file(repo_root, path, 200_000)
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![local_snapshot, read_local_report])
        .run(tauri::generate_context!())
        .expect("failed to run Structure desktop app");
}
