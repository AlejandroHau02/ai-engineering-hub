// SYNCODE Desktop — Diagnóstico y Sincronización (vista previa, solo lectura).
// Renders validated outcomes from the read-only commands `run_doctor` and
// `preview_sync`. All data reaches the DOM through textContent.
"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const SVG = "http://www.w3.org/2000/svg";

  // ---- vocabulary -------------------------------------------------------------
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
    "not-found": ["Comando no encontrado (solo informativo)", "neutral"],
  };
  const TRANSACTION = {
    clear: ["Sin transacción pendiente", "ok"],
    "pending-recovery": ["Recuperación pendiente", "block"],
    unsafe: ["Área de transacción insegura", "block"],
  };
  const LOCK = {
    absent: ["Sin archivo lock", "ok"],
    recognized: ["Archivo de bloqueo anterior (normal, no requiere acción)", "neutral"],
    unexpected: ["Archivo lock con contenido inesperado", "block"],
    unsafe: ["Archivo lock en ruta insegura", "block"],
  };
  const NEXT_STEP = {
    // The managed block of the instructions file is replaced by a sync; the
    // personal content outside the markers is preserved.
    "instructions:modified": "El bloque gestionado difiere del de esta versión. Una sincronización lo reemplazaría y conservaría el contenido fuera de los marcadores. Compruébalo en Sincronización.",
    modified: "Revisa los cambios locales; SYNCODE no los sobrescribirá.",
    "unsafe-or-unreadable": "Inspección manual de la ruta (enlace simbólico, junction o permisos).",
    "transaction:pending-recovery": "Una sincronización anterior no terminó. Recupérala antes de sincronizar de nuevo.",
    "transaction:unsafe": "La carpeta de recuperación de SYNCODE tiene un formato inesperado (por ejemplo, un enlace). No es seguro recuperarla automáticamente: requiere revisión manual.",
    "lock:unexpected": "Inspección manual del archivo lock antes de cualquier sincronización.",
    "lock:unsafe": "Inspección manual de la ruta del archivo lock.",
  };
  const CLIENTS = {
    claude: { name: "Claude Code", instructions: "~/.claude/CLAUDE.md" },
    codex: { name: "Codex", instructions: "~/.codex/AGENTS.md" },
  };
  // Transport failures, parameterized by the subject that could not run.
  function transportText(code, subject) {
    const S = subject;
    const table = {
      bundleIncomplete: ["Falta el entorno de ejecución integrado",
        "SYNCODE Desktop está incompleto: no se encontró el runtime Python o el núcleo. Reinstala la aplicación o, si usas el paquete portable, extrae el ZIP completo en una carpeta propia."],
      spawnFailed: [`La app no pudo iniciar ${S.name}`, "El sistema no permitió iniciar el runtime Python integrado."],
      timeout: [`${S.Name} no respondió a tiempo`, "Se superó el tiempo máximo y el proceso se detuvo. Ningún archivo fue modificado."],
      outputTooLarge: ["Respuesta demasiado grande", `La respuesta de ${S.name} superó el tamaño permitido y se descartó.`],
      noOutput: [`${S.Name} terminó sin respuesta`, "El proceso terminó sin un resultado JSON. El estado es desconocido."],
      invalidJson: ["Respuesta no válida", `La respuesta de ${S.name} no es JSON válido y se descartó.`],
      contractMismatch: [`Respuesta de ${S.name} incompatible`, "La respuesta no tiene el formato que espera esta versión de la app y se descartó."],
      processFailed: ["El proceso terminó de forma inesperada", "El runtime Python finalizó con un código fuera del contrato."],
      busy: ["Otra operación en curso", "SYNCODE ya está ejecutando un diagnóstico, una vista previa o una aplicación. Espera a que termine."],
      integrityFailed: ["El paquete de SYNCODE no supera la verificación de integridad",
        "Algún archivo del runtime o del núcleo no coincide con esta versión de la app (falta, sobra o fue modificado). No se ejecutó nada. Reinstala la aplicación o vuelve a extraer el ZIP completo en una carpeta nueva."],
      bridge: ["La interfaz no puede comunicarse con la app", "El puente con SYNCODE Desktop no está disponible en esta ventana."],
    };
    return table[code] || ["La app no pudo comunicarse con el núcleo", "Fallo de transporte no reconocido."];
  }
  const SUBJECTS = {
    diagnostico: { name: "Doctor", Name: "Doctor", what: "el diagnóstico", notEvaluated: "Esto no es un resultado del diagnóstico: no se evaluó el estado de la instalación." },
    sincronizacion: { name: "el planificador", Name: "El planificador", what: "la vista previa", notEvaluated: "Esto no es una vista previa: no se calculó ningún plan." },
    catalogo: { name: "el catálogo", Name: "El catálogo", what: "el catálogo", notEvaluated: "No se pudo leer el catálogo; no se evaluó ningún componente." },
  };
  const KIND = {
    CREATE: ["Se creará", "info"],
    UPDATE: ["Se actualizará", "info"],
    UPGRADE: ["Se actualizará la skill (versión v0.1 reconocida)", "warn"],
  };
  const REFUSAL = {
    "skill-upgrade-available": "Una skill instalada difiere de la versión actual. Si es idéntica a la versión v0.1 reconocida, puedes previsualizar su actualización.",
    "skill-modified": "Una skill instalada fue modificada localmente o no corresponde a una versión reconocida. SYNCODE no la sobrescribirá: requiere revisión manual.",
    "destination-conflict": "Un destino gestionado ya contiene archivos o carpetas ajenos a SYNCODE. Requiere revisión manual.",
    "transaction-pending": "Una sincronización anterior no terminó. Recupérala desde Diagnóstico antes de planificar.",
    "stale-plan": "Los archivos cambiaron desde la vista previa.",
    "destination-changed": "Un archivo cambió mientras se aplicaba el plan.",
    "lock-busy": "Otra instalación o recuperación de SYNCODE está en curso.",
    "managed-markers-invalid": "Los marcadores del bloque gestionado de un archivo de instrucciones están repetidos, incompletos o desordenados. Revísalo manualmente.",
    "unsafe-path": "Un destino es un enlace simbólico, una junction o una ruta no permitida. Requiere inspección manual.",
    "io-error": "El sistema de archivos devolvió un error al leer un destino.",
    "encoding-error": "Un archivo existente no se pudo leer como UTF-8.",
    interrupted: "La planificación se interrumpió antes de terminar.",
    "invalid-arguments": "La app envió una combinación de opciones no válida (error de integración).",
  };
  const TARGET_LABEL = { both: "Claude Code y Codex", claude: "Solo Claude Code", codex: "Solo Codex" };

  // ---- shared state -----------------------------------------------------------
  // `homes`: spellings of the user's home seen so far (USERPROFILE and the
  // core's resolved home), all shortened to "~" for privacy.
  const homes = new Set();
  const rawPath = new WeakMap();
  const screens = {
    diagnostico: { title: "Diagnóstico", runId: 0, running: false, current: null, lastGood: null },
    sincronizacion: { title: "Sincronización", runId: 0, running: false, current: null, lastGood: null },
    catalogo: { title: "Catálogo", runId: 0, running: false, current: null, lastGood: null },
  };
  // Single-use recover token from the latest Doctor report (null otherwise).
  let recoverToken = null;
  let active = "diagnostico";
  // Set by every command response: how to recover with the bundled runtime.
  let recoveryCommand = null;
  // True from the confirmation until the apply result is shown.
  let writing = false;
  // Commands are serialized: the core runs one at a time, so a request made
  // while the other screen is busy waits for its turn instead of failing.
  let queue = Promise.resolve();

  // ---- DOM helpers ------------------------------------------------------------
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
    info: "M7 3h7l4 4v14H7zM14 3v4h4M10 12h5M10 16h5",
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

  function plural(n, one, many) { return `${n} ${n === 1 ? one : many}`; }

  function formatTime(date) {
    return date.toLocaleTimeString("es", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  }

  // ---- privacy: shorten the user's home to "~" unless asked otherwise ------
  function showFull() { return $("show-paths").checked; }
  function shorten(text) {
    if (typeof text !== "string" || showFull()) return text;
    // Longest first, so a short spelling never pre-empts a longer resolved home.
    const sorted = [...homes].filter(Boolean).sort((a, b) => b.length - a.length);
    let out = text;
    for (const home of sorted) {
      const needle = home.toLowerCase();
      const lower = out.toLowerCase();
      let result = "";
      let index = 0;
      for (;;) {
        const at = lower.indexOf(needle, index);
        if (at < 0) break;
        result += out.slice(index, at) + "~";
        index = at + home.length;
      }
      out = result + out.slice(index);
    }
    return out;
  }

  // A text node that may contain local paths; re-shortened in place when the
  // "Mostrar rutas completas" toggle changes (no repaint, no re-announcement).
  function pathText(tag, attrs, raw) {
    const node = el(tag, Object.assign({ "data-path": true }, attrs || {}));
    rawPath.set(node, raw);
    node.textContent = shorten(raw);
    return node;
  }

  function refreshPaths() {
    for (const node of document.querySelectorAll("[data-path]")) {
      if (rawPath.has(node)) node.textContent = shorten(rawPath.get(node));
    }
  }

  // ---- reusable components ------------------------------------------------------
  function verdictPanel(prefix, level, title, sub, extra) {
    return el("section", { class: `panel verdict level-${level}`, "aria-labelledby": `${prefix}-verdict` },
      icon(level),
      el("div", {},
        el("h2", { id: `${prefix}-verdict`, text: title }),
        sub ? el("p", { class: "sub", text: sub }) : null,
        extra || null));
  }

  function loadingPanel(label) {
    return el("section", { class: "panel loading", "aria-label": label },
      el("span", { class: "spinner", "aria-hidden": "true" }), el("p", { text: `${label}…` }));
  }

  function groupNode(id, title, summary, entries, open, renderRow) {
    const button = el("button", { type: "button", "aria-expanded": open ? "true" : "false", "aria-controls": id },
      el("span", {}, el("strong", { text: title }), summary ? ` · ${summary}` : ""),
      el("span", { class: "chev", "aria-hidden": "true", text: "›" }));
    const list = el("ul", { class: "rows", id }, entries.map(renderRow));
    list.hidden = !open;
    button.addEventListener("click", () => {
      const next = button.getAttribute("aria-expanded") !== "true";
      button.setAttribute("aria-expanded", String(next));
      list.hidden = !next;
    });
    return el("div", { class: "group" }, button, list);
  }

  // Moves focus to a row, opening its collapsed group first. A button (not a
  // link) so it never interferes with the #screen routing.
  function jumpButton(anchor, text) {
    const button = el("button", { type: "button", class: "link", text });
    button.addEventListener("click", () => {
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
    return button;
  }

  function failurePanel(screen, outcome, at, retry) {
    const subject = SUBJECTS[screen];
    const prefix = screen === "diagnostico" ? "diag" : "sync";
    let origin, title, body;
    const rows = [];
    if (outcome.kind === "doctorError") {
      const interrupted = outcome.exitCode === 130;
      origin = interrupted ? "Diagnóstico interrumpido" : "Doctor informó un error";
      title = "No se pudo completar el diagnóstico";
      body = interrupted ? "Doctor se interrumpió antes de terminar." : outcome.message || "Doctor no pudo completar la inspección.";
      rows.push(["Código", outcome.errorCode], ["Salida", outcome.exitCode]);
    } else {
      const code = outcome.kind === "transport" ? outcome.code : "contractMismatch";
      [title, body] = transportText(code, subject);
      origin = code === "busy" ? "Espera" : `La app no pudo comunicarse con ${subject.name}`;
      rows.push(["Código", code]);
      if (outcome.exitCode !== null && outcome.exitCode !== undefined) rows.push(["Salida del proceso", outcome.exitCode]);
      if (outcome.detail) rows.push(["Detalle", outcome.detail]);
    }
    rows.push(["Hora", formatTime(at)]);
    const retryButton = el("button", { type: "button", class: "primary", text: "Reintentar" });
    retryButton.addEventListener("click", () => retry(retryButton));
    return el("section", { class: "panel failure", "aria-labelledby": `${prefix}-fail` },
      icon("failure"),
      el("div", {},
        el("p", { class: "origin", text: origin }),
        el("h2", { id: `${prefix}-fail`, text: title }),
        pathText("p", {}, body),
        el("p", { class: "note", text: subject.notEvaluated }),
        el("details", {}, el("summary", { text: "Detalle técnico" }),
          el("dl", {}, rows.map(([k, v]) => [el("dt", { text: k }), pathText("dd", {}, String(v))]))),
        el("div", { class: "actions" }, retryButton)));
  }

  function staleResult(screen, render) {
    const last = screens[screen].lastGood;
    if (!last) return null;
    const box = el("details", { class: "stale" },
      el("summary", { text: `Ver último resultado válido (${formatTime(last.at)}); puede estar desactualizado` }));
    box.append(render(last.run, last.at, true));
    return box;
  }

  // ---- Diagnóstico ------------------------------------------------------------
  function collect(report) {
    const items = [];
    const clients = [];
    for (const key of ["claude", "codex"]) {
      const client = report.clients[key] || {};
      const meta = CLIENTS[key];
      const exe = client.executable || {};
      const rows = [
        { id: `diag-${key}-cli`, name: "CLI", hint: exe.path || null, info: describe(EXECUTABLE, exe.status) },
        { id: `diag-${key}-instructions`, name: "Instrucciones globales", hint: meta.instructions,
          info: describe(STATUS, (client.instructions || {}).status), instructions: true },
      ];
      const groups = [];
      for (const [field, title] of [["skills", "Skills"], ["specialists", "Especialistas"]]) {
        const entries = Object.entries(client[field] || {}).map(([name, value]) => ({
          id: `diag-${key}-${field}-${name}`, name, info: describe(STATUS, (value || {}).status),
        }));
        groups.push({ field, title, entries });
      }
      for (const row of [rows[1], ...groups.flatMap((g) => g.entries)]) {
        if (row.info.level === "block" || row.info.level === "unknown") {
          const specific = row.instructions ? NEXT_STEP[`instructions:${row.info.raw}`] : null;
          items.push({ where: `${meta.name} · ${row.name}`, info: row.info, anchor: row.id,
            next: specific || NEXT_STEP[row.info.raw] || "Revisión manual." });
        }
      }
      clients.push({ key, meta, rows, groups });
    }
    const tx = describe(TRANSACTION, report.transaction);
    const lock = describe(LOCK, report.lock);
    for (const [prefix, info, label] of [["transaction", tx, "Transacción"], ["lock", lock, "Archivo lock"]]) {
      if (info.level === "block" || info.level === "unknown") {
        items.push({ where: `Estado de sincronización · ${label}`, info, anchor: `diag-state-${prefix}`,
          next: NEXT_STEP[`${prefix}:${info.raw}`] || "Revisión manual." });
      }
    }
    return { items, clients, tx, lock };
  }

  function rank(level) { return { block: 0, unknown: 1, warn: 2, neutral: 3, info: 3, ok: 4 }[level] ?? 1; }

  function diagRow(row) {
    return el("li", { class: "row", id: row.id, tabindex: "-1" },
      el("span", { class: "name" }, row.name,
        row.hint ? pathText("span", { class: "hint path" }, row.hint) : null),
      statusChip(row.info));
  }

  function renderReport(run, at, nested) {
    const report = run.outcome.report;
    const summary = report.summary || {};
    const blockers = Number(summary.blockers) || 0;
    const derived = collect(report);
    const frag = document.createDocumentFragment();
    // Nested (stale) copies must not duplicate ids.
    const id = (value) => (nested ? null : value);

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
    frag.append(verdictPanel(nested ? "diag-old" : "diag", level, title,
      notes.length ? notes.join(" · ") : "Sin componentes informados."));

    frag.append(el("section", { class: "panel strip", "aria-label": "Estado de sincronización" },
      el("h2", { text: "Estado de sincronización" }),
      el("span", { id: id("diag-state-transaction"), tabindex: "-1" }, "Transacción: ", statusChip(derived.tx)),
      el("span", { id: id("diag-state-lock"), tabindex: "-1" }, "Archivo lock: ", statusChip(derived.lock))));

    if (blockers > 0 || derived.items.length) {
      const list = el("ul", {}, derived.items.map((item) => el("li", {},
        el("div", { class: "what" }, nested ? item.where : jumpButton(item.anchor, item.where), " — ", statusChip(item.info)),
        el("p", { class: "next", text: item.next }),
        item.info.raw === "pending-recovery" && !nested ? recoverAction() : null)));
      const extra = blockers - derived.items.filter((i) => i.info.level === "block").length;
      frag.append(el("section", { class: "panel review", "aria-label": "Requieren revisión" },
        el("h2", { text: "Requieren revisión" }),
        list,
        extra > 0 ? el("p", { class: "note", text: `Doctor informa ${plural(extra, "elemento adicional", "elementos adicionales")} sin detalle en esta versión de la app.` }) : null,
        el("p", { class: "note", text: "Las sincronizaciones se aplican desde «Sincronización»." })));
    }

    const cards = el("div", { class: "clients" });
    for (const client of derived.clients) {
      const all = [client.rows[1], ...client.groups.flatMap((g) => g.entries)];
      const ok = all.filter((r) => r.info.level === "ok").length;
      const card = el("section", { class: "panel client", "aria-label": client.meta.name },
        el("header", {}, el("h3", { text: client.meta.name }),
          el("span", { class: "count", text: `${ok} de ${all.length} al día` })));
      const rows = nested ? client.rows.map((r) => ({ ...r, id: null })) : client.rows;
      card.append(el("ul", { class: "rows" }, rows.map(diagRow)));
      for (const group of client.groups) {
        const entries = [...group.entries].sort((a, b) => rank(a.info.level) - rank(b.info.level))
          .map((e) => (nested ? { ...e, id: null } : e));
        const okCount = entries.filter((e) => e.info.level === "ok").length;
        const attention = entries.some((e) => e.info.level !== "ok");
        card.append(groupNode(`${nested ? "old-" : ""}diag-${client.key}-${group.field}-list`, group.title,
          `${okCount} de ${entries.length} al día`, entries, attention, diagRow));
      }
      cards.append(card);
    }
    frag.append(cards);
    frag.append(el("p", { class: "note" },
      "Home analizado: ", pathText("span", { class: "path" }, report.home),
      ` · Instantánea de las ${formatTime(at)}; los cambios posteriores no se reflejan. La detección de CLI no verifica versiones.`));
    return frag;
  }

  function doctorFoundUpgrades() {
    const last = screens.diagnostico.lastGood;
    return Boolean(last && Number((last.run.outcome.report.summary || {}).upgradeAvailable) > 0);
  }

  // ---- Sincronización ---------------------------------------------------------
  function readOptions() {
    const form = $("sync-form");
    const target = (form.querySelector("input[name=target]:checked") || {}).value || "both";
    return {
      target: Object.prototype.hasOwnProperty.call(TARGET_LABEL, target) ? target : "both",
      specialists: $("opt-specialists").checked,
      upgradeSkills: $("opt-upgrade").checked,
    };
  }

  function sameOptions(a, b) {
    return a && b && a.target === b.target && a.specialists === b.specialists && a.upgradeSkills === b.upgradeSkills;
  }

  function scopeText(options) {
    return [TARGET_LABEL[options.target] || options.target,
      options.specialists ? "con especialistas" : "sin especialistas",
      options.upgradeSkills ? "con actualizaciones de skills v0.1" : "sin actualizaciones de skills"].join(" · ");
  }

  function cliEquivalent(options) {
    return ["python scripts/sync.py --preview", `--target ${options.target}`,
      options.specialists ? "--specialists" : null, options.upgradeSkills ? "--upgrade-skills" : null]
      .filter(Boolean).join(" ");
  }

  function componentLabel(change) {
    const name = change.componentName;
    switch (change.componentType) {
      case "instructions": return "Instrucciones globales";
      case "skill": return `Skill ${name || ""}`.trim();
      case "specialist": return `Especialista ${name || ""}`.trim();
      default: return `Componente desconocido (${change.componentType})`;
    }
  }

  function changeAction(change) {
    if (change.kind === "UPDATE" && change.componentType === "instructions") {
      return { label: "Se actualizará la sección gestionada; el resto del archivo se conserva", level: "info" };
    }
    const known = Object.prototype.hasOwnProperty.call(KIND, change.kind) ? KIND[change.kind] : null;
    return known ? { label: known[0], level: known[1] } : { label: `Tipo desconocido (${change.kind})`, level: "unknown" };
  }

  function homePath(home, relative) {
    const sep = home.includes("\\") ? "\\" : "/";
    return home.replace(/[\\/]+$/, "") + sep + relative.split("/").join(sep);
  }

  function renderPlan(run, at, nested) {
    const o = run.outcome;
    const frag = document.createDocumentFragment();
    const prefix = nested ? "sync-old" : "sync";
    if (typeof o.home === "string") homes.add(o.home);

    if (o.status === "unchanged") {
      frag.append(verdictPanel(prefix, "ok", "Vista previa: sin cambios",
        `No hay nada que sincronizar para: ${scopeText(o.options)}.`));
    } else {
      const counts = {};
      for (const c of o.changes) counts[c.kind] = (counts[c.kind] || 0) + 1;
      const parts = [];
      if (counts.CREATE) parts.push(plural(counts.CREATE, "se creará", "se crearán"));
      if (counts.UPDATE) parts.push(plural(counts.UPDATE, "se actualizará", "se actualizarán"));
      if (counts.UPGRADE) parts.push(plural(counts.UPGRADE, "actualización de skill", "actualizaciones de skills"));
      const other = o.changes.length - (counts.CREATE || 0) - (counts.UPDATE || 0) - (counts.UPGRADE || 0);
      if (other > 0) parts.push(plural(other, "de tipo desconocido", "de tipo desconocido"));
      frag.append(verdictPanel(prefix, "info",
        `Vista previa: ${plural(o.changes.length, "cambio propuesto", "cambios propuestos")} · nada se ha escrito`,
        parts.join(" · "),
        el("p", { class: "sub", text: `Alcance: ${scopeText(o.options)}.` })));
    }

    if (o.recoveryRequired === true) {
      frag.append(el("section", { class: "panel notice-panel level-warn" }, icon("warn"),
        el("p", { text: "Apareció una transacción de instalación mientras se calculaba la vista previa (otra herramienta puede estar aplicando cambios). Esta vista previa puede no reflejar el estado final: revisa Diagnóstico." })));
    } else if (o.recoveryRequired !== false) {
      frag.append(el("p", { class: "note", text: "No se pudo comprobar si había una transacción en curso al terminar la vista previa." }));
    }

    if (o.changes.length) frag.append(changeCards(o.changes, o.home, null));
    return renderPlanTail(o, at, nested, frag);
  }

  function changeCards(changes, home, doneLabel) {
    const frag = document.createDocumentFragment();
    {
      const groups = [
        { key: "claude", title: "Claude Code", entries: [] },
        { key: "codex", title: "Codex", entries: [] },
        { key: "other", title: "Otros archivos", entries: [] },
      ];
      for (const change of changes) {
        const group = groups.find((g) => g.key === change.target) || groups[2];
        group.entries.push(change);
      }
      // Every change is shown exactly once.
      const shown = groups.reduce((n, g) => n + g.entries.length, 0);
      const cards = el("div", { class: "clients" });
      for (const group of groups.filter((g) => g.entries.length)) {
        const card = el("section", { class: "panel client", "aria-label": `${group.title}: ${doneLabel ? "cambios escritos" : "cambios propuestos"}` },
          el("header", {}, el("h3", { text: group.title }),
            el("span", { class: "count", text: plural(group.entries.length, "cambio", "cambios") })));
        card.append(el("ul", { class: "rows" }, group.entries.map((change) => {
          const action = doneLabel ? { label: change.kind === "CREATE" ? "Creado" : "Actualizado", level: "ok" } : changeAction(change);
          return el("li", { class: "row" },
            el("span", { class: "name" }, componentLabel(change),
              pathText("span", { class: "hint path" }, homePath(home, change.path))),
            statusChip(action));
        })));
        cards.append(card);
      }
      frag.append(cards);
      if (shown !== changes.length) {
        frag.append(el("p", { class: "note", text: "Algunos cambios no pudieron agruparse." }));
      }
    }
    return frag;
  }

  function renderPlanTail(o, at, nested, frag) {
    const technical = nested ? null : el("details", { class: "technical", "data-plan-details": true },
      el("summary", { text: "Detalle técnico" }),
      el("dl", {},
        el("dt", { text: "Identificador del plan (técnico)" }),
        el("dd", {}, el("code", { text: o.planId })),
        el("dt", { text: "Qué significa" }),
        el("dd", { text: "Identifica este lote exacto de cambios. No es una autorización: aplicar exige tu confirmación, se puede hacer una sola vez y el núcleo vuelve a comprobar el lote bajo su bloqueo antes de escribir. La huella completa nunca sale del proceso de la app." }),
        el("dt", { text: "Equivalente en CLI (desde un checkout de SYNCODE)" }),
        el("dd", {}, el("code", { text: cliEquivalent(o.options) })),
        el("dt", { text: "Home analizado" }),
        pathText("dd", {}, o.home)));
    if (!nested && o.status === "planned") frag.append(applyArea(o));
    if (technical) frag.append(technical);
    frag.append(el("p", { class: "note" },
      `Generada a las ${formatTime(at)}. Es una instantánea: si algún archivo cambia antes de aplicar, `,
      "no se aplicará nada y tendrás que generar una nueva vista previa."));
    if (!nested) {
      frag.prepend(el("p", { class: "panel notice-panel level-warn stale-notice", hidden: true, "data-stale-notice": true },
        "Esta vista previa corresponde a otras opciones. Genérala de nuevo para ver el plan actual."));
    }
    return frag;
  }

  function renderRefusal(run, at) {
    const o = run.outcome;
    const frag = document.createDocumentFragment();
    const explanation = REFUSAL[o.code] || `El planificador rechazó la vista previa (${o.code}).`;
    const actions = el("div", { class: "actions" });
    if (o.code === "skill-upgrade-available" && !o.options.upgradeSkills) {
      const enable = el("button", { type: "button", class: "link", text: "Activar «Previsualizar actualizaciones de skills v0.1»" });
      enable.addEventListener("click", () => {
        $("opt-upgrade").checked = true;
        $("opt-upgrade").dispatchEvent(new Event("change", { bubbles: true }));
        $("opt-upgrade").focus();
      });
      actions.append(enable);
    }
    if (o.code === "transaction-pending") {
      actions.append(el("a", { href: "#diagnostico", class: "link", text: "Ir a Diagnóstico" }));
    }
    const steps = o.code === "transaction-pending" ? recoverySteps() : null;
    frag.append(el("section", { class: "panel verdict level-warn refusal", "aria-labelledby": "sync-refusal" },
      icon("warn"),
      el("div", {},
        el("h2", { id: "sync-refusal", text: "La planificación se detuvo" }),
        el("p", { text: explanation }),
        el("p", { class: "sub", text: "Se detiene en el primer conflicto; puede haber otros. No se escribió ningún archivo." }),
        o.code === "skill-upgrade-available" && !o.options.upgradeSkills
          ? el("p", { class: "sub", text: "Al activar la vista previa de actualizaciones, una skill modificada localmente seguiría deteniendo la planificación." })
          : null,
        steps,
        actions.childNodes.length ? actions : null,
        el("details", {}, el("summary", { text: "Detalle técnico" }),
          el("dl", {},
            el("dt", { text: "Código" }), el("dd", { text: o.code }),
            el("dt", { text: "Mensaje del planificador" }), pathText("dd", {}, o.message || "—"),
            el("dt", { text: "Alcance" }), el("dd", { text: scopeText(o.options) }),
            el("dt", { text: "Hora" }), el("dd", { text: formatTime(at) }))))));
    return frag;
  }

  function markStale() {
    const state = screens.sincronizacion;
    const current = state.current && state.current.run.outcome;
    const stale = Boolean(current && current.kind === "plan" && !sameOptions(current.options, readOptions()));
    if (stale && current.applyToken) {
      // Authorization ends as soon as the options change: forget it here and
      // in Rust (single-use plan store), even if the options are changed back.
      current.applyToken = null;
      current.discarded = true;
      invokeCommand("discard_plan").catch(() => undefined);
    }
    const area = document.querySelector("#sync-result [data-apply-area]");
    if (area && current && current.kind === "plan") area.replaceWith(applyArea(current));
    const notice = document.querySelector("#sync-result [data-stale-notice]");
    if (notice) notice.hidden = !stale;
    const details = document.querySelector("#sync-result [data-plan-details]");
    if (details) details.hidden = stale; // never show a plan id for other options
    $("sync-result").classList.toggle("is-stale", stale);
    const canApply = Boolean(current && current.kind === "plan" && current.applyToken && !stale);
    $("sync-run").classList.toggle("primary", !canApply); // one primary action at a time
  }

  // ---- Apply ----------------------------------------------------------------------
  function invokeCommand(command, args) {
    const invoke = window.__TAURI__ && window.__TAURI__.core && window.__TAURI__.core.invoke;
    if (typeof invoke !== "function") return Promise.reject(new Error("bridge"));
    return invoke(command, args);
  }

  function doctorTransactionState() {
    const last = screens.diagnostico.lastGood;
    return last ? last.run.outcome.report.transaction : "clear";
  }

  function applyBlockedReason(o) {
    if (o.discarded) return "Las opciones cambiaron: genera una nueva vista previa para aplicar.";
    if (o.recoveryRequired !== false) return "Hay una transacción de instalación en curso o sin terminar; revisa Diagnóstico.";
    if (doctorTransactionState() === "pending-recovery") return "Una sincronización anterior no terminó; recupérala en Diagnóstico antes de aplicar.";
    if (doctorTransactionState() !== "clear") return "Diagnóstico detectó un problema en la carpeta de recuperación; requiere revisión manual.";
    if (!o.applyToken) return "Esta vista previa ya no puede aplicarse; genera una nueva.";
    return null;
  }

  function applyArea(o) {
    const reason = applyBlockedReason(o);
    if (reason) {
      return el("div", { class: "apply-area", "data-apply-area": true },
        el("p", { class: "note", text: `Aplicar no está disponible: ${reason}` }));
    }
    const n = o.changes.length;
    const button = el("button", { type: "button", class: "primary", id: "apply-open",
      text: `Aplicar ${plural(n, "cambio", "cambios")}…` });
    button.addEventListener("click", () => openApplyDialog());
    return el("div", { class: "apply-area", "data-apply-area": true },
      button,
      el("p", { class: "note", text: "Pedirá confirmación. La integridad del paquete se verificó al calcular esta vista previa y se vuelve a verificar antes de escribir." }));
  }

  function countsByClient(changes) {
    const names = { claude: "Claude Code", codex: "Codex" };
    const counts = new Map();
    for (const c of changes) {
      const key = names[c.target] || "Otros archivos";
      counts.set(key, (counts.get(key) || 0) + 1);
    }
    return [...counts.entries()];
  }

  function openApplyDialog() {
    const state = screens.sincronizacion;
    const current = state.current;
    if (!current || writing || state.running) return;
    const o = current.run.outcome;
    if (o.kind !== "plan" || applyBlockedReason(o) || !sameOptions(o.options, readOptions())) return;
    const n = o.changes.length;
    $("apply-dialog-title").textContent = `¿Aplicar estos ${plural(n, "cambio", "cambios")}?`;
    $("apply-dialog-scope").textContent = `Alcance: ${scopeText(o.options)} · vista previa de las ${formatTime(current.at)}.`;
    $("apply-dialog-counts").replaceChildren(...countsByClient(o.changes)
      .map(([name, count]) => el("li", { text: `${name}: ${plural(count, "cambio", "cambios")}` })));
    const updates = o.changes.filter((c) => c.kind === "UPDATE").length;
    const upgrades = o.changes.filter((c) => c.kind === "UPGRADE").length;
    const creates = o.changes.filter((c) => c.kind === "CREATE").length;
    const warnings = [
      updates || upgrades ? "Se hará una copia .bak de cada archivo existente antes de modificarlo." : null,
      o.changes.some((c) => c.componentType === "instructions" && c.kind === "UPDATE")
        ? "En CLAUDE.md / AGENTS.md solo se reemplaza la sección gestionada por SYNCODE; el resto se conserva." : null,
      creates ? `Se crearán ${plural(creates, "archivo nuevo", "archivos nuevos")}.` : null,
      upgrades ? `Se actualizarán ${plural(upgrades, "skill v0.1 sin modificaciones locales", "skills v0.1 sin modificaciones locales")}.` : null,
      "Si algún archivo cambió desde la vista previa, no se aplicará nada.",
      "No cierres la app mientras se aplica.",
      "Todo ocurre en este equipo: no se sube nada ni se ejecuta ningún agente.",
    ].filter(Boolean);
    $("apply-dialog-warnings").replaceChildren(...warnings.map((w) => el("li", { text: w })));
    $("apply-confirm").textContent = `Aplicar ${plural(n, "cambio", "cambios")}`;
    const dialog = $("apply-dialog");
    dialog.returnValue = "";
    dialog.dataset.token = o.applyToken;
    dialog.showModal();
  }

  function onApplyDialogClose() {
    const dialog = $("apply-dialog");
    const token = dialog.dataset.token;
    delete dialog.dataset.token;
    if (dialog.returnValue !== "apply" || !token) {
      const opener = $("apply-open");
      if (opener) opener.focus();
      return;
    }
    const current = screens.sincronizacion.current;
    const o = current && current.run.outcome;
    // The plan on screen must still be the confirmed one.
    if (!o || o.kind !== "plan" || o.applyToken !== token || !sameOptions(o.options, readOptions())) return;
    runApply(current, token);
  }

  async function runApply(planEntry, token) {
    const state = screens.sincronizacion;
    if (writing || state.running) return;
    setWriting(true);
    state.running = true;
    recoverToken = null; // Rust invalidates the recover gate on apply
    planEntry.run.outcome.applyToken = null; // single use, also in the UI
    const n = planEntry.run.outcome.changes.length;
    const result = $("sync-result");
    result.setAttribute("aria-busy", "true");
    result.replaceChildren(el("section", { class: "panel loading", "aria-label": "Aplicando cambios", tabindex: "-1", id: "sync-writing" },
      el("span", { class: "spinner", "aria-hidden": "true" }),
      el("p", { text: `Aplicando ${plural(n, "cambio", "cambios")}… No cierres la app.` })));
    $("sync-writing").focus();
    announce("sincronizacion", "Aplicando cambios…");
    $("sync-meta").textContent = "Aplicando…";
    const call = queue.then(() => invokeCommand("apply_sync", { token }));
    queue = call.catch(() => undefined);
    let run;
    try {
      run = await call;
    } catch (error) {
      run = { outcome: { kind: "unknown", detail: String((error && error.message) || error), recoveryCommand: recoveryCommand || "" } };
    }
    run.phase = "apply";
    if (run.recoveryCommand) recoveryCommand = run.recoveryCommand;
    setWriting(false);
    state.running = false;
    const at = new Date();
    state.current = { run, at, plan: planEntry };
    paintScreen("sincronizacion");
    updateFooter(run);
    const seconds = typeof run.durationMs === "number" ? ` en ${(run.durationMs / 1000).toFixed(1)} s` : "";
    $("sync-meta").textContent = `Aplicación: ${formatTime(at)}${seconds}.`;
    const heading = $("sync-apply-result");
    if (heading && active === "sincronizacion") heading.focus();
    else announce("sincronizacion", APPLY_ANNOUNCE(run.outcome));
    // Whatever happened, the real state comes from Doctor.
    runScreen("diagnostico", null, true);
  }

  function APPLY_ANNOUNCE(o) {
    return {
      applied: o.verified ? "Cambios aplicados y verificados." : "Cambios aplicados, sin verificar.",
      appliedWithError: "Cambios aplicados con un error posterior.",
      stale: "No se aplicó nada: los archivos cambiaron desde la vista previa.",
      rejected: "La aplicación se rechazó.",
      recoveryRequired: "Recuperación necesaria.",
      busy: "No se aplicó nada: otra instalación está en curso.",
      notAuthorized: "No se aplicó nada: la autorización ya no es válida.",
      transport: "No se aplicó nada.",
    }[o.kind] || "Estado desconocido tras aplicar; revisa Diagnóstico.";
  }

  function recoverySteps() {
    if (!recoveryCommand) return null;
    const command = recoveryCommand;
    const copy = el("button", { type: "button", class: "secondary", text: "Copiar comando" });
    copy.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(command);
        copy.textContent = "Copiado";
      } catch (error) {
        const range = document.createRange();
        range.selectNodeContents(code);
        const selection = window.getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
        copy.textContent = "Selecciónalo y copia con Ctrl+C";
      }
    });
    const code = el("code", { class: "command", text: command });
    return el("div", { class: "recovery" },
      el("p", { class: "recovery-title", text: "Cómo recuperar (la app no lo hace por ti):" }),
      el("ol", {},
        el("li", { text: "Abre PowerShell. No ejecutes otra sincronización mientras tanto." }),
        el("li", {}, "Ejecuta este comando, que usa el runtime integrado de esta app: ", code, " ", copy),
        el("li", {}, "Vuelve a ", el("a", { href: "#diagnostico", class: "link", text: "Diagnóstico" }), " y pulsa «Volver a diagnosticar».")));
  }

  function renderApplyResult(run, at) {
    const o = run.outcome;
    const frag = document.createDocumentFragment();
    const plan = run.plan || (screens.sincronizacion.current && screens.sincronizacion.current.plan);
    const home = plan && plan.run.outcome.home;
    let level = "warn";
    let title;
    const body = [];
    let steps = null;
    switch (o.kind) {
      case "applied":
        level = o.verified ? "ok" : "warn";
        title = o.verified ? "Cambios aplicados y verificados" : "Cambios aplicados, sin verificar";
        body.push(`${plural(o.changes.length, "cambio escrito", "cambios escritos")} por el núcleo de SYNCODE para: ${scopeText(o.options)}.`);
        body.push(o.verified ? "Una nueva vista previa con las mismas opciones no encuentra cambios pendientes."
          : "La comprobación posterior no pudo confirmar el resultado. Revisa Diagnóstico.");
        body.push("Los archivos que ya existían conservan una copia .bak junto al original.");
        break;
      case "appliedWithError":
        title = "Cambios aplicados con un error posterior";
        body.push("El núcleo confirmó la escritura del lote, pero informó un error después. No vuelvas a aplicar.");
        if (o.cleanupPending) {
          body.push("Falta limpiar el área de transacción.");
          steps = recoverySteps();
        }
        break;
      case "stale":
        title = "No se aplicó nada: los archivos cambiaron desde la vista previa";
        body.push("El núcleo volvió a calcular el plan bajo su bloqueo y ya no coincidía con el que revisaste. Genera una nueva vista previa y revísala de nuevo.");
        break;
      case "rejected":
        level = "block";
        title = "La aplicación se rechazó";
        body.push(REFUSAL[o.code] || `El núcleo rechazó la aplicación (${o.code}).`);
        body.push(o.outcome === "rolled-back" ? "Se restauró el estado anterior de todos los archivos." : "No se modificó ningún archivo.");
        break;
      case "recoveryRequired":
        level = "block";
        title = "Recuperación necesaria";
        body.push({
          "rolled-back": "No quedó ningún cambio aplicado; solo falta limpiar el área de transacción.",
          "recovery-pending": "Los archivos pueden haber quedado parcialmente aplicados. El journal de la transacción se conservó para recuperarlos.",
          "not-started": "Hay una transacción anterior sin terminar; no se escribió nada nuevo.",
        }[o.outcome] || "La transacción necesita recuperación.");
        body.push("Diagnóstico se actualizó: desde allí puedes recuperar la operación con un clic, tras confirmarlo.");
        break;
      case "busy":
        title = "No se aplicó nada: otra instalación está en curso";
        body.push("Otra instalación o recuperación de SYNCODE tiene el bloqueo. Espera a que termine y genera una nueva vista previa.");
        break;
      case "notAuthorized":
        title = "No se aplicó nada: la autorización ya no es válida";
        body.push({
          noPlan: "La vista previa ya se usó, se reemplazó o se descartó al cambiar las opciones.",
          wrongToken: "La autorización no coincide con la vista previa vigente.",
          expired: "La vista previa caducó (10 minutos).",
        }[o.reason] || "Genera una nueva vista previa.");
        break;
      case "transport": {
        const [what, text] = transportText(o.code, SUBJECTS.sincronizacion);
        return [el("section", { class: "panel failure", "aria-labelledby": "sync-apply-result" },
          icon("failure"),
          el("div", {},
            el("p", { class: "origin", text: "No se ejecutó la aplicación" }),
            el("h2", { id: "sync-apply-result", tabindex: "-1", text: `No se aplicó nada: ${what.toLowerCase()}` }),
            el("p", { text }),
            el("details", {}, el("summary", { text: "Detalle técnico" }),
              el("dl", {}, el("dt", { text: "Código" }), el("dd", { text: o.code }),
                el("dt", { text: "Detalle" }), pathText("dd", {}, o.detail || "—")))))];
      }
      default: // unknown
        return [el("section", { class: "panel failure", "aria-labelledby": "sync-apply-result" },
          icon("failure"),
          el("div", {},
            el("p", { class: "origin", text: "Resultado no verificable" }),
            el("h2", { id: "sync-apply-result", tabindex: "-1", text: "Estado desconocido tras aplicar" }),
            el("p", { text: "La app no pudo confirmar qué ocurrió: puede que se hayan escrito algunos archivos. Diagnóstico se actualizó automáticamente; si indica una recuperación pendiente, sigue estos pasos." }),
            recoverySteps(),
            el("details", {}, el("summary", { text: "Detalle técnico" }),
              el("dl", {}, el("dt", { text: "Detalle" }), pathText("dd", {}, o.detail || "—")))))];
    }
    frag.append(el("section", { class: `panel verdict level-${level}`, "aria-labelledby": "sync-apply-result" },
      icon(level === "ok" ? "ok" : level),
      el("div", {},
        el("h2", { id: "sync-apply-result", tabindex: "-1", text: title }),
        body.map((text) => pathText("p", {}, text)),
        o.message ? el("details", {}, el("summary", { text: "Detalle técnico" }),
          el("dl", {}, el("dt", { text: "Código" }), el("dd", { text: o.code || "—" }),
            el("dt", { text: "Mensaje del núcleo" }), pathText("dd", {}, o.message))) : null,
        steps,
        el("div", { class: "actions" },
          el("a", { href: "#diagnostico", class: "link", text: "Ver Diagnóstico actualizado" })))));
    if (o.kind === "applied" && home) {
      frag.append(changeCards(o.changes, home, "escritos"));
    }
    if (plan) {
      const used = el("details", { class: "stale" },
        el("summary", { text: `Plan revisado a las ${formatTime(plan.at)} (ya usado)` }));
      used.append(renderPlan(plan.run, plan.at, true));
      frag.append(used);
    }
    return frag;
  }

  // ---- per-screen runner --------------------------------------------------------
  const SCREENS = {
    diagnostico: {
      prefix: "diag",
      command: "run_doctor",
      args: () => undefined,
      busy: "Diagnosticando",
      paint(run, at) {
        return run.outcome.kind === "report" ? renderReport(run, at, false) : [
          failurePanel("diagnostico", run.outcome, at, (b) => runScreen("diagnostico", b)),
          staleResult("diagnostico", renderReport),
        ];
      },
      good: (run) => run.outcome.kind === "report",
      announce(run) {
        const o = run.outcome;
        if (o.kind === "report") {
          const n = Number(o.report.summary.blockers) || 0;
          return n ? `Diagnóstico completado: ${plural(n, "elemento requiere revisión", "elementos requieren revisión")}.`
            : "Diagnóstico completado: sin elementos que requieran revisión.";
        }
        return "No se pudo completar el diagnóstico.";
      },
      meta: (at, seconds) => `Último diagnóstico: ${formatTime(at)}${seconds}.`,
      after() {
        $("upgrade-hint").hidden = !doctorFoundUpgrades();
        markStale(); // a pending recovery found by Doctor disables Apply
        if (screens.catalogo.current) paintScreen("catalogo"); // fresh statuses
      },
    },
    sincronizacion: {
      prefix: "sync",
      command: "preview_sync",
      args: () => ({ options: readOptions() }),
      busy: "Calculando la vista previa",
      paint(run, at) {
        if (run.phase === "apply") return renderApplyResult(run, at);
        if (run.outcome.kind === "plan") return renderPlan(run, at, false);
        if (run.outcome.kind === "plannerRefusal") return renderRefusal(run, at);
        return [failurePanel("sincronizacion", run.outcome, at, (b) => runScreen("sincronizacion", b)),
          staleResult("sincronizacion", renderPlan)];
      },
      good: (run) => run.outcome.kind === "plan",
      announce(run) {
        const o = run.outcome;
        if (run.phase === "apply") return APPLY_ANNOUNCE(o);
        if (o.kind === "plan") {
          return o.status === "unchanged" ? "Vista previa lista: sin cambios."
            : `Vista previa lista: ${plural(o.changes.length, "cambio propuesto", "cambios propuestos")}; nada se ha escrito.`;
        }
        if (o.kind === "plannerRefusal") return "La planificación se detuvo; no se escribió ningún archivo.";
        return "No se pudo calcular la vista previa.";
      },
      meta: (at, seconds) => `Última vista previa: ${formatTime(at)}${seconds}.`,
      after: markStale,
    },
    catalogo: {
      prefix: "cat",
      command: "read_catalog",
      args: () => undefined,
      busy: "Leyendo el catálogo",
      paint(run, at) {
        return run.outcome.kind === "catalog" ? renderCatalog(run, at)
          : failurePanel("catalogo", run.outcome, at, (b) => runScreen("catalogo", b));
      },
      good: (run) => run.outcome.kind === "catalog",
      announce: (run) => run.outcome.kind === "catalog" ? "Catálogo listo." : "No se pudo leer el catálogo.",
      meta: (at, seconds) => `Catálogo leído a las ${formatTime(at)}${seconds}.`,
      after() {},
    },
  };

  // ---- Catálogo --------------------------------------------------------------
  // Plain-Spanish summaries of the first-party components (the original
  // descriptions, in English, stay available under "Más información").
  const COMPONENT_ES = {
    "architecture-review": "Revisa la arquitectura: límites entre módulos, contratos de API, escalabilidad y compromisos de diseño antes de cambios importantes.",
    "ux-ui": "Diseña y revisa interfaces accesibles: flujos, estados de carga y error, diseño adaptable y verificación visual real.",
    "backend-engineering": "Implementa y revisa APIs, lógica de negocio, bases de datos y tareas en segundo plano, con autorización y fiabilidad.",
    "code-review": "Revisa cambios de código buscando defectos concretos, fallos de autorización, accesibilidad y validaciones que faltan.",
    "application-security-audit": "Audita la seguridad de aplicaciones, APIs y pipelines con hallazgos basados en evidencia y correcciones seguras.",
    "hub-ux-auditor": "Segunda opinión independiente sobre experiencia de usuario y accesibilidad de interfaces y flujos complejos.",
    "hub-backend-auditor": "Auditoría independiente de seguridad y fiabilidad de backend: APIs, permisos, persistencia y procesos asíncronos.",
    "hub-architecture-advisor": "Análisis de compromisos de arquitectura cuando un cambio afecta límites, escala, esquemas o interfaces duraderas.",
    "hub-code-reviewer": "Revisión de código independiente para cambios relevantes, regresiones y riesgos de seguridad.",
    "hub-application-security-auditor": "Auditoría independiente de seguridad de aplicaciones: fronteras de confianza, entradas, secretos y dependencias.",
  };
  const CLIENT_NAMES = { claude: "Claude Code", codex: "Codex" };

  function installedStatus(client, component) {
    const last = screens.diagnostico.lastGood;
    if (!last) return null;
    const field = component.kind === "skill" ? "skills" : "specialists";
    const entry = ((last.run.outcome.report.clients[client] || {})[field] || {})[component.name];
    return describe(STATUS, entry ? entry.status : "missing");
  }

  function renderCatalog(run, at) {
    const o = run.outcome;
    const frag = document.createDocumentFragment();
    const last = screens.diagnostico.lastGood;
    const skills = o.components.filter((c) => c.kind === "skill");
    const specialists = o.components.filter((c) => c.kind === "specialist");
    let differing = 0;
    const card = (component) => {
      const statuses = ["claude", "codex"].map((client) => [client, installedStatus(client, component)]);
      const differ = statuses[0][1] && statuses[1][1] && statuses[0][1].raw !== statuses[1][1].raw;
      if (differ) differing += 1;
      const info = el("details", { class: "more" }, el("summary", { text: "Más información" }),
        el("dl", {},
          component.kind === "specialist" ? [el("dt", { text: "Aplica la skill" }), el("dd", { text: component.skill || "—" })] : null,
          component.focus && component.focus.length ? [el("dt", { text: "En qué se fija" }),
            el("dd", {}, el("ul", {}, component.focus.filter(Boolean).map((f) => el("li", { text: f }))))] : null,
          component.deliverable ? [el("dt", { text: "Qué entrega" }), el("dd", { text: component.deliverable })] : null,
          el("dt", { text: "Descripción original (inglés)" }), el("dd", { text: component.description || "—" }),
          el("dt", { text: "Ubicación" }),
          el("dd", {}, pathText("span", { class: "path" }, homePath(o.home, component.paths.claude)), el("br"),
            pathText("span", { class: "path" }, homePath(o.home, component.paths.codex)))));
      const notes = [];
      if (differ) notes.push("Distinto en cada cliente.");
      if (statuses.some(([, s]) => s && s.raw === "modified")) notes.push("Modificado localmente: SYNCODE no lo actualizará.");
      const upgrade = statuses.some(([, s]) => s && s.raw === "upgrade-available");
      return el("li", { class: "panel component" },
        el("h3", { text: component.name }),
        el("p", { class: "summary", text: COMPONENT_ES[component.name] || component.description || "Sin descripción." }),
        component.kind === "specialist" ? el("p", { class: "note", text: "Solo analiza y recomienda; no edita archivos ni ejecuta cambios." }) : null,
        el("ul", { class: "client-status" }, statuses.map(([client, status]) => el("li", {},
          `${CLIENT_NAMES[client]}: `, status ? statusChip(status) : el("span", { class: "label", text: "Sin datos (ejecuta Diagnóstico)" })))),
        notes.length ? el("p", { class: "note", text: notes.join(" ") }) : null,
        upgrade ? el("p", { class: "note" }, el("a", { href: "#sincronizacion", class: "link", text: "Revisar la actualización en Sincronización" })) : null,
        info);
    };
    const skillList = el("ul", { class: "components" }, skills.map(card));
    const specialistList = el("ul", { class: "components" }, specialists.map(card));
    frag.append(el("p", { class: "meta-line", text: [
      plural(skills.length, "skill", "skills"),
      plural(specialists.length, "especialista", "especialistas"),
      last ? (differing ? plural(differing, "componente difiere entre clientes", "componentes difieren entre clientes") : "sin diferencias entre clientes") : null,
      last ? `Estado según el diagnóstico de las ${formatTime(last.at)}` : "Sin datos de estado: ejecuta Diagnóstico",
    ].filter(Boolean).join(" · ") }));
    frag.append(el("section", { "aria-labelledby": "cat-skills" },
      el("h2", { id: "cat-skills", text: "Skills" }),
      el("p", { class: "note", text: "Conocimiento especializado que Claude Code y Codex cargan cuando la tarea lo requiere." }),
      skillList));
    frag.append(el("section", { "aria-labelledby": "cat-specialists" },
      el("h2", { id: "cat-specialists", text: "Especialistas" }),
      el("p", { class: "note", text: "Asesores de segunda opinión, de solo lectura, que el agente principal puede consultar." }),
      specialistList));
    const others = el("section", { "aria-labelledby": "cat-others" },
      el("h2", { id: "cat-others", text: "Otros elementos instalados" }),
      el("p", { class: "note", text: "Encontrados en las carpetas de skills y agentes de cada cliente. No son de SYNCODE: SYNCODE no los modifica ni los revisa." }));
    for (const client of ["claude", "codex"]) {
      const folders = o.unmanaged[client];
      const entries = [...folders.skills.entries.map((e) => ({ ...e, kind: "Skill" })),
        ...folders.agents.entries.map((e) => ({ ...e, kind: "Agente" }))];
      const box = el("details", { class: "others" },
        el("summary", { text: `${CLIENT_NAMES[client]} · ${entries.length}` }));
      box.append(entries.length ? el("ul", { class: "rows" }, entries.map((e) => el("li", { class: "row" },
        el("span", { class: "name" }, `${e.kind}: ${e.name}`,
          el("span", { class: "hint", text: e.link ? "Enlace o junction: no se inspecciona" : (e.description || "Sin descripción") })))))
        : el("p", { class: "note", text: "No se encontraron otros elementos." }));
      if (folders.skills.truncated || folders.agents.truncated) {
        box.append(el("p", { class: "note", text: "La lista está recortada: hay más elementos de los que se muestran." }));
      }
      others.append(box);
    }
    frag.append(others);
    return frag;
  }

  // ---- Recover -----------------------------------------------------------------
  function recoverAction() {
    const box = el("div", { class: "recover-action" });
    if (recoverToken) {
      const open = el("button", { type: "button", class: "primary", id: "recover-open", text: "Recuperar operación interrumpida…" });
      open.addEventListener("click", openRecoverDialog);
      box.append(open);
    } else {
      box.append(el("p", { class: "note", text: "Vuelve a diagnosticar para habilitar «Recuperar»." }));
    }
    const manual = recoverySteps();
    if (manual) box.append(el("details", {}, el("summary", { text: "Alternativa manual" }), manual));
    return box;
  }

  function openRecoverDialog() {
    if (writing || !recoverToken) return;
    const dialog = $("recover-dialog");
    dialog.returnValue = "";
    dialog.dataset.token = recoverToken;
    dialog.showModal();
  }

  function onRecoverDialogClose() {
    const dialog = $("recover-dialog");
    const token = dialog.dataset.token;
    delete dialog.dataset.token;
    if (dialog.returnValue !== "recover" || !token || token !== recoverToken) {
      const opener = $("recover-open");
      (opener || $("diag-h1")).focus();
      return;
    }
    runRecover(token);
  }

  function setWriting(on) {
    writing = on;
    for (const control of $("sync-form").elements) control.disabled = on;
    for (const id of ["diag-run", "cat-run"]) {
      if (on) $(id).setAttribute("aria-disabled", "true");
      else $(id).removeAttribute("aria-disabled");
    }
  }

  async function runRecover(token) {
    if (writing || screens.diagnostico.running) return;
    recoverToken = null; // single use, also in the UI
    setWriting(true);
    const region = $("diag-recover-result");
    region.replaceChildren(el("section", { class: "panel loading", tabindex: "-1", id: "diag-recovering" },
      el("span", { class: "spinner", "aria-hidden": "true" }),
      el("p", { text: "Recuperando la operación interrumpida… No cierres la app." })));
    $("diag-recovering").focus();
    announce("diagnostico", "Recuperando…");
    const call = queue.then(() => invokeCommand("recover_transaction", { token }));
    queue = call.catch(() => undefined);
    let run;
    try {
      run = await call;
    } catch (error) {
      run = { outcome: { kind: "unknown", detail: String((error && error.message) || error) } };
    }
    setWriting(false);
    region.replaceChildren(renderRecoverResult(run.outcome, new Date()));
    const heading = $("diag-recover-title");
    if (heading && active === "diagnostico") heading.focus();
    announce("diagnostico", RECOVER_TEXT(run.outcome).title);
    runScreen("diagnostico", null, true); // the real state comes from Doctor
  }

  function RECOVER_TEXT(o) {
    switch (o.kind) {
      case "recovered":
        return {
          level: "ok",
          title: "Recuperado",
          body: {
            "rolled-back": "Tus archivos volvieron al estado anterior a la operación interrumpida. Si querías esos cambios, genera una nueva vista previa en Sincronización.",
            committed: "La operación ya estaba completa; solo faltaba limpiar. No hace falta repetirla.",
            "not-started": "La operación se interrumpió antes de escribir nada. Tus archivos no cambiaron.",
            unknown: "Se eliminó un resto vacío de una operación anterior. Revisa el diagnóstico actualizado.",
          }[o.outcome] || "La operación se recuperó.",
        };
      case "noTransaction":
        return { level: "neutral", title: "No había nada que recuperar", body: "Puede que otra herramienta ya la hubiera recuperado." };
      case "busy":
        return { level: "warn", title: "No se hizo nada: otra operación de SYNCODE está en curso", body: "Puede ser otra ventana o la línea de comandos. Espera a que termine y vuelve a diagnosticar." };
      case "stillPending":
        return o.code === "cleanup-failed"
          ? { level: "warn", title: "Recuperación casi completa: falta limpiar", body: "Vuelve a diagnosticar y pulsa «Recuperar» otra vez para terminar la limpieza." }
          : { level: "block", title: "No se pudo recuperar de forma segura", body: "SYNCODE se detuvo para no dañar tus archivos. No sincronices hasta revisarlo manualmente (ver detalle técnico)." };
      case "failed":
        return { level: "block", title: "La recuperación falló", body: "No queda ninguna operación pendiente, pero no se pudo confirmar el estado de todos los archivos. Revisa el diagnóstico actualizado." };
      case "notAuthorized":
        return { level: "warn", title: "No se hizo nada: la autorización caducó", body: "El diagnóstico se está actualizando; si sigue habiendo una operación pendiente, vuelve a pulsar «Recuperar»." };
      case "transport":
        return { level: "failure", title: "No se ejecutó la recuperación", body: transportText(o.code, SUBJECTS.diagnostico)[1] };
      default:
        return { level: "failure", title: "No se pudo confirmar qué pasó", body: "Mira el diagnóstico actualizado." };
    }
  }

  function renderRecoverResult(o, at) {
    const text = RECOVER_TEXT(o);
    const failure = text.level === "failure";
    const hide = el("button", { type: "button", class: "secondary", text: "Ocultar" });
    hide.addEventListener("click", () => { $("diag-recover-result").replaceChildren(); $("diag-h1").focus(); });
    const details = [["Resultado", o.kind], ["Hora", formatTime(at)]];
    if (o.outcome) details.push(["Estado de la operación", o.outcome]);
    if (o.code) details.push(["Código", o.code]);
    if (o.message) details.push(["Mensaje del núcleo", o.message]);
    if (o.detail) details.push(["Detalle", o.detail]);
    const steps = o.kind === "stillPending" && o.code !== "cleanup-failed" ? recoverySteps() : null;
    return el("section", { class: failure ? "panel failure" : `panel verdict level-${text.level}`, "aria-labelledby": "diag-recover-title" },
      icon(failure ? "failure" : text.level),
      el("div", {},
        el("p", { class: "origin", text: "Recuperación" }),
        el("h2", { id: "diag-recover-title", tabindex: "-1", text: text.title }),
        el("p", { text: text.body }),
        steps,
        el("details", {}, el("summary", { text: "Detalle técnico" }),
          el("dl", {}, details.map(([k, v]) => [el("dt", { text: k }), pathText("dd", {}, String(v))]))),
        el("div", { class: "actions" }, hide)));
  }

  function paintScreen(name) {
    const state = screens[name];
    const spec = SCREENS[name];
    if (!state.current || state.running) return;
    const target = $(`${spec.prefix}-result`);
    const content = spec.paint(state.current.run, state.current.at);
    target.replaceChildren(...[content].flat().filter(Boolean));
    target.setAttribute("aria-busy", "false");
    spec.after();
  }

  function updateFooter(run) {
    const p = run.provenance || {};
    $("foot").textContent = [
      `SYNCODE Desktop ${p.appVersion || "?"}`,
      p.pythonVersion ? `Python integrado ${p.pythonVersion}` : "Python integrado: desconocido",
      p.sourceCommit ? `núcleo ${p.sourceCommit.slice(0, 12)}` : null,
    ].filter(Boolean).join(" · ");
  }

  async function runScreen(name, focusAfter, silent) {
    const state = screens[name];
    const spec = SCREENS[name];
    if (state.running || (writing && !silent)) return;
    const button = $(`${spec.prefix}-run`);
    const result = $(`${spec.prefix}-result`);
    const id = ++state.runId;
    state.running = true;
    button.setAttribute("aria-disabled", "true");
    result.setAttribute("aria-busy", "true");
    if (!silent) announce(name, `${spec.busy}…`);
    if (state.current || name !== "diagnostico") result.replaceChildren(loadingPanel(spec.busy));
    const args = spec.args(); // options as chosen when the user asked
    const other = Object.keys(screens).find((key) => key !== name && screens[key].running);
    if (other) $(`${spec.prefix}-meta`).textContent = `En espera: ${screens[other].title.toLowerCase()} en curso…`;
    const invoke = window.__TAURI__ && window.__TAURI__.core && window.__TAURI__.core.invoke;
    const call = queue.then(async () => {
      $(`${spec.prefix}-meta`).textContent = `${spec.busy}…`;
      if (typeof invoke !== "function") throw new Error("bridge");
      return invoke(spec.command, args);
    });
    queue = call.catch(() => undefined);
    let run;
    try {
      run = await call;
    } catch (error) {
      run = { outcome: { kind: "transport", code: "bridge", detail: String((error && error.message) || error), exitCode: null } };
    }
    state.running = false;
    button.removeAttribute("aria-disabled");
    if (id !== state.runId) return; // a newer run superseded this one
    if (run.outcome.kind === "transport" && run.outcome.code === "busy") {
      // Not a result: keep whatever was shown before and say why.
      if (state.current) paintScreen(name);
      else result.replaceChildren(failurePanel(name, run.outcome, new Date(), (b) => runScreen(name, b)));
      result.setAttribute("aria-busy", "false");
      $(`${spec.prefix}-meta`).textContent = "Otra operación estaba en curso; vuelve a intentarlo.";
      announce(name, "Otra operación estaba en curso; no se ejecutó.");
      return;
    }
    const at = new Date();
    if (name === "diagnostico") recoverToken = run.recoverToken || null;
    if (name === "sincronizacion") recoverToken = null;
    if (typeof run.recoveryCommand === "string") recoveryCommand = run.recoveryCommand;
    if (typeof run.userHome === "string") homes.add(run.userHome);
    if (run.outcome.kind === "report" && typeof run.outcome.report.home === "string") homes.add(run.outcome.report.home);
    state.current = { run, at };
    if (spec.good(run)) state.lastGood = { run, at };
    paintScreen(name);
    updateFooter(run);
    const seconds = typeof run.durationMs === "number" ? ` en ${(run.durationMs / 1000).toFixed(1)} s` : "";
    $(`${spec.prefix}-meta`).textContent = spec.meta(at, seconds);
    if (!silent) announce(name, spec.announce(run));
    // Keep focus on the trigger if it is still on the visible screen.
    if (focusAfter && name === active) (document.contains(focusAfter) ? focusAfter : button).focus();
  }

  function announce(name, text) {
    // One live region for the whole app; results of a hidden screen are prefixed.
    $("live").textContent = name === active ? text : `${screens[name].title}: ${text}`;
  }

  // ---- routing ----------------------------------------------------------------
  function routeFromHash() {
    const name = location.hash.replace(/^#/, "");
    return Object.prototype.hasOwnProperty.call(screens, name) ? name : null;
  }

  function show(name, moveFocus) {
    active = name;
    for (const key of Object.keys(screens)) {
      $(`screen-${key}`).hidden = key !== name;
    }
    for (const link of document.querySelectorAll("nav.sections a")) {
      if (link.dataset.screen === name) link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
    }
    document.title = `SYNCODE — ${screens[name].title}`;
    if (moveFocus) $(`${SCREENS[name].prefix}-h1`).focus();
    if (name === "catalogo" && !screens.catalogo.current && !screens.catalogo.running) runScreen("catalogo", null);
  }

  function onHashChange() {
    const name = routeFromHash();
    if (!name) {
      history.replaceState(null, "", `#${active}`);
      return;
    }
    show(name, true);
  }

  document.addEventListener("DOMContentLoaded", () => {
    const initial = routeFromHash();
    if (!initial) history.replaceState(null, "", "#diagnostico");
    show(initial || "diagnostico", false);
    window.addEventListener("hashchange", onHashChange);

    $("diag-run").addEventListener("click", () => {
      $("diag-recover-result").replaceChildren(); // a manual diagnosis starts fresh
      runScreen("diagnostico", $("diag-run"));
    });
    $("sync-form").addEventListener("submit", (event) => {
      event.preventDefault();
      runScreen("sincronizacion", $("sync-run"));
    });
    $("sync-form").addEventListener("change", markStale);
    $("show-paths").addEventListener("change", refreshPaths);
    $("apply-dialog").addEventListener("close", onApplyDialogClose);
    $("recover-dialog").addEventListener("close", onRecoverDialogClose);
    $("cat-run").addEventListener("click", () => {
      runScreen("diagnostico", null, true); // statuses come from a fresh diagnosis
      runScreen("catalogo", $("cat-run"));
    });
    document.addEventListener("keydown", (event) => {
      // F5 / Ctrl+R run the active screen's action instead of reloading.
      if (event.key === "F5" || ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "r")) {
        event.preventDefault();
        if (writing || $("apply-dialog").open || $("recover-dialog").open) return;
        runScreen(active, $(`${SCREENS[active].prefix}-run`));
      }
    });
    document.addEventListener("contextmenu", (event) => event.preventDefault());
    // Diagnóstico runs on start (read-only, no options). The preview runs
    // only on request, with the options the user chose.
    runScreen("diagnostico", null);
  });
})();
