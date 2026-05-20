# F04 — Extractor: PageData — Implementation Report

**Fecha:** 2026-05-18  
**Feature:** F04 — Extractor: PageData  
**Estado:** DONE

---

## Archivos creados

### `app/pipeline/extractor.py`
- Función principal `async def run_extractor(job_id: str) -> bool`
- Función auxiliar `_extract_page_data(html_content: str, page_url: str) -> dict`
- Función auxiliar `_url_hash(url: str) -> str` (mirrors fetcher.py)

### `tests/test_f04_extractor.py`
- 12 tests cubriendo todos los checkpoints de F04

---

## Implementación

### Flujo principal (`run_extractor`)

1. Lee `fetch_results.json` — falla con `EXTRACTION_EMPTY` si no existe
2. `await job_manager.update_status(job_id, JobStatus.extracting)` al inicio
3. Filtra solo entradas con `status: "success"`
4. Crea directorio `pages/` con `mkdir(parents=True, exist_ok=True)`
5. Procesa cada página secuencialmente:
   - Lee `raw/<url_hash>.html` con `aiofiles`
   - Extrae PageData con `_extract_page_data()`
   - Escribe `pages/<url_hash>.json` con `aiofiles`
   - `os.remove(raw_html_path)` — inmediatamente tras escribir JSON
   - Si falla: marca como `"failed"`, intenta eliminar `.html`, continúa
   - `await job_manager.update_progress(job_id, pages_done=done, pages_total=total)`
6. `raw_dir.rmdir()` — silencioso si no vacío
7. Si `empty_pages > total_successful * 0.5` → `fail_job(EXTRACTION_EMPTY)` → `return False`
8. Escribe `extract_results.json`
9. `return True`

### Extracción de PageData (`_extract_page_data`)

Todos los campos implementados según spec:
- `title`: `<title>` con fallback a primer `<h1>`
- `meta_description`: `<meta name="description">`
- `meta_keywords`: `<meta name="keywords">` split por ","
- `og_data`: todos los `<meta property="og:*">`
- `canonical_url`: `<link rel="canonical">`
- `language`: `<html lang="...">`
- `headings`: dict con h1, h2, h3
- `text_content`: readability-lxml → BS4 `get_text()` → truncado a 10.000 chars
- `word_count`: `len(text_content.split())`
- `internal_links`: mismo netloc (con `lstrip("www.")`)
- `external_links`: solo http/https de dominios distintos
- `images`: src absoluta, alt, width (int|None), height (int|None)
- `schema_org`: JSON-LD parseado como lista de dicts
- `has_forms`: bool
- `has_tables`: bool
- `extracted_at`: ISO 8601 UTC con "Z"

---

## Tests implementados (12/12)

| # | Test | Descripción |
|---|------|-------------|
| 1 | `test_extracts_all_fields` | HTML completo → todos los campos con valores correctos |
| 2 | `test_title_fallback_to_h1` | Sin `<title>` → usa primer `<h1>` |
| 3 | `test_text_content_readability` | text_content no contiene tags HTML crudas |
| 4 | `test_text_content_max_10000_chars` | Texto largo → truncado a ≤ 10.000 chars |
| 5 | `test_internal_vs_external_links` | Links internos vs externos clasificados correctamente |
| 6 | `test_images_resolved_to_absolute` | `src="/logo.png"` → `https://example.com/logo.png` |
| 7 | `test_schema_org_parsed` | JSON-LD → lista de dicts (array mergeado, invalid ignorado) |
| 8 | `test_raw_html_deleted_immediately` | `raw/<hash>.html` eliminado tras extraer |
| 9 | `test_raw_dir_removed_when_empty` | `raw/` eliminado al terminar todas las páginas |
| 10 | `test_extraction_empty_fails_job` | word_count < 50 en >50% → EXTRACTION_EMPTY |
| 11 | `test_partial_failure_continues` | Error en 1 página no aborta las demás |
| 12 | `test_extract_results_json_format` | Schema completo de extract_results.json |

---

## Checkpoints F04 verificados

- [x] Extrae todos los campos de PageData definidos en el plan (Parte 7.3)
- [x] `text_content` usa `readability-lxml`
- [x] URLs de imágenes resueltas a absolutas (via `urljoin`)
- [x] Elimina `raw/<hash>.html` inmediatamente tras extraer cada página (`os.remove`)
- [x] Elimina directorio `raw/` al terminar (`raw_dir.rmdir()` silencioso)
- [x] `EXTRACTION_EMPTY` si > 50% con `word_count < 50`

---

## Notas técnicas

- El extractor es CPU-bound; el loop interno es síncrono pero la función es `async` por compatibilidad.
- `readability-lxml` tiene fallback a `BeautifulSoup.get_text()` si falla.
- `lstrip("www.")` implementado exactamente como especificado para clasificación de links.
- `JobStatus.extracting` ya existía en `app/models/job.py` — no fue necesario agregarlo.
- No se requirió respx ni mocking HTTP — el extractor opera exclusivamente sobre archivos locales.
