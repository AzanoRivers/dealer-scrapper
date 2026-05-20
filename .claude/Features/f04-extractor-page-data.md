# F04 — Extractor: PageData

## Objetivo

Implementar `app/pipeline/extractor.py` — el subagente Extractor.
Dado el `job_id`, lee los archivos HTML de `raw/`, extrae datos estructurados de cada página,
escribe `pages/<url_hash>.json` y elimina `raw/<url_hash>.html` inmediatamente.
Al finalizar, escribe `extract_results.json` y elimina el directorio `raw/` si está vacío.

---

## Contrato de entrada

El Extractor recibe:
- `job_id: str` — para leer `fetch_results.json` (lista de URLs exitosas) y los HTML de `raw/`

Lee la lista de páginas exitosas desde `fetch_results.json` (solo `status: "success"`).

---

## Contrato de salida

### PageData JSON individual

Directorio: `/tmp/dealerscrapper/<job_id>/pages/`
Nombre: `<url_hash>.json`

```json
{
  "url": "https://example.com/about",
  "url_hash": "a3f1c2...",
  "title": "About Us",
  "meta_description": "We are a company...",
  "meta_keywords": ["company", "about"],
  "og_data": {
    "og:title": "About Us",
    "og:description": "..."
  },
  "canonical_url": "https://example.com/about",
  "language": "en",
  "headings": {
    "h1": ["About Us"],
    "h2": ["Our Mission", "Our Team"],
    "h3": []
  },
  "text_content": "We are a company dedicated to...",
  "word_count": 342,
  "internal_links": ["https://example.com/contact", "https://example.com/team"],
  "external_links": ["https://linkedin.com/company/example"],
  "images": [
    {
      "src": "https://example.com/images/team.jpg",
      "alt": "Our team",
      "width": 800,
      "height": 600
    }
  ],
  "schema_org": [
    {"@type": "Organization", "name": "Example"}
  ],
  "has_forms": false,
  "has_tables": false,
  "extracted_at": "2026-05-18T22:00:00Z"
}
```

### Índice: `extract_results.json`

```json
{
  "job_id": "uuid-v4",
  "total_pages": 10,
  "successful": 9,
  "failed": 1,
  "empty_pages": 2,
  "results": [
    {
      "url": "https://example.com/about",
      "url_hash": "a3f1c2...",
      "status": "success",
      "word_count": 342,
      "file": "pages/a3f1c2....json"
    },
    {
      "url": "https://example.com/broken",
      "url_hash": "b9d2e1...",
      "status": "failed",
      "word_count": null,
      "file": null
    }
  ]
}
```

`status`: `"success"` | `"failed"`
`empty_pages`: número de páginas con `word_count < 50` (para que el Auditor lo use)

---

## Extracción de campos (BeautifulSoup + readability-lxml)

### title
```python
soup.find("title").get_text(strip=True)
# fallback: primer <h1>
soup.find("h1").get_text(strip=True) if soup.find("h1") else ""
```

### meta_description
```python
tag = soup.find("meta", attrs={"name": "description"})
tag["content"] if tag else ""
```

### meta_keywords
```python
tag = soup.find("meta", attrs={"name": "keywords"})
[k.strip() for k in tag["content"].split(",")] if tag else []
```

### og_data
```python
{tag["property"]: tag.get("content", "") 
 for tag in soup.find_all("meta", property=True) 
 if tag["property"].startswith("og:")}
```

### canonical_url
```python
tag = soup.find("link", rel="canonical")
tag["href"] if tag else ""
```

### language
```python
soup.find("html").get("lang", "") if soup.find("html") else ""
```

### headings
```python
{
  "h1": [h.get_text(strip=True) for h in soup.find_all("h1")],
  "h2": [h.get_text(strip=True) for h in soup.find_all("h2")],
  "h3": [h.get_text(strip=True) for h in soup.find_all("h3")],
}
```

### text_content (readability-lxml)
```python
from readability import Document
doc = Document(html_content)
readable_html = doc.summary()
readable_soup = BeautifulSoup(readable_html, "lxml")
text = readable_soup.get_text(separator=" ", strip=True)
text_content = text[:10_000]  # máx 10.000 chars
```

### word_count
```python
len(text_content.split())
```

### internal_links / external_links
```python
from urllib.parse import urlparse, urljoin
base_host = urlparse(base_url).netloc.lower().lstrip("www.")
for tag in soup.find_all("a", href=True):
    abs_url = urljoin(page_url, tag["href"])
    host = urlparse(abs_url).netloc.lower().lstrip("www.")
    if host == base_host:
        internal_links.append(abs_url)
    elif urlparse(abs_url).scheme in ("http", "https"):
        external_links.append(abs_url)
# Deduplicar ambas listas
```

### images
```python
for img in soup.find_all("img"):
    src = img.get("src", "")
    if not src:
        continue
    abs_src = urljoin(page_url, src)
    images.append({
        "src": abs_src,
        "alt": img.get("alt", ""),
        "width": int(img.get("width")) if img.get("width", "").isdigit() else None,
        "height": int(img.get("height")) if img.get("height", "").isdigit() else None,
    })
```

### schema_org (JSON-LD)
```python
schemas = []
for tag in soup.find_all("script", type="application/ld+json"):
    try:
        data = json.loads(tag.string or "")
        if isinstance(data, list):
            schemas.extend(data)
        else:
            schemas.append(data)
    except (json.JSONDecodeError, TypeError):
        pass
```

### has_forms / has_tables
```python
has_forms = bool(soup.find("form"))
has_tables = bool(soup.find("table"))
```

---

## Limpieza inmediata (CRÍTICO)

Después de extraer cada página y escribir `pages/<hash>.json`:
```python
os.remove(raw_html_path)  # eliminar raw/<hash>.html inmediatamente
```

Al finalizar todas las páginas:
```python
try:
    raw_dir.rmdir()  # solo si está vacío
except OSError:
    pass  # ignorar si no está vacío (puede tener archivos de failed fetches)
```

---

## Actualización de estado

```python
# Al iniciar
await job_manager.update_status(job_id, JobStatus.extracting)

# Tras cada página
await job_manager.update_progress(job_id, pages_done=done, pages_total=total)

# Al terminar (éxito o EXTRACTION_EMPTY):
# NO cambiar estado — el Orchestrer lo hace al pasar a auditing
# Solo escribir extract_results.json y retornar

# Si EXTRACTION_EMPTY (> 50% con word_count < 50):
await job_manager.fail_job(job_id, "EXTRACTION_EMPTY",
    "El contenido extraído está vacío. El sitio puede requerir JavaScript para renderizar.")
# retornar False
```

---

## Signatura de la función principal

```python
async def run_extractor(job_id: str) -> bool:
    """
    Extracts PageData from raw HTML files and writes pages/<hash>.json.
    Deletes each raw HTML file immediately after extraction.
    Returns True on success, False if job was failed (EXTRACTION_EMPTY).
    Updates job status to 'extracting' at the start.
    """
```

---

## Manejo de errores por página

Si BeautifulSoup o readability falla en una página individual:
- Marcar esa página como `status: "failed"` en `extract_results.json`
- Continuar con las demás (no abortar el job completo)
- Eliminar igualmente el `.html` crudo
- El check de `EXTRACTION_EMPTY` se aplica sobre las páginas exitosas solamente

---

## Concurrencia

El Extractor procesa páginas **secuencialmente** (no en paralelo).
CPU-bound: BeautifulSoup + readability no se benefician de asyncio.
La función principal es `async` para compatibilidad con el pipeline, pero el loop interno es síncrono.
Usar `aiofiles` para escritura de `pages/<hash>.json`.

---

## Tests requeridos (`tests/test_f04_extractor.py`)

1. **test_extracts_all_fields** — HTML completo, verifica todos los campos de PageData
2. **test_title_fallback_to_h1** — sin `<title>`, usa primer `<h1>`
3. **test_text_content_readability** — `text_content` usa readability (no todo el HTML crudo)
4. **test_text_content_max_10000_chars** — texto > 10.000 chars se trunca
5. **test_internal_vs_external_links** — links internos vs externos clasificados correctamente
6. **test_images_resolved_to_absolute** — URLs relativas de imágenes se convierten a absolutas
7. **test_schema_org_parsed** — JSON-LD extraído como lista de dicts
8. **test_raw_html_deleted_immediately** — `raw/<hash>.html` eliminado tras extraer cada página
9. **test_raw_dir_removed_when_empty** — directorio `raw/` eliminado al finalizar si está vacío
10. **test_extraction_empty_fails_job** — > 50% con word_count < 50 → `EXTRACTION_EMPTY`
11. **test_partial_failure_continues** — error en 1 página no aborta las demás
12. **test_extract_results_json_format** — `extract_results.json` schema correcto

---

## Archivos a crear/modificar

- **Crear**: `app/pipeline/extractor.py`
- **Crear**: `tests/test_f04_extractor.py`
- **Posible**: agregar `JobStatus.extracting` a `app/models/job.py` si no existe
- **No tocar**: `main.py`, `router.py`, `fetcher.py`, `explorer.py`, `config.py`

---

## Notas de implementación

- `readability-lxml` ya está en `requirements.txt` (`readability-lxml 0.8.1`)
- `beautifulsoup4` + `lxml` ya están en `requirements.txt`
- `aiofiles` ya está en `requirements.txt`
- Verificar que `JobStatus.extracting` existe en `app/models/job.py`; si no, agregarlo
- El extractor es sync internamente — no usar `asyncio.gather()` aquí
- `extracted_at` = `datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")`
- Para tests: crear archivos `raw/<hash>.html` manualmente en el setup, no usar respx
