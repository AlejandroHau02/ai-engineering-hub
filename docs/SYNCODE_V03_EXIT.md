# SYNCODE v0.3 — criterios de cierre del núcleo (antes de Desktop)

**Estado: pendiente de pruebas reales locales.** La suite de CI valida código e instalador; no demuestra que las skills se invoquen siempre ni que mejoren el rendimiento del modelo.

## Puertas de cierre

1. **Instalación local:** actualizar `main`, ejecutar CI local, sincronizar cinco skills propias y cuatro especialistas por cliente; repetir dry-run y observar `NO CHANGES`.
2. **Estado y seguridad:** comprobar Doctor, JSON, detección de conflictos, modificación local y recuperación. Documentar claramente qué se probó en Windows y qué quedó simulado.
3. **Skill externa E2E:** usar Scout para encontrar e inspeccionar un candidato pequeño con `SKILL.md` autónomo; revisar licencia, riesgos y compatibilidad en ambos clientes; registrar aprobación humana verificable antes de cualquier instalación; instalar solamente sobre `--home` aislado y comprobar rollback/conflictos. No inventar aprobaciones ni instalar en el home personal por defecto.
4. **Evaluaciones de calidad:** ejecutar escenarios E05 y E08 en condiciones comparables y registrar evidencia; repetir algunos casos sin Hub para estimar valor añadido. Verificar manualmente las invocaciones de especialistas (issue #6), sin asumir selección automática.
5. **Reconciliación documental:** cerrar issues implementados con referencias al PR/CI correspondiente, actualizar las guías que aún describen el flujo externo como futuro y etiquetar una versión estable solo después de la evidencia de arriba.

## Qué no bloquea Desktop

- Skills externas con bundles/scripts.
- Marketplace, autoactualización, agentes que se entrenan solos o monitorización de CVE en segundo plano.
- Interfaz remota, servicio HTTP, orquestación multiagente automática.
- Certificación formal de seguridad, que este Hub no ofrece.

## Desarrollo de SYNCODE Desktop (dogfooding)

El escritorio será un **cliente local** de estos contratos. Las tareas sustanciales deberán registrar: agente y versión, skill invocada *si es observable*, asesor especialista solicitado, evidencia de implementación, pruebas realizadas, hallazgos de revisión cruzada y riesgos residuales. Preferir ejecuciones de Claude Code y Codex en ramas o worktrees separados; ninguna delegación autoriza un merge o despliegue sin consentimiento humano.

Medir tres criterios: **reutilización** (menos configuración repetida), **calidad** (errores reales evitados) y **eficiencia** (tiempo/coste total, incluida la revisión). No atribuir causalidad a una sola ejecución.
