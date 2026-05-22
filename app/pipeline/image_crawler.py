"""
Image Crawler — post-extraction site-wide image consolidation.

Reads pages/<hash>.json files written by the Extractor, deduplicates
images across the entire site, enriches each with aggregate classification
signals (cross-page frequency, OG status), and writes image_catalogue.json.

Runs silently within the extracting phase — errors never fail the job.
"""

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Roles in priority order for conflict resolution
_ROLE_PRIORITY: dict[str, int] = {
    "banner": 0,
    "gallery_item": 1,
    "reference": 2,
    "logo": 3,
}

_CLEAN_RE = re.compile(r"[^a-z0-9]+")


def _best_role(role_hints: list[str], frequency: int, total_pages: int) -> str:
    """
    Determine final role for an image using aggregate signals.
    - OG banner wins.
    - Frequency >= 40% of site pages → logo (repeated nav/footer element).
    - Among remaining roles, pick the one with highest priority.
    """
    if total_pages > 0 and frequency / total_pages >= 0.40:
        return "logo"  # Appears on most pages = site-wide repeated element

    best = "reference"
    best_prio = _ROLE_PRIORITY.get("reference", 99)
    for r in role_hints:
        p = _ROLE_PRIORITY.get(r, 99)
        if p < best_prio:
            best_prio = p
            best = r
    return best


def _slug_from_url(src: str) -> str:
    """Extract a clean slug from an image URL for use in filenames."""
    try:
        path = urlparse(src).path
        stem = Path(path).stem if path else "image"
        clean = _CLEAN_RE.sub("-", stem.lower()).strip("-")[:40]
        return clean or "image"
    except Exception:
        return "image"


async def run_image_crawler(job_id: str) -> dict:
    """
    Consolidates all images from extracted pages.
    Writes image_catalogue.json to the job directory.
    Returns summary stats. Never raises.
    """
    from app.config import settings

    job_dir = Path(settings.JOB_BASE_DIR) / job_id
    pages_dir = job_dir / "pages"

    if not pages_dir.exists():
        logger.debug("Image crawler: no pages dir for job %s", job_id)
        return {}

    page_files = list(pages_dir.glob("*.json"))
    total_pages = len(page_files)

    if total_pages == 0:
        return {}

    # Map: src → accumulated data
    registry: dict[str, dict] = {}

    for page_file in page_files:
        try:
            page_data = json.loads(page_file.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.debug("Image crawler: cannot read %s: %s", page_file, exc)
            continue

        page_url: str = page_data.get("url", "")

        for img in page_data.get("images", []) or []:
            src: str = img.get("src", "")
            if not src or src.startswith("data:"):
                continue

            if src not in registry:
                registry[src] = {
                    "src": src,
                    "alt": "",
                    "role_hints": [],
                    "pages": [],
                    "width": None,
                    "height": None,
                }

            entry = registry[src]
            role_hint: str = img.get("role_hint", "reference")
            entry["role_hints"].append(role_hint)

            if page_url and page_url not in entry["pages"]:
                entry["pages"].append(page_url)

            # Keep best alt (first non-empty)
            if not entry["alt"] and img.get("alt"):
                entry["alt"] = img["alt"]

            # Keep best dimensions
            if entry["width"] is None and img.get("width"):
                entry["width"] = img["width"]
            if entry["height"] is None and img.get("height"):
                entry["height"] = img["height"]

    if not registry:
        return {}

    # Build consolidated list
    consolidated: list[dict] = []
    for src, entry in registry.items():
        frequency = len(entry["pages"])
        final_role = _best_role(entry["role_hints"], frequency, total_pages)
        slug = _slug_from_url(src)

        consolidated.append(
            {
                "src": src,
                "alt": entry["alt"],
                "final_role": final_role,
                "frequency": frequency,
                "frequency_pct": round(frequency / total_pages * 100, 1) if total_pages > 0 else 0,
                "pages": entry["pages"],
                "width": entry["width"],
                "height": entry["height"],
                "slug": f"{final_role}-{slug}",
            }
        )

    # Sort: banners first, then gallery, reference, logo; within each by frequency desc
    role_order = {"banner": 0, "gallery_item": 1, "reference": 2, "logo": 3}
    consolidated.sort(
        key=lambda x: (role_order.get(x["final_role"], 4), -x["frequency"])
    )

    catalogue = {
        "total_unique": len(consolidated),
        "total_pages_scanned": total_pages,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "images": consolidated,
    }

    catalogue_path = job_dir / "image_catalogue.json"
    catalogue_path.write_text(
        json.dumps(catalogue, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    logger.info(
        "Image crawler: job=%s unique_images=%d pages=%d",
        job_id,
        len(consolidated),
        total_pages,
    )
    return {"total_unique": len(consolidated), "total_pages": total_pages}
