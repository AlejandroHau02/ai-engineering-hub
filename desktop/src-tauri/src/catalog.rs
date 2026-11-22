//! `read_catalog`: SYNCODE components and other installed entries (read-only).
//!
//! Runs the bundled `catalog.py --format json` (literal argv, no home
//! override) and validates it into typed data: known shapes, bounded sizes,
//! managed relative paths. Installed status per client stays Doctor's; the UI
//! joins both. Every string is untrusted data rendered with `textContent`.

use crate::runner::{self, is_plain_relative, Exited, Runtime, Script, Transport, TransportCode};
use serde::{Deserialize, Serialize};
use std::ffi::OsString;
use std::time::Duration;

pub const CATALOG_TIMEOUT: Duration = Duration::from_secs(20);
pub const CATALOG_ARGS: [&str; 2] = ["--format", "json"];
const MAX_COMPONENTS: usize = 50;
const MAX_ENTRIES: usize = 100;
const MAX_TEXT: usize = 1200;
const MAX_FOCUS: usize = 10;

#[derive(Deserialize, Serialize, Debug, Clone, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct Paths {
    pub claude: String,
    pub codex: String,
}

#[derive(Deserialize, Serialize, Debug, Clone, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct Component {
    pub kind: String,
    pub name: String,
    pub description: Option<String>,
    #[serde(default)]
    pub skill: Option<String>,
    #[serde(default)]
    pub focus: Vec<Option<String>>,
    #[serde(default)]
    pub deliverable: Option<String>,
    pub paths: Paths,
}

#[derive(Deserialize, Serialize, Debug, Clone, PartialEq)]
pub struct Entry {
    pub name: String,
    pub link: bool,
    pub description: Option<String>,
}

#[derive(Deserialize, Serialize, Debug, Clone, PartialEq)]
pub struct Folder {
    pub entries: Vec<Entry>,
    pub truncated: bool,
}

#[derive(Deserialize, Serialize, Debug, Clone, PartialEq)]
pub struct ClientFolders {
    pub skills: Folder,
    pub agents: Folder,
}

#[derive(Deserialize, Serialize, Debug, Clone, PartialEq)]
pub struct Unmanaged {
    pub claude: ClientFolders,
    pub codex: ClientFolders,
}

#[derive(Deserialize, Debug)]
struct Response {
    status: String,
    home: Option<String>,
    components: Vec<Component>,
    unmanaged: serde_json::Value,
    #[serde(default)]
    error: Option<serde_json::Value>,
}

#[derive(Serialize, Debug, PartialEq)]
#[serde(tag = "kind", rename_all = "camelCase")]
pub enum CatalogOutcome {
    #[serde(rename_all = "camelCase")]
    Catalog {
        home: String,
        components: Vec<Component>,
        unmanaged: Unmanaged,
    },
    /// The core reported its own error (e.g. an unreadable home).
    #[serde(rename_all = "camelCase")]
    CatalogError { code: String, message: String },
    /// The catalog could not be read or its answer cannot be trusted.
    Transport(Transport),
}

pub fn run(runtime: &Runtime, env: &[(String, OsString)]) -> CatalogOutcome {
    match runner::execute(
        runtime,
        Script::Catalog,
        &CATALOG_ARGS,
        None,
        env,
        CATALOG_TIMEOUT,
    ) {
        Ok(exited) => match classify(&exited) {
            CatalogOutcome::Transport(failure) => {
                CatalogOutcome::Transport(exited.annotate(failure))
            }
            other => other,
        },
        Err(failure) => CatalogOutcome::Transport(failure),
    }
}

/// Printable text only: no control, bidi-override or zero-width characters
/// (the core sanitizes; Rust re-checks before anything reaches the webview).
fn printable(text: &str) -> bool {
    !text.chars().any(|c| {
        c.is_control()
            || matches!(c, '\u{200B}'..='\u{200F}' | '\u{202A}'..='\u{202E}'
                | '\u{2066}'..='\u{2069}' | '\u{FEFF}')
    })
}

fn short(text: &Option<String>) -> bool {
    text.as_ref()
        .map_or(true, |t| t.chars().count() <= MAX_TEXT && printable(t))
}

fn managed_path(path: &str, roots: &[&str]) -> bool {
    is_plain_relative(path) && roots.iter().any(|root| path.starts_with(root))
}

fn valid_folder(folder: &Folder) -> bool {
    folder.entries.len() <= MAX_ENTRIES
        && folder.entries.iter().all(|e| {
            !e.name.is_empty()
                && e.name.chars().count() <= 64
                && printable(&e.name)
                && short(&e.description)
        })
}

/// Pure contract check of `catalog.py --format json`.
pub fn classify(exited: &Exited) -> CatalogOutcome {
    let (code, payload) = match runner::parse_contract(exited, &[0, 1]) {
        Ok(parsed) => parsed,
        Err(failure) => return CatalogOutcome::Transport(failure),
    };
    let mismatch = |why: &str| {
        CatalogOutcome::Transport(Transport::new(
            TransportCode::ContractMismatch,
            why,
            Some(code),
        ))
    };
    let response: Response = match serde_json::from_value(serde_json::Value::Object(payload)) {
        Ok(response) => response,
        Err(error) => return mismatch(&format!("unexpected catalog shape: {error}")),
    };
    if code == 1 && response.status == "error" {
        let error = response.error.unwrap_or_default();
        let field = |key: &str| {
            error
                .get(key)
                .and_then(serde_json::Value::as_str)
                .unwrap_or_default()
                .to_string()
        };
        let error_code = field("code");
        if error_code.is_empty() {
            return mismatch("error exit without error.code");
        }
        return CatalogOutcome::CatalogError {
            code: error_code,
            message: field("message"),
        };
    }
    if code != 0 || response.status != "ok" {
        return mismatch("exit code inconsistent with status");
    }
    let Some(home) = response.home else {
        return mismatch("home missing");
    };
    if response.components.is_empty() || response.components.len() > MAX_COMPONENTS {
        return mismatch("unexpected number of components");
    }
    for component in &response.components {
        let (claude_roots, codex_roots): (&[&str], &[&str]) = match component.kind.as_str() {
            "skill" => (&[".claude/skills/"], &[".agents/skills/"]),
            "specialist" => (&[".claude/agents/"], &[".codex/agents/"]),
            _ => return mismatch("unknown component kind"),
        };
        if component.name.is_empty()
            || component.name.len() > 64
            || !component
                .name
                .chars()
                .all(|c| c.is_ascii_alphanumeric() || c == '-')
            || !short(&component.description)
            || !short(&component.skill)
            || !short(&component.deliverable)
            || component.focus.len() > MAX_FOCUS
            || !component.focus.iter().all(short)
            || !managed_path(&component.paths.claude, claude_roots)
            || !managed_path(&component.paths.codex, codex_roots)
        {
            return mismatch("component outside the expected shape");
        }
    }
    let unmanaged: Unmanaged = match serde_json::from_value(response.unmanaged) {
        Ok(unmanaged) => unmanaged,
        Err(error) => return mismatch(&format!("unexpected unmanaged listing: {error}")),
    };
    for folders in [&unmanaged.claude, &unmanaged.codex] {
        if !valid_folder(&folders.skills) || !valid_folder(&folders.agents) {
            return mismatch("unmanaged listing outside bounds");
        }
    }
    CatalogOutcome::Catalog {
        home,
        components: response.components,
        unmanaged,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::runner::testing::*;
    use serde_json::{json, Value};

    /// Serialize like the core does (`ensure_ascii`): non-ASCII as \uXXXX.
    fn exited(code: i32, value: &Value) -> Exited {
        let mut ascii = String::new();
        for c in serde_json::to_string(value).unwrap().chars() {
            if c.is_ascii() {
                ascii.push(c);
            } else {
                let mut units = [0u16; 2];
                for unit in c.encode_utf16(&mut units) {
                    ascii.push_str(&format!("\\u{unit:04x}"));
                }
            }
        }
        Exited {
            code: Some(code),
            stdout: ascii.into_bytes(),
            overflow: false,
            stderr_len: 0,
        }
    }

    fn folder() -> Value {
        json!({"entries": [{"name": "mine", "link": false, "description": "<b>x</b>"}], "truncated": false})
    }

    fn body() -> Value {
        json!({
            "schemaVersion": 1, "product": "SYNCODE", "status": "ok", "home": "C:\\Users\\x",
            "components": [
                {"kind": "skill", "name": "ux-ui", "description": "d",
                 "paths": {"claude": ".claude/skills/ux-ui/SKILL.md", "codex": ".agents/skills/ux-ui/SKILL.md"}},
                {"kind": "specialist", "name": "hub-ux-auditor", "description": "d", "skill": "ux-ui",
                 "focus": ["a", "b"], "deliverable": "x", "extra": "tolerated",
                 "paths": {"claude": ".claude/agents/hub-ux-auditor.md", "codex": ".codex/agents/hub-ux-auditor.toml"}}
            ],
            "unmanaged": {"claude": {"skills": folder(), "agents": folder()},
                          "codex": {"skills": folder(), "agents": folder()}},
            "error": null
        })
    }

    fn is_mismatch(outcome: CatalogOutcome) -> bool {
        matches!(outcome, CatalogOutcome::Transport(t) if t.code == TransportCode::ContractMismatch)
    }

    #[test]
    fn valid_catalog_is_accepted() {
        match classify(&exited(0, &body())) {
            CatalogOutcome::Catalog {
                components,
                unmanaged,
                ..
            } => {
                assert_eq!(components.len(), 2);
                assert_eq!(
                    unmanaged.claude.skills.entries[0].description.as_deref(),
                    Some("<b>x</b>")
                );
            }
            other => panic!("{other:?}"),
        }
    }

    #[test]
    fn unexpected_shapes_are_rejected() {
        for (pointer, replacement) in [
            ("/status", json!("error")),
            ("/components/0/kind", json!("plugin")),
            ("/components/0/paths/claude", json!("../evil")),
            (
                "/components/0/paths/claude",
                json!(".claude/agents/ux-ui.md"),
            ),
            ("/components/1/paths/codex", json!("C:/x.toml")),
            ("/components/0/name", json!("bad name")),
            ("/components/0/description", json!("x".repeat(MAX_TEXT + 1))),
            (
                "/unmanaged/codex/agents/entries/0/name",
                json!("n".repeat(65)),
            ),
            ("/unmanaged/claude", json!(null)),
            ("/components", json!([])),
            (
                "/unmanaged/claude/skills/entries/0/name",
                json!("evil\u{202e}txt"),
            ),
            (
                "/unmanaged/codex/skills/entries/0/description",
                json!("bell\u{7}"),
            ),
        ] {
            let mut value = body();
            *value.pointer_mut(pointer).unwrap() = replacement.clone();
            assert!(
                is_mismatch(classify(&exited(0, &value))),
                "{pointer} = {replacement}"
            );
        }
        // An error exit is never a catalog; a core error is reported as such.
        assert!(is_mismatch(classify(&exited(1, &body()))));
        let error = json!({"schemaVersion": 1, "product": "SYNCODE", "status": "error",
            "home": null, "components": [], "unmanaged": {},
            "error": {"type": "OSError", "code": "io-error", "message": "denied"}});
        assert!(matches!(
            classify(&exited(1, &error)),
            CatalogOutcome::CatalogError { ref code, .. } if code == "io-error"
        ));
    }

    #[test]
    fn real_catalog_from_the_staged_bundle() {
        let Some(runtime) = staged() else { return };
        let home = scratch("catalog-real");
        std::fs::create_dir_all(home.join(".claude").join("skills").join("mine")).unwrap();
        let before = snapshot(&home);
        match run(&runtime, &env_with_home(&home)) {
            CatalogOutcome::Catalog {
                components,
                unmanaged,
                ..
            } => {
                assert_eq!(components.len(), 10);
                assert!(components
                    .iter()
                    .any(|c| c.name == "hub-application-security-auditor"));
                assert_eq!(unmanaged.claude.skills.entries[0].name, "mine");
            }
            other => panic!("{other:?}"),
        }
        assert_eq!(snapshot(&home), before, "catalog must not write");
    }
}
