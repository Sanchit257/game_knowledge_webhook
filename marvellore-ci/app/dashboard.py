"""Dashboard routes for live audit results."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any

from flask import Blueprint, Response, jsonify, render_template, request

from app import database
from app.auditor import AuditIssue, AuditResult, format_pr_comment
from app.parser import load_knowledge_base

dashboard_bp = Blueprint("dashboard", __name__)

DEMO_SCENARIOS: list[dict[str, Any]] = [
    {
        "id": 1,
        "title": "Clean submission",
        "description": "A correct Spider-Man stat block matching official values.",
        "expected": "Status clean ✅",
    },
    {
        "id": 2,
        "title": "Stat mismatch",
        "description": "Spider-Man Agility listed as 4 (official is 7).",
        "expected": "Warnings for stat_mismatch 🟡",
    },
    {
        "id": 3,
        "title": "Faction conflict",
        "description": "Iron Man submitted as X-Men (official is Avengers).",
        "expected": "Warnings for faction_conflict 🟡",
    },
]


def _utc_now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string."""

    return datetime.now(timezone.utc).isoformat()


@dashboard_bp.route("/")
def dashboard_home() -> str:
    """Render the main dashboard listing recent audits."""

    all_audits = database.get_all_audits()
    audits = all_audits[:20]

    total_audits_run = len(all_audits)
    issues_caught = sum(int(a.get("issues_found") or 0) for a in all_audits)

    tunnel_url = database.get_system_state("tunnel_url") or ""

    webhook_log: list[dict[str, Any]] = []
    raw_log = database.get_system_state("webhook_log")
    if raw_log:
        try:
            parsed = json.loads(raw_log)
            if isinstance(parsed, list):
                webhook_log = [x for x in parsed if isinstance(x, dict)][:5]
        except Exception:
            webhook_log = []

    knowledge_summary = {
        "characters": 0,
        "factions": 0,
        "last_scraped": None,
        "faction_members": [],
        "error": None,
    }
    try:
        kb = load_knowledge_base()
        characters = list(kb.get("characters", []) or [])
        factions = list(kb.get("factions", []) or [])
        knowledge_summary = {
            "characters": len(characters),
            "factions": len(factions),
            "last_scraped": kb.get("last_updated"),
            "faction_members": [
                {"name": f.get("name"), "members_count": len(f.get("members", []) or [])}
                for f in factions
                if isinstance(f, dict)
            ],
            "error": None,
        }
    except FileNotFoundError as exc:
        knowledge_summary["error"] = str(exc)

    return render_template(
        "dashboard.html",
        audits=audits,
        total_audits_run=total_audits_run,
        issues_caught=issues_caught,
        tunnel_url=tunnel_url,
        knowledge_summary=knowledge_summary,
        webhook_log=webhook_log,
    )


@dashboard_bp.route("/audit/<int:audit_id>")
def audit_detail(audit_id: int) -> str:
    """Render a single audit detail page."""

    audit = database.get_audit_by_id(audit_id)
    report: dict[str, Any] = {}
    issues: list[dict[str, Any]] = []
    raw_response: dict[str, Any] = {}

    comment_preview = ""
    if audit:
        raw = audit.get("report_json")
        if raw:
            try:
                report = json.loads(raw) if isinstance(raw, str) else dict(raw)
            except Exception:
                report = {}
        result_obj = report.get("result") if isinstance(report, dict) else None
        if isinstance(result_obj, dict):
            raw_response = result_obj.get("raw_response") or {}
            issues = list(result_obj.get("issues") or [])
            try:
                ar = AuditResult(
                    status=str(result_obj.get("status") or "warnings"),  # type: ignore[arg-type]
                    issues=[
                        AuditIssue(
                            severity=str(i.get("severity") or "warning"),  # type: ignore[arg-type]
                            character_name=str(i.get("character_name") or ""),
                            issue_type=str(i.get("issue_type") or ""),
                            description=str(i.get("description") or ""),
                            official_value=str(i.get("official_value") or ""),
                            submitted_value=str(i.get("submitted_value") or ""),
                            suggestion=str(i.get("suggestion") or ""),
                        )
                        for i in issues
                        if isinstance(i, dict)
                    ],
                    characters_checked=list(result_obj.get("characters_checked") or []),
                    duration_seconds=float(result_obj.get("duration_seconds") or 0.0),
                    raw_response=dict(raw_response) if isinstance(raw_response, dict) else {},
                )
                comment_preview = format_pr_comment(
                    result=ar,
                    pr_number=int(audit.get("pr_number") or 0),
                    commit_sha=str(audit.get("commit_sha") or "unknown"),
                )
            except Exception:
                comment_preview = ""

    return render_template(
        "audit_detail.html",
        audit_id=audit_id,
        audit=audit,
        report=report,
        issues=issues,
        comment_preview=comment_preview,
    )


@dashboard_bp.get("/api/audits")
def api_audits() -> Response:
    """Return the last 20 audits as JSON for live polling."""

    audits = database.get_all_audits()[:20]
    slim = [
        {
            "id": a.get("id"),
            "repo": a.get("repo"),
            "pr_number": a.get("pr_number"),
            "commit_sha": a.get("commit_sha"),
            "triggered_at": a.get("triggered_at"),
            "status": a.get("status"),
            "issues_found": a.get("issues_found"),
            "duration_seconds": a.get("duration_seconds"),
        }
        for a in audits
    ]
    return jsonify(slim)


@dashboard_bp.get("/api/knowledge")
def api_knowledge() -> Response:
    """Return knowledge base summary stats as JSON."""

    try:
        kb = load_knowledge_base()
        factions = list(kb.get("factions", []) or [])
        return jsonify(
            {
                "characters": len(kb.get("characters", []) or []),
                "factions": len(factions),
                "last_scraped": kb.get("last_updated"),
                "faction_members": [
                    {"name": f.get("name"), "members_count": len(f.get("members", []) or [])}
                    for f in factions
                    if isinstance(f, dict)
                ],
            }
        )
    except FileNotFoundError as exc:
        return jsonify(
            {
                "characters": 0,
                "factions": 0,
                "last_scraped": None,
                "faction_members": [],
                "error": str(exc),
            }
        )


@dashboard_bp.get("/characters")
def characters() -> str:
    """Render a searchable character browser page."""

    faction_filter = (request.args.get("faction") or "").strip()
    kb = load_knowledge_base()
    chars = list(kb.get("characters", []) or [])
    factions = list(kb.get("factions", []) or [])
    if faction_filter:
        chars = [
            c
            for c in chars
            if isinstance(c, dict)
            and faction_filter.lower() in str(c.get("faction") or "").lower()
        ]
    return render_template(
        "characters.html",
        characters=chars,
        factions=factions,
        faction_filter=faction_filter,
        last_scraped=kb.get("last_updated"),
    )


@dashboard_bp.get("/demo")
def demo() -> str:
    """Render the one-click demo scenario launcher page."""

    tunnel_url = database.get_system_state("tunnel_url") or ""
    last_run_raw = database.get_system_state("demo_last_run") or ""
    last_run: dict[str, Any] | None = None
    if last_run_raw:
        try:
            parsed = json.loads(last_run_raw)
            if isinstance(parsed, dict):
                last_run = parsed
        except Exception:
            last_run = None
    return render_template(
        "demo.html",
        tunnel_url=tunnel_url,
        scenarios=DEMO_SCENARIOS,
        last_run=last_run,
    )


@dashboard_bp.post("/demo/run/<int:scenario_id>")
def demo_run(scenario_id: int) -> Response:
    """Start a demo scenario in the background via subprocess and redirect."""

    from pathlib import Path

    repo_root = str(Path(__file__).resolve().parent.parent)
    script = Path(repo_root) / "demo" / "simulate_push.py"
    started = False
    error: str | None = None
    if not script.is_file():
        error = f"Missing demo script at {script}"
    else:
        try:
            subprocess.Popen(
                [sys.executable, str(script), "--scenario", str(scenario_id)],
                cwd=repo_root,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            started = True
        except Exception as exc:  # noqa: BLE001
            error = str(exc)

    database.set_system_state(
        "demo_last_run",
        json.dumps(
            {
                "timestamp": _utc_now_iso(),
                "scenario": scenario_id,
                "started": started,
                "error": error,
            }
        ),
    )

    return jsonify({"started": started, "scenario": scenario_id, "error": error})


@dashboard_bp.get("/health")
def health() -> Response:
    """Return system health and readiness for the demo."""

    tunnel_url = database.get_system_state("tunnel_url") or ""
    try:
        kb = load_knowledge_base()
        knowledge_loaded = True
        chars = len(kb.get("characters", []) or [])
    except Exception:
        knowledge_loaded = False
        chars = 0

    try:
        _ = database.get_all_audits()[:1]
        db_ok = True
    except Exception:
        db_ok = False

    return jsonify(
        {
            "status": "online",
            "tunnel_url": tunnel_url,
            "knowledge_loaded": knowledge_loaded,
            "characters_indexed": chars,
            "db_ok": db_ok,
        }
    )
