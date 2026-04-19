"""Lore integrity auditing against the local Marvel RPG knowledge base via Human Delta."""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Literal

import requests

from app import database


def _utc_now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string."""

    return datetime.now(timezone.utc).isoformat()


def _is_binary_content(content: str) -> bool:
    """Heuristically detect binary content in a string payload."""

    return "\x00" in content


def _is_auditable_filename(filename: str) -> bool:
    """Return True for auditable text-like content files and False otherwise."""

    lowered = filename.lower().strip()
    if lowered.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".zip", ".tar", ".gz")):
        return False
    if lowered.endswith((".env", "requirements.txt", "config.py")):
        return False
    return lowered.endswith((".md", ".json", ".yaml", ".yml", ".txt"))


def _instructions_prompt() -> str:
    """Return the system instructions sent to Human Delta for auditing."""

    return (
        "You are MarvelLore CI, an integrity auditor for the Marvel Multiverse Role-Playing Game. "
        "Given submitted content and an official knowledge context, identify conflicts and return "
        "a structured list of issues. Look specifically for: "
        "1) stat inconsistencies vs official Marvel RPG values (melee, agility, resilience, vigilance, ego, logic), "
        "2) faction membership conflicts, "
        "3) ability/power mismatches (missing, renamed, or incorrect levels/values), "
        "4) errata violations (submitted text contradicts published errata), "
        "5) rank discrepancies. "
        "Be conservative: only report when evidence is clear from the provided context. "
        "Return JSON with an 'issues' array, each issue having: severity, character_name, issue_type, "
        "description, official_value, submitted_value, suggestion."
    )


class HumanDeltaClient:
    """HTTP client for Human Delta audit requests."""

    def __init__(self, api_key: str):
        """Create a Human Delta client using the provided API key."""

        self.api_key = api_key
        self.base_url = os.getenv("HUMAN_DELTA_API_URL", "https://api.humandelta.ai/v1/audit")
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
        )

    def audit(self, content: str, context: dict[str, Any]) -> dict[str, Any]:
        """
        Send changed content and relevant knowledge context to Human Delta.

        Handles 429s with exponential backoff (max 3 retries). Other HTTP errors
        are returned as a structured error response.
        """

        body = {
            "content": content,
            "knowledge_context": json.dumps(context, ensure_ascii=False),
            "instructions": _instructions_prompt(),
        }

        delay_s = 1.0
        for attempt in range(4):
            try:
                resp = self._session.post(self.base_url, json=body, timeout=90)
            except requests.RequestException as exc:
                return {"status": "error", "error": f"request_failed: {exc}"}

            if resp.status_code == 429 and attempt < 3:
                time.sleep(delay_s)
                delay_s *= 2
                continue

            if resp.status_code >= 400:
                try:
                    payload = resp.json()
                except Exception:  # noqa: BLE001
                    payload = {"message": resp.text[:2000]}
                return {
                    "status": "error",
                    "error": f"http_{resp.status_code}",
                    "details": payload,
                }

            try:
                return resp.json()
            except Exception as exc:  # noqa: BLE001
                return {"status": "error", "error": f"invalid_json: {exc}", "raw": resp.text[:2000]}

        return {"status": "error", "error": "rate_limited"}


def find_relevant_context(changed_content: str, knowledge_base: dict[str, Any]) -> dict[str, Any]:
    """
    Return a subset of the knowledge base for characters mentioned in the content.

    Uses simple case-insensitive substring matching. Limits to max 5 characters.
    """

    haystack = changed_content.casefold()
    characters: list[dict[str, Any]] = list(knowledge_base.get("characters", []) or [])
    matched: list[dict[str, Any]] = []

    for ch in characters:
        name = str(ch.get("name", "")).strip()
        if not name:
            continue
        if name.casefold() in haystack:
            matched.append(ch)
            if len(matched) >= 5:
                break

    relevant_factions: list[dict[str, Any]] = []
    kb_factions = knowledge_base.get("factions", []) or []
    matched_names = {str(c.get("name", "")).strip() for c in matched}
    for f in kb_factions:
        members = set(f.get("members", []) or [])
        if members.intersection(matched_names):
            relevant_factions.append(f)

    return {
        "characters": matched,
        "factions": relevant_factions,
        "errata": knowledge_base.get("errata", []) or [],
        "last_updated": knowledge_base.get("last_updated"),
    }


@dataclass(frozen=True)
class AuditIssue:
    """One specific integrity issue found by the auditor."""

    severity: Literal["critical", "warning", "info"]
    character_name: str
    issue_type: str
    description: str
    official_value: str
    submitted_value: str
    suggestion: str


@dataclass(frozen=True)
class AuditResult:
    """Aggregate audit result across all changed files."""

    status: Literal["clean", "warnings", "error"]
    issues: list[AuditIssue]
    characters_checked: list[str]
    duration_seconds: float
    raw_response: dict[str, Any]


def _issues_from_response(raw: dict[str, Any]) -> list[AuditIssue]:
    """Parse a Human Delta response into a list of AuditIssue objects."""

    items = raw.get("issues") or raw.get("conflicts") or []
    if not isinstance(items, list):
        return []

    out: list[AuditIssue] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        sev = str(it.get("severity", "warning")).lower()
        if sev not in {"critical", "warning", "info"}:
            sev = "warning"
        out.append(
            AuditIssue(
                severity=sev,  # type: ignore[arg-type]
                character_name=str(it.get("character_name", "") or ""),
                issue_type=str(it.get("issue_type", "") or "unknown"),
                description=str(it.get("description", "") or ""),
                official_value=str(it.get("official_value", "") or ""),
                submitted_value=str(it.get("submitted_value", "") or ""),
                suggestion=str(it.get("suggestion", "") or ""),
            )
        )
    return out


def _extract_submitted_number(content: str, key: str) -> int | None:
    """Extract an integer value for a stat-like key from text content."""

    # JSON-ish: "agility": 7
    m = re.search(rf'(?i)"{re.escape(key)}"\s*:\s*(\d+)', content)
    if m:
        return int(m.group(1))
    # Markdown/text: Agility: 7  OR  Agility 7
    m = re.search(rf"(?i)\b{re.escape(key)}\b\s*[:=]?\s*(\d+)", content)
    if m:
        return int(m.group(1))
    return None


def _local_audit(content: str, context: dict[str, Any], knowledge_base: dict[str, Any]) -> list[AuditIssue]:
    """
    Deterministic local audit mode used when Human Delta is unavailable.

    Performs simple numeric comparisons for stats/rank and string checks for faction conflicts.
    """

    issues: list[AuditIssue] = []
    factions = [f.get("name", "") for f in (knowledge_base.get("factions") or []) if isinstance(f, dict)]
    content_cf = content.casefold()

    for ch in context.get("characters", []) or []:
        if not isinstance(ch, dict):
            continue
        name = str(ch.get("name") or "").strip()
        if not name:
            continue

        official_stats = dict(ch.get("stats") or {})
        official_rank = ch.get("rank")
        official_faction = str(ch.get("faction") or "")

        # Stat mismatches
        for stat_key in ("melee", "agility", "resilience", "vigilance", "ego", "logic"):
            submitted = _extract_submitted_number(content, stat_key)
            if submitted is None:
                continue
            try:
                official = int(official_stats.get(stat_key))
            except Exception:
                continue
            if submitted != official:
                issues.append(
                    AuditIssue(
                        severity="warning",
                        character_name=name,
                        issue_type="stat_mismatch",
                        description=f"Submitted {stat_key} does not match official value.",
                        official_value=str(official),
                        submitted_value=str(submitted),
                        suggestion=f"Update {stat_key} to {official} for {name}.",
                    )
                )

        # Rank discrepancy
        submitted_rank = _extract_submitted_number(content, "rank")
        if submitted_rank is not None and official_rank is not None:
            try:
                official_r = int(official_rank)
            except Exception:
                official_r = None
            if official_r is not None and submitted_rank != official_r:
                issues.append(
                    AuditIssue(
                        severity="warning",
                        character_name=name,
                        issue_type="rank_discrepancy",
                        description="Submitted rank does not match official rank.",
                        official_value=str(official_r),
                        submitted_value=str(submitted_rank),
                        suggestion=f"Set Rank to {official_r} for {name}.",
                    )
                )

        # Faction conflicts: if content mentions another known faction that is not in official faction string.
        for f in factions:
            if not f:
                continue
            f_cf = str(f).casefold()
            if f_cf in content_cf and f_cf not in official_faction.casefold():
                # Only flag if the character name is also mentioned in the content.
                if name.casefold() not in content_cf:
                    continue
                issues.append(
                    AuditIssue(
                        severity="warning",
                        character_name=name,
                        issue_type="faction_conflict",
                        description=f"Submitted content associates {name} with faction '{f}', which conflicts with official faction.",
                        official_value=official_faction,
                        submitted_value=f,
                        suggestion=f"Align faction membership with official: {official_faction}.",
                    )
                )

    return issues


def run_audit(
    changed_files: list[dict[str, Any]],
    knowledge_base: dict[str, Any],
    repo: str,
    pr_number: int,
    commit_sha: str,
) -> AuditResult:
    """
    Main audit entrypoint.

    Audits only .md/.json/.yaml/.yml/.txt files, skipping obvious binaries and config files.
    Aggregates issues across files and persists the audit summary to SQLite.
    """

    start = time.monotonic()
    triggered_at = _utc_now_iso()

    api_key = os.getenv("HUMAN_DELTA_API_KEY", "").strip()
    raw_by_file: dict[str, Any] = {}
    all_issues: list[AuditIssue] = []
    characters_checked: list[str] = []

    if not api_key:
        print("⚠️ Human Delta API key missing, using local audit mode")
        client = None
    else:
        client = HumanDeltaClient(api_key=api_key)

    for item in changed_files:
        filename = str(item.get("filename", "") or "")
        if not filename or not _is_auditable_filename(filename):
            continue

        content = item.get("content", "")
        if not isinstance(content, str):
            continue
        if _is_binary_content(content):
            continue

        context = find_relevant_context(content, knowledge_base)
        mentioned = [str(c.get("name", "") or "") for c in context.get("characters", [])]
        for n in mentioned:
            if n and n not in characters_checked:
                characters_checked.append(n)

        resp: dict[str, Any]
        if client is None:
            resp = {"status": "error", "error": "human_delta_disabled"}
        else:
            resp = client.audit(content=content, context=context)

        issues = _issues_from_response(resp)
        if not issues and str(resp.get("status") or "").lower() == "error":
            print("⚠️ Human Delta unreachable, using local audit mode")
            issues = _local_audit(content=content, context=context, knowledge_base=knowledge_base)
            raw_by_file[filename] = {"fallback": "local", "human_delta": resp}
        else:
            raw_by_file[filename] = resp
        all_issues.extend(issues)

    duration = time.monotonic() - start

    status: Literal["clean", "warnings", "error"] = "clean"
    if any(i.severity == "critical" for i in all_issues):
        status = "error"
    elif all_issues:
        status = "warnings"

    result = AuditResult(
        status=status,
        issues=all_issues,
        characters_checked=characters_checked,
        duration_seconds=duration,
        raw_response=raw_by_file,
    )

    database.insert_audit(
        repo=repo,
        pr_number=pr_number,
        commit_sha=commit_sha,
        triggered_at=triggered_at,
        status=result.status,
        issues_found=len(result.issues),
        report_json={"result": asdict(result)},
        duration_seconds=result.duration_seconds,
    )

    return result


def format_pr_comment(result: AuditResult, pr_number: int, commit_sha: str) -> str:
    """
    Format an AuditResult as a GitHub PR comment in Markdown.

    Uses ✅ 🟡 🔴 emojis and collapsible <details> blocks for issues.
    """

    if result.status == "clean":
        emoji = "✅"
    elif result.status == "warnings":
        emoji = "🟡"
    else:
        emoji = "🔴"

    short_sha = (commit_sha or "unknown")[:7]
    duration = f"{result.duration_seconds:.2f}"
    issues_count = len(result.issues)
    names = ", ".join(result.characters_checked) if result.characters_checked else "(none)"

    issues_md: list[str] = []
    for idx, issue in enumerate(result.issues, start=1):
        title = f"{idx}. {issue.severity.upper()} — {issue.issue_type}"
        body = "\n".join(
            [
                f"**Character:** {issue.character_name or '(unspecified)'}",
                f"**Type:** `{issue.issue_type}`",
                f"**Severity:** `{issue.severity}`",
                "",
                f"**Description:** {issue.description}",
                "",
                f"**Official:** `{issue.official_value}`",
                f"**Submitted:** `{issue.submitted_value}`",
                "",
                f"**Suggestion:** {issue.suggestion}",
            ]
        )
        issues_md.append(
            "\n".join(
                [
                    "<details>",
                    f"<summary>{title}</summary>",
                    "",
                    body,
                    "</details>",
                ]
            )
        )

    characters_with_issues = {i.character_name for i in result.issues if i.character_name}
    clean_nodes = [n for n in result.characters_checked if n and n not in characters_with_issues]
    clean_md = "\n".join(f"- {n}" for n in clean_nodes) if clean_nodes else "- (none)"

    if result.status == "clean" and result.characters_checked:
        celebratory = (
            "All checked nodes look consistent with the current Marvel RPG knowledge base. "
            "Great job keeping the multiverse tidy."
        )
    elif result.status == "clean":
        celebratory = "No auditable characters were detected in the submitted changes."
    else:
        celebratory = ""

    issues_section = "\n\n".join(issues_md) if issues_md else "No issues found."

    return "\n".join(
        [
            "## 🛡️ MarvelLore CI — Audit Report",
            f"**PR:** #{pr_number}  **Commit:** `{short_sha}`  **Duration:** {duration}s",
            "",
            "### Summary",
            "| Status | Issues | Characters Checked |",
            "|--------|--------|--------------------|",
            f"| {emoji} {result.status} | {issues_count} | {names} |",
            "",
            (f"{celebratory}\n" if celebratory else "").rstrip(),
            "### Issues Found",
            issues_section,
            "",
            "### ✅ Clean Nodes",
            clean_md,
            "",
            "---",
            "*Powered by [Human Delta](https://humandelta.ai) knowledge infrastructure*",
        ]
    ).strip()
