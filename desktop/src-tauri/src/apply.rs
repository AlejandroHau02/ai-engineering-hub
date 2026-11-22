//! Bound apply: execute exactly the plan the user reviewed.
//!
//! * After a valid preview the [`PlanStore`] keeps the full fingerprint, the
//!   options, the analysed home and the planned operations; the webview only
//!   receives an opaque, single-use token.
//! * The token is invalidated by a new preview, by `discard_plan` (options
//!   changed), after a TTL, by a wrong token and by any apply attempt.
//! * Apply runs `sync.py --apply --expect-plan <fingerprint>` with the stored
//!   options; the core rebuilds the plan under its exclusive lock and refuses
//!   with `stale-plan` if anything changed.
//! * The response is classified by `transaction.outcome` first (contract
//!   table), then by `error.code`. Success requires the committed batch to be
//!   exactly the reviewed one, and is then re-checked with a fresh preview.

use crate::runner::{self, Exited, Fingerprint, Runtime, Script, Transport};
use crate::sync_preview::{self, PlannedChange, PreviewOptions, PreviewOutcome};
use serde::Serialize;
use serde_json::{Map, Value};
use std::ffi::OsString;
use std::sync::Mutex;
use std::time::{Duration, Instant};

pub const APPLY_TIMEOUT: Duration = Duration::from_secs(300);
pub const PLAN_TTL: Duration = Duration::from_secs(10 * 60);

/// A reviewed plan that may be applied once.
#[derive(Debug, Clone)]
pub struct Pending {
    token: String,
    fingerprint: Fingerprint,
    options: PreviewOptions,
    home: String,
    changes: Vec<(String, String)>,
    issued: Instant,
}

#[derive(Default)]
struct StoreState {
    generation: u64,
    pending: Option<Pending>,
}

/// One slot; every change of state bumps the generation, so a preview that
/// finishes after the options changed can never store its plan.
#[derive(Default)]
pub struct PlanStore {
    state: Mutex<StoreState>,
}

#[derive(Serialize, Debug, Clone, Copy, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub enum Denied {
    /// No reviewed plan, or it was already used, replaced or discarded.
    NoPlan,
    /// The token does not match the current plan (the plan is discarded).
    WrongToken,
    /// The reviewed plan is older than the TTL.
    Expired,
}

impl PlanStore {
    fn lock(&self) -> std::sync::MutexGuard<'_, StoreState> {
        self.state
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
    }

    /// Drop any pending plan; returns the new generation.
    pub fn invalidate(&self) -> u64 {
        let mut state = self.lock();
        state.generation += 1;
        state.pending = None;
        state.generation
    }

    /// Keep an applicable plan issued by the preview started at `generation`.
    fn offer(
        &self,
        generation: u64,
        fingerprint: Fingerprint,
        options: PreviewOptions,
        home: &str,
        changes: &[PlannedChange],
    ) -> Option<String> {
        let mut state = self.lock();
        if state.generation != generation {
            return None; // options changed or another preview started meanwhile
        }
        let token = format!("plan-{generation}");
        state.pending = Some(Pending {
            token: token.clone(),
            fingerprint,
            options,
            home: home.to_string(),
            changes: changes
                .iter()
                .map(|c| (c.kind.clone(), c.path.clone()))
                .collect(),
            issued: Instant::now(),
        });
        Some(token)
    }

    /// Consume the pending plan. Always leaves the store empty.
    pub fn take(&self, token: &str, ttl: Duration) -> Result<Pending, Denied> {
        let mut state = self.lock();
        state.generation += 1;
        let pending = state.pending.take().ok_or(Denied::NoPlan)?;
        if pending.token != token {
            return Err(Denied::WrongToken);
        }
        if pending.issued.elapsed() > ttl {
            return Err(Denied::Expired);
        }
        Ok(pending)
    }
}

/// Preview and, when the plan can be applied, issue a single-use token.
pub fn preview(
    store: &PlanStore,
    runtime: &Runtime,
    options: &PreviewOptions,
    env: &[(String, OsString)],
) -> PreviewOutcome {
    let generation = store.invalidate();
    let mut outcome = sync_preview::run(runtime, options, env, sync_preview::PREVIEW_TIMEOUT);
    if let PreviewOutcome::Plan {
        status,
        home,
        changes,
        fingerprint,
        apply_token,
        recovery_required,
        options,
        ..
    } = &mut outcome
    {
        // Only a non-empty plan with no transaction in sight is applicable.
        if status.as_str() == "planned" && *recovery_required == Some(false) && !changes.is_empty()
        {
            *apply_token = store.offer(generation, fingerprint.clone(), *options, home, changes);
        }
    }
    outcome
}

/// Literal part of the apply argv; the fingerprint is appended by the runner.
pub fn argv(options: &PreviewOptions) -> Vec<&'static str> {
    let target = match options.target {
        sync_preview::Target::Both => "both",
        sync_preview::Target::Claude => "claude",
        sync_preview::Target::Codex => "codex",
    };
    let mut args = vec!["--apply", "--target", target, "--format", "json"];
    if options.specialists {
        args.push("--specialists");
    }
    if options.upgrade_skills {
        args.push("--upgrade-skills");
    }
    args
}

#[derive(Serialize, Debug, PartialEq)]
#[serde(tag = "kind", rename_all = "camelCase")]
pub enum ApplyOutcome {
    /// The reviewed batch was committed. `verified` is true only if a fresh
    /// preview with the same options finds nothing left to do.
    #[serde(rename_all = "camelCase")]
    Applied {
        options: PreviewOptions,
        changes: Vec<PlannedChange>,
        verified: bool,
        verification: String,
    },
    /// Committed, but the core reported an error afterwards (e.g. cleanup).
    /// Never re-apply.
    #[serde(rename_all = "camelCase")]
    AppliedWithError {
        code: String,
        message: String,
        cleanup_pending: bool,
        recovery_command: String,
    },
    /// Something changed since the preview: nothing was applied.
    Stale,
    /// The core refused or rolled back; destinations are as before.
    #[serde(rename_all = "camelCase")]
    Rejected {
        code: String,
        message: String,
        outcome: String,
    },
    /// A transaction journal must be recovered before anything else.
    #[serde(rename_all = "camelCase")]
    RecoveryRequired {
        code: String,
        message: String,
        outcome: String,
        recovery_command: String,
    },
    /// Another SYNCODE installation or recovery holds the lock.
    #[serde(rename_all = "camelCase")]
    Busy { message: String },
    /// The process ran but its result cannot be trusted: run Doctor.
    #[serde(rename_all = "camelCase")]
    Unknown {
        detail: String,
        recovery_command: String,
    },
    /// No valid authorization: nothing was executed.
    #[serde(rename_all = "camelCase")]
    NotAuthorized { reason: Denied },
    /// Failed before any process started (integrity, bundle, spawn, busy).
    Transport(Transport),
}

/// Command the user can run from the app folder to recover a transaction.
pub fn recovery_command(runtime: &Runtime) -> String {
    let script = runtime
        .python
        .parent()
        .and_then(|p| p.parent())
        .map(|root| root.join("syncode").join("scripts").join("sync.py"))
        .unwrap_or_default();
    // Single-quoted PowerShell literals: `$` and backticks in the install
    // path are not expanded; embedded single quotes are doubled.
    let quote = |p: &std::path::Path| format!("'{}'", p.display().to_string().replace('\'', "''"));
    format!(
        "& {} -I -B {} --recover",
        quote(&runtime.python),
        quote(&script)
    )
}

pub fn apply(
    store: &PlanStore,
    runtime: &Runtime,
    token: &str,
    env: &[(String, OsString)],
) -> ApplyOutcome {
    let pending = match store.take(token, PLAN_TTL) {
        Ok(pending) => pending,
        Err(reason) => return ApplyOutcome::NotAuthorized { reason },
    };
    let exited = match runner::execute(
        runtime,
        Script::Sync,
        &argv(&pending.options),
        Some(&pending.fingerprint),
        env,
        APPLY_TIMEOUT,
    ) {
        Ok(exited) => exited,
        Err(failure) if failure.before_launch() => return ApplyOutcome::Transport(failure),
        Err(failure) => {
            return ApplyOutcome::Unknown {
                detail: format!("{:?}: {}", failure.code, failure.detail),
                recovery_command: recovery_command(runtime),
            }
        }
    };
    match classify(&pending, &exited, &recovery_command(runtime)) {
        ApplyOutcome::Applied {
            options, changes, ..
        } => {
            let (verified, verification) = verify_after(runtime, &pending.options, env);
            ApplyOutcome::Applied {
                options,
                changes,
                verified,
                verification,
            }
        }
        other => other,
    }
}

/// Fresh preview with the same options: success means nothing is pending.
fn verify_after(
    runtime: &Runtime,
    options: &PreviewOptions,
    env: &[(String, OsString)],
) -> (bool, String) {
    match sync_preview::run(runtime, options, env, sync_preview::PREVIEW_TIMEOUT) {
        PreviewOutcome::Plan {
            status, changes, ..
        } if status == "unchanged" && changes.is_empty() => {
            (true, "a new preview finds no pending changes".into())
        }
        PreviewOutcome::Plan { changes, .. } => (
            false,
            format!("a new preview still finds {} change(s)", changes.len()),
        ),
        PreviewOutcome::PlannerRefusal { code, .. } => {
            (false, format!("the verification preview stopped: {code}"))
        }
        PreviewOutcome::Transport(t) => (false, format!("verification failed: {:?}", t.code)),
    }
}

fn str_of<'a>(object: &'a Map<String, Value>, key: &str) -> Option<&'a str> {
    object.get(key).and_then(Value::as_str)
}

/// Pure contract check of `sync.py --apply --expect-plan ... --format json`.
pub fn classify(pending: &Pending, exited: &Exited, recovery: &str) -> ApplyOutcome {
    let unknown = |why: &str| ApplyOutcome::Unknown {
        detail: why.to_string(),
        recovery_command: recovery.to_string(),
    };
    let (code, payload) = match runner::parse_contract(exited, &[0, 1, 130]) {
        Ok(parsed) => parsed,
        Err(failure) => {
            let code = failure.code;
            let detail = exited.annotate(failure).detail;
            return unknown(&format!("{code:?}: {detail}"));
        }
    };
    let Some(transaction) = payload.get("transaction").and_then(Value::as_object) else {
        return unknown("transaction missing");
    };
    let outcome = str_of(transaction, "outcome").unwrap_or_default();
    let recovery_required = match transaction.get("recoveryRequired") {
        Some(Value::Bool(b)) => Some(*b),
        Some(Value::Null) => None,
        _ => return unknown("transaction.recoveryRequired is not boolean or null"),
    };
    let Some(plan) = payload.get("plan").and_then(Value::as_object) else {
        return unknown("plan missing");
    };
    if str_of(plan, "algorithm") != Some("syncode-plan-v1")
        || str_of(plan, "expected") != Some(pending.fingerprint.as_str())
    {
        return unknown("the core did not bind the reviewed plan");
    }
    if str_of(&payload, "home") != Some(pending.home.as_str()) {
        return unknown("the core analysed a different home");
    }
    let status = str_of(&payload, "status").unwrap_or_default();
    let error = payload.get("error").and_then(Value::as_object);
    let error_code = error.and_then(|e| str_of(e, "code")).unwrap_or_default();
    let message = error
        .and_then(|e| str_of(e, "message"))
        .unwrap_or_default()
        .to_string();

    if code == 0 {
        if status != "applied" || error.is_some() {
            return unknown("exit 0 without status applied");
        }
        if outcome != "committed" || recovery_required != Some(false) {
            return unknown("applied without a clean committed transaction");
        }
        if plan.get("matched") != Some(&Value::Bool(true))
            || str_of(plan, "fingerprint") != Some(pending.fingerprint.as_str())
        {
            return unknown("applied plan does not match the reviewed fingerprint");
        }
        let Some(raw) = payload.get("changes").and_then(Value::as_array) else {
            return unknown("changes missing");
        };
        let mut applied = Vec::with_capacity(raw.len());
        for item in raw {
            match project(&pending.options, item) {
                Some(change) => applied.push(change),
                None => return unknown("an applied change is not a managed path"),
            }
        }
        let mut got: Vec<(String, String)> = applied
            .iter()
            .map(|c| (c.kind.clone(), c.path.clone()))
            .collect();
        let mut want = pending.changes.clone();
        got.sort();
        want.sort();
        if got != want {
            return unknown("the applied batch differs from the reviewed one");
        }
        return ApplyOutcome::Applied {
            options: pending.options,
            changes: applied,
            verified: false,
            verification: String::new(),
        };
    }

    if status != "error" || error_code.is_empty() {
        return unknown("error exit without status error and error.code");
    }
    if (code == 130) != (error_code == "interrupted") {
        return unknown("exit code inconsistent with error.code");
    }
    if error_code == "lock-busy" {
        // Another transaction is active: never suggest recovering concurrently.
        return if outcome == "not-started" {
            ApplyOutcome::Busy { message }
        } else {
            unknown("lock-busy with an unexpected transaction outcome")
        };
    }
    if error_code == "stale-plan" && outcome == "not-started" {
        // The early check runs outside the lock; a workspace seen afterwards
        // may belong to a concurrent apply, so this is never "recover".
        return ApplyOutcome::Stale;
    }
    let recovery_command = recovery.to_string();
    match (outcome, recovery_required) {
        ("committed", Some(cleanup_pending)) => ApplyOutcome::AppliedWithError {
            code: error_code.to_string(),
            message,
            cleanup_pending,
            recovery_command,
        },
        ("recovery-pending", _) | ("rolled-back", Some(true)) | ("not-started", Some(true)) => {
            ApplyOutcome::RecoveryRequired {
                code: error_code.to_string(),
                message,
                outcome: outcome.to_string(),
                recovery_command,
            }
        }
        ("not-started" | "rolled-back", Some(false)) => ApplyOutcome::Rejected {
            code: error_code.to_string(),
            message,
            outcome: outcome.to_string(),
        },
        _ => unknown("transaction state could not be determined"),
    }
}

fn project(options: &PreviewOptions, raw: &Value) -> Option<PlannedChange> {
    // Same validation as the preview projection.
    let wrapped = serde_json::json!({
        "schemaVersion": 1, "product": "SYNCODE", "status": "planned", "home": "",
        "targets": target_list(options), "changes": [raw], "events": [],
        "transaction": {"outcome": "not-started", "recoveryRequired": false},
        "plan": {"algorithm": "syncode-plan-v1",
                 "fingerprint": format!("{}{}", Fingerprint::PREFIX, "0".repeat(64)),
                 "expected": null, "matched": null},
        "error": null
    });
    let exited = Exited {
        code: Some(0),
        stdout: serde_json::to_vec(&wrapped).ok()?,
        overflow: false,
        stderr_len: 0,
    };
    match sync_preview::classify(options, &exited) {
        PreviewOutcome::Plan { mut changes, .. } => changes.pop(),
        _ => None,
    }
}

fn target_list(options: &PreviewOptions) -> Vec<&'static str> {
    match options.target {
        sync_preview::Target::Both => vec!["claude", "codex"],
        sync_preview::Target::Claude => vec!["claude"],
        sync_preview::Target::Codex => vec!["codex"],
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::runner::testing::*;
    use crate::runner::TransportCode;
    use crate::sync_preview::Target;
    use serde_json::json;
    use std::path::Path;

    const ALL: [Target; 3] = [Target::Both, Target::Claude, Target::Codex];

    fn opts(target: Target, specialists: bool, upgrade_skills: bool) -> PreviewOptions {
        PreviewOptions {
            target,
            specialists,
            upgrade_skills,
        }
    }

    fn fp(c: char) -> Fingerprint {
        Fingerprint::parse(&format!(
            "{}{}",
            Fingerprint::PREFIX,
            c.to_string().repeat(64)
        ))
        .unwrap()
    }

    fn change(kind: &str, path: &str) -> PlannedChange {
        PlannedChange {
            kind: kind.into(),
            path: path.into(),
            target: Some("claude".into()),
            component_type: "instructions".into(),
            component_name: None,
        }
    }

    #[test]
    fn apply_argv_is_exact_and_never_previews_or_escapes() {
        for target in ALL {
            for specialists in [false, true] {
                for upgrade in [false, true] {
                    let o = opts(target, specialists, upgrade);
                    let args = argv(&o);
                    let target_arg = match target {
                        Target::Both => "both",
                        Target::Claude => "claude",
                        Target::Codex => "codex",
                    };
                    let mut expected = vec!["--apply", "--target", target_arg, "--format", "json"];
                    if specialists {
                        expected.push("--specialists");
                    }
                    if upgrade {
                        expected.push("--upgrade-skills");
                    }
                    assert_eq!(args, expected);
                    for banned in [
                        "--home",
                        "--approved-skills",
                        "--preview",
                        "--recover",
                        "--expect-plan",
                    ] {
                        assert!(!args.contains(&banned), "{banned}");
                    }
                }
            }
        }
    }

    #[test]
    fn store_is_single_use_and_invalidated() {
        let store = PlanStore::default();
        let changes = vec![change("CREATE", ".claude/CLAUDE.md")];
        let o = opts(Target::Both, false, false);

        // Issued plan can be taken once.
        let generation = store.invalidate();
        let token = store
            .offer(generation, fp('a'), o, "C:\\h", &changes)
            .unwrap();
        assert_eq!(store.take(&token, PLAN_TTL).unwrap().fingerprint, fp('a'));
        assert_eq!(store.take(&token, PLAN_TTL).unwrap_err(), Denied::NoPlan);

        // Options changed (discard) before apply.
        let generation = store.invalidate();
        let token = store
            .offer(generation, fp('b'), o, "C:\\h", &changes)
            .unwrap();
        store.invalidate();
        assert_eq!(store.take(&token, PLAN_TTL).unwrap_err(), Denied::NoPlan);

        // A preview that finishes after the options changed is not stored.
        let generation = store.invalidate();
        store.invalidate();
        assert!(store
            .offer(generation, fp('c'), o, "C:\\h", &changes)
            .is_none());

        // A wrong token discards the plan; the right one no longer works.
        let generation = store.invalidate();
        let token = store
            .offer(generation, fp('d'), o, "C:\\h", &changes)
            .unwrap();
        assert_eq!(
            store.take("plan-999", PLAN_TTL).unwrap_err(),
            Denied::WrongToken
        );
        assert_eq!(store.take(&token, PLAN_TTL).unwrap_err(), Denied::NoPlan);

        // Expired.
        let generation = store.invalidate();
        let token = store
            .offer(generation, fp('e'), o, "C:\\h", &changes)
            .unwrap();
        assert_eq!(
            store.take(&token, Duration::ZERO).unwrap_err(),
            Denied::Expired
        );

        // A new preview replaces the previous token.
        let generation = store.invalidate();
        let first = store
            .offer(generation, fp('f'), o, "C:\\h", &changes)
            .unwrap();
        let generation = store.invalidate();
        let second = store
            .offer(generation, fp('0'), o, "C:\\h", &changes)
            .unwrap();
        assert_ne!(first, second);
        assert_eq!(store.take(&second, PLAN_TTL).unwrap().fingerprint, fp('0'));
    }

    fn pending() -> Pending {
        Pending {
            token: "plan-1".into(),
            fingerprint: fp('a'),
            options: opts(Target::Both, false, false),
            home: "C:\\Users\\x".into(),
            changes: vec![
                ("UPDATE".into(), ".claude/CLAUDE.md".into()),
                ("CREATE".into(), ".agents/skills/ux-ui/SKILL.md".into()),
            ],
            issued: Instant::now(),
        }
    }

    fn body(status: &str, code: Option<&str>, outcome: &str, recovery: Value) -> Value {
        let fp = pending().fingerprint.as_str().to_string();
        json!({
            "schemaVersion": 1, "product": "SYNCODE", "status": status, "home": "C:\\Users\\x",
            "targets": ["claude", "codex"],
            "changes": [
                {"kind": "CREATE", "path": ".agents/skills/ux-ui/SKILL.md", "sha256": "a".repeat(64),
                 "target": "codex", "component": {"type": "skill", "name": "ux-ui"}},
                {"kind": "UPDATE", "path": ".claude/CLAUDE.md", "sha256": "b".repeat(64),
                 "target": "claude", "component": {"type": "instructions", "name": null}}
            ],
            "events": [],
            "transaction": {"outcome": outcome, "recoveryRequired": recovery},
            "plan": {"algorithm": "syncode-plan-v1", "fingerprint": fp, "expected": fp,
                     "matched": status == "applied"},
            "error": code.map(|c| json!({"type": "SyncodeError", "code": c, "message": "m"}))
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

    fn kind(outcome: &ApplyOutcome) -> &'static str {
        match outcome {
            ApplyOutcome::Applied { .. } => "applied",
            ApplyOutcome::AppliedWithError { .. } => "appliedWithError",
            ApplyOutcome::Stale => "stale",
            ApplyOutcome::Rejected { .. } => "rejected",
            ApplyOutcome::RecoveryRequired { .. } => "recoveryRequired",
            ApplyOutcome::Busy { .. } => "busy",
            ApplyOutcome::Unknown { .. } => "unknown",
            ApplyOutcome::NotAuthorized { .. } => "notAuthorized",
            ApplyOutcome::Transport(_) => "transport",
        }
    }

    #[test]
    fn contract_table_classification() {
        let p = pending();
        let cases: Vec<(i32, Value, &str)> = vec![
            (
                0,
                body("applied", None, "committed", json!(false)),
                "applied",
            ),
            (
                1,
                body("error", Some("stale-plan"), "not-started", json!(false)),
                "stale",
            ),
            (
                1,
                body("error", Some("stale-plan"), "not-started", json!(true)),
                "stale",
            ),
            (
                1,
                body(
                    "error",
                    Some("destination-changed"),
                    "not-started",
                    json!(false),
                ),
                "rejected",
            ),
            (
                1,
                body("error", Some("io-error"), "rolled-back", json!(false)),
                "rejected",
            ),
            (
                1,
                body("error", Some("cleanup-failed"), "rolled-back", json!(true)),
                "recoveryRequired",
            ),
            (
                1,
                body(
                    "error",
                    Some("recovery-required"),
                    "recovery-pending",
                    json!(true),
                ),
                "recoveryRequired",
            ),
            (
                1,
                body(
                    "error",
                    Some("transaction-pending"),
                    "not-started",
                    json!(true),
                ),
                "recoveryRequired",
            ),
            (
                1,
                body("error", Some("cleanup-failed"), "committed", json!(true)),
                "appliedWithError",
            ),
            (
                1,
                body("error", Some("io-error"), "committed", json!(false)),
                "appliedWithError",
            ),
            (
                1,
                body("error", Some("lock-busy"), "not-started", json!(true)),
                "busy",
            ),
            (
                1,
                body("error", Some("lock-busy"), "not-started", json!(false)),
                "busy",
            ),
            (
                1,
                body("error", Some("io-error"), "unknown", json!(true)),
                "unknown",
            ),
            (
                1,
                body("error", Some("io-error"), "rolled-back", Value::Null),
                "unknown",
            ),
            (
                130,
                body("error", Some("interrupted"), "rolled-back", json!(false)),
                "rejected",
            ),
            (
                130,
                body(
                    "error",
                    Some("interrupted"),
                    "recovery-pending",
                    json!(true),
                ),
                "recoveryRequired",
            ),
            (
                1,
                body("error", Some("interrupted"), "rolled-back", json!(false)),
                "unknown",
            ),
            (
                0,
                body("unchanged", None, "not-started", json!(false)),
                "unknown",
            ),
            (
                2,
                body("applied", None, "committed", json!(false)),
                "unknown",
            ),
        ];
        for (code, value, expected) in cases {
            let outcome = classify(&p, &exited(code, &value), "cmd");
            assert_eq!(kind(&outcome), expected, "{code} {value}");
        }
    }

    #[test]
    fn nothing_after_commit_with_discrepancies_is_success() {
        let p = pending();
        let mutations: Vec<(&str, Value)> = vec![
            ("/plan/matched", json!(false)),
            (
                "/plan/expected",
                json!(format!("{}{}", Fingerprint::PREFIX, "c".repeat(64))),
            ),
            (
                "/plan/fingerprint",
                json!(format!("{}{}", Fingerprint::PREFIX, "c".repeat(64))),
            ),
            ("/home", json!("C:\\Users\\other")),
            ("/transaction/recoveryRequired", json!(true)),
            ("/transaction/outcome", json!("rolled-back")),
            ("/changes/0/path", json!(".agents/skills/other/SKILL.md")),
            ("/changes/1/kind", json!("CREATE")),
            ("/changes/0/path", json!("../evil")),
        ];
        for (pointer, value) in mutations {
            let mut v = body("applied", None, "committed", json!(false));
            *v.pointer_mut(pointer).unwrap() = value.clone();
            let outcome = classify(&p, &exited(0, &v), "cmd");
            assert_eq!(kind(&outcome), "unknown", "{pointer} = {value}");
        }
        let mut extra = body("applied", None, "committed", json!(false));
        extra["changes"].as_array_mut().unwrap().push(json!(
            {"kind": "CREATE", "path": ".claude/agents/x.md", "sha256": "a".repeat(64),
             "target": "claude", "component": {"type": "specialist", "name": "x"}}));
        assert_eq!(kind(&classify(&p, &exited(0, &extra), "cmd")), "unknown");
    }

    #[test]
    fn outcomes_never_leak_the_fingerprint() {
        let p = pending();
        for (code, v) in [
            (0, body("applied", None, "committed", json!(false))),
            (
                1,
                body("error", Some("stale-plan"), "not-started", json!(false)),
            ),
        ] {
            let wire = serde_json::to_string(&classify(&p, &exited(code, &v), "cmd")).unwrap();
            assert!(!wire.contains(&"a".repeat(20)), "{wire}");
            assert!(!wire.contains("sha256"), "{wire}");
        }
    }

    // ---- Integration tests against the staged runtime and temporary homes --

    fn seed(runtime: &Runtime, home: &Path, files: &[(&str, &str)]) {
        let _ = runtime;
        for (rel, content) in files {
            let path = rel
                .split('/')
                .fold(home.to_path_buf(), |p, part| p.join(part));
            std::fs::create_dir_all(path.parent().unwrap()).unwrap();
            std::fs::write(path, content).unwrap();
        }
    }

    fn token_of(outcome: &PreviewOutcome) -> String {
        match outcome {
            PreviewOutcome::Plan {
                apply_token: Some(token),
                ..
            } => token.clone(),
            other => panic!("expected an applicable plan, got {other:?}"),
        }
    }

    #[test]
    fn preview_then_apply_succeeds_and_is_verified() {
        let Some(runtime) = staged() else { return };
        let home = scratch("apply-ok");
        seed(
            &runtime,
            &home,
            &[(".claude/CLAUDE.md", "# Personal notes\n")],
        );
        let env = env_with_home(&home);
        let store = PlanStore::default();
        let o = opts(Target::Both, true, false);
        let token = token_of(&preview(&store, &runtime, &o, &env));
        match apply(&store, &runtime, &token, &env) {
            ApplyOutcome::Applied {
                verified, changes, ..
            } => {
                assert!(verified);
                assert_eq!(changes.len(), 20);
            }
            other => panic!("unexpected {other:?}"),
        }
        let claude = std::fs::read_to_string(home.join(".claude/CLAUDE.md")).unwrap();
        assert!(
            claude.starts_with("# Personal notes\n"),
            "personal content kept"
        );
        assert!(claude.contains("<!-- ai-engineering-hub:start -->"));
        let backups = std::fs::read_dir(home.join(".claude"))
            .unwrap()
            .filter(|e| {
                e.as_ref()
                    .unwrap()
                    .file_name()
                    .to_string_lossy()
                    .starts_with("CLAUDE.md.bak.")
            })
            .count();
        assert_eq!(backups, 1, "the updated file keeps a .bak copy");
        // The token was consumed.
        assert!(matches!(
            apply(&store, &runtime, &token, &env),
            ApplyOutcome::NotAuthorized {
                reason: Denied::NoPlan
            }
        ));
    }

    #[test]
    fn external_change_after_preview_is_stale_and_writes_nothing() {
        let Some(runtime) = staged() else { return };
        let home = scratch("apply-stale");
        seed(&runtime, &home, &[(".claude/CLAUDE.md", "# Personal\n")]);
        let env = env_with_home(&home);
        let store = PlanStore::default();
        let token = token_of(&preview(
            &store,
            &runtime,
            &opts(Target::Claude, false, false),
            &env,
        ));
        std::fs::write(
            home.join(".claude/CLAUDE.md"),
            "# Personal, edited elsewhere\n",
        )
        .unwrap();
        let before = snapshot(&home);
        assert_eq!(kind(&apply(&store, &runtime, &token, &env)), "stale");
        let after: Vec<_> = snapshot(&home)
            .into_iter()
            .filter(|(rel, _, _)| rel != Path::new(".ai-engineering-hub.sync.lock"))
            .collect();
        let before: Vec<_> = before
            .into_iter()
            .filter(|(rel, _, _)| rel != Path::new(".ai-engineering-hub.sync.lock"))
            .collect();
        assert_eq!(after, before, "a stale plan must not write");
    }

    #[test]
    fn option_change_and_unchanged_plans_never_authorize() {
        let Some(runtime) = staged() else { return };
        let home = scratch("apply-options");
        let env = env_with_home(&home);
        let store = PlanStore::default();
        let token = token_of(&preview(
            &store,
            &runtime,
            &opts(Target::Both, false, false),
            &env,
        ));
        store.invalidate(); // discard_plan: the user changed an option
        assert!(matches!(
            apply(&store, &runtime, &token, &env),
            ApplyOutcome::NotAuthorized { .. }
        ));
        assert_eq!(std::fs::read_dir(&home).unwrap().count(), 0);

        // After applying everything, a new preview is unchanged: no token.
        let token = token_of(&preview(
            &store,
            &runtime,
            &opts(Target::Both, false, false),
            &env,
        ));
        assert_eq!(kind(&apply(&store, &runtime, &token, &env)), "applied");
        match preview(&store, &runtime, &opts(Target::Both, false, false), &env) {
            PreviewOutcome::Plan {
                status,
                apply_token,
                ..
            } => {
                assert_eq!(status, "unchanged");
                assert!(apply_token.is_none());
            }
            other => panic!("unexpected {other:?}"),
        }
    }

    #[test]
    fn tampered_bundle_blocks_apply_before_any_write() {
        let Some(runtime) = staged() else { return };
        let home = scratch("apply-tampered");
        let env = env_with_home(&home);
        let store = PlanStore::default();
        let copy = scratch("apply-tampered-bundle");
        for tree in ["python", "syncode"] {
            copy_tree(&runtime.workdir.join(tree), &copy.join(tree));
        }
        let tampered = Runtime::from_resource_dir(&copy);
        let token = token_of(&preview(
            &store,
            &tampered,
            &opts(Target::Both, false, false),
            &env,
        ));
        let module = copy.join("syncode").join("scripts").join("transaction.py");
        let mut code = std::fs::read(&module).unwrap();
        code.extend_from_slice(b"\n# altered after the preview\n");
        std::fs::write(&module, code).unwrap();
        match apply(&store, &tampered, &token, &env) {
            ApplyOutcome::Transport(t) => assert_eq!(t.code, TransportCode::IntegrityFailed),
            other => panic!("unexpected {other:?}"),
        }
        assert_eq!(
            std::fs::read_dir(&home).unwrap().count(),
            0,
            "nothing written"
        );
    }

    #[test]
    fn held_core_lock_is_reported_busy_without_writes() {
        let Some(runtime) = staged() else { return };
        let home = scratch("apply-busy");
        let env = env_with_home(&home);
        let store = PlanStore::default();
        let token = token_of(&preview(
            &store,
            &runtime,
            &opts(Target::Codex, false, false),
            &env,
        ));
        // Another SYNCODE process holds the installation lock.
        let ready = home
            .join("..")
            .join(format!("lock-ready-{}", std::process::id()));
        let _ = std::fs::remove_file(&ready);
        let holder = std::process::Command::new(&runtime.python)
            .args(["-I", "-B", "-c"])
            .arg(format!(
                "import sys, time; sys.path.insert(0, r'{}'); import transaction\n\
                 from pathlib import Path\nfrom sync import ensure_safe\n\
                 with transaction.exclusive_home_lock(Path(r'{}'), ensure_safe):\n    \
                 open(r'{}', 'w').close(); time.sleep(20)\n",
                runtime.workdir.join("syncode").join("scripts").display(),
                home.display(),
                ready.display()
            ))
            .spawn()
            .unwrap();
        let mut holder = holder;
        let started = Instant::now();
        while !ready.exists() && started.elapsed() < Duration::from_secs(15) {
            std::thread::sleep(Duration::from_millis(50));
        }
        assert!(ready.exists(), "lock holder did not start");
        let outcome = apply(&store, &runtime, &token, &env);
        let _ = holder.kill();
        let _ = holder.wait();
        assert_eq!(kind(&outcome), "busy", "{outcome:?}");
        assert!(!home.join(".codex").exists() && !home.join(".agents").exists());
    }

    #[test]
    fn failure_during_apply_rolls_back_consistently() {
        let Some(runtime) = staged() else { return };
        let home = scratch("apply-rollback");
        seed(&runtime, &home, &[(".claude/CLAUDE.md", "# Personal\n")]);
        let env = env_with_home(&home);
        let store = PlanStore::default();
        let token = token_of(&preview(
            &store,
            &runtime,
            &opts(Target::Both, false, false),
            &env,
        ));
        let instructions = home.join(".claude").join("CLAUDE.md");
        let mut perms = std::fs::metadata(&instructions).unwrap().permissions();
        perms.set_readonly(true);
        std::fs::set_permissions(&instructions, perms.clone()).unwrap();
        let before = snapshot(&home);
        let outcome = apply(&store, &runtime, &token, &env);
        perms.set_readonly(false);
        std::fs::set_permissions(&instructions, perms).unwrap();
        match &outcome {
            ApplyOutcome::Rejected { outcome, .. } => assert_eq!(outcome, "rolled-back"),
            other => panic!("unexpected {other:?}"),
        }
        let strip = |s: Vec<(std::path::PathBuf, Option<Vec<u8>>, std::time::SystemTime)>| {
            s.into_iter()
                .filter(|(rel, _, _)| rel != Path::new(".ai-engineering-hub.sync.lock"))
                .map(|(rel, bytes, _)| (rel, bytes))
                .collect::<Vec<_>>()
        };
        assert_eq!(
            strip(snapshot(&home)),
            strip(before),
            "rollback restores everything"
        );
        assert!(!home.join(".ai-engineering-hub-transaction").exists());
    }
}
