"""CLI entry: scrape, ngrok tunnel, webhook receiver, and dashboard."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app import create_app
from app.dashboard import dashboard_bp
from app.database import init_db
from app.scraper import run_scrape
from app.tunnel import start_tunnel
from app.webhook import webhook_bp
from config import BASE_DIR, KNOWLEDGE_DIR, get_flask_port

from dotenv import load_dotenv
from pyngrok import ngrok


def _parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""

    parser = argparse.ArgumentParser(description="MarvelLore CI server")
    parser.add_argument(
        "--scrape-only",
        action="store_true",
        help="Run PDF scrape once and exit.",
    )
    parser.add_argument(
        "--skip-scrape",
        action="store_true",
        help="Do not run scrape on startup.",
    )
    parser.add_argument(
        "--force-scrape",
        action="store_true",
        help="Force scrape even if knowledge_base.json already exists.",
    )
    return parser.parse_args()


def _print_banner() -> None:
    """Print the MarvelLore CI ASCII art banner."""

    print(
        "\n".join(
            [
                "███╗   ███╗ █████╗ ██████╗ ██╗   ██╗███████╗██╗     ██╗      ██████╗ ██████╗ ███████╗",
                "████╗ ████║██╔══██╗██╔══██╗██║   ██║██╔════╝██║     ██║     ██╔═══██╗██╔══██╗██╔════╝",
                "██╔████╔██║███████║██████╔╝██║   ██║█████╗  ██║     ██║     ██║   ██║██████╔╝█████╗  ",
                "██║╚██╔╝██║██╔══██║██╔══██╗╚██╗ ██╔╝██╔══╝  ██║     ██║     ██║   ██║██╔══██╗██╔══╝  ",
                "██║ ╚═╝ ██║██║  ██║██║  ██║ ╚████╔╝ ███████╗███████╗███████╗╚██████╔╝██║  ██║███████╗",
                "╚═╝     ╚═╝╚═╝  ╚═╝╚═╝  ╚═╝  ╚═══╝  ╚══════╝╚══════╝╚══════╝ ╚═════╝ ╚═╝  ╚═╝╚══════╝",
                "                                  MarvelLore CI",
                "",
            ]
        )
    )


def main() -> None:
    """Start MarvelLore CI: scrape, tunnel, Flask app, and webhook endpoints."""

    args = _parse_args()
    _print_banner()

    load_dotenv(BASE_DIR / ".env")
    init_db()

    if args.scrape_only:
        run_scrape()
        sys.exit(0)

    kb_path = KNOWLEDGE_DIR / "knowledge_base.json"
    if kb_path.is_file() and not args.force_scrape:
        print(f"Knowledge base present at {kb_path}; skipping scrape.")
    elif not args.skip_scrape:
        run_scrape()
    port = get_flask_port()
    public_url = start_tunnel(port)

    app = create_app()
    app.register_blueprint(webhook_bp)
    app.register_blueprint(dashboard_bp)

    print(f"Dashboard URL: {public_url}/")
    print(f"Webhook URL:   {public_url}/webhook/github")

    try:
        app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
    except KeyboardInterrupt:
        print("Shutting down...")
    finally:
        try:
            ngrok.disconnect(public_url)
        except Exception:
            pass
        try:
            ngrok.kill()
        except Exception:
            pass


if __name__ == "__main__":
    main()
