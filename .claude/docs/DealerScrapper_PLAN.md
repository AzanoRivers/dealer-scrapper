# DealerScrapper — Plan Maestro v3.0
> Documento optimizado para Claude Code / agentes IA  
> VPS: Oracle Cloud Free Tier — Ampere A1 (1 OCPU / 6 GB RAM / ~46 GB SSD / Oracle Linux ARM64)  
> Coexiste con: OptimusApi (FastAPI, puerto :8001 → DealerScrapper usa :8002)

---

## ÍNDICE DE NAVEGACIÓN PARA AGENTES

```
AGENTS.md (este archivo)   → Leer siempre primero. Mapa completo del proyecto.
specs/                     → Specs por fase (requirements + design + tasks)
progress/
  current.md               → Estado activo de la sesión (sobrescribible)
  history.md               → Bitácora append-only
.claude/agents/
  orchestrer.md            → Orquestador: planifica y coordina. NO escribe código.
  implementer.md           → Prompt base del implementador de código.
  reviewer.md              → Revisor: valida y reporta. NO edita código.
feature_list.json          → Alcance controlado: una feature a la vez.
CHECKPOINTS.md             → Criterios de estado final correcto.
```

---

## PARTE 1 — ANÁLISIS DE VIABILIDAD Y RECURSOS

### 1.1 Veredicto: viable sin Playwright

El scraping con Playwright consume 200–400 MB por instancia de Chromium. En este VPS
con OptimusApi ya corriendo, eso es innecesario y riesgoso. El enfoque basado en
`httpx` + parseo HTML cubre el 85–90% de los sitios modernos (los que tienen SSR,
sitemap, o HTML semántico). Para el 10–15% restante (SPAs puras sin SSR) se documenta
un estado de error específico que el CMS puede manejar.

### 1.2 Presupuesto de recursos

| Recurso | Total VPS | OptimusApi (idle) | OptimusApi (pico FFmpeg) | DealerScrapper (operación) | Margen |
|---|---|---|---|---|---|
| RAM | 6 GB | ~350 MB | ~900 MB | ~200–350 MB | ~4.5 GB |
| CPU | ~2 vCPU equiv. | bajo | alto (FFmpeg) | bajo (I/O-bound) | suficiente |
| Disco | ~46 GB | ~2–5 GB | — | ~500 MB /tmp por job | ~40 GB |
| Puerto | — | :8001 → nginx | — | :8002 → nginx | sin conflicto |

### 1.3 Coexistencia con OptimusApi

- OptimusApi: `/etc/nginx/conf.d/optimus.conf` → `api.azanolabs.com` → `:8001`
- DealerScrapper: `/etc/nginx/conf.d/dealerscrapper.conf` → `scraper.azanolabs.com` → `:8002`
- Certificado SSL `*.azanolabs.com` ya instalado en `/etc/nginx/ssl/` → reutilizable sin cambios.
- Los dos archivos `.conf` coexisten en `conf.d/` sin interferencia.

---

## PARTE 2 — CICLO DE VIDA COMPLETO DE UN JOB Y SUS ARCHIVOS

Esta sección es crítica. Define exactamente cuándo se crea, cuándo se elimina
y qué queda en disco en cada momento. El servidor no debe acumular basura.

### 2.1 Diagrama de ciclo de vida

```
POST /api/v1/scrape
        │
        ▼
  /tmp/dealerscrapper/<job_id>/   ← directorio creado
        │  state.json             ← status: "queued"
        │
        ▼ [pipeline corre...]
        │
        ├── raw/          ← HTMLs crudos (temporales)
        ├── pages/        ← PageData JSON por página (temporales)
        ├── routes.json
        ├── fetch_results.json
        ├── extract_results.json
        ├── audit_report.json
        ├── chunk_summaries/      ← chunks del LLM (temporales)
        ├── result.json           ← output final
        └── images/               ← solo si DOWNLOAD_IMAGES=true

        ▼ [Packager completa]

  status: "done"
  done_at: <timestamp>   ← TTL de 15 minutos empieza AQUÍ

        ▼ [CMS consume result / imágenes / ZIP]

        ▼ [15 minutos después de done_at]

  CLEANUP COMPLETO: rm -rf /tmp/dealerscrapper/<job_id>/
  state.json eliminado → cualquier request al job devuelve 404
```

### 2.2 Limpieza por fases (durante el pipeline)

No se espera al final para limpiar. Cada subagente limpia lo suyo al terminar:

| Momento | Qué se elimina | Qué se conserva |
|---|---|---|
| Extractor termina | `raw/*.html` (HTML crudos) | `pages/*.json`, `routes.json`, `fetch_results.json` |
| Reviewer termina | `chunk_summaries/*.json` | `pages/*.json`, `audit_report.json`, `result.json` |
| Packager termina | `pages/*.json`, `extract_results.json`, `fetch_results.json`, `routes.json`, `audit_report.json` | `result.json`, `images/`, `result.zip`, `state.json` |
| TTL 15min post-done | **TODO** `rm -rf /tmp/dealerscrapper/<job_id>/` | Nada |
| Job falla | Archivos de procesamiento en curso | `state.json` (con error), `pages/*.json` opcionales para diagnóstico |
| TTL 15min post-failed | **TODO** `rm -rf /tmp/dealerscrapper/<job_id>/` | Nada |

> **Regla general**: después de `done` o `failed`, el único responsable de que
> quede algo en disco es el TTL de 15 minutos. Pasado ese tiempo, el directorio
> desaparece completamente.

### 2.3 Limpieza al arrancar el servidor

Cuando FastAPI arranca, antes de aceptar requests:

```python
async def startup_cleanup():
    """
    Elimina directorios de jobs huérfanos (servidor cayó durante un job).
    Criterios de eliminación al arrancar:
    - Jobs en estado "done" o "failed" con done_at/failed_at > 0 minutos (ya terminaron)
    - Jobs en cualquier estado con created_at > JOB_MAX_DURATION_SECONDS (timeout global)
    - Jobs sin state.json válido (corruptos)
    """
```

### 2.4 Definición precisa de los dos timeouts

Es importante distinguirlos porque tienen propósitos completamente distintos:

```
┌─────────────────────────────────────────────────────────────────────┐
│  WATCHDOG DE INACTIVIDAD DEL LLM (LLM_WATCHDOG_SECONDS = 300)      │
│                                                                     │
│  Mide: tiempo desde la última señal de actividad del Reviewer.     │
│  "Actividad" = cualquier event.set() (respuesta del modelo,        │
│  chunk guardado, inicio de batch, etc.)                            │
│                                                                     │
│  Dispara cuando: el modelo deja de responder completamente          │
│  por 5 minutos consecutivos.                                        │
│                                                                     │
│  Error: LLM_TIMEOUT                                                 │
│  Aplica: SOLO durante la fase "analyzing" (Reviewer activo)         │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│  TIMEOUT GLOBAL DEL JOB (JOB_MAX_DURATION_SECONDS = 1800)          │
│                                                                     │
│  Mide: tiempo total desde que el job pasó a estado "exploring"     │
│  (desde que empezó a ejecutarse, no desde que fue creado).         │
│                                                                     │
│  Dispara cuando: el job lleva más de 30 minutos ejecutándose,      │
│  independientemente de si hay actividad o no.                       │
│                                                                     │
│  Ejemplo: un sitio con 10.000 URLs en el sitemap podría colgar     │
│  el Explorer indefinidamente. Este timeout lo mata.                 │
│                                                                     │
│  Error: JOB_TIMEOUT                                                 │
│  Aplica: a TODAS las fases del pipeline                             │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│  TTL POST-COMPLETION (RESULT_TTL_MINUTES = 15)                      │
│                                                                     │
│  Mide: tiempo desde que el job llegó a estado "done" o "failed".   │
│                                                                     │
│  Dispara cuando: han pasado 15 minutos desde done_at/failed_at.    │
│                                                                     │
│  Acción: rm -rf /tmp/dealerscrapper/<job_id>/                      │
│  Respuesta API tras TTL: 404 con mensaje "job expirado"            │
│                                                                     │
│  Aplica: a TODOS los jobs completados (done o failed)               │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.5 Mecanismo del TTL de 15 minutos

El TTL no corre en un loop de polling. Usa una `asyncio.Task` programada con `asyncio.sleep`:

```python
async def schedule_job_cleanup(job_id: str, delay_seconds: int):
    """
    Se lanza como asyncio.Task cuando el job llega a done o failed.
    Duerme RESULT_TTL_MINUTES * 60 segundos y luego elimina el directorio.
    Si el servidor cae y reinicia antes del TTL, startup_cleanup() lo maneja.
    """
    await asyncio.sleep(delay_seconds)
    job_dir = Path(settings.JOB_BASE_DIR) / job_id
    if job_dir.exists():
        shutil.rmtree(job_dir, ignore_errors=True)
```

Esta Task se lanza en el momento exacto en que `job_manager.complete_job()` o
`job_manager.fail_job()` actualizan el `state.json`. No hay cronjob externo ni
loop de limpieza corriendo todo el tiempo.

---

## PARTE 3 — ARQUITECTURA DEL SISTEMA

### 3.1 Stack técnico

```
Python 3.9 + FastAPI + Gunicorn (1 worker, timeout 600s) + asyncio
Puerto VPS  : 8002
Subdomain   : scraper.azanolabs.com
LLM Provider: configurable via .env (openai | anthropic | deepseek | minimax)
LLM Client  : httpx async (nativo Python, sin Vercel AI SDK)
Persistencia: /tmp/dealerscrapper/<job_id>/ (JSON en disco, nunca en RAM acumulada)
Watchdog LLM: asyncio.Task por job — mata el proceso si el modelo no responde en 5 min
Timeout Global: asyncio.Task por job — mata cualquier fase tras 30 min de ejecución
TTL Cleanup : asyncio.Task por job — elimina TODO 15 min después de done/failed
```

### 3.2 Pipeline de subagentes

```
┌─────────────────────────────────────────────────────────────────┐
│                         ORCHESTRER                              │
│  Coordina el pipeline. No escribe código ni procesa datos.      │
└──────────────────────────┬──────────────────────────────────────┘
                           │ delega en secuencia
           ┌───────────────▼───────────────────┐
           │           EXPLORER                │
           │  Descubre rutas via robots.txt    │
           │  sitemap, links, fallbacks        │
           └───────────────┬───────────────────┘
                           │ → routes.json
           ┌───────────────▼───────────────────┐
           │           FETCHER                 │
           │  Descarga HTML de cada ruta       │
           │  semáforo asyncio (máx 3)         │
           └───────────────┬───────────────────┘
                           │ → raw/*.html  [se elimina tras Extractor]
           ┌───────────────▼───────────────────┐
           │          EXTRACTOR                │
           │  Parsea HTML → PageData JSON      │
           │  Limpia raw/*.html al terminar    │
           └───────────────┬───────────────────┘
                           │ → pages/*.json
           ┌───────────────▼───────────────────┐
           │           AUDITOR                 │
           │  Verifica cobertura y coherencia  │
           │  Puede pedir re-fetch parcial     │
           │  Segunda capa de revisión         │
           └───────────────┬───────────────────┘
                           │ → audit_report.json
           ┌───────────────▼───────────────────┐
           │       REVIEWER (LLM)              │
           │  Batches de 5 páginas → LLM       │
           │  Watchdog 5min activo             │
           │  Limpia chunk_summaries/ al fin   │
           └───────────────┬───────────────────┘
                           │ → result.json
           ┌───────────────▼───────────────────┐
           │          PACKAGER                 │
           │  Descarga imágenes (si aplica)    │
           │  Limpia pages/ y archivos temp    │
           │  Lanza TTL Task de 15 minutos     │
           └───────────────┬───────────────────┘
                           │ → status: "done"
                           │    done_at: <timestamp>
                           │    TTL Task: -15min → rm -rf job_dir
                           ▼
           [CMS consume en ≤ 15 minutos]
```

---

## PARTE 4 — MODELO DE ESTADOS (contrato CMS polling)

### 4.1 Estados del job

```
queued       → job creado, en cola, aún no inició
exploring    → Explorer descubriendo rutas
fetching     → Fetcher descargando HTML (progreso numérico disponible)
extracting   → Extractor parseando HTML a PageData
auditing     → Auditor verificando cobertura
analyzing    → Reviewer + LLM construyendo estructura (Watchdog activo)
packaging    → Packager empaquetando resultado final
done         → completado con éxito (TTL de 15 min corriendo)
failed       → error terminal (TTL de 15 min corriendo)
expired      → el job existió pero el TTL venció y fue eliminado
```

> `expired` es el estado que devuelve la API cuando el job_id es válido (UUID v4 bien formado)
> pero el directorio ya fue eliminado. Distingue "job que existió y expiró" de "job_id inválido".

### 4.2 Respuesta 404 por job expirado o inexistente

```json
{
  "error": "job_not_found",
  "detail": "El job no existe o ha expirado. Los resultados se eliminan 15 minutos después de completarse.",
  "job_id": "uuid-solicitado"
}
```

### 4.3 Códigos de error (error_code en estado "failed")

| Código | Causa | retry_after | Acción recomendada CMS |
|---|---|---|---|
| `NO_ROUTES_FOUND` | Sitio JS-only sin SSR o bloqueó el crawler | — | Notificar al usuario |
| `FETCH_ALL_FAILED` | Todas las páginas fallaron (4xx/5xx/timeout) | 300 | Reintentar más tarde |
| `EXTRACTION_EMPTY` | HTML descargado sin contenido útil (JS-rendered) | — | Notificar: sitio requiere JS |
| `AUDIT_CRITICAL_GAPS` | Cobertura < umbral mínimo tras re-fetch | — | Resultado parcial si aplica |
| `LLM_TIMEOUT` | Modelo inactivo > 5 minutos | 300 | Reintentar; verificar créditos |
| `LLM_AUTH_ERROR` | API key inválida o sin créditos | — | No reintentar; verificar config |
| `LLM_PARSE_ERROR` | JSON malformado tras 2 reintentos | 60 | Puede reintentar |
| `JOB_TIMEOUT` | Job superó 30 minutos de ejecución total | 600 | Reintentar con max_pages menor |
| `INTERNAL_ERROR` | Error inesperado del servidor | 60 | Reportar al administrador |

### 4.4 Schema del endpoint de status

```json
{
  "job_id": "uuid-v4",
  "status": "fetching",
  "progress": {
    "phase": "fetching",
    "pages_done": 12,
    "pages_total": 45,
    "percent": 26
  },
  "ttl_remaining_seconds": null,
  "error": null,
  "created_at": "2026-05-18T10:00:00Z",
  "started_at": "2026-05-18T10:00:05Z",
  "updated_at": "2026-05-18T10:02:30Z",
  "done_at": null,
  "estimated_remaining_seconds": 120
}
```

Cuando `status == "done"`:

```json
{
  "job_id": "uuid-v4",
  "status": "done",
  "progress": { "phase": "done", "pages_done": 45, "pages_total": 45, "percent": 100 },
  "ttl_remaining_seconds": 847,
  "error": null,
  "created_at": "2026-05-18T10:00:00Z",
  "started_at": "2026-05-18T10:00:05Z",
  "updated_at": "2026-05-18T10:10:00Z",
  "done_at": "2026-05-18T10:10:00Z",
  "estimated_remaining_seconds": 0
}
```

> `ttl_remaining_seconds` empieza a decrementar desde 900 (15 min) en cuanto el job
> llega a `done` o `failed`. El CMS puede usarlo para mostrar una cuenta regresiva
> o para priorizar la descarga antes de que expire.

Cuando `status == "failed"`:

```json
{
  "job_id": "uuid-v4",
  "status": "failed",
  "progress": { "phase": "analyzing", "pages_done": 45, "pages_total": 45, "percent": 100 },
  "ttl_remaining_seconds": 782,
  "error": {
    "code": "LLM_TIMEOUT",
    "message": "El modelo LLM no generó actividad en 5 minutos. Proceso terminado.",
    "failed_at": "2026-05-18T10:15:00Z",
    "retry_after": 300
  },
  "created_at": "2026-05-18T10:00:00Z",
  "started_at": "2026-05-18T10:00:05Z",
  "updated_at": "2026-05-18T10:15:00Z",
  "done_at": null
}
```

---

## PARTE 5 — ENDPOINTS COMPLETOS DE LA API

| Método | Ruta | Auth | Descripción |
|---|---|---|---|
| `GET` | `/` | No | Nombre, versión, estado del servidor |
| `GET` | `/guide-ai` | No | Referencia JSON para agentes IA |
| `GET` | `/api/v1/status` | Sí | Jobs activos, capacidad, estado del servidor |
| `POST` | `/api/v1/scrape` | Sí | Iniciar un job de scraping |
| `GET` | `/api/v1/scrape/{job_id}/status` | Sí | Estado + progreso + TTL (polling del CMS) |
| `GET` | `/api/v1/scrape/{job_id}/result` | Sí | result.json completo (solo si done, antes del TTL) |
| `GET` | `/api/v1/scrape/{job_id}/images` | Sí | Lista de imágenes descargadas + URLs de acceso |
| `GET` | `/api/v1/scrape/{job_id}/images/{filename}` | Sí | Descarga de una imagen individual |
| `GET` | `/api/v1/scrape/{job_id}/download` | Sí | ZIP completo: result.json + todas las imágenes |
| `DELETE` | `/api/v1/scrape/{job_id}` | Sí | Cancela y elimina el job inmediatamente (cualquier estado) |

### Endpoint GET /api/v1/scrape/{job_id}/images

Devuelve el listado de imágenes disponibles para descarga individual.
Solo disponible si `DOWNLOAD_IMAGES=true` y el job está en estado `done`.

```json
{
  "job_id": "uuid-v4",
  "total_images": 8,
  "ttl_remaining_seconds": 720,
  "images": [
    {
      "filename": "img_001.webp",
      "original_url": "https://example.com/img/hero.jpg",
      "alt": "Hero image",
      "size_bytes": 45320,
      "download_url": "/api/v1/scrape/uuid-v4/images/img_001.webp"
    },
    {
      "filename": "img_002.png",
      "original_url": "https://example.com/img/logo.png",
      "alt": "Company logo",
      "size_bytes": 12480,
      "download_url": "/api/v1/scrape/uuid-v4/images/img_002.png"
    }
  ]
}
```

### Endpoint GET /api/v1/scrape/{job_id}/images/{filename}

Sirve una imagen individual como `StreamingResponse` con el `Content-Type` correcto.
Si el archivo no existe (TTL vencido o imagen no descargada) → 404.

```python
@router.get("/scrape/{job_id}/images/{filename}")
async def get_image(job_id: str, filename: str, api_key: str = Depends(verify_api_key)):
    job_dir = Path(settings.JOB_BASE_DIR) / job_id
    image_path = job_dir / "images" / filename

    if not job_dir.exists():
        raise HTTPException(404, detail="job_not_found_or_expired")
    if not image_path.exists():
        raise HTTPException(404, detail="image_not_found")

    # Determinar Content-Type por extensión
    media_type = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png", ".webp": "image/webp",
        ".gif": "image/gif", ".svg": "image/svg+xml"
    }.get(image_path.suffix.lower(), "application/octet-stream")

    return FileResponse(image_path, media_type=media_type)
```

### Body del POST /api/v1/scrape

```json
{
  "url": "https://example.com",
  "options": {
    "max_pages": 50,
    "download_images": false,
    "llm_provider": null,
    "llm_model": null
  }
}
```

> `llm_provider` y `llm_model` en el body tienen precedencia sobre el `.env`.
> Permiten al CMS cambiar el modelo por job sin reiniciar el servidor.

---

## PARTE 6 — WATCHDOG E INACTIVIDAD

### 6.1 Tres guardias distintos (resumen definitivo)

```python
# Al arrancar el pipeline de un job, se lanzan 3 Tasks en paralelo:

asyncio.create_task(global_job_timeout(job_id))   # Guard 1: 30 min absolutos
# Guard 2 (LLM watchdog) se lanza solo cuando el job entra en "analyzing"
# Guard 3 (TTL cleanup) se lanza solo cuando el job llega a done/failed
```

### 6.2 Guard 1 — Timeout global (todas las fases)

```python
async def global_job_timeout(job_id: str):
    """
    Corre desde que el job empieza a ejecutarse (status: exploring).
    Si el job sigue en curso tras JOB_MAX_DURATION_SECONDS → falla con JOB_TIMEOUT.
    No mide inactividad. Mide tiempo total de ejecución.
    """
    await asyncio.sleep(settings.JOB_MAX_DURATION_SECONDS)
    state = await job_manager.get_state(job_id)
    if state and state.status not in ("done", "failed"):
        await job_manager.fail_job(job_id, "JOB_TIMEOUT",
            "El job superó el tiempo máximo de ejecución (30 minutos).")
```

### 6.3 Guard 2 — Watchdog de inactividad del LLM (solo fase analyzing)

```python
async def llm_watchdog(job_id: str, activity_event: asyncio.Event):
    """
    Corre SOLO durante la fase "analyzing" (Reviewer activo con el LLM).
    Mide tiempo desde la última señal de actividad del modelo.
    Si no hay event.set() en LLM_WATCHDOG_SECONDS → falla con LLM_TIMEOUT.
    """
    while True:
        activity_event.clear()
        try:
            await asyncio.wait_for(
                activity_event.wait(),
                timeout=settings.LLM_WATCHDOG_SECONDS
            )
            # Hubo actividad → resetear y seguir vigilando
            state = await job_manager.get_state(job_id)
            if state.status in ("done", "failed"):
                return  # El job terminó, el watchdog ya no tiene trabajo
        except asyncio.TimeoutError:
            await job_manager.fail_job(job_id, "LLM_TIMEOUT",
                "El modelo LLM no generó actividad en 5 minutos. Proceso terminado.",
                retry_after=300)
            return
```

El Reviewer hace `activity_event.set()` en estos momentos mínimos:
- Al iniciar el procesamiento de cada batch
- Al recibir la primera respuesta del LLM (aunque sea parcial)
- Al guardar cada `chunk_summary_N.json` en disco
- Al iniciar la llamada de merge final
- Al escribir `result.json`

### 6.4 Guard 3 — TTL de limpieza post-completion (15 minutos)

```python
async def schedule_cleanup(job_id: str):
    """
    Se lanza en el momento exacto en que el job llega a "done" o "failed".
    Duerme RESULT_TTL_MINUTES * 60 segundos y luego elimina el directorio completo.
    Si el servidor cae antes, startup_cleanup() maneja los directorios huérfanos.
    """
    delay = settings.RESULT_TTL_MINUTES * 60  # default: 900 segundos (15 min)
    await asyncio.sleep(delay)
    job_dir = Path(settings.JOB_BASE_DIR) / job_id
    if job_dir.exists():
        shutil.rmtree(job_dir, ignore_errors=True)
```

### 6.5 Limpieza al arrancar (startup)

```python
@app.on_event("startup")
async def startup_cleanup():
    """
    Limpia directorios huérfanos de ejecuciones anteriores del servidor.
    Se ejecuta antes de aceptar cualquier request.
    """
    base = Path(settings.JOB_BASE_DIR)
    if not base.exists():
        base.mkdir(parents=True)
        return

    now = datetime.utcnow()
    for job_dir in base.iterdir():
        if not job_dir.is_dir():
            continue
        state_file = job_dir / "state.json"
        try:
            state = json.loads(state_file.read_text())
            status = state.get("status")
            # Caso 1: job completado (done/failed) → TTL ya venció (servidor estaba caído)
            if status in ("done", "failed"):
                completed_at = datetime.fromisoformat(
                    state.get("done_at") or state.get("updated_at")
                )
                if (now - completed_at).total_seconds() > settings.RESULT_TTL_MINUTES * 60:
                    shutil.rmtree(job_dir, ignore_errors=True)
            # Caso 2: job en curso pero más viejo que el timeout global → huérfano
            elif status not in ("done", "failed", "expired"):
                started_at = datetime.fromisoformat(state.get("started_at", state["created_at"]))
                if (now - started_at).total_seconds() > settings.JOB_MAX_DURATION_SECONDS:
                    shutil.rmtree(job_dir, ignore_errors=True)
        except Exception:
            # state.json corrupto o inexistente → eliminar directorio
            shutil.rmtree(job_dir, ignore_errors=True)
```

---

## PARTE 7 — SUBAGENTES: ESPECIFICACIONES DETALLADAS

### 7.1 EXPLORER — Descubrimiento de rutas

**Responsabilidad única**: dado un dominio, producir `routes.json`.

**Estrategias en orden de prioridad**:
```
1. GET /robots.txt          → extraer directivas Sitemap:
2. GET /sitemap.xml         → parsear URLs (sitemaps hijos, profundidad máx 2)
3. GET /sitemap_index.xml   → iterar sitemaps hijos
4. Patrones comunes:        /sitemap-pages.xml, /sitemap-posts.xml,
                             /sitemap-products.xml, /page-sitemap.xml
5. GET / (homepage)         → extraer <a href> internos del HTML
6. Fallback hardcoded:      /about, /contact, /products, /blog, /faq, /services
```

**Filtros de rutas**:
- Excluir extensiones: `.css`, `.js`, `.png`, `.jpg`, `.jpeg`, `.gif`, `.webp`,
  `.svg`, `.pdf`, `.zip`, `.xml` (excepto sitemaps), `.ico`, `.woff`, `.woff2`
- Excluir parámetros: `?utm_`, `?ref=`, `?session=`, `?token=`, `?fbclid=`
- Excluir rutas admin: `/wp-admin`, `/admin`, `/login`, `/dashboard`, `/_next`, `/.well-known`
- Deduplicar (trailing slash = misma URL)
- Límite: `MAX_PAGES_PER_JOB` (default: 50)

**Salida**: `/tmp/dealerscrapper/<job_id>/routes.json`

**Error reportable**: `NO_ROUTES_FOUND` → job falla inmediatamente.

---

### 7.2 FETCHER — Descarga de HTML

**Responsabilidad única**: descargar HTML crudo de cada URL en `routes.json`.

**Comportamiento**:
- Semáforo asyncio: máx `MAX_CONCURRENT_FETCHES` (default: 3) en paralelo
- Timeout: 15s por request
- Reintentos: 3, con backoff exponencial (1s, 2s, 4s)
- Guarda HTML a disco inmediatamente, nunca en RAM
- Sigue redirects (máx 3) pero solo dentro del dominio base

**Salida**: `/tmp/dealerscrapper/<job_id>/raw/<url_hash>.html` por URL exitosa  
**Índice**: `/tmp/dealerscrapper/<job_id>/fetch_results.json`

**Errores reportables**: `FETCH_ALL_FAILED` si 0 páginas exitosas.

---

### 7.3 EXTRACTOR — Parseo y estructuración

**Responsabilidad única**: convertir cada `.html` crudo en `PageData` JSON.

**PageData schema**:
```python
{
  "url": str,
  "url_hash": str,
  "title": str,                    # <title> → fallback: primer <h1>
  "meta_description": str,
  "meta_keywords": list[str],
  "og_data": dict,                 # Open Graph completo
  "canonical_url": str,
  "language": str,                 # <html lang="...">
  "headings": { "h1": [], "h2": [], "h3": [] },
  "text_content": str,             # readability-lxml, máx 10.000 chars
  "word_count": int,
  "internal_links": list[str],
  "external_links": list[str],
  "images": list[{
    "src": str,                    # URL absoluta resuelta
    "alt": str,
    "width": int | None,
    "height": int | None
  }],
  "schema_org": list[dict],        # JSON-LD
  "has_forms": bool,
  "has_tables": bool,
  "extracted_at": str
}
```

**Limpieza**: elimina `raw/<url_hash>.html` inmediatamente tras extraer cada página.  
**Al terminar**: elimina el directorio `raw/` completo si está vacío.

**Error reportable**: `EXTRACTION_EMPTY` si > 50% de páginas con word_count < 50.

---

### 7.4 AUDITOR — Verificación de cobertura

**Responsabilidad única**: verificar completitud antes de pasar al LLM.

**Verificaciones**:
```
1. COBERTURA: pages/ vs routes.json
   < 70% → flag COVERAGE_LOW
   < AUDIT_COVERAGE_MIN_PERCENT → critical: true → AUDIT_CRITICAL_GAPS

2. PÁGINAS CLAVE:
   ¿Existe home? ¿Existe alguna con has_forms=true? ¿schema_org Product si ecommerce?
   Si faltan → añadir a candidatos de re-fetch

3. COHERENCIA:
   > 50% con word_count < 50 → EXTRACTION_EMPTY

4. LINKS INTERNOS NO CRAWLEADOS:
   Extraer internal_links de pages/*.json no presentes en routes.json
   → Añadir hasta AUDIT_MAX_NEW_ROUTES URLs nuevas

5. INTEGRIDAD:
   PageData sin campos obligatorios → marcar como inválido, excluir del LLM
```

**Re-fetch loop** (máx 1 iteración):
```
Auditor detecta gaps → reporta al Orchestrer
Orchestrer → lanza Fetcher parcial (solo nuevas URLs)
Orchestrer → lanza Extractor parcial
Orchestrer → lanza Auditor de nuevo (flag: second_pass=true)
Si second_pass y aún hay gaps → continuar sin re-fetch (no bucle infinito)
```

**Salida**: `/tmp/dealerscrapper/<job_id>/audit_report.json`

---

### 7.5 REVIEWER — Análisis LLM

**Responsabilidad única**: construir `result.json` con el LLM.
Protegido por el Watchdog de 5 minutos de inactividad.

**Patrón de chunking**:
```
1. Leer lista de páginas válidas desde audit_report.json
2. Dividir en batches de 5 páginas
3. Por cada batch:
   - activity_event.set()
   - Llamar al LLM con el batch
   - activity_event.set() al recibir respuesta
   - Guardar chunk_summary_N.json
   - activity_event.set() al guardar
4. Llamada de merge con todos los chunk_summaries
   - activity_event.set() al iniciar merge
5. Escribir result.json
   - activity_event.set() al terminar
6. Eliminar chunk_summaries/ al completar
```

**Detección del estado failed durante ejecución**:
```python
# El Reviewer verifica el estado del job antes de cada llamada al LLM
state = await job_manager.get_state(job_id)
if state.status == "failed":
    return  # El Watchdog o el timeout global dispararon. Abortar limpio.
```

**LLM Client** — soporta 4 providers:
```python
class LLMClient:
    """
    Errores mapeados a error_codes:
    - httpx.TimeoutException → no hacer raise; el Watchdog detectará la inactividad
    - 401 / 403              → raise LLMAuthError → job falla con LLM_AUTH_ERROR
    - 429                    → esperar retry-after (máx 60s), reintentar 1 vez
    - 5xx                    → reintentar 1 vez; si falla → LLM_PARSE_ERROR
    - JSON malformado        → reintentar 1 vez con prompt estricto; si falla → LLM_PARSE_ERROR
    """
```

**Limpieza al terminar**: elimina `chunk_summaries/` completo.

---

### 7.6 PACKAGER — Empaquetado final

**Responsabilidad única**: empaquetar el resultado y lanzar el TTL de limpieza.

**Comportamiento**:
```
1. Si DOWNLOAD_IMAGES=true:
   - Para cada imagen en result.assets.images:
     * Descargar con timeout 10s
     * Verificar Content-Type es imagen válida
     * Verificar tamaño < MAX_IMAGE_SIZE_MB
     * Guardar en /tmp/dealerscrapper/<job_id>/images/<filename>
     * Actualizar local_path en result.json
   - Si imagen falla → registrar error, continuar (no bloquear el job)

2. Crear result.zip con:
   - result.json
   - images/ (si aplica)

3. Limpiar archivos temporales:
   - Eliminar pages/*.json
   - Eliminar routes.json, fetch_results.json, extract_results.json, audit_report.json

4. Actualizar state.json:
   - status: "done"
   - done_at: <timestamp>

5. Lanzar: asyncio.create_task(schedule_cleanup(job_id))
   → job_dir completo eliminado en 15 minutos
```

**Lo que queda en disco tras el Packager** (solo por 15 minutos):
```
/tmp/dealerscrapper/<job_id>/
├── state.json        ← necesario para responder al polling del CMS
├── result.json       ← consumible por GET /result
├── result.zip        ← consumible por GET /download
└── images/           ← consumibles por GET /images/{filename}
```

---

## PARTE 8 — DEFINICIÓN DE AGENTES (.claude/agents/)

### orchestrer.md

```markdown
# Agente: Orchestrer — DealerScrapper

## Identidad
Coordinás el pipeline completo. Nunca escribís código ni procesás datos.

## Pipeline
Explorer → Fetcher → Extractor → Auditor → [Fetcher+Extractor+Auditor parcial si hay gaps]
→ Reviewer → Packager

## Reglas
- Un subagente a la vez. Esperás DONE antes de lanzar el siguiente.
- Si un subagente reporta BLOCKED o ERROR:
    * Evaluás el error_code y decidís: reintentar una vez o fallar el job.
- Actualizás job_manager.update_status() después de cada transición.
- Toda decisión queda en progress/current.md antes de ejecutarla.
- Toda comunicación entre subagentes es via archivos en /tmp/dealerscrapper/<job_id>/

## Re-fetch del Auditor
Solo se permite un ciclo de re-fetch (second_pass=true en audit_report).
Si el segundo Auditor también detecta gaps críticos → fallar con AUDIT_CRITICAL_GAPS.

## NO hacés
- Escribir código de aplicación
- Procesar HTML ni llamar al LLM directamente
- Modificar result.json
```

### implementer.md

```markdown
# Agente: Implementer — DealerScrapper

## Identidad
Implementás features según las specs asignadas. Una feature a la vez, completa.

## Reglas
- Al terminar: escribís reporte en progress/impl_<feature_id>.md
- Solo devolvés: DONE | DONE_WITH_CONCERNS | NEEDS_CONTEXT | BLOCKED
- No te autoaprobás. El Reviewer valida.
- Si hay ambigüedad: NEEDS_CONTEXT antes de asumir.

## Stack
Python 3.9, FastAPI, asyncio, httpx, beautifulsoup4, readability-lxml
Sin Playwright. Sin Selenium. Sin Chromium.
ARM64 compatible: no agregar dependencias sin verificar wheels linux/arm64.
```

### reviewer.md

```markdown
# Agente: Reviewer — DealerScrapper

## Identidad
Validás el trabajo del Implementer contra los checkpoints de CHECKPOINTS.md.
No editás código. Reportás hallazgos con precisión.

## Proceso
1. Leer checkpoints de la feature en revisión
2. Ejecutar verificaciones (correr tests si existen, revisar código)
3. Escribir reporte en progress/review_<feature_id>.md
4. Devolver: APPROVED | REJECTED (con lista de issues específicos)

## Reglas
- REJECTED requiere lista de issues: archivo, línea, comportamiento esperado vs actual.
- No hacés el fix. El Orchestrer lanza un nuevo Implementer para eso.
```

---

## PARTE 9 — VARIABLES DE ENTORNO (.env)

```env
# === API ===
PROJECT_NAME=DealerScrapper
API_VERSION=1.0.0
DEBUG=false
API_KEY=<random_32_char_hex>

# === LLM Provider ===
LLM_PROVIDER=openai
LLM_MODEL=gpt-4o-mini
LLM_API_KEY=<provider_api_key>
LLM_MAX_TOKENS=4000
LLM_TEMPERATURE=0.2

# === Crawler ===
MAX_CONCURRENT_FETCHES=3
MAX_PAGES_PER_JOB=50
FETCH_TIMEOUT_SECONDS=15
FETCH_RETRIES=3

# === Timeouts y ciclo de vida ===
LLM_WATCHDOG_SECONDS=300         # 5 min de inactividad del LLM → LLM_TIMEOUT
JOB_MAX_DURATION_SECONDS=1800    # 30 min de ejecución total → JOB_TIMEOUT
RESULT_TTL_MINUTES=15            # 15 min post-done/failed → rm -rf job_dir

# === Storage ===
JOB_BASE_DIR=/tmp/dealerscrapper

# === Imágenes ===
DOWNLOAD_IMAGES=false
MAX_IMAGE_SIZE_MB=5

# === Auditor ===
AUDIT_COVERAGE_MIN_PERCENT=30
AUDIT_REFETCH_ENABLED=true
AUDIT_MAX_NEW_ROUTES=10
```

---

## PARTE 10 — DEPENDENCIAS (requirements.txt)

```
fastapi==0.115.0
gunicorn==22.0.0
uvicorn[standard]==0.30.6
pydantic-settings==2.3.4
httpx==0.27.0
beautifulsoup4==4.12.3
lxml==5.2.2
readability-lxml==0.8.1
python-multipart==0.0.9
aiofiles==23.2.1
```

Peso: ~80 MB. Sin Chromium. Todas con wheels linux/arm64.

---

## PARTE 11 — NGINX (dealerscrapper.conf)

```nginx
server {
    listen 80;
    server_name scraper.azanolabs.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name scraper.azanolabs.com;

    ssl_certificate     /etc/nginx/ssl/origin.crt;
    ssl_certificate_key /etc/nginx/ssl/origin.key;

    client_max_body_size 10m;
    proxy_read_timeout   600s;
    proxy_send_timeout   600s;

    location / {
        proxy_pass         http://127.0.0.1:8002;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
    }
}
```

Rate limiting separado (añadir a `rate-limit.conf`):
```nginx
limit_req_zone $http_cf_connecting_ip zone=scraper:10m rate=1r/s;
```

Y en el bloque `location /` de dealerscrapper.conf:
```nginx
limit_req zone=scraper burst=5 nodelay;
```

---

## PARTE 12 — SYSTEMD SERVICE (dealerscrapper.service)

```ini
[Unit]
Description=DealerScrapper API
After=network.target

[Service]
User=<your-user>
WorkingDirectory=/srv/dealerscrapper
EnvironmentFile=-/srv/dealerscrapper/.env
ExecStart=/srv/dealerscrapper/.venv/bin/gunicorn app.main:app \
    -w 1 -k uvicorn.workers.UvicornWorker \
    --bind 127.0.0.1:8002 \
    --timeout 600
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

---

## PARTE 13 — CHECKPOINTS DE AUDITORÍA

### F01 — Core API y job management
- [ ] `POST /api/v1/scrape` crea job con UUID v4, devuelve `{ job_id, status: "queued" }`
- [ ] `GET /status` devuelve schema correcto en todos los estados incluyendo `ttl_remaining_seconds`
- [ ] `ttl_remaining_seconds` decrementa correctamente desde 900 cuando status==done/failed
- [ ] `GET /result` devuelve 404 con `job_not_found_or_expired` tras el TTL
- [ ] `GET /images` devuelve 404 si `DOWNLOAD_IMAGES=false`
- [ ] `DELETE /{job_id}` elimina el directorio y cancela todas las Tasks asyncio del job
- [ ] startup_cleanup() corre antes de aceptar requests y elimina huérfanos correctamente
- [ ] `X-API-Key` requerida en todos los endpoints `/api/v1/`
- [ ] `GET /guide-ai` devuelve JSON válido con todos los endpoints y error_codes

### F02 — Explorer
- [ ] Detecta y parsea sitemap.xml (incluye sitemaps anidados, profundidad máx 2, sin bucles)
- [ ] Fallback a homepage links si no hay sitemap
- [ ] Filtros activos (assets, tracking, admin)
- [ ] Respeta `MAX_PAGES_PER_JOB`
- [ ] `routes.json` en formato correcto con `discovery_method` y `source` por URL
- [ ] `NO_ROUTES_FOUND` falla el job inmediatamente

### F03 — Fetcher
- [ ] Semáforo asyncio activo (máx `MAX_CONCURRENT_FETCHES` simultáneos)
- [ ] Backoff exponencial en reintentos
- [ ] HTML guardado en disco inmediatamente (nunca acumulado en RAM)
- [ ] `fetch_results.json` con todas las URLs y sus estados
- [ ] `FETCH_ALL_FAILED` si 0 páginas exitosas

### F04 — Extractor
- [ ] Extrae todos los campos de PageData definidos
- [ ] `text_content` usa readability-lxml
- [ ] URLs de imágenes resueltas a absolutas
- [ ] Elimina `raw/<hash>.html` inmediatamente tras extraer cada página
- [ ] Elimina directorio `raw/` al terminar
- [ ] `EXTRACTION_EMPTY` si > 50% con word_count < 50

### F05 — Auditor
- [ ] Calcula `coverage_percent` correctamente
- [ ] `critical: true` cuando `coverage_percent < AUDIT_COVERAGE_MIN_PERCENT`
- [ ] Detecta internal_links no crawleados y los agrega (respeta `AUDIT_MAX_NEW_ROUTES`)
- [ ] `second_pass=true` en segunda ejecución (no bucle infinito)
- [ ] `audit_report.json` en formato correcto

### F06 — Guards de tiempo y limpieza
- [ ] Guard 1 (global timeout): falla el job con `JOB_TIMEOUT` si supera 30 min desde `started_at`
- [ ] Guard 2 (LLM watchdog): falla con `LLM_TIMEOUT` si no hay `event.set()` en 5 min
- [ ] Guard 2 solo se lanza al entrar en fase "analyzing"
- [ ] Guard 3 (TTL): lanzado exactamente cuando status cambia a done/failed
- [ ] Guard 3: elimina `rm -rf job_dir` tras 15 min exactos
- [ ] Reviewer detecta `state.status == "failed"` y aborta sin error adicional
- [ ] `DELETE /{job_id}` cancela las 3 Tasks asyncio activas del job

### F07 — Reviewer
- [ ] `activity_event.set()` en mínimo 5 puntos por batch
- [ ] Verifica `state.status == "failed"` antes de cada llamada al LLM
- [ ] 4 providers funcionan (openai, anthropic, deepseek, minimax)
- [ ] `LLM_AUTH_ERROR` falla inmediatamente (sin reintentos)
- [ ] 429 espera retry-after y reintenta 1 vez
- [ ] JSON malformado reintenta 1 vez con prompt estricto
- [ ] Elimina `chunk_summaries/` al completar
- [ ] `result.json` cumple el schema completo

### F08 — Packager
- [ ] Descarga imágenes solo si `DOWNLOAD_IMAGES=true`
- [ ] Verifica Content-Type y tamaño de cada imagen descargada
- [ ] Actualiza `local_path` en `result.json` por cada imagen descargada
- [ ] Elimina pages/, routes.json, fetch_results.json, extract_results.json, audit_report.json
- [ ] `result.zip` contiene result.json + images/ (si aplica)
- [ ] `state.json` → `status: "done"`, `done_at: <timestamp>`
- [ ] `asyncio.create_task(schedule_cleanup(job_id))` lanzado DESPUÉS de escribir state.json

### F09 — Endpoints de imágenes
- [ ] `GET /images` devuelve listado con `download_url`, `size_bytes`, `original_url`, `alt`
- [ ] `GET /images` incluye `ttl_remaining_seconds` actualizado
- [ ] `GET /images/{filename}` sirve la imagen con el Content-Type correcto
- [ ] `GET /images/{filename}` devuelve 404 si el archivo no existe
- [ ] Ambos endpoints devuelven 404 con `job_not_found_or_expired` si el directorio fue eliminado
- [ ] `GET /download` descarga el ZIP completo correctamente

### Checklist de infraestructura (verificación manual del desarrollador)
- [ ] `systemctl status dealerscrapper` → activo y corriendo
- [ ] `ss -tlnp | grep 8002` → puerto escuchando
- [ ] `nginx -t` → sin errores; `api.azanolabs.com` sigue respondiendo (sin regresión)
- [ ] `curl https://scraper.azanolabs.com/` → JSON de status
- [ ] Memoria total VPS < 70% con ambas APIs idle
- [ ] Job de prueba end-to-end: POST → poll → done → GET /result → GET /images → GET /download → verificar que tras 15 min devuelve 404

---

## PARTE 14 — FEATURE LIST

```json
[
  {
    "id": "f01",
    "name": "core_api_job_management",
    "description": "FastAPI :8002, X-API-Key, gestión de jobs en disco, startup_cleanup, endpoints status/result/download/delete, guide-ai con todos los error_codes",
    "status": "pending",
    "phase": 1
  },
  {
    "id": "f02",
    "name": "explorer_route_discovery",
    "description": "Subagente Explorer: robots.txt, sitemap (anidado profundidad 2), homepage links, filtros, routes.json",
    "status": "pending",
    "phase": 2
  },
  {
    "id": "f03",
    "name": "fetcher_html_download",
    "description": "Subagente Fetcher: semáforo asyncio, backoff, HTML a disco inmediato, fetch_results.json",
    "status": "pending",
    "phase": 2
  },
  {
    "id": "f04",
    "name": "extractor_page_data",
    "description": "Subagente Extractor: PageData completo, readability-lxml, limpieza raw/ al terminar, extract_results.json",
    "status": "pending",
    "phase": 3
  },
  {
    "id": "f05",
    "name": "auditor_coverage",
    "description": "Subagente Auditor: cobertura, gaps, re-fetch parcial (máx 1 ciclo), integridad PageData, audit_report.json",
    "status": "pending",
    "phase": 3
  },
  {
    "id": "f06",
    "name": "guards_timeout_cleanup",
    "description": "Guard 1 (global 30min), Guard 2 (LLM watchdog 5min con asyncio.Event), Guard 3 (TTL 15min post-done/failed con schedule_cleanup), startup_cleanup al arrancar",
    "status": "pending",
    "phase": 4
  },
  {
    "id": "f07",
    "name": "reviewer_llm_analysis",
    "description": "Subagente Reviewer: LLM client 4 providers, batching 5 páginas, activity_event en 5+ puntos, detección de failed mid-run, result.json, limpieza chunk_summaries/",
    "status": "pending",
    "phase": 4
  },
  {
    "id": "f08",
    "name": "packager_output",
    "description": "Subagente Packager: descarga imágenes (si aplica), limpieza de temporales, result.zip, status done, lanzar Guard 3",
    "status": "pending",
    "phase": 5
  },
  {
    "id": "f09",
    "name": "images_endpoints",
    "description": "GET /images (listado con ttl_remaining), GET /images/{filename} (StreamingResponse), 404 correcto post-TTL",
    "status": "pending",
    "phase": 5
  },
  {
    "id": "f10",
    "name": "nginx_systemd_scripts",
    "description": "dealerscrapper.conf, dealerscrapper.service, scripts/linux/setup.sh y deploy.sh",
    "status": "pending",
    "phase": 5
  }
]
```

---

## PARTE 15 — PASOS MANUALES PARA EL DESARROLLADOR

### Paso 1 — DNS en Cloudflare
```
Tipo: A | Nombre: scraper | Valor: <IP VPS> | Proxy: ✅ activado
```

### Paso 2 — Setup en VPS
```bash
cd /srv && git clone <repo> dealerscrapper && cd dealerscrapper
chmod +x scripts/linux/setup.sh && ./scripts/linux/setup.sh
nano .env  # API_KEY, LLM_PROVIDER, LLM_MODEL, LLM_API_KEY, DEBUG=false
```

### Paso 3 — Systemd
```bash
sudo cp dealerscrapper.service /etc/systemd/system/
sudo nano /etc/systemd/system/dealerscrapper.service  # editar User=
sudo systemctl daemon-reload && sudo systemctl enable --now dealerscrapper
sudo systemctl status dealerscrapper
```

### Paso 4 — Nginx
```bash
sudo cp dealerscrapper.conf /etc/nginx/conf.d/
# Añadir zona scraper a rate-limit.conf
sudo nginx -t && sudo systemctl reload nginx
```

### Paso 5 — Verificación final
```bash
# Ambas APIs
curl https://api.azanolabs.com/
curl https://scraper.azanolabs.com/

# Job de prueba end-to-end
JOB=$(curl -s -X POST https://scraper.azanolabs.com/api/v1/scrape \
  -H "X-API-Key: TU_KEY" -H "Content-Type: application/json" \
  -d '{"url":"https://example.com","options":{}}' | jq -r .job_id)

# Polling
watch -n 5 "curl -s -H 'X-API-Key: TU_KEY' \
  https://scraper.azanolabs.com/api/v1/scrape/$JOB/status | jq '{status,ttl_remaining_seconds,progress}'"

# Consumir resultados (antes de que expire el TTL)
curl -H "X-API-Key: TU_KEY" https://scraper.azanolabs.com/api/v1/scrape/$JOB/result
curl -H "X-API-Key: TU_KEY" https://scraper.azanolabs.com/api/v1/scrape/$JOB/images

# Verificar que tras 15 min devuelve 404
sleep 900 && curl -H "X-API-Key: TU_KEY" \
  https://scraper.azanolabs.com/api/v1/scrape/$JOB/status
```

---

## NOTAS FINALES PARA AGENTES

1. **Una feature a la vez.** No empezar la siguiente hasta que la actual pase todos sus checkpoints.
2. **Estado en disco, no en chat.** Todo output intermedio → `progress/current.md`.
3. **Anti-teléfono-descompuesto.** Implementer y Reviewer devuelven solo la referencia, no el contenido.
4. **Los 3 Guards son no negociables.** Guard 1 (global), Guard 2 (LLM), Guard 3 (TTL). Sin ellos el VPS acumula basura indefinidamente.
5. **El CMS depende del TTL.** `ttl_remaining_seconds` en el status es el mecanismo principal para que el CMS sepa cuánto tiempo tiene para consumir los resultados.
6. **DELETE cancela todo.** Un `DELETE /{job_id}` debe cancelar las 3 Tasks asyncio activas del job (Guards 1, 2 y 3) y eliminar el directorio. No puede dejar Tasks huérfanas.
7. **ARM64.** Verificar wheels linux/arm64 antes de agregar cualquier dependencia nueva.
8. **No tocar optimus.conf.** DealerScrapper vive en dealerscrapper.conf únicamente.
