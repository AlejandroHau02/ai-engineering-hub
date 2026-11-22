# SYNCODE Desktop 0.4.0 — cierre funcional (Diagnóstico, Sincronización, Catálogo y Recuperación)

Aplicación de escritorio para Windows 11 con tres secciones y la recuperación integrada en Diagnóstico:

- **Diagnóstico**: resultado real de [SYNCODE Doctor](../scripts/doctor.py).
- **Sincronización**: vista previa calculada por el planificador real (`sync.py --preview`). Muestra el objetivo y el alcance elegidos, los cambios propuestos agrupados por cliente y un identificador técnico del plan, que no autoriza ninguna ejecución.
- **Catálogo** (Fase 3): las 5 skills y los 5 especialistas de SYNCODE, qué hace cada uno, su estado en Claude Code y Codex (según el último diagnóstico) y otros elementos instalados que SYNCODE no gestiona. Solo consulta (`catalog.py`).
- **Recuperar** (Fase 3): cuando Diagnóstico detecta una operación interrumpida, «Recuperar operación interrumpida…» ejecuta, tras confirmar, el `sync.py --recover` del núcleo con un token de un solo uso ([ADR-0010](../docs/ADR-0010.md)).
- **Aplicar** (Fase 2B): tras una confirmación explícita, ejecuta **exactamente** el plan revisado con `sync.py --apply --expect-plan <huella>`. La huella se guarda solo en Rust y la UI recibe un id de un solo uso. El núcleo vuelve a comprobar el plan bajo su bloqueo transaccional y no aplica nada si algo cambió. Antes de cada proceso, la app verifica la integridad de todo el runtime y el núcleo contra una tabla compilada en el ejecutable ([ADR-0009](../docs/ADR-0009.md)).

Todo se ejecuta con un **Python embebido** dentro del paquete y consume el contrato JSON `schemaVersion 1` ([contrato](../docs/SYNCODE_CLI_CONTRACT.md)). Decisiones: [ADR-0007](../docs/ADR-0007.md), [ADR-0008](../docs/ADR-0008.md), [ADR-0009](../docs/ADR-0009.md) y [ADR-0010](../docs/ADR-0010.md). Evaluación de los especialistas que se usaron para construirla: [SYNCODE_SPECIALIST_EVALUATION.md](../docs/SYNCODE_SPECIALIST_EVALUATION.md).

**Alcance:** diagnosticar, consultar el catálogo, generar planes, aplicar cambios revisados y recuperar transacciones, sin depender del CLI. No incluye Remove, instalación individual de componentes, marketplace, actualizaciones automáticas, cuentas ni telemetría. Se distribuye como instalador NSIS (versiones estables) y como **ZIP portable** de prueba.

## Arquitectura

```text
WebView2 (ui/: HTML + CSS + JS sin frameworks)
   │  invoke("run_doctor")                      invoke("preview_sync", {options})
   ▼                                              (Target enum + 2 bool, deny_unknown_fields)
Rust  doctor.rs · sync_preview.rs  ──►  runner.rs (ejecutor común cerrado)
   │  python.exe -I -B syncode/scripts/doctor.py --target both --format json
   │  python.exe -I -B syncode/scripts/sync.py --preview --target <t> --format json [--specialists] [--upgrade-skills]
   │  sin shell · argv solo literales · entorno allow-list · timeout 20/30 s · stdout ≤ 1 MiB · una ejecución a la vez
   ▼
Python embebido 3.12.10 (python/, ._pth fijo) + copia del núcleo (syncode/)
```

- **Superficie cerrada:** seis comandos. `run_doctor`, `read_catalog` (sin parámetros) y `preview_sync` (opciones cerradas) son de lectura. `discard_plan` no recibe parámetros. `apply_sync` y `recover_transaction` solo reciben un id de un solo uso. La capability concede únicamente `allow-run-doctor`, `allow-preview-sync`, `allow-read-catalog`, `allow-discard-plan`, `allow-apply-sync` y `allow-recover-transaction`; no hay plugins (shell, fs, http, dialog) ni permisos `core:*`. La CSP prohíbe scripts y estilos en línea y contenido remoto. Ninguna opción puede expresar `--apply`, `--recover`, `--expect-plan`, `--home` ni `--approved-skills`; además el núcleo rechaza `--preview` junto a `--apply`/`--recover`.
- **Vista previa:** Rust exige `transaction.outcome = not-started`, un plan sin vincular, rutas bajo `.claude/`, `.codex/` o `.agents/` y estados permitidos. Al webview solo le llega un DTO: sin hashes por archivo, sin `events[]` y con el identificador del plan abreviado. Los rechazos del planificador (`skill-upgrade-available`, …) se muestran como resultado, nunca como fallo de transporte.
- **Entorno del proceso hijo:** `env_clear()` y solo `SystemRoot`, `SystemDrive`, `USERPROFILE`, `HOMEDRIVE`, `HOMEPATH`, `PATH`, `PATHEXT`, `TEMP`, `TMP` y `NoDefaultCurrentDirectoryInExePath=1`. `PATH` solo sirve para que Doctor detecte `claude`/`codex` con `shutil.which`; nunca los ejecuta.
- **Aislamiento de Python:** `-I` (ignora `PYTHON*`, site de usuario, directorio del script y cwd), `-B` (no escribe `__pycache__`) y `python312._pth` reescrito a `python312.zip`, `.` y `..\syncode\scripts`, sin `import site`. `sys.path` no depende de `PYTHONPATH`, `PYTHONHOME`, del cwd ni del Python global.
- **Transporte frente a diagnóstico:** Rust valida el contrato y devuelve `report` (exit 0/2), `doctorError` (exit 1/130 con `status:"error"`) o `transport` con un código propio (`bundleIncomplete`, `spawnFailed`, `timeout`, `outputTooLarge`, `noOutput`, `invalidJson`, `contractMismatch`, `processFailed`, `busy`). La UI presenta los fallos de transporte con un tratamiento neutro y el texto «no se evaluó el estado de la instalación»; nunca como bloqueos.
- **Privacidad en pantalla:** el home del usuario se abrevia a `~` salvo que se active «Mostrar rutas completas» (no persistente).

## Requisitos de compilación (Windows 11 x64)

| Herramienta | Versión probada | Notas |
| --- | --- | --- |
| Rust (rustup, `stable-x86_64-pc-windows-msvc`) | 1.100 (cargo 1.100.0) | `winget install Rustlang.Rustup` |
| Visual Studio Build Tools | VS 18 Build Tools, MSVC 14.52, Windows SDK 10.0.26100 | Carga «Desarrollo para escritorio con C++» (`Microsoft.VisualStudio.Workload.VCTools`) |
| Tauri CLI | 2.12.6 | `cargo install tauri-cli --version "=2.12.6" --locked` |
| WebView2 Runtime | incluido en Windows 11 | El instalador NSIS descarga el bootstrapper si falta |
| Python del sistema | 3.10+ | Solo para preparar recursos y ejecutar pruebas; **la app no lo usa** |

No se necesita Node.js ni npm.

## Compilar y ejecutar

Desde la raíz del repositorio:

```powershell
# 1. Preparar el runtime embebido (descarga python.org, verifica SHA-256 fijado)
python desktop/scripts/stage_runtime.py
#    sin red: --archive C:\ruta\python-3.12.10-embed-amd64.zip

# 2. Prueba de aislamiento del runtime fuera del repositorio
python -I desktop/scripts/smoke_runtime.py

# 3. Desarrollo (ventana con recarga del frontend)
cd desktop/src-tauri
cargo tauri dev

# 4. Pruebas Rust (unitarias + integración contra el runtime preparado)
cargo test

# 5. Instalador NSIS por usuario (sin privilegios de administrador)
cargo tauri build
#    → target/release/bundle/nsis/SYNCODE_0.4.0_x64-setup.exe
#    instala syncode-desktop.exe, python/ y syncode/ (por defecto en %LOCALAPPDATA%)

# 6. ZIP portable de prueba (desde la raíz del repo; lista exacta + SHA256SUMS)
cd ../..
python desktop/scripts/package_portable.py build --exe desktop/src-tauri/target/release/syncode-desktop.exe --out dist
python desktop/scripts/package_portable.py verify dist/SYNCODE-0.2.0-<commit7>-win-x64.zip

# 7. Evidencia: abrir, navegar, capturar y cerrar (instalado o ZIP extraído)
desktop\scripts\launch_smoke.ps1 -Exe C:\ruta\syncode-desktop.exe -Out diag.png -ExpectText 'Diagnóstico completado'
desktop\scripts\launch_smoke.ps1 -Exe C:\ruta\syncode-desktop.exe -Out sync.png -Isolated -Screen sincronizacion -HomeOverride C:\tmp\home-vacio -ExpectText 'Vista previa:'
python desktop/scripts/home_snapshot.py take antes.json   # … y después: take + compare
```

**Paquete portable**: carpeta raíz `SYNCODE-<versión>-<commit7>-win-x64/` con `syncode-desktop.exe`, `python/`, `syncode/` y `README-PORTABLE.txt`. No necesita instalación, Python ni privilegios. Requiere WebView2 Runtime (incluido en Windows 11). Hay que **extraerlo completo** en una carpeta propia: el `.exe` ejecutado desde dentro del ZIP informa de que faltan componentes. Se verifica con `SHA256SUMS` y, en CI, con una attestation de procedencia. No está firmado ni se actualiza solo. El ejecutable se enlaza con `/DEPENDENTLOADFLAG:0x800`, de modo que sus importaciones estáticas se resuelven solo desde System32.

`-Isolated` arranca la app sin ningún Python en `PATH`, con `PYTHONPATH`/`PYTHONHOME` apuntando a módulos que suplantan `sync`, `json`, `cli_contract`, `doctor` y `specialists`, y desde ese mismo directorio hostil; falla si alguno se importa. `-Screen sincronizacion` abre la sección y pulsa «Generar vista previa» mediante UI Automation. `-HomeOverride` ejecuta la app contra un home desechable. Los iconos se regeneran con `python desktop/scripts/make_icons.py`.

El job `desktop-windows` de CI hace lo siguiente en `windows-latest`:

- ejecuta los pasos 1–7;
- valida el ZIP extraído fuera del repo con Doctor y la vista previa reales, sin Python global, tanto directamente como desde la app;
- comprueba con `home_snapshot.py` que las rutas gestionadas del home no cambian;
- publica los artefactos `syncode-desktop-portable` (ZIP + `SHA256SUMS`) y `syncode-desktop-windows` (instalador + capturas).

`stage_runtime.py` rechaza preparar recursos si `scripts/`, `core/`, `adapters/`, `skills/`, `specialists/` o `registry/` tienen cambios sin confirmar (`--allow-dirty` solo para desarrollo; el manifiesto registra `dirty: true`). Cada paquete incluye `syncode/BUNDLE_MANIFEST.json` con el commit de origen, la versión y el hash del runtime y el SHA-256 de cada archivo copiado. El manifiesto es evidencia de procedencia, no un control de integridad en tiempo de ejecución.

## Pruebas

- `tests/test_sync_preview.py`: clasificación `target`/`component`, `--preview` excluyente con `--apply`/`--recover` y **ausencia de escrituras, red y subprocesos** mediante audit hooks de CPython. Cubre las 12 combinaciones de opciones de Desktop sobre 5 estados de home, con `--apply` como control negativo.
- `tests/test_desktop_portable.py`: listado exacto, raíz única, determinismo, rechazo de árbol sucio, de archivos no declarados (DLL plantada) o alterados y verificación de ZIP manipulado.
- `python -m unittest discover -s tests -v` incluye `tests/test_desktop_runtime.py`: hash del archivo, extracción segura (rutas absolutas, `..`, enlaces), `._pth`, lista explícita igual al grafo de imports de Doctor, manifiesto y, si el runtime está preparado, la prueba de aislamiento.
- `cargo test` (en `desktop/src-tauri`) cubre:
  - las reglas de contrato comunes y las de Doctor;
  - el argv exacto de las 12 combinaciones de vista previa;
  - el rechazo de opciones IPC inesperadas;
  - la clasificación de la vista previa: estados de escritura, planes vinculados, rutas inseguras, `APPROVED-CREATE`, rechazos y proyección sin hashes;
  - la allow-list de entorno y la ejecución única.
  - Con el runtime preparado: Doctor y vista previa reales contra homes temporales, comprobando que **el home no cambia**, junto con fallos simulados.

## Smart App Control

Con **Smart App Control** activo, Windows 11 bloquea por reputación ejecutables nuevos sin firma (`os error 4551`, «Una directiva de Control de aplicaciones bloqueó este archivo»). Cargo compila los *build scripts* de las dependencias como ejecutables temporales, de modo que `cargo build`/`cargo test` de este proyecto fallan de forma determinista en un equipo con SAC activo (observado con `selectors`, dependencia de Tauri). Opciones: compilar en CI (`desktop-windows`) o en un equipo sin SAC. El instalador y la app compilados en CI se instalaron y ejecutaron en un equipo con SAC activo sin bloqueo en la prueba de Fase 1, pero eso depende de la reputación en la nube y **no está garantizado**: la distribución requiere firma de código.

## Límites conocidos

- Ejecutables sin firma de código: Smart App Control o SmartScreen pueden bloquear o advertir al instalar o ejecutar. Aplica también al ZIP portable.
- La vista previa no toma el lock: un `--apply` simultáneo desde el CLI puede producir una vista intermedia (se avisa). El planificador se detiene en el primer conflicto.
- El perfil de WebView2 (`%LOCALAPPDATA%\io.github.syncode.desktop`) es compartido por la variante portable y la instalada, y ninguna lo elimina.
- Solo Windows x64. Doctor sigue siendo multiplataforma; el empaquetado no.
- La copia del núcleo es una instantánea del commit de compilación: actualizar SYNCODE exige recompilar (las actualizaciones quedan fuera de esta fase).
- La detección de CLI usa el `PATH` del proceso de la app; un `PATH` modificado después de iniciar sesión puede no reflejarse hasta reiniciarla.
