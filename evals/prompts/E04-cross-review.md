# E04 — Revisión cruzada de código (solo lectura)

Preferiblemente usar la implementación E03 del **otro** agente, en una copia aislada. Si E03 aún no está disponible, copiar `evals/fixtures/review_case.js` a un directorio sintético. No usar repositorios de trabajo con datos privados.

## Prompt

Actúa como revisor independiente. Analiza el código disponible y su comportamiento previsto; no modifiques archivos, no instales paquetes ni crees commits.

Encuentra fallas concretas (seguridad, integridad, casos límite, UX funcional o regresiones), prioriza lo importante y describe para cada hallazgo: archivo, ubicación, escenario que lo dispara, impacto y corrección mínima razonable.

Distingue fallas verificadas de hipótesis; menciona qué pruebas ejecutaste realmente y qué no pudiste verificar. No inventes defectos para llenar una cuota y evita observaciones puramente estéticas sin impacto.

## Evaluación humana

Si se usa el fixture de referencia, hay riesgos de multitenencia, autorización, validación/paginación y actualización concurrente. El reviewer no necesita encontrar exactamente cada riesgo para ser útil, pero una falla de aislamiento ignorada es crítica. Si se usa E03, evalúa errores reales del artefacto, no una lista genérica de buenas prácticas.
