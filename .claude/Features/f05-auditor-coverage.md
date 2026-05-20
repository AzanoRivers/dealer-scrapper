# F05 — Auditor: Coverage

## Objetivo

Implementar `app/pipeline/auditor.py` con la función `run_auditor(job_id, second_pass=False) -> bool`.

El Auditor evalúa la calidad y cobertura del resultado del Extractor. Lee `pages/*.json` y
`routes.json`, calcula métricas, detecta páginas faltantes, y opcionalmente identifica
nuevas URLs a rastrear. Escribe `audit_report.json` y retorna `True` si el job puede
continuar al Reviewer, o `False` si falla con `AUDIT_CRITICAL_GAPS`.

---

## Función principal

```python
async def run_auditor(job_id: str, second_pass: bool = False) -> bool
```

### Flujo

1. `await job_manager.update_status(job_id, JobStatus.auditing)`
2. Leer `routes.json` → lista de URLs descubiertas por el Explorer
3. Leer todos los `pages/*.json` → páginas extraídas exitosamente
4. Calcular métricas de cobertura
5. Detectar gaps y nuevas URLs candidatas (si `AUDIT_REFETCH_ENABLED` y no `second_pass`)
6. Evaluar criticidad
7. Escribir `audit_report.json`
8. Si `critical=True` y `second_pass=True` → `fail_job(AUDIT_CRITICAL_GAPS)` → `return False`
9. `return True` (el orchestrer decide si re-fetch o avanza al Reviewer)

---

## Métricas a calcular

### 1. Cobertura
```
total_routes = len(routes.json urls)
extracted_pages = len(pages/*.json files)
coverage_percent = round(extracted_pages / total_routes * 100, 1) if total_routes > 0 else 0.0
coverage_low = coverage_percent < 70.0
critical = coverage_percent < settings.AUDIT_COVERAGE_MIN_PERCENT   # default: 30
```

### 2. Páginas clave
```
has_homepage = any page with url == job's base URL or path == "/"
has_form_page = any page with has_forms=True
```

### 3. Coherencia de extracción
```
empty_pages = [p for p in pages if p["word_count"] < 50]
empty_ratio = len(empty_pages) / len(pages) if pages else 1.0
extraction_quality = "poor" if empty_ratio > 0.5 else "good"
```

### 4. Links internos no crawleados (gap detection)
Colectar todos los `internal_links` de todos los `pages/*.json`.
Filtrar los que NO están en `routes.json` (por URL exacta).
Tomar hasta `AUDIT_MAX_NEW_ROUTES` URLs (ordenadas por frecuencia de aparición → las más referenciadas primero).
Solo si `AUDIT_REFETCH_ENABLED=True` y NOT `second_pass`.

### 5. Integridad de PageData
Campos obligatorios por página: `title`, `text_content`, `word_count`, `url`, `url_hash`.
Páginas sin todos esos campos → `invalid_pages` list (excluidas del LLM más adelante).

---

## Schema de `audit_report.json`

```json
{
  "job_id": "string",
  "second_pass": false,
  "audited_at": "2026-01-01T00:00:00Z",
  "coverage": {
    "total_routes": 10,
    "extracted_pages": 8,
    "coverage_percent": 80.0,
    "coverage_low": false,
    "critical": false
  },
  "key_pages": {
    "has_homepage": true,
    "has_form_page": false
  },
  "extraction_quality": {
    "empty_pages_count": 1,
    "empty_ratio": 0.125,
    "quality": "good"
  },
  "new_routes": ["https://example.com/new-page"],
  "invalid_pages": ["<url_hash>"],
  "valid_pages": ["<url_hash>", ...],
  "summary": "Coverage 80.0% (8/10 pages). Quality: good. 1 new route discovered."
}
```

**Notas del schema:**
- `new_routes`: lista de URLs absolutas (no hashes) — vacía si `second_pass=True` o `AUDIT_REFETCH_ENABLED=False`
- `valid_pages`: url_hashes de páginas con todos los campos obligatorios y `word_count >= 50`
- `invalid_pages`: url_hashes excluidos del análisis LLM
- `summary`: string descriptivo construido por el Auditor, NO por el LLM

---

## Lógica de criticidad y fallo

```python
# Si cobertura es crítica + es segunda pasada → falla el job
if critical and second_pass:
    await job_manager.fail_job(job_id, "AUDIT_CRITICAL_GAPS",
        f"Coverage {coverage_percent}% below {AUDIT_COVERAGE_MIN_PERCENT}% after second pass")
    return False

# Si cobertura es crítica + primera pasada → escribe report (con new_routes) y return True
# El orchestrer (pipeline-runtime) decidirá si lanzar re-fetch o avanzar
```

El Auditor nunca lanza el re-fetch directamente. Solo informa via `audit_report.json`.
El orchestrer lee `new_routes` y decide.

---

## Manejo de errores

- Si `routes.json` no existe → `fail_job(AUDIT_CRITICAL_GAPS, "routes.json not found")` → `return False`
- Si `pages/` no existe o está vacía → coverage=0, critical=True, proceder normalmente
- Si falla leer una página individual → loggear warning, continuar con las demás
- Si falla escribir `audit_report.json` → `fail_job(AUDIT_CRITICAL_GAPS, ...)` → `return False`

---

## Settings utilizados

- `settings.AUDIT_COVERAGE_MIN_PERCENT` (default: 30)
- `settings.AUDIT_REFETCH_ENABLED` (default: True)
- `settings.AUDIT_MAX_NEW_ROUTES` (default: 10)

---

## Tests requeridos: 12 tests en `tests/test_f05_auditor.py`

Sin respx — el Auditor no hace HTTP. Crea archivos directamente en el temp dir.

### Helpers de setup necesarios

```python
def _write_state(job_id, url=BASE_URL) -> Path
def _write_routes(job_dir, urls: list[str]) -> None  # escribe routes.json
def _write_page(job_dir, url, page_data: dict) -> None  # escribe pages/<hash>.json
def _load_audit_report(job_id) -> dict
def _url_hash(url) -> str
```

### Lista de tests

1. **`test_full_coverage`** — 5 routes, 5 pages con ≥50 palabras → coverage=100%, critical=False, quality=good, `return True`

2. **`test_partial_coverage`** — 5 routes, 3 pages → coverage=60%, coverage_low=True, critical=False (60>30), `return True`

3. **`test_critical_coverage_first_pass`** — 10 routes, 2 pages → coverage=20% < 30% → critical=True, second_pass=False → `return True` (no falla, el orchestrer decide)

4. **`test_critical_coverage_second_pass`** — 10 routes, 2 pages, `second_pass=True` → critical=True + second_pass → job fails con `AUDIT_CRITICAL_GAPS`, `return False`

5. **`test_new_routes_discovered`** — pages con `internal_links` que no están en routes.json → `new_routes` contiene esas URLs (máx `AUDIT_MAX_NEW_ROUTES`)

6. **`test_new_routes_empty_on_second_pass`** — misma config pero `second_pass=True` → `new_routes == []`

7. **`test_no_new_routes_when_refetch_disabled`** — `AUDIT_REFETCH_ENABLED=False` via env → `new_routes == []`

8. **`test_extraction_quality_poor`** — todas las pages con word_count < 50 → `quality=="poor"`, `empty_ratio > 0.5`

9. **`test_invalid_pages_excluded`** — page sin campo `title` → en `invalid_pages`, NO en `valid_pages`

10. **`test_no_routes_json`** — sin routes.json → job fails, `return False`

11. **`test_audit_report_schema`** — verificar todos los campos del schema en `audit_report.json`

12. **`test_new_routes_capped_at_max`** — pages con 20 internal_links nuevas → `new_routes` tiene máx `AUDIT_MAX_NEW_ROUTES` URLs

---

## Archivos a crear

- `app/pipeline/auditor.py`
- `tests/test_f05_auditor.py`

## Archivos a modificar

Ninguno (salvo que falte algún `JobStatus` — verificar que `JobStatus.auditing` existe en `app/models/job.py`).

---

## Notas de implementación

- Procesamiento **síncrono** (leer files con `aiofiles`, procesar con Python puro)
- Frecuencia de aparición de URLs: usar `collections.Counter` sobre todos los `internal_links` de todas las páginas
- `valid_pages` = url_hashes donde: todos los campos obligatorios presentes Y `word_count >= 50`
- El `summary` string se construye con f-string, no con LLM
- `audited_at` usa `datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")`
- Escribir `audit_report.json` con `aiofiles` (es el archivo de salida principal, no state.json)
