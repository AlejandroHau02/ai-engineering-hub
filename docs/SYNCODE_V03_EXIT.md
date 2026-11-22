# SYNCODE v0.3 — criterios de cierre del núcleo (antes de Desktop)

**Estado (21–22 nov 2026): pruebas locales principales y comparación inicial sin Syncode completadas en Windows; cierre formal sujeto a revisión de PR/CI y decisión de release.** La suite de CI valida código e instalador; las pruebas reales no demuestran que las skills se invoquen siempre ni que mejoren causalmente el rendimiento del modelo.

## Puertas de cierre

1. **Instalación local:** actualizar `main`, ejecutar CI local, sincronizar cinco skills propias y cuatro especialistas por cliente; repetir dry-run y observar `NO CHANGES`.
2. **Estado y seguridad:** comprobar Doctor, JSON, detección de conflictos, modificación local y recuperación. Documentar claramente qué se probó en Windows y qué quedó simulado.
3. **Skill externa E2E:** usar Scout para encontrar e inspeccionar un candidato pequeño con `SKILL.md` autónomo; revisar licencia, riesgos y compatibilidad en ambos clientes; registrar aprobación humana verificable antes de cualquier instalación; instalar solamente sobre `--home` aislado y comprobar rollback/conflictos. No inventar aprobaciones ni instalar en el home personal por defecto.
4. **Evaluaciones de calidad:** ejecutar escenarios E05 y E08 y registrar evidencia; repetir algunos casos sin Hub para estimar valor añadido. Verificar manualmente las invocaciones de especialistas (issue #6), sin asumir selección automática.
5. **Reconciliación documental:** cerrar issues implementados con referencias al PR/CI correspondiente, actualizar las guías que aún describen el flujo externo como futuro y etiquetar una versión estable solo después de la evidencia de arriba.


## Evidencia local reunida (noviembre de 2026)

| Puerta | Evidencia | Estado / límite |
| --- | --- | --- |
| 1. Instalación | `main` actualizado, Python 3.12.10, `unittest`: 156 pruebas, 0 fallidas, 7 omitidas; 20/20 componentes en Doctor global y home QA; `NO CHANGES`. | **PASS** en Windows. |
| 2. Seguridad y estado | Doctor y contratos JSON revisados; aislamiento de `--home`, retirada/reinstalación de especialista Codex con respaldo SHA-256 idéntico; sin conflictos. | **PASS parcial**: recuperación tras interrupción y conflictos adversariales cubiertos por tests, pero no todos ejercitados manualmente. |
| 3. Skill externa E2E | Scout `discover`, `locate` e `inspect --related` sobre `obra/superpowers` a commit `22f0b463e7a5e579904dd6211a520321035526f7`; SHA-256 verificado; examen MIT; prueba funcional y descubrimiento de `verification-before-completion` en Claude/Codex; rechazo de manifiesto pendiente; aprobación **solo para QA** en clon temporal; `sync.py --approved-skills --apply` creó 2 archivos; SHA-256 2/2, idempotencia `NO CHANGES`, `remove.py` retiró 2 y conservó respaldos. | **PASS** aislado. El manifiesto de `main` conserva 0 aprobaciones. [Expediente #14](https://github.com/AlejandroHau02/ai-engineering-hub/issues/14). |
| 4. Calidad | E05: Claude 95/100, Codex 100/100; E08: Claude 100/100, Codex 91/100, según evaluación manual de una ejecución por caso. Delegación nativa reportada de `hub-backend-auditor` en ambos. | **PASS de funcionamiento inicial y baseline E05**; ninguna mejora causal demostrada. Sin evidencia de selección automática fiable. Las calificaciones no son métricas estadísticas. |
| 5. Documentos y release | Reconcilia README y guía de especialistas; elimina recomendación de `/agents` retirado en Claude. | **En revisión** de este PR. No etiquetar versión estable hasta definir alcance de la comparación y aprobación del cierre. |

### Baseline E05 sin Syncode (ejecución individual por condición)

| Modelo | E05 con Syncode | E05 sin Syncode | Alcance |
| --- | ---: | ---: | --- |
| Claude Code | 95/100 | 100/100 | `--safe-mode` desactivó las personalizaciones habituales; una ejecución por condición. |
| Codex | 100/100 | 100/100 | Perfil temporal con `USERPROFILE`, `HOME` y `CODEX_HOME` independientes; `--no-daemon --sandbox read-only` para evitar límite `SUN_LEN` en Windows; una ejecución por condición. |

Ambos baselines rechazaron el acceso de solo lectura a la cancelación, exigieron autorización backend, descartaron la ocultación del botón como seguridad y propusieron prueba negativa `403` que verifique ausencia de modificación. **No hay evidencia de mayor calidad de respuesta causada por Syncode en E05.** El diseño de una sola ejecución, las variaciones de configuración (`--safe-mode` desactiva más que Syncode) y la falta de una auditoría independiente de trazas impiden atribuir diferencias al Hub. Tampoco se realizó E08 sin Syncode. El valor observado del núcleo está en capacidad de configuración y gestión común, delegación nativa explícita y reversibilidad; medir calidad/tiempo/coste permanece como trabajo posterior, no condición para iniciar el diseño del Desktop.

### Alcance exacto de las conclusiones

- Las salidas locales y respuestas de agentes provienen de ejecuciones compartidas por el usuario; no son auditoría independiente de logs internos.
- `Skill` fue invocada explícitamente según el informe de Claude. Codex reportó descubrimiento y lectura local de la skill sin herramienta de invocación dedicada: no equiparar ambas mecánicas.
- La aprobación de la skill externa se limitó a un **checkout y home temporal de QA**. No implica autorización para instalaciones globales ni incorporación al catálogo oficial.
- La reversión comprobada fue desinstalación recuperable por renombrado y reinstalación de componentes; no se recreó manualmente una interrupción abrupta del journal.
- El sistema está suficientemente ejercitado para **planificar** Desktop, y el contraste inicial E05 ya se realizó; para cualquier afirmación de valor añadido **medido causalmente** se requieren repeticiones y controles adicionales.

## Qué no bloquea Desktop

- Skills externas con bundles/scripts.
- Marketplace, autoactualización, agentes que se entrenan solos o monitorización de CVE en segundo plano.
- Interfaz remota, servicio HTTP, orquestación multiagente automática.
- Certificación formal de seguridad, que este Hub no ofrece.

## Desarrollo de SYNCODE Desktop (dogfooding)

El escritorio será un **cliente local** de estos contratos. Las tareas sustanciales deberán registrar: agente y versión, skill invocada *si es observable*, asesor especialista solicitado, evidencia de implementación, pruebas realizadas, hallazgos de revisión cruzada y riesgos residuales. Preferir ejecuciones de Claude Code y Codex en ramas o worktrees separados; ninguna delegación autoriza un merge o despliegue sin consentimiento humano.

Medir tres criterios: **reutilización** (menos configuración repetida), **calidad** (errores reales evitados) y **eficiencia** (tiempo/coste total, incluida la revisión). No atribuir causalidad a una sola ejecución.
