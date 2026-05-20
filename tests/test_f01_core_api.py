"""
F01 — Core API y Job Management
Tests de checkpoints definidos en CHECKPOINTS.md y Features/188052025-build-fundation.md
"""
import json
import os
import shutil
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

TEST_API_KEY = "test-key-12345678901234567890123456"
VALID_URL = "https://example.com"
# Minimal response_schema used in all POST /api/v1/scrape requests (required field).
MINIMAL_SCHEMA = {"title": "...", "description": "..."}


# ===========================================================================
# Helper: create a job state.json directly on disk (bypassing API)
# ===========================================================================

def _write_state(job_base_dir: str, job_id: str, state: dict) -> Path:
    job_dir = Path(job_base_dir) / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    state_file = job_dir / "state.json"
    state_file.write_text(json.dumps(state), encoding="utf-8")
    return job_dir


# ===========================================================================
# Checkpoint 1: POST /api/v1/scrape crea job con UUID v4, devuelve {job_id, status: "queued"}
# ===========================================================================

class TestPostScrape:
    def test_creates_job_with_uuid_v4(self, client: TestClient, api_headers: dict) -> None:
        resp = client.post(
            "/api/v1/scrape",
            json={"url": VALID_URL, "response_schema": MINIMAL_SCHEMA, "options": {}},
            headers=api_headers,
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert "job_id" in data
        assert data["status"] == "queued"
        # Validate UUID v4
        parsed = uuid.UUID(data["job_id"])
        assert parsed.version == 4

    def test_job_directory_created_on_disk(self, client: TestClient, api_headers: dict) -> None:
        resp = client.post(
            "/api/v1/scrape",
            json={"url": VALID_URL, "response_schema": MINIMAL_SCHEMA, "options": {}},
            headers=api_headers,
        )
        assert resp.status_code == 201
        job_id = resp.json()["job_id"]
        job_base = os.environ["JOB_BASE_DIR"]
        state_file = Path(job_base) / job_id / "state.json"
        assert state_file.exists(), "state.json should be created on disk"

    def test_state_json_has_correct_fields(self, client: TestClient, api_headers: dict) -> None:
        resp = client.post(
            "/api/v1/scrape",
            json={"url": VALID_URL, "response_schema": MINIMAL_SCHEMA, "options": {"max_pages": 10}},
            headers=api_headers,
        )
        assert resp.status_code == 201
        job_id = resp.json()["job_id"]
        job_base = os.environ["JOB_BASE_DIR"]
        state = json.loads((Path(job_base) / job_id / "state.json").read_text())
        assert state["status"] == "queued"
        # Pydantic HttpUrl normalizes URLs (e.g., adds trailing slash to bare domains)
        assert state["url"].rstrip("/") == VALID_URL.rstrip("/")
        assert state["options"]["max_pages"] == 10
        assert "created_at" in state
        assert "updated_at" in state

    def test_missing_response_schema_returns_422(self, client: TestClient, api_headers: dict) -> None:
        """POST without response_schema must return 422 (required field)."""
        resp = client.post(
            "/api/v1/scrape",
            json={"url": VALID_URL, "options": {}},
            headers=api_headers,
        )
        assert resp.status_code == 422, resp.text

    def test_empty_response_schema_returns_422(self, client: TestClient, api_headers: dict) -> None:
        """POST with response_schema={} (empty object) must return 422."""
        resp = client.post(
            "/api/v1/scrape",
            json={"url": VALID_URL, "response_schema": {}, "options": {}},
            headers=api_headers,
        )
        assert resp.status_code == 422, resp.text


# ===========================================================================
# Checkpoint 2: GET /status devuelve schema correcto incluyendo ttl_remaining_seconds
# ===========================================================================

class TestGetJobStatus:
    def test_status_schema_in_queued_state(self, client: TestClient, api_headers: dict) -> None:
        resp = client.post(
            "/api/v1/scrape",
            json={"url": VALID_URL, "response_schema": MINIMAL_SCHEMA, "options": {}},
            headers=api_headers,
        )
        job_id = resp.json()["job_id"]
        status_resp = client.get(f"/api/v1/scrape/{job_id}/status", headers=api_headers)
        assert status_resp.status_code == 200
        data = status_resp.json()
        # Required fields
        assert data["job_id"] == job_id
        assert data["status"] == "queued"
        assert "progress" in data
        assert "ttl_remaining_seconds" in data
        assert "error" in data
        assert "created_at" in data
        assert "updated_at" in data
        assert "done_at" in data
        assert "estimated_remaining_seconds" in data

    def test_ttl_is_null_when_not_terminal(self, client: TestClient, api_headers: dict) -> None:
        resp = client.post(
            "/api/v1/scrape",
            json={"url": VALID_URL, "response_schema": MINIMAL_SCHEMA, "options": {}},
            headers=api_headers,
        )
        job_id = resp.json()["job_id"]
        status_resp = client.get(f"/api/v1/scrape/{job_id}/status", headers=api_headers)
        data = status_resp.json()
        assert data["ttl_remaining_seconds"] is None, (
            "ttl_remaining_seconds must be null when status is not done/failed"
        )

    def test_status_404_for_nonexistent_job(self, client: TestClient, api_headers: dict) -> None:
        fake_id = str(uuid.uuid4())
        resp = client.get(f"/api/v1/scrape/{fake_id}/status", headers=api_headers)
        assert resp.status_code == 404
        data = resp.json()
        assert data["error"] == "job_not_found"
        assert data["job_id"] == fake_id


# ===========================================================================
# Checkpoint 3: ttl_remaining_seconds decrementa desde 900 cuando status==done/failed
# ===========================================================================

class TestTtlRemainingSeconds:
    def test_ttl_decrements_from_900_on_done(self, client: TestClient, api_headers: dict) -> None:
        """Simulate a done job and check ttl_remaining_seconds < 900."""
        job_base = os.environ["JOB_BASE_DIR"]
        job_id = str(uuid.uuid4())
        done_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        _write_state(job_base, job_id, {
            "job_id": job_id,
            "status": "done",
            "url": VALID_URL,
            "options": {},
            "progress": {"phase": "done", "pages_done": 5, "pages_total": 5, "percent": 100},
            "error": None,
            "created_at": done_at,
            "started_at": done_at,
            "updated_at": done_at,
            "done_at": done_at,
            "ttl_remaining_seconds": None,
            "estimated_remaining_seconds": 0,
        })
        resp = client.get(f"/api/v1/scrape/{job_id}/status", headers=api_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "done"
        ttl = data["ttl_remaining_seconds"]
        assert ttl is not None, "ttl_remaining_seconds must not be null when done"
        assert 0 <= ttl <= 900, f"TTL should be between 0 and 900, got {ttl}"

    def test_ttl_decrements_from_900_on_failed(self, client: TestClient, api_headers: dict) -> None:
        job_base = os.environ["JOB_BASE_DIR"]
        job_id = str(uuid.uuid4())
        failed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        _write_state(job_base, job_id, {
            "job_id": job_id,
            "status": "failed",
            "url": VALID_URL,
            "options": {},
            "progress": {"phase": "analyzing", "pages_done": 5, "pages_total": 5, "percent": 100},
            "error": {
                "code": "LLM_TIMEOUT",
                "message": "Test error",
                "failed_at": failed_at,
                "retry_after": 300,
            },
            "created_at": failed_at,
            "started_at": failed_at,
            "updated_at": failed_at,
            "done_at": None,
            "ttl_remaining_seconds": None,
            "estimated_remaining_seconds": 0,
        })
        resp = client.get(f"/api/v1/scrape/{job_id}/status", headers=api_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "failed"
        ttl = data["ttl_remaining_seconds"]
        assert ttl is not None
        assert 0 <= ttl <= 900


# ===========================================================================
# Checkpoint 4: GET /result devuelve 404 con job_not_found_or_expired tras TTL
# ===========================================================================

class TestGetResult:
    def test_result_returns_404_after_ttl_expired(
        self, client: TestClient, api_headers: dict
    ) -> None:
        job_base = os.environ["JOB_BASE_DIR"]
        job_id = str(uuid.uuid4())
        # Simulate a done job with done_at 20 minutes ago (TTL=15min so it's expired)
        expired_at = (
            datetime.now(timezone.utc) - timedelta(minutes=20)
        ).isoformat().replace("+00:00", "Z")
        job_dir = _write_state(job_base, job_id, {
            "job_id": job_id,
            "status": "done",
            "url": VALID_URL,
            "options": {},
            "progress": {"phase": "done", "pages_done": 5, "pages_total": 5, "percent": 100},
            "error": None,
            "created_at": expired_at,
            "started_at": expired_at,
            "updated_at": expired_at,
            "done_at": expired_at,
            "ttl_remaining_seconds": None,
            "estimated_remaining_seconds": 0,
        })
        resp = client.get(f"/api/v1/scrape/{job_id}/result", headers=api_headers)
        assert resp.status_code == 404
        data = resp.json()
        assert data["error"] == "job_not_found"
        assert data["job_id"] == job_id

    def test_result_returns_425_when_not_done(self, client: TestClient, api_headers: dict) -> None:
        resp = client.post(
            "/api/v1/scrape",
            json={"url": VALID_URL, "response_schema": MINIMAL_SCHEMA, "options": {}},
            headers=api_headers,
        )
        job_id = resp.json()["job_id"]
        result_resp = client.get(f"/api/v1/scrape/{job_id}/result", headers=api_headers)
        assert result_resp.status_code == 425
        data = result_resp.json()
        assert data["status"] == "queued"

    def test_result_returns_404_for_nonexistent(self, client: TestClient, api_headers: dict) -> None:
        fake_id = str(uuid.uuid4())
        resp = client.get(f"/api/v1/scrape/{fake_id}/result", headers=api_headers)
        assert resp.status_code == 404
        data = resp.json()
        assert data["error"] == "job_not_found"


# ===========================================================================
# Checkpoint 5: GET /images devuelve 404 si DOWNLOAD_IMAGES=false (stub)
# ===========================================================================

class TestGetImages:
    def test_images_returns_404_when_download_disabled(
        self, client: TestClient, api_headers: dict
    ) -> None:
        resp = client.post(
            "/api/v1/scrape",
            json={"url": VALID_URL, "response_schema": MINIMAL_SCHEMA, "options": {}},
            headers=api_headers,
        )
        job_id = resp.json()["job_id"]
        images_resp = client.get(f"/api/v1/scrape/{job_id}/images", headers=api_headers)
        # DOWNLOAD_IMAGES=false in test env → 404 stub
        assert images_resp.status_code == 404
        data = images_resp.json()
        assert data["error"] == "images_not_available"
        assert data["job_id"] == job_id


# ===========================================================================
# Checkpoint 6: DELETE /{job_id} elimina el directorio y devuelve 200
# ===========================================================================

class TestDeleteJob:
    def test_delete_removes_directory(self, client: TestClient, api_headers: dict) -> None:
        resp = client.post(
            "/api/v1/scrape",
            json={"url": VALID_URL, "response_schema": MINIMAL_SCHEMA, "options": {}},
            headers=api_headers,
        )
        job_id = resp.json()["job_id"]
        job_base = os.environ["JOB_BASE_DIR"]
        job_dir = Path(job_base) / job_id
        assert job_dir.exists()

        del_resp = client.delete(f"/api/v1/scrape/{job_id}", headers=api_headers)
        assert del_resp.status_code == 200
        data = del_resp.json()
        assert data["deleted"] is True
        assert data["job_id"] == job_id
        assert not job_dir.exists(), "Job directory should be deleted"

    def test_status_returns_404_after_delete(self, client: TestClient, api_headers: dict) -> None:
        resp = client.post(
            "/api/v1/scrape",
            json={"url": VALID_URL, "response_schema": MINIMAL_SCHEMA, "options": {}},
            headers=api_headers,
        )
        job_id = resp.json()["job_id"]
        client.delete(f"/api/v1/scrape/{job_id}", headers=api_headers)
        status_resp = client.get(f"/api/v1/scrape/{job_id}/status", headers=api_headers)
        assert status_resp.status_code == 404

    def test_delete_returns_404_for_nonexistent(self, client: TestClient, api_headers: dict) -> None:
        fake_id = str(uuid.uuid4())
        resp = client.delete(f"/api/v1/scrape/{fake_id}", headers=api_headers)
        assert resp.status_code == 404
        data = resp.json()
        assert data["error"] == "job_not_found"


# ===========================================================================
# Checkpoint 7: startup_cleanup() elimina huérfanos correctamente
# ===========================================================================

class TestStartupCleanup:
    def test_startup_cleanup_removes_expired_done_jobs(self) -> None:
        """startup_cleanup should remove done jobs with TTL expired."""
        from app.main import startup_cleanup
        import asyncio

        job_base = os.environ["JOB_BASE_DIR"]
        job_id = str(uuid.uuid4())
        # done job with done_at 20 minutes ago
        expired_at = (
            datetime.now(timezone.utc) - timedelta(minutes=20)
        ).isoformat().replace("+00:00", "Z")
        job_dir = _write_state(job_base, job_id, {
            "job_id": job_id,
            "status": "done",
            "url": VALID_URL,
            "options": {},
            "progress": None,
            "error": None,
            "created_at": expired_at,
            "started_at": expired_at,
            "updated_at": expired_at,
            "done_at": expired_at,
            "ttl_remaining_seconds": None,
            "estimated_remaining_seconds": 0,
        })
        assert job_dir.exists()
        asyncio.run(startup_cleanup())
        assert not job_dir.exists(), "Expired done job should be removed by startup_cleanup"

    def test_startup_cleanup_removes_orphan_in_progress_jobs(self) -> None:
        """startup_cleanup should remove in-progress jobs older than JOB_MAX_DURATION_SECONDS."""
        from app.main import startup_cleanup
        import asyncio

        job_base = os.environ["JOB_BASE_DIR"]
        job_id = str(uuid.uuid4())
        # in-progress job started 40 minutes ago
        old_at = (
            datetime.now(timezone.utc) - timedelta(minutes=40)
        ).isoformat().replace("+00:00", "Z")
        job_dir = _write_state(job_base, job_id, {
            "job_id": job_id,
            "status": "fetching",
            "url": VALID_URL,
            "options": {},
            "progress": None,
            "error": None,
            "created_at": old_at,
            "started_at": old_at,
            "updated_at": old_at,
            "done_at": None,
            "ttl_remaining_seconds": None,
            "estimated_remaining_seconds": 0,
        })
        assert job_dir.exists()
        asyncio.run(startup_cleanup())
        assert not job_dir.exists(), "Old in-progress job should be removed by startup_cleanup"

    def test_startup_cleanup_removes_corrupt_directories(self) -> None:
        """startup_cleanup should remove directories with corrupt state.json."""
        from app.main import startup_cleanup
        import asyncio

        job_base = os.environ["JOB_BASE_DIR"]
        job_id = str(uuid.uuid4())
        job_dir = Path(job_base) / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        (job_dir / "state.json").write_text("not valid json", encoding="utf-8")
        assert job_dir.exists()
        asyncio.run(startup_cleanup())
        assert not job_dir.exists(), "Corrupt job directory should be removed"

    def test_startup_cleanup_keeps_recent_done_jobs(self) -> None:
        """startup_cleanup should NOT remove done jobs that are still within TTL."""
        from app.main import startup_cleanup
        import asyncio

        job_base = os.environ["JOB_BASE_DIR"]
        job_id = str(uuid.uuid4())
        recent_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        job_dir = _write_state(job_base, job_id, {
            "job_id": job_id,
            "status": "done",
            "url": VALID_URL,
            "options": {},
            "progress": None,
            "error": None,
            "created_at": recent_at,
            "started_at": recent_at,
            "updated_at": recent_at,
            "done_at": recent_at,
            "ttl_remaining_seconds": None,
            "estimated_remaining_seconds": 0,
        })
        assert job_dir.exists()
        asyncio.run(startup_cleanup())
        assert job_dir.exists(), "Recent done job (within TTL) should NOT be removed"
        # Cleanup
        shutil.rmtree(job_dir, ignore_errors=True)


# ===========================================================================
# Checkpoint 8: X-API-Key requerida en todos los endpoints /api/v1/ → 401 sin key
# ===========================================================================

class TestApiKeyAuth:
    ENDPOINTS = [
        ("GET", "/api/v1/status"),
        ("POST", "/api/v1/scrape"),
        ("GET", "/api/v1/scrape/some-job-id/status"),
        ("GET", "/api/v1/scrape/some-job-id/result"),
        ("GET", "/api/v1/scrape/some-job-id/images"),
        ("GET", "/api/v1/scrape/some-job-id/download"),
        ("DELETE", "/api/v1/scrape/some-job-id"),
    ]

    def test_returns_401_without_api_key(self, client: TestClient) -> None:
        for method, path in self.ENDPOINTS:
            resp = client.request(method, path)
            assert resp.status_code in (401, 422), (
                f"Expected 401/422 for {method} {path} without API key, got {resp.status_code}"
            )

    def test_returns_401_with_wrong_api_key(self, client: TestClient) -> None:
        bad_headers = {"X-API-Key": "wrong-key-totally-invalid"}
        for method, path in self.ENDPOINTS:
            resp = client.request(method, path, headers=bad_headers)
            assert resp.status_code == 401, (
                f"Expected 401 for {method} {path} with wrong key, got {resp.status_code}"
            )

    def test_returns_200_or_valid_with_correct_key(self, client: TestClient, api_headers: dict) -> None:
        resp = client.get("/api/v1/status", headers=api_headers)
        assert resp.status_code == 200


# ===========================================================================
# Checkpoint 9: GET /guide-ai devuelve JSON válido con todos los endpoints y error_codes
# ===========================================================================

class TestGuideAi:
    def test_guide_ai_returns_valid_json(self, client: TestClient) -> None:
        resp = client.get("/guide-ai")
        assert resp.status_code == 200
        data = resp.json()
        assert "endpoints" in data
        assert "error_codes" in data

    def test_guide_ai_contains_all_error_codes(self, client: TestClient) -> None:
        resp = client.get("/guide-ai")
        data = resp.json()
        required_codes = [
            "NO_ROUTES_FOUND",
            "FETCH_ALL_FAILED",
            "EXTRACTION_EMPTY",
            "AUDIT_CRITICAL_GAPS",
            "LLM_TIMEOUT",
            "LLM_AUTH_ERROR",
            "LLM_PARSE_ERROR",
            "JOB_TIMEOUT",
            "INTERNAL_ERROR",
        ]
        for code in required_codes:
            assert code in data["error_codes"], f"Missing error_code: {code}"

    def test_guide_ai_contains_all_endpoints(self, client: TestClient) -> None:
        resp = client.get("/guide-ai")
        data = resp.json()
        paths = {e["path"] for e in data["endpoints"]}
        required_paths = {
            "/",
            "/guide-ai",
            "/api/v1/status",
            "/api/v1/scrape",
            "/api/v1/scrape/{job_id}/status",
            "/api/v1/scrape/{job_id}/result",
            "/api/v1/scrape/{job_id}/images",
            "/api/v1/scrape/{job_id}/images/{filename}",
            "/api/v1/scrape/{job_id}/download",
            "/api/v1/scrape/{job_id}",
        }
        for path in required_paths:
            assert path in paths, f"Missing path in guide-ai endpoints: {path}"

    def test_guide_ai_has_no_auth_required(self, client: TestClient) -> None:
        """guide-ai is accessible without auth."""
        resp = client.get("/guide-ai")
        assert resp.status_code == 200

    def test_root_is_accessible_without_auth(self, client: TestClient) -> None:
        resp = client.get("/")
        assert resp.status_code == 200
        data = resp.json()
        assert "name" in data
        assert "version" in data
        assert "status" in data


# ===========================================================================
# Checkpoint 10: GET /api/v1/status devuelve jobs activos y capacidad
# ===========================================================================

class TestServerStatus:
    def test_server_status_schema(self, client: TestClient, api_headers: dict) -> None:
        resp = client.get("/api/v1/status", headers=api_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "name" in data
        assert "version" in data
        assert "active_jobs" in data
        assert "max_concurrent_jobs" in data
        assert "status" in data
        assert data["status"] == "ok"
        assert isinstance(data["active_jobs"], int)
