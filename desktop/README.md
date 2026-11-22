# SYNCODE Desktop — Fase 1 (prototipo Doctor)

Prototipo ejecutable de la aplicación de escritorio para Windows 11. Muestra una única pantalla, **Diagnóstico**, con el resultado real de [SYNCODE Doctor](../scripts/doctor.py) ejecutado con un **Python embebido** dentro del paquete. El núcleo Python no se modifica: la app consume su contrato JSON `schemaVersion 1` ([contrato](../docs/SYNCODE_CLI_CONTRACT.md)). Decisión de arquitectura: [ADR-0007](../docs/ADR-0007.md).

**Alcance:** solo lectura. No incluye Plan, Apply, Recover, Remove, gestión de skills ni actualizaciones.

## Arquitectura

```text
WebView2 (ui/: HTML + CSS + JS sin frameworks)
   │  invoke("run_doctor")         ← único comando permitido (capability "main")
   ▼
Rust (src-tauri/src/doctor.rs)
   │  python.exe -I -B syncode/scripts/doctor.py --target both --format json
   │  sin shell · argumentos fijos · entorno allow-list · timeout 20 s · stdout ≤ 1 MiB
   ▼
Python embebido 3.12.10 (resources/python, ._pth fijo) + copia del núcleo (resources/syncode)
```

- **Superficie cerrada:** un solo comando Tauri, `run_doctor`, sin parámetros. La capability concede únicamente `allow-run-doctor`; no hay plugins (shell, fs, http, dialog) ni permisos `core:*`. La CSP prohíbe scripts y estilos en línea y contenido remoto.
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
#    → target/release/bundle/nsis/SYNCODE_0.1.0_x64-setup.exe
#    instala syncode-desktop.exe, python/ y syncode/ (por defecto en %LOCALAPPDATA%)

# 6. Evidencia: instalar fuera del repo, abrir, capturar y cerrar
.\target\release\bundle\nsis\SYNCODE_0.1.0_x64-setup.exe /S /D=C:\ruta\fuera\del\repo
..\scripts\launch_smoke.ps1 -Exe C:\ruta\fuera\del\repo\syncode-desktop.exe -Out diag.png
..\scripts\launch_smoke.ps1 -Exe C:\ruta\fuera\del\repo\syncode-desktop.exe -Out diag-aislado.png -Isolated
```

`-Isolated` arranca la app sin ningún Python en `PATH`, con `PYTHONPATH`/`PYTHONHOME` apuntando a módulos que suplantan `sync`, `json`, `cli_contract`, `doctor` y `specialists`, y desde ese mismo directorio hostil; falla si alguno se importa. Los iconos se regeneran con `python desktop/scripts/make_icons.py`.

El job `desktop-windows` de CI ejecuta los pasos 1–6 en `windows-latest` y publica el instalador y las capturas como artefacto `syncode-desktop-windows`.

`stage_runtime.py` rechaza preparar recursos si `scripts/`, `core/`, `adapters/`, `skills/`, `specialists/` o `registry/` tienen cambios sin confirmar (`--allow-dirty` solo para desarrollo; el manifiesto registra `dirty: true`). Cada paquete incluye `syncode/BUNDLE_MANIFEST.json` con el commit de origen, la versión y el hash del runtime y el SHA-256 de cada archivo copiado. El manifiesto es evidencia de procedencia, no un control de integridad en tiempo de ejecución.

## Pruebas

- `python -m unittest discover -s tests -v` incluye `tests/test_desktop_runtime.py`: hash del archivo, extracción segura (rutas absolutas, `..`, enlaces), `._pth`, lista explícita igual al grafo de imports de Doctor, manifiesto y, si el runtime está preparado, la prueba de aislamiento.
- `cargo test` (en `desktop/src-tauri`): reglas de contrato (exit ↔ payload, `schemaVersion`, `product`, datos extra, no ASCII, códigos aditivos), allow-list de entorno, ejecución única y, con el runtime preparado, Doctor real contra un home temporal (exit 0 y exit 2) más fallos simulados: JSON inválido, sin salida, timeout con kill y salida excesiva sin bloqueo.

## Smart App Control

Con **Smart App Control** activo, Windows 11 bloquea por reputación ejecutables nuevos sin firma (`os error 4551`, «Una directiva de Control de aplicaciones bloqueó este archivo»). Cargo compila los *build scripts* de las dependencias como ejecutables temporales, de modo que `cargo build`/`cargo test` de este proyecto fallan de forma determinista en un equipo con SAC activo (observado con `selectors`, dependencia de Tauri). Opciones: compilar en CI (`desktop-windows`) o en un equipo sin SAC. El instalador y la app compilados en CI se instalaron y ejecutaron en un equipo con SAC activo sin bloqueo en la prueba de Fase 1, pero eso depende de la reputación en la nube y **no está garantizado**: la distribución requiere firma de código.

## Límites conocidos del prototipo

- Ejecutables sin firma de código: Smart App Control o SmartScreen pueden bloquear o advertir al instalar o ejecutar.
- Solo Windows x64. Doctor sigue siendo multiplataforma; el empaquetado no.
- La copia del núcleo es una instantánea del commit de compilación: actualizar SYNCODE exige recompilar (las actualizaciones quedan fuera de esta fase).
- La detección de CLI usa el `PATH` del proceso de la app; un `PATH` modificado después de iniciar sesión puede no reflejarse hasta reiniciarla.
