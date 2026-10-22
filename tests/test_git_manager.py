"""Tests for GitManager parsing, repo detection, and metadata extraction."""

import pytest
from pathlib import Path
from agent_orchestrator.git_manager import GitManager


def test_parse_github_identifiers():
    cases = [
        ("https://github.com/torvalds/linux.git", "torvalds", "linux"),
        ("https://github.com/openai/codex", "openai", "codex"),
        ("git@github.com:facebook/react.git", "facebook", "react"),
        ("anthropic/claude-cli", "anthropic", "claude-cli"),
    ]

    for identifier, expected_owner, expected_repo in cases:
        parsed = GitManager.parse_github_identifier(identifier)
        assert parsed is not None, f"Failed to parse: {identifier}"
        assert parsed["owner"] == expected_owner
        assert parsed["repo"] == expected_repo
        assert parsed["clone_url"].startswith("https://github.com/")


def test_invalid_github_identifier():
    assert GitManager.parse_github_identifier("just_a_random_string") is None
    assert GitManager.parse_github_identifier("/local/path/to/folder") is None


def test_is_git_repo(tmp_path: Path):
    assert GitManager.is_git_repo(tmp_path) is False
    (tmp_path / ".git").mkdir()
    assert GitManager.is_git_repo(tmp_path) is True


def test_get_repo_info_non_git(tmp_path: Path):
    gm = GitManager(repo_dir=tmp_path)
    info = gm.get_repo_info()
    assert info["is_git_repo"] is False
