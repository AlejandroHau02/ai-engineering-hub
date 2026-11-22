fn main() {
    // Only the read-only commands are part of the app manifest; the
    // capability grants exactly their generated `allow-*` permissions.
    tauri_build::try_build(
        tauri_build::Attributes::new().app_manifest(
            tauri_build::AppManifest::new().commands(&["run_doctor", "preview_sync"]),
        ),
    )
    .expect("failed to run tauri-build");

    // Static imports of the executable resolve from System32 only, so a DLL
    // planted next to an unzipped portable copy is never loaded
    // (LOAD_LIBRARY_SEARCH_SYSTEM32 = 0x800).
    if std::env::var("CARGO_CFG_TARGET_ENV").as_deref() == Ok("msvc") {
        println!("cargo:rustc-link-arg-bins=/DEPENDENTLOADFLAG:0x800");
    }
}
