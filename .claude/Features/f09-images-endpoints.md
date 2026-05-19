# F09 — Endpoints de Imágenes y Descarga

## Objetivo

Reemplazar los 3 stubs en `router.py` con implementaciones reales:
- `GET /api/v1/scrape/{job_id}/images` — listado de imágenes con TTL
- `GET /api/v1/scrape/{job_id}/images/{filename}` — servir imagen individual
- `GET /api/v1/scrape/{job_id}/download` — servir result.zip

---

## Archivos a crear/modificar

- **Modificar**: `app/api/v1/router.py` — reemplazar los 3 stubs
- **Crear**: `tests/test_f09_images.py`
- **NO tocar**: ningún otro archivo

---

## Implementaciones en router.py

### Imports nuevos a agregar

```python
from fastapi.responses import FileResponse
```
(ya debe estar `from pathlib import Path`, si no agregar)

### Mapeo Content-Type para imágenes

Agregar constante module-level en router.py:

```python
_IMAGE_CONTENT_TYPES: dict[str, str] = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
}
```

---

### 1. GET /scrape/{job_id}/images — listado completo

```python
@router.get("/scrape/{job_id}/images", summary="Lista de imágenes descargadas")
async def get_job_images(job_id: str, api_key: str = Depends(verify_api_key)) -> Any:
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
                "detail": "No hay imágenes disponibles. "
                          "El job puede no haber descargado imágenes (DOWNLOAD_IMAGES=false) "
                          "o aún no está completo.",
                "job_id": job_id,
            },
        )

    import json as _json
    index_data = _json.loads(index_path.read_text(encoding="utf-8"))
    images = index_data.get("images", [])

    ttl = job_manager.get_ttl_remaining(state)

    # Build response with download_url per image
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
```

---

### 2. GET /scrape/{job_id}/images/{filename} — imagen individual

```python
@router.get("/scrape/{job_id}/images/{filename}", summary="Descarga de una imagen individual")
async def get_job_image_file(
    job_id: str, filename: str, api_key: str = Depends(verify_api_key)
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

    # Determine Content-Type by extension
    ext = image_path.suffix.lower()
    media_type = _IMAGE_CONTENT_TYPES.get(ext, "application/octet-stream")

    return FileResponse(path=image_path, media_type=media_type, filename=filename)
```

**Nota**: `job_dir.exists()` es suficiente — si el directorio no existe, el job expiró.
No necesitamos leer state.json para este endpoint (evita I/O extra).

---

### 3. GET /scrape/{job_id}/download — ZIP completo

```python
@router.get("/scrape/{job_id}/download", summary="ZIP completo: result.json + imágenes")
async def download_job(job_id: str, api_key: str = Depends(verify_api_key)) -> Any:
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
```

---

## Tests (tests/test_f09_images.py)

10 tests usando TestClient HTTP (no llamadas directas a funciones).
Los tests crean job dirs manuales en JOB_BASE_DIR con los archivos necesarios.

### Setup helper

```python
BASE_URL = "https://example.com"
FAKE_IMAGE_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 100  # fake JPEG header

def _make_done_job(*, with_images: bool = False) -> str:
    """Crea un job en estado 'done' con los archivos que quedarían tras el Packager."""
    job_id = str(uuid.uuid4())
    job_base = Path(os.environ["JOB_BASE_DIR"])
    job_dir = job_base / job_id
    job_dir.mkdir(parents=True)

    # result.json
    result_data = {
        "job_id": job_id, "url": BASE_URL,
        "scraped_at": "2026-05-19T10:00:00Z",
        "llm_provider": "openai", "llm_model": "gpt-4o-mini",
        "business": {"name": "Test", "type": "car_dealer", "description": "...",
                     "language": "en", "address": None, "phone": None, "email": None, "social_links": []},
        "content": {"main_topics": [], "key_pages": []},
        "assets": {"images": []},
        "metadata": {"total_pages_discovered": 1, "pages_fetched": 1,
                     "pages_analyzed": 1, "coverage_percent": 100.0},
    }
    (job_dir / "result.json").write_text(json.dumps(result_data), encoding="utf-8")

    # result.zip (minimal valid zip)
    import zipfile, io
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("result.json", json.dumps(result_data))
    (job_dir / "result.zip").write_bytes(buf.getvalue())

    # images/ (if requested)
    if with_images:
        images_dir = job_dir / "images"
        images_dir.mkdir()
        (images_dir / "img_001.jpg").write_bytes(FAKE_IMAGE_BYTES)
        index = {"images": [{
            "filename": "img_001.jpg",
            "original_url": f"{BASE_URL}/img/hero.jpg",
            "alt": "Hero", "size_bytes": len(FAKE_IMAGE_BYTES),
        }]}
        (images_dir / "index.json").write_text(json.dumps(index), encoding="utf-8")

    # state.json — status: done
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    state = {
        "job_id": job_id, "status": "done", "url": BASE_URL, "options": {},
        "progress": {"phase": "done", "pages_done": 1, "pages_total": 1, "percent": 100},
        "error": None, "created_at": now, "started_at": now,
        "updated_at": now, "done_at": now,
        "ttl_remaining_seconds": None, "estimated_remaining_seconds": 0,
    }
    (job_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")
    return job_id
```

### Tests:

1. **test_images_no_index_returns_404** — job sin images/index.json → 404 images_not_available
2. **test_images_returns_listing** — job con index.json → listado con download_url y ttl_remaining_seconds
3. **test_images_listing_has_correct_download_url** — download_url contiene `/api/v1/scrape/{job_id}/images/{filename}`
4. **test_images_job_not_found_returns_404** — job_id inexistente → 404 job_not_found
5. **test_image_file_serves_bytes** — GET /images/img_001.jpg → 200 con Content-Type: image/jpeg y bytes correctos
6. **test_image_file_not_found_returns_404** — archivo inexistente → 404 image_not_found
7. **test_image_file_job_dir_missing_returns_404** — job_dir eliminado → 404 job_not_found
8. **test_download_zip_returns_file** — GET /download con job done → 200 application/zip
9. **test_download_job_not_ready_returns_425** — job en estado "exploring" → 425 job_not_ready
10. **test_download_job_not_found_returns_404** — job_id inexistente → 404 job_not_found

### Patrón de test HTTP:

```python
def test_images_returns_listing(client, api_headers):
    job_id = _make_done_job(with_images=True)
    resp = client.get(f"/api/v1/scrape/{job_id}/images", headers=api_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["job_id"] == job_id
    assert data["total_images"] == 1
    assert "ttl_remaining_seconds" in data
    assert len(data["images"]) == 1
    img = data["images"][0]
    assert img["filename"] == "img_001.jpg"
    assert img["download_url"] == f"/api/v1/scrape/{job_id}/images/img_001.jpg"
    assert img["size_bytes"] > 0
```

---

## Notas

- `FileResponse` de FastAPI lee el archivo en chunks — seguro para archivos grandes
- TestClient de Starlette soporta `FileResponse` — devuelve bytes en `resp.content`
- Para verificar el ZIP en tests: `zipfile.ZipFile(io.BytesIO(resp.content))` 
- El endpoint `/images/{filename}` no lee state.json (check por existencia de job_dir)
- Los imports `json` e `io` en tests ya usan stdlib — no agregar dependencias
- La sesión del `client` fixture en conftest es `scope="session"` — los jobs creados en tests persisten hasta que `pytest_unconfigure` limpia el temp dir completo
