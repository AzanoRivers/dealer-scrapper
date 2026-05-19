import asyncio
import json
import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import aiofiles

from app.config import settings
from app.models.job import JobError, JobProgress, JobState, JobStatus, TERMINAL_STATUSES


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _to_iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


class JobManager:
    """
    Manages job lifecycle on disk.
    All state is persisted in /tmp/dealerscrapper/<job_id>/state.json.
    Writes are atomic: write to state.tmp.json then os.rename().
    """

    def __init__(self) -> None:
        self._base_dir: Path = Path(settings.JOB_BASE_DIR)
        # Map job_id -> list of asyncio Tasks (Guards 1, 2, 3 added by pipeline)
        self._job_tasks: dict[str, list[asyncio.Task]] = {}  # type: ignore[type-arg]
        # Per-job locks to serialize read-modify-write on state.json
        self._write_locks: dict[str, asyncio.Lock] = {}

    def _get_lock(self, job_id: str) -> asyncio.Lock:
        if job_id not in self._write_locks:
            self._write_locks[job_id] = asyncio.Lock()
        return self._write_locks[job_id]

    def _job_dir(self, job_id: str) -> Path:
        return self._base_dir / job_id

    def _state_file(self, job_id: str) -> Path:
        return self._job_dir(job_id) / "state.json"

    def _state_tmp_file(self, job_id: str) -> Path:
        return self._job_dir(job_id) / "state.tmp.json"

    async def _write_state(self, state: JobState) -> None:
        """Atomic write: write to .tmp then replace.

        state.json is small (~1 KB) so sync I/O is used to avoid Windows
        aiofiles thread-pool timing issues where the OS file handle may not be
        fully flushed before os.replace() runs.
        """
        job_dir = self._job_dir(state.job_id)
        job_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = self._state_tmp_file(state.job_id)
        final_path = self._state_file(state.job_id)
        data = json.dumps(state.model_dump(mode="json"), ensure_ascii=False, indent=2)
        tmp_path.write_text(data, encoding="utf-8")
        os.replace(tmp_path, final_path)  # atomic on both Linux and Windows

    async def create_job(self, url: str, options: dict) -> str:
        """Creates a new job directory and state.json. Returns job_id."""
        job_id = str(uuid.uuid4())
        now = _now_iso()
        state = JobState(
            job_id=job_id,
            status=JobStatus.queued,
            url=url,
            options=options,
            progress=JobProgress(phase="queued", pages_done=0, pages_total=0, percent=0),
            created_at=now,
            updated_at=now,
        )
        await self._write_state(state)
        return job_id

    async def get_state(self, job_id: str) -> Optional[JobState]:
        """Reads state.json from disk. Returns None if not found or corrupt."""
        state_file = self._state_file(job_id)
        if not state_file.exists():
            return None
        try:
            async with aiofiles.open(state_file, "r", encoding="utf-8") as f:
                content = await f.read()
            data = json.loads(content)
            return JobState(**data)
        except Exception:
            return None

    async def update_status(
        self,
        job_id: str,
        status: JobStatus,
        progress: Optional[JobProgress] = None,
    ) -> None:
        """Updates status (and optionally progress) in state.json."""
        async with self._get_lock(job_id):
            state = await self.get_state(job_id)
            if state is None:
                return
            state.status = status
            state.updated_at = _now_iso()
            if progress is not None:
                state.progress = progress
            await self._write_state(state)

    async def update_progress(
        self,
        job_id: str,
        pages_done: int,
        pages_total: int,
    ) -> None:
        """Updates pages_done / pages_total progress counters in state.json."""
        async with self._get_lock(job_id):
            state = await self.get_state(job_id)
            if state is None:
                return
            percent = int(pages_done / pages_total * 100) if pages_total > 0 else 0
            phase = state.status.value
            if state.progress is not None:
                phase = state.progress.phase
            state.progress = JobProgress(
                phase=phase,
                pages_done=pages_done,
                pages_total=pages_total,
                percent=percent,
            )
            state.updated_at = _now_iso()
            await self._write_state(state)

    async def complete_job(self, job_id: str) -> None:
        """Marks job as done and writes done_at. Guard 3 (TTL) is launched by Packager in F08."""
        async with self._get_lock(job_id):
            state = await self.get_state(job_id)
            if state is None:
                return
            now = _now_iso()
            state.status = JobStatus.done
            state.done_at = now
            state.updated_at = now
            if state.progress is not None:
                state.progress.phase = "done"
                state.progress.percent = 100
            await self._write_state(state)

    async def fail_job(
        self,
        job_id: str,
        error_code: str,
        message: str,
        retry_after: Optional[int] = None,
    ) -> None:
        """Marks job as failed with an error code."""
        async with self._get_lock(job_id):
            state = await self.get_state(job_id)
            if state is None:
                return
            now = _now_iso()
            state.status = JobStatus.failed
            state.updated_at = now
            state.error = JobError(
                code=error_code,
                message=message,
                failed_at=now,
                retry_after=retry_after,
            )
            await self._write_state(state)

    async def delete_job(self, job_id: str) -> bool:
        """Deletes job directory and cancels any registered asyncio Tasks."""
        # Cancel registered tasks first
        self._cancel_job_tasks(job_id)
        self._write_locks.pop(job_id, None)
        job_dir = self._job_dir(job_id)
        if job_dir.exists():
            shutil.rmtree(job_dir, ignore_errors=True)
            return True
        return False

    def get_ttl_remaining(self, state: JobState) -> Optional[int]:
        """
        Calculates remaining TTL seconds from done_at / error.failed_at.
        Returns None when status is not terminal (done/failed).
        Returns 0 if TTL already expired.
        """
        if state.status not in TERMINAL_STATUSES:
            return None

        reference_str: Optional[str] = None
        if state.status == JobStatus.done:
            reference_str = state.done_at
        elif state.error is not None:
            reference_str = state.error.failed_at

        if reference_str is None:
            reference_str = state.updated_at

        try:
            reference_dt = datetime.fromisoformat(reference_str.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            elapsed = (now - reference_dt).total_seconds()
            ttl_total = settings.RESULT_TTL_MINUTES * 60
            remaining = int(ttl_total - elapsed)
            return max(0, remaining)
        except Exception:
            return 0

    def register_task(self, job_id: str, task: asyncio.Task) -> None:  # type: ignore[type-arg]
        """Registers an asyncio Task for a job (used by Guards)."""
        if job_id not in self._job_tasks:
            self._job_tasks[job_id] = []
        self._job_tasks[job_id].append(task)

    def _cancel_job_tasks(self, job_id: str) -> None:
        """Cancels all registered asyncio Tasks for a job."""
        tasks = self._job_tasks.pop(job_id, [])
        for task in tasks:
            if not task.done():
                task.cancel()

    @property
    def active_jobs_count(self) -> int:
        """Counts job directories in JOB_BASE_DIR with non-terminal status (best effort, sync)."""
        base = self._base_dir
        if not base.exists():
            return 0
        count = 0
        for job_dir in base.iterdir():
            if not job_dir.is_dir():
                continue
            state_file = job_dir / "state.json"
            if not state_file.exists():
                continue
            try:
                data = json.loads(state_file.read_text(encoding="utf-8"))
                status = data.get("status", "")
                if status not in ("done", "failed", "expired"):
                    count += 1
            except Exception:
                pass
        return count


# Singleton instance used across the application
job_manager = JobManager()
