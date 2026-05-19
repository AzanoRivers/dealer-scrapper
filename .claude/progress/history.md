# Bitácora de Sesiones

> Append-only. No borrar entradas previas. El líder agrega al cerrar cada sesión.

## Formato de entrada

```
## [FECHA] — Feature <id>: <nombre>
- **Resultado**: APPROVED | REJECTED | BLOCKED
- **Implementer**: reporte en `impl_<id>.md`
- **Reviewer**: reporte en `review_<id>.md`
- **Notas**: decisiones no obvias, desvíos del plan, problemas encontrados
```

---

## 2026-05-19 — Feature f10: nginx_systemd_scripts
- **Resultado**: DONE (sin tests automatizados — infraestructura)
- **Archivos creados**: `dealerscrapper.conf`, `dealerscrapper.service`, `scripts/linux/setup.sh`, `scripts/linux/deploy.sh`
- **Notas**: Archivos existían parcialmente (conf y service desactualizados). Actualizados a spec: `limit_req_zone` fuera del bloque server, `include cloudflare-ips.conf` + `deny all` en HTTPS, `User=opc Group=opc`, logs en `logs/`, `RestartSec=5`. Scripts son nuevos. Validación manual en VPS pendiente de deploy.

---

## 2026-05-19 — Feature f09: images_endpoints
- **Resultado**: APPROVED
- **Implementer**: reporte inline (no impl_f09.md separado)
- **Reviewer**: APPROVED — 113/113 tests, 10/10 F09, sin regresiones
- **Notas**: Implementación limpia en primera pasada. 3 stubs reemplazados en router.py. `get_job_image_file` no lee state.json (solo verifica job_dir.exists()). `download_job` retorna 425 si job no está done. `_IMAGE_CONTENT_TYPES` dict a nivel de módulo. FileResponse para imágenes y ZIP. error_code `"job_not_found"` (consistente con plan maestro sección 4.2, no `job_not_found_or_expired`).

---

## 2026-05-19 — Feature f08: packager_output
- **Resultado**: APPROVED
- **Implementer**: reporte en `.claude/progress/impl_f08.md`
- **Reviewer**: reporte en `.claude/progress/review_f08.md`
- **Notas**: 103/103 tests. ~4 min implementación. Decisiones clave: (1) Guard 3 lanzado por run_pipeline en guards.py después de run_packager → no circular import; (2) Guard 3 fuera del finally — debe sobrevivir 15min; (3) _content_type_to_ext rechaza tipos no-imagen explícitos incluso si URL tiene extensión válida; (4) images/index.json creado por Packager con metadata para F09.

---

## 2026-05-19 — Feature f07: reviewer_llm_analysis
- **Resultado**: APPROVED
- **Implementer**: reporte en `.claude/progress/impl_f07.md`
- **Reviewer**: reporte en `.claude/progress/review_f07.md`
- **Notas**: 93/93 tests. ~10 min implementación (feature file completo evitó iteraciones). Decisiones clave: (1) guards.py crea activity_event y lanza Guard2, pasa event a run_reviewer — evita circular import; (2) LLMClient retorna "" en timeout (watchdog detecta); (3) _resolve_valid_page_hashes() maneja tanto formato de auditor real como fixtures de tests; (4) test_f06_guards actualizado para esperar 2 tasks registradas (guard1+guard2).

---

## 2026-05-18 — Feature f06: guards_timeout_cleanup
- **Resultado**: APPROVED
- **Implementer**: reporte en `.claude/progress/impl_f06.md`
- **Reviewer**: reporte en `.claude/progress/review_f06.md`
- **Notas**: 85/85 tests (84/85 determinísticos — test_partial_success F03 es flaky pre-existente en Windows por os.replace race con Defender, no regresión). Decisiones clave: (1) Guard 1 usa loop.call_later en vez de asyncio.sleep para no interferir con mocks de sleep en F03 tests; (2) ENABLE_PIPELINE=0 en conftest evita que tests F01 HTTP lancen pipelines reales; (3) _pipeline_tasks dict en router + cancel/await antes de rmtree resuelve race condition Windows en DELETE; (4) Guard 2 y Guard 3 solo exportados — no invocados en run_pipeline (serán invocados en F07 y F08 respectivamente).

---

## 2026-05-18 — Feature f05: auditor_coverage
- **Resultado**: APPROVED
- **Implementer**: reporte en `.claude/progress/impl_f05.md`
- **Reviewer**: reporte en `.claude/progress/review_f05.md`
- **Notas**: 12/12 tests F05. 76/76 totales. Sin bugs post-Implementer — primera ejecución limpia. Decisión clave: `audit_report.json` se escribe ANTES del check `critical + second_pass` para garantizar que el orchestrer siempre pueda leerlo aunque el job falle. `urlparse` importado localmente en helper `_url_path` (menor, no funcional). Settings: `AUDIT_COVERAGE_MIN_PERCENT=30`, `AUDIT_REFETCH_ENABLED=True`, `AUDIT_MAX_NEW_ROUTES=10`.

---

## 2026-05-18 — Feature f04: extractor_page_data
- **Resultado**: APPROVED
- **Implementer**: reporte en `.claude/progress/impl_f04.md`
- **Reviewer**: reporte en `.claude/progress/review_f04.md`
- **Notas**: 12/12 tests F04. 64/64 totales. Bugs corregidos post-Implementer: (1) `_write_state()` en job_manager cambió de aiofiles a `Path.write_text()` sync para evitar race condition Windows WinError 32/2; (2) `lxml-html-clean` agregado a requirements.txt; (3) fallback text_content: si readability extrae menos palabras que BS4 directo, usar BS4; (4) fixtures HTML enriquecidos a ≥50 palabras para no disparar EXTRACTION_EMPTY.

---

## 2026-05-18 — Feature f03: fetcher_html_download
- **Resultado**: APPROVED
- **Implementer**: reporte en `.claude/progress/impl_f03.md`
- **Reviewer**: reporte en `.claude/progress/review_f03.md`
- **Notas**: 12/12 tests pasando. `update_progress()` agregado a job_manager. Semáforo envuelve loop completo de reintentos. Backoff con `asyncio.sleep`. 52/52 tests totales acumulados.

## 2026-05-18 — Feature f02: explorer_route_discovery
- **Resultado**: APPROVED
- **Implementer**: código existía previo a esta sesión (implementado en sesión Claude anterior, cuota cortó antes de completar)
- **Reviewer**: reporte en `.claude/progress/review_f02.md`
- **Notas**: 12/12 tests pasando. Bugs corregidos: `os.rename()` → `os.replace()` en job_manager (Windows compat); check `NO_ROUTES_FOUND` movido antes de `_ensure_homepage_first`; re-aplicar cap `[:max_pages]` post-homepage-insertion. 40/40 tests totales sin regresiones.
- **Resultado**: APPROVED
- **Implementer**: reporte en `.claude/progress/impl_f01.md`
- **Reviewer**: reporte en `.claude/progress/review_f01.md`
- **Notas**: 28/28 tests pasando. Header X-API-Key ausente devuelve 422 (nativo FastAPI) — test acepta 401 o 422. error_code en /result expirado es `job_not_found` (no `job_not_found_or_expired`). Task registry implementado para cancelación en F06. Escritura atómica y hmac.compare_digest confirmados.
