# E06 — Invocación del especialista global `hub-ux-auditor`

**Fecha:** 21-nov-2026.

**Estado:** smoke test manual **reportado** como satisfactorio en Claude Code y Codex. No se recibieron trazas de ejecución independientes ni captura del panel de subagentes, por lo que no se afirma verificación forense de la invocación.

## Instalación previa en Windows

El propietario ejecutó:

```powershell
python scripts/sync.py --target both --specialists --apply
python scripts/sync.py --target both --specialists
```

Salida declarada: 10 cambios (dos archivos globales de instrucciones actualizados y ocho agentes creados), respaldos de configuraciones y segundo diagnóstico `NO CHANGES`.

## Escenario

Se pidió al agente principal de **ambos** clientes que delegara explícitamente en `hub-ux-auditor` la evaluación **solo lectura** del prototipo estático `hub-eval-codex/e03-bandeja`, formado por `index.html`, `styles.css` y `app.js`. La tarea requería priorizar tres problemas UX/accesibilidad con evidencia y aclarar si el especialista se ejecutó.

## Resultado de Codex

El agente principal **afirmó** que `hub-ux-auditor` se ejecutó y revisó los tres archivos. Priorizó:

1. Posible colapso de lista por diseño de altura fija y contenedor flex; **no reproducido en navegador**.
2. Historial de navegación móvil inexistente al pasar de lista a detalle.
3. Selección no perceptible en modo de alto contraste debido a uso exclusivo de color/sombra.

Declara que no hubo modificaciones. La revisión fue estática y la herramienta de navegador no estaba disponible.

## Resultado de Claude Code

El agente principal **afirmó** que `hub-ux-auditor` se ejecutó en modo lectura; dijo comprobar por su cuenta algunas referencias en los tres archivos. Priorizó:

1. Posible colapso de lista con viewport bajo / zoom, sin reproducción visual en esa invocación.
2. Uso de `innerHTML` con interpolaciones de texto sin escape: riesgo de XSS **condicional** a datos externos; los datos actuales eran ficticios.
3. Falta de integración del historial móvil (`pushState`/`popstate`).

Mencionó observaciones secundarias sobre alto contraste, `Escape`, texto pequeño, ordenación y estados. Declara que no modificó archivos ni ejecutó comandos; sin validación en navegador o lector de pantalla.

## Interpretación

- Ambos reportes son consistentes con **descubrimiento e invocación intencional** del perfil nativo y muestran asesoría temática pertinente.
- La instrucción del especialista de distinguir riesgos de hechos visualmente reproducidos parece reflejada en ambas respuestas.
- **No equivale a prueba técnica independiente de ejecución.** Para cerrar esa parte, revisar el panel o registro nativo de subagentes de cada cliente y confirmar el nombre `hub-ux-auditor`.
- No demuestra selección *automática* del especialista: el prompt lo solicitó explícitamente.
- No hay evidencia de que un agente Codex tenga un sandbox de solo lectura independiente: las restricciones del perfil son instrucciones, mientras el sandbox/approval lo determina la sesión principal.
- Ambas revisiones usaron el mismo prototipo, pero tuvieron capacidades de herramientas distintas. No es un benchmark comparativo de modelos.
- No se atribuyen resultados a un cambio causal del Hub sin una comparación controlada.

## Resultado del piloto

**PASS preliminar** de instalación, detección aparente, delegación dirigida y retorno de hallazgos. Pendiente: verificar trazas nativas e inducir una delegación **sin nombrar un especialista**, con posibilidad explícita de no delegar para medir criterio y coste.
