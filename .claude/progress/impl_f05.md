# Reporte de Implementación — F05 Auditor: Coverage

**Feature**: F05 — Auditor: Coverage  
**Estado**: DONE  
**Fecha**: 2026-01-01

---

## Archivos Creados

### `app/pipeline/auditor.py`

Función principal: `async def run_auditor(job_id: str, second_pass: bool = False) -> bool`

**Implementación:**
- `update_status(job_id, JobStatus.auditing)` al inicio
- Lee `routes.json` con `aiofiles` → extrae `route_urls` (set de URLs)
- Lee todos los `pages/*.json` con `aiofiles` en loop
- Calcula métricas:
  - `coverage_percent = round(extracted / total * 100, 1)`
  - `coverage_low = coverage_percent < 70.0`
  - `critical = coverage_percent < settings.AUDIT_COVERAGE_MIN_PERCENT`
  - `empty_pages_count`, `empty_ratio`, `extraction_quality` ("good" / "poor")
- `valid_pages` = hashes con todos los campos obligatorios Y `word_count >= 50`
- `invalid_pages` = hashes con algún campo faltante o None
- `new_routes` = internal_links no en routes, ordenados por frecuencia (Counter), capped en `AUDIT_MAX_NEW_ROUTES`, solo si `AUDIT_REFETCH_ENABLED` y no `second_pass`
- Escribe `audit_report.json` con `aiofiles`
- Si `critical=True AND second_pass=True` → `fail_job("AUDIT_CRITICAL_GAPS")` → `return False`
- Si `routes.json` no existe → `fail_job("AUDIT_CRITICAL_GAPS")` → `return False`

**Manejo de errores:**
- `routes.json` ausente → fail inmediato
- Página individual ilegible → warning, continua
- Error al escribir `audit_report.json` → fail con AUDIT_CRITICAL_GAPS

### `tests/test_f05_auditor.py`

12 tests, sin respx, sin HTTP. Helpers:
- `_make_job_id()` → uuid4
- `_url_hash(url)` → sha256
- `_write_state(job_id, url)` → crea state.json en JOB_BASE_DIR
- `_write_routes(job_dir, urls, base_url)` → routes.json en formato Explorer
- `_write_page(job_dir, url, extra, remove_fields)` → pages/<hash>.json con PageData válido
- `_load_audit_report(job_id)` → lee audit_report.json
- `_load_state(job_id)` → lee state.json

---

## Checkpoints F05 Verificados

- [x] Calcula `coverage_percent` correctamente (pages/ vs routes.json)
- [x] `critical: true` cuando `coverage_percent < AUDIT_COVERAGE_MIN_PERCENT`
- [x] Detecta `internal_links` no crawleados y los agrega (respeta `AUDIT_MAX_NEW_ROUTES`)
- [x] `second_pass=true` en segunda ejecución (no bucle infinito)
- [x] `audit_report.json` en formato correcto

---

## Decisiones de Diseño

1. **Formato routes.json**: usa el schema real del Explorer (`{"routes": [{"url":...}], "base_url":..., ...}`). Los helpers de test producen el mismo formato.

2. **valid_pages vs invalid_pages**: páginas con todos los campos presentes pero `word_count < 50` no están en ninguna de las dos listas (solo impactan `empty_ratio`).

3. **`_url_path` helper**: función auxiliar local que extrae el path de una URL para detectar homepage (path == "/").

4. **Counter para new_routes**: `collections.Counter` sobre todos los `internal_links` de todas las páginas, filtrados por los que no están en `route_urls`. `most_common(AUDIT_MAX_NEW_ROUTES)` garantiza el límite y el orden por frecuencia.

5. **Orden de operaciones**: `audit_report.json` se escribe ANTES de verificar `critical + second_pass`. Así el orchestrer siempre puede leer el reporte aunque el job falle.

---

## Tests: 12/12

| # | Test | Estado |
|---|------|--------|
| 1 | test_full_coverage | ✓ |
| 2 | test_partial_coverage | ✓ |
| 3 | test_critical_coverage_first_pass | ✓ |
| 4 | test_critical_coverage_second_pass | ✓ |
| 5 | test_new_routes_discovered | ✓ |
| 6 | test_new_routes_empty_on_second_pass | ✓ |
| 7 | test_no_new_routes_when_refetch_disabled | ✓ |
| 8 | test_extraction_quality_poor | ✓ |
| 9 | test_invalid_pages_excluded | ✓ |
| 10 | test_no_routes_json | ✓ |
| 11 | test_audit_report_schema | ✓ |
| 12 | test_new_routes_capped_at_max | ✓ |
