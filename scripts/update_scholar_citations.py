#!/usr/bin/env python3

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT_DIR = Path(__file__).resolve().parents[1]
INDEX_FILE = ROOT_DIR / "index.html"
SCHOLAR_URL = "https://scholar.google.com/citations?user={user_id}&hl=en"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/123.0.0.0 Safari/537.36"
)
COMMON_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,"
        "image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Referer": "https://scholar.google.com/",
    "Upgrade-Insecure-Requests": "1",
}
BLOCK_MARKERS = (
    "unusual traffic",
    "not a robot",
    "detected unusual traffic",
    "please show you're not a robot",
)

PROFILE_USER_RE = re.compile(r'data-scholar-user-id="([A-Za-z0-9_-]+)"')
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
SCHOLAR_CITATIONS_FALLBACK_RE = re.compile(
    r'<td[^>]*>\s*Citations\s*</td>\s*<td[^>]*>\s*([\d,]+)\s*</td>',
    re.I | re.S,
)
SCHOLAR_CITATIONS_META_RE = re.compile(
    r'(?:name|property)="(?:description|og:description)"[^>]*content="[^"]*Cited by ([\d,]+)',
    re.I | re.S,
)


class ScholarFetchError(RuntimeError):
    pass


class ScholarBlockedError(ScholarFetchError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Update Google Scholar citations in index.html.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and print the latest citation count without modifying index.html.",
    )
    parser.add_argument(
        "--user-id",
        help="Override the Scholar user id instead of reading it from index.html.",
    )
    parser.add_argument(
        "--dump-html",
        help="Write the fetched Scholar HTML to this path for debugging.",
    )
    return parser.parse_args()


def extract_user_id(html: str) -> str:
    match = PROFILE_USER_RE.search(html)
    if not match:
        raise RuntimeError("Could not find data-scholar-user-id in index.html")
    return match.group(1)


def build_profile_url(user_id: str) -> str:
    return SCHOLAR_URL.format(user_id=user_id)


def detect_block_page(profile_html: str) -> None:
    lowered = profile_html.lower()
    for marker in BLOCK_MARKERS:
        if marker in lowered:
            raise ScholarBlockedError(f"Google Scholar returned an anti-bot page: {marker}")


def fetch_with_urllib(url: str) -> str:
    request = Request(url, headers=COMMON_HEADERS)
    with urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", errors="replace")


def fetch_with_curl(url: str) -> str:
    command = [
        "curl",
        "--http1.1",
        "--location",
        "--silent",
        "--show-error",
        "--fail",
        "--compressed",
        "--user-agent",
        USER_AGENT,
        "--header",
        f"Accept: {COMMON_HEADERS['Accept']}",
        "--header",
        f"Accept-Language: {COMMON_HEADERS['Accept-Language']}",
        "--header",
        f"Cache-Control: {COMMON_HEADERS['Cache-Control']}",
        "--header",
        f"Pragma: {COMMON_HEADERS['Pragma']}",
        "--header",
        f"Referer: {COMMON_HEADERS['Referer']}",
        "--header",
        f"Upgrade-Insecure-Requests: {COMMON_HEADERS['Upgrade-Insecure-Requests']}",
        url,
    ]
    result = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def fetch_profile_html(user_id: str) -> tuple[str, str]:
    url = build_profile_url(user_id)
    attempts = (
        ("urllib", fetch_with_urllib),
        ("curl", fetch_with_curl),
    )
    failures = []

    for method, fetcher in attempts:
        try:
            profile_html = fetcher(url)
            detect_block_page(profile_html)
            return method, profile_html
        except HTTPError as exc:
            failures.append(f"{method}: HTTP Error {exc.code}: {exc.reason}")
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr.strip() or exc.stdout.strip() or str(exc)
            failures.append(f"{method}: {stderr}")
        except (URLError, TimeoutError, ScholarFetchError) as exc:
            failures.append(f"{method}: {exc}")

    raise ScholarFetchError(" | ".join(failures))


def extract_citation_count(profile_html: str) -> int:
    for pattern in (
        SCHOLAR_CITATIONS_RE,
        SCHOLAR_CITATIONS_FALLBACK_RE,
        SCHOLAR_CITATIONS_META_RE,
    ):
        match = pattern.search(profile_html)
        if match:
            return int(match.group(1).replace(",", ""))

    raise RuntimeError("Could not parse citation count from Google Scholar profile")


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
    args = parse_args()
    index_html = INDEX_FILE.read_text(encoding="utf-8")
    user_id = args.user_id or extract_user_id(index_html)

    try:
        method, profile_html = fetch_profile_html(user_id)
        if args.dump_html:
            Path(args.dump_html).write_text(profile_html, encoding="utf-8")
        citation_count = extract_citation_count(profile_html)
    except (HTTPError, URLError, TimeoutError, RuntimeError) as exc:
        print(f"Failed to update Google Scholar citations: {exc}", file=sys.stderr)
        return 1

    if args.dry_run:
        print(f"Latest Google Scholar citations via {method}: {citation_count:,}")
        return 0

    updated_html = update_index_html(index_html, citation_count)
    if updated_html != index_html:
        INDEX_FILE.write_text(updated_html, encoding="utf-8")
        print(f"Updated Google Scholar citations to {citation_count:,} via {method}")
    else:
        print(f"Google Scholar citations already up to date: {citation_count:,} via {method}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
