# F03 — Fetcher: HTML Download

## Objetivo

Implementar `app/pipeline/fetcher.py` — el subagente Fetcher.
Dado el `job_id`, lee `routes.json` y descarga el HTML de cada URL a disco inmediatamente.
Escribe `/tmp/dealerscrapper/<job_id>/raw/<url_hash>.html` por cada URL exitosa
y el índice `/tmp/dealerscrapper/<job_id>/fetch_results.json` al finalizar.

---

## Contrato de entrada

El Fetcher recibe:
- `job_id: str` — para leer `routes.json` (vía `settings.JOB_BASE_DIR`) y escribir resultados

Lee la lista de URLs desde `routes.json` que el Explorer ya escribió.

---

## Contrato de salida

### Archivos HTML individuales

Directorio: `/tmp/dealerscrapper/<job_id>/raw/`
Nombre: `<url_hash>.html` donde `url_hash = hashlib.sha256(url.encode()).hexdigest()`

### Índice: `fetch_results.json`

```json
{
  "job_id": "uuid-v4",
  "total_urls": 12,
  "successful": 10,
  "failed": 2,
  "results": [
    {
      "url": "https://example.com/about",
      "url_hash": "a3f1c2...",
      "status": "success",
      "http_code": 200,
      "file": "raw/a3f1c2....html",
      "error": null
    },
    {
      "url": "https://example.com/broken",
      "url_hash": "b9d2e1...",
      "status": "failed",
      "http_code": null,
      "file": null,
      "error": "timeout"
    }
  ]
}
```

`status` puede ser: `"success"` | `"failed"`
`error` puede ser: `"timeout"` | `"http_error"` | `"connection_error"` | `null`

---

## Comportamiento

- **Semáforo asyncio**: máx `settings.MAX_CONCURRENT_FETCHES` (default: 3) en paralelo
- **Timeout**: `settings.FETCH_TIMEOUT_SECONDS` (default: 15s) por request
- **Reintentos**: `settings.FETCH_RETRIES` (default: 3) con backoff exponencial: 1s, 2s, 4s
- **HTML a disco inmediatamente**: nunca acumular en RAM — `aiofiles.open()` para cada archivo
- **Redirects**: seguir hasta 3 redirects, solo dentro del dominio base
- **Progreso**: actualizar `job_manager.update_progress(job_id, pages_done=N, pages_total=T)` tras cada URL

---

## Actualización de estado del job

```python
# Al iniciar
await job_manager.update_status(job_id, JobStatus.fetching)

# Tras cada URL (éxito o fallo)
await job_manager.update_progress(job_id, pages_done=done, pages_total=total)

# Al terminar con éxito (al menos 1 HTML descargado)
# NO cambiar estado aquí — el Orchestrer lo hace al pasar a extracting
# Solo escribir fetch_results.json y retornar True

# Si FETCH_ALL_FAILED:
await job_manager.fail_job(job_id, "FETCH_ALL_FAILED",
    "No se pudo descargar ninguna página. El sitio puede estar bloqueando scrapers.")
```

---

## Signatura de la función principal

```python
async def run_fetcher(job_id: str) -> bool:
    """
    Downloads HTML for all routes in routes.json and writes to raw/<hash>.html.
    Returns True on success (>=1 page downloaded), False if job was failed (FETCH_ALL_FAILED).
    Updates job status to 'fetching' at the start.
    """
```

---

## httpx — configuración del cliente

```python
headers = {
    "User-Agent": "Mozilla/5.0 (compatible; DealerScrapper/1.0; +https://azanolabs.com)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}
timeout = httpx.Timeout(settings.FETCH_TIMEOUT_SECONDS)
follow_redirects = True
max_redirects = 3
```

---

## URL hash

```python
import hashlib
url_hash = hashlib.sha256(url.encode()).hexdigest()
```

---

## Lógica de reintentos con backoff

```python
import asyncio

BACKOFF_DELAYS = [1.0, 2.0, 4.0]  # segundos entre reintentos

async def _fetch_with_retry(client, url, semaphore, retries):
    async with semaphore:
        last_error = None
        for attempt in range(retries + 1):
            try:
                resp = await client.get(url)
                if resp.status_code == 200:
                    return resp
                # Non-200 no es retriable — salir con http_error
                return None  # marcar como http_error
            except httpx.TimeoutException:
                last_error = "timeout"
            except httpx.RequestError:
                last_error = "connection_error"
            if attempt < retries:
                await asyncio.sleep(BACKOFF_DELAYS[attempt])
        return None  # agotados los reintentos
```

---

## Manejo de errores HTTP

- `200`: éxito — escribir HTML a disco
- `404`, `410`: página no existe — marcar como `"failed"`, `error: "http_error"`, sin reintento
- `403`, `429`, `5xx`: reintentable con backoff
- Timeout: `error: "timeout"` con reintentos
- Error de conexión: `error: "connection_error"` con reintentos

---

## Tests requeridos (`tests/test_f03_fetcher.py`)

1. **test_fetches_all_routes** — todas las URLs 200, verifica archivos HTML en `raw/` y `fetch_results.json`
2. **test_semaphore_limits_concurrency** — verifica que `MAX_CONCURRENT_FETCHES` se respeta (mock que cuenta llamadas simultáneas)
3. **test_retry_on_timeout** — primer intento timeout, segundo 200 → éxito, verifica reintento
4. **test_retry_backoff_exhausted** — 3 timeouts → `status: "failed"`, `error: "timeout"`
5. **test_404_no_retry** — 404 no se reintenta → `status: "failed"`, `error: "http_error"`
6. **test_html_written_to_disk** — HTML del response está en `raw/<hash>.html` (no en RAM)
7. **test_fetch_results_json_format** — `fetch_results.json` tiene todos los campos correctos
8. **test_fetch_all_failed** — todas las URLs fallan → job en `failed` con `FETCH_ALL_FAILED`
9. **test_partial_success** — 1 de 3 URLs falla → `successful=2`, `failed=1`, retorna `True`
10. **test_progress_updated** — `update_progress` llamado tras cada URL (mock de job_manager)
11. **test_url_hash_correct** — hash en `file` y en `url_hash` es `sha256(url.encode()).hexdigest()`
12. **test_status_set_to_fetching** — status cambia a `fetching` al inicio

Usar `respx` para mockear httpx. Crear `raw/` dir en el job_dir antes de correr el fetcher.

---

## Archivos a crear/modificar

- **Crear**: `app/pipeline/fetcher.py`
- **Crear**: `tests/test_f03_fetcher.py`
- **No tocar**: nada más. No modificar `main.py`, `router.py`, `job_manager.py`, `explorer.py`.

---

## Notas de implementación

- `raw/` directory debe crearse en `run_fetcher` antes de iniciar las descargas.
- Usar `asyncio.gather()` con tareas que cada una adquiere el semáforo internamente.
- `fetch_results.json` se escribe **al final**, después de procesar todas las URLs (no es incremental).
- El error `"http_error"` cubre cualquier código HTTP no-200.
- No modificar `job_manager.py` — si `update_progress` no existe aún, implementarlo en `job_manager.py` solo si falta.
- `respx` ya está en `requirements-dev.txt` (añadido en F02).
