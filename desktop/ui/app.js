// SYNCODE Desktop — Diagnóstico. Renders the validated Doctor outcome from the
// single `run_doctor` command. All data reaches the DOM through textContent.
"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const SVG = "http://www.w3.org/2000/svg";

  // Raw Doctor statuses → Spanish label and severity level.
  const STATUS = {
    current: ["Al día", "ok"],
    missing: ["No instalado", "neutral"],
    unmanaged: ["No gestionado por SYNCODE", "neutral"],
    "upgrade-available": ["Actualización disponible", "warn"],
    modified: ["Modificado localmente", "block"],
    "unsafe-or-unreadable": ["Ruta insegura o ilegible", "block"],
  };
  const EXECUTABLE = {
    found: ["CLI detectada en PATH", "ok"],
    "not-found": ["CLI no encontrada en PATH", "neutral"],
  };
  const TRANSACTION = {
    clear: ["Sin transacción pendiente", "ok"],
    "pending-recovery": ["Recuperación pendiente", "block"],
    unsafe: ["Área de transacción insegura", "block"],
  };
  const LOCK = {
    absent: ["Sin archivo lock", "ok"],
    recognized: ["Archivo lock presente (puede ser residual)", "neutral"],
    unexpected: ["Archivo lock con contenido inesperado", "block"],
    unsafe: ["Archivo lock en ruta insegura", "block"],
  };
  const NEXT_STEP = {
    modified: "Revisa los cambios locales; SYNCODE no los sobrescribirá.",
    "unsafe-or-unreadable": "Inspección manual de la ruta (enlace simbólico, junction o permisos).",
    "transaction:pending-recovery": "Ejecuta «python scripts/sync.py --recover» y vuelve a diagnosticar.",
    "transaction:unsafe": "Revisión manual del área de transacción (docs/TRANSACTION_RECOVERY.md). No uses --recover.",
    "lock:unexpected": "Inspección manual del archivo lock antes de cualquier sincronización.",
    "lock:unsafe": "Inspección manual de la ruta del archivo lock.",
  };
  const CLIENTS = {
    claude: { name: "Claude Code", instructions: "~/.claude/CLAUDE.md" },
    codex: { name: "Codex", instructions: "~/.codex/AGENTS.md" },
  };
  const TRANSPORT = {
    bundleIncomplete: ["Falta el entorno de ejecución integrado",
      "La instalación de SYNCODE Desktop está incompleta: no se encontró el runtime Python o el núcleo de Doctor. Reinstala la aplicación."],
    spawnFailed: ["La app no pudo iniciar Doctor",
      "El sistema no permitió iniciar el runtime Python integrado."],
    timeout: ["Doctor no respondió a tiempo",
      "El diagnóstico superó el tiempo máximo y se detuvo. Ningún archivo fue modificado."],
    outputTooLarge: ["Respuesta demasiado grande",
      "La respuesta de Doctor superó el tamaño permitido y se descartó."],
    noOutput: ["Doctor terminó sin respuesta",
      "El proceso terminó sin un resultado JSON. El estado de la instalación es desconocido."],
    invalidJson: ["Respuesta no válida",
      "La respuesta de Doctor no es JSON válido y se descartó."],
    contractMismatch: ["Versión de Doctor incompatible",
      "La respuesta no cumple el contrato schemaVersion 1 que espera esta versión de la app."],
    processFailed: ["El proceso terminó de forma inesperada",
      "El runtime Python finalizó con un código fuera del contrato de Doctor."],
    busy: ["Diagnóstico en curso",
      "Ya hay un diagnóstico ejecutándose. Espera a que termine."],
    bridge: ["La interfaz no puede comunicarse con la app",
      "El puente con SYNCODE Desktop no está disponible en esta ventana."],
  };

  const state = { runId: 0, running: false, lastGood: null, current: null, userHome: null };

  // ---- small DOM helpers ---------------------------------------------------
  function el(tag, attrs, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs || {})) {
      if (value === null || value === undefined || value === false) continue;
      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = value;
      else node.setAttribute(key, value === true ? "" : String(value));
    }
    for (const child of children.flat()) {
      if (child === null || child === undefined || child === false) continue;
      node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
  }

  const ICONS = {
    ok: "M5 12.5l4.5 4.5L19 7.5",
    warn: "M12 4l9 16H3zM12 10v4M12 17.2v.1",
    block: "M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0-18M8.5 8.5l7 7M15.5 8.5l-7 7",
    neutral: "M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0-18M8 12h8",
    unknown: "M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0-18M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.7.3-1 .9-1 1.7M12 16.8v.1",
    failure: "M4 6h16v12H4zM4 10h16M8 14h3",
  };
  function icon(level) {
    const svg = document.createElementNS(SVG, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("class", "icon");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("focusable", "false");
    const path = document.createElementNS(SVG, "path");
    path.setAttribute("d", ICONS[level] || ICONS.unknown);
    path.setAttribute("fill", "none");
    path.setAttribute("stroke", "currentColor");
    path.setAttribute("stroke-width", "2");
    path.setAttribute("stroke-linecap", "round");
    path.setAttribute("stroke-linejoin", "round");
    svg.append(path);
    return svg;
  }

  function describe(table, raw) {
    const known = Object.prototype.hasOwnProperty.call(table, raw) ? table[raw] : null;
    if (known) return { label: known[0], level: known[1], raw };
    // Additive contract: an unknown status is never shown as healthy.
    return { label: `Estado desconocido (${String(raw)})`, level: "unknown", raw };
  }

  function statusChip(info) {
    return el("span", { class: `status level-${info.level}` }, icon(info.level),
      el("span", { class: "label", text: info.label }));
  }

  // ---- privacy: shorten the user's home to "~" unless asked otherwise ------
  function showFull() { return $("show-paths").checked; }
  function shorten(text) {
    if (typeof text !== "string" || showFull()) return text;
    const home = state.userHome;
    if (!home) return text;
    const lower = text.toLowerCase();
    const needle = home.toLowerCase();
    let out = "";
    let index = 0;
    for (;;) {
      const at = lower.indexOf(needle, index);
      if (at < 0) break;
      out += text.slice(index, at) + "~";
      index = at + home.length;
    }
    return out + text.slice(index);
  }

  // ---- derive what needs review from a validated report --------------------
  function collect(report) {
    const items = [];
    const clients = [];
    for (const key of ["claude", "codex"]) {
      const client = report.clients[key] || {};
      const meta = CLIENTS[key];
      const rows = [];
      const exe = client.executable || {};
      rows.push({ id: `${key}-cli`, name: "CLI", hint: exe.path || null,
        info: describe(EXECUTABLE, exe.status) });
      rows.push({ id: `${key}-instructions`, name: "Instrucciones globales", hint: meta.instructions,
        info: describe(STATUS, (client.instructions || {}).status) });
      const groups = [];
      for (const [field, title] of [["skills", "Skills"], ["specialists", "Especialistas"]]) {
        const entries = Object.entries(client[field] || {}).map(([name, value]) => ({
          id: `${key}-${field}-${name}`, name, info: describe(STATUS, (value || {}).status),
        }));
        groups.push({ field, title, entries });
      }
      for (const row of [rows[1], ...groups.flatMap((g) => g.entries)]) {
        if (row.info.level === "block" || row.info.level === "unknown") {
          items.push({ where: `${meta.name} · ${row.name}`, info: row.info, anchor: row.id,
            next: NEXT_STEP[row.info.raw] || "Revisión manual." });
        }
      }
      clients.push({ key, meta, rows, groups });
    }
    const tx = describe(TRANSACTION, report.transaction);
    const lock = describe(LOCK, report.lock);
    for (const [prefix, info, label] of [["transaction", tx, "Transacción"], ["lock", lock, "Archivo lock"]]) {
      if (info.level === "block" || info.level === "unknown") {
        items.push({ where: `Sincronización · ${label}`, info, anchor: `sync-${prefix}`,
          next: NEXT_STEP[`${prefix}:${info.raw}`] || "Revisión manual." });
      }
    }
    return { items, clients, tx, lock };
  }

  function plural(n, one, many) { return `${n} ${n === 1 ? one : many}`; }

  // ---- renderers --------------------------------------------------------------
  function renderReport(run, at) {
    const report = run.outcome.report;
    const summary = report.summary || {};
    const blockers = Number(summary.blockers) || 0;
    const derived = collect(report);
    const frag = document.createDocumentFragment();

    const notes = [];
    if (summary.upgradeAvailable) notes.push(plural(summary.upgradeAvailable, "actualización disponible", "actualizaciones disponibles"));
    if (summary.missing) notes.push(plural(summary.missing, "componente no instalado", "componentes no instalados"));
    if (summary.unmanaged) notes.push(plural(summary.unmanaged, "archivo no gestionado", "archivos no gestionados"));
    const cliMissing = derived.clients.filter((c) => c.rows[0].info.raw === "not-found").length;
    if (cliMissing) notes.push(plural(cliMissing, "CLI no encontrada", "CLI no encontradas"));
    if (summary.current) notes.push(plural(summary.current, "componente al día", "componentes al día"));

    const level = blockers > 0 ? "block" : "ok";
    const title = blockers > 0
      ? `Diagnóstico completado: ${plural(blockers, "elemento requiere revisión", "elementos requieren revisión")}`
      : "Diagnóstico completado: sin elementos que requieran revisión";
    frag.append(el("section", { class: `panel verdict level-${level}`, "aria-labelledby": "verdict-title" },
      icon(level),
      el("div", {},
        el("h2", { id: "verdict-title", text: title }),
        el("p", { class: "sub", text: notes.length ? notes.join(" · ") : "Sin componentes informados." }))));

    frag.append(el("section", { class: "panel strip", "aria-labelledby": "sync-title" },
      el("h2", { id: "sync-title", text: "Estado de sincronización" }),
      el("span", { id: "sync-transaction" }, "Transacción: ", statusChip(derived.tx)),
      el("span", { id: "sync-lock" }, "Archivo lock: ", statusChip(derived.lock))));

    if (blockers > 0 || derived.items.length) {
      const list = el("ul", {}, derived.items.map((item) => el("li", {},
        el("div", { class: "what" }, jumpLink(item.anchor, item.where), " — ", statusChip(item.info)),
        el("p", { class: "next", text: item.next }))));
      const extra = blockers - derived.items.filter((i) => i.info.level === "block").length;
      frag.append(el("section", { class: "panel review", "aria-labelledby": "review-title" },
        el("h2", { id: "review-title", text: "Requieren revisión" }),
        list,
        extra > 0 ? el("p", { class: "note", text: `Doctor informa ${plural(extra, "elemento adicional", "elementos adicionales")} sin detalle en esta versión de la app.` }) : null,
        el("p", { class: "note", text: "Esta pantalla no aplica cambios. Las acciones sugeridas se realizan desde la línea de comandos." })));
    }

    const cards = el("div", { class: "clients" });
    for (const client of derived.clients) {
      const all = [client.rows[1], ...client.groups.flatMap((g) => g.entries)];
      const ok = all.filter((r) => r.info.level === "ok").length;
      const card = el("section", { class: "panel client", "aria-labelledby": `${client.key}-title` },
        el("header", {}, el("h3", { id: `${client.key}-title`, text: client.meta.name }),
          el("span", { class: "count", text: `${ok} de ${all.length} al día` })));
      card.append(el("ul", { class: "rows" }, client.rows.map(rowNode)));
      for (const group of client.groups) card.append(groupNode(client.key, group));
      cards.append(card);
    }
    frag.append(cards);
    frag.append(el("p", { class: "note" },
      "Home analizado: ", el("span", { class: "path", text: shorten(report.home) }),
      ` · Instantánea de ${formatTime(at)}; los cambios posteriores no se reflejan. La detección de CLI no verifica versiones.`));
    return frag;
  }

  function jumpLink(anchor, text) {
    const link = el("a", { href: `#${anchor}`, text });
    link.addEventListener("click", (event) => {
      event.preventDefault();
      const target = document.getElementById(anchor);
      if (!target) return;
      const list = target.closest("ul[hidden]");
      if (list) {
        list.hidden = false;
        const toggle = document.querySelector(`[aria-controls="${list.id}"]`);
        if (toggle) toggle.setAttribute("aria-expanded", "true");
      }
      target.scrollIntoView({ block: "center" });
      target.focus();
    });
    return link;
  }

  function rowNode(row) {
    return el("li", { class: "row", id: row.id, tabindex: "-1" },
      el("span", { class: "name" }, row.name,
        row.hint ? el("span", { class: "hint path", text: shorten(row.hint) }) : null),
      statusChip(row.info));
  }

  function groupNode(clientKey, group) {
    const id = `${clientKey}-${group.field}-list`;
    const ok = group.entries.filter((e) => e.info.level === "ok").length;
    const attention = group.entries.some((e) => e.info.level !== "ok");
    const sorted = [...group.entries].sort((a, b) => rank(a.info.level) - rank(b.info.level));
    const button = el("button", { type: "button", "aria-expanded": attention ? "true" : "false", "aria-controls": id },
      el("span", {}, el("strong", { text: group.title }), ` · ${ok} de ${group.entries.length} al día`),
      el("span", { class: "chev", "aria-hidden": "true", text: "›" }));
    const list = el("ul", { class: "rows", id }, sorted.map(rowNode));
    list.hidden = !attention;
    button.addEventListener("click", () => {
      const open = button.getAttribute("aria-expanded") !== "true";
      button.setAttribute("aria-expanded", String(open));
      list.hidden = !open;
    });
    return el("div", { class: "group" }, button, list);
  }

  function rank(level) { return { block: 0, unknown: 1, warn: 2, neutral: 3, ok: 4 }[level] ?? 1; }

  function renderFailure(run, at) {
    const outcome = run.outcome;
    let origin, title, body;
    const rows = [];
    if (outcome.kind === "doctorError") {
      const interrupted = outcome.exitCode === 130;
      origin = interrupted ? "Diagnóstico interrumpido" : "Doctor informó un error";
      title = "No se pudo completar el diagnóstico";
      body = interrupted ? "Doctor se interrumpió antes de terminar." : shorten(outcome.message) || "Doctor no pudo completar la inspección.";
      rows.push(["Código", outcome.errorCode], ["Salida", outcome.exitCode]);
    } else {
      const code = outcome.kind === "transport" ? outcome.code : "contractMismatch";
      const [what, text] = TRANSPORT[code] || ["La app no pudo comunicarse con Doctor", "Fallo de transporte no reconocido."];
      origin = code === "busy" ? "Espera" : "La app no pudo comunicarse con Doctor";
      title = what;
      body = text;
      rows.push(["Código", code]);
      if (outcome.exitCode !== null && outcome.exitCode !== undefined) rows.push(["Salida del proceso", outcome.exitCode]);
      if (outcome.detail) rows.push(["Detalle", shorten(outcome.detail)]);
    }
    rows.push(["Hora", formatTime(at)]);
    const retry = el("button", { type: "button", class: "primary", text: "Reintentar" });
    retry.addEventListener("click", () => runDiagnosis(retry));
    const frag = document.createDocumentFragment();
    frag.append(el("section", { class: "panel failure", role: "alert", "aria-labelledby": "fail-title" },
      icon("failure"),
      el("div", {},
        el("p", { class: "origin", text: origin }),
        el("h2", { id: "fail-title", text: title }),
        el("p", { text: body }),
        el("p", { class: "note", text: "Esto no es un resultado del diagnóstico: no se evaluó el estado de la instalación." }),
        el("details", {}, el("summary", { text: "Detalle técnico" }),
          el("dl", {}, rows.map(([k, v]) => [el("dt", { text: k }), el("dd", { text: String(v) })]))),
        el("div", { class: "actions" }, retry))));
    if (state.lastGood) {
      const stale = el("details", { class: "stale" },
        el("summary", { text: `Ver último resultado válido (${formatTime(state.lastGood.at)}); puede estar desactualizado` }));
      stale.append(renderReport(state.lastGood.run, state.lastGood.at));
      frag.append(stale);
    }
    return frag;
  }

  function formatTime(date) {
    return date.toLocaleTimeString("es", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  }

  function paint() {
    const target = $("result");
    if (!state.current) return;
    const { run, at } = state.current;
    target.replaceChildren(run.outcome.kind === "report" ? renderReport(run, at) : renderFailure(run, at));
    target.setAttribute("aria-busy", "false");
    const p = run.provenance || {};
    $("foot").textContent = [
      `SYNCODE Desktop ${p.appVersion || "?"} · prototipo Fase 1`,
      p.pythonVersion ? `Python integrado ${p.pythonVersion}` : "Python integrado: desconocido",
      p.sourceCommit ? `núcleo ${p.sourceCommit.slice(0, 12)}` : null,
    ].filter(Boolean).join(" · ");
  }

  function announce(run) {
    const o = run.outcome;
    if (o.kind === "report") {
      const n = Number(o.report.summary.blockers) || 0;
      return n ? `Diagnóstico completado: ${plural(n, "elemento requiere revisión", "elementos requieren revisión")}.`
        : "Diagnóstico completado: sin elementos que requieran revisión.";
    }
    return "No se pudo completar el diagnóstico.";
  }

  async function runDiagnosis(focusAfter) {
    if (state.running) return;
    const button = $("run");
    const id = ++state.runId;
    state.running = true;
    button.setAttribute("aria-disabled", "true");
    $("result").setAttribute("aria-busy", "true");
    $("live").textContent = "Diagnosticando…";
    $("meta").textContent = "Diagnosticando…";
    if (state.current) { // the first run keeps the static loading panel
      $("result").replaceChildren(el("section", { class: "panel loading", "aria-label": "Diagnóstico en curso" },
        el("span", { class: "spinner", "aria-hidden": "true" }), el("p", { text: "Diagnosticando…" })));
    }
    let run;
    const invoke = window.__TAURI__ && window.__TAURI__.core && window.__TAURI__.core.invoke;
    try {
      if (typeof invoke !== "function") throw new Error("bridge");
      run = await invoke("run_doctor");
    } catch (error) {
      run = { outcome: { kind: "transport", code: "bridge", detail: String(error && error.message || error), exitCode: null } };
    }
    if (id !== state.runId) return; // a newer run superseded this one
    const at = new Date();
    if (run.userHome) state.userHome = run.userHome;
    state.current = { run, at };
    if (run.outcome.kind === "report") state.lastGood = { run, at };
    state.running = false;
    button.removeAttribute("aria-disabled");
    paint();
    const seconds = typeof run.durationMs === "number" ? ` en ${(run.durationMs / 1000).toFixed(1)} s` : "";
    $("meta").textContent = `Último diagnóstico: hoy, ${formatTime(at)}${seconds}.`;
    $("live").textContent = announce(run);
    // Keep focus on the trigger; a replaced "Reintentar" falls back to the toolbar button.
    if (focusAfter) (document.contains(focusAfter) ? focusAfter : button).focus();
  }

  document.addEventListener("DOMContentLoaded", () => {
    $("run").addEventListener("click", () => runDiagnosis($("run")));
    $("show-paths").addEventListener("change", paint);
    document.addEventListener("keydown", (event) => {
      // F5 / Ctrl+R re-run the diagnosis instead of reloading the webview.
      if (event.key === "F5" || ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "r")) {
        event.preventDefault();
        runDiagnosis($("run"));
      }
    });
    document.addEventListener("contextmenu", (event) => event.preventDefault());
    runDiagnosis(null);
  });
})();
