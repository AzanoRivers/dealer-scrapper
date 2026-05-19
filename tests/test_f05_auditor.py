"""
F05 — Auditor: Coverage
Tests for checkpoints in CHECKPOINTS.md and Features/f05-auditor-coverage.md

No HTTP mocking — the Auditor reads local files from disk only.
"""

import hashlib
import json
import os
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

from app.config import settings

# ── Force pytest-asyncio to auto mode for all tests in this module ──────────
pytestmark = pytest.mark.asyncio

BASE_URL = "https://example.com"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_job_id() -> str:
    return str(uuid.uuid4())


def _url_hash(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()


def _write_state(job_id: str, url: str = BASE_URL) -> Path:
    """Write a minimal state.json for the given job_id in JOB_BASE_DIR."""
    job_base = os.environ["JOB_BASE_DIR"]
    job_dir = Path(job_base) / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    now = "2026-01-01T00:00:00Z"
    state = {
        "job_id": job_id,
        "status": "extracting",
        "url": url,
        "options": {},
        "progress": {
            "phase": "extracting",
            "pages_done": 0,
            "pages_total": 0,
            "percent": 0,
        },
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


def _write_routes(job_dir: Path, urls: list[str], base_url: str = BASE_URL) -> None:
    """Write routes.json in the same format as the Explorer produces."""
    routes = [{"url": url, "source": "sitemap", "priority": None} for url in urls]
    payload = {
        "job_id": job_dir.name,
        "base_url": base_url,
        "total_routes": len(routes),
        "discovery_method": "sitemap",
        "routes": routes,
    }
    (job_dir / "routes.json").write_text(json.dumps(payload), encoding="utf-8")


def _write_page(
    job_dir: Path,
    url: str,
    extra: dict | None = None,
    remove_fields: list[str] | None = None,
) -> None:
    """
    Write pages/<hash>.json with minimal valid PageData.
    Use `extra` to override any field.
    Use `remove_fields` to remove specific keys (simulates missing fields).
    Default word_count=60 (≥ 50 threshold).
    """
    pages_dir = job_dir / "pages"
    pages_dir.mkdir(exist_ok=True)
    h = _url_hash(url)

    page_data: dict = {
        "url": url,
        "url_hash": h,
        "title": "Test Page",
        "meta_description": "",
        "meta_keywords": [],
        "og_data": {},
        "canonical_url": "",
        "language": "",
        "headings": {"h1": [], "h2": [], "h3": []},
        "text_content": "word " * 60,
        "word_count": 60,
        "internal_links": [],
        "external_links": [],
        "images": [],
        "schema_org": [],
        "has_forms": False,
        "has_tables": False,
        "extracted_at": "2026-01-01T00:00:00Z",
    }

    if extra:
        page_data.update(extra)

    if remove_fields:
        for field in remove_fields:
            page_data.pop(field, None)

    (pages_dir / f"{h}.json").write_text(json.dumps(page_data), encoding="utf-8")


def _load_audit_report(job_id: str) -> dict:
    job_base = os.environ["JOB_BASE_DIR"]
    path = Path(job_base) / job_id / "audit_report.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _load_state(job_id: str) -> dict:
    job_base = os.environ["JOB_BASE_DIR"]
    path = Path(job_base) / job_id / "state.json"
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Test 1: test_full_coverage
# ---------------------------------------------------------------------------


async def test_full_coverage() -> None:
    """5 routes, 5 pages ≥50 words → coverage=100%, critical=False, return True."""
    job_id = _make_job_id()
    job_dir = _write_state(job_id)

    urls = [f"{BASE_URL}/page-{i}" for i in range(5)]
    _write_routes(job_dir, urls)
    for url in urls:
        _write_page(job_dir, url)

    from app.pipeline.auditor import run_auditor

    result = await run_auditor(job_id)
    assert result is True

    report = _load_audit_report(job_id)
    assert report["coverage"]["total_routes"] == 5
    assert report["coverage"]["extracted_pages"] == 5
    assert report["coverage"]["coverage_percent"] == 100.0
    assert report["coverage"]["critical"] is False
    assert report["extraction_quality"]["quality"] == "good"


# ---------------------------------------------------------------------------
# Test 2: test_partial_coverage
# ---------------------------------------------------------------------------


async def test_partial_coverage() -> None:
    """5 routes, 3 pages → coverage=60%, coverage_low=True, critical=False, return True."""
    job_id = _make_job_id()
    job_dir = _write_state(job_id)

    urls = [f"{BASE_URL}/page-{i}" for i in range(5)]
    _write_routes(job_dir, urls)
    for url in urls[:3]:
        _write_page(job_dir, url)

    from app.pipeline.auditor import run_auditor

    result = await run_auditor(job_id)
    assert result is True

    report = _load_audit_report(job_id)
    assert report["coverage"]["coverage_percent"] == 60.0
    assert report["coverage"]["coverage_low"] is True
    assert report["coverage"]["critical"] is False


# ---------------------------------------------------------------------------
# Test 3: test_critical_coverage_first_pass
# ---------------------------------------------------------------------------


async def test_critical_coverage_first_pass() -> None:
    """10 routes, 2 pages → 20% < 30% → critical=True, second_pass=False → return True (no falla)."""
    job_id = _make_job_id()
    job_dir = _write_state(job_id)

    urls = [f"{BASE_URL}/page-{i}" for i in range(10)]
    _write_routes(job_dir, urls)
    for url in urls[:2]:
        _write_page(job_dir, url)

    from app.pipeline.auditor import run_auditor

    result = await run_auditor(job_id, second_pass=False)
    assert result is True

    report = _load_audit_report(job_id)
    assert report["coverage"]["coverage_percent"] == 20.0
    assert report["coverage"]["critical"] is True

    # Job should NOT be failed on first pass
    state = _load_state(job_id)
    assert state["status"] != "failed"


# ---------------------------------------------------------------------------
# Test 4: test_critical_coverage_second_pass
# ---------------------------------------------------------------------------


async def test_critical_coverage_second_pass() -> None:
    """10 routes, 2 pages, second_pass=True → return False, job failed AUDIT_CRITICAL_GAPS."""
    job_id = _make_job_id()
    job_dir = _write_state(job_id)

    urls = [f"{BASE_URL}/page-{i}" for i in range(10)]
    _write_routes(job_dir, urls)
    for url in urls[:2]:
        _write_page(job_dir, url)

    from app.pipeline.auditor import run_auditor

    result = await run_auditor(job_id, second_pass=True)
    assert result is False

    state = _load_state(job_id)
    assert state["status"] == "failed"
    assert state["error"]["code"] == "AUDIT_CRITICAL_GAPS"


# ---------------------------------------------------------------------------
# Test 5: test_new_routes_discovered
# ---------------------------------------------------------------------------


async def test_new_routes_discovered() -> None:
    """Pages with internal_links not in routes → new_routes contains those URLs."""
    job_id = _make_job_id()
    job_dir = _write_state(job_id)

    urls = [f"{BASE_URL}/page-{i}" for i in range(3)]
    _write_routes(job_dir, urls)

    new_url_a = f"{BASE_URL}/new-page-a"
    new_url_b = f"{BASE_URL}/new-page-b"

    _write_page(job_dir, urls[0], extra={"internal_links": [new_url_a, new_url_b]})
    _write_page(job_dir, urls[1], extra={"internal_links": [new_url_a]})
    _write_page(job_dir, urls[2])

    from app.pipeline.auditor import run_auditor

    result = await run_auditor(job_id)
    assert result is True

    report = _load_audit_report(job_id)
    # new_url_a appears twice (higher frequency), new_url_b once
    assert new_url_a in report["new_routes"]
    assert new_url_b in report["new_routes"]
    # new_url_a should be first (most referenced)
    assert report["new_routes"][0] == new_url_a


# ---------------------------------------------------------------------------
# Test 6: test_new_routes_empty_on_second_pass
# ---------------------------------------------------------------------------


async def test_new_routes_empty_on_second_pass() -> None:
    """Same config as test 5, but second_pass=True → new_routes == []."""
    job_id = _make_job_id()
    job_dir = _write_state(job_id)

    urls = [f"{BASE_URL}/page-{i}" for i in range(3)]
    _write_routes(job_dir, urls)

    new_url = f"{BASE_URL}/new-page-x"
    _write_page(job_dir, urls[0], extra={"internal_links": [new_url]})
    _write_page(job_dir, urls[1])
    _write_page(job_dir, urls[2])

    from app.pipeline.auditor import run_auditor

    # 3/3 pages = 100% coverage, not critical, but second_pass=True → new_routes empty
    result = await run_auditor(job_id, second_pass=True)
    assert result is True

    report = _load_audit_report(job_id)
    assert report["new_routes"] == []


# ---------------------------------------------------------------------------
# Test 7: test_no_new_routes_when_refetch_disabled
# ---------------------------------------------------------------------------


async def test_no_new_routes_when_refetch_disabled() -> None:
    """AUDIT_REFETCH_ENABLED=False → new_routes == [] even with unvisited internal_links."""
    job_id = _make_job_id()
    job_dir = _write_state(job_id)

    urls = [f"{BASE_URL}/page-{i}" for i in range(3)]
    _write_routes(job_dir, urls)

    new_url = f"{BASE_URL}/new-page-y"
    _write_page(job_dir, urls[0], extra={"internal_links": [new_url]})
    _write_page(job_dir, urls[1])
    _write_page(job_dir, urls[2])

    from app.pipeline.auditor import run_auditor

    with patch.object(settings, "AUDIT_REFETCH_ENABLED", False):
        result = await run_auditor(job_id)

    assert result is True

    report = _load_audit_report(job_id)
    assert report["new_routes"] == []


# ---------------------------------------------------------------------------
# Test 8: test_extraction_quality_poor
# ---------------------------------------------------------------------------


async def test_extraction_quality_poor() -> None:
    """All pages with word_count < 50 → quality=='poor', empty_ratio > 0.5."""
    job_id = _make_job_id()
    job_dir = _write_state(job_id)

    urls = [f"{BASE_URL}/page-{i}" for i in range(4)]
    _write_routes(job_dir, urls)
    for url in urls:
        _write_page(job_dir, url, extra={"word_count": 10, "text_content": "short text"})

    from app.pipeline.auditor import run_auditor

    result = await run_auditor(job_id)
    assert result is True

    report = _load_audit_report(job_id)
    assert report["extraction_quality"]["quality"] == "poor"
    assert report["extraction_quality"]["empty_ratio"] > 0.5
    assert report["extraction_quality"]["empty_pages_count"] == 4


# ---------------------------------------------------------------------------
# Test 9: test_invalid_pages_excluded
# ---------------------------------------------------------------------------


async def test_invalid_pages_excluded() -> None:
    """Page without 'title' field → in invalid_pages, NOT in valid_pages."""
    job_id = _make_job_id()
    job_dir = _write_state(job_id)

    urls = [f"{BASE_URL}/good", f"{BASE_URL}/bad"]
    _write_routes(job_dir, urls)

    # Good page: all fields present, word_count >= 50
    _write_page(job_dir, urls[0])

    # Bad page: missing 'title' field
    _write_page(job_dir, urls[1], remove_fields=["title"])

    from app.pipeline.auditor import run_auditor

    result = await run_auditor(job_id)
    assert result is True

    report = _load_audit_report(job_id)
    good_hash = _url_hash(urls[0])
    bad_hash = _url_hash(urls[1])

    assert good_hash in report["valid_pages"]
    assert bad_hash not in report["valid_pages"]
    assert bad_hash in report["invalid_pages"]
    assert good_hash not in report["invalid_pages"]


# ---------------------------------------------------------------------------
# Test 10: test_no_routes_json
# ---------------------------------------------------------------------------


async def test_no_routes_json() -> None:
    """Without routes.json → job fails with AUDIT_CRITICAL_GAPS, return False."""
    job_id = _make_job_id()
    _write_state(job_id)
    # Intentionally do NOT write routes.json

    from app.pipeline.auditor import run_auditor

    result = await run_auditor(job_id)
    assert result is False

    state = _load_state(job_id)
    assert state["status"] == "failed"
    assert state["error"]["code"] == "AUDIT_CRITICAL_GAPS"


# ---------------------------------------------------------------------------
# Test 11: test_audit_report_schema
# ---------------------------------------------------------------------------


async def test_audit_report_schema() -> None:
    """Verify audit_report.json contains all required schema fields."""
    job_id = _make_job_id()
    job_dir = _write_state(job_id)

    urls = [f"{BASE_URL}/page-{i}" for i in range(3)]
    _write_routes(job_dir, urls)
    for url in urls:
        _write_page(job_dir, url)

    from app.pipeline.auditor import run_auditor

    result = await run_auditor(job_id)
    assert result is True

    report = _load_audit_report(job_id)

    # Top-level keys
    assert "job_id" in report
    assert "second_pass" in report
    assert "audited_at" in report
    assert "coverage" in report
    assert "key_pages" in report
    assert "extraction_quality" in report
    assert "new_routes" in report
    assert "invalid_pages" in report
    assert "valid_pages" in report
    assert "summary" in report

    # coverage sub-fields
    cov = report["coverage"]
    assert "total_routes" in cov
    assert "extracted_pages" in cov
    assert "coverage_percent" in cov
    assert "coverage_low" in cov
    assert "critical" in cov

    # key_pages sub-fields
    kp = report["key_pages"]
    assert "has_homepage" in kp
    assert "has_form_page" in kp

    # extraction_quality sub-fields
    eq = report["extraction_quality"]
    assert "empty_pages_count" in eq
    assert "empty_ratio" in eq
    assert "quality" in eq

    # Types
    assert isinstance(report["job_id"], str)
    assert isinstance(report["second_pass"], bool)
    assert isinstance(report["audited_at"], str)
    assert isinstance(report["new_routes"], list)
    assert isinstance(report["invalid_pages"], list)
    assert isinstance(report["valid_pages"], list)
    assert isinstance(report["summary"], str)
    assert len(report["summary"]) > 0


# ---------------------------------------------------------------------------
# Test 12: test_new_routes_capped_at_max
# ---------------------------------------------------------------------------


async def test_new_routes_capped_at_max() -> None:
    """Pages with 20 unique new internal_links → new_routes capped at AUDIT_MAX_NEW_ROUTES (10)."""
    job_id = _make_job_id()
    job_dir = _write_state(job_id)

    urls = [f"{BASE_URL}/page-{i}" for i in range(3)]
    _write_routes(job_dir, urls)

    # Generate 20 unique new URLs not in routes
    new_urls = [f"{BASE_URL}/new-{i}" for i in range(20)]

    # First page links to all 20 new URLs
    _write_page(job_dir, urls[0], extra={"internal_links": new_urls})
    _write_page(job_dir, urls[1])
    _write_page(job_dir, urls[2])

    from app.pipeline.auditor import run_auditor

    result = await run_auditor(job_id)
    assert result is True

    report = _load_audit_report(job_id)
    assert len(report["new_routes"]) == settings.AUDIT_MAX_NEW_ROUTES
    # All returned routes must be from new_urls
    for nr in report["new_routes"]:
        assert nr in new_urls
