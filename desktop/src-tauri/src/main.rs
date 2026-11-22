// Release builds must not open a console window next to the app.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod doctor;
mod runner;
mod sync_preview;

use doctor::DoctorOutcome;
use runner::{Provenance, RunGuard, Runtime, Transport, TransportCode};
use serde::Serialize;
use std::path::PathBuf;
use std::time::Instant;
use sync_preview::{PreviewOptions, PreviewOutcome};
use tauri::Manager;

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct CommandRun<T> {
    outcome: T,
    duration_ms: u64,
    provenance: Provenance,
    /// The user's home, only so the UI can shorten local paths to `~`.
    user_home: Option<String>,
}

/// Run `work` on a blocking thread under the process-wide single-run guard.
async fn guarded<T, F>(
    app: &tauri::AppHandle,
    work: F,
    transport: fn(Transport) -> T,
) -> CommandRun<T>
where
    T: Send + 'static,
    F: FnOnce(&Runtime, &[(String, std::ffi::OsString)]) -> T + Send + 'static,
{
    let resource_dir: Option<PathBuf> = app.path().resource_dir().ok();
    let provenance = runner::provenance(resource_dir.as_deref());
    let started = Instant::now();
    let outcome = tauri::async_runtime::spawn_blocking(move || {
        let Some(_guard) = RunGuard::acquire() else {
            return transport(Transport::new(
                TransportCode::Busy,
                "another command is already running",
                None,
            ));
        };
        let Some(dir) = resource_dir else {
            return transport(Transport::new(
                TransportCode::BundleIncomplete,
                "resource directory unavailable",
                None,
            ));
        };
        let env = runner::child_env(|name| std::env::var_os(name));
        work(&Runtime::from_resource_dir(&dir), &env)
    })
    .await
    .unwrap_or_else(|error| {
        transport(Transport::new(
            TransportCode::SpawnFailed,
            format!("worker failed: {error}"),
            None,
        ))
    });
    CommandRun {
        outcome,
        duration_ms: started.elapsed().as_millis() as u64,
        provenance,
        user_home: std::env::var("USERPROFILE").ok(),
    }
}

/// Read-only diagnosis. No arguments: script, flags and environment are fixed.
#[tauri::command]
async fn run_doctor(app: tauri::AppHandle) -> CommandRun<DoctorOutcome> {
    guarded(
        &app,
        |runtime, env| doctor::run(runtime, env, doctor::DOCTOR_TIMEOUT),
        DoctorOutcome::Transport,
    )
    .await
}

/// Read-only sync preview from the real planner. `options` is a closed,
/// strictly deserialized struct; the argv is built from literals only.
#[tauri::command]
async fn preview_sync(
    app: tauri::AppHandle,
    options: PreviewOptions,
) -> CommandRun<PreviewOutcome> {
    guarded(
        &app,
        move |runtime, env| {
            sync_preview::run(runtime, &options, env, sync_preview::PREVIEW_TIMEOUT)
        },
        PreviewOutcome::Transport,
    )
    .await
}

fn main() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![run_doctor, preview_sync])
        .run(tauri::generate_context!())
        .expect("error while running SYNCODE Desktop");
}
