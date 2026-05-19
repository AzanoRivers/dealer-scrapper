"""
F08 — Packager: Output
Tests for CHECKPOINTS.md section F08.

Covers:
  1. test_packager_sets_done_status      — state.json → status=done + done_at
  2. test_packager_creates_zip           — result.zip exists and contains result.json
  3. test_packager_cleans_temp_files     — pages/, routes.json, etc. deleted
  4. test_packager_no_images_download_disabled — DOWNLOAD_IMAGES=false → images/ not created
  5. test_packager_downloads_images_when_enabled — mock HTTP → images/img_001.jpg created
  6. test_packager_updates_local_path    — result.json local_path updated after download
  7. test_packager_image_too_large_skipped — image > MAX_IMAGE_SIZE_MB → skipped, no failure
  8. test_packager_image_invalid_content_type_skipped — text/html → skipped
  9. test_packager_image_404_skipped     — 404 → skipped, job doesn't fail
  10. test_packager_zip_contains_images  — DOWNLOAD_IMAGES=true → zip has images/ entry
"""

import json
import os
import uuid
import zipfile
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
import respx

from app.config import settings as cfg
from app.core.job_manager import job_manager
from app.models.job import JobStatus
from app.pipeline.packager import run_packager

# ── Force pytest-asyncio to auto mode for all tests in this module ──────────
pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Setup helper
# ---------------------------------------------------------------------------

def _make_job_with_result(download_images: bool = False) -> tuple[str, Path]:
    """Creates a job in 'analyzing' state with result.json ready for Packager."""
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
            "name": "Test Dealer",
            "type": "car_dealer",
            "description": "A dealership",
            "language": "en",
            "address": None,
            "phone": None,
            "email": None,
            "social_links": [],
        },
        "content": {"main_topics": ["cars"], "key_pages": []},
        "assets": {
            "images": [
                {
                    "src": "https://example.com/img/hero.jpg",
                    "alt": "Hero",
                    "local_path": None,
                    "width": None,
                    "height": None,
                }
            ]
        },
        "metadata": {
            "total_pages_discovered": 5,
            "pages_fetched": 5,
            "pages_analyzed": 5,
            "coverage_percent": 100.0,
        },
    }
    (job_dir / "result.json").write_text(json.dumps(result_data), encoding="utf-8")

    # Temp files that should be cleaned up
    pages_dir = job_dir / "pages"
    pages_dir.mkdir()
    (pages_dir / "page1.json").write_text("{}", encoding="utf-8")
    for fname in [
        "routes.json",
        "fetch_results.json",
        "extract_results.json",
        "audit_report.json",
    ]:
        (job_dir / fname).write_text("{}", encoding="utf-8")

    # state.json
    state = {
        "job_id": job_id,
        "status": "analyzing",
        "url": "https://example.com",
        "options": {"download_images": download_images},
        "progress": {
            "phase": "analyzing",
            "pages_done": 5,
            "pages_total": 5,
            "percent": 100,
        },
        "error": None,
        "created_at": "2026-05-19T10:00:00Z",
        "started_at": "2026-05-19T10:00:00Z",
        "updated_at": "2026-05-19T10:00:00Z",
        "done_at": None,
        "ttl_remaining_seconds": None,
        "estimated_remaining_seconds": 0,
    }
    (job_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")
    return job_id, job_dir


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_packager_sets_done_status() -> None:
    """After run_packager, state.json has status=done and done_at set."""
    job_id, job_dir = _make_job_with_result()

    with patch.object(cfg, "DOWNLOAD_IMAGES", False):
        result = await run_packager(job_id)

    assert result is True

    state = await job_manager.get_state(job_id)
    assert state is not None
    assert state.status == JobStatus.done
    assert state.done_at is not None


async def test_packager_creates_zip() -> None:
    """result.zip is created and contains result.json."""
    job_id, job_dir = _make_job_with_result()

    with patch.object(cfg, "DOWNLOAD_IMAGES", False):
        await run_packager(job_id)

    zip_path = job_dir / "result.zip"
    assert zip_path.exists(), "result.zip should be created"

    with zipfile.ZipFile(zip_path, "r") as zf:
        names = zf.namelist()
    assert "result.json" in names, "result.zip must contain result.json"


async def test_packager_cleans_temp_files() -> None:
    """pages/, routes.json, fetch_results.json, extract_results.json,
    audit_report.json are deleted after packaging."""
    job_id, job_dir = _make_job_with_result()

    with patch.object(cfg, "DOWNLOAD_IMAGES", False):
        await run_packager(job_id)

    assert not (job_dir / "pages").exists(), "pages/ should be deleted"
    for fname in [
        "routes.json",
        "fetch_results.json",
        "extract_results.json",
        "audit_report.json",
    ]:
        assert not (job_dir / fname).exists(), f"{fname} should be deleted"


async def test_packager_no_images_download_disabled() -> None:
    """When DOWNLOAD_IMAGES=false, images/ directory is NOT created."""
    job_id, job_dir = _make_job_with_result(download_images=False)

    with patch.object(cfg, "DOWNLOAD_IMAGES", False):
        await run_packager(job_id)

    # images/ should NOT be created
    assert not (job_dir / "images").exists(), "images/ should not be created when DOWNLOAD_IMAGES=false"


async def test_packager_downloads_images_when_enabled() -> None:
    """DOWNLOAD_IMAGES=true + mocked HTTP → images/img_001.jpg is created."""
    job_id, job_dir = _make_job_with_result(download_images=True)

    with patch.object(cfg, "DOWNLOAD_IMAGES", True), \
         patch.object(cfg, "MAX_IMAGE_SIZE_MB", 5):
        with respx.mock:
            respx.get("https://example.com/img/hero.jpg").mock(
                return_value=httpx.Response(
                    200,
                    content=b"fake_image_bytes",
                    headers={"content-type": "image/jpeg"},
                )
            )
            result = await run_packager(job_id)

    assert result is True
    images_dir = job_dir / "images"
    assert images_dir.exists(), "images/ directory should be created"
    assert (images_dir / "img_001.jpg").exists(), "img_001.jpg should be downloaded"


async def test_packager_updates_local_path() -> None:
    """result.json is updated with local_path for each downloaded image."""
    job_id, job_dir = _make_job_with_result(download_images=True)

    with patch.object(cfg, "DOWNLOAD_IMAGES", True), \
         patch.object(cfg, "MAX_IMAGE_SIZE_MB", 5):
        with respx.mock:
            respx.get("https://example.com/img/hero.jpg").mock(
                return_value=httpx.Response(
                    200,
                    content=b"fake_image_bytes",
                    headers={"content-type": "image/jpeg"},
                )
            )
            await run_packager(job_id)

    result_data = json.loads((job_dir / "result.json").read_text(encoding="utf-8"))
    images = result_data["assets"]["images"]
    assert len(images) > 0
    assert images[0]["local_path"] == "images/img_001.jpg"


async def test_packager_image_too_large_skipped() -> None:
    """Image larger than MAX_IMAGE_SIZE_MB is skipped; job still succeeds."""
    job_id, job_dir = _make_job_with_result(download_images=True)

    # 1-byte limit → any image content will be too large
    with patch.object(cfg, "DOWNLOAD_IMAGES", True), \
         patch.object(cfg, "MAX_IMAGE_SIZE_MB", 0):  # 0 MB → everything too large
        with respx.mock:
            respx.get("https://example.com/img/hero.jpg").mock(
                return_value=httpx.Response(
                    200,
                    content=b"fake_image_bytes",
                    headers={"content-type": "image/jpeg"},
                )
            )
            result = await run_packager(job_id)

    assert result is True
    # Image should NOT be downloaded (too large)
    images_dir = job_dir / "images"
    if images_dir.exists():
        assert not (images_dir / "img_001.jpg").exists(), "Large image should not be saved"

    # Job should still be done
    state = await job_manager.get_state(job_id)
    assert state is not None
    assert state.status == JobStatus.done


async def test_packager_image_invalid_content_type_skipped() -> None:
    """Image with text/html Content-Type is skipped; job still succeeds."""
    job_id, job_dir = _make_job_with_result(download_images=True)

    with patch.object(cfg, "DOWNLOAD_IMAGES", True), \
         patch.object(cfg, "MAX_IMAGE_SIZE_MB", 5):
        with respx.mock:
            respx.get("https://example.com/img/hero.jpg").mock(
                return_value=httpx.Response(
                    200,
                    content=b"<html>not an image</html>",
                    headers={"content-type": "text/html; charset=utf-8"},
                )
            )
            result = await run_packager(job_id)

    assert result is True

    # Image should NOT be saved
    images_dir = job_dir / "images"
    if images_dir.exists():
        assert not (images_dir / "img_001.jpg").exists(), "HTML response should not be saved as image"

    state = await job_manager.get_state(job_id)
    assert state is not None
    assert state.status == JobStatus.done


async def test_packager_image_404_skipped() -> None:
    """Image returning 404 is skipped; job still succeeds."""
    job_id, job_dir = _make_job_with_result(download_images=True)

    with patch.object(cfg, "DOWNLOAD_IMAGES", True), \
         patch.object(cfg, "MAX_IMAGE_SIZE_MB", 5):
        with respx.mock:
            respx.get("https://example.com/img/hero.jpg").mock(
                return_value=httpx.Response(404, content=b"Not Found")
            )
            result = await run_packager(job_id)

    assert result is True

    images_dir = job_dir / "images"
    if images_dir.exists():
        assert not (images_dir / "img_001.jpg").exists(), "404 image should not be saved"

    state = await job_manager.get_state(job_id)
    assert state is not None
    assert state.status == JobStatus.done


async def test_packager_zip_contains_images() -> None:
    """DOWNLOAD_IMAGES=true → result.zip includes the images/ entry."""
    job_id, job_dir = _make_job_with_result(download_images=True)

    with patch.object(cfg, "DOWNLOAD_IMAGES", True), \
         patch.object(cfg, "MAX_IMAGE_SIZE_MB", 5):
        with respx.mock:
            respx.get("https://example.com/img/hero.jpg").mock(
                return_value=httpx.Response(
                    200,
                    content=b"fake_image_bytes",
                    headers={"content-type": "image/jpeg"},
                )
            )
            await run_packager(job_id)

    zip_path = job_dir / "result.zip"
    assert zip_path.exists(), "result.zip should exist"

    with zipfile.ZipFile(zip_path, "r") as zf:
        names = zf.namelist()

    assert "result.json" in names, "result.json must be in zip"
    image_entries = [n for n in names if n.startswith("images/")]
    assert len(image_entries) > 0, "images/ entries should be present in zip"
    assert "images/img_001.jpg" in names, "images/img_001.jpg should be in zip"
