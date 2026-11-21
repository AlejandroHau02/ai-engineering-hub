# E04 — revisión cruzada de prototipos (21-nov-2026)

**Estado:** evidencia preliminar aportada por el propietario. **Fuentes:** transcripciones compartidas en conversación; los archivos originales de prototipos y las capturas de reproducción no están en este repositorio, por lo que la verificación independiente sigue pendiente.

## Protocolo

- **Codex** revisó el prototipo E03 creado por **Claude Code** (HTML/CSS/JS).
- **Claude Code** revisó el prototipo E03 creado por **Codex**.
- Ambos indicaron no modificar archivos.
- Las implementaciones se evaluaron en carpetas locales separadas. No se copió código ni información personal a este repositorio público.

## Codex → prototipo de Claude

Reportó **cuatro defectos de lógica**, reproducidos según su informe mediante DOM simulado:

1. Una carga asincrónica puede marcar mensajes como leídos cuando el operador abandona el detalle antes de verlos.
2. Los tiempos de espera se calculan desde un reloj fijo y permanecen congelados al actualizar.
3. Navegación móvil atrás/adelante: el estado del historial no restaura el detalle al avanzar.
4. Durante un error de listado persisten resumen y acción de apertura basados en información anterior.

Verificación declarada: `node --check` y cuatro reproducciones con DOM simulado. **Sin validación visual en navegador.** No reportó problemas de inyección confirmados en ese prototipo porque el texto se inserta con `textContent`.

## Claude → prototipo de Codex

Reportó **seis defectos reproducidos en Chrome headless**, según su informe:

1. Lista con altura cero en pantallas de poca altura/zoom.
2. Escape desde el buscador cierra inesperadamente el detalle.
3. En móvil, el botón atrás del navegador sale de la página en lugar de regresar al listado.
4. Indicador visual de selección no visible en modo de alto contraste emulado.
5. Error de bandeja y detalle abierto generan estados contradictorios.
6. Enlace de salto apunta a un elemento oculto en detalle móvil.

Riesgos/observaciones adicionales: uso de `innerHTML` con campos que serían no confiables si se conectaran datos externos, cadenas largas que desbordan, ausencia de ordenación explícita pese a promesa «pendientes primero», campos con contornos de bajo contraste, tipografía pequeña, compactación insuficiente y formato poco legible.

Verificación declarada: `node --check`, navegador headless, tamaños variados, comportamiento de teclado, alto contraste emulado y árbol de accesibilidad. **No probó lector de pantalla real ni dispositivo físico.**

**Precisión pendiente:** el reporte afirma que todo el texto pasa WCAG AA, pero también menciona un contraste mínimo de 4,39:1; un valor menor a 4,5:1 podría incumplir el umbral para texto normal. Revisar el elemento y la excepción aplicable antes de darlo por válido.

## Interpretación

- Hay evidencia de utilidad de revisión cruzada, **no** evidencia causal de mejora por instalar el Hub.
- La profundidad de comprobación visual **no es comparable** cuando un agente tiene navegador disponible y el otro no.
- Comparar número bruto de hallazgos no mide calidad del revisor: faltan verificación independiente, severidad de los verdaderos positivos y estimación de falsos positivos.
- No asignamos una puntuación cuantitativa de E04 con las fuentes disponibles: no se han recibido artefactos/capturas de reproducción verificables ni hay condiciones equivalentes.

## Acciones derivadas

1. Añadir a la skill de UX/UI una checklist breve para altura pequeña y zoom, navegación móvil, historial, foco, estados de error, contraste y alto contraste.
2. Separar verificación lógica (DOM simulado) de verificación real renderizada. Informar siempre qué se probó y qué no.
3. Añadir a la skill de frontend una comprobación explícita de inserción segura de texto externo en el DOM.
4. Recomendar revisión independiente únicamente en cambios de impacto medio/alto o interfaces operativas complejas; evitar aumentar consumo para arreglos triviales.
5. Preparar actualizaciones **opt-in** de skills ya instaladas (v0.1 protege archivos existentes y no actualiza SKILL.md divergentes).
