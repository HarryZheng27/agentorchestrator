"""Tests for concise command-line activity output."""

from __future__ import annotations

import io

from agent_orchestrator import cli


class CapturedOutput(io.StringIO):
    """A text buffer that can emulate either an interactive terminal or a pipe."""

    def __init__(self, is_tty: bool):
        super().__init__()
        self._is_tty = is_tty

    def isatty(self) -> bool:
        return self._is_tty


def test_file_edits_show_colored_added_and_removed_lines_in_a_tty(monkeypatch):
    output = CapturedOutput(is_tty=True)
    monkeypatch.setattr(cli.sys, "stdout", output)

    cli._print_cli_event(
        "file_edited",
        {
            "path": "src/example.py",
            "diff": "--- a/src/example.py\n+++ b/src/example.py\n@@ -1 +1 @@\n-old\n+new\n context",
        },
    )

    rendered = output.getvalue()
    assert "Edited: example.py" in rendered
    assert "\033[31m-old\033[0m" in rendered
    assert "\033[32m+new\033[0m" in rendered
    assert "--- a/src/example.py" not in rendered
    assert "+++ b/src/example.py" not in rendered


def test_file_edits_are_plain_text_when_stdout_is_not_a_tty(monkeypatch):
    output = CapturedOutput(is_tty=False)
    monkeypatch.setattr(cli.sys, "stdout", output)

    cli._print_cli_event(
        "file_edited",
        {
            "path": "src/example.py",
            "diff": "--- a/src/example.py\n+++ b/src/example.py\n@@ -1 +1 @@\n-old\n+new",
        },
    )

    rendered = output.getvalue()
    assert "    -old\n" in rendered
    assert "    +new\n" in rendered
    assert "\033[" not in rendered
    assert "--- a/src/example.py" not in rendered
    assert "+++ b/src/example.py" not in rendered


def test_file_edits_preserve_changed_content_starting_with_extra_signs(monkeypatch):
    output = CapturedOutput(is_tty=True)
    monkeypatch.setattr(cli.sys, "stdout", output)

    cli._print_cli_event(
        "file_edited",
        {
            "path": "src/options.py",
            "diff": "--- a/src/options.py\n+++ b/src/options.py\n@@ -1 +1 @@\n--- something\n+++ something",
        },
    )

    rendered = output.getvalue()
    assert "\033[31m--- something\033[0m" in rendered
    assert "\033[32m+++ something\033[0m" in rendered


def test_subagent_activity_prints_without_elapsed_timer(monkeypatch):
    output = CapturedOutput(is_tty=False)
    monkeypatch.setattr(cli.sys, "stdout", output)

    cli.cli_event_logger(
        "run_started",
        {"initial_agent": "architect", "workspace": ".", "prompt": "Ship feature"},
    )
    cli.cli_event_logger("agent_turn_start", {"agent": "architect"})
    cli.cli_event_logger(
        "delegation_dispatched",
        {"from_agent": "architect", "to_agent": "coder", "role": "Coder", "task": "Implement API", "depth": 1},
    )
    cli.cli_event_logger("agent_turn_start", {"agent": "coder", "call_stack": ["architect", "coder"]})
    cli.cli_event_logger("tool_invoked", {"agent": "coder", "tool": "git_status", "args": {}})
    cli.cli_event_logger("agent_final_response", {"agent": "coder", "role": "Coder", "text": "Done coding"})
    cli.cli_event_logger("run_completed", {})

    rendered = output.getvalue()
    assert "architect → coder (Coder)  ·  Implement API" in rendered
    assert "coder working  [architect → coder]" in rendered
    assert "[coder] Checking git status" in rendered
    assert "Done coding" in rendered
    assert "Workflow finished." in rendered
    assert "Elapsed" not in rendered
    assert "\r" not in rendered
