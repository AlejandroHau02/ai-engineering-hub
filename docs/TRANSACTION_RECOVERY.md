# Recuperación de instalaciones por lote — PR #10

Las aplicaciones de `scripts/sync.py --apply` utilizan `scripts/transaction.py`, sin dependencias de terceros. Aplica para instrucciones, skills propias, especialistas y, si se habilita explícitamente, skills externas aprobadas.

## Qué garantiza

1. **Simulación predeterminada:** sin `--apply` no crea directorios ni modifica los archivos instalados.
2. **Preparación completa:** valida todos los destinos y crea el journal y las copias exactas de los originales antes de la primera sustitución.
3. **Escritura por archivo:** utiliza escritura temporal y sustitución atómica por archivo; vuelve a comprobar rutas y hashes y verifica el contenido final de todos los archivos.
4. **Error ordinario:** si una operación falla, revierte el lote (archivos existentes y archivos/directorios recién creados) siempre que el estado permanezca reconocible.
5. **Respaldos convencionales:** después de completar las escrituras, publica respaldos `*.bak.<timestamp>-<token>` de los archivos reemplazados; la publicación del backup es exclusiva y se evita escribir respaldos finales a medias.
6. **Interrupción abrupta:** el journal persistente permite reanudar la recuperación en una nueva ejecución, a petición explícita.

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

El journal y las copias de preimagen se conservan en `~/.ai-engineering-hub-transaction/` mientras el lote está activo o pendiente de recuperación. El programa se niega a iniciar otro plan si detecta ese workspace.

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
