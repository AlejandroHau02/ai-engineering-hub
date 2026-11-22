# SYNCODE — contratos locales para Desktop (schemaVersion 1)

La aplicación de escritorio podrá consultar los scripts actuales sin crear un servicio ni ejecutar código arbitrario. Todos los comandos son locales, read-only por defecto y operan con rutas validadas.

## Reglas comunes del modo JSON (`--format json`)

- **Un único objeto por invocación** en stdout, seguido de un salto de línea. stderr no forma parte del contrato y es **de mejor esfuerzo**: si está cerrado, roto o ausente, los diagnósticos se descartan sin impedir el JSON ni alterar el código de salida (el descriptor se redirige a devnull para que el cierre del intérprete no lo cambie). Nunca se escriben diagnósticos en stdout.
- **Codificación independiente de la configuración regional:** el JSON se escribe como bytes **ASCII** (subconjunto válido de UTF-8); todo carácter no ASCII se escapa como `\uXXXX`. Un parser JSON obtiene las cadenas originales (por ejemplo, un home `C:\Users\测试`) con cualquier página de códigos de la consola o del pipe. Un home con surrogates UTF-16 sueltos se representa con escapes `\udXXX`; algunos parsers estrictos los rechazan.
- **Errores de argumentos:** también producen JSON (`error.code: "invalid-arguments"`, exit `1`). Se detecta `--format json`, `--format=json` y sus abreviaturas válidas para argparse. Si no se pidió JSON, argparse conserva su comportamiento histórico: uso en stderr y exit `2`.
- **Errores inesperados** (excepciones no previstas por el contrato): JSON con `code: "internal-error"` y un mensaje genérico; **nunca** se incluye el texto de la excepción. En stderr se indica el tipo y cómo obtener el traceback (modo texto).
- **`--help`** imprime ayuda en texto y sale con `0`; no forma parte del contrato JSON y un cliente no debe invocarlo.
- **Interrupción** (`Ctrl+C`/`KeyboardInterrupt`): JSON con `code: "interrupted"` y exit `130`, tras el rollback que corresponda. Si el rollback también falla, la interrupción se conserva igualmente (`interrupted`, `130`) con `outcome: recovery-pending`, `recoveryRequired: true` y el journal preservado para `--recover`. Si el proceso se termina abruptamente (TerminateProcess/SIGKILL) no hay JSON: **ausencia de JSON significa estado desconocido**; el cliente debe ejecutar Doctor, nunca suponer que la operación no empezó.
- `message` es texto para personas: puede contener **rutas locales absolutas** y no debe interpretarse ni enviarse a terceros. `type` es informativo. **La lógica del cliente depende de `code`.**

### Objeto `error` (sync y remove)

`null` en éxito o `{type, code, message}`. Doctor mantiene su forma histórica (ver abajo).

### Códigos de error estables

Los códigos son aditivos: no se renombran ni se reutilizan con otro significado.

| `code` | Significado | Acción sugerida para el cliente |
| --- | --- | --- |
| `invalid-arguments` | Argumentos de CLI no válidos. | Error de integración; no reintentar igual. |
| `unsafe-path` | Destino fuera de home, symlink/junction, padre no directorio o archivo no regular. | Inspección manual. |
| `managed-markers-invalid` | Marcadores gestionados repetidos, incompletos o desordenados. | Revisar el archivo de instrucciones. |
| `skill-upgrade-available` | Una skill instalada difiere; existe vista previa con `--upgrade-skills`. | Ofrecer la vista previa de upgrade. |
| `skill-modified` | Skill/componente modificado localmente o sin baseline reconocido. | Revisión manual; nunca sobrescribir. |
| `destination-conflict` | El destino existe con contenido o archivos ajenos (incluye backups preexistentes). | Revisión manual. |
| `destination-changed` | Un destino cambió entre el plan y la escritura. | Volver a planificar. |
| `unknown-component` | Componente no reconocido por el catálogo o manifiesto. | Error de integración. |
| `transaction-pending` | Existe un workspace de transacción. | Ejecutar `sync.py --recover`. |
| `lock-busy` | Otra instalación o recuperación está activa. | Esperar y reintentar. |
| `lock-unexpected` | El archivo de bloqueo tiene contenido o tipo inesperado. | Inspección manual. |
| `transaction-limit` | Límite de archivos, snapshot o journal excedido. | Inspección manual. |
| `integrity-mismatch` | Bytes finales o snapshots no coinciden con lo previsto. | Doctor y revisión manual. |
| `recovery-blocked` | La recuperación rechazó un journal, snapshot, backup o destino alterado. Se conserva el journal. | Revisión manual (ver `TRANSACTION_RECOVERY.md`). |
| `recovery-required` | La aplicación falló y el rollback no pudo completarse. Se conserva el journal. | Ejecutar `sync.py --recover` tras inspección. |
| `cleanup-failed` | El workspace no pudo eliminarse tras alcanzar el commit (`outcome: committed`), tras un rollback completo (`outcome: rolled-back`) o tras abortar antes de escribir (`outcome: not-started`). | Ejecutar `sync.py --recover`; **no reaplicar**. |
| `approved-skill-rejected` | Verificación de una skill aprobada fallida (hash, árbol, formato, compatibilidad). | Revisión de la aprobación. |
| `registry-invalid` | Manifiesto de aprobaciones inválido. | Revisión del manifiesto. |
| `io-error` | Error del sistema de archivos u otro `OSError`. | Doctor y reintento consciente. |
| `encoding-error` | Contenido no decodificable. | Revisión manual. |
| `operation-failed` | Otro rechazo controlado sin código específico. | Mostrar mensaje; Doctor. |
| `interrupted` | Interrupción del usuario. | Doctor antes de continuar. |
| `internal-error` | Fallo no previsto (posible defecto). | Doctor; reportar el defecto. |

## Diagnóstico

```powershell
python scripts/doctor.py --target both --format json
```

Respuesta JSON de `SYNCODE Doctor`:

- `schemaVersion: 1`, `product: "SYNCODE"`, `home`, `targets`.
- `transaction`: `clear`, `pending-recovery` o `unsafe`; una transacción pendiente bloquea un nuevo sync y requiere recuperación explícita. Doctor no lee el estado del journal: un workspace con commit completado y limpieza pendiente también aparece como `pending-recovery`.
- `lock`: `absent`, `recognized`, `unexpected` o `unsafe`. **La existencia del lock no implica una operación activa.** La lectura no adquiere el lock ni identifica qué proceso lo posee.
- `clients.<target>.executable`: `found/not-found` con ruta si la encuentra en PATH. No ejecuta `claude` ni `codex`; no demuestra compatibilidad con una versión.
- `clients.<target>.instructions/skills/specialists`: estados `current`, `missing`, `modified`, `unmanaged`, `upgrade-available` o `unsafe-or-unreadable`, según aplique. No lee el contenido de rutas inseguras ni incluye contenido privado en la respuesta.
- `summary`: contadores; `blockers > 0` señala modificaciones, rutas inseguras o recuperación pendiente. Las ausencias y CLI no encontrada se reportan, pero no bloquean el diagnóstico.

**Error de Doctor:** `{schemaVersion, product, status: "error", home, targets, error, errorCode}`. Por compatibilidad, `error` sigue siendo **una cadena** (mensaje); el código estable está en el campo hermano `errorCode`. `home` es `null` si no llegó a resolverse (por ejemplo, argumentos inválidos).

Código de salida: `0` diagnóstico completado sin bloqueos; `2` bloqueos que requieren inspección; `1` error del comando (incluidos argumentos inválidos en modo JSON); `130` interrupción en modo JSON.

## Instalación: planes y resultados

```powershell
python scripts/sync.py --target both --format json
```

No instala ni actualiza nada. Respuesta JSON:

- `schemaVersion: 1`, `product: "SYNCODE"`, `home` (`null` si no llegó a resolverse), `targets`.
- `status`: `planned`, `unchanged`, `applied`, `recovered`, `no-pending-transaction` o `error`.
- `changes[]`: `kind` (`CREATE`, `UPDATE`, `UPGRADE`, `APPROVED-CREATE`), ruta **relativa a home**, SHA-256 del contenido a instalar. Nunca contiene contenido de la skill. Si `--apply` falla después de planificar, contiene **lo que se intentó aplicar**; su estado real lo indica `transaction.outcome`. Si el fallo ocurre durante la planificación, es `[]`.
- `events[]`: líneas del registro textual existente capturadas para compatibilidad; son informativas, no un contrato para parsear. Pueden incluir rutas locales de backups, incluso de backups retirados después por un rollback.
- `transaction`: `{outcome, recoveryRequired}` (ver abajo).
- `error`: `null` en éxito o `{type, code, message}`.

### `transaction.outcome` y `recoveryRequired`

`outcome` describe el estado de **los archivos de destino** al terminar el comando:

| `outcome` | Significado |
| --- | --- |
| `not-started` | Ningún destino fue modificado por esta invocación (dry-run, sin cambios, rechazo antes de escribir o recuperación de un journal en preparación). |
| `rolled-back` | Pudo haberse escrito algún destino y todos fueron restaurados a su estado anterior. |
| `committed` | Todos los destinos se escribieron y verificaron y el journal `committed` quedó registrado (**punto de commit**). Nunca se revierte. |
| `recovery-pending` | Los destinos pueden estar parcialmente aplicados; el journal se conserva y se requiere `sync.py --recover`. |
| `unknown` | Normalmente solo en `--recover` (en `--apply` únicamente si un actor externo vacía el workspace): el workspace estaba vacío (sin journal ni datos de recuperación) y se eliminó sin tocar destinos; el resultado de la operación anterior no puede deducirse del workspace. Ejecutar Doctor. |

`recoveryRequired` se calcula al final **observando el disco**: `true` si existe el workspace de transacción, `null` si no pudo determinarse. Combinaciones relevantes:

- `status: applied`, `committed`, `false`: aplicación completa.
- `status: error`, `committed`, `true`, `cleanup-failed`: los cambios **ya están aplicados**; solo falta limpiar el workspace con `--recover`. No debe reaplicarse ni presentarse como fallo de instalación. Antes de informar el error se reintenta la limpieza una vez.
- `status: error`, `committed`, `false`: el error ocurrió justo después del punto de commit (por ejemplo, al sincronizar el directorio); los cambios están aplicados.
- `status: error`, `rolled-back`, `false`: fallo ordinario con rollback completo.
- `status: error`, `rolled-back`, `true`, `cleanup-failed`: rollback completo; solo falta limpiar el workspace con `--recover`.
- `status: error`, `recovery-pending`, `true`, `recovery-required`: rollback incompleto.
- `status: error`, `not-started`, `true`: no se escribió ningún destino, pero quedó un workspace (`transaction-pending`, o `cleanup-failed` si falló la limpieza antes del primer journal o la de un journal en preparación).

Con `--recover`, `outcome` indica cómo terminó **la transacción recuperada**: `rolled-back` (se restauró un lote pendiente), `committed` (el lote ya estaba completo y solo se limpió el workspace), `not-started` (se descartó un journal en preparación, o no había nada que recuperar) o `unknown` (workspace vacío). Un workspace sin journal que contenga cualquier otro archivo **no se elimina**: `recovery-blocked`. Si la recuperación se rechaza, `outcome` es `recovery-pending`. Si la recuperación alcanza un resultado terminal y solo falla la limpieza (incluido el `rmdir` final después de borrar el journal), `outcome` **conserva ese resultado** (`committed`, `rolled-back`, `not-started` o `unknown`) con `error.code: "cleanup-failed"`; un nuevo `--recover` termina la limpieza.

La limpieza elimina el journal **en último lugar**: una limpieza interrumpida deja el journal (recuperable) o un directorio vacío, nunca datos de recuperación huérfanos.

El formato es válido también para `--apply` y `--recover`, que **conservan su semántica y permisos anteriores**. La aplicación deberá comprobar `status`, `transaction`, `error.code` y el código de salida antes de habilitar confirmaciones. Un plan no es un token de autorización ni queda fijado en disco: cada invocación reconstruye el plan y, durante `--apply`, el instalador vuelve a verificar los destinos bajo un bloqueo exclusivo antes de escribir. **No garantiza que el entorno completo permanezca inmutable entre el dry-run y la aplicación** (el plan vinculante queda para la Fase 0B).

Código de salida: `0` operación completada; `1` error (incluidos argumentos inválidos en modo JSON); `130` interrupción en modo JSON. El modo texto es el predeterminado y conserva sus mensajes anteriores; argumentos inválidos en modo texto siguen saliendo con `2`.

## Retirada de componentes

```powershell
python scripts/remove.py --target claude --skill code-review --format json
```

Respuesta: `schemaVersion: 1`, `product`, `status` (`planned`, `unchanged`, `removed` o `error`), `home` (`null` si no llegó a resolverse), `target`, `component` y `backup` (rutas relativas a home o `null`), `error` (`null` o `{type, code, message}`). Códigos de salida como `sync.py`.

## Compatibilidad de esta revisión

Se mantiene `schemaVersion: 1` porque los cambios son aditivos para los consumidores existentes:

- Campos nuevos: `error.code` (sync/remove), `errorCode`, `home` y `targets` en errores de Doctor, `transaction` en sync, `home` en remove.
- Sin valores nuevos de `status` y sin cambios en los valores de `type` emitidos antes.
- Dos cambios de comportamiento que no son estrictamente aditivos: `changes[]` puede ser no vacío con `status: error` (antes siempre `[]` en error), y en modo JSON los argumentos inválidos salen con `1` en lugar del `2` de argparse (que en Doctor colisionaba con «bloqueos»).
- Los bytes cambian (escapes `\uXXXX` en lugar de caracteres sin escapar), pero el JSON decodificado es idéntico.
- Respuestas JSON nuevas donde antes no había JSON: argumentos inválidos, errores inesperados, interrupción y fallos al resolver `--home`.
- Comportamiento corregido: un fallo posterior al punto de commit ya no se reporta como fallo genérico; su `outcome` es `committed`. Un fallo al registrar el primer journal ya no deja un workspace sin journal que bloquee futuras operaciones.

Un cambio incompatible futuro (por ejemplo, unificar el error de Doctor como objeto) exigirá `schemaVersion: 2`.

## Límites y seguridad

No se agregan comandos de shell dinámicos, servidor HTTP, procesos de fondo ni lecturas de proyectos laborales. `--home` es principalmente para pruebas; la GUI tendrá que restringirlo a directorios elegidos conscientemente por el usuario. El JSON no garantiza una operación transaccional simultánea entre archivos ni evita los límites de TOCTOU ya documentados en `TRANSACTION_RECOVERY.md`. El modo texto no cambia su codificación: imprimir rutas no representables en una consola o pipe con página de códigos heredada sigue pudiendo fallar; los clientes deben usar `--format json`.

Los contratos versionados permiten evolucionar la GUI sin acoplarla a mensajes de consola. Los nuevos campos serán aditivos siempre que sea posible; cualquier cambio incompatible exige incrementar `schemaVersion`.
