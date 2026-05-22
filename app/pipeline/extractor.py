"""
Extractor pipeline subagent — PageData Extraction.

Reads raw HTML files from raw/<url_hash>.html, extracts structured PageData
using BeautifulSoup + readability-lxml, writes pages/<url_hash>.json immediately,
and deletes each raw HTML file right after extraction.

At the end, attempts to remove the raw/ directory (silent if non-empty) and
writes extract_results.json. Returns False if > 50% of successfully extracted
pages have word_count < 50 (EXTRACTION_EMPTY).
"""

import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import aiofiles
from bs4 import BeautifulSoup
from readability import Document

from app.config import settings
from app.core.job_manager import job_manager
from app.models.job import JobStatus

logger = logging.getLogger(__name__)

# Regex for CSS background-image: url(...) extraction
_BG_URL_RE = re.compile(
    r'background(?:-image)?\s*:[^;{]*url\s*\(\s*[\'"]?([^\'"\)\s]+)[\'"]?\s*\)',
    re.IGNORECASE,
)

# Keywords that indicate a CSS class belongs to a hero/banner context
_BG_BANNER_KEYWORDS_RE = re.compile(
    r'hero|banner|cover|header|splash', re.IGNORECASE
)


# ---------------------------------------------------------------------------
# URL hash helper (mirrors fetcher.py for consistency)
# ---------------------------------------------------------------------------


def _url_hash(url: str) -> str:
    """Return sha256 hex digest of the URL string."""
    return hashlib.sha256(url.encode()).hexdigest()


# ---------------------------------------------------------------------------
# PageData extraction (CPU-bound — runs synchronously inside the async loop)
# ---------------------------------------------------------------------------


def _extract_page_data(html_content: str, page_url: str) -> dict:
    """
    Extract all PageData fields from raw HTML using BeautifulSoup and
    readability-lxml.  Returns a dict matching the PageData JSON schema.
    """
    soup = BeautifulSoup(html_content, "lxml")

    # -- title ---------------------------------------------------------------
    title_tag = soup.find("title")
    if title_tag and title_tag.get_text(strip=True):
        title: str = title_tag.get_text(strip=True)
    else:
        h1_tag = soup.find("h1")
        title = h1_tag.get_text(strip=True) if h1_tag else ""

    # -- meta_description ----------------------------------------------------
    meta_desc_tag = soup.find("meta", attrs={"name": "description"})
    meta_description: str = (
        meta_desc_tag.get("content", "") if meta_desc_tag else ""  # type: ignore[union-attr]
    )

    # -- meta_keywords -------------------------------------------------------
    meta_kw_tag = soup.find("meta", attrs={"name": "keywords"})
    meta_keywords: list[str] = (
        [k.strip() for k in meta_kw_tag.get("content", "").split(",")]  # type: ignore[union-attr]
        if meta_kw_tag
        else []
    )
    # Remove empty strings produced by split on empty content
    meta_keywords = [k for k in meta_keywords if k]

    # -- og_data -------------------------------------------------------------
    og_data: dict[str, str] = {
        tag["property"]: tag.get("content", "")
        for tag in soup.find_all("meta", property=True)
        if tag.get("property", "").startswith("og:")
    }

    # -- canonical_url -------------------------------------------------------
    canonical_tag = soup.find("link", rel="canonical")
    canonical_url: str = canonical_tag.get("href", "") if canonical_tag else ""  # type: ignore[union-attr]

    # -- language ------------------------------------------------------------
    html_tag = soup.find("html")
    language: str = html_tag.get("lang", "") if html_tag else ""  # type: ignore[union-attr]

    # -- headings ------------------------------------------------------------
    headings: dict[str, list[str]] = {
        "h1": [h.get_text(strip=True) for h in soup.find_all("h1")],
        "h2": [h.get_text(strip=True) for h in soup.find_all("h2")],
        "h3": [h.get_text(strip=True) for h in soup.find_all("h3")],
    }

    # -- text_content (readability-lxml with BS4 fallback) -------------------
    readability_text = ""
    try:
        doc = Document(html_content)
        readable_html = doc.summary()
        readable_soup = BeautifulSoup(readable_html, "lxml")
        readability_text = readable_soup.get_text(separator=" ", strip=True)
    except Exception:
        pass

    bs4_text = soup.get_text(separator=" ", strip=True)
    # Use whichever source provides more content — readability can strip too
    # aggressively on non-article pages (navigation-heavy or thin content).
    # Home/root pages: always use full BS4 text to preserve hero sections,
    # services, CTAs, and marketing copy that readability strips.
    _is_root_page = urlparse(page_url).path in ("", "/", "/index.html", "/index.php")
    if _is_root_page:
        raw_text = bs4_text
    else:
        raw_text = (
            readability_text
            if len(readability_text.split()) >= len(bs4_text.split())
            else bs4_text
        )
    text_content: str = raw_text[:25_000]

    # -- word_count ----------------------------------------------------------
    word_count: int = len(text_content.split())

    # -- internal_links / external_links -------------------------------------
    base_host: str = urlparse(page_url).netloc.lower().lstrip("www.")
    internal_links_raw: list[str] = []
    external_links_raw: list[str] = []

    for a_tag in soup.find_all("a", href=True):
        href: str = a_tag["href"]
        abs_url: str = urljoin(page_url, href)
        parsed = urlparse(abs_url)
        host: str = parsed.netloc.lower().lstrip("www.")

        if host == base_host:
            internal_links_raw.append(abs_url)
        elif parsed.scheme in ("http", "https"):
            external_links_raw.append(abs_url)

    # Deduplicate while preserving order
    internal_links: list[str] = list(dict.fromkeys(internal_links_raw))
    external_links: list[str] = list(dict.fromkeys(external_links_raw))

    # -- images --------------------------------------------------------------
    _BANNER_CLASSES  = {"hero", "banner", "header", "splash", "cover",
                        "jumbotron", "masthead", "main-image", "featured"}
    _GALLERY_CLASSES = {"gallery", "carousel", "slider", "swiper",
                        "lightbox", "thumb", "thumbnail", "mosaic", "grid"}
    _LOGO_CLASSES    = {"logo", "brand", "wordmark", "site-logo"}
    _og_image: str   = og_data.get("og:image", "")

    def _image_role_hint(img_tag: object, abs_src: str) -> str:  # type: ignore[type-arg]
        if _og_image and abs_src == urljoin(page_url, _og_image):
            return "banner"
        all_classes: set[str] = set()
        # Collect CSS classes from img and its ancestors (up to 4 levels)
        node = img_tag
        for _ in range(5):
            try:
                cls = node.get("class", [])  # type: ignore[union-attr]
                if cls:
                    all_classes.update(c.lower() for c in cls)
                node = node.parent  # type: ignore[union-attr]
                if node is None or node.name in ("html", "body", "[document]"):
                    break
            except AttributeError:
                break
        classes_str = " ".join(all_classes)
        if any(kw in classes_str for kw in _LOGO_CLASSES):
            return "logo"
        if any(kw in classes_str for kw in _BANNER_CLASSES):
            return "banner"
        if any(kw in classes_str for kw in _GALLERY_CLASSES):
            return "gallery_item"
        # Parent element hints
        try:
            parent = img_tag.find_parent(["header", "figure", "aside"])  # type: ignore[union-attr]
            if parent:
                if parent.name == "header":
                    return "banner"
                if parent.name == "figure":
                    return "reference"
        except AttributeError:
            pass
        # Large explicit width → likely banner
        width_attr = img_tag.get("width", "")  # type: ignore[union-attr]
        if str(width_attr).isdigit() and int(width_attr) >= 600:
            return "banner"
        return "reference"

    def _bg_role_hint_from_node(node: object) -> str:
        """
        Determine role hint for a CSS background-image by inspecting
        up to 3 ancestor levels for banner-related class keywords.
        """
        try:
            current = node
            for _ in range(3):
                if current is None:
                    break
                cls_list = current.get("class", [])  # type: ignore[union-attr]
                if cls_list:
                    classes_str = " ".join(c.lower() for c in cls_list)
                    if _BG_BANNER_KEYWORDS_RE.search(classes_str):
                        return "banner"
                current = current.parent  # type: ignore[union-attr]
                if current is None or getattr(current, "name", None) in ("html", "body", "[document]"):
                    break
        except AttributeError:
            pass
        return "reference"

    # Deduplicate by absolute src
    _seen_srcs: set[str] = set()
    images: list[dict] = []

    # -- 1. OG image (prepend at index 0, added last after we build the rest) --
    _og_image_abs: str = urljoin(page_url, _og_image) if _og_image else ""

    # -- 2. <img> tags — covers standard src + common lazy-load attr patterns ---
    for img in soup.find_all("img"):
        # Priority order: src → lazy-load variants (jQuery, WP, Swiper, etc.)
        raw_src: str = (
            img.get("src", "")
            or img.get("data-src", "")
            or img.get("data-lazy-src", "")
            or img.get("data-original", "")       # jQuery lazyload
            or img.get("data-lazy", "")            # WordPress
            or img.get("data-url", "")             # various CMS
            or img.get("data-source", "")          # some frameworks
            or img.get("data-img", "")             # custom lazy patterns
        )
        if not raw_src:
            continue
        if raw_src.startswith("data:"):
            continue
        abs_src: str = urljoin(page_url, raw_src)

        width_str: str = img.get("width", "")
        height_str: str = img.get("height", "")
        w: int | None = int(width_str) if width_str.isdigit() else None
        h: int | None = int(height_str) if height_str.isdigit() else None

        # Skip tracking pixels (explicit dimension ≤ 2px on either axis)
        if (w is not None and w <= 2) or (h is not None and h <= 2):
            continue

        if abs_src not in _seen_srcs:
            _seen_srcs.add(abs_src)
            images.append(
                {
                    "src": abs_src,
                    "alt": img.get("alt", ""),
                    "width": w,
                    "height": h,
                    "role_hint": _image_role_hint(img, abs_src),
                }
            )

        # Parse srcset / data-srcset on the <img> tag itself (no <picture> wrapper)
        srcset_raw: str = (img.get("srcset", "") or img.get("data-srcset", "")).strip()
        if srcset_raw:
            best_url_s: str = ""
            best_desc_s: float = -1.0
            for part in srcset_raw.split(","):
                part = part.strip()
                if not part:
                    continue
                tokens = part.split()
                if not tokens or tokens[0].startswith("data:"):
                    continue
                descriptor: float = 1.0
                if len(tokens) > 1:
                    rd = tokens[1].lower()
                    try:
                        if rd.endswith("x"):
                            descriptor = float(rd[:-1])
                        elif rd.endswith("w"):
                            descriptor = float(rd[:-1])
                    except ValueError:
                        pass
                if descriptor > best_desc_s:
                    best_desc_s = descriptor
                    best_url_s = tokens[0]
            if best_url_s:
                abs_srcset: str = urljoin(page_url, best_url_s)
                if abs_srcset not in _seen_srcs and abs_srcset != abs_src:
                    _seen_srcs.add(abs_srcset)
                    images.append(
                        {
                            "src": abs_srcset,
                            "alt": img.get("alt", ""),
                            "width": None,
                            "height": None,
                            "role_hint": _image_role_hint(img, abs_srcset),
                        }
                    )

    # -- 2b. <noscript> fallbacks — lazy-loaders hide real img inside noscript --
    for noscript_tag in soup.find_all("noscript"):
        try:
            ns_html = noscript_tag.decode_contents()
            if not ns_html.strip():
                continue
            ns_soup = BeautifulSoup(ns_html, "html.parser")
            for ns_img in ns_soup.find_all("img"):
                ns_raw: str = (
                    ns_img.get("src", "")
                    or ns_img.get("data-src", "")
                    or ns_img.get("data-original", "")
                )
                if not ns_raw or ns_raw.startswith("data:"):
                    continue
                abs_ns = urljoin(page_url, ns_raw)
                if abs_ns in _seen_srcs:
                    continue
                _seen_srcs.add(abs_ns)
                images.append(
                    {
                        "src": abs_ns,
                        "alt": ns_img.get("alt", ""),
                        "width": None,
                        "height": None,
                        "role_hint": "reference",
                    }
                )
        except Exception:
            pass

    # -- 3. <picture><source srcset|data-srcset> — pick highest-resolution URL -
    for picture in soup.find_all("picture"):
        for source in picture.find_all("source"):
            srcset_raw: str = (source.get("srcset", "") or source.get("data-srcset", "")).strip()
            if not srcset_raw:
                continue
            # Parse srcset: "url1 1x, url2 2x" or "url1 500w, url2 1000w"
            best_url: str = ""
            best_descriptor: float = -1.0
            for part in srcset_raw.split(","):
                part = part.strip()
                if not part:
                    continue
                tokens = part.split()
                if not tokens:
                    continue
                candidate_url = tokens[0]
                if candidate_url.startswith("data:"):
                    continue
                # Parse descriptor (e.g. "2x" → 2.0, "1000w" → 1000.0)
                descriptor: float = 1.0
                if len(tokens) > 1:
                    raw_desc = tokens[1].lower()
                    try:
                        if raw_desc.endswith("x"):
                            descriptor = float(raw_desc[:-1])
                        elif raw_desc.endswith("w"):
                            descriptor = float(raw_desc[:-1])
                    except ValueError:
                        pass
                if descriptor > best_descriptor:
                    best_descriptor = descriptor
                    best_url = candidate_url

            if not best_url:
                continue
            abs_src = urljoin(page_url, best_url)
            if abs_src in _seen_srcs:
                continue
            _seen_srcs.add(abs_src)
            images.append(
                {
                    "src": abs_src,
                    "alt": "",
                    "width": None,
                    "height": None,
                    "role_hint": _image_role_hint(source, abs_src),
                }
            )

    # -- 4. <link rel="preload" as="image"> -----------------------------------
    for link_tag in soup.find_all("link", rel="preload"):
        if link_tag.get("as", "").lower() != "image":
            continue
        preload_href: str = link_tag.get("href", "").strip()
        if not preload_href or preload_href.startswith("data:"):
            continue
        abs_src = urljoin(page_url, preload_href)
        if abs_src in _seen_srcs:
            continue
        _seen_srcs.add(abs_src)
        images.append(
            {
                "src": abs_src,
                "alt": "",
                "width": None,
                "height": None,
                "role_hint": "reference",
            }
        )

    # -- 5. CSS background-image in inline style attributes -------------------
    for styled_tag in soup.find_all(style=True):
        inline_style: str = styled_tag.get("style", "")
        for match in _BG_URL_RE.finditer(inline_style):
            bg_url: str = match.group(1).strip()
            if not bg_url or bg_url.startswith("data:"):
                continue
            abs_src = urljoin(page_url, bg_url)
            if abs_src in _seen_srcs:
                continue
            _seen_srcs.add(abs_src)
            images.append(
                {
                    "src": abs_src,
                    "alt": "",
                    "width": None,
                    "height": None,
                    "role_hint": _bg_role_hint_from_node(styled_tag),
                }
            )

    # -- 6. CSS background-image in <style> blocks ----------------------------
    for style_tag in soup.find_all("style"):
        style_text: str = style_tag.get_text() if style_tag else ""
        for match in _BG_URL_RE.finditer(style_text):
            bg_url = match.group(1).strip()
            if not bg_url or bg_url.startswith("data:"):
                continue
            abs_src = urljoin(page_url, bg_url)
            if abs_src in _seen_srcs:
                continue
            _seen_srcs.add(abs_src)
            images.append(
                {
                    "src": abs_src,
                    "alt": "",
                    "width": None,
                    "height": None,
                    "role_hint": "reference",
                }
            )

    # -- 6b. data-bg / data-background / data-background-image ---------------
    # Swiper, AOS, Intersection Observer patterns set bg via data attributes
    for _attr in ("data-bg", "data-background", "data-background-image", "data-bkg"):
        for bg_node in soup.find_all(attrs={_attr: True}):
            bg_raw: str = str(bg_node.get(_attr, "")).strip()
            # Some libs store "url(...)" inside the attribute
            _url_match = _BG_URL_RE.search(bg_raw)
            bg_url_clean: str = _url_match.group(1).strip() if _url_match else bg_raw
            if not bg_url_clean or bg_url_clean.startswith("data:"):
                continue
            abs_src = urljoin(page_url, bg_url_clean)
            if abs_src in _seen_srcs:
                continue
            _seen_srcs.add(abs_src)
            images.append(
                {
                    "src": abs_src,
                    "alt": bg_node.get("aria-label", ""),
                    "width": None,
                    "height": None,
                    "role_hint": _bg_role_hint_from_node(bg_node),
                }
            )

    # -- 7. Prepend OG image at index 0 (always banner) -----------------------
    if _og_image_abs and not _og_image_abs.startswith("data:"):
        og_entry: dict = {
            "src": _og_image_abs,
            "alt": og_data.get("og:image:alt", ""),
            "width": None,
            "height": None,
            "role_hint": "banner",
        }
        if _og_image_abs in _seen_srcs:
            # It was already captured — update its role to banner and move to front
            images = [img for img in images if img["src"] != _og_image_abs]
        images.insert(0, og_entry)

    # -- schema_org (JSON-LD) ------------------------------------------------
    schema_org: list[dict] = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            if isinstance(data, list):
                schema_org.extend(data)
            else:
                schema_org.append(data)
        except (json.JSONDecodeError, TypeError):
            pass

    # -- has_forms / has_tables ----------------------------------------------
    has_forms: bool = bool(soup.find("form"))
    has_tables: bool = bool(soup.find("table"))

    # -- extracted_at --------------------------------------------------------
    extracted_at: str = (
        datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    )

    url_hash: str = _url_hash(page_url)

    return {
        "url": page_url,
        "url_hash": url_hash,
        "title": title,
        "meta_description": meta_description,
        "meta_keywords": meta_keywords,
        "og_data": og_data,
        "canonical_url": canonical_url,
        "language": language,
        "headings": headings,
        "text_content": text_content,
        "word_count": word_count,
        "internal_links": internal_links,
        "external_links": external_links,
        "images": images,
        "schema_org": schema_org,
        "has_forms": has_forms,
        "has_tables": has_tables,
        "extracted_at": extracted_at,
    }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


async def run_extractor(job_id: str) -> bool:
    """
    Extracts PageData from raw HTML files and writes pages/<hash>.json.
    Deletes each raw HTML file immediately after extraction.
    Returns True on success, False if job was failed (EXTRACTION_EMPTY).
    Updates job status to 'extracting' at the start.
    """
    job_dir = Path(settings.JOB_BASE_DIR) / job_id
    fetch_results_path = job_dir / "fetch_results.json"

    # --- Read fetch_results.json -------------------------------------------
    try:
        async with aiofiles.open(fetch_results_path, "r", encoding="utf-8") as fh:
            content = await fh.read()
        fetch_data: dict = json.loads(content)
    except Exception as exc:
        logger.error(
            "Extractor: cannot read fetch_results.json for job %s: %s", job_id, exc
        )
        await job_manager.fail_job(
            job_id,
            "EXTRACTION_EMPTY",
            "Could not read fetch_results.json. Fetcher may not have completed.",
        )
        return False

    # --- Mark job as extracting --------------------------------------------
    await job_manager.update_status(job_id, JobStatus.extracting)

    # --- Collect successful pages ------------------------------------------
    successful_pages: list[dict] = [
        r for r in fetch_data.get("results", []) if r.get("status") == "success"
    ]
    total: int = len(successful_pages)

    # --- Prepare directories -----------------------------------------------
    pages_dir = job_dir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = job_dir / "raw"

    # --- Process each page sequentially ------------------------------------
    done: int = 0
    empty_pages: int = 0
    results: list[dict] = []

    for page_entry in successful_pages:
        url: str = page_entry["url"]
        url_hash: str = page_entry["url_hash"]
        raw_html_path: Path = raw_dir / f"{url_hash}.html"

        try:
            # Read HTML from disk
            async with aiofiles.open(raw_html_path, "r", encoding="utf-8") as fh:
                html_content = await fh.read()

            # Extract PageData (CPU-bound — synchronous)
            page_data: dict = _extract_page_data(html_content, url)

            # Write pages/<hash>.json immediately
            page_json_path = pages_dir / f"{url_hash}.json"
            async with aiofiles.open(page_json_path, "w", encoding="utf-8") as fh:
                await fh.write(json.dumps(page_data, ensure_ascii=False, indent=2))

            # Delete raw HTML immediately after writing JSON
            os.remove(raw_html_path)

            word_count: int = page_data["word_count"]
            if word_count < 50:
                empty_pages += 1

            results.append(
                {
                    "url": url,
                    "url_hash": url_hash,
                    "status": "success",
                    "word_count": word_count,
                    "file": f"pages/{url_hash}.json",
                }
            )
            logger.debug(
                "Extractor: extracted %s (word_count=%d)", url, word_count
            )

        except Exception as exc:
            logger.warning(
                "Extractor: failed to extract %s: %s", url, exc
            )
            # Delete raw HTML even on failure
            try:
                os.remove(raw_html_path)
            except OSError:
                pass

            results.append(
                {
                    "url": url,
                    "url_hash": url_hash,
                    "status": "failed",
                    "word_count": None,
                    "file": None,
                }
            )

        done += 1
        await job_manager.update_progress(
            job_id, pages_done=done, pages_total=total
        )

    # --- Attempt to remove raw/ directory (silent if non-empty) -----------
    try:
        raw_dir.rmdir()
    except OSError:
        pass

    # --- EXTRACTION_EMPTY check -------------------------------------------
    total_successful: int = sum(1 for r in results if r["status"] == "success")

    if total_successful > 0 and empty_pages > total_successful * 0.5:
        logger.warning(
            "Extractor: EXTRACTION_EMPTY for job %s "
            "(empty_pages=%d, total_successful=%d)",
            job_id,
            empty_pages,
            total_successful,
        )
        await job_manager.fail_job(
            job_id,
            "EXTRACTION_EMPTY",
            "Extracted content is empty. The site may require JavaScript to render.",
        )
        return False

    # --- Write extract_results.json ----------------------------------------
    total_failed: int = sum(1 for r in results if r["status"] == "failed")
    payload: dict = {
        "job_id": job_id,
        "total_pages": total,
        "successful": total_successful,
        "failed": total_failed,
        "empty_pages": empty_pages,
        "results": results,
    }
    extract_results_path = job_dir / "extract_results.json"
    async with aiofiles.open(extract_results_path, "w", encoding="utf-8") as fh:
        await fh.write(json.dumps(payload, ensure_ascii=False, indent=2))

    logger.info(
        "Extractor: job=%s total=%d successful=%d failed=%d empty=%d",
        job_id,
        total,
        total_successful,
        total_failed,
        empty_pages,
    )
    return True
