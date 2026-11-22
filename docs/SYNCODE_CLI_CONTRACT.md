# SYNCODE — contratos locales para Desktop (schemaVersion 1)

La aplicación de escritorio podrá consultar los scripts actuales sin crear un servicio ni ejecutar código arbitrario. Todos los comandos son locales, read-only por defecto y operan con rutas validadas.

## Diagnóstico

```powershell
python scripts/doctor.py --target both --format json
```

Respuesta JSON de `SYNCODE Doctor`:

- `schemaVersion: 1`, `product: "SYNCODE"`, `home`, `targets`.
- `transaction`: `clear`, `pending-recovery` o `unsafe`; una transacción pendiente bloquea un nuevo sync y requiere recuperación explícita.
- `lock`: `absent`, `recognized`, `unexpected` o `unsafe`. **La existencia del lock no implica una operación activa.** La lectura no adquiere el lock ni identifica qué proceso lo posee.
- `clients.<target>.executable`: `found/not-found` con ruta si la encuentra en PATH. No ejecuta `claude` ni `codex`; no demuestra compatibilidad con una versión.
- `clients.<target>.instructions/skills/specialists`: estados `current`, `missing`, `modified`, `unmanaged`, `upgrade-available` o `unsafe-or-unreadable`, según aplique. No lee el contenido de rutas inseguras ni incluye contenido privado en la respuesta.
- `summary`: contadores; `blockers > 0` señala modificaciones, rutas inseguras o recuperación pendiente. Las ausencias y CLI no encontrada se reportan, pero no bloquean el diagnóstico.

Código de salida: `0` diagnóstico completado sin bloqueos; `2` bloqueos que requieren inspección; `1` error del comando.

## Instalación: planes y resultados

```powershell
python scripts/sync.py --target both --format json
```

No instala ni actualiza nada. Respuesta JSON:

- `schemaVersion: 1`, `product: "SYNCODE"`, `home`, `targets`.
- `status`: `planned`, `unchanged`, `applied`, `recovered`, `no-pending-transaction` o `error`.
- `changes[]`: `kind` (`CREATE`, `UPDATE`, `UPGRADE`, `APPROVED-CREATE`), ruta **relativa a home**, SHA-256 del contenido a instalar. Nunca contiene contenido de la skill.
- `events[]`: líneas del registro textual existente capturadas para compatibilidad; son informativas, no un contrato para parsear. Pueden incluir rutas locales de backups.
- `error`: `null` en éxito o `{type, message}` cuando ocurre una excepción controlada.

El formato es válido también para `--apply` y `--recover`, que **conservan su semántica y permisos anteriores**. La aplicación deberá comprobar `status`, código de salida y `changes` antes de habilitar confirmaciones. Un plan no es un token de autorización ni queda fijado en disco: siempre deben recalcularse los cambios bajo el bloqueo del instalador al aplicar.

Código de salida: `0` operación completada; `1` error. El modo texto es el predeterminado y conserva sus mensajes anteriores.

## Límites y seguridad

No se agregan comandos de shell dinámicos, servidor HTTP, procesos de fondo ni lecturas de proyectos laborales. `--home` es principalmente para pruebas; la GUI tendrá que restringirlo a directorios elegidos conscientemente por el usuario. El JSON no garantiza una operación transaccional simultánea entre archivos ni evita los límites de TOCTOU ya documentados en `TRANSACTION_RECOVERY.md`.

Los contratos versionados permiten evolucionar la GUI sin acoplarla a mensajes de consola. Los nuevos campos serán aditivos siempre que sea posible; cualquier cambio incompatible exige incrementar `schemaVersion`.
