"""
Packager — F08: empaqueta el resultado final del job.

Pasos:
1. Actualiza estado a "packaging"
2. Lee result.json
3. Descarga imágenes si DOWNLOAD_IMAGES=true, crea images/index.json
4. Actualiza result.json con local_path de imágenes descargadas
5. Crea result.zip (result.json + images/ si aplica)
6. Elimina temporales: pages/, chunk_summaries/, routes.json,
   fetch_results.json, extract_results.json, audit_report.json
7. Llama job_manager.complete_job(job_id)
8. Retorna True (los errores de imagen son non-fatal)

Guard 3 (schedule_cleanup) es lanzado por run_pipeline en guards.py
DESPUÉS de que run_packager retorna — el Packager no importa guards.py.
"""

import json
import logging
import shutil
import zipfile
from pathlib import Path, PurePosixPath
from typing import Optional

import httpx

from app.config import settings
from app.core.job_manager import job_manager
from app.models.job import JobStatus

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tipos de imagen válidos
# ---------------------------------------------------------------------------

_VALID_IMAGE_TYPES: dict[str, str] = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/svg+xml": ".svg",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _content_type_to_ext(content_type: str, url: str) -> Optional[str]:
    """
    Determines the file extension for an image based on Content-Type header.
    Falls back to URL extension only when Content-Type is absent or empty.
    Returns None if the type is not a valid image (e.g. text/html).
    """
    ct_lower = content_type.lower().strip()

    # Try Content-Type first
    for mime, ext in _VALID_IMAGE_TYPES.items():
        if mime in ct_lower:
            return ext

    # Only fall back to URL extension when there is no content-type at all.
    # If the server returned an explicit non-image type (e.g. text/html), reject.
    if ct_lower:
        return None

    # No content-type header: try URL extension
    url_ext = PurePosixPath(url.split("?")[0]).suffix.lower()
    valid_exts = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg"}
    if url_ext in valid_exts:
        return url_ext if url_ext != ".jpeg" else ".jpg"

    return None


async def _download_image(
    client: httpx.AsyncClient,
    url: str,
    idx: int,
    images_dir: Path,
) -> tuple[str, bool]:
    """
    Downloads one image to images_dir.
    Returns (filename, success). Never raises.
    Skips images that are too large or have invalid content types.
    """
    try:
        resp = await client.get(url)
        if resp.status_code != 200:
            return "", False

        content_type = resp.headers.get("content-type", "")
        ext = _content_type_to_ext(content_type, url)
        if ext is None:
            return "", False  # Not a valid image type

        content = resp.content
        if len(content) > settings.MAX_IMAGE_SIZE_MB * 1024 * 1024:
            return "", False  # Too large

        filename = f"img_{idx + 1:03d}{ext}"
        (images_dir / filename).write_bytes(content)
        return filename, True

    except Exception:
        return "", False


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def run_packager(job_id: str) -> bool:
    """
    Packages the final result:
    1. Downloads images if DOWNLOAD_IMAGES=true (non-blocking per image)
    2. Creates images/index.json for the /images endpoint (F09)
    3. Creates result.zip (result.json + images/ if applicable)
    4. Deletes temp files (pages/, routes.json, fetch_results.json,
       extract_results.json, audit_report.json, chunk_summaries/)
    5. Calls job_manager.complete_job(job_id) → status: done, done_at: <ts>
    Returns True always (Packager does not fail the job for image errors).
    """
    logger.info("Packager starting for job %s", job_id)

    # 1. Update status to packaging
    await job_manager.update_status(job_id, JobStatus.packaging)

    job_dir = Path(settings.JOB_BASE_DIR) / job_id
    result_path = job_dir / "result.json"

    # 2. Read result.json
    result_data: dict = json.loads(result_path.read_text(encoding="utf-8"))
    images_in_result: list[dict] = result_data.get("assets", {}).get("images", [])

    # 3. Download images if enabled
    if settings.DOWNLOAD_IMAGES and images_in_result:
        images_dir = job_dir / "images"
        images_dir.mkdir(exist_ok=True)

        downloaded: list[dict] = []  # metadata for index.json

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(10.0), follow_redirects=True
        ) as client:
            for idx, img in enumerate(images_in_result):
                src: str = img.get("src", "")
                if not src:
                    continue
                filename, success = await _download_image(client, src, idx, images_dir)
                if success:
                    # Update local_path in result_data (img is a reference)
                    img["local_path"] = f"images/{filename}"
                    img_path = images_dir / filename
                    downloaded.append(
                        {
                            "filename": filename,
                            "original_url": src,
                            "alt": img.get("alt", ""),
                            "size_bytes": img_path.stat().st_size,
                        }
                    )

        # 4. Create images/index.json if any images were downloaded
        if downloaded:
            index_path = images_dir / "index.json"
            index_path.write_text(
                json.dumps({"images": downloaded}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        # 5. Update result.json with local_paths
        result_path.write_text(
            json.dumps(result_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # 6. Create result.zip
    zip_path = job_dir / "result.zip"
    images_dir_for_zip = job_dir / "images"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(result_path, "result.json")
        if settings.DOWNLOAD_IMAGES and images_dir_for_zip.exists():
            for img_file in images_dir_for_zip.iterdir():
                if img_file.name != "index.json" and img_file.is_file():
                    zf.write(img_file, f"images/{img_file.name}")

    # 7. Clean up temporary files
    for dirname in ["pages", "chunk_summaries"]:
        d = job_dir / dirname
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)

    for fname in [
        "routes.json",
        "fetch_results.json",
        "extract_results.json",
        "audit_report.json",
    ]:
        f = job_dir / fname
        if f.exists():
            f.unlink(missing_ok=True)

    # 8. Complete the job (status: done, done_at: <timestamp>)
    await job_manager.complete_job(job_id)

    logger.info("Packager completed for job %s", job_id)
    return True
