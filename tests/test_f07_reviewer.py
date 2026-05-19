"""
F07 — Reviewer: LLM Analysis
Tests for CHECKPOINTS.md section F07.

Covers:
  - Successful analysis with openai provider (result.json created)
  - Successful analysis with anthropic provider
  - LLM 401 → job fails with LLM_AUTH_ERROR
  - LLM 429 with Retry-After → retries once, succeeds
  - LLM JSON parse error twice → LLM_PARSE_ERROR
  - activity_event.set() called ≥5 times during a run
  - Job already failed before LLM call → returns False without calling LLM
  - chunk_summaries/ cleaned up after successful run
"""

import asyncio
import hashlib
import json
import os
import uuid
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
import respx

from app.core.job_manager import job_manager
from app.models.job import JobStatus
from app.pipeline.reviewer import LLMAuthError, LLMClient, LLMParseError, run_reviewer

# ── Force pytest-asyncio to auto mode for all tests in this module ──────────
pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_OPENAI_URL = "https://api.openai.com/v1/chat/completions"
_ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"


# ---------------------------------------------------------------------------
# Mock response payloads
# ---------------------------------------------------------------------------

MOCK_BATCH_RESPONSE: dict = {
    "business_name": "Test Dealer",
    "business_type": "car_dealer",
    "description": "A car dealership",
    "language": "en",
    "key_topics": ["cars"],
    "contact_info": {"phone": None, "email": None, "address": None},
    "pages_summary": [
        {
            "url": "https://example.com/",
            "title": "Home",
            "summary": "...",
            "key_points": [],
        }
    ],
    "images": [{"src": "https://example.com/img.jpg", "alt": "img"}],
}

MOCK_MERGE_RESPONSE: dict = {
    "business_name": "Test Dealer",
    "business_type": "car_dealer",
    "description": "A car dealership.",
    "language": "en",
    "address": None,
    "phone": None,
    "email": None,
    "social_links": [],
    "main_topics": ["cars"],
    "key_pages": [],
    "images": [{"src": "https://example.com/img.jpg", "alt": "img"}],
}


def _openai_response(content_dict: dict) -> httpx.Response:
    """Wrap a dict in an OpenAI-style chat completion response."""
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": json.dumps(content_dict)}}]
        },
    )


def _anthropic_response(content_dict: dict) -> httpx.Response:
    """Wrap a dict in an Anthropic-style messages response."""
    return httpx.Response(
        200,
        json={
            "content": [{"type": "text", "text": json.dumps(content_dict)}]
        },
    )


# ---------------------------------------------------------------------------
# Job setup helper
# ---------------------------------------------------------------------------


def _make_job_with_pages(n_pages: int = 1, status: str = "auditing") -> str:
    """
    Creates a fully populated job directory in JOB_BASE_DIR with:
    - state.json
    - audit_report.json  (pages format: list of {url, url_hash, valid})
    - pages/<url_hash>.json  (one per page)

    Returns the job_id string.
    """
    job_id = str(uuid.uuid4())
    job_base = Path(os.environ["JOB_BASE_DIR"])
    job_dir = job_base / job_id
    (job_dir / "pages").mkdir(parents=True)

    pages: list[dict] = []
    for i in range(n_pages):
        url = f"https://example.com/page{i}"
        url_hash = hashlib.sha256(url.encode()).hexdigest()
        page_data = {
            "url": url,
            "url_hash": url_hash,
            "title": f"Page {i}",
            "meta_description": "",
            "meta_keywords": [],
            "og_data": {},
            "canonical_url": "",
            "language": "en",
            "headings": {"h1": [f"Page {i}"], "h2": [], "h3": []},
            "text_content": f"Page {i} content. " * 30,  # >50 words
            "word_count": 90,
            "internal_links": [],
            "external_links": [],
            "images": [
                {
                    "src": "https://example.com/img.jpg",
                    "alt": "img",
                    "width": None,
                    "height": None,
                }
            ],
            "schema_org": [],
            "has_forms": False,
            "has_tables": False,
            "extracted_at": "2026-05-19T10:00:00Z",
        }
        (job_dir / "pages" / f"{url_hash}.json").write_text(
            json.dumps(page_data), encoding="utf-8"
        )
        pages.append({"url": url, "url_hash": url_hash, "valid": True})

    audit_report = {
        "job_id": job_id,
        "coverage_percent": 100.0,
        "critical": False,
        "second_pass": False,
        "needs_refetch": False,
        "pages": pages,
        "total_routes": n_pages,
        "pages_fetched": n_pages,
    }
    (job_dir / "audit_report.json").write_text(
        json.dumps(audit_report), encoding="utf-8"
    )

    state = {
        "job_id": job_id,
        "status": status,
        "url": "https://example.com",
        "options": {},
        "progress": {
            "phase": "auditing",
            "pages_done": n_pages,
            "pages_total": n_pages,
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
    return job_id


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@respx.mock
async def test_reviewer_success_openai() -> None:
    """Successful LLM analysis with openai provider writes result.json."""
    job_id = _make_job_with_pages(n_pages=1)

    # Both batch and merge call go to the same OpenAI URL.
    # respx.mock intercepts all calls; we queue two side_effects.
    call_count = 0
    responses = [
        _openai_response(MOCK_BATCH_RESPONSE),
        _openai_response(MOCK_MERGE_RESPONSE),
    ]

    def _side_effect(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        resp = responses[call_count]
        call_count += 1
        return resp

    respx.post(_OPENAI_URL).mock(side_effect=_side_effect)

    activity_event = asyncio.Event()
    with patch("app.pipeline.reviewer.settings") as mock_settings:
        mock_settings.LLM_PROVIDER = "openai"
        mock_settings.LLM_MODEL = "gpt-4o-mini"
        mock_settings.LLM_API_KEY = "test-key"
        mock_settings.LLM_MAX_TOKENS = 4000
        mock_settings.LLM_TEMPERATURE = 0.2
        mock_settings.JOB_BASE_DIR = os.environ["JOB_BASE_DIR"]

        result = await run_reviewer(job_id, activity_event)

    assert result is True, "run_reviewer should return True on success"

    result_path = (
        Path(os.environ["JOB_BASE_DIR"]) / job_id / "result.json"
    )
    assert result_path.exists(), "result.json must be created"

    data = json.loads(result_path.read_text(encoding="utf-8"))
    assert data["job_id"] == job_id
    assert data["business"]["name"] == "Test Dealer"
    assert data["business"]["type"] == "car_dealer"
    assert "assets" in data
    assert "metadata" in data
    assert data["metadata"]["pages_analyzed"] == 1


@respx.mock
async def test_reviewer_success_anthropic() -> None:
    """Successful LLM analysis with anthropic provider writes result.json."""
    job_id = _make_job_with_pages(n_pages=1)

    call_count = 0
    responses = [
        _anthropic_response(MOCK_BATCH_RESPONSE),
        _anthropic_response(MOCK_MERGE_RESPONSE),
    ]

    def _side_effect(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        resp = responses[call_count]
        call_count += 1
        return resp

    respx.post(_ANTHROPIC_URL).mock(side_effect=_side_effect)

    activity_event = asyncio.Event()
    with patch("app.pipeline.reviewer.settings") as mock_settings:
        mock_settings.LLM_PROVIDER = "anthropic"
        mock_settings.LLM_MODEL = "claude-3-haiku-20240307"
        mock_settings.LLM_API_KEY = "test-anthropic-key"
        mock_settings.LLM_MAX_TOKENS = 4000
        mock_settings.LLM_TEMPERATURE = 0.2
        mock_settings.JOB_BASE_DIR = os.environ["JOB_BASE_DIR"]

        result = await run_reviewer(job_id, activity_event)

    assert result is True

    result_path = (
        Path(os.environ["JOB_BASE_DIR"]) / job_id / "result.json"
    )
    assert result_path.exists()
    data = json.loads(result_path.read_text(encoding="utf-8"))
    assert data["llm_provider"] == "anthropic"
    assert data["business"]["name"] == "Test Dealer"


@respx.mock
async def test_reviewer_auth_error() -> None:
    """LLM 401 → job fails with LLM_AUTH_ERROR, run_reviewer returns False."""
    job_id = _make_job_with_pages(n_pages=1)

    respx.post(_OPENAI_URL).mock(
        return_value=httpx.Response(401, json={"error": "Unauthorized"})
    )

    activity_event = asyncio.Event()
    with patch("app.pipeline.reviewer.settings") as mock_settings:
        mock_settings.LLM_PROVIDER = "openai"
        mock_settings.LLM_MODEL = "gpt-4o-mini"
        mock_settings.LLM_API_KEY = "bad-key"
        mock_settings.LLM_MAX_TOKENS = 4000
        mock_settings.LLM_TEMPERATURE = 0.2
        mock_settings.JOB_BASE_DIR = os.environ["JOB_BASE_DIR"]

        result = await run_reviewer(job_id, activity_event)

    assert result is False

    state = await job_manager.get_state(job_id)
    assert state is not None
    assert state.status == JobStatus.failed
    assert state.error is not None
    assert state.error.code == "LLM_AUTH_ERROR"


@respx.mock
async def test_reviewer_429_retry() -> None:
    """LLM 429 with Retry-After header → waits and retries once, succeeds."""
    job_id = _make_job_with_pages(n_pages=1)

    call_count = 0
    # First batch call → 429; second batch call → 200; merge call → 200
    batch_call = 0

    def _side_effect(request: httpx.Request) -> httpx.Response:
        nonlocal batch_call
        if batch_call == 0:
            batch_call += 1
            return httpx.Response(
                429,
                json={"error": "rate limit"},
                headers={"Retry-After": "1"},
            )
        if batch_call == 1:
            batch_call += 1
            return _openai_response(MOCK_BATCH_RESPONSE)
        # merge call
        return _openai_response(MOCK_MERGE_RESPONSE)

    respx.post(_OPENAI_URL).mock(side_effect=_side_effect)

    activity_event = asyncio.Event()
    with patch("app.pipeline.reviewer.settings") as mock_settings, \
         patch("asyncio.sleep", return_value=None):
        mock_settings.LLM_PROVIDER = "openai"
        mock_settings.LLM_MODEL = "gpt-4o-mini"
        mock_settings.LLM_API_KEY = "test-key"
        mock_settings.LLM_MAX_TOKENS = 4000
        mock_settings.LLM_TEMPERATURE = 0.2
        mock_settings.JOB_BASE_DIR = os.environ["JOB_BASE_DIR"]

        result = await run_reviewer(job_id, activity_event)

    assert result is True
    result_path = Path(os.environ["JOB_BASE_DIR"]) / job_id / "result.json"
    assert result_path.exists()


@respx.mock
async def test_reviewer_parse_error() -> None:
    """LLM returns invalid JSON twice → job fails with LLM_PARSE_ERROR."""
    job_id = _make_job_with_pages(n_pages=1)

    # Both calls (original + retry) return non-JSON content
    respx.post(_OPENAI_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": "this is not valid json {{{{"}}]
            },
        )
    )

    activity_event = asyncio.Event()
    with patch("app.pipeline.reviewer.settings") as mock_settings:
        mock_settings.LLM_PROVIDER = "openai"
        mock_settings.LLM_MODEL = "gpt-4o-mini"
        mock_settings.LLM_API_KEY = "test-key"
        mock_settings.LLM_MAX_TOKENS = 4000
        mock_settings.LLM_TEMPERATURE = 0.2
        mock_settings.JOB_BASE_DIR = os.environ["JOB_BASE_DIR"]

        result = await run_reviewer(job_id, activity_event)

    assert result is False

    state = await job_manager.get_state(job_id)
    assert state is not None
    assert state.status == JobStatus.failed
    assert state.error is not None
    assert state.error.code == "LLM_PARSE_ERROR"


@respx.mock
async def test_reviewer_activity_event_set() -> None:
    """activity_event.set() is called at least 5 times during a successful run."""
    job_id = _make_job_with_pages(n_pages=1)

    call_count = 0
    responses = [
        _openai_response(MOCK_BATCH_RESPONSE),
        _openai_response(MOCK_MERGE_RESPONSE),
    ]

    def _side_effect(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        resp = responses[call_count]
        call_count += 1
        return resp

    respx.post(_OPENAI_URL).mock(side_effect=_side_effect)

    set_count = 0
    original_event = asyncio.Event()
    original_set = original_event.set

    class CountingEvent:
        """Wraps asyncio.Event and counts set() calls."""
        def __init__(self) -> None:
            self._event = asyncio.Event()
            self.set_count = 0

        def set(self) -> None:
            self.set_count += 1
            self._event.set()

        def clear(self) -> None:
            self._event.clear()

        async def wait(self) -> None:
            await self._event.wait()

        def is_set(self) -> bool:
            return self._event.is_set()

    counting_event = CountingEvent()

    with patch("app.pipeline.reviewer.settings") as mock_settings:
        mock_settings.LLM_PROVIDER = "openai"
        mock_settings.LLM_MODEL = "gpt-4o-mini"
        mock_settings.LLM_API_KEY = "test-key"
        mock_settings.LLM_MAX_TOKENS = 4000
        mock_settings.LLM_TEMPERATURE = 0.2
        mock_settings.JOB_BASE_DIR = os.environ["JOB_BASE_DIR"]

        result = await run_reviewer(job_id, counting_event)  # type: ignore[arg-type]

    assert result is True
    assert counting_event.set_count >= 5, (
        f"activity_event.set() should be called ≥5 times, got {counting_event.set_count}"
    )


async def test_reviewer_aborts_if_failed() -> None:
    """If job status is already 'failed' before LLM call, returns False without calling LLM."""
    job_id = _make_job_with_pages(n_pages=1, status="auditing")

    # Manually fail the job before running the reviewer
    await job_manager.fail_job(
        job_id, "SOME_PRIOR_ERROR", "Previously failed"
    )

    llm_called = False

    async def _fake_complete(*args, **kwargs) -> str:
        nonlocal llm_called
        llm_called = True
        return json.dumps(MOCK_BATCH_RESPONSE)

    activity_event = asyncio.Event()
    with patch("app.pipeline.reviewer.settings") as mock_settings, \
         patch.object(LLMClient, "complete", side_effect=_fake_complete):
        mock_settings.LLM_PROVIDER = "openai"
        mock_settings.LLM_MODEL = "gpt-4o-mini"
        mock_settings.LLM_API_KEY = "test-key"
        mock_settings.LLM_MAX_TOKENS = 4000
        mock_settings.LLM_TEMPERATURE = 0.2
        mock_settings.JOB_BASE_DIR = os.environ["JOB_BASE_DIR"]

        result = await run_reviewer(job_id, activity_event)

    assert result is False
    assert not llm_called, "LLM should NOT be called when job is already failed"


@respx.mock
async def test_chunk_summaries_cleaned() -> None:
    """After a successful run, the chunk_summaries/ directory is removed."""
    job_id = _make_job_with_pages(n_pages=1)

    call_count = 0
    responses = [
        _openai_response(MOCK_BATCH_RESPONSE),
        _openai_response(MOCK_MERGE_RESPONSE),
    ]

    def _side_effect(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        resp = responses[call_count]
        call_count += 1
        return resp

    respx.post(_OPENAI_URL).mock(side_effect=_side_effect)

    activity_event = asyncio.Event()
    with patch("app.pipeline.reviewer.settings") as mock_settings:
        mock_settings.LLM_PROVIDER = "openai"
        mock_settings.LLM_MODEL = "gpt-4o-mini"
        mock_settings.LLM_API_KEY = "test-key"
        mock_settings.LLM_MAX_TOKENS = 4000
        mock_settings.LLM_TEMPERATURE = 0.2
        mock_settings.JOB_BASE_DIR = os.environ["JOB_BASE_DIR"]

        result = await run_reviewer(job_id, activity_event)

    assert result is True

    chunk_dir = Path(os.environ["JOB_BASE_DIR"]) / job_id / "chunk_summaries"
    assert not chunk_dir.exists(), (
        "chunk_summaries/ should be deleted after a successful run"
    )
