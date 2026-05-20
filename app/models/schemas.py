from typing import Any, Dict, Optional
from pydantic import BaseModel, HttpUrl, field_validator


class ScrapeOptions(BaseModel):
    max_pages: Optional[int] = None
    download_images: Optional[bool] = None
    llm_provider: Optional[str] = None
    llm_model: Optional[str] = None


class ScrapeRequest(BaseModel):
    url: HttpUrl
    # JSON de muestra que define la estructura exacta que el cliente espera recibir.
    # El LLM usará esta plantilla como guía y el código validará que el resultado
    # la respete estrictamente (claves presentes, tipos compatibles).
    # Ejemplo: {"concesionario": "...", "marcas": ["..."], "telefono": null}
    # Obligatorio — se retorna 422 si no se envía o si es un objeto vacío {}.
    response_schema: Dict[str, Any]
    options: ScrapeOptions = ScrapeOptions()

    @field_validator("response_schema")
    @classmethod
    def validate_response_schema(cls, v: Dict[str, Any]) -> Dict[str, Any]:
        """El schema debe ser un objeto JSON (dict) no vacío."""
        if len(v) == 0:
            raise ValueError(
                "response_schema no puede ser un objeto vacío. "
                "Provee al menos una clave que defina la estructura esperada."
            )
        return v


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
    author: str
    status: str
    uptime: str
    uptime_seconds: int


class DeleteResponse(BaseModel):
    job_id: str
    deleted: bool
    message: str
