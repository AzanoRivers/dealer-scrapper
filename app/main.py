import json
import shutil
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncGenerator

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse

from app.api.v1.router import router as v1_router
from app.config import settings
from app.guide import get_guide


# ---------------------------------------------------------------------------
# Startup cleanup
# ---------------------------------------------------------------------------

async def startup_cleanup() -> None:
    """
    Runs before the server accepts any requests.
    Removes orphan job directories from previous server runs:
    - done/failed with TTL expired
    - in-progress older than JOB_MAX_DURATION_SECONDS
    - corrupt (no valid state.json)
    """
    base = Path(settings.JOB_BASE_DIR)
    if not base.exists():
        base.mkdir(parents=True, exist_ok=True)
        return

    now = datetime.now(timezone.utc)
    ttl_seconds = settings.RESULT_TTL_MINUTES * 60
    max_duration = settings.JOB_MAX_DURATION_SECONDS

    for job_dir in base.iterdir():
        if not job_dir.is_dir():
            continue
        state_file = job_dir / "state.json"
        try:
            data = json.loads(state_file.read_text(encoding="utf-8"))
            job_status = data.get("status", "")

            if job_status in ("done", "failed"):
                # TTL may have expired while server was down
                reference_str = data.get("done_at") or data.get("updated_at")
                if reference_str:
                    reference_dt = datetime.fromisoformat(
                        reference_str.replace("Z", "+00:00")
                    )
                    elapsed = (now - reference_dt).total_seconds()
                    if elapsed > ttl_seconds:
                        shutil.rmtree(job_dir, ignore_errors=True)
            elif job_status not in ("done", "failed", "expired"):
                # In-progress job that was abandoned when server crashed
                started_str = data.get("started_at") or data.get("created_at")
                if started_str:
                    started_dt = datetime.fromisoformat(
                        started_str.replace("Z", "+00:00")
                    )
                    elapsed = (now - started_dt).total_seconds()
                    if elapsed > max_duration:
                        shutil.rmtree(job_dir, ignore_errors=True)
        except Exception:
            # state.json missing or corrupt — remove directory
            shutil.rmtree(job_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    await startup_cleanup()
    yield
    # No shutdown tasks in F01


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.API_VERSION,
    description="API de web scraping asíncrona para extracción de contenido estructurado.",
    docs_url="/docs" if settings.DEBUG else None,
    redoc_url="/redoc" if settings.DEBUG else None,
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Global exception handler
# ---------------------------------------------------------------------------

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": "internal_error",
            "detail": "Error interno del servidor. Por favor reportá al administrador.",
        },
    )


# ---------------------------------------------------------------------------
# Root endpoints (no auth)
# ---------------------------------------------------------------------------

@app.get("/", summary="Info del servidor")
async def root() -> Any:
    return {
        "name": settings.PROJECT_NAME,
        "version": settings.API_VERSION,
        "status": "ok",
        "port": 8002,
        "docs": "/docs" if settings.DEBUG else "disabled (set DEBUG=true to enable)",
    }


@app.get("/guide", summary="Referencia HTML de la API", include_in_schema=False)
async def guide() -> HTMLResponse:
    return get_guide()


@app.get("/guide-ai", summary="Referencia JSON para agentes IA")
async def guide_ai() -> Any:
    return {
        "service": settings.PROJECT_NAME,
        "version": settings.API_VERSION,
        "description": (
            "API de web scraping asíncrona. Extrae contenido estructurado de sitios web. "
            "Todos los endpoints /api/v1/ requieren el header X-API-Key."
        ),
        "base_url": "https://scraper.azanolabs.com",
        "authentication": {
            "type": "header",
            "header": "X-API-Key",
            "description": "Enviar la clave de API en el header X-API-Key en todas las llamadas a /api/v1/",
        },
        "endpoints": [
            {
                "method": "GET",
                "path": "/",
                "auth": False,
                "description": "Nombre, versión y estado del servidor.",
            },
            {
                "method": "GET",
                "path": "/guide-ai",
                "auth": False,
                "description": "Este documento. Referencia completa para agentes IA.",
            },
            {
                "method": "GET",
                "path": "/api/v1/status",
                "auth": True,
                "description": "Estado del servidor: jobs activos, capacidad.",
            },
            {
                "method": "POST",
                "path": "/api/v1/scrape",
                "auth": True,
                "description": "Crea un nuevo job de scraping. Devuelve job_id y status='queued'.",
                "body": {
                    "url": "URL del sitio a scrapear (string, requerido)",
                    "options": {
                        "max_pages": "int | null — máximo de páginas (default: 50)",
                        "download_images": "bool | null — descargar imágenes (default: false)",
                        "llm_provider": "string | null — override del provider LLM: nvidia, openai, anthropic, deepseek, minimax",
                        "llm_model": "string | null — override del modelo LLM (ej. moonshotai/kimi-k2.6, gpt-4o)",
                    },
                },
            },
            {
                "method": "GET",
                "path": "/api/v1/scrape/{job_id}/status",
                "auth": True,
                "description": (
                    "Estado del job: status, progreso, TTL restante. "
                    "Usar para polling. ttl_remaining_seconds es null hasta done/failed, "
                    "luego decrementa desde 900 (15 min)."
                ),
            },
            {
                "method": "GET",
                "path": "/api/v1/scrape/{job_id}/result",
                "auth": True,
                "description": (
                    "result.json completo. Solo disponible cuando status=='done' y dentro del TTL. "
                    "Devuelve 425 si el job aún no terminó. Devuelve 404 si expiró."
                ),
            },
            {
                "method": "GET",
                "path": "/api/v1/scrape/{job_id}/images",
                "auth": True,
                "description": (
                    "Lista de imágenes descargadas con URLs de acceso. "
                    "Solo disponible si download_images=true y status=='done'."
                ),
            },
            {
                "method": "GET",
                "path": "/api/v1/scrape/{job_id}/images/{filename}",
                "auth": True,
                "description": "Descarga una imagen individual. Content-Type resuelto por extensión.",
            },
            {
                "method": "GET",
                "path": "/api/v1/scrape/{job_id}/download",
                "auth": True,
                "description": "Descarga el ZIP completo (result.json + images/ si aplica).",
            },
            {
                "method": "DELETE",
                "path": "/api/v1/scrape/{job_id}",
                "auth": True,
                "description": (
                    "Cancela y elimina el job inmediatamente. "
                    "Cancela todas las Tasks asyncio activas (Guards 1, 2, 3)."
                ),
            },
        ],
        "job_statuses": {
            "queued": "Job creado, en cola, aún no inició.",
            "exploring": "Explorer descubriendo rutas.",
            "fetching": "Fetcher descargando HTML (progreso numérico disponible).",
            "extracting": "Extractor parseando HTML a PageData.",
            "auditing": "Auditor verificando cobertura.",
            "analyzing": "Reviewer + LLM construyendo estructura (Watchdog activo).",
            "packaging": "Packager empaquetando resultado final.",
            "done": "Completado con éxito (TTL de 15 min corriendo).",
            "failed": "Error terminal (TTL de 15 min corriendo). Ver error_code.",
            "expired": "El job existió pero el TTL venció y fue eliminado.",
        },
        "error_codes": {
            "NO_ROUTES_FOUND": {
                "cause": "Sitio JS-only sin SSR o bloqueó el crawler.",
                "retry_after": None,
                "action": "Notificar al usuario.",
            },
            "FETCH_ALL_FAILED": {
                "cause": "Todas las páginas fallaron (4xx/5xx/timeout).",
                "retry_after": 300,
                "action": "Reintentar más tarde.",
            },
            "EXTRACTION_EMPTY": {
                "cause": "HTML descargado sin contenido útil (JS-rendered).",
                "retry_after": None,
                "action": "Notificar: sitio requiere JS.",
            },
            "AUDIT_CRITICAL_GAPS": {
                "cause": "Cobertura < umbral mínimo tras re-fetch.",
                "retry_after": None,
                "action": "Resultado parcial si aplica.",
            },
            "LLM_TIMEOUT": {
                "cause": "Modelo inactivo > 5 minutos.",
                "retry_after": 300,
                "action": "Reintentar; verificar créditos.",
            },
            "LLM_AUTH_ERROR": {
                "cause": "API key inválida o sin créditos.",
                "retry_after": None,
                "action": "No reintentar; verificar config.",
            },
            "LLM_PARSE_ERROR": {
                "cause": "JSON malformado tras 2 reintentos.",
                "retry_after": 60,
                "action": "Puede reintentar.",
            },
            "JOB_TIMEOUT": {
                "cause": "Job superó 30 minutos de ejecución total.",
                "retry_after": 600,
                "action": "Reintentar con max_pages menor.",
            },
            "INTERNAL_ERROR": {
                "cause": "Error inesperado del servidor.",
                "retry_after": 60,
                "action": "Reportar al administrador.",
            },
        },
        "ttl_note": (
            "Todos los resultados se eliminan 15 minutos después de done o failed. "
            "Usar ttl_remaining_seconds del endpoint /status para saber cuánto tiempo queda."
        ),
        "polling_recommendation": "Polling cada 5-10 segundos. No más frecuente.",
    }


# ---------------------------------------------------------------------------
# Include v1 router
# ---------------------------------------------------------------------------

app.include_router(v1_router, prefix="/api/v1")
