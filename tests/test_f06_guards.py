"""
F06 — Guards: Timeout y Cleanup
Tests for CHECKPOINTS.md section F06.

Covers:
  - Guard 1 (global_job_timeout): fires JOB_TIMEOUT / does NOT fire if already done
  - Guard 2 (llm_watchdog): fires LLM_TIMEOUT / resets on activity / stops if done
  - Guard 3 (schedule_cleanup): deletes dir / no-op if dir missing
  - run_pipeline: stops when a subagent fails / cancels guard1 on completion
"""

import asyncio
import json
import os
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import settings
from app.core.guards import (
    global_job_timeout,
    llm_watchdog,
    run_pipeline,
    schedule_cleanup,
)
from app.core.job_manager import job_manager
from app.models.job import JobStatus

# ── Force pytest-asyncio to auto mode for all tests in this module ──────────
pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_job_id() -> str:
    return str(uuid.uuid4())


async def _create_job(url: str = "https://example.com") -> str:
    """Create a real job via job_manager and return the job_id."""
    return await job_manager.create_job(url=url, options={})


# ---------------------------------------------------------------------------
# Guard 1 — global_job_timeout
# ---------------------------------------------------------------------------


async def test_guard1_fires_on_timeout() -> None:
    """Guard 1 fails the job with JOB_TIMEOUT when the timeout elapses."""
    job_id = await _create_job()

    with patch.object(settings, "JOB_MAX_DURATION_SECONDS", 0.05):
        guard = asyncio.create_task(global_job_timeout(job_id))
        await asyncio.sleep(0.15)
        await guard

    state = await job_manager.get_state(job_id)
    assert state is not None
    assert state.status == JobStatus.failed
    assert state.error is not None
    assert state.error.code == "JOB_TIMEOUT"
    assert state.error.retry_after == 600


async def test_guard1_no_fire_if_done() -> None:
    """Guard 1 does NOT fail the job when it is already done before the timeout."""
    job_id = await _create_job()

    # Mark job as done before the guard fires
    await job_manager.complete_job(job_id)

    with patch.object(settings, "JOB_MAX_DURATION_SECONDS", 0.05):
        guard = asyncio.create_task(global_job_timeout(job_id))
        await asyncio.sleep(0.15)
        await guard

    state = await job_manager.get_state(job_id)
    assert state is not None
    assert state.status == JobStatus.done
    assert state.error is None


# ---------------------------------------------------------------------------
# Guard 2 — llm_watchdog
# ---------------------------------------------------------------------------


async def test_guard2_fires_on_inactivity() -> None:
    """Guard 2 fails the job with LLM_TIMEOUT when no event.set() occurs."""
    job_id = await _create_job()
    event = asyncio.Event()

    with patch.object(settings, "LLM_WATCHDOG_SECONDS", 0.05):
        watchdog = asyncio.create_task(llm_watchdog(job_id, event))
        await asyncio.sleep(0.2)
        # watchdog should have already completed (by failing the job)
        assert watchdog.done(), "Watchdog task should have finished"

    state = await job_manager.get_state(job_id)
    assert state is not None
    assert state.status == JobStatus.failed
    assert state.error is not None
    assert state.error.code == "LLM_TIMEOUT"
    assert state.error.retry_after == 300


async def test_guard2_resets_on_activity() -> None:
    """Guard 2 does NOT fail the job when activity_event.set() fires repeatedly."""
    job_id = await _create_job()
    event = asyncio.Event()

    async def periodic_activity() -> None:
        for _ in range(6):
            await asyncio.sleep(0.03)
            event.set()

    with patch.object(settings, "LLM_WATCHDOG_SECONDS", 0.1):
        watchdog = asyncio.create_task(llm_watchdog(job_id, event))
        activity_task = asyncio.create_task(periodic_activity())
        await asyncio.sleep(0.25)
        # Watchdog should still be running (job not failed)
        state = await job_manager.get_state(job_id)
        assert state is not None
        assert state.status != JobStatus.failed, "Job should NOT be failed while activity keeps resetting watchdog"

        # Clean up
        watchdog.cancel()
        activity_task.cancel()
        try:
            await watchdog
        except asyncio.CancelledError:
            pass
        try:
            await activity_task
        except asyncio.CancelledError:
            pass


async def test_guard2_stops_if_job_done() -> None:
    """Guard 2 exits cleanly when the job reaches done status."""
    job_id = await _create_job()
    event = asyncio.Event()

    async def finish_job_soon() -> None:
        await asyncio.sleep(0.05)
        await job_manager.complete_job(job_id)
        event.set()  # unblock the wait so the watchdog loop can check

    with patch.object(settings, "LLM_WATCHDOG_SECONDS", 1.0):
        watchdog = asyncio.create_task(llm_watchdog(job_id, event))
        finisher = asyncio.create_task(finish_job_soon())

        # Wait a bit more than the finisher
        await asyncio.sleep(0.3)
        await finisher

        # The watchdog should have exited because the job is done
        assert watchdog.done(), "Watchdog should have exited cleanly after job is done"

    state = await job_manager.get_state(job_id)
    assert state is not None
    assert state.status == JobStatus.done


# ---------------------------------------------------------------------------
# Guard 3 — schedule_cleanup
# ---------------------------------------------------------------------------


async def test_schedule_cleanup_deletes_dir(tmp_path: Path) -> None:
    """schedule_cleanup removes the job directory after the TTL delay."""
    job_id = _make_job_id()
    job_dir = tmp_path / job_id
    job_dir.mkdir(parents=True)
    (job_dir / "state.json").write_text("{}", encoding="utf-8")

    # Override JOB_BASE_DIR to tmp_path and TTL to a tiny value
    with patch.object(settings, "JOB_BASE_DIR", str(tmp_path)), \
         patch.object(settings, "RESULT_TTL_MINUTES", 0.001):  # ~0.06 seconds
        cleanup_task = asyncio.create_task(schedule_cleanup(job_id))
        await asyncio.sleep(0.2)
        await cleanup_task

    assert not job_dir.exists(), "Job directory should have been deleted by schedule_cleanup"


async def test_schedule_cleanup_noop_if_dir_missing(tmp_path: Path) -> None:
    """schedule_cleanup does not raise if the job directory is already gone."""
    job_id = _make_job_id()
    # Directory is intentionally NOT created

    with patch.object(settings, "JOB_BASE_DIR", str(tmp_path)), \
         patch.object(settings, "RESULT_TTL_MINUTES", 0.001):
        cleanup_task = asyncio.create_task(schedule_cleanup(job_id))
        await asyncio.sleep(0.2)
        # Should complete without error
        await cleanup_task  # raises if there was an unhandled exception


# ---------------------------------------------------------------------------
# run_pipeline — subagent failure and guard1 cancellation
# ---------------------------------------------------------------------------


async def test_pipeline_stops_on_explorer_failure() -> None:
    """If Explorer returns False, the pipeline stops and other subagents are NOT called."""
    job_id = await _create_job()

    with patch("app.core.guards.run_explorer", new_callable=AsyncMock, return_value=False) as mock_explorer, \
         patch("app.core.guards.run_fetcher", new_callable=AsyncMock, return_value=True) as mock_fetcher, \
         patch("app.core.guards.run_extractor", new_callable=AsyncMock, return_value=True) as mock_extractor, \
         patch("app.core.guards.run_auditor", new_callable=AsyncMock, return_value=True) as mock_auditor:

        await run_pipeline(job_id)

        mock_explorer.assert_called_once_with(job_id)
        mock_fetcher.assert_not_called()
        mock_extractor.assert_not_called()
        mock_auditor.assert_not_called()


async def test_pipeline_cancel_guard1_on_completion() -> None:
    """Guard 1 task is cancelled when the pipeline finishes normally."""
    job_id = await _create_job()
    captured_guard: list[asyncio.Task] = []

    original_register = job_manager.register_task

    def _capture_register(jid: str, task: asyncio.Task) -> None:
        if jid == job_id:
            captured_guard.append(task)
        original_register(jid, task)

    with patch("app.core.guards.run_explorer", new_callable=AsyncMock, return_value=True), \
         patch("app.core.guards.run_fetcher", new_callable=AsyncMock, return_value=True), \
         patch("app.core.guards.run_extractor", new_callable=AsyncMock, return_value=True), \
         patch("app.core.guards.run_auditor", new_callable=AsyncMock, return_value=True), \
         patch("app.core.guards.run_reviewer", new_callable=AsyncMock, return_value=True), \
         patch.object(job_manager, "register_task", side_effect=_capture_register), \
         patch.object(settings, "JOB_MAX_DURATION_SECONDS", 60):

        await run_pipeline(job_id)

    # Guard 1 (index 0) and Guard 2 (index 1) should both be registered and done
    assert len(captured_guard) == 2, (
        f"Expected guard1 + guard2 registered (2 tasks), got {len(captured_guard)}"
    )
    guard1 = captured_guard[0]
    guard2 = captured_guard[1]
    assert guard1.done(), "Guard 1 task should be done (cancelled) after pipeline completes"
    assert guard2.done(), "Guard 2 task should be done (cancelled) after pipeline completes"
