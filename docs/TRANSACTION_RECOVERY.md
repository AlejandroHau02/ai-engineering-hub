# Recuperación de instalaciones por lote — PR #10

Las aplicaciones de `scripts/sync.py --apply` utilizan `scripts/transaction.py`, sin dependencias de terceros. Aplica para instrucciones, skills propias, especialistas y, si se habilita explícitamente, skills externas aprobadas.

## Qué garantiza

1. **Simulación predeterminada:** sin `--apply` no crea directorios ni modifica los archivos instalados.
2. **Preparación completa:** valida todos los destinos y crea el journal y las copias exactas de los originales antes de la primera sustitución.
3. **Escritura por archivo:** utiliza escritura temporal y sustitución atómica por archivo; vuelve a comprobar rutas y hashes y verifica el contenido final de todos los archivos.
4. **Error ordinario:** si una operación falla, revierte el lote (archivos existentes y archivos/directorios recién creados) siempre que el estado permanezca reconocible.
5. **Respaldos convencionales:** después de completar las escrituras, publica respaldos `*.bak.<timestamp>-<token>` de los archivos reemplazados; la publicación del backup es exclusiva y se evita escribir respaldos finales a medias.
6. **Interrupción abrupta:** el journal persistente permite reanudar la recuperación en una nueva ejecución, a petición explícita.

## Punto de commit y resultados verificables

El **punto de commit** es la sustitución atómica del journal con estado `committed`, después de escribir y verificar todos los destinos y publicar los respaldos. A partir de ahí el lote **nunca se revierte**:

- Si falla la eliminación del workspace, se reintenta una vez; si persiste, el comando informa `error.code: "cleanup-failed"` con `transaction.outcome: "committed"` y conserva el journal. `sync.py --recover` solo termina la limpieza y conserva los archivos instalados y sus respaldos. **No reapliques** en ese estado.
- Si el error ocurre dentro de la misma escritura del journal, después de la sustitución atómica (por ejemplo, al sincronizar el directorio en POSIX), el resultado es también `committed`.
- Tras un rollback completo, el journal se marca `rolled-back` antes de borrar los snapshots; si la limpieza se interrumpe, `--recover` solo la termina.
- La limpieza borra el journal en último lugar. Un workspace vacío (o con solo `journal.json.tmp`) se elimina con `--recover` (`outcome: unknown`); con cualquier otro contenido sin journal, la recuperación se bloquea.
- Si falla el registro del primer journal, no se escribió ningún destino: el workspace recién creado se elimina para no bloquear futuras operaciones.

En modo `--format json`, `transaction.outcome` distingue `not-started`, `rolled-back`, `committed` y `recovery-pending`, y `transaction.recoveryRequired` indica si el workspace sigue en disco. Ver [contratos CLI](SYNCODE_CLI_CONTRACT.md).

## Comandos

Revisar un plan sin cambios:

```powershell
python scripts/sync.py --target both
```

Aplicar el plan revisado (no es necesario añadir `--approved-skills` para skills propias):

```powershell
python scripts/sync.py --target both --apply
```

Si se interrumpió el proceso, **no vuelvas a aplicar primero**. Ejecuta:

```powershell
python scripts/sync.py --recover
```

Si la instalación se hizo con `--home <directorio>`, la recuperación debe utilizar exactamente el mismo `--home`. `--recover` es incompatible con `--apply`; no consulta GitHub y no necesita aprobaciones externas.

Cuando la recuperación termina correctamente, muestra `RECOVERED`. Si no hay un lote pendiente, indica `NO PENDING TRANSACTION`. Luego realiza un nuevo dry-run para comprobar el estado antes de reintentar.

## Cuándo bloquea la recuperación

El journal y las copias de preimagen se conservan en `~/.ai-engineering-hub-transaction/` mientras el lote está activo o pendiente de recuperación. El programa se niega a iniciar otro plan si detecta ese workspace. Para evitar una recuperación mientras otro proceso sigue instalando, se utiliza además un bloqueo exclusivo entre procesos en `~/.ai-engineering-hub.sync.lock`, adquirido durante toda la aplicación o recuperación. El archivo de bloqueo **permanece después de terminar** porque eliminarlo permitiría que procesos distintos bloquearan inodos diferentes; no debes borrarlo manualmente. Si el proceso termina inesperadamente, el bloqueo del sistema operativo se libera automáticamente, pero el journal de transacción sigue disponible.

La recuperación verifica hashes de snapshots, hashes de contenido anterior y nuevo, ubicaciones gestionadas, ausencia de rutas escapadas/symlinks/junctions y respaldos publicados. Si un archivo fue modificado por una acción externa y ya no coincide con el valor anterior ni con el nuevo previsto, **no lo sobrescribe**. Tampoco borra respaldos cuyo contenido haya sido alterado. En esas circunstancias deja el journal intacto: revisa el archivo, las preimágenes, los respaldos y las diferencias antes de proceder manualmente.

No elimines el directorio de transacción ni archivos `*.bak.*` como primer intento de reparación. Haz una copia del workspace y solicita revisión técnica si `--recover` no puede concluir.

## Límites reales

**Este mecanismo no hace que varios archivos cambien de forma simultáneamente atómica.** Durante `--apply` otros procesos pueden observar una instalación parcialmente aplicada. Un fallo normal activa rollback, pero una terminación abrupta requiere `--recover` y puede dejar archivos en estados intermedios hasta ejecutarlo.

El journal utiliza escrituras y `fsync` para reducir pérdidas en disco, pero no constituye una garantía absoluta ante apagón ni frente a cambios concurrentes maliciosos; Windows y algunos filesystems no permiten sincronizar metadatos de directorio con las mismas garantías que ciertos POSIX. Una interrupción entre la creación del workspace y su primer journal puede exigir una limpieza manual, ya que no hubo escrituras de destino en esa etapa.

Los snapshots individuales se limitan a 16 MiB y cada lote a 300 archivos. La publicación de respaldos utiliza hardlinks exclusivos entre el workspace y el destino, por lo que si el filesystem o los volúmenes no los permiten, la operación falla de forma cerrada y revierte lo que pueda verificar. No se ejecuta código de terceros.

Si un operador modifica un destino durante el rollback, el proceso se detiene sin borrar ese contenido desconocido. La recuperación puede ser **parcialmente completada** en ese punto, pero mantiene el journal y es reintentable después de una revisión. Ningún software de archivos puede garantizar atomicidad transaccional global en sistemas arbitrarios sin soporte específico del filesystem.

Pruebas automatizadas offline (fallos de escritura, backups, recuperación posinterrupción, conflictos, manipulación de snapshots y symlinks):

```powershell
python -m unittest discover -s tests -v
```
