"""F09 — Tests for image listing, individual image serving, and ZIP download endpoints."""
import io
import json
import os
import uuid
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

BASE_URL = "https://example.com"
FAKE_IMAGE_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 100  # fake JPEG header


def _make_done_job(*, with_images: bool = False) -> str:
    """Creates a job in 'done' state with the files that would remain after the Packager."""
    job_id = str(uuid.uuid4())
    job_base = Path(os.environ["JOB_BASE_DIR"])
    job_dir = job_base / job_id
    job_dir.mkdir(parents=True)

    # result.json
    result_data = {
        "job_id": job_id,
        "url": BASE_URL,
        "scraped_at": "2026-05-19T10:00:00Z",
        "llm_provider": "openai",
        "llm_model": "gpt-4o-mini",
        "business": {
            "name": "Test",
            "type": "car_dealer",
            "description": "...",
            "language": "en",
            "address": None,
            "phone": None,
            "email": None,
            "social_links": [],
        },
        "content": {"main_topics": [], "key_pages": []},
        "assets": {"images": []},
        "metadata": {
            "total_pages_discovered": 1,
            "pages_fetched": 1,
            "pages_analyzed": 1,
            "coverage_percent": 100.0,
        },
    }
    (job_dir / "result.json").write_text(json.dumps(result_data), encoding="utf-8")

    # result.zip (minimal valid zip)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("result.json", json.dumps(result_data))
    (job_dir / "result.zip").write_bytes(buf.getvalue())

    # images/ (if requested)
    if with_images:
        images_dir = job_dir / "images"
        images_dir.mkdir()
        (images_dir / "img_001.jpg").write_bytes(FAKE_IMAGE_BYTES)
        index = {
            "images": [
                {
                    "filename": "img_001.jpg",
                    "original_url": f"{BASE_URL}/img/hero.jpg",
                    "alt": "Hero",
                    "size_bytes": len(FAKE_IMAGE_BYTES),
                }
            ]
        }
        (images_dir / "index.json").write_text(json.dumps(index), encoding="utf-8")

    # state.json — status: done
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    state = {
        "job_id": job_id,
        "status": "done",
        "url": BASE_URL,
        "options": {},
        "progress": {"phase": "done", "pages_done": 1, "pages_total": 1, "percent": 100},
        "error": None,
        "created_at": now,
        "started_at": now,
        "updated_at": now,
        "done_at": now,
        "ttl_remaining_seconds": None,
        "estimated_remaining_seconds": 0,
    }
    (job_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")
    return job_id


def _make_in_progress_job(status_str: str = "exploring") -> str:
    """Creates a job in a non-done state."""
    job_id = str(uuid.uuid4())
    job_base = Path(os.environ["JOB_BASE_DIR"])
    job_dir = job_base / job_id
    job_dir.mkdir(parents=True)

    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    state = {
        "job_id": job_id,
        "status": status_str,
        "url": BASE_URL,
        "options": {},
        "progress": {"phase": status_str, "pages_done": 0, "pages_total": 0, "percent": 0},
        "error": None,
        "created_at": now,
        "started_at": now,
        "updated_at": now,
        "done_at": None,
        "ttl_remaining_seconds": None,
        "estimated_remaining_seconds": 0,
    }
    (job_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")
    return job_id


# ---------------------------------------------------------------------------
# Test 1: images listing — no index.json returns 404 images_not_available
# ---------------------------------------------------------------------------

def test_images_no_index_returns_404(client: TestClient, api_headers: dict) -> None:
    job_id = _make_done_job(with_images=False)
    resp = client.get(f"/api/v1/scrape/{job_id}/images", headers=api_headers)
    assert resp.status_code == 404
    data = resp.json()
    assert data["error"] == "images_not_available"
    assert data["job_id"] == job_id


# ---------------------------------------------------------------------------
# Test 2: images listing returns full listing with ttl_remaining_seconds
# ---------------------------------------------------------------------------

def test_images_returns_listing(client: TestClient, api_headers: dict) -> None:
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
    assert img["size_bytes"] > 0


# ---------------------------------------------------------------------------
# Test 3: download_url has correct format
# ---------------------------------------------------------------------------

def test_images_listing_has_correct_download_url(client: TestClient, api_headers: dict) -> None:
    job_id = _make_done_job(with_images=True)
    resp = client.get(f"/api/v1/scrape/{job_id}/images", headers=api_headers)
    assert resp.status_code == 200
    img = resp.json()["images"][0]
    assert img["download_url"] == f"/api/v1/scrape/{job_id}/images/img_001.jpg"


# ---------------------------------------------------------------------------
# Test 4: images listing — unknown job_id returns 404 job_not_found
# ---------------------------------------------------------------------------

def test_images_job_not_found_returns_404(client: TestClient, api_headers: dict) -> None:
    fake_id = str(uuid.uuid4())
    resp = client.get(f"/api/v1/scrape/{fake_id}/images", headers=api_headers)
    assert resp.status_code == 404
    assert resp.json()["error"] == "job_not_found"


# ---------------------------------------------------------------------------
# Test 5: individual image file — serves bytes with correct Content-Type
# ---------------------------------------------------------------------------

def test_image_file_serves_bytes(client: TestClient, api_headers: dict) -> None:
    job_id = _make_done_job(with_images=True)
    resp = client.get(f"/api/v1/scrape/{job_id}/images/img_001.jpg", headers=api_headers)
    assert resp.status_code == 200
    assert "image/jpeg" in resp.headers["content-type"]
    assert resp.content == FAKE_IMAGE_BYTES


# ---------------------------------------------------------------------------
# Test 6: individual image — file does not exist returns 404 image_not_found
# ---------------------------------------------------------------------------

def test_image_file_not_found_returns_404(client: TestClient, api_headers: dict) -> None:
    job_id = _make_done_job(with_images=True)
    resp = client.get(f"/api/v1/scrape/{job_id}/images/nonexistent.png", headers=api_headers)
    assert resp.status_code == 404
    data = resp.json()
    assert data["error"] == "image_not_found"
    assert data["filename"] == "nonexistent.png"
    assert data["job_id"] == job_id


# ---------------------------------------------------------------------------
# Test 7: individual image — job_dir missing returns 404 job_not_found
# ---------------------------------------------------------------------------

def test_image_file_job_dir_missing_returns_404(client: TestClient, api_headers: dict) -> None:
    fake_id = str(uuid.uuid4())
    resp = client.get(f"/api/v1/scrape/{fake_id}/images/img_001.jpg", headers=api_headers)
    assert resp.status_code == 404
    assert resp.json()["error"] == "job_not_found"


# ---------------------------------------------------------------------------
# Test 8: download ZIP — done job returns 200 application/zip
# ---------------------------------------------------------------------------

def test_download_zip_returns_file(client: TestClient, api_headers: dict) -> None:
    job_id = _make_done_job(with_images=False)
    resp = client.get(f"/api/v1/scrape/{job_id}/download", headers=api_headers)
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    # Verify it is a valid zip
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    assert "result.json" in zf.namelist()


# ---------------------------------------------------------------------------
# Test 9: download ZIP — job not done returns 425 job_not_ready
# ---------------------------------------------------------------------------

def test_download_job_not_ready_returns_425(client: TestClient, api_headers: dict) -> None:
    job_id = _make_in_progress_job("exploring")
    resp = client.get(f"/api/v1/scrape/{job_id}/download", headers=api_headers)
    assert resp.status_code == 425
    data = resp.json()
    assert data["error"] == "job_not_ready"
    assert data["status"] == "exploring"


# ---------------------------------------------------------------------------
# Test 10: download ZIP — unknown job_id returns 404 job_not_found
# ---------------------------------------------------------------------------

def test_download_job_not_found_returns_404(client: TestClient, api_headers: dict) -> None:
    fake_id = str(uuid.uuid4())
    resp = client.get(f"/api/v1/scrape/{fake_id}/download", headers=api_headers)
    assert resp.status_code == 404
    assert resp.json()["error"] == "job_not_found"
