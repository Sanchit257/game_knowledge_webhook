"""Simulate lore contributions by pushing branches and opening PRs."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from github import Github
from github.GithubException import GithubException
from dotenv import load_dotenv
load_dotenv()


SCENARIOS: dict[int, dict[str, Any]] = {
    1: {
        "name": "Clean submission",
        "filename": "lore/demo/scenario_1_spider-man.md",
        "title": "Demo: Clean Spider-Man submission",
        "body": (
            "# SPIDER-MAN\n\n"
            "Rank: 4\n"
            "Faction: Avengers/Solo\n\n"
            "Stats:\n"
            "- Melee: 4\n"
            "- Agility: 7\n"
            "- Resilience: 4\n"
            "- Vigilance: 4\n"
            "- Ego: 4\n"
            "- Logic: 4\n"
        ),
        "expected": "Should be clean (no issues).",
    },
    2: {
        "name": "Stat mismatch",
        "filename": "lore/demo/scenario_2_spider-man_mismatch.md",
        "title": "Demo: Spider-Man agility mismatch",
        "body": (
            "# SPIDER-MAN\n\n"
            "Rank: 4\n"
            "Faction: Avengers/Solo\n\n"
            "Stats:\n"
            "- Melee: 4\n"
            "- Agility: 4\n"
            "- Resilience: 4\n"
            "- Vigilance: 4\n"
            "- Ego: 4\n"
            "- Logic: 4\n"
        ),
        "expected": "Should warn: Agility mismatch (official 7).",
    },
    3: {
        "name": "Faction conflict",
        "filename": "lore/demo/scenario_3_iron-man_faction.md",
        "title": "Demo: Iron Man faction conflict",
        "body": (
            "# IRON MAN\n\n"
            "Rank: 5\n"
            "Faction: X-Men\n\n"
            "Stats:\n"
            "- Melee: 3\n"
            "- Agility: 3\n"
            "- Resilience: 4\n"
            "- Vigilance: 5\n"
            "- Ego: 4\n"
            "- Logic: 7\n"
        ),
        "expected": "Should warn: faction conflict (official Avengers).",
    },
}


def _run(cmd: list[str]) -> None:
    """Run a command and stream output to the console."""

    print(f"$ {' '.join(cmd)}")
    subprocess.run(cmd, check=True)


def _git_root() -> Path:
    """Return the repository root directory."""

    return Path(__file__).resolve().parents[2]


def _branch_name(scenario_id: int) -> str:
    """Return a unique demo branch name."""

    return f"demo/scenario-{scenario_id}-{int(time.time())}"


def _get_tunnel_url() -> str:
    """Fetch tunnel URL from SQLite state (best-effort)."""

    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from app.database import get_system_state  # type: ignore

        return get_system_state("tunnel_url") or ""
    except Exception:
        return ""


def _create_pr(branch: str, title: str, body: str) -> int:
    """Create a PR via PyGithub and return PR number."""

    token = os.getenv("GITHUB_TOKEN", "").strip()
    repo_name = os.getenv("GITHUB_REPO", "").strip()
    if not token or not repo_name:
        raise RuntimeError("GITHUB_TOKEN and GITHUB_REPO must be set to open a PR.")

    gh = Github(token)
    repo = gh.get_repo(repo_name)

    base = os.getenv("DEMO_BASE_BRANCH", "main").strip() or "main"
    try:
        pr = repo.create_pull(title=title, body=body, head=branch, base=base)
    except GithubException:
        # Fall back to master if main doesn't exist.
        pr = repo.create_pull(title=title, body=body, head=branch, base="master")

    return int(pr.number)


def main() -> None:
    """Entry point for the demo simulator."""

    parser = argparse.ArgumentParser(description="MarvelLore CI demo PR simulator")
    parser.add_argument("--scenario", type=int, required=True, choices=[1, 2, 3])
    args = parser.parse_args()

    scenario = SCENARIOS[args.scenario]
    print(f"Scenario {args.scenario}: {scenario['name']}")
    print(f"Expected: {scenario['expected']}")

    repo_root = _git_root()
    marvellore_dir = repo_root / "marvellore-ci"
    target_path = marvellore_dir / scenario["filename"]
    target_path.parent.mkdir(parents=True, exist_ok=True)

    branch = _branch_name(args.scenario)
    print(f"Creating branch {branch}")
    _run(["git", "-C", str(repo_root), "checkout", "-b", branch])

    print(f"Writing file {target_path}")
    body = str(scenario["body"]).rstrip() + "\n"
    body += f"\n<!-- demo_scenario: {args.scenario} run_id: {int(time.time())} -->\n"
    target_path.write_text(body, encoding="utf-8")

    print("Committing change")
    _run(["git", "-C", str(repo_root), "add", str(target_path.relative_to(repo_root))])
    # Ensure there's something to commit (avoid no-op when file already matches).
    diff_check = subprocess.run(
        ["git", "-C", str(repo_root), "diff", "--cached", "--quiet"],
        check=False,
    )
    if diff_check.returncode == 0:
        raise RuntimeError(
            "No changes were staged for commit (scenario file produced no diff)."
        )
    _run(["git", "-C", str(repo_root), "commit", "-m", f"demo: scenario {args.scenario} {scenario['name']}"])

    print("Pushing branch to origin")
    _run(["git", "-C", str(repo_root), "push", "-u", "origin", "HEAD"])

    print("Opening pull request")
    pr_number = _create_pr(branch=branch, title=str(scenario["title"]), body="Hackathon demo submission.")
    print(f"PR opened: #{pr_number}")

    url = _get_tunnel_url()
    if url:
        print(f"Now watch your dashboard at {url}")
    else:
        print("Now watch your dashboard (tunnel URL not found in DB).")


if __name__ == "__main__":
    main()

