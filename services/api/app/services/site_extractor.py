"""Website crawler for extracting business info.

Fetches the homepage, follows links to "about-like" subpages (about, services,
pricing, contact, faq), cleans HTML, and returns combined text ready for
Claude extraction.

Conservative crawl: max 6 pages total, 1 hop deep, 5-second timeout per page,
respects robots.txt is OUT OF SCOPE for beta (we're explicitly asked to crawl
a single specific URL).
"""
from __future__ import annotations

import asyncio
import logging
import re
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup


logger = logging.getLogger(__name__)


# Keywords in URLs that signal "this page has business info"
RELEVANT_PATH_KEYWORDS = {
    "about", "services", "service", "pricing", "price", "contact",
    "faq", "faqs", "team", "what-we-do", "our-work", "solutions",
    "products", "menu", "offerings", "consulting", "approach",
}

# Path patterns to skip (legal pages, careers, blog posts, etc.)
SKIP_PATTERNS = [
    "/privacy", "/terms", "/legal", "/cookies", "/sitemap",
    "/blog/", "/posts/", "/articles/", "/news/",
    "/careers", "/jobs", "/login", "/signin", "/signup",
    "/cart", "/checkout", "/account", ".pdf", ".jpg",
    ".png", ".css", ".js", ".xml", ".ico",
]

MAX_PAGES = 6
MAX_TOTAL_CHARS = 30_000
PER_REQUEST_TIMEOUT = 5.0
USER_AGENT = "Mozilla/5.0 (compatible; ReachAI-Onboarding-Crawler/1.0; +https://reachai.com)"


class ExtractError(Exception):
    pass


def _normalize_url(base: str, link: str) -> str | None:
    """Resolve a relative link against base, drop fragments and querystrings."""
    try:
        abs_url = urljoin(base, link)
        parsed = urlparse(abs_url)
        if parsed.scheme not in ("http", "https"):
            return None
        # Drop fragment and query for canonical form
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")
    except Exception:
        return None


def _is_relevant_url(url: str, base_netloc: str) -> bool:
    """True if this URL is on the same domain AND looks like a business-info page."""
    parsed = urlparse(url)
    if parsed.netloc != base_netloc:
        return False

    path = parsed.path.lower()
    if any(skip in path for skip in SKIP_PATTERNS):
        return False

    if not path or path == "/":
        return False  # Homepage already crawled

    # Split path into segments and check for relevance keywords
    segments = [s for s in path.split("/") if s]
    if not segments:
        return False

    for segment in segments:
        # Strip extensions
        segment_clean = re.sub(r"\.\w+$", "", segment)
        for keyword in RELEVANT_PATH_KEYWORDS:
            if keyword in segment_clean:
                return True

    return False


def _clean_html_to_text(html: str) -> str:
    """Strip scripts/styles/nav/footer; return readable plain text."""
    soup = BeautifulSoup(html, "lxml")

    # Remove non-content tags
    for tag in soup.find_all(["script", "style", "noscript", "iframe", "svg"]):
        tag.decompose()

    # Remove common navigation/footer regions by tag and common class names
    for tag in soup.find_all(["nav", "footer", "header"]):
        tag.decompose()
    for tag in soup.find_all(attrs={"class": re.compile(r"\b(nav|navbar|menu|footer|cookie|popup|modal)\b", re.I)}):
        tag.decompose()

    # Get text with sensible whitespace
    text = soup.get_text(separator=" ", strip=True)
    # Collapse multiple whitespace chars
    text = re.sub(r"\s+", " ", text).strip()
    return text


async def _fetch_one(client: httpx.AsyncClient, url: str) -> tuple[str, str]:
    """Fetch one URL. Returns (final_url, text_content) or raises ExtractError."""
    try:
        resp = await client.get(url, follow_redirects=True, timeout=PER_REQUEST_TIMEOUT)
    except httpx.HTTPError as e:
        raise ExtractError(f"Network error for {url}: {e}")

    if resp.status_code != 200:
        raise ExtractError(f"{url} returned status {resp.status_code}")

    content_type = resp.headers.get("content-type", "")
    if "html" not in content_type.lower():
        raise ExtractError(f"{url} returned non-HTML content-type: {content_type}")

    final_url = str(resp.url)
    text = _clean_html_to_text(resp.text)
    return final_url, text


def _find_internal_links(homepage_html: str, base_url: str) -> list[str]:
    """Parse homepage to find relevant internal links."""
    soup = BeautifulSoup(homepage_html, "lxml")
    base_netloc = urlparse(base_url).netloc

    candidates: dict[str, int] = {}  # url -> priority (higher = better)

    for anchor in soup.find_all("a", href=True):
        href = anchor.get("href", "").strip()
        if not href or href.startswith(("javascript:", "mailto:", "tel:", "#")):
            continue

        full_url = _normalize_url(base_url, href)
        if not full_url:
            continue

        if not _is_relevant_url(full_url, base_netloc):
            continue

        # Priority: prefer pages with multiple keywords in path
        path = urlparse(full_url).path.lower()
        priority = sum(1 for kw in RELEVANT_PATH_KEYWORDS if kw in path)
        candidates[full_url] = max(candidates.get(full_url, 0), priority)

    # Sort by priority desc, take top N
    sorted_urls = sorted(candidates.items(), key=lambda x: -x[1])
    return [url for url, _ in sorted_urls[: MAX_PAGES - 1]]


async def crawl_business_site(start_url: str) -> dict:
    """Crawl a business website and return combined extracted text.

    Returns: {
        "pages": [{"url": "...", "text": "..."}, ...],
        "combined_text": "...",       # all pages joined, capped
        "pages_crawled": N,
    }
    """
    if not start_url.startswith(("http://", "https://")):
        start_url = "https://" + start_url

    async with httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT},
        timeout=PER_REQUEST_TIMEOUT,
    ) as client:
        # 1. Fetch homepage
        try:
            home_url, home_text = await _fetch_one(client, start_url)
        except ExtractError as e:
            raise ExtractError(f"Could not load homepage: {e}")

        # 2. Get the raw homepage HTML again to extract links (re-fetch is cheap, cached by httpx)
        try:
            homepage_resp = await client.get(start_url, follow_redirects=True, timeout=PER_REQUEST_TIMEOUT)
            homepage_html = homepage_resp.text
        except httpx.HTTPError:
            homepage_html = ""  # Continue with just homepage text

        pages = [{"url": home_url, "text": home_text}]

        # 3. Find relevant subpages
        if homepage_html:
            subpages = _find_internal_links(homepage_html, home_url)
        else:
            subpages = []

        # 4. Fetch subpages concurrently
        if subpages:
            tasks = [_fetch_one(client, url) for url in subpages]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for url, result in zip(subpages, results):
                if isinstance(result, Exception):
                    logger.warning("Skipping %s: %s", url, result)
                    continue
                final_url, text = result
                if text and len(text) > 100:  # Skip empty/tiny pages
                    pages.append({"url": final_url, "text": text})

        # 5. Combine, capping total length
        combined = "\n\n".join(f"### Page: {p['url']}\n\n{p['text']}" for p in pages)
        if len(combined) > MAX_TOTAL_CHARS:
            combined = combined[:MAX_TOTAL_CHARS] + "\n\n[...truncated]"

        return {
            "pages": [{"url": p["url"], "text_length": len(p["text"])} for p in pages],
            "combined_text": combined,
            "pages_crawled": len(pages),
        }
