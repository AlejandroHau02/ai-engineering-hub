//! Recover an interrupted transaction with the core's own `--recover`.
//!
//! * Availability: a single-use token is issued only when a Doctor run,
//!   validated in Rust, reports `transaction: "pending-recovery"`. Any later
//!   Doctor, preview or apply invalidates it; it also expires.
//! * Execution: `sync.py --recover --format json` (literal argv) under the
//!   global run guard, after the bundle integrity check done by the runner.
//!   Rust never reads or edits the journal: rollback, validation and cleanup
//!   remain the core's (`transaction.recover`).
//! * Result: classified by `status`, then `transaction.outcome` and
//!   `recoveryRequired`; anything inconsistent is "unknown" (run Doctor).

use crate::apply::{new_token, same_token, Denied};
use crate::runner::{self, Exited, Runtime, Script, Transport};
use serde::Serialize;
use serde_json::{Map, Value};
use std::ffi::OsString;
use std::sync::Mutex;
use std::time::{Duration, Instant};

pub const RECOVER_TIMEOUT: Duration = Duration::from_secs(300);
pub const RECOVER_TTL: Duration = Duration::from_secs(10 * 60);
pub const RECOVER_ARGS: [&str; 3] = ["--recover", "--format", "json"];

#[derive(Debug)]
struct Pending {
    token: String,
    home: String,
    issued: Instant,
}

#[derive(Default)]
struct GateState {
    generation: u64,
    pending: Option<Pending>,
}

/// One slot, same discipline as the apply `PlanStore`.
#[derive(Default)]
pub struct RecoverGate {
    state: Mutex<GateState>,
}

impl RecoverGate {
    fn lock(&self) -> std::sync::MutexGuard<'_, GateState> {
        self.state.lock().unwrap_or_else(|p| p.into_inner())
    }

    pub fn invalidate(&self) -> u64 {
        let mut state = self.lock();
        state.generation += 1;
        state.pending = None;
        state.generation
    }

    /// Issue a token for the Doctor run started at `generation`.
    pub fn offer(&self, generation: u64, home: &str) -> Option<String> {
        let mut state = self.lock();
        if state.generation != generation {
            return None;
        }
        let token = new_token()?;
        state.pending = Some(Pending {
            token: token.clone(),
            home: home.to_string(),
            issued: Instant::now(),
        });
        Some(token)
    }

    /// Consume the token; returns the home Doctor analysed.
    pub fn take(&self, token: &str, ttl: Duration) -> Result<String, Denied> {
        let mut state = self.lock();
        state.generation += 1;
        let pending = state.pending.take().ok_or(Denied::NoPlan)?;
        if !same_token(&pending.token, token) {
            return Err(Denied::WrongToken);
        }
        if pending.issued.elapsed() > ttl {
            return Err(Denied::Expired);
        }
        Ok(pending.home)
    }
}

/// Token for a validated Doctor report that shows a pending recovery.
pub fn token_for_report(gate: &RecoverGate, generation: u64, report: &Value) -> Option<String> {
    if report.get("transaction").and_then(Value::as_str) != Some("pending-recovery") {
        return None;
    }
    let home = report.get("home").and_then(Value::as_str)?;
    gate.offer(generation, home)
}

#[derive(Serialize, Debug, PartialEq)]
#[serde(tag = "kind", rename_all = "camelCase")]
pub enum RecoverOutcome {
    /// The core finished the interrupted transaction. `outcome`:
    /// `rolled-back` (files restored), `committed` (it was complete; only
    /// cleaned up), `not-started` (a journal still in preparation was
    /// discarded) or `unknown` (an empty workspace was removed).
    Recovered { outcome: String },
    /// Nothing to recover (another process may have recovered meanwhile).
    NoTransaction,
    /// Another SYNCODE installation or recovery holds the lock.
    #[serde(rename_all = "camelCase")]
    Busy { message: String },
    /// The journal is still there: recovery was refused or cleanup failed.
    #[serde(rename_all = "camelCase")]
    StillPending {
        code: String,
        message: String,
        outcome: String,
    },
    /// The core reported an error and no transaction remains.
    #[serde(rename_all = "camelCase")]
    Failed {
        code: String,
        message: String,
        outcome: String,
    },
    /// The process ran but its result cannot be trusted: run Doctor.
    #[serde(rename_all = "camelCase")]
    Unknown { detail: String },
    /// No valid authorization: nothing was executed.
    #[serde(rename_all = "camelCase")]
    NotAuthorized { reason: Denied },
    /// Failed before any process started (integrity, bundle, spawn, busy).
    Transport(Transport),
}

pub fn recover(
    gate: &RecoverGate,
    runtime: &Runtime,
    token: &str,
    env: &[(String, OsString)],
) -> RecoverOutcome {
    let home = match gate.take(token, RECOVER_TTL) {
        Ok(home) => home,
        Err(reason) => return RecoverOutcome::NotAuthorized { reason },
    };
    match runner::execute(
        runtime,
        Script::Sync,
        &RECOVER_ARGS,
        None,
        env,
        RECOVER_TIMEOUT,
    ) {
        Ok(exited) => classify(&home, &exited),
        Err(failure) if failure.before_launch() => RecoverOutcome::Transport(failure),
        Err(failure) => RecoverOutcome::Unknown {
            detail: format!("{:?}: {}", failure.code, failure.detail),
        },
    }
}

fn str_of<'a>(object: &'a Map<String, Value>, key: &str) -> Option<&'a str> {
    object.get(key).and_then(Value::as_str)
}

/// Pure contract check of `sync.py --recover --format json`.
pub fn classify(home: &str, exited: &Exited) -> RecoverOutcome {
    let unknown = |why: &str| RecoverOutcome::Unknown {
        detail: why.to_string(),
    };
    let (code, payload) = match runner::parse_contract(exited, &[0, 1, 130]) {
        Ok(parsed) => parsed,
        Err(failure) => {
            let code = failure.code;
            let detail = exited.annotate(failure).detail;
            return unknown(&format!("{code:?}: {detail}"));
        }
    };
    if str_of(&payload, "home") != Some(home) {
        return unknown("the core analysed a different home");
    }
    let Some(transaction) = payload.get("transaction").and_then(Value::as_object) else {
        return unknown("transaction missing");
    };
    let outcome = str_of(transaction, "outcome")
        .unwrap_or_default()
        .to_string();
    let recovery_required = match transaction.get("recoveryRequired") {
        Some(Value::Bool(b)) => Some(*b),
        Some(Value::Null) => None,
        _ => return unknown("transaction.recoveryRequired is not boolean or null"),
    };
    // Recover never writes a new batch: a plan in the response is a bug.
    if payload
        .get("changes")
        .and_then(Value::as_array)
        .map_or(true, |c| !c.is_empty())
    {
        return unknown("recover reported planned changes");
    }
    let status = str_of(&payload, "status").unwrap_or_default();
    let error = payload.get("error").and_then(Value::as_object);
    let error_code = error.and_then(|e| str_of(e, "code")).unwrap_or_default();
    let message = error
        .and_then(|e| str_of(e, "message"))
        .unwrap_or_default()
        .to_string();

    if code == 0 {
        if error.is_some() || recovery_required != Some(false) {
            return unknown("successful recover with an error or a remaining workspace");
        }
        return match (status, outcome.as_str()) {
            ("recovered", "rolled-back" | "committed" | "not-started" | "unknown") => {
                RecoverOutcome::Recovered { outcome }
            }
            ("no-pending-transaction", "not-started") => RecoverOutcome::NoTransaction,
            _ => unknown("unexpected status or outcome for a successful recover"),
        };
    }
    if status != "error" || error_code.is_empty() {
        return unknown("error exit without status error and error.code");
    }
    if (code == 130) != (error_code == "interrupted") {
        return unknown("exit code inconsistent with error.code");
    }
    if error_code == "lock-busy" {
        return RecoverOutcome::Busy { message };
    }
    match recovery_required {
        Some(true) => RecoverOutcome::StillPending {
            code: error_code.to_string(),
            message,
            outcome,
        },
        Some(false) => RecoverOutcome::Failed {
            code: error_code.to_string(),
            message,
            outcome,
        },
        None => unknown("transaction state could not be determined"),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::runner::testing::*;
    use crate::runner::TransportCode;
    use serde_json::json;
    use std::path::Path;

    fn body(status: &str, code: Option<&str>, outcome: &str, recovery: Value) -> Value {
        json!({
            "schemaVersion": 1, "product": "SYNCODE", "status": status, "home": "C:\\Users\\x",
            "targets": ["claude", "codex"], "changes": [], "events": ["RECOVERED"],
            "transaction": {"outcome": outcome, "recoveryRequired": recovery},
            "plan": {"algorithm": "syncode-plan-v1", "fingerprint": null, "expected": null, "matched": null},
            "error": code.map(|c| json!({"type": "TransactionError", "code": c, "message": "m"}))
        })
    }

    fn exited(code: i32, value: &Value) -> Exited {
        Exited {
            code: Some(code),
            stdout: serde_json::to_vec(value).unwrap(),
            overflow: false,
            stderr_len: 0,
        }
    }

    fn kind(o: &RecoverOutcome) -> &'static str {
        match o {
            RecoverOutcome::Recovered { .. } => "recovered",
            RecoverOutcome::NoTransaction => "noTransaction",
            RecoverOutcome::Busy { .. } => "busy",
            RecoverOutcome::StillPending { .. } => "stillPending",
            RecoverOutcome::Failed { .. } => "failed",
            RecoverOutcome::Unknown { .. } => "unknown",
            RecoverOutcome::NotAuthorized { .. } => "notAuthorized",
            RecoverOutcome::Transport(_) => "transport",
        }
    }

    #[test]
    fn contract_table_classification() {
        let home = "C:\\Users\\x";
        let cases: Vec<(i32, Value, &str)> = vec![
            (
                0,
                body("recovered", None, "rolled-back", json!(false)),
                "recovered",
            ),
            (
                0,
                body("recovered", None, "committed", json!(false)),
                "recovered",
            ),
            (
                0,
                body("recovered", None, "not-started", json!(false)),
                "recovered",
            ),
            (
                0,
                body("recovered", None, "unknown", json!(false)),
                "recovered",
            ),
            (
                0,
                body("no-pending-transaction", None, "not-started", json!(false)),
                "noTransaction",
            ),
            (
                1,
                body("error", Some("lock-busy"), "not-started", json!(true)),
                "busy",
            ),
            (
                1,
                body(
                    "error",
                    Some("recovery-blocked"),
                    "recovery-pending",
                    json!(true),
                ),
                "stillPending",
            ),
            (
                1,
                body("error", Some("cleanup-failed"), "rolled-back", json!(true)),
                "stillPending",
            ),
            (
                1,
                body("error", Some("io-error"), "not-started", json!(false)),
                "failed",
            ),
            (
                1,
                body("error", Some("io-error"), "unknown", Value::Null),
                "unknown",
            ),
            (
                130,
                body(
                    "error",
                    Some("interrupted"),
                    "recovery-pending",
                    json!(true),
                ),
                "stillPending",
            ),
            (
                1,
                body(
                    "error",
                    Some("interrupted"),
                    "recovery-pending",
                    json!(true),
                ),
                "unknown",
            ),
            (
                0,
                body("recovered", None, "rolled-back", json!(true)),
                "unknown",
            ),
            (
                0,
                body("applied", None, "committed", json!(false)),
                "unknown",
            ),
            (
                0,
                body("recovered", None, "recovery-pending", json!(false)),
                "unknown",
            ),
            (
                2,
                body("recovered", None, "rolled-back", json!(false)),
                "unknown",
            ),
        ];
        for (code, value, expected) in cases {
            assert_eq!(
                kind(&classify(home, &exited(code, &value))),
                expected,
                "{code} {value}"
            );
        }
        let mut other_home = body("recovered", None, "rolled-back", json!(false));
        other_home["home"] = json!("C:\\Users\\other");
        assert_eq!(kind(&classify(home, &exited(0, &other_home))), "unknown");
        let mut with_changes = body("recovered", None, "rolled-back", json!(false));
        with_changes["changes"] = json!([{"kind": "CREATE", "path": ".claude/CLAUDE.md"}]);
        assert_eq!(kind(&classify(home, &exited(0, &with_changes))), "unknown");
    }

    #[test]
    fn gate_requires_a_pending_recovery_report_and_is_single_use() {
        let gate = RecoverGate::default();
        let clear = json!({"transaction": "clear", "home": "C:\\h"});
        let generation = gate.invalidate();
        assert!(token_for_report(&gate, generation, &clear).is_none());

        let pending = json!({"transaction": "pending-recovery", "home": "C:\\h"});
        let generation = gate.invalidate();
        let token = token_for_report(&gate, generation, &pending).unwrap();
        assert!(token.starts_with("plan-") && token.len() == 37);
        assert_eq!(gate.take(&token, RECOVER_TTL).unwrap(), "C:\\h");
        assert_eq!(gate.take(&token, RECOVER_TTL).unwrap_err(), Denied::NoPlan);

        // Any later command invalidates it; a stale Doctor cannot issue one.
        let generation = gate.invalidate();
        let token = token_for_report(&gate, generation, &pending).unwrap();
        gate.invalidate();
        assert_eq!(gate.take(&token, RECOVER_TTL).unwrap_err(), Denied::NoPlan);
        let generation = gate.invalidate();
        gate.invalidate();
        assert!(token_for_report(&gate, generation, &pending).is_none());

        // Wrong and expired tokens.
        let generation = gate.invalidate();
        let token = token_for_report(&gate, generation, &pending).unwrap();
        assert_eq!(
            gate.take("plan-0", RECOVER_TTL).unwrap_err(),
            Denied::WrongToken
        );
        assert_eq!(gate.take(&token, RECOVER_TTL).unwrap_err(), Denied::NoPlan);
        let generation = gate.invalidate();
        let token = token_for_report(&gate, generation, &pending).unwrap();
        assert_eq!(
            gate.take(&token, Duration::ZERO).unwrap_err(),
            Denied::Expired
        );
    }

    /// Interrupt a real apply abruptly after `writes` destination writes
    /// (test-only process; Desktop never does this).
    fn interrupted_apply(runtime: &Runtime, home: &Path, writes: usize) {
        let scripts = runtime.workdir.join("syncode").join("scripts");
        let code = format!(
            "import os, sys\nsys.path.insert(0, r'{}')\nimport sync\n\
             orig, n = sync.write_with_backup, [0]\n\
             def write(*a, **k):\n    orig(*a, **k); n[0] += 1\n    \
             if n[0] == {writes}: os._exit(3)\n\
             sync.write_with_backup = write\n\
             sync.main(['--target', 'both', '--apply', '--home', r'{}'])\n",
            scripts.display(),
            home.display()
        );
        let status = std::process::Command::new(&runtime.python)
            .args(["-I", "-B", "-c"])
            .arg(code)
            .stdout(std::process::Stdio::null())
            .status()
            .unwrap();
        assert_eq!(status.code(), Some(3), "apply was not interrupted");
        assert!(home.join(".ai-engineering-hub-transaction").exists());
    }

    fn doctor_token(gate: &RecoverGate, runtime: &Runtime, home: &Path) -> Option<String> {
        let generation = gate.invalidate();
        match crate::doctor::run(runtime, &env_with_home(home), crate::doctor::DOCTOR_TIMEOUT) {
            crate::doctor::DoctorOutcome::Report { report, .. } => {
                token_for_report(gate, generation, &report)
            }
            other => panic!("doctor failed: {other:?}"),
        }
    }

    #[test]
    fn interrupted_apply_is_recovered_to_the_previous_state() {
        let Some(runtime) = staged() else { return };
        let home = scratch("recover-interrupted");
        std::fs::create_dir_all(home.join(".claude")).unwrap();
        std::fs::write(home.join(".claude").join("CLAUDE.md"), "# Personal\n").unwrap();
        interrupted_apply(&runtime, &home, 2);
        let gate = RecoverGate::default();
        let token = doctor_token(&gate, &runtime, &home).expect("pending recovery offers a token");
        match recover(&gate, &runtime, &token, &env_with_home(&home)) {
            RecoverOutcome::Recovered { outcome } => assert_eq!(outcome, "rolled-back"),
            other => panic!("unexpected {other:?}"),
        }
        assert_eq!(
            std::fs::read_to_string(home.join(".claude").join("CLAUDE.md")).unwrap(),
            "# Personal\n"
        );
        assert!(!home.join(".ai-engineering-hub-transaction").exists());
        assert!(!home.join(".codex").exists() && !home.join(".agents").exists());
        // Doctor is clear afterwards: no token any more; replay is refused.
        assert!(doctor_token(&gate, &runtime, &home).is_none());
        assert_eq!(
            kind(&recover(&gate, &runtime, &token, &env_with_home(&home))),
            "notAuthorized"
        );
    }

    #[test]
    fn recovered_meanwhile_is_reported_as_no_transaction() {
        let Some(runtime) = staged() else { return };
        let home = scratch("recover-meanwhile");
        interrupted_apply(&runtime, &home, 1);
        let gate = RecoverGate::default();
        let token = doctor_token(&gate, &runtime, &home).unwrap();
        // Another tool recovers between Doctor and the user's confirmation.
        let status = std::process::Command::new(&runtime.python)
            .args(["-I", "-B"])
            .arg(
                runtime
                    .workdir
                    .join("syncode")
                    .join("scripts")
                    .join("sync.py"),
            )
            .args(["--recover", "--home"])
            .arg(&home)
            .stdout(std::process::Stdio::null())
            .status()
            .unwrap();
        assert!(status.success());
        assert_eq!(
            kind(&recover(&gate, &runtime, &token, &env_with_home(&home))),
            "noTransaction"
        );
    }

    #[test]
    fn tampered_bundle_blocks_recover_before_running() {
        let Some(runtime) = staged() else { return };
        let home = scratch("recover-tampered");
        interrupted_apply(&runtime, &home, 1);
        let copy = scratch("recover-tampered-bundle");
        for tree in ["python", "syncode"] {
            copy_tree(&runtime.workdir.join(tree), &copy.join(tree));
        }
        let tampered = Runtime::from_resource_dir(&copy);
        let gate = RecoverGate::default();
        let token = doctor_token(&gate, &tampered, &home).unwrap();
        let journal_before = snapshot(&home.join(".ai-engineering-hub-transaction"));
        let module = copy.join("syncode").join("scripts").join("transaction.py");
        let mut code = std::fs::read(&module).unwrap();
        code.extend_from_slice(b"\n# altered\n");
        std::fs::write(&module, code).unwrap();
        match recover(&gate, &tampered, &token, &env_with_home(&home)) {
            RecoverOutcome::Transport(t) => assert_eq!(t.code, TransportCode::IntegrityFailed),
            other => panic!("unexpected {other:?}"),
        }
        assert_eq!(
            snapshot(&home.join(".ai-engineering-hub-transaction")),
            journal_before,
            "the journal is untouched"
        );
    }
}
