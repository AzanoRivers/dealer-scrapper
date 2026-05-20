"""
Auditor pipeline subagent — Coverage Analysis.

Reads routes.json and pages/*.json, calculates coverage metrics,
detects extraction quality gaps, and optionally discovers new routes
to crawl. Writes audit_report.json.

Returns True if the job can proceed to the Reviewer,
False if the job was failed with AUDIT_CRITICAL_GAPS.
"""

import json
import logging
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiofiles

from app.config import settings
from app.core.job_manager import job_manager
from app.models.job import JobStatus

logger = logging.getLogger(__name__)

# Required fields that every PageData dict must contain
_REQUIRED_FIELDS: tuple[str, ...] = (
    "title",
    "text_content",
    "word_count",
    "url",
    "url_hash",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


async def run_auditor(job_id: str, second_pass: bool = False) -> bool:
    """
    Evaluates coverage and quality of extracted pages for a job.

    Reads routes.json and pages/*.json, computes metrics, writes
    audit_report.json.

    Returns True on success (even if coverage is critical on first pass).
    Returns False if the job was failed (AUDIT_CRITICAL_GAPS).
    """
    await job_manager.update_status(job_id, JobStatus.auditing)
    await job_manager.update_progress(job_id, 0, 1)

    job_dir = Path(settings.JOB_BASE_DIR) / job_id
    routes_path = job_dir / "routes.json"
    pages_dir = job_dir / "pages"

    # --- 1. Read routes.json ------------------------------------------------
    if not routes_path.exists():
        logger.error("Auditor: routes.json not found for job %s", job_id)
        await job_manager.fail_job(
            job_id,
            "AUDIT_CRITICAL_GAPS",
            "routes.json not found",
        )
        return False

    try:
        async with aiofiles.open(routes_path, "r", encoding="utf-8") as fh:
            content = await fh.read()
        routes_data: dict[str, Any] = json.loads(content)
    except Exception as exc:
        logger.error("Auditor: cannot read routes.json for job %s: %s", job_id, exc)
        await job_manager.fail_job(
            job_id,
            "AUDIT_CRITICAL_GAPS",
            f"routes.json not found or corrupt: {exc}",
        )
        return False

    route_entries: list[dict[str, Any]] = routes_data.get("routes", [])
    route_urls: set[str] = {str(r["url"]) for r in route_entries if "url" in r}
    total_routes: int = len(route_urls)

    # --- 2. Read pages/*.json -----------------------------------------------
    pages: list[dict[str, Any]] = []

    if pages_dir.exists():
        for page_file in sorted(pages_dir.glob("*.json")):
            try:
                async with aiofiles.open(page_file, "r", encoding="utf-8") as fh:
                    page_content = await fh.read()
                page_data: dict[str, Any] = json.loads(page_content)
                pages.append(page_data)
            except Exception as exc:
                logger.warning(
                    "Auditor: cannot read page file %s: %s", page_file, exc
                )

    extracted_pages: int = len(pages)

    # --- 3. Coverage metrics ------------------------------------------------
    if total_routes > 0:
        coverage_percent: float = round(extracted_pages / total_routes * 100, 1)
    else:
        coverage_percent = 0.0

    coverage_low: bool = coverage_percent < 70.0
    critical: bool = coverage_percent < settings.AUDIT_COVERAGE_MIN_PERCENT

    # --- 4. Key pages -------------------------------------------------------
    base_url: str = routes_data.get("base_url", "")
    has_homepage: bool = any(
        p.get("url") == base_url
        or p.get("url", "").rstrip("/") == base_url.rstrip("/")
        or (p.get("url") is not None and _url_path(str(p["url"])) == "/")
        for p in pages
    )
    has_form_page: bool = any(p.get("has_forms") is True for p in pages)

    # --- 5. Extraction quality ----------------------------------------------
    empty_pages_list: list[dict[str, Any]] = [
        p for p in pages if p.get("word_count", 0) < 50
    ]
    empty_pages_count: int = len(empty_pages_list)
    empty_ratio: float = (
        round(empty_pages_count / extracted_pages, 3) if extracted_pages > 0 else 1.0
    )
    extraction_quality: str = "poor" if empty_ratio > 0.5 else "good"

    # --- 6. Page integrity --------------------------------------------------
    valid_pages: list[str] = []
    invalid_pages: list[str] = []

    for page in pages:
        url_hash: str = str(page.get("url_hash", ""))
        # A field is "missing" if key is absent or its value is None.
        # Empty string ("") still counts as present.
        has_all_fields: bool = all(
            field in page and page[field] is not None for field in _REQUIRED_FIELDS
        )

        if not has_all_fields:
            if url_hash:
                invalid_pages.append(url_hash)
        elif page.get("word_count", 0) >= 50:
            if url_hash:
                valid_pages.append(url_hash)
        # Pages with all fields but word_count < 50 are neither invalid nor valid
        # (they are extracted but empty — they still go into valid_pages if fields are present)
        # Re-reading spec: "valid_pages = hashes with all required fields AND word_count >= 50"
        # So pages with all fields but wc < 50 are just not in valid_pages (and not invalid)

    # --- 7. New routes detection --------------------------------------------
    new_routes: list[str] = []

    if settings.AUDIT_REFETCH_ENABLED and not second_pass:
        # Collect all internal_links across all pages
        link_counter: Counter[str] = Counter()
        for page in pages:
            for link in page.get("internal_links", []):
                link_str = str(link)
                if link_str not in route_urls:
                    link_counter[link_str] += 1

        # Sort by frequency (most referenced first), cap at AUDIT_MAX_NEW_ROUTES
        new_routes = [
            url
            for url, _count in link_counter.most_common(settings.AUDIT_MAX_NEW_ROUTES)
        ]

    # --- 8. Build summary ---------------------------------------------------
    new_routes_count: int = len(new_routes)
    summary_parts: list[str] = [
        f"Coverage {coverage_percent}% ({extracted_pages}/{total_routes} pages).",
        f"Quality: {extraction_quality}.",
    ]
    if new_routes_count > 0:
        summary_parts.append(
            f"{new_routes_count} new route{'s' if new_routes_count != 1 else ''} discovered."
        )
    summary: str = " ".join(summary_parts)

    # --- 9. Write audit_report.json -----------------------------------------
    audit_report: dict[str, Any] = {
        "job_id": job_id,
        "second_pass": second_pass,
        "audited_at": _now_iso(),
        "coverage": {
            "total_routes": total_routes,
            "extracted_pages": extracted_pages,
            "coverage_percent": coverage_percent,
            "coverage_low": coverage_low,
            "critical": critical,
        },
        "key_pages": {
            "has_homepage": has_homepage,
            "has_form_page": has_form_page,
        },
        "extraction_quality": {
            "empty_pages_count": empty_pages_count,
            "empty_ratio": empty_ratio,
            "quality": extraction_quality,
        },
        "new_routes": new_routes,
        "invalid_pages": invalid_pages,
        "valid_pages": valid_pages,
        "summary": summary,
    }

    audit_report_path = job_dir / "audit_report.json"
    try:
        async with aiofiles.open(audit_report_path, "w", encoding="utf-8") as fh:
            await fh.write(json.dumps(audit_report, ensure_ascii=False, indent=2))
    except Exception as exc:
        logger.error(
            "Auditor: cannot write audit_report.json for job %s: %s", job_id, exc
        )
        await job_manager.fail_job(
            job_id,
            "AUDIT_CRITICAL_GAPS",
            f"Failed to write audit_report.json: {exc}",
        )
        return False

    logger.info(
        "Auditor: job=%s coverage=%.1f%% critical=%s second_pass=%s new_routes=%d",
        job_id,
        coverage_percent,
        critical,
        second_pass,
        new_routes_count,
    )

    # --- 10. Fail if critical on second pass --------------------------------
    if critical and second_pass:
        await job_manager.fail_job(
            job_id,
            "AUDIT_CRITICAL_GAPS",
            f"Coverage {coverage_percent}% below {settings.AUDIT_COVERAGE_MIN_PERCENT}% after second pass",
        )
        return False

    await job_manager.update_progress(job_id, 1, 1)
    return True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _url_path(url: str) -> str:
    """Return the path component of a URL (normalised)."""
    from urllib.parse import urlparse

    try:
        return urlparse(url).path or "/"
    except Exception:
        return "/"
