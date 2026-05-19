from enum import Enum
from typing import Optional
from pydantic import BaseModel


class JobStatus(str, Enum):
    queued = "queued"
    exploring = "exploring"
    fetching = "fetching"
    extracting = "extracting"
    auditing = "auditing"
    analyzing = "analyzing"
    packaging = "packaging"
    done = "done"
    failed = "failed"
    expired = "expired"


TERMINAL_STATUSES = {JobStatus.done, JobStatus.failed}
IN_PROGRESS_STATUSES = {
    JobStatus.exploring,
    JobStatus.fetching,
    JobStatus.extracting,
    JobStatus.auditing,
    JobStatus.analyzing,
    JobStatus.packaging,
}


class JobProgress(BaseModel):
    phase: str
    pages_done: int = 0
    pages_total: int = 0
    percent: int = 0


class JobError(BaseModel):
    code: str
    message: str
    failed_at: str
    retry_after: Optional[int] = None


class JobState(BaseModel):
    job_id: str
    status: JobStatus
    url: str
    options: dict = {}
    progress: Optional[JobProgress] = None
    error: Optional[JobError] = None
    created_at: str
    started_at: Optional[str] = None
    updated_at: str
    done_at: Optional[str] = None
    ttl_remaining_seconds: Optional[int] = None
    estimated_remaining_seconds: int = 0
