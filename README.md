# DealerScrapper

**[English](#english) · [Español](#español)**

![Python](https://img.shields.io/badge/Python-3.9-3776AB?style=flat&logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115.0-009688?style=flat&logo=fastapi&logoColor=white)
![Gunicorn](https://img.shields.io/badge/Gunicorn-22.0.0-499848?style=flat&logo=gunicorn&logoColor=white)
![httpx](https://img.shields.io/badge/httpx-0.27.0-2088FF?style=flat&logo=python&logoColor=white)
![BeautifulSoup4](https://img.shields.io/badge/BeautifulSoup4-4.12.3-orange?style=flat&logo=python&logoColor=white)
![Oracle Cloud](https://img.shields.io/badge/Oracle_Cloud-ARM64-F80000?style=flat&logo=oracle&logoColor=white)
![Cloudflare](https://img.shields.io/badge/Cloudflare-protected-F38020?style=flat&logo=cloudflare&logoColor=white)
![nginx](https://img.shields.io/badge/nginx-proxy-009639?style=flat&logo=nginx&logoColor=white)
![Tests](https://img.shields.io/badge/tests-127%2F127%20passing-brightgreen?style=flat&logo=pytest&logoColor=white)

---

## English

### What is it?

DealerScrapper is an async web scraping API built with FastAPI. It accepts a URL, crawls the site asynchronously, extracts structured business content using an LLM, and returns a `result.json` with business information, content topics, and images.

Designed to run as a microservice on an Oracle Cloud VPS (ARM64).

### Stack

| Component | Version | Details |
|-----------|---------|---------|
| Python | 3.9 | Runtime: chosen for ARM64 VPS compatibility |
| FastAPI | 0.115.0 | Web framework: async endpoints, Pydantic validation, OpenAPI |
| Gunicorn + UvicornWorker | 22.0.0 | Production server: 1 worker, ASGI, 600 s timeout |
| httpx | 0.27.0 | Async HTTP client: crawling pages and calling LLM APIs |
| beautifulsoup4 + lxml | 4.12.3 / 5.2.2 | HTML parsing: link extraction and sitemap parsing |
| readability-lxml | 0.8.1 | Content extraction: strips nav/ads, returns clean article text |
| pydantic-settings | 2.3.4 | Config management: typed settings loaded from `.env` |

**Not used:** Playwright, Selenium, Chromium (too heavy for production VPS deployment).

### Infrastructure

- **VPS**: Oracle Cloud Free Tier (ARM64, Oracle Linux)
- **Domain**: `scraper.azanolabs.com` → Cloudflare → nginx → `127.0.0.1:8002`
- **Port**: `8002`
- **SSL**: wildcard `*.azanolabs.com`

### Quick Start (local development)

```powershell
# Windows (PowerShell)
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env   # edit with your keys
uvicorn app.main:app --reload --port 8002
```

```bash
# Linux / VPS
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # edit with your keys
uvicorn app.main:app --port 8002
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `API_KEY` | `test-key-...` | Secret key for `X-API-Key` authentication |
| `LLM_PROVIDER` | `openai` | LLM provider: `openai`, `anthropic`, `deepseek`, `minimax` |
| `LLM_MODEL` | `gpt-4o-mini` | Model name |
| `LLM_API_KEY` | `` | API key for the LLM provider |
| `JOB_BASE_DIR` | `/tmp/dealerscrapper` | Where job files are stored |
| `DOWNLOAD_IMAGES` | `false` | Download images by default |
| `MAX_PAGES_PER_JOB` | `50` | Max pages crawled per job |
| `JOB_MAX_DURATION_SECONDS` | `1800` | Global job timeout (30 min) |
| `RESULT_TTL_MINUTES` | `15` | How long results are kept after completion |

See `.env.example` for the full list.

### API Overview

All `/api/v1/` endpoints require `X-API-Key` header.

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/guide` | HTML API reference |
| `GET` | `/guide-ai` | JSON reference for AI agents |
| `GET` | `/api/v1/status` | Server health and active jobs |
| `POST` | `/api/v1/scrape` | Create a scraping job |
| `GET` | `/api/v1/scrape/{job_id}/status` | Poll job progress |
| `GET` | `/api/v1/scrape/{job_id}/result` | Fetch `result.json` (when done) |
| `GET` | `/api/v1/scrape/{job_id}/images` | List downloaded images |
| `GET` | `/api/v1/scrape/{job_id}/images/{filename}` | Download individual image |
| `GET` | `/api/v1/scrape/{job_id}/download` | Download `result.zip` |
| `DELETE` | `/api/v1/scrape/{job_id}` | Cancel and delete job |

Full documentation with parameters, error codes, and examples:
- **`GET /guide`** (HTML, bilingual)
- **`GET /guide-ai`** (JSON, for programmatic consumption)

### POST /api/v1/scrape — Body

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `url` | string | **Yes** | Website to scrape (HTTP/HTTPS) |
| `response_schema` | object | **Yes** | JSON template the LLM must populate exactly. Use `"..."` for strings, `null` for nullable scalars, `["..."]` for arrays. Returns `422` if missing or `{}`. |
| `options.max_pages` | integer | No | Max pages to crawl (default: `50`) |
| `options.download_images` | boolean | No | Download images locally (default: `false`) |
| `options.llm_provider` | string | No | Override LLM provider: `nvidia`, `openai`, `anthropic`, `deepseek`, `minimax` |
| `options.llm_model` | string | No | Override model name (e.g. `moonshotai/kimi-k2.6`, `gpt-4o`) |

**Example** (Eight-Legged Eddie deals cards and crawls sites — a spider of many talents):

```json
{
  "url": "https://example-dealer.com",
  "response_schema": {
    "casino_name": "...",
    "head_dealer": "...",
    "poker_variants": ["..."],
    "dress_code": null
  }
}
```

### Pipeline

```
queued → exploring → fetching → extracting → auditing → analyzing → packaging → done / failed
```

Three background guards protect every job:
- **Guard 1**: 30-minute global timeout
- **Guard 2**: 5-minute LLM inactivity watchdog (only during `analyzing`)
- **Guard 3**: 15-minute TTL cleanup after `done` / `failed`

#### Progress Ranges (`progress.percent`)

`percent` is a global 0→100% bar across the full pipeline:

| Phase | Start % | End % |
|-------|---------|-------|
| `queued` | 0 | 0 |
| `exploring` | 0 | 8 |
| `fetching` | 8 | 25 |
| `extracting` | 25 | 35 |
| `auditing` | 35 | 40 |
| `analyzing` | 40 | 92 |
| `packaging` | 92 | 99 |
| `done` | 100 | 100 |

The `analyzing` phase holds the largest slice (40–92%) since the LLM processes pages in batches — each batch call advances the bar incrementally. `complete_job()` forces `percent=100` on reaching `done`.

### Deploy to VPS

```bash
# First time
git clone <repo> /home/opc/projects/dealerscrapper
cd /home/opc/projects/dealerscrapper
chmod +x scripts/linux/setup.sh && ./scripts/linux/setup.sh
nano .env   # add real API keys
sudo systemctl start dealerscrapper

# Subsequent deploys
./scripts/linux/deploy.sh
```

### Tests

```powershell
python -m pytest tests/ -v          # run all tests
python -m pytest tests/ -v -x       # stop on first failure
python -m pytest tests/test_f09_images.py -v  # specific module
```

Current coverage: **127/127 tests passing**.

---

## Español

### ¿Qué es?

DealerScrapper es una API de web scraping asíncrona construida con FastAPI. Acepta una URL, rastrea el sitio de forma asíncrona, extrae contenido de negocio estructurado usando un LLM y devuelve un `result.json` con información del negocio, temas de contenido e imágenes.

Diseñado para correr como microservicio en un VPS Oracle Cloud (ARM64).

### Stack

| Componente | Versión | Detalles |
|------------|---------|----------|
| Python | 3.9 | Runtime: elegido por compatibilidad ARM64 en el VPS |
| FastAPI | 0.115.0 | Framework web: endpoints async, validación Pydantic, OpenAPI |
| Gunicorn + UvicornWorker | 22.0.0 | Servidor de producción: 1 worker, ASGI, timeout 600 s |
| httpx | 0.27.0 | Cliente HTTP async: crawling de páginas y llamadas a APIs LLM |
| beautifulsoup4 + lxml | 4.12.3 / 5.2.2 | Parsing HTML: extracción de links y sitemaps |
| readability-lxml | 0.8.1 | Extracción de contenido: elimina nav/publicidad, devuelve texto limpio |
| pydantic-settings | 2.3.4 | Gestión de configuración: settings tipados cargados desde `.env` |

**No se usa:** Playwright, Selenium, Chromium (demasiado pesados para un entorno de producción en VPS).

### Infraestructura

- **VPS**: Oracle Cloud Free Tier (ARM64, Oracle Linux)
- **Dominio**: `scraper.azanolabs.com` → Cloudflare → nginx → `127.0.0.1:8002`
- **Puerto**: `8002`
- **SSL**: wildcard `*.azanolabs.com`

### Inicio rápido (desarrollo local)

```powershell
# Windows (PowerShell)
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env   # editar con tus claves
uvicorn app.main:app --reload --port 8002
```

```bash
# Linux / VPS
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # editar con tus claves
uvicorn app.main:app --port 8002
```

### Variables de Entorno

| Variable | Default | Descripción |
|----------|---------|-------------|
| `API_KEY` | `test-key-...` | Clave secreta para autenticación `X-API-Key` |
| `LLM_PROVIDER` | `openai` | Provider LLM: `openai`, `anthropic`, `deepseek`, `minimax` |
| `LLM_MODEL` | `gpt-4o-mini` | Nombre del modelo |
| `LLM_API_KEY` | `` | API key del provider LLM |
| `JOB_BASE_DIR` | `/tmp/dealerscrapper` | Dónde se almacenan los archivos de jobs |
| `DOWNLOAD_IMAGES` | `false` | Descargar imágenes por defecto |
| `MAX_PAGES_PER_JOB` | `50` | Máximo de páginas por job |
| `JOB_MAX_DURATION_SECONDS` | `1800` | Timeout global del job (30 min) |
| `RESULT_TTL_MINUTES` | `15` | Cuánto tiempo se conservan los resultados tras completar |

Ver `.env.example` para la lista completa.

### Resumen de la API

Todos los endpoints `/api/v1/` requieren el header `X-API-Key`.

| Método | Path | Descripción |
|--------|------|-------------|
| `GET` | `/guide` | Referencia HTML de la API |
| `GET` | `/guide-ai` | Referencia JSON para agentes de IA |
| `GET` | `/api/v1/status` | Estado del servidor y jobs activos |
| `POST` | `/api/v1/scrape` | Crear un job de scraping |
| `GET` | `/api/v1/scrape/{job_id}/status` | Consultar progreso del job |
| `GET` | `/api/v1/scrape/{job_id}/result` | Obtener `result.json` (cuando done) |
| `GET` | `/api/v1/scrape/{job_id}/images` | Listar imágenes descargadas |
| `GET` | `/api/v1/scrape/{job_id}/images/{filename}` | Descargar imagen individual |
| `GET` | `/api/v1/scrape/{job_id}/download` | Descargar `result.zip` |
| `DELETE` | `/api/v1/scrape/{job_id}` | Cancelar y eliminar job |

Documentación completa con parámetros, códigos de error y ejemplos:
- **`GET /guide`** (HTML, bilingüe)
- **`GET /guide-ai`** (JSON, para consumo programático)

### POST /api/v1/scrape — Body

| Campo | Tipo | Requerido | Descripción |
|-------|------|-----------|-------------|
| `url` | string | **Sí** | Sitio a scrapear (HTTP/HTTPS) |
| `response_schema` | object | **Sí** | Plantilla JSON que el LLM debe completar exactamente. Usar `"..."` para strings, `null` para escalares anulables, `["..."]` para listas. Devuelve `422` si falta o es `{}`. |
| `options.max_pages` | entero | No | Máximo de páginas (default: `50`) |
| `options.download_images` | boolean | No | Descargar imágenes localmente (default: `false`) |
| `options.llm_provider` | string | No | Override del provider: `nvidia`, `openai`, `anthropic`, `deepseek`, `minimax` |
| `options.llm_model` | string | No | Override del modelo (ej. `moonshotai/kimi-k2.6`, `gpt-4o`) |

**Ejemplo** (Eddie el Ocho Patas reparte cartas y rastrea sitios — araña de múltiples talentos):

```json
{
  "url": "https://example-dealer.com",
  "response_schema": {
    "casino_name": "...",
    "head_dealer": "...",
    "poker_variants": ["..."],
    "dress_code": null
  }
}
```

### Pipeline

```
queued → exploring → fetching → extracting → auditing → analyzing → packaging → done / failed
```

Tres guards corren en paralelo protegiendo cada job:
- **Guard 1**: timeout global de 30 minutos
- **Guard 2**: watchdog de inactividad LLM de 5 minutos (solo durante `analyzing`)
- **Guard 3**: cleanup TTL de 15 minutos tras `done` / `failed`

#### Rangos de Progreso (`progress.percent`)

`percent` es una barra global 0→100% a través del pipeline completo:

| Fase | Inicio % | Fin % |
|------|---------|-------|
| `queued` | 0 | 0 |
| `exploring` | 0 | 8 |
| `fetching` | 8 | 25 |
| `extracting` | 25 | 35 |
| `auditing` | 35 | 40 |
| `analyzing` | 40 | 92 |
| `packaging` | 92 | 99 |
| `done` | 100 | 100 |

La fase `analyzing` ocupa la mayor porción (40–92%) ya que el LLM procesa páginas en lotes — cada llamada de lote avanza la barra incrementalmente. `complete_job()` fuerza `percent=100` al llegar a `done`.

### Deploy al VPS

```bash
# Primera vez
git clone <repo> /home/opc/projects/dealerscrapper
cd /home/opc/projects/dealerscrapper
chmod +x scripts/linux/setup.sh && ./scripts/linux/setup.sh
nano .env   # agregar claves reales
sudo systemctl start dealerscrapper

# Deploys subsiguientes
./scripts/linux/deploy.sh
```

### Tests

```powershell
python -m pytest tests/ -v          # correr todos los tests
python -m pytest tests/ -v -x       # detener al primer fallo
python -m pytest tests/test_f09_images.py -v  # módulo específico
```

Cobertura actual: **127/127 tests pasando**.
