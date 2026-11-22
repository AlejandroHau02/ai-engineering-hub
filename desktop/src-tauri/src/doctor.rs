//! `run_doctor`: SYNCODE Doctor's fixed invocation and its contract check.
//!
//! Doctor is read-only. Its result (exit 0/2), its own errors (exit 1/130)
//! and transport failures (see `runner::Transport`) are kept apart.

use crate::runner::{self, Exited, Runtime, Script, Transport, TransportCode};
use serde::Serialize;
use serde_json::Value;
use std::ffi::OsString;
use std::time::Duration;

pub const DOCTOR_TIMEOUT: Duration = Duration::from_secs(20);
/// Fixed, closed argument list (contract: docs/SYNCODE_CLI_CONTRACT.md).
pub const DOCTOR_ARGS: [&str; 4] = ["--target", "both", "--format", "json"];
const EXPECTED_TARGETS: [&str; 2] = ["claude", "codex"];

#[derive(Serialize, Debug, PartialEq)]
#[serde(tag = "kind", rename_all = "camelCase")]
pub enum DoctorOutcome {
    /// Doctor completed: exit 0 (no blockers) or 2 (blockers to review).
    #[serde(rename_all = "camelCase")]
    Report { exit_code: i32, report: Value },
    /// Doctor ran and reported its own error (exit 1, or 130 when interrupted).
    #[serde(rename_all = "camelCase")]
    DoctorError {
        exit_code: i32,
        error_code: String,
        message: String,
    },
    /// The diagnosis could not run or its output cannot be trusted.
    Transport(Transport),
}

pub fn run(runtime: &Runtime, env: &[(String, OsString)], timeout: Duration) -> DoctorOutcome {
    match runner::execute(runtime, Script::Doctor, &DOCTOR_ARGS, env, timeout) {
        Ok(exited) => match classify(&exited) {
            DoctorOutcome::Transport(failure) => DoctorOutcome::Transport(exited.annotate(failure)),
            other => other,
        },
        Err(failure) => DoctorOutcome::Transport(failure),
    }
}

fn get_str<'a>(object: &'a serde_json::Map<String, Value>, key: &str) -> Option<&'a str> {
    object.get(key).and_then(Value::as_str)
}

/// Pure contract check of Doctor's exit code and stdout (schemaVersion 1).
///
/// Invariants are strict, while additive evolution (unknown error codes,
/// statuses or extra fields) is tolerated and left to the UI to present as
/// unknown rather than rejected.
pub fn classify(exited: &Exited) -> DoctorOutcome {
    let (code, payload) = match runner::parse_contract(exited, &[0, 1, 2, 130]) {
        Ok(parsed) => parsed,
        Err(failure) => return DoctorOutcome::Transport(failure),
    };
    let mismatch = |why: &str| {
        DoctorOutcome::Transport(Transport::new(
            TransportCode::ContractMismatch,
            why,
            Some(code),
        ))
    };
    let is_error = get_str(&payload, "status") == Some("error");
    match code {
        0 | 2 => {
            if payload.get("status").is_some() {
                return mismatch("diagnosis report carries a status field");
            }
            let targets_ok = payload
                .get("targets")
                .and_then(Value::as_array)
                .map(|t| {
                    t.iter()
                        .map(Value::as_str)
                        .eq(EXPECTED_TARGETS.iter().map(|s| Some(*s)))
                })
                .unwrap_or(false);
            if !targets_ok {
                return mismatch("targets are not [claude, codex]");
            }
            let clients_ok = payload
                .get("clients")
                .and_then(Value::as_object)
                .map_or(false, |c| {
                    EXPECTED_TARGETS
                        .iter()
                        .all(|t| c.get(*t).map_or(false, Value::is_object))
                });
            if !clients_ok {
                return mismatch("clients.claude/clients.codex missing");
            }
            if get_str(&payload, "home").is_none() {
                return mismatch("home missing");
            }
            let blockers = match payload
                .get("summary")
                .and_then(|s| s.get("blockers"))
                .and_then(Value::as_u64)
            {
                Some(n) => n,
                None => return mismatch("summary.blockers missing"),
            };
            if (code == 2) != (blockers > 0) {
                return mismatch("exit code inconsistent with summary.blockers");
            }
            DoctorOutcome::Report {
                exit_code: code,
                report: Value::Object(payload),
            }
        }
        _ => {
            if !is_error {
                return mismatch("error exit without status: error");
            }
            let error_code = match get_str(&payload, "errorCode") {
                Some(c) if !c.is_empty() => c.to_string(),
                _ => return mismatch("errorCode missing"),
            };
            if (code == 130) != (error_code == "interrupted") {
                return mismatch("exit code inconsistent with errorCode");
            }
            let message = get_str(&payload, "error").unwrap_or_default().to_string();
            DoctorOutcome::DoctorError {
                exit_code: code,
                error_code,
                message,
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::runner::testing::*;
    use serde_json::json;
    use std::path::Path;

    fn exited(code: i32, stdout: Vec<u8>) -> Exited {
        Exited {
            code: Some(code),
            stdout,
            overflow: false,
            stderr_len: 0,
        }
    }

    fn report(blockers: u64) -> Vec<u8> {
        let mut body = serde_json::to_vec(&json!({
            "schemaVersion": 1, "product": "SYNCODE", "home": "C:\\Users\\x",
            "targets": ["claude", "codex"], "transaction": "clear", "lock": "absent",
            "clients": {"claude": {}, "codex": {}},
            "summary": {"current": 0, "missing": 0, "upgradeAvailable": 0, "unmanaged": 0, "blockers": blockers}
        }))
        .unwrap();
        body.push(b'\n');
        body
    }

    fn error_payload(code: &str) -> Vec<u8> {
        serde_json::to_vec(&json!({
            "schemaVersion": 1, "product": "SYNCODE", "status": "error", "home": null,
            "targets": [], "error": "boom", "errorCode": code
        }))
        .unwrap()
    }

    fn transport_code(outcome: DoctorOutcome) -> TransportCode {
        match outcome {
            DoctorOutcome::Transport(failure) => failure.code,
            other => panic!("expected transport failure, got {other:?}"),
        }
    }

    #[test]
    fn exit_zero_without_blockers_is_report() {
        assert!(matches!(
            classify(&exited(0, report(0))),
            DoctorOutcome::Report { exit_code: 0, .. }
        ));
    }

    #[test]
    fn exit_two_with_blockers_is_report() {
        assert!(matches!(
            classify(&exited(2, report(3))),
            DoctorOutcome::Report { exit_code: 2, .. }
        ));
    }

    #[test]
    fn exit_code_must_match_blockers() {
        assert_eq!(
            transport_code(classify(&exited(0, report(1)))),
            TransportCode::ContractMismatch
        );
        assert_eq!(
            transport_code(classify(&exited(2, report(0)))),
            TransportCode::ContractMismatch
        );
    }

    #[test]
    fn doctor_error_is_not_transport() {
        assert_eq!(
            classify(&exited(1, error_payload("io-error"))),
            DoctorOutcome::DoctorError {
                exit_code: 1,
                error_code: "io-error".into(),
                message: "boom".into()
            }
        );
    }

    #[test]
    fn unknown_error_code_is_tolerated() {
        assert!(matches!(
            classify(&exited(1, error_payload("future-code"))),
            DoctorOutcome::DoctorError { .. }
        ));
    }

    #[test]
    fn interrupted_requires_exit_130() {
        assert!(matches!(
            classify(&exited(130, error_payload("interrupted"))),
            DoctorOutcome::DoctorError { exit_code: 130, .. }
        ));
        assert_eq!(
            transport_code(classify(&exited(1, error_payload("interrupted")))),
            TransportCode::ContractMismatch
        );
        assert_eq!(
            transport_code(classify(&exited(130, error_payload("io-error")))),
            TransportCode::ContractMismatch
        );
    }

    #[test]
    fn report_on_error_exit_is_mismatch() {
        assert_eq!(
            transport_code(classify(&exited(1, report(0)))),
            TransportCode::ContractMismatch
        );
        assert_eq!(
            transport_code(classify(&exited(0, error_payload("io-error")))),
            TransportCode::ContractMismatch
        );
    }

    #[test]
    fn rejects_wrong_shape() {
        let swap = |from: &str, to: &str| {
            String::from_utf8(report(0))
                .unwrap()
                .replace(from, to)
                .into_bytes()
        };
        for body in [
            swap("[\"claude\",\"codex\"]", "[\"claude\"]"),
            swap("\"codex\":{}", "\"codex\":[]"),
            swap("\"home\":\"C:\\\\Users\\\\x\"", "\"home\":null"),
        ] {
            assert_eq!(
                transport_code(classify(&exited(0, body.clone()))),
                TransportCode::ContractMismatch,
                "{}",
                String::from_utf8_lossy(&body)
            );
        }
    }

    #[test]
    fn transport_serializes_with_kind_tag() {
        let value = serde_json::to_value(DoctorOutcome::Transport(Transport::new(
            TransportCode::InvalidJson,
            "x",
            Some(0),
        )))
        .unwrap();
        assert_eq!(
            value,
            json!({"kind": "transport", "code": "invalidJson", "detail": "x", "exitCode": 0})
        );
    }

    // ---- Integration tests against the staged embedded runtime ----------

    #[test]
    fn real_doctor_on_empty_home() {
        let Some(runtime) = staged() else { return };
        let home = scratch("home");
        std::env::set_var("PYTHONPATH", home.join("poison"));
        match run(&runtime, &env_with_home(&home), DOCTOR_TIMEOUT) {
            DoctorOutcome::Report {
                exit_code: 0,
                report,
            } => {
                assert_eq!(report["summary"]["blockers"], 0);
                assert_eq!(report["summary"]["missing"], 20);
                assert_eq!(
                    Path::new(report["home"].as_str().unwrap())
                        .canonicalize()
                        .unwrap(),
                    home.canonicalize().unwrap()
                );
            }
            other => panic!("unexpected outcome {other:?}"),
        }
        assert_eq!(
            std::fs::read_dir(&home).unwrap().count(),
            0,
            "doctor must not write"
        );
    }

    #[test]
    fn real_doctor_reports_blockers_with_exit_two() {
        let Some(runtime) = staged() else { return };
        let home = scratch("blocked");
        std::fs::create_dir_all(home.join(".ai-engineering-hub-transaction")).unwrap();
        match run(&runtime, &env_with_home(&home), DOCTOR_TIMEOUT) {
            DoctorOutcome::Report {
                exit_code: 2,
                report,
            } => assert!(report["summary"]["blockers"].as_u64().unwrap() > 0),
            other => panic!("unexpected outcome {other:?}"),
        }
    }

    #[test]
    fn invalid_json_from_process() {
        let Some(runtime) = staged() else { return };
        let rt = fake(&runtime, "garbage", "print('not json')\n");
        assert_eq!(
            transport_code(run(&rt, &[], DOCTOR_TIMEOUT)),
            TransportCode::InvalidJson
        );
    }

    #[test]
    fn crash_without_output_is_no_output() {
        let Some(runtime) = staged() else { return };
        let rt = fake(
            &runtime,
            "crash",
            "import sys\nsys.stderr.write('Traceback...')\nraise SystemExit(1)\n",
        );
        match run(&rt, &[], DOCTOR_TIMEOUT) {
            DoctorOutcome::Transport(failure) => {
                assert_eq!(failure.code, TransportCode::NoOutput);
                assert!(failure.detail.contains("stderr"), "{}", failure.detail);
            }
            other => panic!("unexpected outcome {other:?}"),
        }
    }
}
