//! Closed bridge to SYNCODE Doctor running on the bundled Python runtime.
//!
//! The only process this module starts is the embedded `python.exe` running
//! the bundled `doctor.py` with fixed arguments. There is no shell, no
//! caller-supplied argument and no environment inheritance beyond an explicit
//! allow-list. Transport failures (the diagnosis could not run or its answer
//! cannot be trusted) are kept apart from Doctor's own results and errors.

use serde::Serialize;
use serde_json::Value;
use std::ffi::OsString;
use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::thread;
use std::time::{Duration, Instant};

pub const DOCTOR_TIMEOUT: Duration = Duration::from_secs(20);
pub const MAX_STDOUT: usize = 1 << 20;
const MAX_STDERR: usize = 64 * 1024;
/// Fixed, closed argument list (contract: docs/SYNCODE_CLI_CONTRACT.md).
pub const DOCTOR_ARGS: [&str; 4] = ["--target", "both", "--format", "json"];
/// `-I`: isolated mode (ignores PYTHON* variables, user site, script dir and
/// cwd on sys.path). `-B`: never write `__pycache__` into the install dir.
const PYTHON_FLAGS: [&str; 2] = ["-I", "-B"];
/// Variables Doctor legitimately needs: the Windows system root, the user
/// home (`Path.home()`), PATH/PATHEXT (only for `shutil.which` detection of
/// `claude`/`codex`, which are never executed) and a temp directory.
const ENV_ALLOWLIST: [&str; 9] = [
    "SystemRoot",
    "SystemDrive",
    "USERPROFILE",
    "HOMEDRIVE",
    "HOMEPATH",
    "PATH",
    "PATHEXT",
    "TEMP",
    "TMP",
];
const EXPECTED_TARGETS: [&str; 2] = ["claude", "codex"];

#[derive(Serialize, Debug, Clone, Copy, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub enum TransportCode {
    /// The bundled runtime or Doctor files are missing.
    BundleIncomplete,
    /// The OS refused to start the interpreter.
    SpawnFailed,
    Timeout,
    OutputTooLarge,
    /// Process ended without a JSON response: state unknown.
    NoOutput,
    InvalidJson,
    /// Valid JSON that violates the schemaVersion 1 contract.
    ContractMismatch,
    /// Exit code outside the contract (crash, missing DLL, ...).
    ProcessFailed,
    /// A diagnosis is already running.
    Busy,
}

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
    #[serde(rename_all = "camelCase")]
    Transport {
        code: TransportCode,
        detail: String,
        exit_code: Option<i32>,
    },
}

impl DoctorOutcome {
    fn transport(code: TransportCode, detail: impl Into<String>, exit_code: Option<i32>) -> Self {
        DoctorOutcome::Transport {
            code,
            detail: detail.into(),
            exit_code,
        }
    }
}

const MAX_MANIFEST: u64 = 256 * 1024;

/// Relative paths declared in `BUNDLE_MANIFEST.json`'s `files` map. `None`
/// when the manifest is unreadable, oversized, malformed or declares a path
/// that is not a plain relative path (no `..`, drive, root or backslash).
fn manifest_files(manifest: &Path) -> Option<Vec<String>> {
    if std::fs::metadata(manifest).ok()?.len() > MAX_MANIFEST {
        return None;
    }
    let value: Value = serde_json::from_slice(&std::fs::read(manifest).ok()?).ok()?;
    let files = value.get("files")?.as_object()?;
    let safe = |rel: &str| {
        !rel.is_empty()
            && rel
                .split('/')
                .all(|part| !part.is_empty() && part != "." && part != "..")
            && rel
                .chars()
                .all(|c| c.is_ascii_alphanumeric() || "._-/".contains(c))
    };
    if files.is_empty() || !files.keys().all(|k| safe(k)) {
        return None;
    }
    Some(files.keys().cloned().collect())
}

/// Locations of the bundled runtime, derived only from the resource dir.
#[derive(Debug, Clone)]
pub struct Runtime {
    pub python: PathBuf,
    pub script: PathBuf,
    pub workdir: PathBuf,
    required: Vec<PathBuf>,
}

impl Runtime {
    pub fn from_resource_dir(dir: &Path) -> Self {
        let python = dir.join("python").join("python.exe");
        let script = dir.join("syncode").join("scripts").join("doctor.py");
        let manifest = dir.join("syncode").join("BUNDLE_MANIFEST.json");
        let mut required = vec![
            python.clone(),
            dir.join("python").join("python312._pth"),
            dir.join("python").join("python312.dll"),
            dir.join("python").join("python312.zip"),
            script.clone(),
            manifest.clone(),
        ];
        // Every core file the bundle declares must exist, so a partial
        // install is reported as incomplete rather than as "no output".
        // This is an existence check only; hashes stay audit evidence.
        match manifest_files(&manifest) {
            Some(files) => required.extend(files.iter().map(|rel| {
                rel.split('/')
                    .fold(dir.join("syncode"), |p, part| p.join(part))
            })),
            // Present but unreadable/invalid: report it as an incomplete bundle.
            None if manifest.exists() => {
                required.push(dir.join("syncode").join("BUNDLE_MANIFEST.json (invalid)"))
            }
            None => {}
        }
        Runtime {
            python,
            script,
            workdir: dir.to_path_buf(),
            required,
        }
    }

    #[cfg(test)]
    pub fn with_script(&self, script: PathBuf) -> Self {
        Runtime {
            script: script.clone(),
            required: vec![self.python.clone(), script],
            ..self.clone()
        }
    }

    /// Names of required files that are absent (relative to the resource dir).
    pub fn missing(&self) -> Vec<String> {
        self.required
            .iter()
            .filter(|p| !p.is_file())
            .map(|p| {
                p.strip_prefix(&self.workdir)
                    .unwrap_or(p)
                    .display()
                    .to_string()
            })
            .collect()
    }
}

/// Build the child environment from an explicit allow-list.
pub fn child_env(lookup: impl Fn(&str) -> Option<OsString>) -> Vec<(String, OsString)> {
    let mut env: Vec<(String, OsString)> = ENV_ALLOWLIST
        .iter()
        .filter_map(|name| lookup(name).map(|value| (name.to_string(), value)))
        .collect();
    // `shutil.which` must not resolve executables from the working directory.
    env.push(("NoDefaultCurrentDirectoryInExePath".into(), "1".into()));
    env
}

struct Captured {
    bytes: Vec<u8>,
    overflow: bool,
}

/// Read a pipe to EOF, keeping at most `cap` bytes, so the child never blocks.
fn read_capped(mut reader: impl Read, cap: usize) -> Captured {
    let mut bytes = Vec::new();
    let mut overflow = false;
    let mut buf = [0u8; 8192];
    loop {
        match reader.read(&mut buf) {
            Ok(0) | Err(_) => break,
            Ok(n) => {
                let room = cap.saturating_sub(bytes.len());
                if n > room {
                    overflow = true;
                }
                bytes.extend_from_slice(&buf[..n.min(room)]);
            }
        }
    }
    Captured { bytes, overflow }
}

/// Spawn the runtime with the fixed arguments and classify the result.
pub fn run(runtime: &Runtime, env: &[(String, OsString)], timeout: Duration) -> DoctorOutcome {
    let missing = runtime.missing();
    if !missing.is_empty() {
        return DoctorOutcome::transport(TransportCode::BundleIncomplete, missing.join(", "), None);
    }
    let mut command = Command::new(&runtime.python);
    command
        .args(PYTHON_FLAGS)
        .arg(&runtime.script)
        .args(DOCTOR_ARGS)
        .current_dir(&runtime.workdir)
        .env_clear()
        .envs(env.iter().map(|(k, v)| (k, v)))
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        command.creation_flags(CREATE_NO_WINDOW);
    }
    let mut child = match command.spawn() {
        Ok(child) => child,
        Err(error) => {
            return DoctorOutcome::transport(TransportCode::SpawnFailed, error.to_string(), None)
        }
    };
    let stdout = child
        .stdout
        .take()
        .map(|pipe| thread::spawn(move || read_capped(pipe, MAX_STDOUT)));
    // stderr is not part of the contract: drained (to avoid a pipe deadlock)
    // and only its size is kept.
    let stderr = child
        .stderr
        .take()
        .map(|pipe| thread::spawn(move || read_capped(pipe, MAX_STDERR)));

    let deadline = Instant::now() + timeout;
    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break Some(status),
            Ok(None) if Instant::now() >= deadline => {
                let _ = child.kill();
                let _ = child.wait();
                break None;
            }
            Ok(None) => thread::sleep(Duration::from_millis(20)),
            Err(error) => {
                let _ = child.kill();
                let _ = child.wait();
                return DoctorOutcome::transport(
                    TransportCode::SpawnFailed,
                    error.to_string(),
                    None,
                );
            }
        }
    };
    let out = stdout.and_then(|h| h.join().ok()).unwrap_or(Captured {
        bytes: vec![],
        overflow: false,
    });
    let err_len = stderr
        .and_then(|h| h.join().ok())
        .map(|c| c.bytes.len())
        .unwrap_or(0);
    match status {
        None => DoctorOutcome::transport(
            TransportCode::Timeout,
            format!(
                "no response after {} s; process terminated",
                timeout.as_secs()
            ),
            None,
        ),
        Some(status) => {
            let outcome = classify(status.code(), &out.bytes, out.overflow);
            match outcome {
                DoctorOutcome::Transport {
                    code,
                    detail,
                    exit_code,
                } if err_len > 0 => DoctorOutcome::Transport {
                    code,
                    detail: format!("{detail} (stderr: {err_len} bytes, not shown)"),
                    exit_code,
                },
                other => other,
            }
        }
    }
}

fn get_str<'a>(object: &'a Value, key: &str) -> Option<&'a str> {
    object.get(key).and_then(Value::as_str)
}

/// Pure contract check of Doctor's exit code and stdout (schemaVersion 1).
///
/// Invariants are strict, while additive evolution (unknown error codes,
/// statuses or extra fields) is tolerated and left to the UI to present as
/// unknown rather than rejected.
pub fn classify(exit_code: Option<i32>, stdout: &[u8], overflow: bool) -> DoctorOutcome {
    use TransportCode::*;
    if overflow {
        return DoctorOutcome::transport(
            OutputTooLarge,
            format!("more than {MAX_STDOUT} bytes"),
            exit_code,
        );
    }
    let code = match exit_code {
        Some(code @ (0 | 1 | 2 | 130)) => code,
        other => {
            return DoctorOutcome::transport(
                ProcessFailed,
                "exit code outside the Doctor contract",
                other,
            )
        }
    };
    let text = stdout.trim_ascii();
    if text.is_empty() {
        return DoctorOutcome::transport(NoOutput, "no JSON on stdout; state unknown", Some(code));
    }
    if !stdout.is_ascii() {
        return DoctorOutcome::transport(InvalidJson, "stdout is not ASCII", Some(code));
    }
    // from_slice rejects trailing data, so a second object is invalid.
    let payload: Value = match serde_json::from_slice(text) {
        Ok(value) => value,
        Err(error) => return DoctorOutcome::transport(InvalidJson, error.to_string(), Some(code)),
    };
    let mismatch =
        |why: &str| DoctorOutcome::transport(ContractMismatch, why.to_string(), Some(code));
    if !payload.is_object() {
        return mismatch("response is not a JSON object");
    }
    // as_u64 is None for "1" and for 1.0: only the integer 1 is accepted.
    if payload.get("schemaVersion").and_then(Value::as_u64) != Some(1) {
        return mismatch("schemaVersion is not 1");
    }
    if get_str(&payload, "product") != Some("SYNCODE") {
        return mismatch("product is not SYNCODE");
    }
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
            let blockers = match payload.pointer("/summary/blockers").and_then(Value::as_u64) {
                Some(n) => n,
                None => return mismatch("summary.blockers missing"),
            };
            if (code == 2) != (blockers > 0) {
                return mismatch("exit code inconsistent with summary.blockers");
            }
            DoctorOutcome::Report {
                exit_code: code,
                report: payload,
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

/// Process-wide guard: only one diagnosis at a time.
static RUNNING: AtomicBool = AtomicBool::new(false);

pub struct RunGuard;

impl RunGuard {
    pub fn acquire() -> Option<RunGuard> {
        RUNNING
            .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
            .ok()
            .map(|_| RunGuard)
    }
}

impl Drop for RunGuard {
    fn drop(&mut self) {
        RUNNING.store(false, Ordering::Release);
    }
}

/// Display-only provenance from the bundle manifest (never trusted for logic).
#[derive(Serialize, Debug, Default, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct Provenance {
    pub app_version: String,
    pub python_version: Option<String>,
    pub source_commit: Option<String>,
}

pub fn provenance(resource_dir: Option<&Path>) -> Provenance {
    let manifest = resource_dir
        .map(|d| d.join("syncode").join("BUNDLE_MANIFEST.json"))
        .and_then(|p| std::fs::read(p).ok())
        .filter(|b| b.len() < 256 * 1024)
        .and_then(|b| serde_json::from_slice::<Value>(&b).ok());
    let pick = |ptr: &str, ok: fn(&str) -> bool| {
        manifest
            .as_ref()
            .and_then(|m| m.pointer(ptr))
            .and_then(Value::as_str)
            .filter(|s| ok(s))
            .map(str::to_string)
    };
    Provenance {
        app_version: env!("CARGO_PKG_VERSION").to_string(),
        python_version: pick("/python/version", |s| {
            s.len() <= 16 && s.chars().all(|c| c.is_ascii_digit() || c == '.')
        }),
        source_commit: pick("/source/commit", |s| {
            s.len() == 40 && s.chars().all(|c| c.is_ascii_hexdigit())
        }),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

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
            DoctorOutcome::Transport { code, .. } => code,
            other => panic!("expected transport failure, got {other:?}"),
        }
    }

    #[test]
    fn exit_zero_without_blockers_is_report() {
        assert!(matches!(
            classify(Some(0), &report(0), false),
            DoctorOutcome::Report { exit_code: 0, .. }
        ));
    }

    #[test]
    fn exit_two_with_blockers_is_report() {
        assert!(matches!(
            classify(Some(2), &report(3), false),
            DoctorOutcome::Report { exit_code: 2, .. }
        ));
    }

    #[test]
    fn exit_code_must_match_blockers() {
        assert_eq!(
            transport_code(classify(Some(0), &report(1), false)),
            TransportCode::ContractMismatch
        );
        assert_eq!(
            transport_code(classify(Some(2), &report(0), false)),
            TransportCode::ContractMismatch
        );
    }

    #[test]
    fn doctor_error_is_not_transport() {
        let outcome = classify(Some(1), &error_payload("io-error"), false);
        assert_eq!(
            outcome,
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
            classify(Some(1), &error_payload("future-code"), false),
            DoctorOutcome::DoctorError { .. }
        ));
    }

    #[test]
    fn interrupted_requires_exit_130() {
        assert!(matches!(
            classify(Some(130), &error_payload("interrupted"), false),
            DoctorOutcome::DoctorError { exit_code: 130, .. }
        ));
        assert_eq!(
            transport_code(classify(Some(1), &error_payload("interrupted"), false)),
            TransportCode::ContractMismatch
        );
        assert_eq!(
            transport_code(classify(Some(130), &error_payload("io-error"), false)),
            TransportCode::ContractMismatch
        );
    }

    #[test]
    fn report_on_error_exit_is_mismatch() {
        assert_eq!(
            transport_code(classify(Some(1), &report(0), false)),
            TransportCode::ContractMismatch
        );
        assert_eq!(
            transport_code(classify(Some(0), &error_payload("io-error"), false)),
            TransportCode::ContractMismatch
        );
    }

    #[test]
    fn transport_failures_are_classified() {
        assert_eq!(
            transport_code(classify(Some(1), b"", false)),
            TransportCode::NoOutput
        );
        assert_eq!(
            transport_code(classify(Some(0), b"  \r\n", false)),
            TransportCode::NoOutput
        );
        assert_eq!(
            transport_code(classify(
                Some(0),
                b"Traceback (most recent call last)",
                false
            )),
            TransportCode::InvalidJson
        );
        assert_eq!(
            transport_code(classify(Some(0), &report(0), true)),
            TransportCode::OutputTooLarge
        );
        assert_eq!(
            transport_code(classify(Some(-1073741515), &report(0), false)),
            TransportCode::ProcessFailed
        );
        assert_eq!(
            transport_code(classify(Some(3), &report(0), false)),
            TransportCode::ProcessFailed
        );
        assert_eq!(
            transport_code(classify(None, &report(0), false)),
            TransportCode::ProcessFailed
        );
    }

    #[test]
    fn rejects_trailing_data_and_non_ascii() {
        let mut two = report(0);
        two.extend_from_slice(&report(0));
        assert_eq!(
            transport_code(classify(Some(0), &two, false)),
            TransportCode::InvalidJson
        );
        assert_eq!(
            transport_code(classify(Some(0), "{\"a\":\"é\"}".as_bytes(), false)),
            TransportCode::InvalidJson
        );
    }

    #[test]
    fn rejects_wrong_schema_product_or_shape() {
        let swap = |from: &str, to: &str| {
            String::from_utf8(report(0))
                .unwrap()
                .replace(from, to)
                .into_bytes()
        };
        for body in [
            swap("\"schemaVersion\":1", "\"schemaVersion\":2"),
            swap("\"schemaVersion\":1", "\"schemaVersion\":\"1\""),
            swap("\"schemaVersion\":1", "\"schemaVersion\":1.0"),
            swap("\"product\":\"SYNCODE\"", "\"product\":\"OTHER\""),
            swap("[\"claude\",\"codex\"]", "[\"claude\"]"),
            swap("\"codex\":{}", "\"codex\":[]"),
            b"[1,2]".to_vec(),
        ] {
            assert_eq!(
                transport_code(classify(Some(0), &body, false)),
                TransportCode::ContractMismatch,
                "{}",
                String::from_utf8_lossy(&body)
            );
        }
    }

    #[test]
    fn child_env_is_an_allowlist() {
        let env = child_env(|name| match name {
            "PATH" => Some("C:\\bin".into()),
            "USERPROFILE" => Some("C:\\Users\\x".into()),
            "PYTHONPATH" | "PYTHONHOME" => Some("C:\\evil".into()),
            _ => None,
        });
        let names: Vec<&str> = env.iter().map(|(k, _)| k.as_str()).collect();
        assert_eq!(
            names,
            ["USERPROFILE", "PATH", "NoDefaultCurrentDirectoryInExePath"]
        );
    }

    #[test]
    fn guard_allows_one_run_at_a_time() {
        let first = RunGuard::acquire().expect("first run");
        assert!(RunGuard::acquire().is_none());
        drop(first);
        assert!(RunGuard::acquire().is_some());
    }

    #[test]
    fn missing_runtime_is_bundle_incomplete() {
        let dir = std::env::temp_dir().join(format!("syncode-missing-{}", std::process::id()));
        let outcome = run(&Runtime::from_resource_dir(&dir), &[], DOCTOR_TIMEOUT);
        assert_eq!(transport_code(outcome), TransportCode::BundleIncomplete);
    }

    fn write_manifest(dir: &Path, body: &str) -> PathBuf {
        let path = dir.join("BUNDLE_MANIFEST.json");
        std::fs::create_dir_all(dir).unwrap();
        std::fs::write(&path, body).unwrap();
        path
    }

    #[test]
    fn manifest_files_accepts_only_plain_relative_paths() {
        let dir = std::env::temp_dir().join(format!("syncode-manifest-{}", std::process::id()));
        let ok = write_manifest(
            &dir,
            r#"{"files": {"scripts/sync.py": "x", "skills/ux-ui/SKILL.md": "y"}}"#,
        );
        assert_eq!(manifest_files(&ok).unwrap().len(), 2);
        for bad in [
            r#"{"files": {"../evil.py": "x"}}"#,
            r#"{"files": {"/abs.py": "x"}}"#,
            r#"{"files": {"C:/x.py": "x"}}"#,
            r#"{"files": {"a\\b.py": "x"}}"#,
            r#"{"files": {"a//b.py": "x"}}"#,
            r#"{"files": {}}"#,
            r#"{"nofiles": 1}"#,
            "not json",
        ] {
            let path = write_manifest(&dir, bad);
            assert!(manifest_files(&path).is_none(), "{bad}");
        }
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn partial_bundle_is_incomplete_before_spawning() {
        let Some(runtime) = staged() else { return };
        let copy = scratch("partial");
        for tree in ["python", "syncode"] {
            copy_tree(&runtime.workdir.join(tree), &copy.join(tree));
        }
        std::fs::remove_file(copy.join("syncode").join("scripts").join("sync.py")).unwrap();
        let outcome = run(&Runtime::from_resource_dir(&copy), &[], DOCTOR_TIMEOUT);
        match outcome {
            DoctorOutcome::Transport {
                code: TransportCode::BundleIncomplete,
                detail,
                ..
            } => {
                assert!(detail.contains("sync.py"), "{detail}")
            }
            other => panic!("unexpected outcome {other:?}"),
        }
    }

    fn copy_tree(from: &Path, to: &Path) {
        std::fs::create_dir_all(to).unwrap();
        for entry in std::fs::read_dir(from).unwrap() {
            let entry = entry.unwrap();
            let target = to.join(entry.file_name());
            if entry.file_type().unwrap().is_dir() {
                copy_tree(&entry.path(), &target);
            } else {
                std::fs::copy(entry.path(), target).unwrap();
            }
        }
    }

    // ---- Integration tests against the staged embedded runtime ----------

    fn staged() -> Option<Runtime> {
        let dir = Path::new(env!("CARGO_MANIFEST_DIR")).join("resources");
        let runtime = Runtime::from_resource_dir(&dir);
        if runtime.missing().is_empty() {
            Some(runtime)
        } else {
            eprintln!("SKIP: runtime not staged (python desktop/scripts/stage_runtime.py)");
            None
        }
    }

    fn scratch(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("syncode-rs-{}-{name}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    fn env_with_home(home: &Path) -> Vec<(String, OsString)> {
        child_env(|name| match name {
            "USERPROFILE" => Some(home.as_os_str().to_owned()),
            "HOMEDRIVE" | "HOMEPATH" => None,
            // A poisoned variable in the parent must never reach the child.
            other => std::env::var_os(other),
        })
    }

    #[test]
    fn real_doctor_on_empty_home() {
        let Some(runtime) = staged() else { return };
        let home = scratch("home");
        std::env::set_var("PYTHONPATH", home.join("poison"));
        let outcome = run(&runtime, &env_with_home(&home), DOCTOR_TIMEOUT);
        match outcome {
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
        let outcome = run(&runtime, &env_with_home(&home), DOCTOR_TIMEOUT);
        match outcome {
            DoctorOutcome::Report {
                exit_code: 2,
                report,
            } => assert!(report["summary"]["blockers"].as_u64().unwrap() > 0),
            other => panic!("unexpected outcome {other:?}"),
        }
    }

    fn fake(runtime: &Runtime, name: &str, body: &str) -> Runtime {
        let dir = scratch(name);
        let script = dir.join("fake.py");
        std::fs::write(&script, body).unwrap();
        Runtime {
            workdir: dir,
            ..runtime.with_script(script)
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
        assert_eq!(
            transport_code(run(&rt, &[], DOCTOR_TIMEOUT)),
            TransportCode::NoOutput
        );
    }

    #[test]
    fn timeout_kills_the_process() {
        let Some(runtime) = staged() else { return };
        let rt = fake(&runtime, "slow", "import time\ntime.sleep(30)\n");
        let started = Instant::now();
        assert_eq!(
            transport_code(run(&rt, &[], Duration::from_secs(1))),
            TransportCode::Timeout
        );
        assert!(started.elapsed() < Duration::from_secs(10));
    }

    #[test]
    fn oversized_output_is_rejected_without_deadlock() {
        let Some(runtime) = staged() else { return };
        let rt = fake(
            &runtime,
            "big",
            "import sys\nsys.stdout.write('x' * (3 << 20))\nsys.stderr.write('y' * (1 << 20))\n",
        );
        assert_eq!(
            transport_code(run(&rt, &[], DOCTOR_TIMEOUT)),
            TransportCode::OutputTooLarge
        );
    }

    #[test]
    fn provenance_reads_manifest() {
        let Some(runtime) = staged() else { return };
        let p = provenance(Some(&runtime.workdir));
        assert_eq!(p.python_version.as_deref(), Some("3.12.10"));
        assert!(p.source_commit.is_some());
    }
}
