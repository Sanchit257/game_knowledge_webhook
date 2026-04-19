"""Download Marvel RPG PDFs from Marvel CDN into ``data/pdfs/``."""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

from config import MARVEL_PDF_URLS, PDF_DIR

logger = logging.getLogger(__name__)

_BROWSER_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def _ts() -> str:
    """Return an ISO-like UTC timestamp for console logs."""

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def download_pdfs(force: bool = False) -> dict[str, Path]:
    """
    Download each PDF in ``MARVEL_PDF_URLS`` to ``data/pdfs/{key}.pdf``.

    Skips existing files unless ``force`` is True. Logs progress with
    timestamps. Continues after individual failures; returns only successful
    ``{key: path}`` pairs.
    """

    PDF_DIR.mkdir(parents=True, exist_ok=True)
    results: dict[str, Path] = {}
    session = requests.Session()
    session.headers.update(_BROWSER_HEADERS)

    for key, url in MARVEL_PDF_URLS.items():
        dest = PDF_DIR / f"{key}.pdf"
        if dest.exists() and not force:
            print(f"[{_ts()}] SKIP  {key} -> {dest} (already exists)")
            results[key] = dest
            continue
        print(f"[{_ts()}] START {key} <- {url}")
        try:
            response = session.get(url, timeout=120)
            response.raise_for_status()
        except requests.RequestException as exc:
            print(f"[{_ts()}] ERROR {key}: {exc}", file=sys.stderr)
            logger.exception("Download failed for %s", key)
            continue
        try:
            dest.write_bytes(response.content)
        except OSError as exc:
            print(f"[{_ts()}] ERROR {key} writing file: {exc}", file=sys.stderr)
            logger.exception("Write failed for %s", key)
            continue
        print(f"[{_ts()}] OK    {key} -> {dest} ({len(response.content)} bytes)")
        results[key] = dest

    return results


def run_scrape(force: bool = False) -> None:
    """
    Download configured PDFs and build the knowledge graph from them.

    Called once at startup (unless skipped) or via ``main --scrape-only``.
    """

    download_pdfs(force=force)
    from app.parser import build_knowledge_graph

    build_knowledge_graph()
