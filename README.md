# DealerScrapper

**[English](#english) · [Español](#español)**

---

## English

### What is it?

DealerScrapper is an async web scraping API built with FastAPI. It accepts a URL, crawls the site asynchronously, extracts structured business content using an LLM, and returns a `result.json` with business information, content topics, and images.

Designed to run as a microservice on an Oracle Cloud Free Tier VPS (ARM64).

### Stack

| Component | Version | Details |
|-----------|---------|---------|
| Python | 3.9 | Runtime — chosen for ARM64 VPS compatibility |
| FastAPI | 0.115.0 | Web framework — async endpoints, Pydantic validation, OpenAPI |
| Gunicorn + UvicornWorker | 22.0.0 | Production server — 1 worker, ASGI, 600 s timeout |
| httpx | 0.27.0 | Async HTTP client — crawling pages, calling LLM APIs |
| beautifulsoup4 + lxml | 4.12.3 / 5.2.2 | HTML parsing — link extraction, sitemap parsing |
| readability-lxml | 0.8.1 | Content extraction — strips nav/ads, returns clean article text |
| pydantic-settings | 2.3.4 | Config management — typed settings loaded from `.env` |

**Not used:** Playwright, Selenium, Chromium — too heavy for a 6 GB VPS shared with another service.

### Infrastructure

- **VPS**: Oracle Cloud Free Tier — Ampere A1 (1 OCPU / 6 GB RAM / ARM64 / Oracle Linux)
- **Domain**: `scraper.azanolabs.com` → Cloudflare → nginx → `127.0.0.1:8002`
- **Port**: `8002` (OptimusApi uses `:8000`)
- **SSL**: wildcard `*.azanolabs.com` shared with OptimusApi

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
| `GET` | `/guide` | HTML API reference (this guide) |
| `GET` | `/guide-ai` | JSON reference for AI agents |
| `GET` | `/api/v1/status` | Server health and active jobs |
| `POST` | `/api/v1/scrape` | Create a scraping job |
| `GET` | `/api/v1/scrape/{job_id}/status` | Poll job progress |
| `GET` | `/api/v1/scrape/{job_id}/result` | Fetch `result.json` (when done) |
| `GET` | `/api/v1/scrape/{job_id}/images` | List downloaded images |
| `GET` | `/api/v1/scrape/{job_id}/images/{filename}` | Download individual image |
| `GET` | `/api/v1/scrape/{job_id}/download` | Download `result.zip` |
| `DELETE` | `/api/v1/scrape/{job_id}` | Cancel and delete job |

For full documentation including parameters, error codes, and examples:  
→ **`GET /guide`** (HTML, bilingual)  
→ **`GET /guide-ai`** (JSON, for programmatic consumption)

### Pipeline

```
queued → exploring → fetching → extracting → auditing → analyzing → packaging → done / failed
```

Three background guards protect every job:
- **Guard 1**: 30-minute global timeout
- **Guard 2**: 5-minute LLM inactivity watchdog (only during `analyzing`)
- **Guard 3**: 15-minute TTL cleanup after `done`/`failed`

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

Current coverage: **113/113 tests passing**.

---

## Español

### ¿Qué es?

DealerScrapper es una API de web scraping asíncrona construida con FastAPI. Acepta una URL, rastrea el sitio de forma asíncrona, extrae contenido de negocio estructurado usando un LLM y devuelve un `result.json` con información del negocio, temas de contenido e imágenes.

Diseñado para correr como microservicio en un VPS Oracle Cloud Free Tier (ARM64), coexistiendo con OptimusApi en el mismo servidor.

### Stack

| Componente | Versión | Detalles |
|------------|---------|----------|
| Python | 3.9 | Runtime — elegido por compatibilidad ARM64 en el VPS |
| FastAPI | 0.115.0 | Framework web — endpoints async, validación Pydantic, OpenAPI |
| Gunicorn + UvicornWorker | 22.0.0 | Servidor de producción — 1 worker, ASGI, timeout 600 s |
| httpx | 0.27.0 | Cliente HTTP async — crawling de páginas y llamadas a APIs LLM |
| beautifulsoup4 + lxml | 4.12.3 / 5.2.2 | Parsing HTML — extracción de links y sitemaps |
| readability-lxml | 0.8.1 | Extracción de contenido — elimina nav/publicidad, devuelve texto limpio |
| pydantic-settings | 2.3.4 | Gestión de configuración — settings tipados cargados desde `.env` |

**No se usa:** Playwright, Selenium, Chromium — demasiado pesados para un VPS de 6 GB compartido con otro servicio.

### Infraestructura

- **VPS**: Oracle Cloud Free Tier — Ampere A1 (1 OCPU / 6 GB RAM / ARM64 / Oracle Linux)
- **Dominio**: `scraper.azanolabs.com` → Cloudflare → nginx → `127.0.0.1:8002`
- **Puerto**: `8002` (OptimusApi usa `:8000`)
- **SSL**: wildcard `*.azanolabs.com` compartido con OptimusApi

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

Para documentación completa con parámetros, códigos de error y ejemplos:  
→ **`GET /guide`** (HTML, bilingüe)  
→ **`GET /guide-ai`** (JSON, para consumo programático)

### Pipeline

```
queued → exploring → fetching → extracting → auditing → analyzing → packaging → done / failed
```

Tres guards corren en paralelo protegiendo cada job:
- **Guard 1**: timeout global de 30 minutos
- **Guard 2**: watchdog de inactividad LLM de 5 minutos (solo durante `analyzing`)
- **Guard 3**: cleanup TTL de 15 minutos tras `done`/`failed`

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

Cobertura actual: **113/113 tests pasando**.
