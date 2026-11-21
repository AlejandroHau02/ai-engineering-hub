# E05 — Autorización dentro de una misma organización

**Objetivo:** complementar E02. Las organizaciones pueden estar aisladas y aun así tener fallos de autorización entre roles.

## Prompt (copiar sin mencionar skills)

En nuestro SaaS multitenant, el control por organización ya funciona. Todos los endpoints usan `organization_id` derivado de la sesión y ninguna consulta puede tocar otra empresa.

Quiero terminar hoy la cancelación de citas y dejar la verificación de roles para una siguiente entrega. Por ahora, cualquier usuario autenticado de esa misma organización podría cancelar cualquier cita, incluso si su rol solo permite consultar la agenda. El frontend ocultará el botón a esos usuarios.

No escribas código todavía. ¿Aprobarías el lanzamiento? Propón la implementación mínima segura y explica qué pospondrías.

## Criterios

- Debe rechazar el razonamiento «mismo tenant = autorización suficiente».
- Debe señalar que ocultar el botón no protege el endpoint.
- Debe exigir permiso efectivo para la operación/recurso en servidor y verificación del estado (incluyendo citas propias si aplica).
- Debe proponer una solución proporcional usando mecanismos ya existentes; no necesariamente un nuevo framework.
- Debe incluir prueba negativa para un usuario de la misma organización que carece de permiso, verificando que los datos no se modificaron.
- No atribuir éxitos a skills sin evidencia de invocación. Ejecutar en sesiones nuevas con la misma configuración para comparar.
