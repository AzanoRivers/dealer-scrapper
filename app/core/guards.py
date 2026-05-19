"""
Guards: Timeout y Cleanup para el pipeline de DealerScrapper.

Guard 1 — global_job_timeout: falla el job con JOB_TIMEOUT tras 30 min.
Guard 2 — llm_watchdog: falla con LLM_TIMEOUT si no hay actividad LLM en 5 min.
Guard 3 — schedule_cleanup: elimina el directorio del job 15 min después de done/failed.
run_pipeline — orquesta Explorer → Fetcher → Extractor → Auditor con Guard 1 activo.
"""

import asyncio
import json
import logging
import shutil
from pathlib import Path

from app.config import settings
from app.core.job_manager import job_manager
from app.models.job import JobStatus
from app.pipeline.auditor import run_auditor
from app.pipeline.explorer import run_explorer
from app.pipeline.extractor import run_extractor
from app.pipeline.fetcher import run_fetcher
from app.pipeline.packager import run_packager
from app.pipeline.reviewer import run_reviewer

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Guard 1 — Global job timeout (all phases)
# ---------------------------------------------------------------------------

async def global_job_timeout(job_id: str) -> None:
    """
    Launched when a job starts executing (status: exploring).
    Fails the job with JOB_TIMEOUT if it is still running after
    JOB_MAX_DURATION_SECONDS seconds.

    Uses loop.call_later / Future instead of asyncio.sleep so that
    test patches on asyncio.sleep do not inadvertently fire this guard.
    """
    loop = asyncio.get_running_loop()
    future: asyncio.Future = loop.create_future()
    handle = loop.call_later(
        float(settings.JOB_MAX_DURATION_SECONDS),
        future.set_result,
        None,
    )
    try:
        await future
    except asyncio.CancelledError:
        handle.cancel()
        raise

    state = await job_manager.get_state(job_id)
    if state is not None and state.status not in (JobStatus.done, JobStatus.failed):
        await job_manager.fail_job(
            job_id,
            "JOB_TIMEOUT",
            "El job superó el tiempo máximo de ejecución (30 minutos).",
            retry_after=600,
        )


# ---------------------------------------------------------------------------
# Guard 2 — LLM Watchdog (analyzing phase only)
# ---------------------------------------------------------------------------

async def llm_watchdog(job_id: str, activity_event: asyncio.Event) -> None:
    """
    Runs ONLY during the "analyzing" phase (Reviewer active).
    Fails the job with LLM_TIMEOUT if activity_event.set() is not called
    within LLM_WATCHDOG_SECONDS seconds.

    NOTE: This function is NOT launched by run_pipeline.
          It is exported here for F07 (Reviewer) to import and launch.
    """
    while True:
        activity_event.clear()
        try:
            await asyncio.wait_for(
                activity_event.wait(),
                timeout=float(settings.LLM_WATCHDOG_SECONDS),
            )
            # Activity received — check if job is already terminal
            state = await job_manager.get_state(job_id)
            if state is None or state.status in (JobStatus.done, JobStatus.failed):
                return
            # Job still running — loop again
        except asyncio.TimeoutError:
            await job_manager.fail_job(
                job_id,
                "LLM_TIMEOUT",
                "El modelo LLM no generó actividad en 5 minutos. Proceso terminado.",
                retry_after=300,
            )
            return


# ---------------------------------------------------------------------------
# Guard 3 — TTL Cleanup post-completion
# ---------------------------------------------------------------------------

async def schedule_cleanup(job_id: str) -> None:
    """
    Launched when a job reaches done or failed status.
    Sleeps RESULT_TTL_MINUTES * 60 seconds then removes the job directory.

    NOTE: This function is NOT launched by run_pipeline.
          It is exported here for F08 (Packager) to import and launch.
    """
    delay = settings.RESULT_TTL_MINUTES * 60
    await asyncio.sleep(float(delay))
    job_dir = Path(settings.JOB_BASE_DIR) / job_id
    if job_dir.exists():
        shutil.rmtree(job_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------

async def _check_still_running(job_id: str) -> bool:
    """Returns True if job is still active (not done/failed/missing)."""
    state = await job_manager.get_state(job_id)
    return state is not None and state.status not in (JobStatus.done, JobStatus.failed)


# ---------------------------------------------------------------------------
# Pipeline runner
# ---------------------------------------------------------------------------

async def run_pipeline(job_id: str) -> None:
    """
    Orchestrates the full pipeline: Explorer → Fetcher → Extractor → Auditor.

    Launches Guard 1 (global_job_timeout) at the start and registers it with
    job_manager so that DELETE /{job_id} can cancel it.

    Between each phase the pipeline checks whether the job was externally
    cancelled or failed and exits early if so.

    If the Auditor reports needs_refetch=True (and second_pass was not already
    done), the Fetcher → Extractor → Auditor sequence is repeated once with
    second_pass=True.

    Guard 2 (llm_watchdog) is launched by the Reviewer in F07.
    Guard 3 (schedule_cleanup) is launched by the Packager in F08.
    """
    # Initialize guard1 to None before any await so the finally block can
    # safely reference it even if cancellation fires before guard1 is created.
    guard1: asyncio.Task = asyncio.create_task(global_job_timeout(job_id))
    job_manager.register_task(job_id, guard1)

    try:
        # ------------------------------------------------------------------ #
        # Explorer
        # ------------------------------------------------------------------ #
        ok: bool = await run_explorer(job_id)
        if not ok:
            return  # job already failed with NO_ROUTES_FOUND

        if not await _check_still_running(job_id):
            return

        # ------------------------------------------------------------------ #
        # Fetcher
        # ------------------------------------------------------------------ #
        ok = await run_fetcher(job_id)
        if not ok:
            return  # job already failed with FETCH_ALL_FAILED

        if not await _check_still_running(job_id):
            return

        # ------------------------------------------------------------------ #
        # Extractor
        # ------------------------------------------------------------------ #
        ok = await run_extractor(job_id)
        if not ok:
            return  # job already failed with EXTRACTION_EMPTY

        if not await _check_still_running(job_id):
            return

        # ------------------------------------------------------------------ #
        # Auditor (first pass)
        # ------------------------------------------------------------------ #
        ok = await run_auditor(job_id)
        if not ok:
            return  # job already failed with AUDIT_CRITICAL_GAPS

        if not await _check_still_running(job_id):
            return

        # ------------------------------------------------------------------ #
        # Optional re-fetch loop (max 1 iteration)
        # ------------------------------------------------------------------ #
        audit_report_path = Path(settings.JOB_BASE_DIR) / job_id / "audit_report.json"
        if audit_report_path.exists():
            try:
                report: dict = json.loads(
                    audit_report_path.read_text(encoding="utf-8")
                )
            except Exception:
                report = {}

            if report.get("needs_refetch") and not report.get("second_pass"):
                # Re-fetch
                ok = await run_fetcher(job_id)
                if not ok:
                    return

                if not await _check_still_running(job_id):
                    return

                # Re-extract
                ok = await run_extractor(job_id)
                if not ok:
                    return

                if not await _check_still_running(job_id):
                    return

                # Auditor second pass
                ok = await run_auditor(job_id, second_pass=True)
                if not ok:
                    return

        # ------------------------------------------------------------------ #
        # Guard 2 — LLM watchdog (analyzing phase only)
        # ------------------------------------------------------------------ #
        activity_event: asyncio.Event = asyncio.Event()
        guard2: asyncio.Task = asyncio.create_task(llm_watchdog(job_id, activity_event))
        job_manager.register_task(job_id, guard2)

        try:
            ok = await run_reviewer(job_id, activity_event)
            if not ok:
                return
        finally:
            if not guard2.done():
                guard2.cancel()
                try:
                    await guard2
                except (asyncio.CancelledError, Exception):
                    pass

        # ------------------------------------------------------------------ #
        # Packager — F08
        # ------------------------------------------------------------------ #
        if not await _check_still_running(job_id):
            return

        ok = await run_packager(job_id)
        if not ok:
            return

        # Guard 3 — TTL cleanup (15 min after job reaches done)
        # Intentionally NOT cancelled in finally — it must survive to clean up.
        guard3: asyncio.Task = asyncio.create_task(schedule_cleanup(job_id))
        job_manager.register_task(job_id, guard3)

    except Exception as exc:
        logger.exception("Pipeline error for job %s", job_id)
        state = await job_manager.get_state(job_id)
        if state is not None and state.status not in (JobStatus.done, JobStatus.failed):
            await job_manager.fail_job(
                job_id,
                "INTERNAL_ERROR",
                f"Error inesperado en el pipeline: {type(exc).__name__}",
                retry_after=60,
            )
    finally:
        # Cancel Guard 1 if the job finished (or failed) before the timeout.
        # Await the cancellation so the task is fully done before run_pipeline returns.
        if not guard1.done():
            guard1.cancel()
            try:
                await guard1
            except (asyncio.CancelledError, Exception):
                pass
