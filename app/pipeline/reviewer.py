"""
Reviewer pipeline subagent — LLM Analysis.

Reads audit_report.json and pages/*.json, calls an LLM to analyze the
extracted content in batches, and writes result.json.

Guard 2 (llm_watchdog) is managed by the caller (run_pipeline in guards.py).
This module only calls activity_event.set() at the prescribed checkpoints.

Returns True on success, False if the job was failed due to an LLM error.
"""

import asyncio
import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import httpx

from app.config import settings
from app.core.job_manager import job_manager
from app.models.job import JobStatus

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------


class LLMAuthError(Exception):
    """Raised on 401/403 — fail job with LLM_AUTH_ERROR."""


class LLMParseError(Exception):
    """Raised when response JSON is malformed after 1 retry."""


# ---------------------------------------------------------------------------
# Provider configuration
# ---------------------------------------------------------------------------

_PROVIDER_URLS: dict[str, str] = {
    "openai": "https://api.openai.com/v1/chat/completions",
    "nvidia": "https://integrate.api.nvidia.com/v1/chat/completions",
    "deepseek": "https://api.deepseek.com/v1/chat/completions",
    "anthropic": "https://api.anthropic.com/v1/messages",
    "minimax": "https://api.minimax.chat/v1/text/chatcompletion_v2",
}


# ---------------------------------------------------------------------------
# LLM Client
# ---------------------------------------------------------------------------


class LLMClient:
    """
    Async wrapper over httpx for 5 LLM providers.
    Supports: openai, nvidia, deepseek, anthropic, minimax.
    """

    def __init__(self, provider: str, model: str, api_key: str) -> None:
        self.provider = provider
        self.model = model
        self.api_key = api_key
        self._url: str = _PROVIDER_URLS.get(provider, _PROVIDER_URLS["openai"])

    def _build_headers(self) -> dict[str, str]:
        if self.provider == "anthropic":
            return {
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            }
        return {
            "Authorization": f"Bearer {self.api_key}",
            "content-type": "application/json",
        }

    def _build_payload(
        self,
        messages: list[dict[str, Any]],
        max_tokens: int,
        temperature: float,
    ) -> dict[str, Any]:
        if self.provider == "anthropic":
            # Separate system message from user messages
            system_content: str = ""
            user_messages: list[dict[str, Any]] = []
            for msg in messages:
                if msg.get("role") == "system":
                    system_content = msg.get("content", "")
                else:
                    user_messages.append(msg)
            payload: dict[str, Any] = {
                "model": self.model,
                "messages": user_messages,
                "max_tokens": max_tokens,
            }
            if system_content:
                payload["system"] = system_content
            return payload

        # openai / deepseek / minimax — OpenAI-compatible
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        # openai, nvidia, deepseek support response_format; minimax does not
        if self.provider in ("openai", "nvidia", "deepseek"):
            payload["response_format"] = {"type": "json_object"}
        # nvidia/kimi has reasoning on by default — disable to avoid <think> tags breaking JSON parse
        if self.provider == "nvidia":
            payload["chat_template_kwargs"] = {"thinking": False}
        return payload

    def _extract_content(self, response_json: dict[str, Any]) -> str:
        """Extract the text content from a provider response dict."""
        if self.provider == "anthropic":
            content_blocks = response_json.get("content", [])
            if content_blocks:
                return str(content_blocks[0].get("text", ""))
            return ""
        # openai / deepseek / minimax
        choices = response_json.get("choices", [])
        if choices:
            message = choices[0].get("message", {})
            return str(message.get("content", ""))
        return ""

    async def _do_request(
        self,
        messages: list[dict[str, Any]],
        max_tokens: int,
        temperature: float,
    ) -> str:
        """
        Makes a single HTTP request to the LLM provider.

        Returns the extracted text content.
        Raises:
            LLMAuthError  — on 401/403
            httpx.TimeoutException — propagated to caller (returns "")
            httpx.HTTPStatusError — for non-auth, non-429 HTTP errors
        """
        payload = self._build_payload(messages, max_tokens, temperature)
        headers = self._build_headers()

        # nvidia can be slow to start — give it more read time before declaring timeout
        if self.provider == "nvidia":
            timeout = httpx.Timeout(connect=15.0, read=120.0, write=15.0, pool=15.0)
        else:
            timeout = httpx.Timeout(60.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(self._url, json=payload, headers=headers)

        if response.status_code in (401, 403):
            raise LLMAuthError(
                f"LLM authentication failed: HTTP {response.status_code}"
            )

        response.raise_for_status()

        try:
            response_json: dict[str, Any] = response.json()
        except Exception as exc:
            raise LLMParseError(f"Cannot parse LLM JSON response: {exc}") from exc

        return self._extract_content(response_json)

    async def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int,
        temperature: float,
    ) -> str:
        """
        Sends a chat completion request. Returns the response text.

        Error handling:
        - httpx.TimeoutException  → return "" (Watchdog detects inactivity)
        - 401 / 403               → raise LLMAuthError
        - 429                     → asyncio.sleep(min(retry_after, 60)), retry once
        - 5xx / JSONDecodeError   → retry once; if still fails → raise LLMParseError
        """
        try:
            return await self._do_request(messages, max_tokens, temperature)

        except httpx.TimeoutException:
            logger.warning("LLM request timed out (provider=%s)", self.provider)
            if self.provider == "nvidia":
                logger.warning("Nvidia NIM timeout — retrying once in 10s")
                await asyncio.sleep(10)
                try:
                    return await self._do_request(messages, max_tokens, temperature)
                except httpx.TimeoutException as exc:
                    raise LLMParseError(
                        "Nvidia NIM no responde — modelo ocupado o sin capacidad disponible"
                    ) from exc
            return ""

        except LLMAuthError:
            raise

        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code

            if status == 429:
                # Parse Retry-After header
                retry_after_raw: str = exc.response.headers.get("Retry-After", "60")
                try:
                    retry_after: int = int(retry_after_raw)
                except ValueError:
                    retry_after = 60
                wait_seconds: float = min(float(retry_after), 60.0)
                logger.warning(
                    "LLM 429 — waiting %.1f seconds before retry", wait_seconds
                )
                await asyncio.sleep(wait_seconds)
                # One retry
                try:
                    return await self._do_request(messages, max_tokens, temperature)
                except (httpx.HTTPStatusError, httpx.TimeoutException, LLMParseError) as retry_exc:
                    raise LLMParseError(
                        f"LLM retry after 429 failed: {retry_exc}"
                    ) from retry_exc

            if status >= 500:
                logger.warning("LLM 5xx (%d) — retrying once", status)
                try:
                    return await self._do_request(messages, max_tokens, temperature)
                except (httpx.HTTPStatusError, httpx.TimeoutException, LLMParseError) as retry_exc:
                    raise LLMParseError(
                        f"LLM retry after 5xx failed: {retry_exc}"
                    ) from retry_exc

            # Other HTTP error — treat as parse error
            raise LLMParseError(
                f"Unexpected HTTP {status} from LLM provider"
            ) from exc

        except LLMParseError:
            # JSON malformed — retry once
            logger.warning("LLM JSON parse error — retrying once")
            try:
                return await self._do_request(messages, max_tokens, temperature)
            except Exception as retry_exc:
                raise LLMParseError(
                    f"LLM JSON parse retry failed: {retry_exc}"
                ) from retry_exc


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


def _validate_schema_structure(
    result: Any, template: Any, path: str = "root"
) -> list[str]:
    """
    Validates that `result` conforms to the structure defined by `template`.

    Rules:
    - If template is a dict: result must be a dict containing ALL the same keys.
      Nested dicts are validated recursively.
    - If template is a list: result must be a list.
      If the template list is non-empty, the first item acts as the item template
      and every element in result is validated against it.
    - For primitives (str, int, float, bool, None): no type enforcement —
      the LLM is allowed to return null for any field.

    Returns a list of human-readable error strings. Empty list = valid.
    """
    errors: list[str] = []

    if isinstance(template, dict):
        if not isinstance(result, dict):
            errors.append(
                f"{path}: se esperaba un objeto (dict), se obtuvo {type(result).__name__}"
            )
            return errors
        for key, tval in template.items():
            if key not in result:
                errors.append(f"{path}.{key}: clave requerida no encontrada en el resultado")
            else:
                errors.extend(
                    _validate_schema_structure(result[key], tval, f"{path}.{key}")
                )

    elif isinstance(template, list):
        if not isinstance(result, list):
            errors.append(
                f"{path}: se esperaba un array (list), se obtuvo {type(result).__name__}"
            )
        elif len(template) > 0 and len(result) > 0:
            # Use first template item to validate all result items
            item_template = template[0]
            for i, item in enumerate(result):
                errors.extend(
                    _validate_schema_structure(item, item_template, f"{path}[{i}]")
                )

    # Primitives: allow any value (including null) — no validation needed

    return errors


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

BATCH_SYSTEM_PROMPT: str = (
    "Eres un asistente especializado en análisis de sitios web de concesionarios de automóviles y negocios. "
    "Analiza las páginas proporcionadas y extrae información estructurada en JSON. "
    "Responde ÚNICAMENTE con JSON válido, sin texto adicional."
)


def _build_pages_text(pages_data: list[dict[str, Any]]) -> str:
    return "\n\n---\n\n".join(
        [
            f"URL: {p.get('url', '')}\nTítulo: {p.get('title', '')}\nContenido:\n{str(p.get('text_content', ''))[:2000]}"
            for p in pages_data
        ]
    )


def build_schema_batch_prompt(
    pages_data: list[dict[str, Any]],
    response_schema: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    Builds the messages list for a batch of pages when the client provided a
    custom response_schema. The LLM is instructed to fill EXACTLY that structure.
    """
    pages_text: str = _build_pages_text(pages_data)
    schema_str: str = json.dumps(response_schema, ensure_ascii=False, indent=2)
    return [
        {"role": "system", "content": BATCH_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "Analiza estas páginas web y extrae la información necesaria para completar "
                "EXACTAMENTE la siguiente estructura JSON.\n\n"
                "REGLAS ESTRICTAS:\n"
                "- Devuelve ÚNICAMENTE el JSON, sin texto adicional ni explicaciones.\n"
                "- Mantén EXACTAMENTE las mismas claves y la misma estructura anidada.\n"
                "- Usa null para campos sin información disponible en las páginas.\n"
                "- Si un campo es un array, devuelve un array (puede estar vacío []).\n"
                "- Si un campo es un objeto, devuelve un objeto con las mismas claves.\n\n"
                f"Estructura requerida:\n{schema_str}\n\n"
                f"Páginas a analizar:\n{pages_text}"
            ),
        },
    ]


def build_schema_merge_prompt(
    chunk_summaries: list[dict[str, Any]],
    base_url: str,
    response_schema: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    Builds the merge/consolidation prompt when the client provided a custom
    response_schema. The LLM must consolidate into EXACTLY that structure.
    """
    summaries_text: str = json.dumps(chunk_summaries, ensure_ascii=False, indent=2)
    schema_str: str = json.dumps(response_schema, ensure_ascii=False, indent=2)
    return [
        {"role": "system", "content": BATCH_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "Consolida estos análisis parciales en un único resultado que cumpla "
                "EXACTAMENTE la siguiente estructura JSON.\n\n"
                "REGLAS ESTRICTAS:\n"
                "- Devuelve ÚNICAMENTE el JSON, sin texto adicional.\n"
                "- Mantén EXACTAMENTE las mismas claves y la misma estructura anidada.\n"
                "- Usa null para campos sin información disponible.\n"
                "- Si un campo es un array, devuelve un array (puede estar vacío []).\n"
                "- Si un campo es un objeto, devuelve un objeto con las mismas claves.\n\n"
                f"Estructura requerida:\n{schema_str}\n\n"
                f"URL base del sitio: {base_url}\n\n"
                f"Análisis parciales:\n{summaries_text}"
            ),
        },
    ]


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------


def _batches_of(
    items: list[Any], size: int
) -> list[list[Any]]:
    """Split a list into batches of at most `size` items."""
    return [items[i : i + size] for i in range(0, len(items), size)]


def _load_page_data(
    job_dir: Path, url_hash: str
) -> Optional[dict[str, Any]]:
    """
    Loads a page's JSON file from pages/<url_hash>.json.
    Returns None if the file does not exist or cannot be parsed.
    """
    page_file = job_dir / "pages" / f"{url_hash}.json"
    if not page_file.exists():
        logger.debug("Page file not found: %s", page_file)
        return None
    try:
        return json.loads(page_file.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Cannot read page file %s: %s", page_file, exc)
        return None


def _parse_json_response(raw: str) -> dict[str, Any]:
    """
    Attempts to parse a JSON string (possibly with markdown code fences).
    Raises LLMParseError if it cannot be parsed.
    """
    text = raw.strip()
    # Strip markdown code fences if present
    if text.startswith("```"):
        lines = text.splitlines()
        # Remove first line (```json or ```) and last line (```)
        inner = "\n".join(lines[1:-1]) if len(lines) > 2 else ""
        text = inner.strip()
    try:
        result = json.loads(text)
        if not isinstance(result, dict):
            raise LLMParseError("LLM response is not a JSON object")
        return result
    except json.JSONDecodeError as exc:
        raise LLMParseError(f"JSON decode error: {exc}") from exc


def _build_result_json(
    job_id: str,
    url: str,
    merged: dict[str, Any],
    valid_page_count: int,
    audit_report: dict[str, Any],
    provider: str,
    model: str,
) -> dict[str, Any]:
    """Assembles the final result.json from LLM merged data + job metadata.

    The LLM output is stored verbatim under the "data" key.
    "schema_validated" is always True — every job now requires a response_schema.
    """
    # Coverage metadata — handle both auditor formats
    coverage_data = audit_report.get("coverage", {})
    total_routes: int = coverage_data.get("total_routes") or audit_report.get("total_routes", valid_page_count)
    pages_fetched: int = coverage_data.get("extracted_pages") or audit_report.get("pages_fetched", valid_page_count)
    coverage_percent: float = coverage_data.get("coverage_percent") or audit_report.get("coverage_percent", 100.0)

    return {
        "job_id": job_id,
        "url": url,
        "scraped_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "llm_provider": provider,
        "llm_model": model,
        "schema_validated": True,
        "metadata": {
            "total_pages_discovered": total_routes,
            "pages_fetched": pages_fetched,
            "pages_analyzed": valid_page_count,
            "coverage_percent": coverage_percent,
        },
        "data": merged,
    }


# ---------------------------------------------------------------------------
# Valid pages resolution
# ---------------------------------------------------------------------------


def _resolve_valid_page_hashes(
    audit_report: dict[str, Any],
    pages_dir: Path,
) -> list[str]:
    """
    Returns a list of url_hash strings for pages that are valid.

    Handles two audit_report formats:
    1. Real auditor format: `valid_pages` is a list of url_hash strings.
    2. Test/spec format: `pages` is a list of dicts with `url_hash` and optional `valid` bool.

    If neither key exists, falls back to scanning all *.json files in pages_dir.
    """
    # Format 1: real auditor output (list of hash strings)
    if "valid_pages" in audit_report and isinstance(audit_report["valid_pages"], list):
        hashes = audit_report["valid_pages"]
        # Verify they are strings (hash format)
        if all(isinstance(h, str) for h in hashes):
            return hashes

    # Format 2: test fixture format (list of page dicts)
    if "pages" in audit_report and isinstance(audit_report["pages"], list):
        return [
            p["url_hash"]
            for p in audit_report["pages"]
            if isinstance(p, dict)
            and "url_hash" in p
            and p.get("valid", True)
        ]

    # Fallback: use all page files present on disk
    if pages_dir.exists():
        return [f.stem for f in sorted(pages_dir.glob("*.json"))]

    return []


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


async def run_reviewer(job_id: str, activity_event: asyncio.Event) -> bool:
    """
    Analyzes extracted pages with an LLM and writes result.json.
    Expects audit_report.json and pages/*.json to be present.

    If the job's options contain a "response_schema" key, the LLM is instructed
    to fill that exact structure and the result is strictly validated against it
    before writing to disk.  A mismatch fails the job with RESULT_SCHEMA_MISMATCH.

    Calls activity_event.set() at minimum 5 points per batch (plus merge points).
    Guard 2 (llm_watchdog) is managed by the caller (run_pipeline in guards.py).

    Returns True on success, False if job was failed (LLM error or cancelled).
    """
    # 0. Check if job is already failed before doing anything
    pre_check = await job_manager.get_state(job_id)
    if pre_check is None or pre_check.status == JobStatus.failed:
        return False

    # 1. Update status to "analyzing"
    await job_manager.update_status(job_id, JobStatus.analyzing)

    job_dir: Path = Path(settings.JOB_BASE_DIR) / job_id
    audit_report_path: Path = job_dir / "audit_report.json"

    # 2. Read audit_report.json
    if not audit_report_path.exists():
        logger.error("Reviewer: audit_report.json not found for job %s", job_id)
        await job_manager.fail_job(
            job_id,
            "LLM_PARSE_ERROR",
            "audit_report.json not found — cannot run LLM analysis.",
        )
        return False

    try:
        audit_report: dict[str, Any] = json.loads(
            audit_report_path.read_text(encoding="utf-8")
        )
    except Exception as exc:
        logger.error(
            "Reviewer: cannot parse audit_report.json for job %s: %s", job_id, exc
        )
        await job_manager.fail_job(
            job_id,
            "LLM_PARSE_ERROR",
            f"audit_report.json is corrupt: {exc}",
        )
        return False

    pages_dir: Path = job_dir / "pages"
    valid_hashes: list[str] = _resolve_valid_page_hashes(audit_report, pages_dir)

    if not valid_hashes:
        logger.warning(
            "Reviewer: no valid pages found for job %s — writing empty result", job_id
        )

    # 3. Setup LLM client (options override settings)
    state = await job_manager.get_state(job_id)
    if state is None or state.status == JobStatus.failed:
        return False

    provider: str = (
        (state.options.get("llm_provider") or "") or settings.LLM_PROVIDER
    )
    model: str = (
        (state.options.get("llm_model") or "") or settings.LLM_MODEL
    )
    # response_schema is mandatory — every job must provide it.
    response_schema: Optional[dict[str, Any]] = state.options.get("response_schema")
    if response_schema is None:
        logger.error(
            "Reviewer: response_schema missing from job options for job %s", job_id
        )
        await job_manager.fail_job(
            job_id,
            "INTERNAL_ERROR",
            "response_schema no encontrado en las opciones del job.",
        )
        return False

    client = LLMClient(provider, model, settings.LLM_API_KEY)

    # 4. Create chunk_summaries/ directory
    chunk_dir: Path = job_dir / "chunk_summaries"
    chunk_dir.mkdir(exist_ok=True)

    # 5. Process batches of 5 pages
    chunk_summaries: list[dict[str, Any]] = []
    batches = _batches_of(valid_hashes, 5)

    for i, batch_hashes in enumerate(batches):
        # Check if job was cancelled/failed before this batch
        state = await job_manager.get_state(job_id)
        if state is None or state.status == JobStatus.failed:
            return False

        activity_event.set()  # Point 1: batch start

        # Load page data for this batch
        pages_data: list[dict[str, Any]] = []
        for url_hash in batch_hashes:
            page = _load_page_data(job_dir, url_hash)
            if page is not None:
                pages_data.append(page)

        if not pages_data:
            logger.debug("Reviewer: batch %d has no loadable pages, skipping", i)
            activity_event.set()
            activity_event.set()
            continue

        # Build schema-aware prompt
        batch_messages = build_schema_batch_prompt(pages_data, response_schema)

        try:
            raw_response: str = await client.complete(
                batch_messages,
                max_tokens=settings.LLM_MAX_TOKENS,
                temperature=settings.LLM_TEMPERATURE,
            )
        except LLMAuthError:
            await job_manager.fail_job(
                job_id,
                "LLM_AUTH_ERROR",
                "API key inválida o sin créditos.",
            )
            return False
        except LLMParseError:
            await job_manager.fail_job(
                job_id,
                "LLM_PARSE_ERROR",
                "Respuesta JSON inválida tras 2 intentos.",
                retry_after=60,
            )
            return False

        if not raw_response:
            # Timeout detected — watchdog will handle failing the job
            return False

        activity_event.set()  # Point 2: response received

        try:
            chunk = _parse_json_response(raw_response)
        except LLMParseError:
            await job_manager.fail_job(
                job_id,
                "LLM_PARSE_ERROR",
                "Respuesta JSON inválida tras 2 intentos.",
                retry_after=60,
            )
            return False

        chunk_path = chunk_dir / f"chunk_summary_{i}.json"
        chunk_path.write_text(
            json.dumps(chunk, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        chunk_summaries.append(chunk)

        activity_event.set()  # Point 3: chunk saved

    # 6. Merge call — consolidate all chunks
    state = await job_manager.get_state(job_id)
    if state is None or state.status == JobStatus.failed:
        return False

    activity_event.set()  # Point 4: merge start

    job_url: str = state.url

    # If no chunks (no valid pages), create a minimal merged result
    if not chunk_summaries:
        merged: dict[str, Any] = {k: None for k in response_schema}
    else:
        # Build schema-aware merge prompt
        merge_messages = build_schema_merge_prompt(
            chunk_summaries, job_url, response_schema
        )

        try:
            merged_raw: str = await client.complete(
                merge_messages,
                max_tokens=settings.LLM_MAX_TOKENS,
                temperature=settings.LLM_TEMPERATURE,
            )
        except LLMAuthError:
            await job_manager.fail_job(
                job_id,
                "LLM_AUTH_ERROR",
                "API key inválida o sin créditos.",
            )
            return False
        except LLMParseError:
            await job_manager.fail_job(
                job_id,
                "LLM_PARSE_ERROR",
                "Respuesta JSON inválida tras 2 intentos.",
                retry_after=60,
            )
            return False

        if not merged_raw:
            # Timeout — watchdog handles it
            return False

        try:
            merged = _parse_json_response(merged_raw)
        except LLMParseError:
            await job_manager.fail_job(
                job_id,
                "LLM_PARSE_ERROR",
                "Respuesta JSON inválida tras 2 intentos.",
                retry_after=60,
            )
            return False

    # 6b. Strict schema validation
    schema_errors = _validate_schema_structure(merged, response_schema)
    if schema_errors:
        errors_summary = "; ".join(schema_errors[:5])  # cap at 5 for message length
        logger.error(
            "Reviewer: schema validation failed for job %s — %s",
            job_id,
            errors_summary,
        )
        await job_manager.fail_job(
            job_id,
            "RESULT_SCHEMA_MISMATCH",
            f"El resultado del LLM no respeta la estructura solicitada. "
            f"Errores: {errors_summary}",
        )
        return False

    # 7. Build and write result.json
    result: dict[str, Any] = _build_result_json(
        job_id=job_id,
        url=job_url,
        merged=merged,
        valid_page_count=len(valid_hashes),
        audit_report=audit_report,
        provider=provider,
        model=model,
    )

    result_path: Path = job_dir / "result.json"
    result_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    activity_event.set()  # Point 5: result written

    # 8. Clean up chunk_summaries/
    shutil.rmtree(chunk_dir, ignore_errors=True)

    logger.info(
        "Reviewer: job=%s provider=%s model=%s pages_analyzed=%d",
        job_id,
        provider,
        model,
        len(valid_hashes),
    )
    return True
