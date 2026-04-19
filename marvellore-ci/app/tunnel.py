"""Expose the local Flask server via an ngrok HTTPS tunnel (pyngrok)."""

from __future__ import annotations

from pyngrok import conf, ngrok


def start_tunnel(port: int) -> str:
    """
    Start an HTTPS ngrok tunnel to ``localhost:port`` and return the public URL.

    Also persists the tunnel URL for dashboard health display.
    """

    from config import get_ngrok_authtoken
    from app.database import set_system_state

    token = get_ngrok_authtoken()
    if token:
        conf.get_default().auth_token = token
    tunnel = ngrok.connect(port, "http")
    url = str(tunnel.public_url)
    print(f"🚀 MarvelLore CI is live at: {url}")
    print(f"📡 GitHub Webhook URL: {url}/webhook/github")
    set_system_state("tunnel_url", url)
    return url


def start_public_tunnel(port: int) -> str:
    """Backward-compatible alias for :func:`start_tunnel`."""

    return start_tunnel(port)
