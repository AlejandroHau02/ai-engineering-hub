//! Closed process runner shared by every SYNCODE Desktop command.
//!
//! The only executable ever started is the bundled `python.exe`, running one
//! of a closed set of bundled scripts ([`Script`]) with arguments that are
//! compile-time string literals (`&'static str`): nothing typed in the webview
//! can become part of a command line. There is no shell, the environment is
//! cleared and rebuilt from an allow-list, stdout/stderr are drained with caps,
//! a timeout kills the process and only one command runs at a time.
//!
//! Each command module (`doctor`, `sync_preview`) owns its fixed argv and its
//! contract classifier; this module only reports raw process results or a
//! [`Transport`] failure.

use serde::Serialize;
use serde_json::Value;
use std::ffi::OsString;
use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::thread;
use std::time::{Duration, Instant};

pub const MAX_STDOUT: usize = 1 << 20;
const MAX_STDERR: usize = 64 * 1024;
const MAX_MANIFEST: u64 = 256 * 1024;
/// `-I`: isolated mode (ignores PYTHON* variables, user site, script dir and
/// cwd on sys.path). `-B`: never write `__pycache__` into the install dir.
const PYTHON_FLAGS: [&str; 2] = ["-I", "-B"];
/// Variables the core legitimately needs: the Windows system root, the user
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

/// The closed set of bundled entry points Desktop may start.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Script {
    Doctor,
    Sync,
}

impl Script {
    fn file_name(self) -> &'static str {
        match self {
            Script::Doctor => "doctor.py",
            Script::Sync => "sync.py",
        }
    }
}

#[derive(Serialize, Debug, Clone, Copy, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub enum TransportCode {
    /// The bundled runtime or core files are missing.
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
    /// Another command is already running.
    Busy,
}

/// The command could not run or its answer cannot be trusted. Never a result
/// of the SYNCODE core itself.
#[derive(Serialize, Debug, Clone, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct Transport {
    pub code: TransportCode,
    pub detail: String,
    pub exit_code: Option<i32>,
}

impl Transport {
    pub fn new(code: TransportCode, detail: impl Into<String>, exit_code: Option<i32>) -> Self {
        Transport {
            code,
            detail: detail.into(),
            exit_code,
        }
    }
}

/// Relative paths declared in `BUNDLE_MANIFEST.json`'s `files` map. `None`
/// when the manifest is unreadable, oversized, malformed or declares a path
/// that is not a plain relative path (no `..`, drive, root or backslash).
fn manifest_files(manifest: &Path) -> Option<Vec<String>> {
    if std::fs::metadata(manifest).ok()?.len() > MAX_MANIFEST {
        return None;
    }
    let value: Value = serde_json::from_slice(&std::fs::read(manifest).ok()?).ok()?;
    let files = value.get("files")?.as_object()?;
    if files.is_empty() || !files.keys().all(|k| is_plain_relative(k)) {
        return None;
    }
    Some(files.keys().cloned().collect())
}

/// `a/b/c.ext` with ASCII `[A-Za-z0-9._-]` segments; no empty, `.` or `..`.
pub fn is_plain_relative(rel: &str) -> bool {
    !rel.is_empty()
        && rel
            .split('/')
            .all(|part| !part.is_empty() && part != "." && part != "..")
        && rel
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || "._-/".contains(c))
}

/// Locations of the bundled runtime, derived only from the resource dir.
#[derive(Debug, Clone)]
pub struct Runtime {
    pub python: PathBuf,
    pub workdir: PathBuf,
    scripts: PathBuf,
    required: Vec<PathBuf>,
    #[cfg(test)]
    script_override: Option<PathBuf>,
}

impl Runtime {
    pub fn from_resource_dir(dir: &Path) -> Self {
        let python = dir.join("python").join("python.exe");
        let scripts = dir.join("syncode").join("scripts");
        let manifest = dir.join("syncode").join("BUNDLE_MANIFEST.json");
        let mut required = vec![
            python.clone(),
            dir.join("python").join("python312._pth"),
            dir.join("python").join("python312.dll"),
            dir.join("python").join("python312.zip"),
            scripts.join(Script::Doctor.file_name()),
            scripts.join(Script::Sync.file_name()),
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
            None if manifest.exists() => {
                required.push(dir.join("syncode").join("BUNDLE_MANIFEST.json (invalid)"))
            }
            None => {}
        }
        Runtime {
            python,
            workdir: dir.to_path_buf(),
            scripts,
            required,
            #[cfg(test)]
            script_override: None,
        }
    }

    fn script_path(&self, script: Script) -> PathBuf {
        #[cfg(test)]
        if let Some(path) = &self.script_override {
            return path.clone();
        }
        self.scripts.join(script.file_name())
    }

    /// Test hook: run an arbitrary script with the embedded interpreter.
    #[cfg(test)]
    pub fn with_script(&self, script: PathBuf, workdir: PathBuf) -> Self {
        Runtime {
            required: vec![self.python.clone(), script.clone()],
            script_override: Some(script),
            workdir,
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

/// Raw result of a process that ran to completion.
#[derive(Debug)]
pub struct Exited {
    pub code: Option<i32>,
    pub stdout: Vec<u8>,
    pub overflow: bool,
    pub stderr_len: usize,
}

impl Exited {
    /// Annotate a transport failure with the (never shown) stderr size.
    pub fn annotate(&self, mut failure: Transport) -> Transport {
        if self.stderr_len > 0 {
            failure.detail = format!(
                "{} (stderr: {} bytes, not shown)",
                failure.detail, self.stderr_len
            );
        }
        failure
    }
}

/// Start `script` on the embedded interpreter with literal arguments.
pub fn execute(
    runtime: &Runtime,
    script: Script,
    args: &[&'static str],
    env: &[(String, OsString)],
    timeout: Duration,
) -> Result<Exited, Transport> {
    let missing = runtime.missing();
    if !missing.is_empty() {
        return Err(Transport::new(
            TransportCode::BundleIncomplete,
            missing.join(", "),
            None,
        ));
    }
    let mut command = Command::new(&runtime.python);
    command
        .args(PYTHON_FLAGS)
        .arg(runtime.script_path(script))
        .args(args)
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
    let mut child = command
        .spawn()
        .map_err(|error| Transport::new(TransportCode::SpawnFailed, error.to_string(), None))?;
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
                return Err(Transport::new(
                    TransportCode::SpawnFailed,
                    error.to_string(),
                    None,
                ));
            }
        }
    };
    let out = stdout.and_then(|h| h.join().ok()).unwrap_or(Captured {
        bytes: vec![],
        overflow: false,
    });
    let stderr_len = stderr
        .and_then(|h| h.join().ok())
        .map(|c| c.bytes.len())
        .unwrap_or(0);
    match status {
        None => Err(Transport::new(
            TransportCode::Timeout,
            format!(
                "no response after {} s; process terminated",
                timeout.as_secs()
            ),
            None,
        )),
        Some(status) => Ok(Exited {
            code: status.code(),
            stdout: out.bytes,
            overflow: out.overflow,
            stderr_len,
        }),
    }
}

/// Common schemaVersion 1 framing checks shared by every command.
///
/// Returns the exit code and the decoded top-level object, or a transport
/// failure: overflow, exit code outside `allowed`, empty stdout, non-ASCII,
/// invalid or trailing JSON, non-object, `schemaVersion` not the integer 1, or
/// `product` not `SYNCODE`.
pub fn parse_contract(
    exited: &Exited,
    allowed: &[i32],
) -> Result<(i32, serde_json::Map<String, Value>), Transport> {
    use TransportCode::*;
    if exited.overflow {
        return Err(Transport::new(
            OutputTooLarge,
            format!("more than {MAX_STDOUT} bytes"),
            exited.code,
        ));
    }
    let code = match exited.code {
        Some(code) if allowed.contains(&code) => code,
        other => {
            return Err(Transport::new(
                ProcessFailed,
                "exit code outside the contract",
                other,
            ))
        }
    };
    let text = exited.stdout.trim_ascii();
    if text.is_empty() {
        return Err(Transport::new(
            NoOutput,
            "no JSON on stdout; state unknown",
            Some(code),
        ));
    }
    if !exited.stdout.is_ascii() {
        return Err(Transport::new(
            InvalidJson,
            "stdout is not ASCII",
            Some(code),
        ));
    }
    // from_slice rejects trailing data, so a second object is invalid.
    let payload: Value = serde_json::from_slice(text)
        .map_err(|error| Transport::new(InvalidJson, error.to_string(), Some(code)))?;
    let Value::Object(object) = payload else {
        return Err(Transport::new(
            ContractMismatch,
            "response is not a JSON object",
            Some(code),
        ));
    };
    // as_u64 is None for "1" and for 1.0: only the integer 1 is accepted.
    if object.get("schemaVersion").and_then(Value::as_u64) != Some(1) {
        return Err(Transport::new(
            ContractMismatch,
            "schemaVersion is not 1",
            Some(code),
        ));
    }
    if object.get("product").and_then(Value::as_str) != Some("SYNCODE") {
        return Err(Transport::new(
            ContractMismatch,
            "product is not SYNCODE",
            Some(code),
        ));
    }
    Ok((code, object))
}

/// Process-wide guard: only one command at a time.
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
        .filter(|b| (b.len() as u64) < MAX_MANIFEST)
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
pub mod testing {
    //! Helpers shared by command-module tests (staged runtime, scratch dirs).
    use super::*;

    pub fn staged() -> Option<Runtime> {
        let dir = Path::new(env!("CARGO_MANIFEST_DIR")).join("resources");
        let runtime = Runtime::from_resource_dir(&dir);
        if runtime.missing().is_empty() {
            Some(runtime)
        } else {
            eprintln!("SKIP: runtime not staged (python desktop/scripts/stage_runtime.py)");
            None
        }
    }

    pub fn scratch(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("syncode-rs-{}-{name}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    /// Allow-listed environment with the user home redirected to `home`.
    pub fn env_with_home(home: &Path) -> Vec<(String, OsString)> {
        child_env(|name| match name {
            "USERPROFILE" => Some(home.as_os_str().to_owned()),
            "HOMEDRIVE" | "HOMEPATH" => None,
            other => std::env::var_os(other),
        })
    }

    /// Runtime that runs `body` as its script, in a scratch directory.
    pub fn fake(runtime: &Runtime, name: &str, body: &str) -> Runtime {
        let dir = scratch(name);
        let script = dir.join("fake.py");
        std::fs::write(&script, body).unwrap();
        runtime.with_script(script, dir)
    }

    /// Recursive snapshot (relative path → bytes, mtime) to prove no writes.
    pub fn snapshot(root: &Path) -> Vec<(PathBuf, Option<Vec<u8>>, std::time::SystemTime)> {
        let mut out = Vec::new();
        let mut stack = vec![root.to_path_buf()];
        while let Some(dir) = stack.pop() {
            for entry in std::fs::read_dir(&dir).unwrap() {
                let entry = entry.unwrap();
                // fs::metadata opens the file: DirEntry metadata on Windows comes
                // from the directory index, which NTFS updates lazily.
                let meta = std::fs::metadata(entry.path()).unwrap();
                let rel = entry.path().strip_prefix(root).unwrap().to_path_buf();
                if meta.is_dir() {
                    stack.push(entry.path());
                    out.push((rel, None, meta.modified().unwrap()));
                } else {
                    out.push((
                        rel,
                        Some(std::fs::read(entry.path()).unwrap()),
                        meta.modified().unwrap(),
                    ));
                }
            }
        }
        out.sort_by(|a, b| a.0.cmp(&b.0));
        out
    }

    pub fn copy_tree(from: &Path, to: &Path) {
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
}

#[cfg(test)]
mod tests {
    use super::testing::*;
    use super::*;

    fn exited(code: i32, stdout: &[u8]) -> Exited {
        Exited {
            code: Some(code),
            stdout: stdout.to_vec(),
            overflow: false,
            stderr_len: 0,
        }
    }

    fn failure(result: Result<(i32, serde_json::Map<String, Value>), Transport>) -> TransportCode {
        result.expect_err("expected a transport failure").code
    }

    #[test]
    fn framing_rejects_transport_failures() {
        use TransportCode::*;
        let ok = br#"{"schemaVersion":1,"product":"SYNCODE"}"#;
        assert!(parse_contract(&exited(0, ok), &[0]).is_ok());
        assert_eq!(
            failure(parse_contract(&exited(3, ok), &[0, 1])),
            ProcessFailed
        );
        assert_eq!(
            failure(parse_contract(&exited(-1073741515, ok), &[0])),
            ProcessFailed
        );
        assert_eq!(
            failure(parse_contract(&exited(1, b" \r\n"), &[1])),
            NoOutput
        );
        assert_eq!(
            failure(parse_contract(&exited(0, b"Traceback (most recent"), &[0])),
            InvalidJson
        );
        assert_eq!(
            failure(parse_contract(&exited(0, "{\"a\":\"é\"}".as_bytes()), &[0])),
            InvalidJson
        );
        let mut two = ok.to_vec();
        two.extend_from_slice(ok);
        assert_eq!(failure(parse_contract(&exited(0, &two), &[0])), InvalidJson);
        assert_eq!(
            failure(parse_contract(&exited(0, b"[1]"), &[0])),
            ContractMismatch
        );
        for body in [
            r#"{"schemaVersion":2,"product":"SYNCODE"}"#,
            r#"{"schemaVersion":"1","product":"SYNCODE"}"#,
            r#"{"schemaVersion":1.0,"product":"SYNCODE"}"#,
            r#"{"schemaVersion":1,"product":"OTHER"}"#,
        ] {
            assert_eq!(
                failure(parse_contract(&exited(0, body.as_bytes()), &[0])),
                ContractMismatch,
                "{body}"
            );
        }
        let mut big = exited(0, ok);
        big.overflow = true;
        assert_eq!(failure(parse_contract(&big, &[0])), OutputTooLarge);
        let none = Exited {
            code: None,
            ..exited(0, ok)
        };
        assert_eq!(failure(parse_contract(&none, &[0])), ProcessFailed);
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
        let result = execute(
            &Runtime::from_resource_dir(&dir),
            Script::Doctor,
            &[],
            &[],
            Duration::from_secs(5),
        );
        assert_eq!(
            result.expect_err("missing").code,
            TransportCode::BundleIncomplete
        );
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
        std::fs::remove_file(
            copy.join("syncode")
                .join("scripts")
                .join("plan_identity.py"),
        )
        .unwrap();
        let result = execute(
            &Runtime::from_resource_dir(&copy),
            Script::Sync,
            &[],
            &[],
            Duration::from_secs(5),
        );
        let failure = result.expect_err("partial bundle");
        assert_eq!(failure.code, TransportCode::BundleIncomplete);
        assert!(
            failure.detail.contains("plan_identity.py"),
            "{}",
            failure.detail
        );
    }

    #[test]
    fn timeout_kills_the_process() {
        let Some(runtime) = staged() else { return };
        let rt = fake(&runtime, "slow", "import time\ntime.sleep(30)\n");
        let started = Instant::now();
        let result = execute(&rt, Script::Doctor, &[], &[], Duration::from_secs(1));
        assert_eq!(result.expect_err("timeout").code, TransportCode::Timeout);
        assert!(started.elapsed() < Duration::from_secs(10));
    }

    #[test]
    fn oversized_output_is_capped_without_deadlock() {
        let Some(runtime) = staged() else { return };
        let rt = fake(
            &runtime,
            "big",
            "import sys\nsys.stdout.write('x' * (3 << 20))\nsys.stderr.write('y' * (1 << 20))\n",
        );
        let exited = execute(&rt, Script::Doctor, &[], &[], Duration::from_secs(20)).unwrap();
        assert!(exited.overflow);
        assert_eq!(exited.stdout.len(), MAX_STDOUT);
    }

    #[test]
    fn provenance_reads_manifest() {
        let Some(runtime) = staged() else { return };
        let p = provenance(Some(&runtime.workdir));
        assert_eq!(p.python_version.as_deref(), Some("3.12.10"));
        assert!(p.source_commit.is_some());
    }
}
