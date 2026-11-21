# Capa de especialistas — v0.2 (vista previa)

**Objetivo:** extender el Engineering Hub instalado globalmente con asesores de segunda opinión, sin tocar la configuración de cada proyecto ni generar un enjambre automático.

## Diseño

- La fuente compartida es [`catalog.json`](catalog.json). Cada ficha define un nombre, criterio de activación, skill preexistente, focos y entregable; son definiciones **first-party** del Hub.
- [`scripts/specialists.py`](../scripts/specialists.py) valida el catálogo (identificadores seguros, skills conocidas, estructura) y genera archivos nativos de manera determinista.
- Claude Code los detecta en `~/.claude/agents/hub-*.md`, con `tools: Read, Glob, Grep`, `permissionMode: plan` y la skill correspondiente precargada. El modelo usado queda bajo la configuración del usuario, no se fuerza uno.
- Codex los detecta en `~/.codex/agents/hub-*.toml` con `name`, `description` y `developer_instructions`. Las skills globales existentes se mantienen disponibles; no se fuerza modelo ni se modifica `config.toml`.
- **Importante:** Codex hereda permisos y sandbox de la sesión principal. Las instrucciones «no editar» **no garantizan** un sandbox de solo lectura. Para revisiones de alto riesgo, utiliza permisos/sandbox de lectura en la sesión principal. Claude limita herramientas, pero ningún prompt sustituye políticas de permisos del cliente.

## Especialistas iniciales

| Nombre | Activación recomendada | Skill |
| --- | --- | --- |
| `hub-ux-auditor` | Revisión UX/UI y accesibilidad de interfaces sustanciales | `ux-ui` |
| `hub-backend-auditor` | Seguridad de API, permisos, multitenencia, concurrencia | `backend-engineering` |
| `hub-architecture-advisor` | Decisiones sobre límites, contratos, escala o migraciones | `architecture-review` |
| `hub-code-reviewer` | Revisión independiente de cambios materiales | `code-review` |

Todos **analizan y recomiendan**; el agente principal toma las decisiones, implementa y coordina las pruebas. No se les asignan tareas de escritura, merge, despliegue ni instalación.

## Instalación opcional, global y explícita

En el checkout de la rama que contiene esta capa:

```powershell
python scripts/sync.py --target both --specialists
```

Esto es un **dry-run**: no escribe nada. Espera ocho entradas nuevas de especialistas (cuatro por cliente); si las instrucciones globales aún no están sincronizadas, también mostrará sus actualizaciones. Revisa cada ruta. Tras aprobarlo:

```powershell
python scripts/sync.py --target both --specialists --apply
python scripts/sync.py --target both --specialists
```

La última ejecución debe decir `NO CHANGES`. Los archivos existentes que difieran del catálogo **bloquean** la operación, incluso con `--apply`, para respetar cambios propios. Como en v0.2, las configuraciones globales administradas se respaldan antes de cambiarse.

Para solo Claude o Codex: `--target claude` o `--target codex`. Sin `--specialists` el instalador **no crea** agentes. No realiza llamadas API ni ejecuta agentes; solo escribe archivos declarativos.

## Uso

Inicia una nueva sesión desde cualquier repositorio y pide, por ejemplo:

- «Pide al **hub-ux-auditor** una revisión de accesibilidad de esta pantalla y vuelve con los bloqueos principales».
- «Delegar en **hub-backend-auditor** la revisión del endpoint de cancelación, especialmente permisos dentro de tenant».
- «Consulta **hub-architecture-advisor** sobre las opciones de migración, sin modificar código».
- «Usa **hub-code-reviewer** para revisar este diff; quiero hallazgos concretos y sin cambios».

En Claude Code puedes utilizar `/agents` para explorar agentes disponibles. En Codex CLI utiliza `/subagents` para inspeccionar subagentes activos. La decisión de delegar automáticamente depende del modelo, el contexto y la versión; **no prometemos invocación automática**. Si un especialista no aparece, revisa el archivo global y la versión del cliente.

## Selección proporcional

1. **Tarea pequeña, poco riesgo:** responde el agente principal, sin subagentes.
2. **Implementación sustancial:** el principal implementa; pide una segunda opinión especializada si detecta decisiones significativas o incertidumbre.
3. **Cambio crítico:** solicita un asesor independiente para un objetivo preciso y documenta las pruebas realizadas y no realizadas.
4. **Nunca** lances revisores redundantes por ceremonia; los subagentes consumen tokens adicionales. No uses revisiones paralelas que modifiquen los mismos archivos.

## Fuentes técnicas (consulta de noviembre de 2026)

- [Claude Code — Create custom subagents](https://code.claude.com/docs/en/sub-agents).
- [Codex — Subagents](https://developers.openai.com/codex/multi-agent).

## Alcance pendiente

No incluye aún la interfaz local, el descubrimiento de skills de terceros, reglas de selección automática medidas, renovación de perfiles modificados ni ejecución cruzada automática entre proveedores. Todo eso necesitará pruebas, revisión y autorización adicionales.
