"""
F02 — Explorer: Route Discovery
Tests de checkpoints definidos en CHECKPOINTS.md y Features/f02-explorer-route-discovery.md

Uses respx to mock httpx AsyncClient calls.
"""
import json
import os
import uuid
from pathlib import Path
from typing import Generator
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
import respx
import httpx

# ── Force pytest-asyncio to auto mode for all tests in this module ──────────
pytestmark = pytest.mark.asyncio

BASE_URL = "https://example.com"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_job_id() -> str:
    return str(uuid.uuid4())


def _write_state(job_id: str, url: str = BASE_URL, max_pages: int = 50) -> Path:
    """Write a minimal state.json for the given job_id in JOB_BASE_DIR."""
    job_base = os.environ["JOB_BASE_DIR"]
    job_dir = Path(job_base) / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    now = "2026-01-01T00:00:00Z"
    state = {
        "job_id": job_id,
        "status": "queued",
        "url": url,
        "options": {"max_pages": max_pages},
        "progress": {"phase": "queued", "pages_done": 0, "pages_total": 0, "percent": 0},
        "error": None,
        "created_at": now,
        "started_at": None,
        "updated_at": now,
        "done_at": None,
        "ttl_remaining_seconds": None,
        "estimated_remaining_seconds": 0,
    }
    (job_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")
    return job_dir


def _load_routes(job_id: str) -> dict:
    job_base = os.environ["JOB_BASE_DIR"]
    routes_path = Path(job_base) / job_id / "routes.json"
    return json.loads(routes_path.read_text(encoding="utf-8"))


def _load_state(job_id: str) -> dict:
    job_base = os.environ["JOB_BASE_DIR"]
    state_path = Path(job_base) / job_id / "state.json"
    return json.loads(state_path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Sitemap XML fixtures
# ---------------------------------------------------------------------------

def _sitemap_xml(urls: list[str], priorities: list[float | None] | None = None) -> str:
    entries = []
    for i, url in enumerate(urls):
        priority_str = ""
        if priorities and priorities[i] is not None:
            priority_str = f"    <priority>{priorities[i]}</priority>\n"
        entries.append(
            f"  <url>\n    <loc>{url}</loc>\n{priority_str}  </url>"
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(entries)
        + "\n</urlset>"
    )


def _sitemap_index_xml(child_urls: list[str]) -> str:
    entries = []
    for url in child_urls:
        entries.append(f"  <sitemap>\n    <loc>{url}</loc>\n  </sitemap>")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(entries)
        + "\n</sitemapindex>"
    )


def _robots_txt(sitemap_url: str) -> str:
    return f"User-agent: *\nDisallow: /admin\nSitemap: {sitemap_url}\n"


def _homepage_html(links: list[str]) -> str:
    anchors = "".join(f'<a href="{link}">Link</a>' for link in links)
    return f"<html><body>{anchors}</body></html>"


# ---------------------------------------------------------------------------
# Test 1: sitemap.xml parsed with several URLs
# ---------------------------------------------------------------------------

@respx.mock
async def test_sitemap_xml_parsed() -> None:
    """sitemap.xml with several URLs → correct routes.json."""
    job_id = _make_job_id()
    _write_state(job_id)

    urls = [
        f"{BASE_URL}/about",
        f"{BASE_URL}/contact",
        f"{BASE_URL}/products",
    ]
    sitemap_body = _sitemap_xml(urls, priorities=[0.9, 0.8, 0.7])

    # robots.txt → 404, sitemap.xml → 200, other sitemaps → 404
    respx.get(f"{BASE_URL}/robots.txt").mock(return_value=httpx.Response(404))
    respx.get(f"{BASE_URL}/sitemap.xml").mock(
        return_value=httpx.Response(200, text=sitemap_body)
    )
    # All other common sitemap paths → 404
    for path in ("/sitemap_index.xml", "/sitemap-pages.xml", "/sitemap-posts.xml",
                 "/sitemap-products.xml", "/page-sitemap.xml"):
        respx.get(f"{BASE_URL}{path}").mock(return_value=httpx.Response(404))

    from app.pipeline.explorer import run_explorer
    result = await run_explorer(job_id)

    assert result is True
    data = _load_routes(job_id)
    assert data["job_id"] == job_id
    assert data["base_url"] == BASE_URL
    assert data["discovery_method"] == "sitemap"
    route_urls = [r["url"] for r in data["routes"]]
    for url in urls:
        assert url in route_urls, f"Expected {url} in routes"
    # Verify sources are "sitemap"
    for r in data["routes"]:
        if r["url"] in urls:
            assert r["source"] == "sitemap"
    # Verify priorities parsed
    about_route = next(r for r in data["routes"] if r["url"] == f"{BASE_URL}/about")
    assert about_route["priority"] == 0.9


# ---------------------------------------------------------------------------
# Test 2: sitemap_index nested (depth 2)
# ---------------------------------------------------------------------------

@respx.mock
async def test_sitemap_index_nested() -> None:
    """sitemap_index.xml with 2 child sitemaps → URLs from both children collected."""
    job_id = _make_job_id()
    _write_state(job_id)

    child1_url = f"{BASE_URL}/sitemap-1.xml"
    child2_url = f"{BASE_URL}/sitemap-2.xml"
    index_body = _sitemap_index_xml([child1_url, child2_url])
    child1_body = _sitemap_xml([f"{BASE_URL}/page-a", f"{BASE_URL}/page-b"])
    child2_body = _sitemap_xml([f"{BASE_URL}/page-c", f"{BASE_URL}/page-d"])

    respx.get(f"{BASE_URL}/robots.txt").mock(return_value=httpx.Response(404))
    respx.get(f"{BASE_URL}/sitemap.xml").mock(return_value=httpx.Response(404))
    respx.get(f"{BASE_URL}/sitemap_index.xml").mock(
        return_value=httpx.Response(200, text=index_body)
    )
    respx.get(child1_url).mock(return_value=httpx.Response(200, text=child1_body))
    respx.get(child2_url).mock(return_value=httpx.Response(200, text=child2_body))
    for path in ("/sitemap-pages.xml", "/sitemap-posts.xml",
                 "/sitemap-products.xml", "/page-sitemap.xml"):
        respx.get(f"{BASE_URL}{path}").mock(return_value=httpx.Response(404))

    from app.pipeline.explorer import run_explorer
    result = await run_explorer(job_id)

    assert result is True
    data = _load_routes(job_id)
    assert data["discovery_method"] == "sitemap"
    route_urls = [r["url"] for r in data["routes"]]
    assert f"{BASE_URL}/page-a" in route_urls
    assert f"{BASE_URL}/page-b" in route_urls
    assert f"{BASE_URL}/page-c" in route_urls
    assert f"{BASE_URL}/page-d" in route_urls


# ---------------------------------------------------------------------------
# Test 3: robots.txt Sitemap: directive
# ---------------------------------------------------------------------------

@respx.mock
async def test_robots_txt_sitemap_directive() -> None:
    """robots.txt with Sitemap: header → sitemap is fetched and parsed."""
    job_id = _make_job_id()
    _write_state(job_id)

    custom_sitemap_url = f"{BASE_URL}/custom-sitemap.xml"
    robots_body = _robots_txt(custom_sitemap_url)
    sitemap_body = _sitemap_xml([f"{BASE_URL}/faq", f"{BASE_URL}/blog"])

    respx.get(f"{BASE_URL}/robots.txt").mock(
        return_value=httpx.Response(200, text=robots_body)
    )
    respx.get(custom_sitemap_url).mock(
        return_value=httpx.Response(200, text=sitemap_body)
    )
    # Regular sitemap.xml returns 404 (robots.txt sitemap should take priority)
    respx.get(f"{BASE_URL}/sitemap.xml").mock(return_value=httpx.Response(404))
    for path in ("/sitemap_index.xml", "/sitemap-pages.xml", "/sitemap-posts.xml",
                 "/sitemap-products.xml", "/page-sitemap.xml"):
        respx.get(f"{BASE_URL}{path}").mock(return_value=httpx.Response(404))

    from app.pipeline.explorer import run_explorer
    result = await run_explorer(job_id)

    assert result is True
    data = _load_routes(job_id)
    assert data["discovery_method"] == "sitemap"
    route_urls = [r["url"] for r in data["routes"]]
    assert f"{BASE_URL}/faq" in route_urls
    assert f"{BASE_URL}/blog" in route_urls


# ---------------------------------------------------------------------------
# Test 4: homepage links fallback (no sitemap)
# ---------------------------------------------------------------------------

@respx.mock
async def test_homepage_links_fallback() -> None:
    """No sitemap available → internal links extracted from homepage."""
    job_id = _make_job_id()
    _write_state(job_id)

    homepage_body = _homepage_html([
        "/about-us",
        "/services",
        "/contact-us",
        "https://external.com/page",   # external — should be filtered
    ])

    # All sitemaps return 404
    respx.get(f"{BASE_URL}/robots.txt").mock(return_value=httpx.Response(404))
    for path in ("/sitemap.xml", "/sitemap_index.xml", "/sitemap-pages.xml",
                 "/sitemap-posts.xml", "/sitemap-products.xml", "/page-sitemap.xml"):
        respx.get(f"{BASE_URL}{path}").mock(return_value=httpx.Response(404))
    # Homepage
    respx.get(BASE_URL).mock(return_value=httpx.Response(200, html=homepage_body))

    from app.pipeline.explorer import run_explorer
    result = await run_explorer(job_id)

    assert result is True
    data = _load_routes(job_id)
    assert data["discovery_method"] == "homepage_links"
    route_urls = [r["url"] for r in data["routes"]]
    assert f"{BASE_URL}/about-us" in route_urls
    assert f"{BASE_URL}/services" in route_urls
    assert f"{BASE_URL}/contact-us" in route_urls
    # External link must NOT be included
    assert "https://external.com/page" not in route_urls
    # All sources should be "homepage_links"
    for r in data["routes"]:
        if r["url"] != BASE_URL:
            assert r["source"] == "homepage_links"


# ---------------------------------------------------------------------------
# Test 5: hardcoded fallback (no sitemap, no useful homepage links)
# ---------------------------------------------------------------------------

@respx.mock
async def test_hardcoded_fallback() -> None:
    """No sitemap, no useful homepage links → hardcoded fallback paths used."""
    job_id = _make_job_id()
    _write_state(job_id)

    # Homepage with only external links
    homepage_body = _homepage_html(["https://cdn.example.net/asset.js"])

    respx.get(f"{BASE_URL}/robots.txt").mock(return_value=httpx.Response(404))
    for path in ("/sitemap.xml", "/sitemap_index.xml", "/sitemap-pages.xml",
                 "/sitemap-posts.xml", "/sitemap-products.xml", "/page-sitemap.xml"):
        respx.get(f"{BASE_URL}{path}").mock(return_value=httpx.Response(404))
    respx.get(BASE_URL).mock(return_value=httpx.Response(200, html=homepage_body))

    from app.pipeline.explorer import run_explorer
    result = await run_explorer(job_id)

    assert result is True
    data = _load_routes(job_id)
    assert data["discovery_method"] == "fallback"
    route_urls = [r["url"] for r in data["routes"]]
    # At least some hardcoded paths should be present
    hardcoded_expected = ["/about", "/contact", "/products", "/blog"]
    found = any(any(p in u for u in route_urls) for p in hardcoded_expected)
    assert found, "Expected at least one hardcoded fallback route"
    for r in data["routes"]:
        assert r["source"] == "fallback"


# ---------------------------------------------------------------------------
# Test 6: asset extensions are filtered
# ---------------------------------------------------------------------------

@respx.mock
async def test_filters_assets() -> None:
    """URLs with .css/.js/images are excluded from routes."""
    job_id = _make_job_id()
    _write_state(job_id)

    sitemap_body = _sitemap_xml([
        f"{BASE_URL}/about",           # valid
        f"{BASE_URL}/style.css",       # must be excluded
        f"{BASE_URL}/app.js",          # must be excluded
        f"{BASE_URL}/logo.png",        # must be excluded
        f"{BASE_URL}/font.woff2",      # must be excluded
        f"{BASE_URL}/video.mp4",       # must be excluded
        f"{BASE_URL}/doc.pdf",         # must be excluded
    ])

    respx.get(f"{BASE_URL}/robots.txt").mock(return_value=httpx.Response(404))
    respx.get(f"{BASE_URL}/sitemap.xml").mock(
        return_value=httpx.Response(200, text=sitemap_body)
    )
    for path in ("/sitemap_index.xml", "/sitemap-pages.xml", "/sitemap-posts.xml",
                 "/sitemap-products.xml", "/page-sitemap.xml"):
        respx.get(f"{BASE_URL}{path}").mock(return_value=httpx.Response(404))

    from app.pipeline.explorer import run_explorer
    result = await run_explorer(job_id)

    assert result is True
    data = _load_routes(job_id)
    route_urls = [r["url"] for r in data["routes"]]
    assert f"{BASE_URL}/about" in route_urls
    for excluded in (".css", ".js", ".png", ".woff2", ".mp4", ".pdf"):
        for url in route_urls:
            assert not url.endswith(excluded), f"Asset URL leaked into routes: {url}"


# ---------------------------------------------------------------------------
# Test 7: admin / system routes are filtered
# ---------------------------------------------------------------------------

@respx.mock
async def test_filters_admin_routes() -> None:
    """/wp-admin, /login, /_next, /api/ etc. are excluded."""
    job_id = _make_job_id()
    _write_state(job_id)

    sitemap_body = _sitemap_xml([
        f"{BASE_URL}/about",             # valid
        f"{BASE_URL}/wp-admin/edit.php", # excluded
        f"{BASE_URL}/wp-login.php",      # excluded
        f"{BASE_URL}/admin/dashboard",   # excluded
        f"{BASE_URL}/login",             # excluded
        f"{BASE_URL}/dashboard",         # excluded
        f"{BASE_URL}/_next/static/abc",  # excluded
        f"{BASE_URL}/.well-known/acme",  # excluded
        f"{BASE_URL}/api/users",         # excluded
    ])

    respx.get(f"{BASE_URL}/robots.txt").mock(return_value=httpx.Response(404))
    respx.get(f"{BASE_URL}/sitemap.xml").mock(
        return_value=httpx.Response(200, text=sitemap_body)
    )
    for path in ("/sitemap_index.xml", "/sitemap-pages.xml", "/sitemap-posts.xml",
                 "/sitemap-products.xml", "/page-sitemap.xml"):
        respx.get(f"{BASE_URL}{path}").mock(return_value=httpx.Response(404))

    from app.pipeline.explorer import run_explorer
    result = await run_explorer(job_id)

    assert result is True
    data = _load_routes(job_id)
    route_urls = [r["url"] for r in data["routes"]]
    assert f"{BASE_URL}/about" in route_urls
    forbidden_substrings = [
        "/wp-admin", "/wp-login", "/admin", "/login", "/dashboard",
        "/_next", "/.well-known", "/api/",
    ]
    for url in route_urls:
        for forbidden in forbidden_substrings:
            assert forbidden not in url, f"Admin/system URL leaked into routes: {url}"


# ---------------------------------------------------------------------------
# Test 8: tracking params stripped, base URL preserved
# ---------------------------------------------------------------------------

@respx.mock
async def test_filters_tracking_params() -> None:
    """?utm_* and other tracking params removed; URL base is kept if otherwise valid."""
    job_id = _make_job_id()
    _write_state(job_id)

    sitemap_body = _sitemap_xml([
        f"{BASE_URL}/about?utm_source=email&utm_medium=newsletter",
        f"{BASE_URL}/contact?fbclid=abc123",
        f"{BASE_URL}/products?ref=homepage&color=blue",  # ref stripped, color kept
    ])

    respx.get(f"{BASE_URL}/robots.txt").mock(return_value=httpx.Response(404))
    respx.get(f"{BASE_URL}/sitemap.xml").mock(
        return_value=httpx.Response(200, text=sitemap_body)
    )
    for path in ("/sitemap_index.xml", "/sitemap-pages.xml", "/sitemap-posts.xml",
                 "/sitemap-products.xml", "/page-sitemap.xml"):
        respx.get(f"{BASE_URL}{path}").mock(return_value=httpx.Response(404))

    from app.pipeline.explorer import run_explorer
    result = await run_explorer(job_id)

    assert result is True
    data = _load_routes(job_id)
    route_urls = [r["url"] for r in data["routes"]]

    # Tracking params must be gone
    for url in route_urls:
        assert "utm_" not in url, f"UTM param leaked: {url}"
        assert "fbclid" not in url, f"fbclid param leaked: {url}"
        assert "ref=" not in url, f"ref= param leaked: {url}"

    # Base URLs should be present
    assert any("/about" in u for u in route_urls)
    assert any("/contact" in u for u in route_urls)
    assert any("/products" in u for u in route_urls)


# ---------------------------------------------------------------------------
# Test 9: MAX_PAGES_PER_JOB respected
# ---------------------------------------------------------------------------

@respx.mock
async def test_max_pages_respected() -> None:
    """routes.json must not exceed MAX_PAGES_PER_JOB."""
    max_pages = 5
    job_id = _make_job_id()
    _write_state(job_id, max_pages=max_pages)

    # Provide 20 URLs in sitemap
    urls = [f"{BASE_URL}/page-{i}" for i in range(20)]
    sitemap_body = _sitemap_xml(urls)

    respx.get(f"{BASE_URL}/robots.txt").mock(return_value=httpx.Response(404))
    respx.get(f"{BASE_URL}/sitemap.xml").mock(
        return_value=httpx.Response(200, text=sitemap_body)
    )
    for path in ("/sitemap_index.xml", "/sitemap-pages.xml", "/sitemap-posts.xml",
                 "/sitemap-products.xml", "/page-sitemap.xml"):
        respx.get(f"{BASE_URL}{path}").mock(return_value=httpx.Response(404))

    from app.pipeline.explorer import run_explorer
    result = await run_explorer(job_id)

    assert result is True
    data = _load_routes(job_id)
    assert data["total_routes"] <= max_pages, (
        f"total_routes={data['total_routes']} exceeds max_pages={max_pages}"
    )
    assert len(data["routes"]) <= max_pages


# ---------------------------------------------------------------------------
# Test 10: NO_ROUTES_FOUND fails the job
# ---------------------------------------------------------------------------

@respx.mock
async def test_no_routes_found_fails_job() -> None:
    """When all strategies find nothing, job is failed with NO_ROUTES_FOUND."""
    job_id = _make_job_id()
    _write_state(job_id)

    # All sitemaps 404, homepage 404 → no routes at all
    respx.get(f"{BASE_URL}/robots.txt").mock(return_value=httpx.Response(404))
    for path in ("/sitemap.xml", "/sitemap_index.xml", "/sitemap-pages.xml",
                 "/sitemap-posts.xml", "/sitemap-products.xml", "/page-sitemap.xml"):
        respx.get(f"{BASE_URL}{path}").mock(return_value=httpx.Response(404))
    respx.get(BASE_URL).mock(return_value=httpx.Response(404))

    # Override _build_fallback_routes so it returns empty list
    with patch("app.pipeline.explorer._build_fallback_routes", return_value=[]):
        from app.pipeline.explorer import run_explorer
        result = await run_explorer(job_id)

    assert result is False
    state = _load_state(job_id)
    assert state["status"] == "failed"
    assert state["error"] is not None
    assert state["error"]["code"] == "NO_ROUTES_FOUND"


# ---------------------------------------------------------------------------
# Test 11: trailing slash deduplication
# ---------------------------------------------------------------------------

@respx.mock
async def test_deduplication() -> None:
    """Trailing slash variants of the same URL are deduplicated."""
    job_id = _make_job_id()
    _write_state(job_id)

    # Provide duplicate variants — /about and /about/ should count as one
    sitemap_body = _sitemap_xml([
        f"{BASE_URL}/about",
        f"{BASE_URL}/about/",
        f"{BASE_URL}/services",
        f"{BASE_URL}/services/",
    ])

    respx.get(f"{BASE_URL}/robots.txt").mock(return_value=httpx.Response(404))
    respx.get(f"{BASE_URL}/sitemap.xml").mock(
        return_value=httpx.Response(200, text=sitemap_body)
    )
    for path in ("/sitemap_index.xml", "/sitemap-pages.xml", "/sitemap-posts.xml",
                 "/sitemap-products.xml", "/page-sitemap.xml"):
        respx.get(f"{BASE_URL}{path}").mock(return_value=httpx.Response(404))

    from app.pipeline.explorer import run_explorer
    result = await run_explorer(job_id)

    assert result is True
    data = _load_routes(job_id)
    route_urls = [r["url"] for r in data["routes"]]

    # /about and /about/ must appear at most once combined
    about_count = route_urls.count(f"{BASE_URL}/about") + route_urls.count(f"{BASE_URL}/about/")
    assert about_count == 1, f"/about duplicated: {about_count} times"

    services_count = route_urls.count(f"{BASE_URL}/services") + route_urls.count(f"{BASE_URL}/services/")
    assert services_count == 1, f"/services duplicated: {services_count} times"


# ---------------------------------------------------------------------------
# Test 12: homepage always included (when accessible)
# ---------------------------------------------------------------------------

@respx.mock
async def test_homepage_always_included() -> None:
    """Homepage URL is always present as the first route when accessible."""
    job_id = _make_job_id()
    _write_state(job_id)

    # Sitemap does NOT list the homepage
    sitemap_body = _sitemap_xml([
        f"{BASE_URL}/about",
        f"{BASE_URL}/contact",
    ])

    respx.get(f"{BASE_URL}/robots.txt").mock(return_value=httpx.Response(404))
    respx.get(f"{BASE_URL}/sitemap.xml").mock(
        return_value=httpx.Response(200, text=sitemap_body)
    )
    for path in ("/sitemap_index.xml", "/sitemap-pages.xml", "/sitemap-posts.xml",
                 "/sitemap-products.xml", "/page-sitemap.xml"):
        respx.get(f"{BASE_URL}{path}").mock(return_value=httpx.Response(404))

    from app.pipeline.explorer import run_explorer
    result = await run_explorer(job_id)

    assert result is True
    data = _load_routes(job_id)
    route_urls = [r["url"] for r in data["routes"]]

    # Homepage (BASE_URL itself or BASE_URL + "/") must be in the list
    homepage_present = BASE_URL in route_urls or f"{BASE_URL}/" in route_urls
    assert homepage_present, f"Homepage not found in routes: {route_urls}"

    # Homepage should be first
    first_url = route_urls[0]
    assert first_url in (BASE_URL, f"{BASE_URL}/"), (
        f"Homepage must be the first route, got: {first_url}"
    )
