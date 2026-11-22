// Release builds must not open a console window next to the app.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod apply;
mod catalog;
mod doctor;
mod integrity;
mod recover;
mod runner;
mod sync_preview;

use apply::{ApplyOutcome, PlanStore};
use catalog::CatalogOutcome;
use doctor::DoctorOutcome;
use recover::{RecoverGate, RecoverOutcome};
use runner::{Provenance, RunGuard, Runtime, Transport, TransportCode};
use serde::Serialize;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Instant;
use sync_preview::{PreviewOptions, PreviewOutcome};
use tauri::Manager;

/// True while an apply or recover process runs: the window refuses to close
/// so the result of a transaction is never silently lost.
static WRITING: AtomicBool = AtomicBool::new(false);

/// Sets `WRITING` for its lifetime (cleared on drop, also on unwinding).
struct Writing;

impl Writing {
    fn start() -> Self {
        WRITING.store(true, Ordering::SeqCst);
        Writing
    }
}

impl Drop for Writing {
    fn drop(&mut self) {
        WRITING.store(false, Ordering::SeqCst);
    }
}

/// Single-use authorizations held in Rust; the webview only sees tokens.
#[derive(Default)]
struct Authorizations {
    plans: PlanStore,
    recovery: RecoverGate,
}

type State<'a> = tauri::State<'a, Arc<Authorizations>>;

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct CommandRun<T> {
    outcome: T,
    duration_ms: u64,
    provenance: Provenance,
    /// The user's home, only so the UI can shorten local paths to `~`.
    user_home: Option<String>,
    /// How to recover a pending transaction with the bundled interpreter
    /// (shown as a fallback; the app offers its own Recover action).
    recovery_command: Option<String>,
    /// Single-use token to recover, issued only by a Doctor report that
    /// shows a pending recovery.
    recover_token: Option<String>,
}

/// Run `work` on a blocking thread under the process-wide single-run guard.
async fn guarded<T, F>(
    app: &tauri::AppHandle,
    work: F,
    transport: fn(Transport) -> T,
) -> CommandRun<T>
where
    T: Send + 'static,
    F: FnOnce(&Runtime, &[(String, std::ffi::OsString)]) -> (T, Option<String>) + Send + 'static,
{
    let resource_dir: Option<PathBuf> = app.path().resource_dir().ok();
    let provenance = runner::provenance(resource_dir.as_deref());
    let recovery_command = resource_dir
        .as_deref()
        .map(|dir| apply::recovery_command(&Runtime::from_resource_dir(dir)));
    let started = Instant::now();
    let (outcome, recover_token) = tauri::async_runtime::spawn_blocking(move || {
        let Some(_guard) = RunGuard::acquire() else {
            return (
                transport(Transport::new(
                    TransportCode::Busy,
                    "another command is already running",
                    None,
                )),
                None,
            );
        };
        let Some(dir) = resource_dir else {
            return (
                transport(Transport::new(
                    TransportCode::BundleIncomplete,
                    "resource directory unavailable",
                    None,
                )),
                None,
            );
        };
        let env = runner::child_env(|name| std::env::var_os(name));
        work(&Runtime::from_resource_dir(&dir), &env)
    })
    .await
    .unwrap_or_else(|error| {
        (
            transport(Transport::new(
                TransportCode::SpawnFailed,
                format!("worker failed: {error}"),
                None,
            )),
            None,
        )
    });
    CommandRun {
        outcome,
        duration_ms: started.elapsed().as_millis() as u64,
        provenance,
        user_home: std::env::var("USERPROFILE").ok(),
        recovery_command,
        recover_token,
    }
}

/// Read-only diagnosis. No arguments: script, flags and environment are fixed.
/// A report showing a pending recovery issues a single-use recover token and
/// withdraws any apply authorization.
#[tauri::command]
async fn run_doctor(
    app: tauri::AppHandle,
    auth: State<'_>,
) -> Result<CommandRun<DoctorOutcome>, ()> {
    let auth = Arc::clone(auth.inner());
    Ok(guarded(
        &app,
        move |runtime, env| {
            let generation = auth.recovery.invalidate();
            let outcome = doctor::run(runtime, env, doctor::DOCTOR_TIMEOUT);
            let token = match &outcome {
                DoctorOutcome::Report { report, .. } => {
                    recover::token_for_report(&auth.recovery, generation, report)
                }
                _ => None,
            };
            if token.is_some() {
                auth.plans.invalidate();
            }
            (outcome, token)
        },
        DoctorOutcome::Transport,
    )
    .await)
}

/// Read-only sync preview from the real planner. `options` is a closed,
/// strictly deserialized struct; the argv is built from literals only. A
/// non-empty plan with no transaction in sight gets a single-use apply token.
#[tauri::command]
async fn preview_sync(
    app: tauri::AppHandle,
    auth: State<'_>,
    options: PreviewOptions,
) -> Result<CommandRun<PreviewOutcome>, ()> {
    let auth = Arc::clone(auth.inner());
    Ok(guarded(
        &app,
        move |runtime, env| {
            auth.recovery.invalidate();
            (apply::preview(&auth.plans, runtime, &options, env), None)
        },
        PreviewOutcome::Transport,
    )
    .await)
}

/// Forget the reviewed plan (the user changed an option). Idempotent.
#[tauri::command]
fn discard_plan(auth: State<'_>) {
    auth.plans.invalidate();
}

/// Apply exactly the reviewed plan identified by `token` (single use). The
/// options, the full fingerprint and the expected batch come from the store,
/// never from the webview.
#[tauri::command]
async fn apply_sync(
    app: tauri::AppHandle,
    auth: State<'_>,
    token: String,
) -> Result<CommandRun<ApplyOutcome>, ()> {
    let auth = Arc::clone(auth.inner());
    let after = Arc::clone(&auth);
    let run = guarded(
        &app,
        move |runtime, env| {
            // Consumed under the run guard.
            let _writing = Writing::start();
            auth.recovery.invalidate();
            (apply::apply(&auth.plans, runtime, &token, env), None)
        },
        ApplyOutcome::Transport,
    )
    .await;
    if let ApplyOutcome::Transport(t) = &run.outcome {
        if t.code == TransportCode::Busy {
            after.plans.invalidate(); // any apply attempt ends the authorization
        }
    }
    Ok(run)
}

/// Recover the interrupted transaction Doctor reported, with the core's
/// `--recover` (single-use token; literal argv; integrity-checked bundle).
#[tauri::command]
async fn recover_transaction(
    app: tauri::AppHandle,
    auth: State<'_>,
    token: String,
) -> Result<CommandRun<RecoverOutcome>, ()> {
    let auth = Arc::clone(auth.inner());
    let after = Arc::clone(&auth);
    let run = guarded(
        &app,
        move |runtime, env| {
            let _writing = Writing::start();
            auth.plans.invalidate();
            (recover::recover(&auth.recovery, runtime, &token, env), None)
        },
        RecoverOutcome::Transport,
    )
    .await;
    if let RecoverOutcome::Transport(t) = &run.outcome {
        if t.code == TransportCode::Busy {
            after.recovery.invalidate(); // any attempt ends the authorization
        }
    }
    Ok(run)
}

/// Read-only catalog of SYNCODE components and other installed entries.
#[tauri::command]
async fn read_catalog(app: tauri::AppHandle) -> CommandRun<CatalogOutcome> {
    // Read-only and unrelated to transactions: authorizations are untouched.
    guarded(
        &app,
        |runtime, env| (catalog::run(runtime, env), None),
        CatalogOutcome::Transport,
    )
    .await
}

fn main() {
    tauri::Builder::default()
        .manage(Arc::new(Authorizations::default()))
        .on_window_event(|_window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                if WRITING.load(Ordering::SeqCst) {
                    api.prevent_close();
                }
            }
        })
        .invoke_handler(tauri::generate_handler![
            run_doctor,
            preview_sync,
            discard_plan,
            apply_sync,
            recover_transaction,
            read_catalog
        ])
        .run(tauri::generate_context!())
        .expect("error while running SYNCODE Desktop");
}
