"""Application configuration and environment-backed settings for MarvelLore CI."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR: Path = Path(__file__).resolve().parent
DATA_DIR: Path = BASE_DIR / "data"
PDF_DIR: Path = DATA_DIR / "pdfs"
KNOWLEDGE_DIR: Path = DATA_DIR / "knowledge"

load_dotenv(BASE_DIR / ".env")

MARVEL_PDF_URLS: dict[str, str] = {
    "character_profiles": (
        "https://cdn.marvel.com/u/prod/marvel/i/pdf/"
        "MMRPG_CharacterProfiles_20240717.pdf"
    ),
    "character_sheets": (
        "https://cdn.marvel.com/u/prod/marvel/i/pdf/"
        "MMRPG_All_Character_Sheets_20240807.pdf"
    ),
    "quick_start": (
        "https://cdn.marvel.com/u/prod/marvel/i/pdf/"
        "MMRPG_Quick-Start_With_Thunderbolts_Adventure_20250718.pdf"
    ),
    "errata": (
        "https://cdn.marvel.com/u/prod/marvel/i/pdf/MMRPG_Errata_20250718.pdf"
    ),
}

SCRAPE_INTERVAL_DAYS: int = 10

HUMAN_DELTA_API_KEY: str | None = os.getenv("HUMAN_DELTA_API_KEY")
GITHUB_TOKEN: str | None = os.getenv("GITHUB_TOKEN")
GITHUB_REPO: str | None = os.getenv("GITHUB_REPO")
GITHUB_WEBHOOK_SECRET: str | None = os.getenv("GITHUB_WEBHOOK_SECRET")
NGROK_AUTHTOKEN: str | None = os.getenv("NGROK_AUTHTOKEN")
FLASK_SECRET_KEY: str = os.getenv("FLASK_SECRET_KEY", "dev-change-me")


def get_flask_port() -> int:
    """Return the TCP port for the Flask dev server from FLASK_PORT or 5000."""

    raw = os.getenv("FLASK_PORT", "5000")
    return int(raw)


def get_ngrok_authtoken() -> str | None:
    """Return the ngrok authtoken from the environment, if set."""

    return NGROK_AUTHTOKEN
