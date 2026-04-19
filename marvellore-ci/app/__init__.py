"""Flask application package for MarvelLore CI."""

from __future__ import annotations

import sys
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from config import BASE_DIR, FLASK_SECRET_KEY, SCRAPE_INTERVAL_DAYS  # noqa: E402

from .database import init_db


def _noop_rescrape() -> None:
    """Placeholder job for periodic PDF re-scrape (not started)."""

    pass


def init_scheduler(app: Flask) -> BackgroundScheduler:
    """
    Register the APScheduler job for re-scraping (disabled for hackathon).

    The scheduler is not started; enable by calling ``scheduler.start()`` later.
    """

    scheduler = BackgroundScheduler()
    scheduler.add_job(
        _noop_rescrape,
        "interval",
        days=SCRAPE_INTERVAL_DAYS,
        id="marvellore_rescrape",
    )
    # Disabled for hackathon: do not call scheduler.start()
    app.extensions["scheduler"] = scheduler
    return scheduler


def create_app() -> Flask:
    """Create and configure the Flask application."""

    app = Flask(
        __name__,
        template_folder=str(BASE_DIR / "templates"),
        static_folder=str(BASE_DIR / "static"),
    )
    app.config["SECRET_KEY"] = FLASK_SECRET_KEY
    init_db()
    init_scheduler(app)
    return app
