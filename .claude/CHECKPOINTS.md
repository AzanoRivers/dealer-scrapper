# CHECKPOINTS — DealerScrapper

Criterios de estado final correcto por feature.
El Reviewer valida contra esta lista antes de emitir APPROVED.
El Implementer usa esta lista para saber cuándo está "done".

---

## F01 — Core API y Job Management

- [ ] `POST /api/v1/scrape` crea job con UUID v4, devuelve `{ job_id, status: "queued" }`
- [ ] `GET /scrape/{job_id}/status` devuelve schema correcto en todos los estados incluyendo `ttl_remaining_seconds`
- [ ] `ttl_remaining_seconds` decrementa correctamente desde 900 cuando `status == done | failed`
- [ ] `GET /scrape/{job_id}/result` devuelve 404 con `job_not_found_or_expired` tras el TTL
- [ ] `GET /scrape/{job_id}/images` devuelve 404 si `DOWNLOAD_IMAGES=false`
- [ ] `DELETE /scrape/{job_id}` elimina el directorio y cancela todas las Tasks asyncio del job
- [ ] `startup_cleanup()` corre antes de aceptar requests y elimina huérfanos correctamente
- [ ] `X-API-Key` requerida en todos los endpoints `/api/v1/` → 401 sin key
- [ ] `GET /guide-ai` devuelve JSON válido con todos los endpoints y error_codes
- [ ] `GET /api/v1/status` devuelve jobs activos y capacidad del servidor

---

## F02 — Explorer: Route Discovery

- [ ] Detecta y parsea `sitemap.xml` (incluye sitemaps anidados, profundidad máx 2, sin bucles)
- [ ] Fallback a homepage links si no hay sitemap
- [ ] Filtros activos: assets (`.css`, `.js`, imágenes, fonts), tracking params, rutas admin
- [ ] Respeta `MAX_PAGES_PER_JOB`
- [ ] `routes.json` en formato correcto con `discovery_method` y `source` por URL
- [ ] `NO_ROUTES_FOUND` falla el job inmediatamente

---

## F03 — Fetcher: HTML Download

- [ ] Semáforo asyncio activo (máx `MAX_CONCURRENT_FETCHES` simultáneos)
- [ ] Backoff exponencial en reintentos (1s, 2s, 4s)
- [ ] HTML guardado en disco inmediatamente (nunca acumulado en RAM)
- [ ] `fetch_results.json` con todas las URLs y sus estados
- [ ] `FETCH_ALL_FAILED` si 0 páginas exitosas

---

## F04 — Extractor: PageData

- [ ] Extrae todos los campos de `PageData` definidos en el plan (Parte 7.3)
- [ ] `text_content` usa `readability-lxml`
- [ ] URLs de imágenes resueltas a absolutas
- [ ] Elimina `raw/<hash>.html` inmediatamente tras extraer cada página
- [ ] Elimina directorio `raw/` al terminar
- [ ] `EXTRACTION_EMPTY` si > 50% con `word_count < 50`

---

## F05 — Auditor: Coverage

- [ ] Calcula `coverage_percent` correctamente (pages/ vs routes.json)
- [ ] `critical: true` cuando `coverage_percent < AUDIT_COVERAGE_MIN_PERCENT`
- [ ] Detecta `internal_links` no crawleados y los agrega (respeta `AUDIT_MAX_NEW_ROUTES`)
- [ ] `second_pass=true` en segunda ejecución (no bucle infinito)
- [ ] `audit_report.json` en formato correcto

---

## F06 — Guards: Timeout y Cleanup

- [ ] Guard 1 (global timeout): falla el job con `JOB_TIMEOUT` si supera 30 min desde `started_at`
- [ ] Guard 2 (LLM watchdog): falla con `LLM_TIMEOUT` si no hay `event.set()` en 5 min
- [ ] Guard 2 solo se lanza al entrar en fase `analyzing`
- [ ] Guard 3 (TTL): lanzado exactamente cuando `status` cambia a `done | failed`
- [ ] Guard 3: elimina `rm -rf job_dir` tras 15 min exactos
- [ ] Reviewer detecta `state.status == "failed"` y aborta sin error adicional
- [ ] `DELETE /{job_id}` cancela las 3 Tasks asyncio activas del job

---

## F07 — Reviewer: LLM Analysis

- [ ] `activity_event.set()` en mínimo 5 puntos por batch
- [ ] Verifica `state.status == "failed"` antes de cada llamada al LLM
- [ ] 4 providers funcionan: `openai`, `anthropic`, `deepseek`, `minimax`
- [ ] `LLM_AUTH_ERROR` falla inmediatamente (sin reintentos)
- [ ] 429 espera `retry-after` y reintenta 1 vez
- [ ] JSON malformado reintenta 1 vez con prompt estricto
- [ ] Elimina `chunk_summaries/` al completar
- [ ] `result.json` cumple el schema completo (Parte 7.5 del plan)

---

## F08 — Packager: Output

- [ ] Descarga imágenes solo si `DOWNLOAD_IMAGES=true`
- [ ] Verifica `Content-Type` y tamaño de cada imagen descargada
- [ ] Actualiza `local_path` en `result.json` por cada imagen descargada
- [ ] Elimina `pages/`, `routes.json`, `fetch_results.json`, `extract_results.json`, `audit_report.json`
- [ ] `result.zip` contiene `result.json` + `images/` (si aplica)
- [ ] `state.json` → `status: "done"`, `done_at: <timestamp>`
- [ ] `asyncio.create_task(schedule_cleanup(job_id))` lanzado DESPUÉS de escribir `state.json`

---

## F09 — Endpoints de Imágenes

- [ ] `GET /images` devuelve listado con `download_url`, `size_bytes`, `original_url`, `alt`
- [ ] `GET /images` incluye `ttl_remaining_seconds` actualizado
- [ ] `GET /images/{filename}` sirve la imagen con el `Content-Type` correcto
- [ ] `GET /images/{filename}` devuelve 404 si el archivo no existe
- [ ] Ambos endpoints devuelven 404 con `job_not_found_or_expired` si el directorio fue eliminado
- [ ] `GET /download` descarga el ZIP completo correctamente

---

## F10 — Nginx, Systemd, Scripts

- [ ] `dealerscrapper.conf` no declara `default_server` (ya está en `optimus.conf`)
- [ ] `dealerscrapper.conf` incluye `/etc/nginx/cloudflare-ips.conf`
- [ ] `dealerscrapper.conf` usa `zone=scraper` (no conflicta con `zone=api` de optimus)
- [ ] `dealerscrapper.service` usa `User=opc`
- [ ] `dealerscrapper.service` usa `WorkingDirectory=/home/opc/projects/dealerscrapper`
- [ ] `scripts/linux/setup.sh` crea venv, instala deps, configura systemd y nginx
- [ ] `scripts/linux/deploy.sh` hace git pull, pip install, systemctl restart, verifica puerto
- [ ] `nginx -t` pasa sin errores con ambos `.conf` activos
- [ ] `systemctl status dealerscrapper` → active (running)
- [ ] `systemctl status optimus-api` → sigue corriendo (sin regresión)

---

## Checklist de Infraestructura (verificación manual del desarrollador)

```
- [ ] systemctl status dealerscrapper → active (running)
- [ ] ss -tlnp | grep 8002 → puerto escuchando
- [ ] nginx -t → sin errores
- [ ] curl https://scraper.azanolabs.com/ → JSON de status
- [ ] curl https://optimus.azanolabs.com/ → sigue respondiendo (sin regresión)
- [ ] Memoria total VPS < 70% con ambas APIs idle
- [ ] Job de prueba end-to-end: POST → poll → done → GET /result → GET /images → GET /download
- [ ] Verificar que tras 15 min devuelve 404
```
