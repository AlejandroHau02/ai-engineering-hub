# E03 — Implementación UX/UI en proyecto sintético

Ejecutar en **una carpeta nueva y vacía por agente** (por ejemplo `hub-eval-claude` y `hub-eval-codex`). No ejecutar en una aplicación real. Copia el prompt idéntico, sin mencionar skills.

## Prompt

Construye un prototipo funcional de una bandeja de atención para el personal de una plataforma SaaS que recibe conversaciones de distintos negocios y sucursales.

El usuario principal es un operador que necesita: identificar conversaciones pendientes, localizar una conversación por cliente y filtrar por sucursal o estado. La acción central es abrir una conversación y consultar sus últimos mensajes. El diseño debe priorizar rapidez de lectura y un aspecto propio, sobrio y profesional; evita plantillas genéricas de dashboard con tarjetas de KPIs innecesarias.

Restricciones: usa **HTML, CSS y JavaScript puros**, sin dependencias, sin CDN y sin backend. Crea `index.html`, `styles.css` y `app.js` con datos completamente ficticios (al menos 8 conversaciones, 2 negocios, 3 sucursales). Los filtros y la búsqueda deben funcionar de verdad. Incluye estados vacío, conversación no seleccionada y un error simulado que el operador pueda recuperar; diseña escritorio y móvil, acceso con teclado, foco visible y etiquetas accesibles.

Puedes tomar decisiones de diseño sin preguntarme por detalles menores. No despliegues ni instales paquetes. Al finalizar, explica qué decisiones de UX tomaste, qué verificaste ejecutando herramientas y qué quedó sin comprobar. Si puedes visualizar la interfaz, revísala realmente.

## Evaluación humana

Revisar en un navegador en escritorio y móvil, probar búsqueda, filtros, selección, estados, foco y legibilidad. Evaluar el resultado visual, no solo la explicación textual. No premiar componentes decorativos que no aportan a la tarea.
