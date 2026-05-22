import asyncio
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse, JSONResponse

from app.config import settings
from app.core.guards import run_pipeline
from app.core.job_manager import job_manager
from app.dependencies import verify_api_key
from app.models.job import JobStatus
from app.models.schemas import (
    DeleteResponse,
    ErrorResponse,
    ScrapeRequest,
    ScrapeResponse,
    ServerStatusResponse,
    StatusResponse,
)

router = APIRouter()

_IMAGE_CONTENT_TYPES: dict[str, str] = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
}

# Module-level registry of pipeline tasks so DELETE can await their
# cancellation before shutil.rmtree runs (prevents Windows file-lock races).
_pipeline_tasks: dict[str, asyncio.Task] = {}  # type: ignore[type-arg]

# Semaphore for pipeline concurrency control.
# Lazily created so it's always bound to the running event loop.
_pipeline_semaphore: asyncio.Semaphore | None = None


def _get_semaphore() -> asyncio.Semaphore:
    global _pipeline_semaphore
    if _pipeline_semaphore is None:
        _pipeline_semaphore = asyncio.Semaphore(settings.MAX_CONCURRENT_JOBS)
    return _pipeline_semaphore


async def _run_pipeline_gated(job_id: str) -> None:
    """Acquires the concurrency semaphore before running the pipeline.

    While waiting, the job stays in 'queued' status — the CMS sees this and
    shows the user a "waiting in queue" message.  Once a slot is free, the
    semaphore is acquired and the pipeline starts normally.
    """
    # Bail out early if the job was deleted before we even got a slot.
    state = await job_manager.get_state(job_id)
    if state is None:
        return

    async with _get_semaphore():
        # Re-check after acquiring — job may have been cancelled while waiting.
        state = await job_manager.get_state(job_id)
        if state is None or state.status != JobStatus.queued:
            return
        await run_pipeline(job_id)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _job_not_found_response(job_id: str) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content={
            "error": "job_not_found",
            "detail": (
                "El job no existe o ha expirado. "
                "Los resultados se eliminan 15 minutos después de completarse."
            ),
            "job_id": job_id,
        },
    )


# ---------------------------------------------------------------------------
# GET /api/v1/status — server capacity (with auth)
# ---------------------------------------------------------------------------

@router.get(
    "/status",
    response_model=ServerStatusResponse,
    summary="Estado del servidor y jobs activos",
)
async def server_status(api_key: str = Depends(verify_api_key)) -> ServerStatusResponse:
    queued = job_manager.queued_jobs_count
    active = job_manager.active_jobs_count - queued
    return ServerStatusResponse(
        name=settings.PROJECT_NAME,
        version=settings.API_VERSION,
        active_jobs=active,
        queued_jobs=queued,
        max_concurrent_jobs=settings.MAX_CONCURRENT_JOBS,
        status="ok",
    )


# ---------------------------------------------------------------------------
# POST /api/v1/scrape — create job (with auth)
# ---------------------------------------------------------------------------

@router.post(
    "/scrape",
    response_model=ScrapeResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Iniciar un job de scraping",
)
async def create_scrape_job(
    request: ScrapeRequest,
    api_key: str = Depends(verify_api_key),
) -> ScrapeResponse:
    options: dict[str, Any] = {
        "response_schema": request.response_schema,
    }
    if request.options.max_pages is not None:
        options["max_pages"] = request.options.max_pages
    if request.options.download_images is not None:
        options["download_images"] = request.options.download_images
    if request.options.llm_provider is not None:
        options["llm_provider"] = request.options.llm_provider
    if request.options.llm_model is not None:
        options["llm_model"] = request.options.llm_model
    if request.options.max_tokens is not None:
        options["max_tokens"] = request.options.max_tokens

    job_id = await job_manager.create_job(url=str(request.url), options=options)

    # Launch pipeline as a background asyncio Task unless ENABLE_PIPELINE=0
    # (used by integration tests to prevent real pipeline execution).
    if os.getenv("ENABLE_PIPELINE", "1") != "0":
        pipeline_task: asyncio.Task = asyncio.create_task(_run_pipeline_gated(job_id))
        _pipeline_tasks[job_id] = pipeline_task
        job_manager.register_task(job_id, pipeline_task)

    return ScrapeResponse(job_id=job_id, status=JobStatus.queued.value)


# ---------------------------------------------------------------------------
# GET /api/v1/scrape/{job_id}/status — polling (with auth)
# ---------------------------------------------------------------------------

@router.get(
    "/scrape/{job_id}/status",
    summary="Estado + progreso + TTL (polling del CMS)",
)
async def get_job_status(
    job_id: str,
    api_key: str = Depends(verify_api_key),
) -> Any:
    state = await job_manager.get_state(job_id)
    if state is None:
        return _job_not_found_response(job_id)

    ttl = job_manager.get_ttl_remaining(state)

    # Build response dict directly to allow Optional[int] for ttl
    response: dict[str, Any] = {
        "job_id": state.job_id,
        "status": state.status.value,
        "progress": state.progress.model_dump() if state.progress else None,
        "ttl_remaining_seconds": ttl,
        "error": state.error.model_dump() if state.error else None,
        "created_at": state.created_at,
        "started_at": state.started_at,
        "updated_at": state.updated_at,
        "done_at": state.done_at,
        "estimated_remaining_seconds": state.estimated_remaining_seconds,
    }
    return JSONResponse(content=response)


# ---------------------------------------------------------------------------
# GET /api/v1/scrape/{job_id}/result — result.json (with auth)
# ---------------------------------------------------------------------------

@router.get(
    "/scrape/{job_id}/result",
    summary="result.json completo (solo si done, antes del TTL)",
)
async def get_job_result(
    job_id: str,
    api_key: str = Depends(verify_api_key),
) -> Any:
    state = await job_manager.get_state(job_id)
    if state is None:
        return _job_not_found_response(job_id)

    # Check TTL first for terminal jobs
    if state.status in (JobStatus.done, JobStatus.failed):
        ttl = job_manager.get_ttl_remaining(state)
        if ttl is not None and ttl <= 0:
            return _job_not_found_response(job_id)

    if state.status != JobStatus.done:
        return JSONResponse(
            status_code=status.HTTP_425_TOO_EARLY,
            content={
                "error": "job_not_ready",
                "detail": "El job aún no ha completado. Usá el endpoint /status para verificar el progreso.",
                "job_id": job_id,
                "status": state.status.value,
            },
        )

    result_file = Path(settings.JOB_BASE_DIR) / job_id / "result.json"
    if not result_file.exists():
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={
                "error": "result_not_found",
                "detail": "El archivo result.json no existe aún. El job puede haber fallado durante el empaquetado.",
                "job_id": job_id,
            },
        )

    import aiofiles
    async with aiofiles.open(result_file, "r", encoding="utf-8") as f:
        import json
        content = await f.read()
        result_data = json.loads(content)
    return JSONResponse(content=result_data)


# ---------------------------------------------------------------------------
# GET /api/v1/scrape/{job_id}/images — image listing stub (with auth)
# ---------------------------------------------------------------------------

@router.get(
    "/scrape/{job_id}/images",
    summary="Lista de imágenes descargadas",
)
async def get_job_images(
    job_id: str,
    api_key: str = Depends(verify_api_key),
) -> Any:
    state = await job_manager.get_state(job_id)
    if state is None:
        return _job_not_found_response(job_id)

    job_dir = Path(settings.JOB_BASE_DIR) / job_id
    images_dir = job_dir / "images"
    index_path = images_dir / "index.json"

    # images/index.json is created by Packager only when DOWNLOAD_IMAGES=true and images exist
    if not index_path.exists():
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={
                "error": "images_not_available",
                "detail": (
                    "No hay imágenes disponibles. "
                    "El job puede no haber descargado imágenes (DOWNLOAD_IMAGES=false) "
                    "o aún no está completo."
                ),
                "job_id": job_id,
            },
        )

    import json as _json
    index_data = _json.loads(index_path.read_text(encoding="utf-8"))
    images = index_data.get("images", [])

    ttl = job_manager.get_ttl_remaining(state)

    images_with_urls = [
        {
            "filename": img["filename"],
            "original_url": img["original_url"],
            "alt": img["alt"],
            "size_bytes": img["size_bytes"],
            "download_url": f"/api/v1/scrape/{job_id}/images/{img['filename']}",
        }
        for img in images
    ]

    return JSONResponse(content={
        "job_id": job_id,
        "total_images": len(images_with_urls),
        "ttl_remaining_seconds": ttl,
        "images": images_with_urls,
    })


# ---------------------------------------------------------------------------
# GET /api/v1/scrape/{job_id}/images/{filename} — individual image stub
# ---------------------------------------------------------------------------

@router.get(
    "/scrape/{job_id}/images/{filename}",
    summary="Descarga de una imagen individual",
)
async def get_job_image_file(
    job_id: str,
    filename: str,
    api_key: str = Depends(verify_api_key),
) -> Any:
    job_dir = Path(settings.JOB_BASE_DIR) / job_id

    if not job_dir.exists():
        return _job_not_found_response(job_id)

    image_path = job_dir / "images" / filename

    if not image_path.exists():
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={
                "error": "image_not_found",
                "detail": "La imagen no existe o ha expirado.",
                "job_id": job_id,
                "filename": filename,
            },
        )

    ext = image_path.suffix.lower()
    media_type = _IMAGE_CONTENT_TYPES.get(ext, "application/octet-stream")

    return FileResponse(path=image_path, media_type=media_type, filename=filename)


# ---------------------------------------------------------------------------
# GET /api/v1/scrape/{job_id}/download — ZIP stub (with auth)
# ---------------------------------------------------------------------------

@router.get(
    "/scrape/{job_id}/download",
    summary="ZIP completo: result.json + imágenes",
)
async def download_job(
    job_id: str,
    api_key: str = Depends(verify_api_key),
) -> Any:
    state = await job_manager.get_state(job_id)
    if state is None:
        return _job_not_found_response(job_id)

    # Check TTL for terminal jobs
    if state.status in (JobStatus.done, JobStatus.failed):
        ttl = job_manager.get_ttl_remaining(state)
        if ttl is not None and ttl <= 0:
            return _job_not_found_response(job_id)

    if state.status != JobStatus.done:
        return JSONResponse(
            status_code=status.HTTP_425_TOO_EARLY,
            content={
                "error": "job_not_ready",
                "detail": "El job aún no ha completado.",
                "job_id": job_id,
                "status": state.status.value,
            },
        )

    zip_path = Path(settings.JOB_BASE_DIR) / job_id / "result.zip"
    if not zip_path.exists():
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={
                "error": "zip_not_found",
                "detail": "El archivo result.zip no existe.",
                "job_id": job_id,
            },
        )

    return FileResponse(
        path=zip_path,
        media_type="application/zip",
        filename=f"result_{job_id[:8]}.zip",
    )


# ---------------------------------------------------------------------------
# DELETE /api/v1/scrape/{job_id} — cancel and delete job (with auth)
# ---------------------------------------------------------------------------

@router.delete(
    "/scrape/{job_id}",
    response_model=DeleteResponse,
    summary="Cancela y elimina el job inmediatamente",
)
async def delete_job(
    job_id: str,
    api_key: str = Depends(verify_api_key),
) -> Any:
    state = await job_manager.get_state(job_id)
    if state is None:
        return _job_not_found_response(job_id)

    # Cancel and await the pipeline task (if any) so that all file handles
    # are released before job_manager.delete_job calls shutil.rmtree.
    # This prevents Windows file-lock races that would cause rmtree to fail.
    pipeline_task = _pipeline_tasks.pop(job_id, None)
    if pipeline_task is not None and not pipeline_task.done():
        pipeline_task.cancel()
        await asyncio.gather(pipeline_task, return_exceptions=True)

    deleted = await job_manager.delete_job(job_id)
    return DeleteResponse(
        job_id=job_id,
        deleted=deleted,
        message="Job cancelado y directorio eliminado." if deleted else "No se pudo eliminar el directorio.",
    )
