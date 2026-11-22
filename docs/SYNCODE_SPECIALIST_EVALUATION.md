# Evaluación de los especialistas de SYNCODE usando SYNCODE (Fases 1–3 de Desktop)

**Qué se evalúa:** la aportación técnica concreta de los especialistas de SYNCODE durante la construcción de SYNCODE Desktop. No se mide cuántas consultas hubo, sino qué cambiaron: defectos evitados o encontrados, recomendaciones adoptadas o descartadas, y lo que se les escapó.

**Cómo se usaron:**
- Cada fase empezó con consultas acotadas y de solo lectura, con un borrador concreto para criticar: arquitectura (`hub-architecture-advisor`), UX (`hub-ux-auditor`) y seguridad (`hub-backend-auditor`, aplicando la skill `application-security-audit`, porque `hub-application-security-auditor` no existía; ver Fase 3).
- Cada fase cerró con una revisión independiente (`hub-code-reviewer`) a la que se dieron los requisitos y el alcance, pero no las conclusiones del agente principal.
- Todos son de solo lectura: no ejecutan código, ni tests, ni ven la app renderizada. Lo declararon en cada informe.

## Resultados por fase

| Fase | Consulta previa: aportación decisiva | Revisión final: hallazgos | Defectos que se escaparon a los especialistas |
| --- | --- | --- | --- |
| 1 (Doctor) | **Arquitectura:** separar «bundle incompleto», «sin salida» y «proceso fallido»; drenar stderr (posible bloqueo de la tubería); tolerar la evolución aditiva del contrato; lista explícita y rechazo de árbol sucio. **UX:** separar visualmente «no se pudo ejecutar» de «encontró problemas»; terminología «requieren revisión» frente a «archivo lock». | 0 críticos/altos, 2 medios (el smoke de CI no comprobaba el resultado; el interruptor de rutas repintaba durante una ejecución), 5 bajos. Todos corregidos. | El arnés de capturas cogía una ventana auxiliar de 22×22 px en el primer arranque (lo detectó la prueba local, no un especialista). |
| 2A (Vista previa + ZIP) | **Seguridad:** argparse acepta abreviaturas (`--app` = `--apply`) → flag `--preview` excluyente; el `sha256` de un `UPDATE` permite confirmar por fuerza bruta el archivo fusionado → no se muestra; `/DEPENDENTLOADFLAG:0x800`; prueba de no escritura con audit hooks y control negativo. **Arquitectura:** campos aditivos `target`/`component` en el núcleo en lugar de reinterpretar rutas en Rust; DTO en vez del JSON bruto. **UX:** contradicción real: Diagnóstico decía «SYNCODE no lo sobrescribirá» para unas instrucciones que la sincronización sí reemplaza. | 1 **alto** (una regla CSS anulaba `hidden`: el aviso de «vista previa obsoleta» se veía siempre), 2 medios, 5 bajos. Todos corregidos. | Falso positivo del test de instantánea en Rust por metadatos de NTFS actualizados de forma diferida (lo detectó CI). |
| 2B (Aplicar) | **Arquitectura:** clasificar primero por `transaction.outcome`. Evitó dos defectos graves del borrador: presentar como «rechazo» un `committed` + error (cambios ya aplicados) y sugerir `--recover` ante un `lock-busy` (transacción ajena activa). **Seguridad:** CPython lee `pyvenv.cfg` en el directorio padre del intérprete (fuera de la tabla de integridad); `cwd = python/`; sin `PATH` para sync; carrera vista previa/descarte. **UX:** F5 relanzaba la vista previa con el modal abierto; los textos de transporte decían «no se modificó nada» en un contexto de aplicación. | **1 bloqueante:** tras una llamada nativa posterior, CI ya no detectaba fallos de los smoke de UI (`exit 1` solo cambia `$LASTEXITCODE`). 1 medio, 5 bajos. Todos corregidos. | **Token predecible** (`plan-<n>`): seguridad había pedido un CSPRNG de 128 bits; arquitectura recomendó un contador «sin RNG» y el agente principal eligió el contador. La revisión final no lo marcó. Lo detectó el usuario antes del merge; se corrigió en el mismo PR. |
| 3 (Cierre) | **Seguridad:** `lock-busy` llega con `outcome: recovery-pending` (copiar la tabla de apply habría dado «desconocido»); flag de cierre RAII; presupuesto de salida del catálogo frente al límite de stdout; surrogates sueltos en nombres de archivo. **Arquitectura:** diagnóstico exacto de la inconsistencia AppSec (`KNOWN_SKILLS` sin la quinta skill) y versión 0.4.0. **UX:** Diagnóstico prometía «solo lectura» y ahora puede recuperar; el resultado de Recuperar se borraba con el nuevo diagnóstico automático; descripciones en inglés. | Ver ADR-0010 («Revisión final»). | — |

## Recomendaciones descartadas (con motivo)

| Recomendación | Origen | Por qué se descartó |
| --- | --- | --- |
| Firmar las confirmaciones con un diálogo nativo de Rust (MessageBoxW) | Seguridad, 2B | Doble confirmación para el usuario. Riesgo aceptado y documentado (CSP estricta, sin contenido remoto ni `innerHTML`). |
| Incrustar el manifiesto en vez de hashear todos los archivos en `build.rs` | Arquitectura, 2B | La tabla derivada de los bytes reales es igual de barata y también cubre el manifiesto. |
| Contador de generación como token, sin RNG | Arquitectura, 2B | **Se adoptó y fue un error.** Se revirtió a CSPRNG de 128 bits a petición del usuario. |
| Recuperar sin token | Arquitectura, 3 | El token es lo que hace cumplir en Rust (y no solo en la UI) «solo cuando hay una recuperación pendiente verificada». |
| No listar elementos ajenos ni crear `catalog.py`; extender Doctor | Arquitectura, 3 | El usuario pidió ver lo instalado en cada cliente. Se aplicaron los límites de seguridad (lectura con tope, sin seguir enlaces, saneado, presupuesto). Doctor conserva su contrato. |
| Lista de copias `.bak` a partir de `events[]` | UX, 2B | `events[]` no es contrato. Se informa de que existen copias, sin listarlas. |

## Evaluación

**Dónde aportaron más:**
1. **Tablas de estados del contrato (arquitectura y seguridad).** Las dos correcciones de mayor impacto de Desktop salieron de leer el contrato con más cuidado que el borrador: la clasificación por `outcome` en Aplicar y `lock-busy` + `recovery-pending` en Recuperar. En ambos casos, el borrador habría dado mensajes falsos en situaciones de pérdida de datos potencial.
2. **Seguridad de bajo nivel en Windows y Python.** `pyvenv.cfg` en el directorio padre, abreviaturas de argparse, filtración por hash de un archivo fusionado, `DEPENDENTLOADFLAG`. Son cosas que no salen sin conocimiento específico y que ningún test previo habría pedido.
3. **Coherencia de la UI con la realidad.** UX detectó en tres fases textos que mentían: «no se sobrescribirá», «solo lectura», «no se modificó nada». El código funcionaba, pero el producto engañaba.
4. **Revisión final independiente.** Encontró defectos reales tras implementar en todas las fases: el CSS que anulaba `hidden` y el enmascaramiento de fallos en CI, que habría dejado pasar en verde una aplicación «sin verificar».

**Límites observados:**
- **No ejecutan nada:** no vieron la UI ni corrieron tests. Los defectos dependientes del entorno (metadatos de NTFS, la ventana de 22 px, Smart App Control) los encontró la ejecución real en CI o en local.
- **Consejo contradictorio sin árbitro:** en el token de 2B, arquitectura (simplicidad) y seguridad (entropía) discreparon; el agente principal eligió mal y la revisión final no lo reabrió. Lección: cuando un especialista de seguridad pide entropía para un identificador de autorización, no se sustituye por simplicidad sin una justificación explícita.
- **La revisión hereda el encuadre del requisito:** el revisor de 2B comprobó «uso único, TTL, invalidación», pero no la imprevisibilidad, que no figuraba como requisito.
- **Falta de especialista AppSec dedicado** hasta la Fase 3: `hub-backend-auditor` lo suplió bien con la skill, pero la ausencia era una inconsistencia del propio catálogo (ver ADR-0010).

**Conclusión:** los especialistas cambiaron decisiones de forma medible en cada fase, sobre todo en estados de error, seguridad de plataforma y honestidad de la UI. No sustituyen la ejecución real ni el criterio final del agente principal, que es responsable de arbitrar consejos contradictorios.
