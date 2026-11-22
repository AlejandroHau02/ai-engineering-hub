// Release builds must not open a console window next to the app.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod doctor;

use doctor::{DoctorOutcome, Provenance, RunGuard, Runtime, TransportCode};
use serde::Serialize;
use std::time::Instant;
use tauri::Manager;

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct DoctorRun {
    outcome: DoctorOutcome,
    duration_ms: u64,
    provenance: Provenance,
    /// The user's home, only so the UI can shorten local paths to `~`.
    user_home: Option<String>,
}

/// The single command exposed to the webview. It takes no arguments: the
/// interpreter, script, flags and environment are fixed in `doctor`.
#[tauri::command]
async fn run_doctor(app: tauri::AppHandle) -> DoctorRun {
    let resource_dir = app.path().resource_dir().ok();
    let user_home = std::env::var("USERPROFILE").ok();
    let provenance_dir = resource_dir.clone();
    let started = Instant::now();
    let outcome = tauri::async_runtime::spawn_blocking(move || {
        let Some(_guard) = RunGuard::acquire() else {
            return DoctorOutcome::Transport {
                code: TransportCode::Busy,
                detail: "a diagnosis is already running".into(),
                exit_code: None,
            };
        };
        let Some(dir) = resource_dir else {
            return DoctorOutcome::Transport {
                code: TransportCode::BundleIncomplete,
                detail: "resource directory unavailable".into(),
                exit_code: None,
            };
        };
        let env = doctor::child_env(|name| std::env::var_os(name));
        doctor::run(
            &Runtime::from_resource_dir(&dir),
            &env,
            doctor::DOCTOR_TIMEOUT,
        )
    })
    .await
    .unwrap_or_else(|error| DoctorOutcome::Transport {
        code: TransportCode::SpawnFailed,
        detail: format!("worker failed: {error}"),
        exit_code: None,
    });
    DoctorRun {
        outcome,
        duration_ms: started.elapsed().as_millis() as u64,
        provenance: doctor::provenance(provenance_dir.as_deref()),
        user_home,
    }
}

fn main() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![run_doctor])
        .run(tauri::generate_context!())
        .expect("error while running SYNCODE Desktop");
}
