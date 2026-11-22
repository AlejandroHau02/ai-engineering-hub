fn main() {
    // Only `run_doctor` is part of the app manifest; the capability grants
    // exactly its generated `allow-run-doctor` permission.
    tauri_build::try_build(
        tauri_build::Attributes::new()
            .app_manifest(tauri_build::AppManifest::new().commands(&["run_doctor"])),
    )
    .expect("failed to run tauri-build");
}
