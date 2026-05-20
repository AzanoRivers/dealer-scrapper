# impl_f03.md — F03 Fetcher: HTML Download

## Estado: DONE

---

## Archivos creados / modificados

| Archivo | Acción |
|---|---|
| `app/pipeline/fetcher.py` | **Creado** — módulo principal del Fetcher |
| `tests/test_f03_fetcher.py` | **Creado** — 12 tests de checkpoints |
| `app/core/job_manager.py` | **Modificado** — agregado `update_progress()` |

---

## Resultado de tests

```
tests/test_f03_fetcher.py   — 12/12 ✅
tests/test_f02_explorer.py  — sin regresiones ✅
tests/test_f01_core_api.py  — sin regresiones ✅
```

---

## Checkpoints cubiertos

- [x] **Semáforo asyncio activo** — `asyncio.Semaphore(settings.MAX_CONCURRENT_FETCHES)` creado en `run_fetcher`, adquirido dentro de `_fetch_url` con `async with semaphore:`. El loop de reintentos completo ocurre bajo el semáforo.  
  → `test_semaphore_limits_concurrency` verifica con MAX=2, 5 URLs, `slow_handler` con `asyncio.sleep(0.05)` real.

- [x] **Backoff exponencial en reintentos (1s, 2s, 4s)** — `BACKOFF_DELAYS = [1.0, 2.0, 4.0]`. `await asyncio.sleep(BACKOFF_DELAYS[attempt])` con `attempt < retries`. Con FETCH_RETRIES=3 hace 4 intentos (0,1,2,3) con sleeps de 1s/2s/4s entre ellos.  
  → `test_retry_on_timeout` y `test_retry_backoff_exhausted`.

- [x] **HTML guardado en disco inmediatamente** — `aiofiles.open(file_path, "w")` dentro del path de éxito en `_fetch_url`, sin acumular en RAM.  
  → `test_html_written_to_disk` verifica que el contenido en disco coincide exactamente con el body HTTP.

- [x] **`fetch_results.json` con todas las URLs y sus estados** — Escrito al finalizar todas las descargas con campos: `job_id`, `total_urls`, `successful`, `failed`, `results[]`.  
  → `test_fetch_results_json_format` y `test_fetches_all_routes`.

- [x] **`FETCH_ALL_FAILED` si 0 páginas exitosas** — Si `successful == 0`, llama `job_manager.fail_job(job_id, "FETCH_ALL_FAILED", ...)` y retorna `False`.  
  → `test_fetch_all_failed`.

---

## Decisiones de implementación no obvias

### 1. `update_progress` agregado a `job_manager.py`
La feature spec indica que si no existe el método se puede agregar. El método preserva el `phase` actual del `progress` (si existe) para no perder el nombre de fase. El `percent` se calcula como `int(pages_done / pages_total * 100)`.

### 2. Semáforo incluye el loop de reintentos completo
El `async with semaphore:` envuelve TODO el loop de intentos, no solo la llamada HTTP individual. Esto garantiza que una URL "difícil" (con retries) no libera el semáforo entre intentos, lo que evita que se inicien otras URLs mientras una está en backoff. Este es el comportamiento correcto: MAX_CONCURRENT_FETCHES = máx URLs en proceso simultáneamente.

### 3. 404/410 no retriable, resto sí
Los códigos 404/410 retornan inmediatamente sin consumir reintentos. Los 403/429/5xx son retriables con backoff. El `last_http_code` en el resultado fallido se setea solo si el error fue `"http_error"` (preserva el código HTTP del server), para timeouts y connection_errors queda en `null`.

### 4. `asyncio.sleep` patcheado en tests con reintentos
Los tests con backoff (`test_retry_on_timeout`, `test_retry_backoff_exhausted`, etc.) patchean `asyncio.sleep` con `AsyncMock` para ejecutar instantáneamente. El test de semáforo usa `asyncio.sleep(0.05)` real para crear window de concurrencia testeable.

### 5. Closure `fetch_and_update` dentro de `async with httpx.AsyncClient`
La closure captura `client` del scope del `async with`. `asyncio.gather` se ejecuta dentro del mismo bloque, garantizando que el cliente está vivo para todas las tareas. No se pasa el cliente como parámetro a `_fetch_url` desde fuera del context manager.

### 6. Contador mutable `done_count: list[int] = [0]`
Patrón Python estándar para mutabilidad en closures asyncio. El incremento `done_count[0] += 1` es atómico desde la perspectiva asyncio (no hay `await` entre read y write), garantizando que cada llamada a `update_progress` recibe un valor único de `pages_done` (1, 2, 3, ..., N).
