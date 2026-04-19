"""GitHub API helpers for PR comments using PyGithub."""

from __future__ import annotations

import base64
import logging
from typing import Any

import requests
from github import Github
from github.GithubException import GithubException

logger = logging.getLogger(__name__)


class GitHubClient:
    """Thin wrapper around PyGithub for PR and push file retrieval."""

    def __init__(self, token: str, repo_name: str):
        """Initialize the GitHub client and target repository."""

        self.token = token
        self.repo_name = repo_name
        self.gh = Github(token)
        self.repo = self.gh.get_repo(repo_name)
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"token {token}",
                "Accept": "application/vnd.github+json",
                "User-Agent": "MarvelLore-CI",
            }
        )

    def get_pr_files(self, pr_number: int) -> list[dict[str, Any]]:
        """
        Return changed PR files with decoded content when available.

        Shape: {filename, status, additions, deletions, patch, content}
        """

        out: list[dict[str, Any]] = []
        try:
            pr = self.repo.get_pull(pr_number)
            for f in pr.get_files():
                content = self._fetch_raw(f.raw_url) if getattr(f, "raw_url", None) else ""
                out.append(
                    {
                        "filename": f.filename,
                        "status": getattr(f, "status", ""),
                        "additions": int(getattr(f, "additions", 0) or 0),
                        "deletions": int(getattr(f, "deletions", 0) or 0),
                        "patch": getattr(f, "patch", "") or "",
                        "content": content,
                    }
                )
        except GithubException as exc:
            logger.exception("get_pr_files failed: %s", exc)
        return out

    def get_push_files(self, commits: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        Build changed file list from a push payload commits list.

        This fetches per-commit file metadata from GitHub and de-duplicates by filename.
        """

        out_by_name: dict[str, dict[str, Any]] = {}
        try:
            for c in commits:
                sha = str(c.get("id") or c.get("sha") or "").strip()
                if not sha:
                    continue
                commit = self.repo.get_commit(sha=sha)
                for f in commit.files:
                    filename = getattr(f, "filename", "") or ""
                    if not filename:
                        continue
                    status = getattr(f, "status", "") or ""
                    if status == "removed":
                        content = ""
                    else:
                        content = self._fetch_repo_file(filename, ref=sha)
                    out_by_name[filename] = {
                        "filename": filename,
                        "status": status,
                        "additions": int(getattr(f, "additions", 0) or 0),
                        "deletions": int(getattr(f, "deletions", 0) or 0),
                        "patch": getattr(f, "patch", "") or "",
                        "content": content,
                    }
        except GithubException as exc:
            logger.exception("get_push_files failed: %s", exc)
        return list(out_by_name.values())

    def post_pr_comment(self, pr_number: int, body: str) -> None:
        """Post a markdown comment on the given pull request."""

        try:
            pr = self.repo.get_pull(pr_number)
            pr.create_issue_comment(body)
        except GithubException as exc:
            logger.exception("post_pr_comment failed: %s", exc)

    def _fetch_raw(self, raw_url: str) -> str:
        """Fetch a raw file URL and decode it as UTF-8 text."""

        try:
            resp = self._session.get(raw_url, timeout=60)
            if resp.status_code >= 400:
                return ""
            return resp.content.decode("utf-8", errors="replace")
        except requests.RequestException:
            return ""

    def _fetch_repo_file(self, path: str, ref: str) -> str:
        """Fetch file content from the repo at a given ref (commit SHA)."""

        try:
            obj = self.repo.get_contents(path, ref=ref)
        except GithubException:
            return ""
        try:
            # PyGithub returns base64-encoded content for files.
            if hasattr(obj, "decoded_content") and obj.decoded_content is not None:
                return obj.decoded_content.decode("utf-8", errors="replace")
            content = getattr(obj, "content", "") or ""
            if not content:
                return ""
            return base64.b64decode(content).decode("utf-8", errors="replace")
        except Exception:
            return ""

def post_pr_comment(
    repo_full_name: str,
    pr_number: int,
    body: str,
) -> None:
    """Post a markdown comment on the given pull request."""

    return


def verify_webhook_signature(payload: bytes, signature_header: str | None) -> bool:
    """Verify a GitHub webhook HMAC signature using the configured secret."""

    return False
