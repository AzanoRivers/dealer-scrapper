# AGENTS.md — Mapa del Proyecto para Agentes

> Leer este archivo primero. Navega al documento correcto según la tarea.

## Archivos Clave

| Archivo | Propósito | Cuándo leerlo |
|---------|-----------|---------------|
| `.claude/AGENTS.md` | Este archivo — mapa del proyecto | Siempre primero |
| `CLAUDE.md` | Contexto técnico completo, stack, infra, comandos | Al inicio de cada sesión |
| `.claude/docs/DealerScrapper_PLAN.md` | Plan maestro detallado con specs de cada subagente de la app | Cuando necesitás specs de implementación |
| `.claude/CHECKPOINTS.md` | Criterios de estado final correcto por feature | El Reviewer lo consulta siempre |
| `.claude/feature_list.json` | Estado actual de cada feature (pending/in_progress/done) | Al decidir qué trabajar |
| `.claude/progress/current.md` | Plan vivo de la sesión activa | Al retomar trabajo |
| `.claude/progress/history.md` | Bitácora append-only de sesiones cerradas | Para contexto de decisiones anteriores |
| `.claude/Features/` | Specs detalladas de cada feature a implementar | El Implementer lo lee antes de codear |

## Roles de Agentes

| Agente | Archivo | Cuándo invocarlo |
|--------|---------|------------------|
| Implementer | `.claude/agents/implementer.md` | Para implementar una feature |
| Reviewer | `.claude/agents/reviewer.md` | Para validar una feature implementada |
| VPS Deploy | `.claude/agents/vps-deploy.md` | Para deployar al VPS de producción |
| Pipeline Runtime | `.claude/agents/pipeline-runtime.md` | Para orquestar un job de scraping en ejecución |

## Flujo de Desarrollo

```
1. .\.claude\init.ps1                           → verifica estado del harness
2. Leer .claude/feature_list.json              → identificar próxima feature pending
3. Leer .claude/Features/<feature_file>        → specs de la feature
4. Actualizar .claude/feature_list.json        → status: "in_progress"
5. Escribir plan en .claude/progress/current.md
6. Invocar Implementer                         → implementa la feature
7. Implementer escribe .claude/progress/impl_<id>.md
8. Invocar Reviewer                            → valida contra .claude/CHECKPOINTS.md
9. Reviewer escribe .claude/progress/review_<id>.md
10. Si APPROVED:
    - Actualizar .claude/feature_list.json → status: "done"
    - Mover plan a .claude/progress/history.md
    - Limpiar .claude/progress/current.md
11. Si REJECTED:
    - Invocar Implementer de nuevo con el reporte del Reviewer
    - Repetir desde paso 7
```

## Reglas del Harness

1. **Una feature a la vez.** `.claude/init.ps1` rechaza múltiples `in_progress`.
2. **Estado en disco, no en chat.** Todo output → archivos en `.claude/progress/`.
3. **El Implementer no se autoaprueba.** Siempre pasa por el Reviewer.
4. **El Reviewer no edita código.** Solo reporta.
5. **Referencias, no contenido.** Los agentes pasan paths de archivos, no el contenido por chat.
6. **Cada sesión cierra limpio.** `.claude/progress/current.md` limpiado, `history.md` actualizado.

## Subagentes de la Aplicación (NO son agentes Claude Code)

Los siguientes son **módulos Python** de la app, no agentes de `.claude/agents/`:

- `Explorer` → `app/pipeline/explorer.py`
- `Fetcher` → `app/pipeline/fetcher.py`
- `Extractor` → `app/pipeline/extractor.py`
- `Auditor` → `app/pipeline/auditor.py`
- `Reviewer (LLM)` → `app/pipeline/reviewer.py`
- `Packager` → `app/pipeline/packager.py`

Sus specs están en `.claude/docs/DealerScrapper_PLAN.md` Parte 7.
