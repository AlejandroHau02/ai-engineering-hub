# Engineering Hub — evaluaciones exploratorias

Esta suite es una **evaluación manual, reproducible y sin datos privados** de criterio técnico, calidad de implementación y revisión de código. No es un test de inteligencia general ni una prueba causal de que el Hub mejora el modelo.

## Escenarios

| ID | Tipo | Qué observar | Resultado |
| --- | --- | --- | --- |
| E01 | UX / desacuerdo suave | Cuestiona un dashboard saturado sin reglas rígidas | Respuesta argumentada |
| E02 | Riesgo / desacuerdo firme | No acepta omitir aislamiento o autorización multitenant | Alternativa segura y pragmática |
| E03 | Implementación UX/UI | Diseña una bandeja operativa real, no un dashboard genérico | Archivos y evidencia visual |
| E04 | Revisión cruzada | Encuentra errores reales en código de otro agente | Hallazgos priorizados |
| E05 | Permisos dentro de tenant | Rechaza diferir autorización por rol aunque haya aislamiento de organización | Alternativa mínima segura |

Prompts disponibles en `evals/prompts/`. No incluyas nombres de clientes, repositorios laborales, credenciales ni datos reales.

## Protocolo de comparación

1. Usa una **sesión nueva por agente y escenario**. Mismo prompt, mismo proyecto sintético y mismas herramientas disponibles.
2. Registra fecha, versión/modelo, configuración, skills invocadas *si son observables*, y cambios realizados. Nunca infieras que se invocó una skill solo porque la respuesta fue buena.
3. Para E01/E02/E05 pega idéntico texto en Claude Code y Codex. Para E03 realiza dos implementaciones **en carpetas separadas** (sin enseñar el resultado de uno al otro).
4. Para E03 inspecciona el resultado renderizado en escritorio y móvil; comprueba filtros, estados y teclado. No des por verificada la interfaz solo porque compila.
5. Para E04 entrega el trabajo del otro agente en una carpeta de revisión aislada. Pide revisión de solo lectura; intercambia roles. Si no existen archivos de E03, utiliza el caso sintético `evals/fixtures/review_case.js`.
6. Registra observaciones y puntajes con `evals/scorecard.example.json`; consulta `evals/RUBRIC.md`. No incluyas material confidencial en el repositorio público.
7. Calcula puntajes de forma local con `python scripts/score_evals.py ruta/al/resultado.json`. El script no llama servicios ni almacena resultados.

## Controles de calidad

- Un puntaje aislado no demuestra una mejora causada por el Hub. Para evaluarlo causalmente harán falta comparaciones controladas con/sin configuración global, condiciones equilibradas y varias repeticiones.
- Los puntajes son **humanos** y tienen subjetividad; evita conclusiones por diferencias pequeñas.
- Las fallas críticas de seguridad se registran aparte; un promedio alto no las compensa.
- No ejecutar ni desplegar los artefactos que contienen errores intencionales de la carpeta `fixtures/`.

## Registro histórico

E01 (21-nov-2026) se probó informalmente en Claude Code y Codex, con resultados cualitativos favorables en ambos; sin registro verificado de invocación automática de skills. No se asigna un benchmark cuantitativo validado.

E05 es un escenario de regresión manual añadido para seguir el hallazgo de E02 sobre permisos entre roles de una misma organización. Aún no tiene un scorecard automatizado.
