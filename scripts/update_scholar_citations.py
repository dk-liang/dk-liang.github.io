#!/usr/bin/env python3

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT_DIR = Path(__file__).resolve().parents[1]
INDEX_FILE = ROOT_DIR / "index.html"
SCHOLAR_URL = "https://scholar.google.com/citations?user={user_id}&hl=en"

PROFILE_USER_RE = re.compile(
    r'data-scholar-user-id="([A-Za-z0-9_-]+)"'
)
SCHOLAR_COUNT_ATTR_RE = re.compile(
    r'(<a class="hero-link primary metric-link"[^>]*data-scholar-user-id="[A-Za-z0-9_-]+"[^>]*data-citation-count=")([\d,]+)(")',
    re.S,
)
SCHOLAR_COUNT_TEXT_RE = re.compile(
    r'(<span class="hero-link-count" id="scholar-citation-count">)([^<]+)(</span>)'
)
SCHOLAR_CITATIONS_RE = re.compile(
    r'<td[^>]*class="gsc_rsb_sth"[^>]*>\s*Citations\s*</td>\s*<td[^>]*class="gsc_rsb_std"[^>]*>\s*([\d,]+)\s*</td>',
    re.I | re.S,
)


def extract_user_id(html: str) -> str:
    match = PROFILE_USER_RE.search(html)
    if not match:
        raise RuntimeError("Could not find data-scholar-user-id in index.html")
    return match.group(1)


def fetch_profile_html(user_id: str) -> str:
    url = SCHOLAR_URL.format(user_id=user_id)
    request = Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/123.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "no-cache",
        },
    )
    with urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", errors="replace")


def extract_citation_count(profile_html: str) -> int:
    match = SCHOLAR_CITATIONS_RE.search(profile_html)
    if not match:
        raise RuntimeError("Could not parse citation count from Google Scholar profile")
    return int(match.group(1).replace(",", ""))


def update_index_html(index_html: str, citation_count: int) -> str:
    formatted = f"{citation_count:,}"

    updated_html, attr_count = SCHOLAR_COUNT_ATTR_RE.subn(
        rf"\g<1>{citation_count}\g<3>",
        index_html,
        count=1,
    )
    if attr_count != 1:
        raise RuntimeError("Could not update data-citation-count in index.html")

    updated_html, text_count = SCHOLAR_COUNT_TEXT_RE.subn(
        rf"\g<1>{formatted}\g<3>",
        updated_html,
        count=1,
    )
    if text_count != 1:
        raise RuntimeError("Could not update scholar-citation-count text in index.html")

    return updated_html


def main() -> int:
    index_html = INDEX_FILE.read_text(encoding="utf-8")
    user_id = extract_user_id(index_html)

    try:
        profile_html = fetch_profile_html(user_id)
        citation_count = extract_citation_count(profile_html)
    except (HTTPError, URLError, TimeoutError, RuntimeError) as exc:
        print(f"Failed to update Google Scholar citations: {exc}", file=sys.stderr)
        return 1

    updated_html = update_index_html(index_html, citation_count)
    if updated_html != index_html:
        INDEX_FILE.write_text(updated_html, encoding="utf-8")
        print(f"Updated Google Scholar citations to {citation_count:,}")
    else:
        print(f"Google Scholar citations already up to date: {citation_count:,}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())