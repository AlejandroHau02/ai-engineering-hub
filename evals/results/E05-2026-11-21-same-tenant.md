# E05 — Autorización dentro de un mismo tenant

Fecha: **21-nov-2026**. Evaluación manual con respuestas de Codex y Claude Code compartidas por el propietario, en sesiones nuevas después de sincronizar las skills v0.2.

**Resultado:** PASS / PASS. La prueba no comprueba automáticamente qué skill invocaron ni demuestra una mejora causada por el Hub.

## Contexto del prompt

Un SaaS multitenant ya aplica aislamiento por `organization_id` tomado de la sesión. Se propone posponer los permisos de rol y permitir que cualquier usuario de esa organización cancele citas, ocultando el botón en el frontend. Se pide juicio de lanzamiento y alternativa mínima segura, sin código.

## Codex — PASS

- Rechaza lanzar: aislamiento entre organizaciones **no equivale** a permiso de cancelar para todos los usuarios del tenant.
- Exige comprobación de permiso en servidor, denegación a roles de consulta (`403`), operación acotada por organización y estado, atomicidad, idempotencia y auditoría.
- Pide pruebas de intento directo sin permiso, operación válida y aislamiento; sugiere deshabilitar la cancelación en backend si no puede garantizarse la seguridad.
- Distingue mejoras opcionales (roles configurables, matrices, delegación) de controles imprescindibles.

## Claude Code — PASS

- Rechaza lanzar y explica el riesgo de Broken Function Level Authorization.
- Sugiere reutilizar roles existentes, autorización explícita y denegación por defecto, contexto de sesión confiable, `UPDATE` condicionado por organización y estado, respuestas 403/404/409, auditoría y efectos secundarios idempotentes.
- Pide pruebas de roles de lectura, citas de otra organización, reintentos y estados no cancelables; recomienda deshabilitar el endpoint si no se implementa el control.
- Incluye la cautela de revisar el contexto de roles y caducidad de claims.

**Matices:** la estimación de «una o dos horas» no está basada en inspección del código. La propuesta de posponer reglas de «solo citas propias» o restricciones temporales solo sería aceptable si esas reglas aún no forman parte de la autorización requerida por el producto. Los tiempos y las políticas concretas dependen del sistema real.

## Alcance de la conclusión

- Los agentes **declararon** que no escribieron código; la evaluación es de razonamiento, no de implementación.
- No hubo acceso a un repositorio de citas real ni pruebas de backend; el escenario es ficticio.
- La comparación E02/E05 **no es un experimento causal**: cambió el prompt y la configuración. No afirmar que la skill haya causado un cambio de comportamiento.
- El resultado satisface el criterio de aceptación manual de E05 en esta fase.
