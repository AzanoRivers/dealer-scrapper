"""
Explorer pipeline subagent — Route Discovery.

Discovers all routes to scrape for a given job and writes routes.json.
Uses a prioritized chain: robots.txt -> sitemap variants -> homepage links -> fallback.
"""

import json
import logging
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlparse, urlunparse, parse_qs, urlencode

import aiofiles
import httpx
from bs4 import BeautifulSoup

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

_EXCLUDED_EXTENSIONS: frozenset[str] = frozenset({
    ".css", ".js", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg",
    ".pdf", ".zip", ".ico", ".woff", ".woff2", ".ttf", ".eot", ".mp4", ".mp3",
})

_EXCLUDED_PATH_PREFIXES: tuple[str, ...] = (
    "/wp-admin", "/wp-login", "/admin", "/login", "/dashboard",
    "/_next", "/.well-known", "/wp-content", "/wp-includes", "/__", "/api/",
)

_TRACKING_PARAMS: frozenset[str] = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "utm_name",
    "ref", "session", "token", "fbclid", "gclid",
})

_COMMON_SITEMAP_PATHS: tuple[str, ...] = (
    "/sitemap.xml",
    "/sitemap_index.xml",
    "/sitemap-pages.xml",
    "/sitemap-posts.xml",
    "/sitemap-products.xml",
    "/page-sitemap.xml",
)

_HARDCODED_PATHS: tuple[str, ...] = (
    "/", "/about", "/contact", "/products", "/blog", "/faq", "/services",
)

_SITEMAP_NAMESPACES: dict[str, str] = {
    "sm": "http://www.sitemaps.org/schemas/sitemap/0.9",
}

# ---------------------------------------------------------------------------
# HTTP client helpers
# ---------------------------------------------------------------------------


def _build_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers=_HEADERS,
        timeout=httpx.Timeout(15.0),
        follow_redirects=True,
        max_redirects=3,
    )


async def _safe_get(client: httpx.AsyncClient, url: str) -> Optional[httpx.Response]:
    """Fetch url, returning None on any error (4xx, 5xx, network, timeout)."""
    try:
        resp = await client.get(url)
        if resp.status_code in (200, 301, 302):
            return resp
        if resp.status_code in (404, 403, 410, 500):
            return None
        # For other codes still return if 2xx
        if 200 <= resp.status_code < 300:
            return resp
        return None
    except (httpx.TimeoutException, httpx.RequestError, httpx.HTTPError):
        return None


# ---------------------------------------------------------------------------
# URL normalisation & filtering
# ---------------------------------------------------------------------------


def _normalise_url(url: str, base_url: str) -> Optional[str]:
    """
    Convert url (possibly relative) to absolute, strip tracking params,
    and normalise trailing slashes. Returns None if url should be excluded.
    """
    try:
        absolute = urljoin(base_url, url.strip())
        parsed = urlparse(absolute)
    except Exception:
        return None

    # Only http/https
    if parsed.scheme not in ("http", "https"):
        return None

    # Same domain (no subdomain differences)
    base_host = urlparse(base_url).netloc.lower().lstrip("www.")
    url_host = parsed.netloc.lower().lstrip("www.")
    if url_host != base_host:
        return None

    # Exclude by extension
    path_lower = parsed.path.lower()
    for ext in _EXCLUDED_EXTENSIONS:
        if path_lower.endswith(ext):
            return None

    # Exclude admin / system paths
    for prefix in _EXCLUDED_PATH_PREFIXES:
        if path_lower == prefix or path_lower.startswith(prefix + "/") or path_lower.startswith(prefix):
            return None

    # Strip tracking params — keep non-tracking ones
    if parsed.query:
        qs = parse_qs(parsed.query, keep_blank_values=True)
        filtered_qs = {k: v for k, v in qs.items() if k not in _TRACKING_PARAMS}
        new_query = urlencode(filtered_qs, doseq=True)
    else:
        new_query = ""

    # Normalise: remove trailing slash (except root /)
    path = parsed.path.rstrip("/") or "/"

    clean = urlunparse((
        parsed.scheme,
        parsed.netloc.lower(),
        path,
        "",       # params
        new_query,
        "",       # fragment
    ))
    return clean


def _is_sitemap_index(content: str) -> bool:
    """Heuristic: does the XML look like a sitemap index (has <sitemapindex>)?"""
    return "<sitemapindex" in content


def _parse_sitemap_locs(content: str) -> list[str]:
    """
    Parse XML sitemap (both sitemap and sitemapindex).
    Returns list of <loc> text values.
    Uses lxml via BeautifulSoup for robustness with malformed XML.
    """
    locs: list[str] = []
    try:
        soup = BeautifulSoup(content, "lxml-xml")
        for loc_tag in soup.find_all("loc"):
            text = loc_tag.get_text(strip=True)
            if text:
                locs.append(text)
    except Exception:
        pass
    return locs


# ---------------------------------------------------------------------------
# Discovery strategies
# ---------------------------------------------------------------------------


async def _fetch_robots_sitemap_urls(client: httpx.AsyncClient, base_url: str) -> list[str]:
    """Extract Sitemap: directives from /robots.txt."""
    robots_url = urljoin(base_url, "/robots.txt")
    resp = await _safe_get(client, robots_url)
    if resp is None:
        return []
    sitemap_urls: list[str] = []
    for line in resp.text.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("sitemap:"):
            sitemap_url = stripped[len("sitemap:"):].strip()
            if sitemap_url:
                sitemap_urls.append(sitemap_url)
    return sitemap_urls


async def _resolve_sitemap(
    client: httpx.AsyncClient,
    sitemap_url: str,
    base_url: str,
    visited: set[str],
    depth: int,
    max_depth: int = 2,
) -> list[dict[str, object]]:
    """
    Recursively resolve a sitemap URL.
    Returns list of route dicts: {"url": ..., "source": "sitemap", "priority": ...}
    """
    if sitemap_url in visited or depth > max_depth:
        return []
    visited.add(sitemap_url)

    resp = await _safe_get(client, sitemap_url)
    if resp is None:
        return []

    content = resp.text
    locs = _parse_sitemap_locs(content)

    if _is_sitemap_index(content):
        # Recurse into child sitemaps
        routes: list[dict[str, object]] = []
        for child_sitemap_url in locs:
            child_routes = await _resolve_sitemap(
                client, child_sitemap_url, base_url, visited, depth + 1, max_depth
            )
            routes.extend(child_routes)
        return routes

    # It's a regular sitemap — extract page URLs
    # Also try to get priority from <priority> tags
    routes = []
    try:
        soup = BeautifulSoup(content, "lxml-xml")
        for url_tag in soup.find_all("url"):
            loc = url_tag.find("loc")
            if loc is None:
                continue
            raw_url = loc.get_text(strip=True)
            priority_tag = url_tag.find("priority")
            priority: Optional[float] = None
            if priority_tag:
                try:
                    priority = float(priority_tag.get_text(strip=True))
                except (ValueError, TypeError):
                    priority = None

            clean = _normalise_url(raw_url, base_url)
            if clean:
                routes.append({"url": clean, "source": "sitemap", "priority": priority})
    except Exception:
        # Fallback: use raw locs without priority
        for raw_url in locs:
            clean = _normalise_url(raw_url, base_url)
            if clean:
                routes.append({"url": clean, "source": "sitemap", "priority": None})

    return routes


async def _discover_via_sitemaps(
    client: httpx.AsyncClient,
    base_url: str,
) -> list[dict[str, object]]:
    """
    Try all sitemap strategies (robots.txt + common paths).
    Returns combined list of route dicts (deduped by URL).
    """
    visited_sitemaps: set[str] = set()
    all_routes: list[dict[str, object]] = []
    seen_urls: set[str] = set()

    # Step 1: robots.txt may list sitemap URLs
    robots_sitemap_urls = await _fetch_robots_sitemap_urls(client, base_url)

    # Build ordered list of sitemap URLs to try
    # robots.txt sitemaps first, then common paths
    sitemap_candidates: list[str] = []
    for su in robots_sitemap_urls:
        if su not in sitemap_candidates:
            sitemap_candidates.append(su)
    for path in _COMMON_SITEMAP_PATHS:
        candidate = urljoin(base_url, path)
        if candidate not in sitemap_candidates:
            sitemap_candidates.append(candidate)

    for sitemap_url in sitemap_candidates:
        routes = await _resolve_sitemap(
            client, sitemap_url, base_url, visited_sitemaps, depth=1
        )
        for route in routes:
            url_str = str(route["url"])
            if url_str not in seen_urls:
                seen_urls.add(url_str)
                all_routes.append(route)

    return all_routes


async def _discover_via_homepage(
    client: httpx.AsyncClient,
    base_url: str,
) -> list[dict[str, object]]:
    """Extract internal <a href> links from the homepage."""
    resp = await _safe_get(client, base_url)
    if resp is None:
        resp = await _safe_get(client, base_url.rstrip("/") + "/")
    if resp is None:
        return []

    routes: list[dict[str, object]] = []
    seen: set[str] = set()
    try:
        soup = BeautifulSoup(resp.text, "lxml")
        for tag in soup.find_all("a", href=True):
            raw = tag["href"]
            clean = _normalise_url(raw, base_url)
            if clean and clean not in seen:
                seen.add(clean)
                routes.append({"url": clean, "source": "homepage_links", "priority": None})
    except Exception:
        pass
    return routes


def _build_fallback_routes(base_url: str) -> list[dict[str, object]]:
    """Build hardcoded fallback routes."""
    routes: list[dict[str, object]] = []
    seen: set[str] = set()
    for path in _HARDCODED_PATHS:
        clean = _normalise_url(path, base_url)
        if clean and clean not in seen:
            seen.add(clean)
            routes.append({"url": clean, "source": "fallback", "priority": None})
    return routes


# ---------------------------------------------------------------------------
# Route deduplication & merging helpers
# ---------------------------------------------------------------------------


def _deduplicate(routes: list[dict[str, object]]) -> list[dict[str, object]]:
    """Remove duplicate routes by URL (first occurrence wins)."""
    seen: set[str] = set()
    result: list[dict[str, object]] = []
    for route in routes:
        url_str = str(route["url"])
        if url_str not in seen:
            seen.add(url_str)
            result.append(route)
    return result


def _ensure_homepage_first(
    routes: list[dict[str, object]], base_url: str
) -> list[dict[str, object]]:
    """Move homepage to the front (or add it if missing)."""
    homepage = _normalise_url("/", base_url) or base_url.rstrip("/") + "/"
    # Also match base_url itself
    homepage_candidates = {homepage, base_url.rstrip("/"), base_url.rstrip("/") + "/"}

    existing_homepage: Optional[dict[str, object]] = None
    others: list[dict[str, object]] = []
    for route in routes:
        if str(route["url"]) in homepage_candidates:
            if existing_homepage is None:
                existing_homepage = route
        else:
            others.append(route)

    if existing_homepage is not None:
        return [existing_homepage] + others
    else:
        # Add homepage with unknown source
        homepage_route: dict[str, object] = {
            "url": homepage,
            "source": "homepage_links",
            "priority": None,
        }
        return [homepage_route] + others


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


async def run_explorer(job_id: str) -> bool:
    """
    Discovers routes for the job's URL and writes routes.json.
    Returns True on success, False if job was failed (NO_ROUTES_FOUND).
    Updates job status to 'exploring' at the start.
    """
    # Read state
    state = await job_manager.get_state(job_id)
    if state is None:
        logger.error("Explorer: state not found for job %s", job_id)
        return False

    base_url = state.url.rstrip("/")
    max_pages: int = state.options.get("max_pages", settings.MAX_PAGES_PER_JOB)  # type: ignore[assignment]
    job_dir = Path(settings.JOB_BASE_DIR) / job_id

    # Update status to exploring
    await job_manager.update_status(job_id, JobStatus.exploring)
    await job_manager.update_progress(job_id, 0, 1)

    async with _build_client() as client:
        # 1. Try sitemap-based discovery (includes robots.txt)
        sitemap_routes = await _discover_via_sitemaps(client, base_url)

        if sitemap_routes:
            discovery_method = "sitemap"
            routes = sitemap_routes
        else:
            # 2. Fallback: homepage links
            homepage_routes = await _discover_via_homepage(client, base_url)
            if homepage_routes:
                discovery_method = "homepage_links"
                routes = homepage_routes
            else:
                # 3. Last resort: hardcoded paths
                routes = _build_fallback_routes(base_url)
                discovery_method = "fallback"

        routes = _deduplicate(routes)

        # Apply max pages limit before homepage injection
        routes = routes[:max_pages]

    if not routes:
        await job_manager.fail_job(
            job_id,
            "NO_ROUTES_FOUND",
            "No routes found to scrape. The site may require JavaScript to render.",
        )
        return False

    # Ensure homepage is first — only after confirming we have real routes
    routes = _ensure_homepage_first(routes, base_url)
    # Re-apply limit since homepage insertion can exceed max_pages
    routes = routes[:max_pages]

    # Build routes.json payload
    payload: dict[str, object] = {
        "job_id": job_id,
        "base_url": base_url,
        "total_routes": len(routes),
        "discovery_method": discovery_method,
        "routes": routes,
    }

    # Write to disk with aiofiles
    routes_path = job_dir / "routes.json"
    async with aiofiles.open(routes_path, "w", encoding="utf-8") as f:
        await f.write(json.dumps(payload, ensure_ascii=False, indent=2))

    logger.info(
        "Explorer: job=%s method=%s routes=%d",
        job_id,
        discovery_method,
        len(routes),
    )
    await job_manager.update_progress(job_id, 1, 1)
    return True
