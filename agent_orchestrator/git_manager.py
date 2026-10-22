"""Git repository manager for recognizing, cloning, and managing Git and GitHub repositories."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional


class GitManager:
    """Provides Git and GitHub repository recognition, inspection, and operations."""

    GITHUB_URL_REGEX = re.compile(
        r"^(https?://github\.com/|git@github\.com:)?([a-zA-Z0-9_\-\.]+)/([a-zA-Z0-9_\-\.]+?)(?:\.git)?/?$"
    )

    def __init__(self, repo_dir: Path | str | None = None, cache_dir: Path | str | None = None) -> None:
        self.repo_dir: Optional[Path] = Path(repo_dir).resolve() if repo_dir else None
        if cache_dir:
            self.cache_dir = Path(cache_dir).resolve()
        else:
            self.cache_dir = Path.home() / ".agent_orchestrator" / "repos"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def parse_github_identifier(cls, identifier: str) -> Optional[Dict[str, str]]:
        """Parses a GitHub URL, SSH URI, or 'owner/repo' slug into owner, repo, and clone URL."""
        cleaned = identifier.strip()
        match = cls.GITHUB_URL_REGEX.match(cleaned)
        if match:
            _, owner, repo = match.groups()
            clone_url = f"https://github.com/{owner}/{repo}.git"
            return {
                "owner": owner,
                "repo": repo,
                "full_name": f"{owner}/{repo}",
                "clone_url": clone_url,
            }
        return None

    @classmethod
    def is_git_repo(cls, path: Path | str) -> bool:
        """Checks if a given directory is a Git repository."""
        target = Path(path).resolve()
        if (target / ".git").exists():
            return True
        try:
            res = subprocess.run(
                ["git", "rev-parse", "--is-inside-work-tree"],
                cwd=str(target),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            return res.returncode == 0 and res.stdout.strip() == "true"
        except Exception:
            return False

    def clone_or_checkout(
        self,
        identifier: str,
        target_dir: Path | str | None = None,
        branch: Optional[str] = None,
        depth: Optional[int] = None,
    ) -> Path:
        """Clones a remote GitHub/Git repo if not cloned, or checks out the existing clone."""
        gh_info = self.parse_github_identifier(identifier)
        clone_url = gh_info["clone_url"] if gh_info else identifier
        repo_name = gh_info["repo"] if gh_info else Path(identifier.rstrip("/")).stem

        if target_dir:
            destination = Path(target_dir).resolve()
        else:
            subfolder = gh_info["full_name"] if gh_info else repo_name
            destination = self.cache_dir / subfolder

        if destination.exists() and self.is_git_repo(destination):
            # Already exists, optionally checkout
            self.repo_dir = destination
            if branch:
                self.checkout(branch)
            return destination

        destination.parent.mkdir(parents=True, exist_ok=True)
        clone_cmd = ["git", "clone"]
        if branch:
            clone_cmd.extend(["--branch", branch])
        if depth:
            clone_cmd.extend(["--depth", str(depth)])
        clone_cmd.extend([clone_url, str(destination)])

        result = subprocess.run(
            clone_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Failed to clone git repository '{clone_url}': {result.stderr.strip()}")

        self.repo_dir = destination
        return destination

    def _run_git(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        if not self.repo_dir:
            raise ValueError("No repository directory is set for GitManager.")
        return subprocess.run(
            ["git"] + args,
            cwd=str(self.repo_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )

    def get_repo_info(self) -> Dict[str, Any]:
        """Returns metadata about the active repository."""
        if not self.repo_dir or not self.is_git_repo(self.repo_dir):
            return {
                "is_git_repo": False,
                "path": str(self.repo_dir) if self.repo_dir else None,
            }

        branch_proc = self._run_git(["branch", "--show-current"])
        branch = branch_proc.stdout.strip()

        commit_proc = self._run_git(["rev-parse", "HEAD"])
        commit = commit_proc.stdout.strip() if commit_proc.returncode == 0 else "unknown"

        remote_proc = self._run_git(["remote", "-v"])
        remotes = remote_proc.stdout.strip().splitlines()

        status_proc = self._run_git(["status", "--porcelain"])
        is_dirty = bool(status_proc.stdout.strip())

        return {
            "is_git_repo": True,
            "path": str(self.repo_dir),
            "branch": branch or "detached",
            "commit": commit,
            "remotes": remotes,
            "is_dirty": is_dirty,
        }

    def status(self) -> str:
        """Returns `git status --short` output."""
        res = self._run_git(["status", "--short"])
        return res.stdout.strip() or "Clean working directory (no changes)"

    def diff(self, cached: bool = False) -> str:
        """Returns current git diff."""
        cmd = ["diff"]
        if cached:
            cmd.append("--cached")
        res = self._run_git(cmd)
        return res.stdout.strip() or "No git differences detected."

    def create_branch(self, branch_name: str) -> str:
        """Creates and checks out a new branch."""
        res = self._run_git(["checkout", "-b", branch_name])
        if res.returncode != 0:
            raise RuntimeError(f"Failed to create branch '{branch_name}': {res.stderr.strip()}")
        return f"Created and switched to branch '{branch_name}'"

    def checkout(self, branch_or_commit: str) -> str:
        """Checks out a branch or commit."""
        res = self._run_git(["checkout", branch_or_commit])
        if res.returncode != 0:
            raise RuntimeError(f"Failed to checkout '{branch_or_commit}': {res.stderr.strip()}")
        return f"Checked out '{branch_or_commit}'"

    def commit(self, message: str, add_all: bool = True) -> str:
        """Stages all files (if add_all) and creates a commit."""
        if add_all:
            self._run_git(["add", "-A"])
        res = self._run_git(["commit", "-m", message])
        if res.returncode != 0:
            raise RuntimeError(f"Failed to commit changes: {res.stderr.strip()}")
        return res.stdout.strip()
