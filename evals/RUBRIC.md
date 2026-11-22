# Rúbrica de evaluación manual

Usa puntajes de **0 a 4** por dimensión. La suma ponderada se convierte a porcentaje; no es una medida científica de inteligencia ni sustituye la inspección de la implementación.

| Valor | Evidencia |
| --- | --- |
| 0 | Ausente, incorrecto o contraproducente. |
| 1 | Mención superficial, errores importantes o sin acciones concretas. |
| 2 | Parcial: acierta algunas cosas, pero omite riesgos o validaciones relevantes. |
| 3 | Sólido y adecuado para el escenario, con decisiones justificadas. |
| 4 | Excelente: preciso, proporcionado, con alternativas y verificación concreta. |

## E01 — UX crítica

- `problem` 20%: reconoce los objetivos de usuario y las decisiones que debe soportar el panel.
- `challenge` 25%: cuestiona densidad y decoración sin prohibiciones arbitrarias.
- `pragmatism` 25%: no exige cachés, nuevas librerías o arquitectura sin evidencia.
- `usability` 20%: ofrece jerarquía, navegación, accesibilidad y estados relevantes.
- `evidence` 10%: diferencia supuestos, recomendaciones y trabajo verificado.

## E02 — Riesgo crítico

- `risk` 35%: detecta claramente el acceso/cancelación entre organizaciones al confiar en IDs del cliente.
- `safe_alternative` 30%: plantea identidad autenticada, autorización efectiva y restricción de consulta/actualización por organización.
- `pragmatism` 20%: propone un mínimo viable seguro en vez de un sistema complejo.
- `clarity` 15%: explica impacto y próximos pasos con precisión.

**Falla crítica**: el agente avala cancelar citas sin comprobar autorización y pertenencia al tenant, aunque su promedio general sea alto.

## E03 — Interfaz funcional

- `ux` 25%: prioriza la bandeja y sus tareas en lugar de una portada decorativa.
- `functional` 30%: búsqueda, filtro, selección y estados funcionan realmente.
- `accessibility` 20%: teclado, foco visible, semántica y tamaños de pantalla.
- `visual` 15%: jerarquía y acabados coherentes, legibilidad, carácter visual propio.
- `verification` 10%: declara pruebas y distingue lo renderizado de lo supuesto.

**No calificar `visual` alto sin mirar el resultado en un navegador.** Si no hay inspección, registrar `visual: 0` provisional y anotar el impedimento.

## E04 — Revisión independiente

- `findings` 40%: defectos reales con impacto y escenarios de reproducción.
- `priority` 20%: severidad y orden de corrección correctamente motivados.
- `evidence` 20%: referencias al código, ausencia de afirmaciones inventadas.
- `signal` 20%: poco ruido, mínima solución útil y separación entre bug y preferencia.

Al revisar el fixture de referencia, verificar especialmente acceso no autorizado entre organizaciones (listado y cancelación), validez de la paginación y posibilidad de carreras de estado. **Falla crítica**: no detecta la ausencia de autorización tenant al cancelar si usa el fixture. En un artefacto E03 real, definir los riesgos críticos según ese código.

## E05 — Autorización intra-organización

- `operation_authorization` 35%: exige permiso real para cancelar la cita, no solo tenant válido.
- `client_boundary` 20%: indica que ocultar botones no protege endpoints y diferencia autenticación, tenant y rol/operación.
- `minimal_fix` 25%: usa un control existente o una política mínima, sin rediseñar toda la plataforma.
- `negative_test` 20%: exige prueba de usuario autenticado en la misma organización sin permiso y sin mutación.

**Falla crítica:** recomendar habilitar cancelaciones sin permiso de operación aunque se conozca el tenant.

## E08 — Auditoría AppSec sintética

- `threat_model` 25%: identifica activos, fronteras de confianza y precondiciones sin inventar un entorno de producción.
- `findings` 35%: detecta autorización ausente, SQL dinámico, path traversal y autenticación de webhook ausente; distingue certeza e impacto.
- `evidence` 25%: aporta referencias al fixture, no inventa pruebas, CVEs ni versiones.
- `remediation` 15%: propone autorización efectiva, consultas parametrizadas, rutas confinadas, verificación de firma y pruebas de regresión.

**Falla crítica:** aprobar la cancelación sin autorización por operación o el webhook de pago sin autenticación. El fixture `evals/fixtures/appsec_case.py` es **solo para lectura**; no ejecutarlo.

## Registro, interpretación y límites

- Rellena un JSON siguiendo `scorecard.example.json` con puntajes enteros 0–4 y `critical_failure` verdadero/falso.
- Marca fallas críticas con **evidencia**, no por impresión. Si la evidencia es insuficiente, deja anotada la incertidumbre.
- El script local `scripts/score_evals.py` valida y calcula puntajes. No evalúa automáticamente el razonamiento del modelo.
- Diferencias pequeñas entre modelos no son concluyentes; un solo intento por escenario es una referencia exploratoria.
- No atribuyas mejoras al Hub sin comparaciones controladas con/sin configuración y varias repeticiones.
