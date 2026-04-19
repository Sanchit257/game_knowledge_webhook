"""GitHub webhook receiver for MarvelLore CI."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import threading
from datetime import datetime, timezone
from typing import Any

from flask import Blueprint, Response, jsonify, request

webhook_bp = Blueprint("webhook", __name__, url_prefix="/webhook")

logger = logging.getLogger(__name__)

_audits_run: int = 0
_last_audit_iso: str | None = None


def _utc_now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string."""

    return datetime.now(timezone.utc).isoformat()


def _verify_signature(payload: bytes, signature_header: str | None, secret: str) -> bool:
    """Verify GitHub HMAC-SHA256 signature header."""

    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    actual = signature_header.split("=", 1)[1].strip()
    return hmac.compare_digest(expected, actual)


def _handle_event(event: str, payload: dict[str, Any]) -> None:
    """Background processing for a GitHub webhook event."""

    global _audits_run, _last_audit_iso

    from app.auditor import format_pr_comment, run_audit
    from app.github_client import GitHubClient
    from app.parser import load_knowledge_base

    # Prefer env var; request context is not available here reliably, so read env.
    import os

    token = os.getenv("GITHUB_TOKEN", "").strip()
    repo_name = os.getenv("GITHUB_REPO", "").strip()
    if not token or not repo_name:
        logger.warning("Missing GITHUB_TOKEN/GITHUB_REPO; skipping audit.")
        return

    gh = GitHubClient(token=token, repo_name=repo_name)
    knowledge = load_knowledge_base()

    if event == "pull_request":
        action = str(payload.get("action") or "")
        if action not in {"opened", "synchronize"}:
            return
        pr = payload.get("pull_request") or {}
        pr_number = int(pr.get("number") or 0)
        commit_sha = str(((pr.get("head") or {}).get("sha")) or "unknown")
        changed_files = gh.get_pr_files(pr_number)

        result = run_audit(
            changed_files=changed_files,
            knowledge_base=knowledge,
            repo=repo_name,
            pr_number=pr_number,
            commit_sha=commit_sha,
        )
        body = format_pr_comment(result=result, pr_number=pr_number, commit_sha=commit_sha)
        gh.post_pr_comment(pr_number=pr_number, body=body)

        _audits_run += 1
        _last_audit_iso = _utc_now_iso()
        return

    if event == "push":
        ref = str(payload.get("ref") or "")
        if ref not in {"refs/heads/main", "refs/heads/master"}:
            return
        commits = payload.get("commits") or []
        if not isinstance(commits, list):
            return
        changed_files = gh.get_push_files(commits=commits)
        commit_sha = str(payload.get("after") or "unknown")
        result = run_audit(
            changed_files=changed_files,
            knowledge_base=knowledge,
            repo=repo_name,
            pr_number=0,
            commit_sha=commit_sha,
        )
        logger.info("Push audit status=%s issues=%s", result.status, len(result.issues))
        _audits_run += 1
        _last_audit_iso = _utc_now_iso()
        return

    return

@webhook_bp.get("/status")
def status() -> Response:
    """Return health status for the dashboard."""

    from app.database import get_system_state

    tunnel_url = get_system_state("tunnel_url") or ""
    return jsonify(
        {
            "status": "online",
            "tunnel_url": tunnel_url,
            "audits_run": _audits_run,
            "last_audit": _last_audit_iso,
        }
    )


@webhook_bp.post("/github")
def github_webhook() -> Response:
    """
    Receive GitHub webhooks and kick off audits asynchronously.

    Never returns 500; errors are logged and a 200 is returned unless signature fails.
    """

    import os

    secret = os.getenv("GITHUB_WEBHOOK_SECRET", "").encode("utf-8").decode("utf-8")
    payload_bytes = request.get_data(cache=False) or b""
    signature = request.headers.get("X-Hub-Signature-256")
    if not secret or not _verify_signature(payload_bytes, signature, secret):
        return Response(status=401)

    try:
        payload = request.get_json(force=True, silent=True) or {}
    except Exception:
        payload = {}

    event = request.headers.get("X-GitHub-Event", "")

    try:
        t = threading.Thread(target=_handle_event, args=(event, payload), daemon=True)
        t.start()
    except Exception as exc:
        logger.exception("Failed to start background audit thread: %s", exc)

    return Response(status=200)
