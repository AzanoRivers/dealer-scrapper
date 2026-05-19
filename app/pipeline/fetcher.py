"""
Fetcher pipeline subagent — HTML Download.

Downloads HTML for all routes listed in routes.json and writes each file to
raw/<sha256(url)>.html immediately upon receipt (never accumulates in RAM).
Writes fetch_results.json after all URLs have been processed.
"""

import asyncio
import hashlib
import json
import logging
from pathlib import Path
from typing import Optional

import aiofiles
import httpx

from app.config import settings
from app.core.job_manager import job_manager
from app.models.job import JobStatus

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_HEADERS: dict[str, str] = {
    "User-Agent": "Mozilla/5.0 (compatible; DealerScrapper/1.0; +https://azanolabs.com)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

# Backoff delays between retry attempts (seconds)
BACKOFF_DELAYS: list[float] = [1.0, 2.0, 4.0]

# Module-level alias so tests can patch only the fetcher's sleep
# without affecting global asyncio.sleep used by guards/cleanup.
_sleep = asyncio.sleep

# HTTP codes that are terminal (no retry)
_NON_RETRIABLE_CODES: frozenset[int] = frozenset({404, 410})


# ---------------------------------------------------------------------------
# URL hash helper
# ---------------------------------------------------------------------------


def _url_hash(url: str) -> str:
    """Return sha256 hex digest of the URL string."""
    return hashlib.sha256(url.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Single-URL fetch with retry & backoff
# ---------------------------------------------------------------------------


async def _fetch_url(
    client: httpx.AsyncClient,
    url: str,
    semaphore: asyncio.Semaphore,
    retries: int,
    raw_dir: Path,
) -> dict:
    """
    Fetch a single URL, with up to `retries` retry attempts and exponential
    backoff.  HTML is written to disk immediately on success — never held in
    RAM beyond what is needed for a single write call.

    Returns a result dict compatible with the fetch_results.json schema.
    """
    url_hash = _url_hash(url)
    file_path = raw_dir / f"{url_hash}.html"

    async with semaphore:
        last_error: Optional[str] = None
        last_http_code: Optional[int] = None

        for attempt in range(retries + 1):
            try:
                resp = await client.get(url)
                last_http_code = resp.status_code

                if resp.status_code == 200:
                    # Write HTML to disk immediately — no RAM accumulation
                    async with aiofiles.open(file_path, "w", encoding="utf-8") as fh:
                        await fh.write(resp.text)
                    return {
                        "url": url,
                        "url_hash": url_hash,
                        "status": "success",
                        "http_code": 200,
                        "file": f"raw/{url_hash}.html",
                        "error": None,
                    }

                if resp.status_code in _NON_RETRIABLE_CODES:
                    # 404 / 410 — page does not exist, no retry
                    return {
                        "url": url,
                        "url_hash": url_hash,
                        "status": "failed",
                        "http_code": resp.status_code,
                        "file": None,
                        "error": "http_error",
                    }

                # 403, 429, 5xx — retriable
                last_error = "http_error"

            except httpx.TimeoutException:
                last_error = "timeout"
                last_http_code = None

            except httpx.RequestError:
                last_error = "connection_error"
                last_http_code = None

            except Exception as exc:  # pragma: no cover
                logger.warning("Fetcher: unexpected error fetching %s: %s", url, exc)
                last_error = "connection_error"
                last_http_code = None

            # Backoff before next attempt
            if attempt < retries:
                await _sleep(BACKOFF_DELAYS[attempt])

        # All attempts exhausted
        return {
            "url": url,
            "url_hash": url_hash,
            "status": "failed",
            "http_code": last_http_code if last_error == "http_error" else None,
            "file": None,
            "error": last_error,
        }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


async def run_fetcher(job_id: str) -> bool:
    """
    Downloads HTML for all routes in routes.json and writes to raw/<hash>.html.
    Returns True on success (>=1 page downloaded), False if job was failed
    (FETCH_ALL_FAILED).  Updates job status to 'fetching' at the start.
    """
    job_dir = Path(settings.JOB_BASE_DIR) / job_id
    routes_path = job_dir / "routes.json"

    # --- Read routes.json ---------------------------------------------------
    try:
        async with aiofiles.open(routes_path, "r", encoding="utf-8") as fh:
            content = await fh.read()
        routes_data: dict = json.loads(content)
        urls: list[str] = [
            str(route["url"]) for route in routes_data.get("routes", [])
        ]
    except Exception as exc:
        logger.error(
            "Fetcher: cannot read routes.json for job %s: %s", job_id, exc
        )
        await job_manager.fail_job(
            job_id,
            "FETCH_ALL_FAILED",
            "No se pudo leer routes.json. El Explorador puede no haber completado.",
        )
        return False

    # --- Mark job as fetching -----------------------------------------------
    await job_manager.update_status(job_id, JobStatus.fetching)

    # --- Prepare raw/ directory ----------------------------------------------
    raw_dir = job_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    total = len(urls)

    # Shared semaphore limits parallel HTTP connections
    semaphore = asyncio.Semaphore(settings.MAX_CONCURRENT_FETCHES)

    # Mutable counter — safe in asyncio (single-threaded cooperative)
    done_count: list[int] = [0]

    # Build httpx client
    timeout = httpx.Timeout(settings.FETCH_TIMEOUT_SECONDS)

    async with httpx.AsyncClient(
        headers=_HEADERS,
        timeout=timeout,
        follow_redirects=True,
        max_redirects=3,
    ) as client:

        async def fetch_and_update(url: str) -> dict:
            result = await _fetch_url(
                client, url, semaphore, settings.FETCH_RETRIES, raw_dir
            )
            done_count[0] += 1
            await job_manager.update_progress(
                job_id, pages_done=done_count[0], pages_total=total
            )
            return result

        results: list[dict] = list(
            await asyncio.gather(*[fetch_and_update(url) for url in urls])
        )

    # --- Compute summary -----------------------------------------------------
    successful = sum(1 for r in results if r["status"] == "success")
    failed_count = sum(1 for r in results if r["status"] == "failed")

    # --- Write fetch_results.json -------------------------------------------
    payload: dict = {
        "job_id": job_id,
        "total_urls": total,
        "successful": successful,
        "failed": failed_count,
        "results": results,
    }
    fetch_results_path = job_dir / "fetch_results.json"
    async with aiofiles.open(fetch_results_path, "w", encoding="utf-8") as fh:
        await fh.write(json.dumps(payload, ensure_ascii=False, indent=2))

    # --- Handle total failure ------------------------------------------------
    if successful == 0:
        await job_manager.fail_job(
            job_id,
            "FETCH_ALL_FAILED",
            "No se pudo descargar ninguna página. "
            "El sitio puede estar bloqueando scrapers.",
        )
        logger.warning("Fetcher: FETCH_ALL_FAILED for job %s", job_id)
        return False

    logger.info(
        "Fetcher: job=%s successful=%d failed=%d", job_id, successful, failed_count
    )
    return True
