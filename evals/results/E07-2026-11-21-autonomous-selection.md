# E07 — Selección autónoma de especialistas (21-nov-2026)

**Estatus:** prueba exploratoria completada; **invocación autónoma de especialistas no demostrada**.

## Método

Se abrió el mismo prototipo local estático `hub-eval-codex/e03-bandeja` en sesiones de Claude Code y Codex con las instrucciones y ocho especialistas globales instalados. El prompt pidió evaluar si la bandeja estaba lista para recibir datos reales, revisar UX, accesibilidad y riesgos de integración, priorizar bloqueos y distinguir comprobaciones; **no mencionó subagentes ni skills**. Esta prueba no utilizó un backend real.

## Codex

- Dictamen: **no apta todavía para datos reales**.
- Identificó interpolación de contenido externo a `innerHTML` (`app.js:30,46`) como posible XSS futuro. Reportó que una cadena `<img … onerror=…>` pasaba intacta a la plantilla, **sin ejecutar el payload en navegador**.
- Indicó reproducciones en DOM simulado de excepciones con mensajes vacíos, nombres `null` e IDs de texto.
- Señaló que `unread = 0` se altera en memoria sin persistencia, contratos/errores/reintentos reales, controles de acceso servidor todavía no verificables, fechas/orden fijo y posibles problemas de altura y navegación.
- Reportó `node --check`, siete comprobaciones en DOM simulado y consulta de skills `code-review`, `backend-engineering` y `ux-ui`. **No hay reporte de invocación de un especialista nativo**. No modificó archivos.
- Sin navegación real, lector de pantalla ni pruebas de backend.

## Claude Code

- Dictamen: **no apta todavía para datos reales**.
- Identificó carencia de integración API, XSS condicional al conectar datos externos, presupuestos frágiles del formato de registros, fechas estáticas, estados ficticios, requisitos de autorización servidor y persistencia de leído.
- Señaló necesidad de ordenación explícita, corrección de layouts de poca altura, navegación móvil, alto contraste y contraste.
- Reportó lectura de los tres archivos y búsquedas de uso de `innerHTML`, `pushState` y `forced-colors`. No ejecutó pruebas funcionales y no accedió al navegador; **dijo explícitamente que no volvió a invocar `hub-ux-auditor`**. No modificó archivos.
- La ausencia de paginación o actualización en vivo no es necesariamente un bloqueo universal: depende del alcance y volumen. Su propuesta de iniciar un piloto tras resolver solo los primeros puntos requiere asegurar también permisos y persistencia según el producto.

## Interpretación de orquestación

- **Descubrimiento y delegación explícita:** evidencias reportadas previamente en E06; no repetidas aquí.
- **Delegación autónoma:** no observada. Codex consultó skills; eso no equivale a invocar un especialista. Claude reutilizó evidencia de E06 sin invocarlo de nuevo.
- **Criterio de no delegación:** puede ser racional porque ambos pudieron contestar la revisión directamente. No constituye un fallo automático de la capa.
- **Calidad de razonamiento:** buena y proporcional en ambos, con diferente profundidad de validación. No hay prueba causal del efecto del Hub, ni condiciones de herramientas idénticas.

## Decisión

Considerar la **capa nativa de asesores opcionales operativa en modalidad dirigida** y cerrar el piloto exploratorio. **No prometer selección autónoma** ni iniciar un mecanismo de delegación automática sin métricas de coste, calidad y falsos positivos. Para siguientes cambios, priorizar estabilización de PR #1, #5 y #7; después diseñar el registro/scout externo con evaluación de confianza y aprobación explícita.

No se copiaron archivos de prototipo, credenciales ni datos privados al repositorio público.
