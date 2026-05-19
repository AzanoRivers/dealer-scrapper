# F08 — Packager: Output

## Objetivo

Implementar `app/pipeline/packager.py` — empaqueta el resultado final, descarga imágenes
(si aplica), crea result.zip, limpia temporales y completa el job.

---

## Archivos a crear/modificar

- **Crear**: `app/pipeline/packager.py`
- **Modificar**: `app/core/guards.py` — agregar run_packager + schedule_cleanup en run_pipeline
- **Crear**: `tests/test_f08_packager.py`
- **NO tocar**: job_manager, config, reviewer, otros subagents

---

## Signatura principal

```python
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
```

**Nota**: `asyncio.create_task(schedule_cleanup(job_id))` es lanzado por `run_pipeline` en
`guards.py` DESPUÉS de que `run_packager` retorna. El Packager NO importa guards.py.

---

## Pasos detallados

### 1. Update status to "packaging"

```python
await job_manager.update_status(job_id, JobStatus.packaging)
```

### 2. Leer result.json

```python
result_path = job_dir / "result.json"
result_data = json.loads(result_path.read_text(encoding="utf-8"))
images_in_result = result_data.get("assets", {}).get("images", [])
```

### 3. Descargar imágenes (si DOWNLOAD_IMAGES=true)

```python
images_dir = job_dir / "images"
images_dir.mkdir(exist_ok=True)

downloaded: list[dict] = []  # metadata for index.json

async with httpx.AsyncClient(timeout=httpx.Timeout(10.0), follow_redirects=True) as client:
    for idx, img in enumerate(images_in_result):
        src = img.get("src", "")
        if not src:
            continue
        filename, success = await _download_image(client, src, idx, images_dir)
        if success:
            # Update local_path in result_data
            img["local_path"] = f"images/{filename}"
            img_path = images_dir / filename
            downloaded.append({
                "filename": filename,
                "original_url": src,
                "alt": img.get("alt", ""),
                "size_bytes": img_path.stat().st_size,
            })
```

**`_download_image(client, url, idx, images_dir) -> tuple[str, bool]`**:
```python
async def _download_image(client, url, idx, images_dir):
    """Downloads one image. Returns (filename, success). Never raises."""
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
        
        filename = f"img_{idx+1:03d}{ext}"
        (images_dir / filename).write_bytes(content)
        return filename, True
    except Exception:
        return "", False
```

**`_content_type_to_ext(content_type, url) -> Optional[str]`**:
```python
_VALID_IMAGE_TYPES = {
    "image/jpeg": ".jpg", "image/jpg": ".jpg",
    "image/png": ".png", "image/webp": ".webp",
    "image/gif": ".gif", "image/svg+xml": ".svg",
}

def _content_type_to_ext(content_type: str, url: str) -> Optional[str]:
    # Try content-type first
    for mime, ext in _VALID_IMAGE_TYPES.items():
        if mime in content_type.lower():
            return ext
    # Fallback: try URL extension
    from pathlib import PurePosixPath
    url_ext = PurePosixPath(url.split("?")[0]).suffix.lower()
    valid_exts = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg"}
    if url_ext in valid_exts:
        return url_ext if url_ext != ".jpeg" else ".jpg"
    return None
```

### 4. Crear images/index.json (para endpoint F09)

Solo si hay imágenes descargadas:
```python
if downloaded:
    index_path = images_dir / "index.json"
    index_path.write_text(
        json.dumps({"images": downloaded}, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
```

### 5. Actualizar result.json con local_path

Si se descargaron imágenes, reescribir result.json con los local_path actualizados:
```python
result_path.write_text(
    json.dumps(result_data, ensure_ascii=False, indent=2),
    encoding="utf-8"
)
```

### 6. Crear result.zip

```python
import zipfile

zip_path = job_dir / "result.zip"
with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
    zf.write(result_path, "result.json")
    if settings.DOWNLOAD_IMAGES and images_dir.exists():
        for img_file in images_dir.iterdir():
            if img_file.name != "index.json" and img_file.is_file():
                zf.write(img_file, f"images/{img_file.name}")
```

### 7. Limpiar archivos temporales

```python
# Directorios
for dirname in ["pages", "chunk_summaries"]:
    d = job_dir / dirname
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)

# Archivos sueltos
for fname in ["routes.json", "fetch_results.json", "extract_results.json", "audit_report.json"]:
    f = job_dir / fname
    if f.exists():
        f.unlink(missing_ok=True)
```

### 8. Completar el job

```python
await job_manager.complete_job(job_id)
# state.json → status: "done", done_at: <timestamp>
```

### 9. Retornar True

El Packager siempre retorna True. Los errores de imágenes son non-fatal.

---

## Modificación a guards.py (run_pipeline)

Agregar después del bloque Guard2/run_reviewer:

```python
from app.pipeline.packager import run_packager  # agregar en imports al inicio del archivo

# En run_pipeline, después del bloque guard2/run_reviewer:

if not await _check_still_running(job_id):
    return

ok = await run_packager(job_id)
if not ok:
    return

# Guard 3 — TTL cleanup (launched after job reaches done)
guard3: asyncio.Task = asyncio.create_task(schedule_cleanup(job_id))
job_manager.register_task(job_id, guard3)
# Note: guard3 is intentionally NOT cancelled in finally — it must run to clean up.
```

**Importante**: Guard 3 NO se cancela en `finally` de `run_pipeline`. Es una cleanup task
que debe correr hasta el final (15 min). Si el job se elimina via DELETE, `_cancel_job_tasks`
la cancelará junto con Guard 1.

---

## Tests (tests/test_f08_packager.py)

10 tests. Usar `respx` para mockear descargas HTTP de imágenes.

### Setup helper

```python
def _make_job_with_result(download_images: bool = False) -> tuple[str, Path]:
    """Crea job en estado 'auditing' con result.json listo para Packager."""
    job_id = str(uuid.uuid4())
    job_base = Path(os.environ["JOB_BASE_DIR"])
    job_dir = job_base / job_id
    job_dir.mkdir(parents=True)
    
    result_data = {
        "job_id": job_id,
        "url": "https://example.com",
        "scraped_at": "2026-05-19T10:00:00Z",
        "llm_provider": "openai",
        "llm_model": "gpt-4o-mini",
        "business": {
            "name": "Test Dealer", "type": "car_dealer",
            "description": "A dealership", "language": "en",
            "address": None, "phone": None, "email": None, "social_links": []
        },
        "content": {"main_topics": ["cars"], "key_pages": []},
        "assets": {
            "images": [
                {"src": "https://example.com/img/hero.jpg", "alt": "Hero",
                 "local_path": None, "width": None, "height": None}
            ]
        },
        "metadata": {
            "total_pages_discovered": 5, "pages_fetched": 5,
            "pages_analyzed": 5, "coverage_percent": 100.0
        }
    }
    (job_dir / "result.json").write_text(json.dumps(result_data), encoding="utf-8")
    
    # Crear algunos archivos temporales para verificar que se limpian
    pages_dir = job_dir / "pages"
    pages_dir.mkdir()
    (pages_dir / "page1.json").write_text("{}", encoding="utf-8")
    for fname in ["routes.json", "fetch_results.json", "extract_results.json", "audit_report.json"]:
        (job_dir / fname).write_text("{}", encoding="utf-8")
    
    # state.json
    state = {
        "job_id": job_id, "status": "analyzing", "url": "https://example.com",
        "options": {"download_images": download_images},
        "progress": {"phase": "analyzing", "pages_done": 5, "pages_total": 5, "percent": 100},
        "error": None, "created_at": "2026-05-19T10:00:00Z",
        "started_at": "2026-05-19T10:00:00Z",
        "updated_at": "2026-05-19T10:00:00Z", "done_at": None,
        "ttl_remaining_seconds": None, "estimated_remaining_seconds": 0
    }
    (job_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")
    return job_id, job_dir
```

### Tests:

1. **test_packager_sets_done_status** — después de run_packager, state.json tiene status=done y done_at
2. **test_packager_creates_zip** — result.zip existe y contiene result.json
3. **test_packager_cleans_temp_files** — pages/, routes.json, fetch_results.json, etc. eliminados
4. **test_packager_no_images_download_disabled** — DOWNLOAD_IMAGES=false → images/ no creado
5. **test_packager_downloads_images_when_enabled** — DOWNLOAD_IMAGES=true + mock httpx → images/ con img_001.jpg
6. **test_packager_updates_local_path** — result.json actualizado con local_path después de descarga
7. **test_packager_image_too_large_skipped** — imagen > MAX_IMAGE_SIZE_MB → no guardada, job no falla
8. **test_packager_image_invalid_content_type_skipped** — Content-Type no es imagen → skip
9. **test_packager_image_404_skipped** — imagen da 404 → skip, job no falla
10. **test_packager_zip_contains_images** — DOWNLOAD_IMAGES=true, verifica que zip tiene images/

### Patrón para tests con DOWNLOAD_IMAGES:
```python
# Usar patch.object en settings para override
from unittest.mock import patch
from app.config import settings as cfg

with patch.object(cfg, "DOWNLOAD_IMAGES", True):
    with patch.object(cfg, "MAX_IMAGE_SIZE_MB", 5):
        with respx.mock:
            respx.get("https://example.com/img/hero.jpg").mock(
                return_value=httpx.Response(
                    200,
                    content=b"fake_image_bytes",
                    headers={"content-type": "image/jpeg"}
                )
            )
            result = await run_packager(job_id)
```

---

## Imports necesarios en packager.py

```python
import asyncio
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
```

---

## Notas

- `zipfile` es stdlib — no agregar dependencias nuevas
- `write_bytes` para imágenes (binario), `write_text` para JSON
- El Packager NO lanza Guard 3 — lo lanza `run_pipeline` en guards.py para evitar circular import
- Si `pages/` no existe (ya limpiado por Extractor), el shutil.rmtree es noop (ignore_errors=True)
- `chunk_summaries/` puede ya estar eliminado por el Reviewer → también noop
- `missing_ok=True` en `unlink` para archivos que pueden no existir
- El job_manager.complete_job ya tiene lock asyncio → seguro llamarlo desde packager
