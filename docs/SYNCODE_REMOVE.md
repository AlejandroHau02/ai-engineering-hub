# SYNCODE — desinstalación controlada de componentes gestionados

Permite **desactivar una skill propia, una skill externa aprobada o un especialista nativo** sin borrar datos desconocidos. Este flujo es distinto de `sync.py --recover`, que solamente revierte operaciones de instalación interrumpidas.

## Uso y límites

Ejecuta siempre desde la raíz del repositorio actualizado. Se requiere seleccionar **exactamente un cliente** (`claude` o `codex`) por operación para evitar una desinstalación parcial entre proveedores. La simulación predeterminada no escribe.

```powershell
python scripts/remove.py --target claude --skill application-security-audit
# Tras revisar el destino:
python scripts/remove.py --target claude --skill application-security-audit --apply
```

Para un especialista:

```powershell
python scripts/remove.py --target codex --specialist hub-ux-auditor
python scripts/remove.py --target codex --specialist hub-ux-auditor --apply
```

Para una skill externa **aún registrada y validada** en el manifiesto de aprobaciones:

```powershell
python scripts/remove.py --target claude --approved-skill ID --apply
```

En este último caso solo se usa su SHA-256 aprobado: **no se descarga ni ejecuta** contenido remoto. Un identificador no registrado, archivo modificado o fuente no reconocida requiere revisión manual.

## Respaldo y restauración

La aplicación compara el contenido exacto con la definición de primera parte o el hash fijado de la skill externa. Rechaza carpetas con otros archivos, symlinks/junctions, modificaciones locales, rutas desconocidas, locks activos y transacciones de instalación pendientes.

Una vez verificado, se ejecuta **una única operación de renombrado local** para desplazar el componente fuera de la ruta donde el cliente lo descubre:

- Skill: `~/.claude/skills/<id>` pasa a un directorio hermano `<id>.removed.<fecha>-<token>` con su archivo intacto.
- Especialista: `~/.codex/agents/hub-foo.toml` pasa a `hub-foo.toml.removed.<fecha>-<token>`, que ya no coincide con la extensión nativa de descubrimiento.

No se borra el contenido original ni se mueven archivos personales. El comando muestra la ubicación exacta del respaldo. Para restaurar manualmente, verifica que la ruta original sigue libre y renombra el respaldo a su **nombre original**; conserva una copia si existe alguna duda.

La instalación con `sync.py --apply` puede volver a crear un componente desactivado, porque el backup queda fuera de la ruta original. Por eso la desinstalación es **reversible, pero no persistente frente a sincronizaciones futuras**: el Hub sigue considerando el componente como parte de su catálogo.

## JSON para la futura interfaz

```powershell
python scripts/remove.py --target claude --skill code-review --format json
```

Contrato v1: `schemaVersion`, `product`, `status` (`planned`, `removed`, `unchanged`, `error`), `target`, `component` relativo a home, `backup` relativo a home o null y `error` opcional. El formato de texto sigue siendo predeterminado.

## Riesgo residual

El renombrado de **un** archivo o directorio dentro del mismo volumen es una operación atómica respecto al nombre de ese componente; esto no constituye una transacción global sobre múltiples archivos. Si el proceso muere después del renombrado, el respaldo permanece íntegro y la restauración es manual. Los controles no eliminan todas las carreras TOCTOU frente a procesos locales maliciosos.

No reemplazar el respaldo ni eliminarlo sin inspeccionarlo. No usar este comando para limpiar carpetas personalizadas ni para restaurar journals de `sync.py`; para eso continúa existiendo `sync.py --recover`.
