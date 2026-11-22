// Release builds must not open a console window next to the app.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod apply;
mod doctor;
mod integrity;
mod runner;
mod sync_preview;

use apply::{ApplyOutcome, PlanStore};
use doctor::DoctorOutcome;
use runner::{Provenance, RunGuard, Runtime, Transport, TransportCode};
use serde::Serialize;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Instant;
use sync_preview::{PreviewOptions, PreviewOutcome};
use tauri::Manager;

/// True while an apply process runs: the window refuses to close so the
/// result of a transaction is never silently lost.
static APPLYING: AtomicBool = AtomicBool::new(false);

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct CommandRun<T> {
    outcome: T,
    duration_ms: u64,
    provenance: Provenance,
    /// The user's home, only so the UI can shorten local paths to `~`.
    user_home: Option<String>,
    /// How to recover a pending transaction with the bundled interpreter
    /// (shown when Doctor or the core reports one; the app never runs it).
    recovery_command: Option<String>,
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
    let recovery_command = resource_dir
        .as_deref()
        .map(|dir| apply::recovery_command(&Runtime::from_resource_dir(dir)));
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
        recovery_command,
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
/// strictly deserialized struct; the argv is built from literals only. A
/// non-empty plan with no transaction in sight gets a single-use apply token.
#[tauri::command]
async fn preview_sync(
    app: tauri::AppHandle,
    store: tauri::State<'_, Arc<PlanStore>>,
    options: PreviewOptions,
) -> Result<CommandRun<PreviewOutcome>, ()> {
    let store = Arc::clone(store.inner());
    Ok(guarded(
        &app,
        move |runtime, env| apply::preview(&store, runtime, &options, env),
        PreviewOutcome::Transport,
    )
    .await)
}

/// Forget the reviewed plan (the user changed an option). Idempotent.
#[tauri::command]
fn discard_plan(store: tauri::State<'_, Arc<PlanStore>>) {
    store.invalidate();
}

/// Apply exactly the reviewed plan identified by `token` (single use). The
/// options, the full fingerprint and the expected batch come from the store,
/// never from the webview.
#[tauri::command]
async fn apply_sync(
    app: tauri::AppHandle,
    store: tauri::State<'_, Arc<PlanStore>>,
    token: String,
) -> Result<CommandRun<ApplyOutcome>, ()> {
    let store = Arc::clone(store.inner());
    Ok(guarded(
        &app,
        move |runtime, env| {
            // Taken under the run guard: a Busy guard never consumes the token.
            APPLYING.store(true, Ordering::SeqCst);
            let outcome = apply::apply(&store, runtime, &token, env);
            APPLYING.store(false, Ordering::SeqCst);
            outcome
        },
        ApplyOutcome::Transport,
    )
    .await)
}

fn main() {
    tauri::Builder::default()
        .manage(Arc::new(PlanStore::default()))
        .on_window_event(|_window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                if APPLYING.load(Ordering::SeqCst) {
                    api.prevent_close();
                }
            }
        })
        .invoke_handler(tauri::generate_handler![
            run_doctor,
            preview_sync,
            discard_plan,
            apply_sync
        ])
        .run(tauri::generate_context!())
        .expect("error while running SYNCODE Desktop");
}
