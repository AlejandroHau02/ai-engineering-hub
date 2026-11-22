//! `preview_sync`: read-only preview from the real SYNCODE planner.
//!
//! The webview may choose only a closed set of options ([`PreviewOptions`]);
//! the argv is assembled from string literals and always carries `--preview`,
//! which the core rejects together with `--apply`/`--recover`. The response is
//! checked against the schemaVersion 1 contract with an allow-list of values
//! and projected into a small DTO: per-file hashes, `events[]` (local backup
//! paths) and the full plan fingerprint never reach the webview.

use crate::runner::{
    self, is_plain_relative, Exited, Fingerprint, Runtime, Script, Transport, TransportCode,
};
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};
use std::ffi::OsString;
use std::time::Duration;

pub const PREVIEW_TIMEOUT: Duration = Duration::from_secs(30);
const MAX_CHANGES: usize = 500;
const MANAGED_ROOTS: [&str; 3] = [".claude", ".codex", ".agents"];

#[derive(Deserialize, Serialize, Debug, Clone, Copy, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum Target {
    Both,
    Claude,
    Codex,
}

impl Target {
    fn arg(self) -> &'static str {
        match self {
            Target::Both => "both",
            Target::Claude => "claude",
            Target::Codex => "codex",
        }
    }

    fn expected(self) -> &'static [&'static str] {
        match self {
            Target::Both => &["claude", "codex"],
            Target::Claude => &["claude"],
            Target::Codex => &["codex"],
        }
    }
}

/// Everything the webview may choose. Unknown keys, missing fields and
/// non-boolean values are rejected at the IPC boundary.
#[derive(Deserialize, Serialize, Debug, Clone, Copy, PartialEq, Eq)]
#[serde(deny_unknown_fields, rename_all = "camelCase")]
pub struct PreviewOptions {
    pub target: Target,
    pub specialists: bool,
    pub upgrade_skills: bool,
}

/// Fixed argv for a preview. Only literals; `--approved-skills` (network),
/// `--apply`, `--recover`, `--expect-plan` and `--home` are unrepresentable.
pub fn argv(options: &PreviewOptions) -> Vec<&'static str> {
    let mut args = vec![
        "--preview",
        "--target",
        options.target.arg(),
        "--format",
        "json",
    ];
    if options.specialists {
        args.push("--specialists");
    }
    if options.upgrade_skills {
        args.push("--upgrade-skills");
    }
    args
}

#[derive(Serialize, Debug, Clone, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct PlannedChange {
    pub kind: String,
    pub path: String,
    pub target: Option<String>,
    pub component_type: String,
    pub component_name: Option<String>,
}

#[derive(Serialize, Debug, PartialEq)]
#[serde(tag = "kind", rename_all = "camelCase")]
pub enum PreviewOutcome {
    /// The planner built a plan (`planned`) or found nothing to do (`unchanged`).
    #[serde(rename_all = "camelCase")]
    Plan {
        status: String,
        options: PreviewOptions,
        home: String,
        changes: Vec<PlannedChange>,
        /// Abbreviated, display-only identifier; not an authorization.
        plan_id: String,
        /// Full fingerprint: kept in Rust (plan store), never serialized.
        #[serde(skip)]
        fingerprint: Fingerprint,
        /// Opaque single-use id issued by the plan store when this plan may
        /// be applied; `None` otherwise.
        apply_token: Option<String>,
        /// `transaction.recoveryRequired` as observed on disk at the end:
        /// `true` means a transaction workspace appeared while previewing
        /// (another tool may be applying); `None` means it could not be
        /// determined. Either way the UI warns instead of trusting the plan.
        recovery_required: Option<bool>,
    },
    /// The planner stopped on a controlled condition; nothing was written.
    #[serde(rename_all = "camelCase")]
    PlannerRefusal {
        options: PreviewOptions,
        exit_code: i32,
        code: String,
        message: String,
        recovery_required: Option<bool>,
    },
    /// The preview could not run or its output cannot be trusted.
    Transport(Transport),
}

pub fn run(
    runtime: &Runtime,
    options: &PreviewOptions,
    env: &[(String, OsString)],
    timeout: Duration,
) -> PreviewOutcome {
    match runner::execute(runtime, Script::Sync, &argv(options), None, env, timeout) {
        Ok(exited) => match classify(options, &exited) {
            PreviewOutcome::Transport(failure) => {
                PreviewOutcome::Transport(exited.annotate(failure))
            }
            other => other,
        },
        Err(failure) => PreviewOutcome::Transport(failure),
    }
}

fn is_hex64(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

fn str_of<'a>(object: &'a Map<String, Value>, key: &str) -> Option<&'a str> {
    object.get(key).and_then(Value::as_str)
}

/// Pure contract check of a `sync.py --preview --format json` response.
pub fn classify(options: &PreviewOptions, exited: &Exited) -> PreviewOutcome {
    let (code, payload) = match runner::parse_contract(exited, &[0, 1, 130]) {
        Ok(parsed) => parsed,
        Err(failure) => return PreviewOutcome::Transport(failure),
    };
    let mismatch = |why: &str| {
        PreviewOutcome::Transport(Transport::new(
            TransportCode::ContractMismatch,
            why,
            Some(code),
        ))
    };

    // Safety invariants valid for every preview response.
    let Some(transaction) = payload.get("transaction").and_then(Value::as_object) else {
        return mismatch("transaction missing");
    };
    if str_of(transaction, "outcome") != Some("not-started") {
        return mismatch("preview reported a transaction outcome other than not-started");
    }
    let recovery_required = match transaction.get("recoveryRequired") {
        Some(Value::Bool(b)) => Some(*b),
        Some(Value::Null) => None,
        _ => return mismatch("transaction.recoveryRequired is not boolean or null"),
    };
    let Some(plan) = payload.get("plan").and_then(Value::as_object) else {
        return mismatch("plan missing");
    };
    if str_of(plan, "algorithm") != Some("syncode-plan-v1")
        || !plan.get("expected").map_or(false, Value::is_null)
        || !plan.get("matched").map_or(false, Value::is_null)
    {
        return mismatch("plan is not an unbound syncode-plan-v1 preview");
    }
    let status = str_of(&payload, "status").unwrap_or_default();

    match code {
        0 => {
            if status != "planned" && status != "unchanged" {
                return mismatch("preview status is not planned or unchanged");
            }
            if !payload.get("error").map_or(false, Value::is_null) {
                return mismatch("successful preview carries an error");
            }
            let targets_ok = payload
                .get("targets")
                .and_then(Value::as_array)
                .map(|t| {
                    t.iter().map(Value::as_str).eq(options
                        .target
                        .expected()
                        .iter()
                        .map(|s| Some(*s)))
                })
                .unwrap_or(false);
            if !targets_ok {
                return mismatch("targets differ from the requested ones");
            }
            let Some(home) = str_of(&payload, "home") else {
                return mismatch("home missing");
            };
            let Some(fingerprint) = str_of(plan, "fingerprint").and_then(Fingerprint::parse) else {
                return mismatch("plan.fingerprint is not well-formed");
            };
            let Some(raw_changes) = payload.get("changes").and_then(Value::as_array) else {
                return mismatch("changes missing");
            };
            if raw_changes.len() > MAX_CHANGES {
                return mismatch("too many changes");
            }
            if (status == "planned") == raw_changes.is_empty() {
                return mismatch("status inconsistent with changes");
            }
            let mut changes = Vec::with_capacity(raw_changes.len());
            for raw in raw_changes {
                match project_change(options, raw) {
                    Ok(change) => changes.push(change),
                    Err(why) => return mismatch(why),
                }
            }
            PreviewOutcome::Plan {
                status: status.to_string(),
                options: *options,
                home: home.to_string(),
                changes,
                plan_id: fingerprint.short(),
                fingerprint,
                apply_token: None,
                recovery_required,
            }
        }
        _ => {
            if status != "error" {
                return mismatch("error exit without status: error");
            }
            let Some(error) = payload.get("error").and_then(Value::as_object) else {
                return mismatch("error object missing");
            };
            let error_code = match str_of(error, "code") {
                Some(c) if !c.is_empty() => c,
                _ => return mismatch("error.code missing"),
            };
            if (code == 130) != (error_code == "interrupted") {
                return mismatch("exit code inconsistent with error.code");
            }
            PreviewOutcome::PlannerRefusal {
                options: *options,
                exit_code: code,
                code: error_code.to_string(),
                message: str_of(error, "message").unwrap_or_default().to_string(),
                recovery_required,
            }
        }
    }
}

fn project_change(options: &PreviewOptions, raw: &Value) -> Result<PlannedChange, &'static str> {
    let object = raw.as_object().ok_or("change is not an object")?;
    let kind = str_of(object, "kind").ok_or("change.kind missing")?;
    if kind == "APPROVED-CREATE" {
        // Impossible without --approved-skills, which Desktop never passes.
        return Err("approved-skill change in a preview without approved skills");
    }
    let path = str_of(object, "path").ok_or("change.path missing")?;
    let root = path.split('/').next().unwrap_or_default();
    if !is_plain_relative(path) || !MANAGED_ROOTS.contains(&root) {
        return Err("change.path is not a managed relative path");
    }
    if !str_of(object, "sha256").map_or(false, is_hex64) {
        return Err("change.sha256 is not a SHA-256");
    }
    let target = match object.get("target") {
        Some(Value::Null) => None,
        Some(Value::String(t)) if options.target.expected().contains(&t.as_str()) => {
            Some(t.clone())
        }
        _ => return Err("change.target is not a requested target"),
    };
    let component = object
        .get("component")
        .and_then(Value::as_object)
        .ok_or("change.component missing")?;
    let component_type = str_of(component, "type")
        .filter(|t| !t.is_empty())
        .ok_or("change.component.type missing")?;
    let component_name = match component.get("name") {
        Some(Value::Null) => None,
        Some(Value::String(n)) => Some(n.clone()),
        _ => return Err("change.component.name is not a string or null"),
    };
    Ok(PlannedChange {
        // Unknown kinds/types are tolerated (additive contract) and shown as unknown.
        kind: kind.to_string(),
        path: path.to_string(),
        target,
        component_type: component_type.to_string(),
        component_name,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::runner::testing::*;
    use serde_json::json;
    use std::path::Path;

    const ALL: [Target; 3] = [Target::Both, Target::Claude, Target::Codex];

    fn options(target: Target, specialists: bool, upgrade_skills: bool) -> PreviewOptions {
        PreviewOptions {
            target,
            specialists,
            upgrade_skills,
        }
    }

    #[test]
    fn argv_is_exact_for_every_combination() {
        for target in ALL {
            for specialists in [false, true] {
                for upgrade in [false, true] {
                    let mut expected =
                        vec!["--preview", "--target", target.arg(), "--format", "json"];
                    if specialists {
                        expected.push("--specialists");
                    }
                    if upgrade {
                        expected.push("--upgrade-skills");
                    }
                    let actual = argv(&options(target, specialists, upgrade));
                    assert_eq!(actual, expected);
                    // Never anything argparse could expand to a write action
                    // (prefix abbreviations included) or to network/home.
                    for token in &actual {
                        for banned in ["--a", "--r", "--e", "--h"] {
                            assert!(!token.starts_with(banned), "{token}");
                        }
                    }
                }
            }
        }
    }

    #[test]
    fn ipc_options_reject_anything_unexpected() {
        let ok: PreviewOptions = serde_json::from_value(
            json!({"target": "codex", "specialists": true, "upgradeSkills": false}),
        )
        .unwrap();
        assert_eq!(ok, options(Target::Codex, true, false));
        for bad in [
            json!({"target": "both", "specialists": false, "upgradeSkills": false, "apply": true}),
            json!({"target": "both --apply", "specialists": false, "upgradeSkills": false}),
            json!({"target": "both", "specialists": "true", "upgradeSkills": false}),
            json!({"target": "both", "specialists": false}),
            json!({"target": "Both", "specialists": false, "upgradeSkills": false}),
            json!({"target": ["both"], "specialists": false, "upgradeSkills": false}),
        ] {
            assert!(
                serde_json::from_value::<PreviewOptions>(bad.clone()).is_err(),
                "{bad}"
            );
        }
    }

    fn change(kind: &str, path: &str, target: &str, ctype: &str, name: Value) -> Value {
        json!({"kind": kind, "path": path, "sha256": "a".repeat(64), "target": target,
               "component": {"type": ctype, "name": name}})
    }

    fn response(status: &str, changes: Vec<Value>) -> Value {
        json!({
            "schemaVersion": 1, "product": "SYNCODE", "status": status,
            "home": "C:\\Users\\x", "targets": ["claude", "codex"], "changes": changes,
            "events": ["BACKUP C:\\Users\\x\\.claude\\CLAUDE.md.bak"],
            "transaction": {"outcome": "not-started", "recoveryRequired": false},
            "plan": {"algorithm": "syncode-plan-v1",
                     "fingerprint": format!("{}{}", Fingerprint::PREFIX, "b".repeat(64)),
                     "expected": null, "matched": null},
            "error": null
        })
    }

    fn refusal(code: &str) -> Value {
        json!({
            "schemaVersion": 1, "product": "SYNCODE", "status": "error", "home": "C:\\x",
            "targets": ["claude", "codex"], "changes": [], "events": [],
            "transaction": {"outcome": "not-started", "recoveryRequired": false},
            "plan": {"algorithm": "syncode-plan-v1", "fingerprint": null,
                     "expected": null, "matched": null},
            "error": {"type": "SyncodeError", "code": code, "message": "Skill update at C:\\x"}
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

    fn both() -> PreviewOptions {
        options(Target::Both, false, false)
    }

    fn mismatch(outcome: PreviewOutcome) -> String {
        match outcome {
            PreviewOutcome::Transport(t) if t.code == TransportCode::ContractMismatch => t.detail,
            other => panic!("expected contract mismatch, got {other:?}"),
        }
    }

    fn sample_changes() -> Vec<Value> {
        vec![
            change(
                "UPDATE",
                ".claude/CLAUDE.md",
                "claude",
                "instructions",
                Value::Null,
            ),
            change(
                "CREATE",
                ".agents/skills/ux-ui/SKILL.md",
                "codex",
                "skill",
                json!("ux-ui"),
            ),
        ]
    }

    #[test]
    fn planned_preview_is_projected_without_hashes_or_events() {
        let outcome = classify(&both(), &exited(0, &response("planned", sample_changes())));
        let PreviewOutcome::Plan {
            ref changes,
            ref plan_id,
            recovery_required,
            ..
        } = outcome
        else {
            panic!("{outcome:?}")
        };
        assert_eq!(changes.len(), 2);
        assert_eq!(changes[1].component_name.as_deref(), Some("ux-ui"));
        assert_eq!(plan_id, "syncode-plan-v1 · bbbbbbbbbbbb");
        assert_eq!(recovery_required, Some(false));
        let wire = serde_json::to_string(&outcome).unwrap();
        for leaked in ["sha256", "aaaaaaaa", "events", "BACKUP", &"b".repeat(13)] {
            assert!(!wire.contains(leaked), "{leaked} leaked: {wire}");
        }
    }

    #[test]
    fn unchanged_preview_and_status_consistency() {
        assert!(matches!(
            classify(&both(), &exited(0, &response("unchanged", vec![]))),
            PreviewOutcome::Plan { .. }
        ));
        mismatch(classify(&both(), &exited(0, &response("planned", vec![]))));
        mismatch(classify(
            &both(),
            &exited(0, &response("unchanged", sample_changes())),
        ));
    }

    #[test]
    fn write_statuses_and_bound_plans_are_rejected() {
        for status in [
            "applied",
            "recovered",
            "no-pending-transaction",
            "error",
            "future",
        ] {
            mismatch(classify(
                &both(),
                &exited(0, &response(status, sample_changes())),
            ));
        }
        for (pointer, value) in [
            ("/transaction/outcome", json!("committed")),
            ("/transaction/outcome", json!("rolled-back")),
            ("/plan/expected", json!("syncode-plan-v1:sha256:00")),
            ("/plan/matched", json!(true)),
            ("/plan/fingerprint", json!("syncode-plan-v1:sha256:XYZ")),
            ("/plan/algorithm", json!("syncode-plan-v2")),
            ("/error", json!({"code": "x"})),
            ("/targets", json!(["claude"])),
            ("/transaction/recoveryRequired", json!("yes")),
        ] {
            let mut body = response("planned", sample_changes());
            *body.pointer_mut(pointer).unwrap() = value.clone();
            mismatch(classify(&both(), &exited(0, &body)));
        }
    }

    #[test]
    fn unsafe_or_impossible_changes_are_rejected() {
        for bad in [
            change(
                "APPROVED-CREATE",
                ".agents/skills/x/SKILL.md",
                "codex",
                "approved-skill",
                json!("x"),
            ),
            change(
                "CREATE",
                "../.claude/CLAUDE.md",
                "claude",
                "instructions",
                Value::Null,
            ),
            change(
                "CREATE",
                ".ssh/authorized_keys",
                "claude",
                "unknown",
                Value::Null,
            ),
            change(
                "CREATE",
                "C:/x/.claude/CLAUDE.md",
                "claude",
                "instructions",
                Value::Null,
            ),
            change(
                "CREATE",
                ".claude\\CLAUDE.md",
                "claude",
                "instructions",
                Value::Null,
            ),
            change(
                "CREATE",
                ".claude/CLAUDE.md",
                "other",
                "instructions",
                Value::Null,
            ),
            json!({"kind": "CREATE", "path": ".claude/CLAUDE.md", "sha256": "z", "target": "claude",
                   "component": {"type": "instructions", "name": null}}),
            json!({"kind": "CREATE", "path": ".claude/CLAUDE.md", "sha256": "a".repeat(64),
                   "target": "claude"}),
        ] {
            mismatch(classify(
                &both(),
                &exited(0, &response("planned", vec![bad])),
            ));
        }
        let too_many = vec![sample_changes()[0].clone(); MAX_CHANGES + 1];
        mismatch(classify(
            &both(),
            &exited(0, &response("planned", too_many)),
        ));
    }

    #[test]
    fn target_must_match_the_request() {
        let only_claude = options(Target::Claude, false, false);
        let mut body = response("planned", vec![sample_changes()[0].clone()]);
        body["targets"] = json!(["claude"]);
        assert!(matches!(
            classify(&only_claude, &exited(0, &body)),
            PreviewOutcome::Plan { .. }
        ));
        body["changes"] = json!([sample_changes()[1].clone()]); // a codex change
        mismatch(classify(&only_claude, &exited(0, &body)));
    }

    #[test]
    fn unknown_kinds_and_types_are_tolerated() {
        let body = response(
            "planned",
            vec![change(
                "REPLACE",
                ".claude/agents/x.md",
                "claude",
                "future-type",
                json!("x"),
            )],
        );
        assert!(matches!(
            classify(&both(), &exited(0, &body)),
            PreviewOutcome::Plan { .. }
        ));
    }

    #[test]
    fn recovery_required_is_passed_through() {
        for (raw, expected) in [(json!(true), Some(true)), (Value::Null, None)] {
            let mut body = response("planned", sample_changes());
            body["transaction"]["recoveryRequired"] = raw;
            match classify(&both(), &exited(0, &body)) {
                PreviewOutcome::Plan {
                    recovery_required, ..
                } => assert_eq!(recovery_required, expected),
                other => panic!("{other:?}"),
            }
        }
    }

    #[test]
    fn planner_refusals_are_not_transport_failures() {
        match classify(&both(), &exited(1, &refusal("skill-upgrade-available"))) {
            PreviewOutcome::PlannerRefusal {
                code, exit_code, ..
            } => {
                assert_eq!(code, "skill-upgrade-available");
                assert_eq!(exit_code, 1);
            }
            other => panic!("{other:?}"),
        }
        assert!(matches!(
            classify(&both(), &exited(1, &refusal("future-code"))),
            PreviewOutcome::PlannerRefusal { .. }
        ));
        assert!(matches!(
            classify(&both(), &exited(130, &refusal("interrupted"))),
            PreviewOutcome::PlannerRefusal { exit_code: 130, .. }
        ));
        mismatch(classify(&both(), &exited(1, &refusal("interrupted"))));
        mismatch(classify(&both(), &exited(130, &refusal("io-error"))));
        let mut wrote = refusal("io-error");
        wrote["transaction"]["outcome"] = json!("recovery-pending");
        mismatch(classify(&both(), &exited(1, &wrote)));
        mismatch(classify(
            &both(),
            &exited(1, &response("planned", sample_changes())),
        ));
        // Exit 2 belongs to Doctor, not to sync: outside the contract.
        assert!(matches!(
            classify(&both(), &exited(2, &response("planned", sample_changes()))),
            PreviewOutcome::Transport(Transport {
                code: TransportCode::ProcessFailed,
                ..
            })
        ));
    }

    // ---- Integration tests against the staged embedded runtime ----------

    /// Seed a home with the staged core itself (test-only process; Desktop
    /// never runs --apply).
    fn seed_installed(runtime: &Runtime, home: &Path) {
        let status = std::process::Command::new(&runtime.python)
            .args(["-I", "-B"])
            .arg(
                runtime
                    .workdir
                    .join("syncode")
                    .join("scripts")
                    .join("sync.py"),
            )
            .args(["--target", "both", "--specialists", "--apply", "--home"])
            .arg(home)
            .stdout(std::process::Stdio::null())
            .status()
            .unwrap();
        assert!(status.success());
    }

    fn assert_untouched(
        home: &Path,
        before: &[(std::path::PathBuf, Option<Vec<u8>>, std::time::SystemTime)],
    ) {
        // The snapshot covers every file and directory, including the
        // persistent lock file and the transaction workspace.
        assert_eq!(snapshot(home), before, "preview must not write");
        for marker in [
            ".ai-engineering-hub.sync.lock",
            ".ai-engineering-hub-transaction",
        ] {
            let existed = before.iter().any(|(rel, _, _)| rel == Path::new(marker));
            assert_eq!(
                home.join(marker).exists(),
                existed,
                "{marker} created or removed"
            );
        }
    }

    #[test]
    fn real_preview_on_empty_home_plans_and_writes_nothing() {
        let Some(runtime) = staged() else { return };
        let home = scratch("preview-empty");
        let before = snapshot(&home);
        match run(&runtime, &both(), &env_with_home(&home), PREVIEW_TIMEOUT) {
            PreviewOutcome::Plan {
                status, changes, ..
            } => {
                assert_eq!(status, "planned");
                assert_eq!(changes.len(), 12);
                assert!(changes.iter().all(|c| c.kind == "CREATE"));
            }
            other => panic!("unexpected outcome {other:?}"),
        }
        assert_untouched(&home, &before);
    }

    #[test]
    fn real_preview_states_write_nothing() {
        let Some(runtime) = staged() else { return };
        let home = scratch("preview-installed");
        seed_installed(&runtime, &home);
        let before = snapshot(&home);
        let env = env_with_home(&home);
        assert!(matches!(
            run(&runtime, &options(Target::Both, true, false), &env, PREVIEW_TIMEOUT),
            PreviewOutcome::Plan { ref status, .. } if status == "unchanged"
        ));
        assert_untouched(&home, &before);
        // A v0.1 baseline skill: refused without upgrade preview, UPGRADE with it.
        let skill = home
            .join(".claude")
            .join("skills")
            .join("ux-ui")
            .join("SKILL.md");
        let baseline = runtime
            .workdir
            .join("syncode/registry/baselines/v0.1/ux-ui/SKILL.md");
        std::fs::copy(baseline, &skill).unwrap();
        let before = snapshot(&home);
        match run(&runtime, &both(), &env, PREVIEW_TIMEOUT) {
            PreviewOutcome::PlannerRefusal { code, .. } => {
                assert_eq!(code, "skill-upgrade-available")
            }
            other => panic!("unexpected outcome {other:?}"),
        }
        match run(
            &runtime,
            &options(Target::Claude, false, true),
            &env,
            PREVIEW_TIMEOUT,
        ) {
            PreviewOutcome::Plan { changes, .. } => {
                assert_eq!(changes.len(), 1);
                assert_eq!(changes[0].kind, "UPGRADE");
                assert_eq!(changes[0].component_name.as_deref(), Some("ux-ui"));
            }
            other => panic!("unexpected outcome {other:?}"),
        }
        assert_untouched(&home, &before);
    }
}
