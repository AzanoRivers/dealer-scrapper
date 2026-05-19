from typing import Optional, Any
from pydantic import BaseModel, HttpUrl


class ScrapeOptions(BaseModel):
    max_pages: Optional[int] = None
    download_images: Optional[bool] = None
    llm_provider: Optional[str] = None
    llm_model: Optional[str] = None


class ScrapeRequest(BaseModel):
    url: HttpUrl
    options: ScrapeOptions = ScrapeOptions()


class ScrapeResponse(BaseModel):
    job_id: str
    status: str


class JobProgressSchema(BaseModel):
    phase: str
    pages_done: int = 0
    pages_total: int = 0
    percent: int = 0


class JobErrorSchema(BaseModel):
    code: str
    message: str
    failed_at: str
    retry_after: Optional[int] = None


class StatusResponse(BaseModel):
    job_id: str
    status: str
    progress: Optional[JobProgressSchema] = None
    ttl_remaining_seconds: Optional[int] = None
    error: Optional[JobErrorSchema] = None
    created_at: str
    started_at: Optional[str] = None
    updated_at: str
    done_at: Optional[str] = None
    estimated_remaining_seconds: int = 0


class ErrorResponse(BaseModel):
    error: str
    detail: str
    job_id: Optional[str] = None


class ServerStatusResponse(BaseModel):
    name: str
    version: str
    active_jobs: int
    max_concurrent_jobs: int
    status: str


class RootResponse(BaseModel):
    name: str
    version: str
    status: str
    port: int
    docs: str


class DeleteResponse(BaseModel):
    job_id: str
    deleted: bool
    message: str
