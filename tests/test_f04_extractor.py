"""
F04 — Extractor: PageData
Tests for checkpoints in CHECKPOINTS.md and Features/f04-extractor-page-data.md

No HTTP mocking — Extractor reads local HTML files from disk only.
"""

import hashlib
import json
import os
import uuid
from pathlib import Path

import pytest

# ── Force pytest-asyncio to auto mode for all tests in this module ──────────
pytestmark = pytest.mark.asyncio

BASE_URL = "https://example.com"

# ---------------------------------------------------------------------------
# HTML fixtures
# ---------------------------------------------------------------------------

_FULL_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <title>About Us</title>
  <meta name="description" content="We are a great company">
  <meta name="keywords" content="company, about, dealer">
  <meta property="og:title" content="About Us OG">
  <meta property="og:description" content="OG description here">
  <link rel="canonical" href="https://example.com/about">
  <script type="application/ld+json">{"@type": "Organization", "name": "Example Corp"}</script>
</head>
<body>
  <h1>About Us</h1>
  <h2>Our Mission</h2>
  <h2>Our Team</h2>
  <h3>Leadership</h3>
  <p>We are a company dedicated to excellence and innovation in the automotive dealer industry.
     Our team brings decades of experience to help dealerships succeed in a competitive market.</p>
  <p>Our comprehensive platform empowers dealerships with modern digital tools, analytics dashboards,
     and customer relationship management capabilities that drive measurable results.</p>
  <a href="/contact">Contact</a>
  <a href="https://example.com/team">Team</a>
  <a href="https://linkedin.com/company/example">LinkedIn</a>
  <img src="/images/team.jpg" alt="Our team" width="800" height="600">
  <img src="https://example.com/images/logo.png" alt="Logo">
  <form action="/submit"><input type="text" name="q"></form>
  <table><tr><td>Data</td></tr></table>
</body>
</html>
"""

_NO_TITLE_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head></head>
<body>
  <h1>Page Heading Used As Title</h1>
  <p>This page has no title tag so the first h1 must be used as the title fallback.</p>
  <p>Adding more text here to ensure word count is well above the empty threshold of fifty words.
     We want to be completely sure that the extraction is not considered empty due to short content.</p>
</body>
</html>
"""

_LONG_TEXT_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head><title>Long Page</title></head>
<body>
<p>{text}</p>
</body>
</html>
""".format(text=" ".join(["word"] * 3000))

_LINKS_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head><title>Links Test</title></head>
<body>
  <p>Content with enough words to not be considered empty. More filler text to pad the count.
     This page specifically tests internal and external link classification for the scraping pipeline.</p>
  <a href="/about">About (internal relative)</a>
  <a href="https://example.com/contact">Contact (internal absolute)</a>
  <a href="https://www.example.com/products">Products (internal www)</a>
  <a href="https://external.com/link">External site</a>
  <a href="https://another.org/page">Another external</a>
  <a href="mailto:info@example.com">Email (should be skipped)</a>
  <a href="javascript:void(0)">JS link (should be skipped)</a>
</body>
</html>
"""

_IMAGES_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head><title>Images Test</title></head>
<body>
  <p>Page content with sufficient text to pass the word count threshold easily.
     This test page verifies that relative image paths like logo.png and hero.jpg are
     correctly resolved to fully qualified absolute URLs using the base page URL as reference.
     Image extraction must handle root-relative paths, document-relative paths, and absolute URLs.</p>
  <img src="/logo.png" alt="Company Logo" width="200" height="100">
  <img src="images/hero.jpg" alt="Hero">
  <img src="https://example.com/absolute.png" alt="Absolute">
  <img src="" alt="Empty src should be skipped">
</body>
</html>
"""

_SCHEMA_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <title>Schema Test</title>
  <script type="application/ld+json">{"@type": "Organization", "name": "Example"}</script>
  <script type="application/ld+json">[{"@type": "Product", "name": "Car"}, {"@type": "Service"}]</script>
  <script type="application/ld+json">INVALID JSON {{</script>
</head>
<body>
  <p>Page content about schema markup and structured data for SEO purposes here.
     JSON-LD scripts enable search engines to understand the content type of the page
     and provide rich results in search engine results pages across automotive verticals. Structured data helps
     dealers appear in automotive search features and product listings.</p>
</body>
</html>
"""

_EMPTY_HTML = """\
<!DOCTYPE html>
<html><head><title>t</title></head><body><p>Hi</p></body></html>
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_job_id() -> str:
    return str(uuid.uuid4())


def _url_hash(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()


def _write_state(job_id: str, url: str = BASE_URL) -> Path:
    """Write a minimal state.json for the given job_id in JOB_BASE_DIR."""
    job_base = os.environ["JOB_BASE_DIR"]
    job_dir = Path(job_base) / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    now = "2026-01-01T00:00:00Z"
    state = {
        "job_id": job_id,
        "status": "fetching",
        "url": url,
        "options": {},
        "progress": {
            "phase": "fetching",
            "pages_done": 0,
            "pages_total": 0,
            "percent": 0,
        },
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


def _write_fetch_results(job_dir: Path, results: list[dict]) -> None:
    """Write fetch_results.json matching the schema produced by the Fetcher."""
    successful = sum(1 for r in results if r["status"] == "success")
    failed_count = sum(1 for r in results if r["status"] != "success")
    payload = {
        "job_id": job_dir.name,
        "total_urls": len(results),
        "successful": successful,
        "failed": failed_count,
        "results": results,
    }
    (job_dir / "fetch_results.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def _write_raw_html(job_dir: Path, url_hash: str, html_content: str) -> None:
    """Write raw HTML to raw/<url_hash>.html (creates raw/ if needed)."""
    raw_dir = job_dir / "raw"
    raw_dir.mkdir(exist_ok=True)
    (raw_dir / f"{url_hash}.html").write_text(html_content, encoding="utf-8")


def _load_state(job_id: str) -> dict:
    job_base = os.environ["JOB_BASE_DIR"]
    path = Path(job_base) / job_id / "state.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _load_extract_results(job_id: str) -> dict:
    job_base = os.environ["JOB_BASE_DIR"]
    path = Path(job_base) / job_id / "extract_results.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _load_page_data(job_id: str, url_hash: str) -> dict:
    job_base = os.environ["JOB_BASE_DIR"]
    path = Path(job_base) / job_id / "pages" / f"{url_hash}.json"
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Test 1: test_extracts_all_fields
# ---------------------------------------------------------------------------


async def test_extracts_all_fields() -> None:
    """Full HTML → all PageData fields present with correct values."""
    job_id = _make_job_id()
    job_dir = _write_state(job_id)
    url = f"{BASE_URL}/about"
    h = _url_hash(url)

    _write_raw_html(job_dir, h, _FULL_HTML)
    _write_fetch_results(
        job_dir,
        [{"url": url, "url_hash": h, "status": "success", "http_code": 200, "file": f"raw/{h}.html", "error": None}],
    )

    from app.pipeline.extractor import run_extractor

    result = await run_extractor(job_id)
    assert result is True

    data = _load_page_data(job_id, h)

    # Scalar fields
    assert data["url"] == url
    assert data["url_hash"] == h
    assert data["title"] == "About Us"
    assert data["meta_description"] == "We are a great company"
    assert "company" in data["meta_keywords"]
    assert "about" in data["meta_keywords"]
    assert "dealer" in data["meta_keywords"]

    # OG data
    assert data["og_data"]["og:title"] == "About Us OG"
    assert data["og_data"]["og:description"] == "OG description here"

    # Canonical and language
    assert data["canonical_url"] == "https://example.com/about"
    assert data["language"] == "en"

    # Headings
    assert "About Us" in data["headings"]["h1"]
    assert "Our Mission" in data["headings"]["h2"]
    assert "Our Team" in data["headings"]["h2"]
    assert "Leadership" in data["headings"]["h3"]

    # text_content and word_count
    assert isinstance(data["text_content"], str)
    assert len(data["text_content"]) > 0
    assert isinstance(data["word_count"], int)
    assert data["word_count"] > 0

    # Links
    assert isinstance(data["internal_links"], list)
    assert isinstance(data["external_links"], list)
    # LinkedIn is external
    assert any("linkedin.com" in lnk for lnk in data["external_links"])

    # Images
    assert len(data["images"]) >= 1
    team_img = next(
        (img for img in data["images"] if "team.jpg" in img["src"]), None
    )
    assert team_img is not None
    assert team_img["alt"] == "Our team"
    assert team_img["width"] == 800
    assert team_img["height"] == 600

    # Schema org
    assert isinstance(data["schema_org"], list)
    assert len(data["schema_org"]) >= 1
    assert data["schema_org"][0]["@type"] == "Organization"

    # Boolean flags
    assert data["has_forms"] is True
    assert data["has_tables"] is True

    # extracted_at
    assert data["extracted_at"].endswith("Z")


# ---------------------------------------------------------------------------
# Test 2: test_title_fallback_to_h1
# ---------------------------------------------------------------------------


async def test_title_fallback_to_h1() -> None:
    """No <title> tag → title is taken from first <h1>."""
    job_id = _make_job_id()
    job_dir = _write_state(job_id)
    url = f"{BASE_URL}/no-title"
    h = _url_hash(url)

    _write_raw_html(job_dir, h, _NO_TITLE_HTML)
    _write_fetch_results(
        job_dir,
        [{"url": url, "url_hash": h, "status": "success", "http_code": 200, "file": f"raw/{h}.html", "error": None}],
    )

    from app.pipeline.extractor import run_extractor

    result = await run_extractor(job_id)
    assert result is True

    data = _load_page_data(job_id, h)
    assert data["title"] == "Page Heading Used As Title"


# ---------------------------------------------------------------------------
# Test 3: test_text_content_readability
# ---------------------------------------------------------------------------


async def test_text_content_readability() -> None:
    """text_content is extracted via readability — not the raw full HTML."""
    job_id = _make_job_id()
    job_dir = _write_state(job_id)
    url = f"{BASE_URL}/readable"
    h = _url_hash(url)

    _write_raw_html(job_dir, h, _FULL_HTML)
    _write_fetch_results(
        job_dir,
        [{"url": url, "url_hash": h, "status": "success", "http_code": 200, "file": f"raw/{h}.html", "error": None}],
    )

    from app.pipeline.extractor import run_extractor

    result = await run_extractor(job_id)
    assert result is True

    data = _load_page_data(job_id, h)
    # text_content must be plain text — must NOT contain raw HTML tags
    assert "<html" not in data["text_content"]
    assert "<head" not in data["text_content"]
    assert "<body" not in data["text_content"]
    # Content must have actual text
    assert len(data["text_content"]) > 10


# ---------------------------------------------------------------------------
# Test 4: test_text_content_max_10000_chars
# ---------------------------------------------------------------------------


async def test_text_content_max_10000_chars() -> None:
    """HTML with very long text → text_content is truncated to ≤ 10,000 chars."""
    job_id = _make_job_id()
    job_dir = _write_state(job_id)
    url = f"{BASE_URL}/long"
    h = _url_hash(url)

    _write_raw_html(job_dir, h, _LONG_TEXT_HTML)
    _write_fetch_results(
        job_dir,
        [{"url": url, "url_hash": h, "status": "success", "http_code": 200, "file": f"raw/{h}.html", "error": None}],
    )

    from app.pipeline.extractor import run_extractor

    result = await run_extractor(job_id)
    assert result is True

    data = _load_page_data(job_id, h)
    assert len(data["text_content"]) <= 10_000, (
        f"text_content must be ≤ 10,000 chars, got {len(data['text_content'])}"
    )


# ---------------------------------------------------------------------------
# Test 5: test_internal_vs_external_links
# ---------------------------------------------------------------------------


async def test_internal_vs_external_links() -> None:
    """Links from same domain → internal_links; other domains → external_links."""
    job_id = _make_job_id()
    job_dir = _write_state(job_id)
    url = f"{BASE_URL}/links"
    h = _url_hash(url)

    _write_raw_html(job_dir, h, _LINKS_HTML)
    _write_fetch_results(
        job_dir,
        [{"url": url, "url_hash": h, "status": "success", "http_code": 200, "file": f"raw/{h}.html", "error": None}],
    )

    from app.pipeline.extractor import run_extractor

    result = await run_extractor(job_id)
    assert result is True

    data = _load_page_data(job_id, h)
    internal = data["internal_links"]
    external = data["external_links"]

    # /about (relative) → must be resolved to https://example.com/about → internal
    assert any("example.com/about" in lnk for lnk in internal), (
        f"Relative /about not found in internal_links: {internal}"
    )
    # https://example.com/contact → internal
    assert any("example.com/contact" in lnk for lnk in internal), (
        f"https://example.com/contact not found in internal_links: {internal}"
    )
    # https://external.com/link → external
    assert any("external.com" in lnk for lnk in external), (
        f"external.com not found in external_links: {external}"
    )
    # https://another.org/page → external
    assert any("another.org" in lnk for lnk in external), (
        f"another.org not found in external_links: {external}"
    )
    # mailto: and javascript: → must NOT appear in either list
    all_links = internal + external
    assert not any("mailto:" in lnk for lnk in all_links), (
        "mailto: links must be excluded"
    )
    assert not any("javascript:" in lnk for lnk in all_links), (
        "javascript: links must be excluded"
    )


# ---------------------------------------------------------------------------
# Test 6: test_images_resolved_to_absolute
# ---------------------------------------------------------------------------


async def test_images_resolved_to_absolute() -> None:
    """Relative image src values must be resolved to absolute URLs."""
    job_id = _make_job_id()
    job_dir = _write_state(job_id)
    url = f"{BASE_URL}/images-page"
    h = _url_hash(url)

    _write_raw_html(job_dir, h, _IMAGES_HTML)
    _write_fetch_results(
        job_dir,
        [{"url": url, "url_hash": h, "status": "success", "http_code": 200, "file": f"raw/{h}.html", "error": None}],
    )

    from app.pipeline.extractor import run_extractor

    result = await run_extractor(job_id)
    assert result is True

    data = _load_page_data(job_id, h)
    images = data["images"]

    # Must not be empty (empty src is skipped)
    assert len(images) >= 1

    srcs = [img["src"] for img in images]

    # /logo.png → https://example.com/logo.png
    assert any(s == "https://example.com/logo.png" for s in srcs), (
        f"Expected https://example.com/logo.png in srcs: {srcs}"
    )
    # images/hero.jpg (relative) → https://example.com/images/hero.jpg
    assert any("example.com" in s and "hero.jpg" in s for s in srcs), (
        f"Expected resolved hero.jpg URL in srcs: {srcs}"
    )
    # All srcs must start with http(s)://
    for src in srcs:
        assert src.startswith("http"), f"Non-absolute src found: {src}"

    # Width / height parsing
    logo = next((img for img in images if "logo.png" in img["src"]), None)
    assert logo is not None
    assert logo["width"] == 200
    assert logo["height"] == 100


# ---------------------------------------------------------------------------
# Test 7: test_schema_org_parsed
# ---------------------------------------------------------------------------


async def test_schema_org_parsed() -> None:
    """JSON-LD <script> blocks are parsed and merged into a list of dicts."""
    job_id = _make_job_id()
    job_dir = _write_state(job_id)
    url = f"{BASE_URL}/schema"
    h = _url_hash(url)

    _write_raw_html(job_dir, h, _SCHEMA_HTML)
    _write_fetch_results(
        job_dir,
        [{"url": url, "url_hash": h, "status": "success", "http_code": 200, "file": f"raw/{h}.html", "error": None}],
    )

    from app.pipeline.extractor import run_extractor

    result = await run_extractor(job_id)
    assert result is True

    data = _load_page_data(job_id, h)
    schemas = data["schema_org"]

    # 1 object + 2-element array = 3 valid entries (invalid JSON ignored)
    assert len(schemas) == 3, (
        f"Expected 3 schema entries (1 object + 2 from array), got {len(schemas)}"
    )
    types = [s.get("@type") for s in schemas]
    assert "Organization" in types
    assert "Product" in types
    assert "Service" in types


# ---------------------------------------------------------------------------
# Test 8: test_raw_html_deleted_immediately
# ---------------------------------------------------------------------------


async def test_raw_html_deleted_immediately() -> None:
    """raw/<hash>.html must be deleted right after pages/<hash>.json is written."""
    job_id = _make_job_id()
    job_dir = _write_state(job_id)
    url = f"{BASE_URL}/delete-test"
    h = _url_hash(url)

    _write_raw_html(job_dir, h, _FULL_HTML)
    _write_fetch_results(
        job_dir,
        [{"url": url, "url_hash": h, "status": "success", "http_code": 200, "file": f"raw/{h}.html", "error": None}],
    )

    raw_html_path = job_dir / "raw" / f"{h}.html"
    assert raw_html_path.exists(), "Setup: raw HTML must exist before extraction"

    from app.pipeline.extractor import run_extractor

    result = await run_extractor(job_id)
    assert result is True

    # Raw HTML must have been deleted
    assert not raw_html_path.exists(), (
        f"raw/{h}.html must be deleted after extraction, but still exists"
    )

    # pages/<hash>.json must exist
    page_json = job_dir / "pages" / f"{h}.json"
    assert page_json.exists(), "pages/<hash>.json must exist after extraction"


# ---------------------------------------------------------------------------
# Test 9: test_raw_dir_removed_when_empty
# ---------------------------------------------------------------------------


async def test_raw_dir_removed_when_empty() -> None:
    """raw/ directory must be removed after all pages are processed (if empty)."""
    job_id = _make_job_id()
    job_dir = _write_state(job_id)
    urls = [f"{BASE_URL}/p{i}" for i in range(3)]
    fetch_results_entries = []

    for url in urls:
        h = _url_hash(url)
        _write_raw_html(job_dir, h, _FULL_HTML)
        fetch_results_entries.append(
            {"url": url, "url_hash": h, "status": "success", "http_code": 200, "file": f"raw/{h}.html", "error": None}
        )

    _write_fetch_results(job_dir, fetch_results_entries)

    raw_dir = job_dir / "raw"
    assert raw_dir.exists(), "Setup: raw/ must exist before extraction"

    from app.pipeline.extractor import run_extractor

    result = await run_extractor(job_id)
    assert result is True

    assert not raw_dir.exists(), (
        "raw/ directory must be removed after all HTML files are extracted"
    )


# ---------------------------------------------------------------------------
# Test 10: test_extraction_empty_fails_job
# ---------------------------------------------------------------------------


async def test_extraction_empty_fails_job() -> None:
    """All pages with word_count < 50 → EXTRACTION_EMPTY, job marked failed."""
    job_id = _make_job_id()
    job_dir = _write_state(job_id)

    # Minimal HTML — readability will produce very few words
    tiny_html = "<html><head><title>T</title></head><body><p>Hi</p></body></html>"

    urls = [f"{BASE_URL}/empty{i}" for i in range(3)]
    fetch_results_entries = []
    for url in urls:
        h = _url_hash(url)
        _write_raw_html(job_dir, h, tiny_html)
        fetch_results_entries.append(
            {"url": url, "url_hash": h, "status": "success", "http_code": 200, "file": f"raw/{h}.html", "error": None}
        )

    _write_fetch_results(job_dir, fetch_results_entries)

    from app.pipeline.extractor import run_extractor

    result = await run_extractor(job_id)
    assert result is False, "run_extractor must return False on EXTRACTION_EMPTY"

    state = _load_state(job_id)
    assert state["status"] == "failed", (
        f"Job status must be 'failed', got '{state['status']}'"
    )
    assert state["error"] is not None
    assert state["error"]["code"] == "EXTRACTION_EMPTY", (
        f"Expected error code EXTRACTION_EMPTY, got {state['error']['code']}"
    )


# ---------------------------------------------------------------------------
# Test 11: test_partial_failure_continues
# ---------------------------------------------------------------------------


async def test_partial_failure_continues() -> None:
    """Error extracting one page does not abort the rest of the pages."""
    job_id = _make_job_id()
    job_dir = _write_state(job_id)

    url_ok = f"{BASE_URL}/good-page"
    url_bad = f"{BASE_URL}/bad-page"
    h_ok = _url_hash(url_ok)
    h_bad = _url_hash(url_bad)

    # Write good HTML for url_ok
    _write_raw_html(job_dir, h_ok, _FULL_HTML)
    # Do NOT write the raw HTML for url_bad → aiofiles.open will raise FileNotFoundError
    # (simulate a corrupted/missing raw file)
    raw_dir = job_dir / "raw"
    raw_dir.mkdir(exist_ok=True)  # dir must exist but file is missing

    _write_fetch_results(
        job_dir,
        [
            {"url": url_ok, "url_hash": h_ok, "status": "success", "http_code": 200, "file": f"raw/{h_ok}.html", "error": None},
            {"url": url_bad, "url_hash": h_bad, "status": "success", "http_code": 200, "file": f"raw/{h_bad}.html", "error": None},
        ],
    )

    from app.pipeline.extractor import run_extractor

    result = await run_extractor(job_id)
    # Extractor should still return True (good page succeeded; bad page failed but job not failed)
    assert result is True

    extract_results = _load_extract_results(job_id)
    assert extract_results["successful"] >= 1, "At least 1 page must succeed"
    assert extract_results["failed"] >= 1, "At least 1 page must be marked failed"

    # pages/<h_ok>.json must exist
    assert (job_dir / "pages" / f"{h_ok}.json").exists(), (
        "pages/<h_ok>.json must exist after partial extraction"
    )
    # pages/<h_bad>.json must NOT exist
    assert not (job_dir / "pages" / f"{h_bad}.json").exists(), (
        "pages/<h_bad>.json must not exist when extraction failed"
    )

    # Check result entries
    results_map = {r["url"]: r for r in extract_results["results"]}
    assert results_map[url_ok]["status"] == "success"
    assert results_map[url_bad]["status"] == "failed"
    assert results_map[url_bad]["word_count"] is None
    assert results_map[url_bad]["file"] is None


# ---------------------------------------------------------------------------
# Test 12: test_extract_results_json_format
# ---------------------------------------------------------------------------


async def test_extract_results_json_format() -> None:
    """extract_results.json must have the correct schema with all required fields."""
    job_id = _make_job_id()
    job_dir = _write_state(job_id)
    url = f"{BASE_URL}/format-check"
    h = _url_hash(url)

    _write_raw_html(job_dir, h, _FULL_HTML)
    _write_fetch_results(
        job_dir,
        [{"url": url, "url_hash": h, "status": "success", "http_code": 200, "file": f"raw/{h}.html", "error": None}],
    )

    from app.pipeline.extractor import run_extractor

    result = await run_extractor(job_id)
    assert result is True

    data = _load_extract_results(job_id)

    # Top-level required fields
    required_top = {"job_id", "total_pages", "successful", "failed", "empty_pages", "results"}
    assert required_top <= data.keys(), (
        f"Missing top-level fields: {required_top - data.keys()}"
    )
    assert data["job_id"] == job_id
    assert data["total_pages"] == 1
    assert data["successful"] == 1
    assert data["failed"] == 0
    assert isinstance(data["empty_pages"], int)
    assert isinstance(data["results"], list)
    assert len(data["results"]) == 1

    # Per-result required fields
    required_entry = {"url", "url_hash", "status", "word_count", "file"}
    entry = data["results"][0]
    assert required_entry <= entry.keys(), (
        f"Missing result entry fields: {required_entry - entry.keys()}"
    )
    assert entry["url"] == url
    assert entry["url_hash"] == h
    assert entry["status"] == "success"
    assert isinstance(entry["word_count"], int)
    assert entry["file"] == f"pages/{h}.json"
