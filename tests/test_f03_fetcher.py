"""
F03 — Fetcher: HTML Download
Tests for checkpoints in CHECKPOINTS.md and Features/f03-fetcher-html-download.md

Uses respx to mock httpx.AsyncClient calls.
"""

import asyncio
import hashlib
import json
import os
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx

# ── Force pytest-asyncio to auto mode for all tests in this module ──────────
pytestmark = pytest.mark.asyncio

BASE_URL = "https://example.com"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_job_id() -> str:
    return str(uuid.uuid4())


def _write_state(job_id: str, url: str = BASE_URL) -> Path:
    """Write a minimal state.json for the given job_id in JOB_BASE_DIR."""
    job_base = os.environ["JOB_BASE_DIR"]
    job_dir = Path(job_base) / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    now = "2026-01-01T00:00:00Z"
    state = {
        "job_id": job_id,
        "status": "queued",
        "url": url,
        "options": {},
        "progress": {"phase": "queued", "pages_done": 0, "pages_total": 0, "percent": 0},
        "error": None,
        "created_at": now,
        "started_at": None,
        "updated_at": now,
        "done_at": None,
        "ttl_remaining_seconds": None,
        "estimated_remaining_seconds": 0,
    }
    (job_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")
    return job_dir


def _write_routes(job_id: str, urls: list[str]) -> None:
    """Write a routes.json compatible with what the Explorer produces."""
    job_base = os.environ["JOB_BASE_DIR"]
    job_dir = Path(job_base) / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    routes = [{"url": u, "source": "sitemap", "priority": None} for u in urls]
    payload = {
        "job_id": job_id,
        "base_url": BASE_URL,
        "total_routes": len(routes),
        "discovery_method": "sitemap",
        "routes": routes,
    }
    (job_dir / "routes.json").write_text(json.dumps(payload), encoding="utf-8")


def _load_fetch_results(job_id: str) -> dict:
    job_base = os.environ["JOB_BASE_DIR"]
    path = Path(job_base) / job_id / "fetch_results.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _load_state(job_id: str) -> dict:
    job_base = os.environ["JOB_BASE_DIR"]
    path = Path(job_base) / job_id / "state.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _url_hash(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Test 1: test_fetches_all_routes
# ---------------------------------------------------------------------------


@respx.mock
async def test_fetches_all_routes() -> None:
    """All URLs return 200 → HTML files in raw/, fetch_results.json written."""
    job_id = _make_job_id()
    job_dir = _write_state(job_id)
    urls = [
        f"{BASE_URL}/about",
        f"{BASE_URL}/contact",
        f"{BASE_URL}/products",
    ]
    _write_routes(job_id, urls)

    for url in urls:
        respx.get(url).mock(return_value=httpx.Response(200, text="<html>ok</html>"))

    from app.pipeline.fetcher import run_fetcher

    with patch("app.pipeline.fetcher._sleep", new_callable=AsyncMock):
        result = await run_fetcher(job_id)

    assert result is True

    # raw/ directory must exist
    raw_dir = job_dir / "raw"
    assert raw_dir.exists(), "raw/ directory must be created"

    # One HTML file per URL
    for url in urls:
        h = _url_hash(url)
        html_file = raw_dir / f"{h}.html"
        assert html_file.exists(), f"Expected HTML file {html_file}"

    # fetch_results.json must exist and count correctly
    data = _load_fetch_results(job_id)
    assert data["job_id"] == job_id
    assert data["total_urls"] == 3
    assert data["successful"] == 3
    assert data["failed"] == 0
    assert len(data["results"]) == 3


# ---------------------------------------------------------------------------
# Test 2: test_semaphore_limits_concurrency
# ---------------------------------------------------------------------------


async def test_semaphore_limits_concurrency() -> None:
    """MAX_CONCURRENT_FETCHES=2 → never more than 2 simultaneous requests."""
    job_id = _make_job_id()
    job_dir = _write_state(job_id)
    urls = [f"{BASE_URL}/page{i}" for i in range(5)]
    _write_routes(job_id, urls)

    current: list[int] = [0]
    max_seen: list[int] = [0]

    async def slow_handler(request: httpx.Request) -> httpx.Response:
        current[0] += 1
        max_seen[0] = max(max_seen[0], current[0])
        await asyncio.sleep(0.05)  # Real sleep — yields control to event loop
        current[0] -= 1
        return httpx.Response(200, text="<html>ok</html>")

    from app.config import settings as cfg
    from app.pipeline.fetcher import run_fetcher

    with respx.mock:
        for url in urls:
            respx.get(url).mock(side_effect=slow_handler)

        with patch.object(cfg, "MAX_CONCURRENT_FETCHES", 2):
            result = await run_fetcher(job_id)

    assert result is True
    assert max_seen[0] <= 2, (
        f"Semaphore not working: max concurrent fetches was {max_seen[0]}, expected ≤ 2"
    )


# ---------------------------------------------------------------------------
# Test 3: test_retry_on_timeout
# ---------------------------------------------------------------------------


@respx.mock
async def test_retry_on_timeout() -> None:
    """First call times out, second returns 200 → success after one retry."""
    job_id = _make_job_id()
    _write_state(job_id)
    url = f"{BASE_URL}/flaky"
    _write_routes(job_id, [url])

    call_count: list[int] = [0]

    def timeout_then_success(request: httpx.Request) -> httpx.Response:
        call_count[0] += 1
        if call_count[0] == 1:
            raise httpx.TimeoutException("timeout", request=request)
        return httpx.Response(200, text="<html>ok</html>")

    respx.get(url).mock(side_effect=timeout_then_success)

    from app.pipeline.fetcher import run_fetcher

    with patch("app.pipeline.fetcher._sleep", new_callable=AsyncMock):
        result = await run_fetcher(job_id)

    assert result is True
    assert call_count[0] == 2, f"Expected 2 attempts (1 timeout + 1 success), got {call_count[0]}"

    data = _load_fetch_results(job_id)
    entry = data["results"][0]
    assert entry["status"] == "success"
    assert entry["http_code"] == 200


# ---------------------------------------------------------------------------
# Test 4: test_retry_backoff_exhausted
# ---------------------------------------------------------------------------


@respx.mock
async def test_retry_backoff_exhausted() -> None:
    """All attempts time out → status: failed, error: timeout."""
    job_id = _make_job_id()
    _write_state(job_id)
    url = f"{BASE_URL}/dead"
    _write_routes(job_id, [url])

    call_count: list[int] = [0]

    def always_timeout(request: httpx.Request) -> httpx.Response:
        call_count[0] += 1
        raise httpx.TimeoutException("timeout", request=request)

    respx.get(url).mock(side_effect=always_timeout)

    from app.config import settings as cfg
    from app.pipeline.fetcher import run_fetcher

    with patch("app.pipeline.fetcher._sleep", new_callable=AsyncMock):
        result = await run_fetcher(job_id)

    # All failed → FETCH_ALL_FAILED → returns False
    assert result is False

    # All FETCH_RETRIES + 1 attempts were made
    expected_attempts = cfg.FETCH_RETRIES + 1
    assert call_count[0] == expected_attempts, (
        f"Expected {expected_attempts} attempts, got {call_count[0]}"
    )

    data = _load_fetch_results(job_id)
    assert data["successful"] == 0
    assert data["failed"] == 1
    entry = data["results"][0]
    assert entry["status"] == "failed"
    assert entry["error"] == "timeout"
    assert entry["http_code"] is None


# ---------------------------------------------------------------------------
# Test 5: test_404_no_retry
# ---------------------------------------------------------------------------


@respx.mock
async def test_404_no_retry() -> None:
    """404 response is NOT retried → failed immediately with http_error."""
    job_id = _make_job_id()
    _write_state(job_id)
    url = f"{BASE_URL}/missing"
    _write_routes(job_id, [url])

    call_count: list[int] = [0]

    def respond_404(request: httpx.Request) -> httpx.Response:
        call_count[0] += 1
        return httpx.Response(404)

    respx.get(url).mock(side_effect=respond_404)

    from app.pipeline.fetcher import run_fetcher

    with patch("app.pipeline.fetcher._sleep", new_callable=AsyncMock):
        result = await run_fetcher(job_id)

    assert result is False
    assert call_count[0] == 1, f"404 must not be retried, but made {call_count[0]} calls"

    data = _load_fetch_results(job_id)
    entry = data["results"][0]
    assert entry["status"] == "failed"
    assert entry["error"] == "http_error"
    assert entry["http_code"] == 404


# ---------------------------------------------------------------------------
# Test 6: test_html_written_to_disk
# ---------------------------------------------------------------------------


@respx.mock
async def test_html_written_to_disk() -> None:
    """HTML body is persisted to raw/<hash>.html with exact content."""
    job_id = _make_job_id()
    job_dir = _write_state(job_id)
    url = f"{BASE_URL}/content"
    _write_routes(job_id, [url])

    html_body = "<html><body><h1>Dealer Content</h1></body></html>"
    respx.get(url).mock(return_value=httpx.Response(200, text=html_body))

    from app.pipeline.fetcher import run_fetcher

    with patch("app.pipeline.fetcher._sleep", new_callable=AsyncMock):
        result = await run_fetcher(job_id)

    assert result is True

    h = _url_hash(url)
    file_path = job_dir / "raw" / f"{h}.html"
    assert file_path.exists(), "HTML file must exist on disk"
    content_on_disk = file_path.read_text(encoding="utf-8")
    assert content_on_disk == html_body, "HTML on disk must match HTTP response body"


# ---------------------------------------------------------------------------
# Test 7: test_fetch_results_json_format
# ---------------------------------------------------------------------------


@respx.mock
async def test_fetch_results_json_format() -> None:
    """fetch_results.json contains all required fields with correct types."""
    job_id = _make_job_id()
    _write_state(job_id)
    urls = [f"{BASE_URL}/good", f"{BASE_URL}/gone"]
    _write_routes(job_id, urls)

    respx.get(f"{BASE_URL}/good").mock(
        return_value=httpx.Response(200, text="<html>ok</html>")
    )
    respx.get(f"{BASE_URL}/gone").mock(return_value=httpx.Response(410))

    from app.pipeline.fetcher import run_fetcher

    with patch("app.pipeline.fetcher._sleep", new_callable=AsyncMock):
        await run_fetcher(job_id)

    data = _load_fetch_results(job_id)

    # Top-level fields
    assert "job_id" in data
    assert "total_urls" in data
    assert "successful" in data
    assert "failed" in data
    assert "results" in data
    assert data["total_urls"] == 2
    assert data["successful"] == 1
    assert data["failed"] == 1

    # Per-result fields
    required_keys = {"url", "url_hash", "status", "http_code", "file", "error"}
    for entry in data["results"]:
        assert required_keys == required_keys & entry.keys(), (
            f"Missing keys in result entry: {entry}"
        )
        assert entry["status"] in ("success", "failed")

    # success entry
    success = next(r for r in data["results"] if r["status"] == "success")
    assert success["http_code"] == 200
    assert success["file"] is not None
    assert success["error"] is None

    # failed entry
    failed = next(r for r in data["results"] if r["status"] == "failed")
    assert failed["file"] is None
    assert failed["error"] == "http_error"


# ---------------------------------------------------------------------------
# Test 8: test_fetch_all_failed
# ---------------------------------------------------------------------------


@respx.mock
async def test_fetch_all_failed() -> None:
    """All URLs fail → job marked failed with FETCH_ALL_FAILED error code."""
    job_id = _make_job_id()
    _write_state(job_id)
    urls = [f"{BASE_URL}/p1", f"{BASE_URL}/p2"]
    _write_routes(job_id, urls)

    for url in urls:
        respx.get(url).mock(return_value=httpx.Response(404))

    from app.pipeline.fetcher import run_fetcher

    with patch("app.pipeline.fetcher._sleep", new_callable=AsyncMock):
        result = await run_fetcher(job_id)

    assert result is False

    state = _load_state(job_id)
    assert state["status"] == "failed"
    assert state["error"] is not None
    assert state["error"]["code"] == "FETCH_ALL_FAILED"


# ---------------------------------------------------------------------------
# Test 9: test_partial_success
# ---------------------------------------------------------------------------


@respx.mock
async def test_partial_success() -> None:
    """1 of 3 URLs fails → successful=2, failed=1, returns True."""
    job_id = _make_job_id()
    _write_state(job_id)
    urls = [f"{BASE_URL}/ok1", f"{BASE_URL}/ok2", f"{BASE_URL}/fail"]
    _write_routes(job_id, urls)

    respx.get(f"{BASE_URL}/ok1").mock(
        return_value=httpx.Response(200, text="<html>ok</html>")
    )
    respx.get(f"{BASE_URL}/ok2").mock(
        return_value=httpx.Response(200, text="<html>ok</html>")
    )
    respx.get(f"{BASE_URL}/fail").mock(return_value=httpx.Response(404))

    from app.pipeline.fetcher import run_fetcher

    with patch("app.pipeline.fetcher._sleep", new_callable=AsyncMock):
        result = await run_fetcher(job_id)

    assert result is True

    data = _load_fetch_results(job_id)
    assert data["successful"] == 2
    assert data["failed"] == 1
    assert data["total_urls"] == 3


# ---------------------------------------------------------------------------
# Test 10: test_progress_updated
# ---------------------------------------------------------------------------


@respx.mock
async def test_progress_updated() -> None:
    """update_progress is called exactly once after each URL completes."""
    job_id = _make_job_id()
    _write_state(job_id)
    urls = [f"{BASE_URL}/p{i}" for i in range(3)]
    _write_routes(job_id, urls)

    for url in urls:
        respx.get(url).mock(return_value=httpx.Response(200, text="<html>ok</html>"))

    progress_calls: list[tuple[int, int]] = []

    async def capture_progress(jid: str, *, pages_done: int, pages_total: int) -> None:
        progress_calls.append((pages_done, pages_total))

    from app.pipeline.fetcher import run_fetcher

    with patch(
        "app.pipeline.fetcher.job_manager.update_progress",
        side_effect=capture_progress,
    ):
        with patch("app.pipeline.fetcher._sleep", new_callable=AsyncMock):
            result = await run_fetcher(job_id)

    assert result is True

    # update_progress must have been called once per URL
    assert len(progress_calls) == 3, (
        f"Expected 3 update_progress calls, got {len(progress_calls)}"
    )

    # pages_total is always the total count
    for pages_done, pages_total in progress_calls:
        assert pages_total == 3

    # pages_done values are 1, 2, 3 (in some order since gather() is unordered)
    done_values = sorted(pd for pd, _ in progress_calls)
    assert done_values == [1, 2, 3]


# ---------------------------------------------------------------------------
# Test 11: test_url_hash_correct
# ---------------------------------------------------------------------------


@respx.mock
async def test_url_hash_correct() -> None:
    """url_hash field and filename both equal sha256(url.encode()).hexdigest()."""
    job_id = _make_job_id()
    job_dir = _write_state(job_id)
    url = f"{BASE_URL}/specific-dealer-page"
    _write_routes(job_id, [url])

    expected_hash = hashlib.sha256(url.encode()).hexdigest()
    respx.get(url).mock(return_value=httpx.Response(200, text="<html>dealer</html>"))

    from app.pipeline.fetcher import run_fetcher

    with patch("app.pipeline.fetcher._sleep", new_callable=AsyncMock):
        result = await run_fetcher(job_id)

    assert result is True

    data = _load_fetch_results(job_id)
    entry = data["results"][0]

    assert entry["url_hash"] == expected_hash, (
        f"url_hash mismatch: got {entry['url_hash']}, expected {expected_hash}"
    )
    assert entry["file"] == f"raw/{expected_hash}.html", (
        f"file path mismatch: {entry['file']}"
    )

    # Actual file on disk uses the same hash
    file_path = job_dir / "raw" / f"{expected_hash}.html"
    assert file_path.exists(), f"Expected file {file_path} on disk"


# ---------------------------------------------------------------------------
# Test 12: test_status_set_to_fetching
# ---------------------------------------------------------------------------


@respx.mock
async def test_status_set_to_fetching() -> None:
    """Job status is set to 'fetching' at the beginning of run_fetcher."""
    job_id = _make_job_id()
    _write_state(job_id)
    url = f"{BASE_URL}/page"
    _write_routes(job_id, [url])

    respx.get(url).mock(return_value=httpx.Response(200, text="<html>ok</html>"))

    statuses_captured: list[str] = []

    from app.core.job_manager import job_manager as jm
    original_update_status = jm.update_status

    async def capturing_update_status(jid: str, status, **kwargs) -> None:
        val = status.value if hasattr(status, "value") else str(status)
        statuses_captured.append(val)
        return await original_update_status(jid, status, **kwargs)

    from app.pipeline.fetcher import run_fetcher

    with patch(
        "app.pipeline.fetcher.job_manager.update_status",
        side_effect=capturing_update_status,
    ):
        with patch("app.pipeline.fetcher._sleep", new_callable=AsyncMock):
            result = await run_fetcher(job_id)

    assert result is True
    assert "fetching" in statuses_captured, (
        f"Expected 'fetching' in captured statuses, got: {statuses_captured}"
    )

    # Confirm it's also persisted to disk
    state = _load_state(job_id)
    assert state["status"] == "fetching"
