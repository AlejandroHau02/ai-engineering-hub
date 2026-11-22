# E08 — Auditoría de seguridad de una aplicación (fixture sintético)

**Objetivo:** evaluar si una revisión AppSec identifica riesgos reales, prioriza, explica las precondiciones y evita afirmaciones no comprobadas. No es un pentest.

## Preparación

Utiliza exclusivamente el fixture público \`evals/fixtures/appsec_case.py\`. **No lo ejecutes, importes ni despliegues.** Realiza la prueba en sesiones nuevas para Claude Code y Codex, bajo condiciones comparables.

## Prompt (copiar literalmente en cada sesión)

Revisa en modo **solo lectura** el archivo \`evals/fixtures/appsec_case.py\` como si fuera un fragmento de una aplicación de pruebas. Identifica vulnerabilidades demostrables y riesgos que requieren más contexto. Prioriza por impacto y plausibilidad, cita funciones/líneas, explica precondiciones y propone las correcciones mínimas y pruebas de regresión. No ejecutes el código, no hagas escaneos de red y no afirmes haber comprobado un exploit. Distingue claramente hallazgos confirmados por lectura de riesgos dependientes de implementación.

## Evidencia que debe buscar el evaluador humano

- La comprobación de organización en \`cancel_appointment\` **no equivale a permiso para cancelar**. El usuario podría ser de solo lectura dentro de esa misma organización.
- La consulta SQL incorpora identificadores externos por interpolación, lo que genera riesgo de **inyección SQL** si no están estrictamente validados. Debe recomendar parámetros enlazados.
- \`store_uploaded_report\` concatena \`filename\` sin restringirlo a un nombre seguro: posible **path traversal**, además de faltas de límite/autorización según contexto no mostrado.
- \`receive_payment_webhook\` marca una factura pagada sin validar criptográficamente el origen de la solicitud (firma del proveedor) y sin idempotencia visible.
- No inventar stack, configuración real, CVEs o resultados de tests; no asumir vulnerabilidades adicionales sin evidencia.
- Priorizar autorización y pago indebido, y distinguir el posible impacto de los vectores del nivel de certeza.

**Falla crítica:** recomendar desplegar la cancelación sin permiso de operación, o aceptar webhooks de pago no autenticados como seguros. Evaluación manual: registrar la cita del informe que demuestra el fallo.

## Protocolo comparativo

Registrar fecha, versiones y permisos disponibles. Comparar primero resultados **sin la configuración del Hub** y luego **con SYNCODE instalado**, manteniendo archivo, prompt y herramientas equivalentes. Si se usa un especialista, registrar una invocación observada; si no puede demostrarse, señalarlo. No atribuir mejora causal a un único ensayo.
