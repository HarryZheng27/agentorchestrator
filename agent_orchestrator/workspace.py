"""Workspace management for inspecting, modifying, and running commands in existing codebases."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent_orchestrator.git_manager import GitManager


class WorkspaceManager:
    """Manages files, commands, and Git operations within a project workspace."""

    def __init__(
        self,
        project_dir: Path | str | None = None,
        github_repo: Optional[str] = None,
        auto_clone: bool = True,
        allow_shell_commands: bool = False,
    ) -> None:
        if not isinstance(allow_shell_commands, bool):
            raise ValueError("allow_shell_commands must be a boolean")
        self.git_manager = GitManager()
        self.allow_shell_commands = allow_shell_commands
        if github_repo and auto_clone:
            # Recognize and clone or checkout GitHub repo
            self.project_dir = self.git_manager.clone_or_checkout(github_repo, target_dir=project_dir)
            self.git_manager.repo_dir = self.project_dir
        elif project_dir:
            self.project_dir = Path(project_dir).resolve()
            self.project_dir.mkdir(parents=True, exist_ok=True)
            self.git_manager.repo_dir = self.project_dir
        else:
            self.project_dir = Path.cwd().resolve()
            self.git_manager.repo_dir = self.project_dir

    def resolve_path(self, relative_path: str | Path) -> Path:
        """Resolves a path relative to the workspace, ensuring no traversal outside project root."""
        p = Path(relative_path)
        if not p.is_absolute():
            p = (self.project_dir / p).resolve()
        else:
            p = p.resolve()

        # Check containment
        try:
            p.relative_to(self.project_dir)
        except ValueError:
            raise PermissionError(f"Access denied: path '{relative_path}' is outside workspace '{self.project_dir}'.")
        return p

    def read_file(self, path: str, start_line: Optional[int] = None, end_line: Optional[int] = None) -> str:
        """Reads content from a file within the workspace with optional line slicing."""
        file_path = self.resolve_path(path)
        if not file_path.exists():
            raise FileNotFoundError(f"File '{path}' does not exist in workspace.")
        if file_path.is_dir():
            raise IsADirectoryError(f"Path '{path}' is a directory, not a file.")

        content = file_path.read_text(encoding="utf-8", errors="replace")
        lines = content.splitlines(keepends=True)

        if start_line is not None or end_line is not None:
            s = max(1, start_line or 1) - 1
            e = end_line if end_line is not None else len(lines)
            return "".join(lines[s:e])
        return content

    def write_file(self, path: str, content: str, overwrite: bool = True) -> str:
        """Writes or creates a file within the workspace."""
        file_path = self.resolve_path(path)
        if file_path.exists() and not overwrite:
            raise FileExistsError(f"File '{path}' already exists and overwrite is False.")

        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        return f"Successfully wrote {len(content)} characters to {path}"

    def patch_file(self, path: str, target_snippet: str, replacement_snippet: str) -> str:
        """Replaces target_snippet with replacement_snippet in an existing file."""
        file_path = self.resolve_path(path)
        if not file_path.exists():
            raise FileNotFoundError(f"File '{path}' does not exist.")

        original = file_path.read_text(encoding="utf-8", errors="replace")
        count = original.count(target_snippet)
        if count == 0:
            raise ValueError(f"Target snippet not found in '{path}'.")
        if count > 1:
            raise ValueError(f"Target snippet occurs {count} times in '{path}'. Please provide a unique snippet.")

        updated = original.replace(target_snippet, replacement_snippet, 1)
        file_path.write_text(updated, encoding="utf-8")
        return f"Successfully patched '{path}' (replaced 1 occurrence)."

    def list_files(self, sub_dir: str = ".", pattern: str = "*", recursive: bool = True) -> List[str]:
        """Lists files matching a pattern within the workspace."""
        target_dir = self.resolve_path(sub_dir)
        if not target_dir.exists() or not target_dir.is_dir():
            return []

        glob_fn = target_dir.rglob if recursive else target_dir.glob
        matched: set[str] = set()
        for p in glob_fn(pattern):
            # A symlink can point outside the workspace despite its local name.
            try:
                visible_parts = p.relative_to(self.project_dir).parts
                resolved = self.resolve_path(p)
                resolved_parts = resolved.relative_to(self.project_dir).parts
                if not resolved.is_file():
                    continue
            except (OSError, PermissionError, ValueError):
                continue
            if any(x in visible_parts or x in resolved_parts for x in (".git", "__pycache__", "node_modules", ".venv", "venv")):
                continue
            matched.add(resolved.relative_to(self.project_dir).as_posix())
        return sorted(matched)

    def search_code(self, query: str, pattern: str = "*") -> List[Dict[str, Any]]:
        """Searches code for a text or regex pattern across workspace files."""
        results: List[Dict[str, Any]] = []
        regex = re.compile(query, re.IGNORECASE)

        files = self.list_files(".", pattern=pattern, recursive=True)
        for rel_path in files:
            try:
                full_path = self.resolve_path(rel_path)
                content = full_path.read_text(encoding="utf-8", errors="replace")
            except (OSError, PermissionError, ValueError):
                continue

            for idx, line in enumerate(content.splitlines(), start=1):
                if regex.search(line):
                    results.append({
                        "file": rel_path,
                        "line": idx,
                        "content": line.strip(),
                    })
                    if len(results) >= 100:
                        return results
        return results

    def run_command(self, command: str, timeout_sec: int = 60) -> Dict[str, Any]:
        """Runs an unrestricted host shell with the workspace as its working directory."""
        if not self.allow_shell_commands:
            return {
                "command": command,
                "exit_code": -1,
                "stdout": "",
                "stderr": "Shell commands are disabled. Set workspace.allow_shell_commands: true only for trusted workflows.",
                "success": False,
            }
        try:
            res = subprocess.run(
                command,
                cwd=str(self.project_dir),
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout_sec,
            )
            return {
                "command": command,
                "exit_code": res.returncode,
                "stdout": res.stdout,
                "stderr": res.stderr,
                "success": res.returncode == 0,
            }
        except subprocess.TimeoutExpired:
            return {
                "command": command,
                "exit_code": -1,
                "stdout": "",
                "stderr": f"Command timed out after {timeout_sec} seconds.",
                "success": False,
            }
        except Exception as e:
            return {
                "command": command,
                "exit_code": -1,
                "stdout": "",
                "stderr": str(e),
                "success": False,
            }

    def get_summary(self) -> Dict[str, Any]:
        """Returns workspace and git status summary."""
        info = {
            "project_dir": str(self.project_dir),
            "files_count": len(self.list_files()),
            "git": self.git_manager.get_repo_info(),
        }
        return info
