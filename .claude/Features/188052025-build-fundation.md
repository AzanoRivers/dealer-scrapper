# Feature F01 — Fundación: Core API y Job Management

**ID**: f01  
**Fecha**: 2026-05-18  
**Fase**: 1 (base sobre la que construyen todas las demás features)  
**Status**: pending  
**Checkpoints de referencia**: `DealerScrapper_PLAN.md` Parte 13 → Sección F01

---

## Objetivo

Construir la estructura completa del proyecto y el núcleo funcional de la API:
servidor FastAPI en `:8002`, autenticación por API key, gestión de jobs en disco,
limpieza al arrancar (`startup_cleanup`), y todos los endpoints del contrato con el CMS.

Al terminar esta feature, la API debe arrancar, autenticar, crear y gestionar jobs
en `/tmp/dealerscrapper/`, y responder correctamente al polling del CMS — sin que
ningún subagente del pipeline esté aún implementado.

---

## Contexto Técnico

- **VPS**: Oracle Linux ARM64, `/home/opc/projects/dealerscrapper/`, puerto `:8002`
- **Coexiste con**: OptimusApi en `:8001` (no tocar su configuración)
- **Dev local**: Windows 11 + PowerShell, Python 3.9
- **Deploy**: Linux ARM64 vía SSH
- **Jobs en disco**: `/tmp/dealerscrapper/<job_id>/state.json`
- **Auth**: header `X-API-Key` en todos los endpoints `/api/v1/`

---

## Estructura de Archivos a Crear

```
vps-dealer-scrapping/
├── app/
│   ├── __init__.py
│   ├── main.py                  ← FastAPI app, startup/shutdown, lifespan
│   ├── config.py                ← pydantic-settings (todas las vars de .env)
│   ├── dependencies.py          ← verify_api_key dependency
│   ├── models/
│   │   ├── __init__.py
│   │   ├── job.py               ← JobState dataclass / Pydantic model
│   │   └── schemas.py           ← Request/Response schemas (ScrapeRequest, StatusResponse, etc.)
│   ├── core/
│   │   ├── __init__.py
│   │   └── job_manager.py       ← JobManager: create, get, update, complete, fail, delete
│   └── api/
│       ├── __init__.py
│       └── v1/
│           ├── __init__.py
│           └── router.py        ← todos los endpoints de /api/v1/
├── tests/
│   ├── __init__.py
│   ├── conftest.py              ← fixtures: TestClient, tmp_dir, valid api_key
│   └── test_f01_core_api.py     ← tests de todos los checkpoints F01
├── requirements.txt
├── requirements-dev.txt         ← pytest, httpx (test client), mypy
├── .env.example
├── .gitignore
├── dealerscrapper.conf          ← nginx (Parte 11 del plan)
└── dealerscrapper.service       ← systemd (Parte 12 del plan)
```

---

## Pasos de Construcción

### Paso 1 — Scaffolding y configuración base

**Crear** `requirements.txt` con versiones exactas del plan (Parte 10):
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

**Crear** `requirements-dev.txt`:
```
pytest==8.2.0
pytest-asyncio==0.23.6
mypy==1.10.0
```

**Crear** `.env.example` con todas las variables de Parte 9 del plan (sin valores reales).

**Crear** `app/config.py` usando `pydantic-settings`:
- Clase `Settings` con todas las vars del `.env` (Parte 9)
- `model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")`
- Valores default definidos (para que arranque sin `.env` en tests)
- Instancia global: `settings = Settings()`

### Paso 2 — Modelos y schemas

**Crear** `app/models/job.py`:
- Enum `JobStatus` con todos los estados: `queued, exploring, fetching, extracting, auditing, analyzing, packaging, done, failed, expired`
- Dataclass o Pydantic model `JobState` con campos: `job_id`, `status`, `progress`, `error`, `created_at`, `started_at`, `updated_at`, `done_at`, `ttl_remaining_seconds`
- Clase `JobProgress` con: `phase`, `pages_done`, `pages_total`, `percent`
- Clase `JobError` con: `code`, `message`, `failed_at`, `retry_after`

**Crear** `app/models/schemas.py`:
- `ScrapeRequest`: `url` (HttpUrl), `options` (ScrapeOptions)
- `ScrapeOptions`: `max_pages`, `download_images`, `llm_provider`, `llm_model` (todos opcionales)
- `ScrapeResponse`: `job_id`, `status`
- `StatusResponse`: schema completo del endpoint de status (Parte 4.4 del plan)
- `ErrorResponse`: `error`, `detail`, `job_id`

### Paso 3 — Job Manager

**Crear** `app/core/job_manager.py`:

Responsabilidades:
- `create_job(url, options) -> str`: crea UUID v4, directorio en disco, `state.json` con status `queued`. Retorna `job_id`.
- `get_state(job_id) -> JobState | None`: lee `state.json` desde disco. Retorna `None` si no existe.
- `update_status(job_id, status, progress=None)`: actualiza `state.json` en disco.
- `complete_job(job_id)`: status → `done`, escribe `done_at`. **No lanza Guard 3 aquí** — eso lo hace el Packager en F08.
- `fail_job(job_id, error_code, message, retry_after=None)`: status → `failed`, escribe error.
- `delete_job(job_id)`: elimina directorio completo. Usado por `DELETE /{job_id}`.
- `get_ttl_remaining(job_id) -> int | None`: calcula segundos restantes desde `done_at`/`failed_at`.
- Propiedad `active_jobs_count`: cuenta directorios en `JOB_BASE_DIR` con status no terminal.

Reglas de implementación:
- Todas las operaciones con `state.json` usan `aiofiles` (async I/O).
- `state.json` se escribe atómicamente: escribir a `state.tmp.json`, luego `rename` (evita corrupción).
- `get_state` retorna `None` (no lanza excepciones) si el directorio no existe.

### Paso 4 — Autenticación

**Crear** `app/dependencies.py`:
- `async def verify_api_key(x_api_key: str = Header(...)) -> str`
- Compara con `settings.API_KEY` usando `hmac.compare_digest` (timing-safe).
- Lanza `HTTPException(401)` si no coincide.
- Lanza `HTTPException(403)` si el header está ausente (FastAPI lo maneja automáticamente con el tipo `Header`).

### Paso 5 — Endpoints

**Crear** `app/api/v1/router.py` con todos los endpoints de Parte 5 del plan:

```
GET  /                              → info del servidor (sin auth)
GET  /guide-ai                      → referencia JSON para agentes (sin auth)
GET  /api/v1/status                 → jobs activos, capacidad (con auth)
POST /api/v1/scrape                 → crear job (con auth)
GET  /api/v1/scrape/{job_id}/status → estado + TTL (con auth)
GET  /api/v1/scrape/{job_id}/result → result.json si done (con auth)
GET  /api/v1/scrape/{job_id}/images → listado imágenes (con auth) — stub: 404 hasta F09
GET  /api/v1/scrape/{job_id}/images/{filename} → imagen individual (con auth) — stub: 404 hasta F09
GET  /api/v1/scrape/{job_id}/download → ZIP completo (con auth) — stub: 404 hasta F08
DELETE /api/v1/scrape/{job_id}      → cancelar y eliminar job (con auth)
```

Comportamientos clave:
- `POST /scrape`: valida URL, crea job, **NO lanza el pipeline aún** (eso es F02+). Devuelve `{job_id, status: "queued"}`.
- `GET /status/{job_id}`: calcula `ttl_remaining_seconds` en tiempo real, devuelve schema completo.
- `GET /result/{job_id}`: devuelve 404 con `job_not_found_or_expired` si job no existe o ya vencido. Devuelve 425 (Too Early) si status != "done".
- `DELETE /{job_id}`: elimina directorio, cancela Tasks asyncio si existen. En F01 no hay Tasks, solo eliminar directorio.
- `GET /guide-ai`: JSON estático con todos los endpoints, error_codes, y descripción del sistema.
- `ttl_remaining_seconds`: `null` cuando status no es done/failed; entero ≥ 0 cuando sí.

**Respuesta 404 estándar** (usarla en todos los endpoints que reciben `job_id`):
```json
{
  "error": "job_not_found",
  "detail": "El job no existe o ha expirado. Los resultados se eliminan 15 minutos después de completarse.",
  "job_id": "<uuid>"
}
```

### Paso 6 — App Principal y Startup Cleanup

**Crear** `app/main.py`:

```python
# Estructura esperada (sin código exacto)
app = FastAPI(title="DealerScrapper", version=settings.API_VERSION, lifespan=lifespan)

@asynccontextmanager
async def lifespan(app: FastAPI):
    await startup_cleanup()   # limpia huérfanos antes de aceptar requests
    yield
    # shutdown: nada que hacer en F01

async def startup_cleanup():
    # Lógica exacta de Parte 6.5 del plan maestro
    # - Crea JOB_BASE_DIR si no existe
    # - Itera directorios en JOB_BASE_DIR
    # - Elimina: done/failed con TTL vencido, en-curso más viejos que JOB_MAX_DURATION_SECONDS, corruptos
```

Incluir middleware:
- `CORSMiddleware` con origins configurables vía settings.
- Manejo de excepciones global → respuesta JSON consistente.

### Paso 7 — Archivos de Infraestructura

> **IMPORTANTE — diferencias vs. el plan maestro (basado en OptimusApi real en producción):**

**Crear** `dealerscrapper.conf` — nginx. Diferencias vs. Parte 11 del plan:

```nginx
# NO declarar default_server — ya está declarado en optimus.conf (devuelve 444).
# Redeclararlo causaría conflicto en nginx.

server {
    listen 80;
    server_name scraper.azanolabs.com;
    include /etc/nginx/cloudflare-ips.conf;   # igual que optimus — solo IPs Cloudflare
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name scraper.azanolabs.com;

    ssl_certificate     /etc/nginx/ssl/origin.crt;
    ssl_certificate_key /etc/nginx/ssl/origin.key;

    include /etc/nginx/cloudflare-ips.conf;   # igual que optimus

    client_max_body_size 10m;
    proxy_read_timeout   600s;
    proxy_send_timeout   600s;

    location / {
        limit_req zone=scraper burst=5 nodelay;   # zone=scraper (no conflicta con zone=api de optimus)

        proxy_pass         http://127.0.0.1:8002;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
    }
}
```

La zona `scraper` se declara en `rate-limit.conf` (ya existe para `api`):
```nginx
limit_req_zone $http_cf_connecting_ip zone=scraper:10m rate=1r/s;
```

**Crear** `dealerscrapper.service` — systemd. Diferencias vs. Parte 12 del plan:

```ini
[Unit]
Description=DealerScrapper API
After=network.target

[Service]
User=opc                                          # usuario real del VPS (no <your-user>)
WorkingDirectory=/home/opc/projects/dealerscrapper  # consistente con optimus
EnvironmentFile=-/home/opc/projects/dealerscrapper/.env
ExecStart=/home/opc/projects/dealerscrapper/.venv/bin/gunicorn app.main:app \
    -w 1 -k uvicorn.workers.UvicornWorker \
    --bind 127.0.0.1:8002 \
    --timeout 600 \
    --access-logfile - \
    --error-logfile -
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

### Paso 8 — Tests

**Crear** `tests/conftest.py`:
- Fixture `client`: `TestClient(app)` con `TEST_API_KEY` en settings.
- Fixture `tmp_job_dir`: directorio temporal para tests que no usan `/tmp/dealerscrapper`.
- Override de `settings.JOB_BASE_DIR` para tests → usar `tmp_path` de pytest.

**Crear** `tests/test_f01_core_api.py` con un test por cada checkpoint de F01:

```python
# Checkpoints a cubrir:
# - POST /scrape → crea job UUID v4, status "queued"
# - GET /status → schema correcto en estado "queued"
# - GET /status → ttl_remaining_seconds es null cuando no es done/failed
# - GET /status con job done → ttl_remaining_seconds decrementa desde 900
# - GET /result → 404 con job_not_found_or_expired tras TTL vencido
# - GET /result → 404 o 425 si status != "done"
# - GET /images → 404 cuando DOWNLOAD_IMAGES=false (stub)
# - DELETE /{job_id} → elimina directorio, 404 si se vuelve a consultar
# - startup_cleanup → elimina directorios huérfanos correctamente
# - X-API-Key requerida en todos los endpoints /api/v1/ → 401 sin key
# - GET /guide-ai → JSON válido con endpoints y error_codes
```

---

## Checkpoints F01 (del Plan Maestro)

```
- [ ] POST /api/v1/scrape crea job con UUID v4, devuelve { job_id, status: "queued" }
- [ ] GET /status devuelve schema correcto en todos los estados incluyendo ttl_remaining_seconds
- [ ] ttl_remaining_seconds decrementa correctamente desde 900 cuando status==done/failed
- [ ] GET /result devuelve 404 con job_not_found_or_expired tras el TTL
- [ ] GET /images devuelve 404 si DOWNLOAD_IMAGES=false
- [ ] DELETE /{job_id} elimina el directorio y cancela todas las Tasks asyncio del job
- [ ] startup_cleanup() corre antes de aceptar requests y elimina huérfanos correctamente
- [ ] X-API-Key requerida en todos los endpoints /api/v1/
- [ ] GET /guide-ai devuelve JSON válido con todos los endpoints y error_codes
```

---

## Validación Local (PowerShell)

Antes de marcar como DONE, ejecutar en orden:

```powershell
# 1. Activar entorno
.\.venv\Scripts\Activate.ps1

# 2. Instalar dependencias
pip install -r requirements.txt -r requirements-dev.txt

# 3. Verificar que el servidor arranca sin errores
python -c "from app.main import app; print('App importa OK')"

# 4. Correr tests de F01
python -m pytest tests/test_f01_core_api.py -v

# 5. Type checking
python -m mypy app/ --ignore-missing-imports

# 6. Verificar que no hay código síncrono no deseado
Select-String -Path "app\**\*.py" -Pattern "time\.sleep|import requests" -Recurse

# 7. Arrancar servidor y verificar endpoints manualmente
uvicorn app.main:app --reload --port 8002
# En otra terminal:
$headers = @{ "X-API-Key" = "test-key-12345678901234567890123456" }
Invoke-RestMethod -Uri "http://localhost:8002/" -Method Get
Invoke-RestMethod -Uri "http://localhost:8002/guide-ai" -Method Get
Invoke-RestMethod -Uri "http://localhost:8002/api/v1/status" -Method Get -Headers $headers
$body = @{ url = "https://example.com"; options = @{} } | ConvertTo-Json
Invoke-RestMethod -Uri "http://localhost:8002/api/v1/scrape" -Method Post -Headers $headers -Body $body -ContentType "application/json"
```

---

## Decisiones de Diseño

**¿Por qué `state.json` en disco y no en Redis/DB?**
El VPS es Oracle Free Tier con recursos limitados. Redis añade complejidad operativa.
Los jobs son efímeros (máx 30 min de ejecución + 15 min TTL). JSON en disco es suficiente,
simple, y legible para debugging.

**¿Por qué escritura atómica de `state.json`?**
Si el servidor cae en mitad de una escritura, el archivo podría quedar corrupto.
`write tmp → rename` garantiza que el archivo siempre es válido o no existe.

**¿Por qué no lanzar el pipeline en el POST /scrape?**
En F01 el pipeline no existe aún. La arquitectura separa la creación del job (F01)
de la ejecución del pipeline (F02+). Cuando el pipeline esté implementado, se lanzará
como `asyncio.create_task()` desde el endpoint POST.

**¿Por qué `GET /result` devuelve 425 y no 404 si status != "done"?**
425 Too Early comunica al CMS que el recurso existe pero aún no está listo,
en lugar de confundirlo con un job inexistente. El CMS puede reintent con el status endpoint.

---

## Siguiente Feature

`F02 — Explorer: Route Discovery`  
Archivo: `Features/[fecha]-explorer-route-discovery.md`  
Prerequisito: F01 completamente aprobado por el Reviewer.
