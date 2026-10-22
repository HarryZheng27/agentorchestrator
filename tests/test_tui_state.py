"""Checks for the event projection used by the terminal monitor."""

import asyncio

import pytest

from agent_orchestrator.tui_state import WorkflowState


def test_delegated_agent_is_nested_and_parent_resumes_after_completion():
    state = WorkflowState()
    state.consume("agent_registered", {"name": "architect", "role": "Lead"})
    state.consume("agent_registered", {"name": "coder", "role": "Developer"})
    state.consume("run_started", {"initial_agent": "architect", "workspace": "."})
    state.consume("agent_turn_start", {"agent": "architect", "prompt": "Build feature"})
    assert state.agents["architect"].status == "Working"
    assert state.agents["architect"].task == "Build feature"
    assert state.agents["architect"].activity == "Working on assigned task"
    state.consume("delegation_dispatched", {
        "from_agent": "architect", "to_agent": "coder", "task": "Implement API",
    })

    assert state.agents["coder"].parent == "architect"
    assert state.agents["coder"].task == "Implement API"
    assert state.agents["architect"].status == "Delegating"

    state.consume("agent_final_response", {"agent": "coder"})
    assert state.agents["coder"].status == "Completed"
    assert state.agents["coder"].activity == "Task completed"
    assert state.agents["architect"].status == "Working"
    assert state.agents["architect"].activity == "Resumed after coder completed"

    state.consume("run_completed", {"final_response": "Done"})
    assert state.status == "Completed"
    assert state.final_response == "Done"


def test_file_events_show_basenames_only():
    state = WorkflowState()
    assert state.consume("file_accessed", {"agent": "coder", "paths": ["secret/path/api.py", "C:\\other\\test.py"]}) == "coder read: api.py, test.py"
    assert state.consume("file_edited", {"agent": "coder", "path": "private/results.txt", "diff": "+secret"}) == "coder edited: results.txt"
    assert "secret" not in "\n".join(state.lines)


def test_agent_response_event_is_added_to_activity_log():
    state = WorkflowState()

    line = state.consume("agent_response", {"agent": "coder", "text": "Implemented the API."})

    assert line == "coder: Implemented the API."
    assert list(state.lines) == ["coder: Implemented the API."]


def test_agent_status_tracks_process_progress_and_tool_activity_without_arguments():
    state = WorkflowState()
    state.consume("agent_turn_start", {"agent": "coder", "prompt": "Implement API"})
    assert state.agents["coder"].status == "Working"

    state.consume("agent_process_started", {"agent": "coder"})
    assert state.agents["coder"].status == "Running Codex"
    state.consume("agent_progress", {"agent": "coder", "text": "Codex turn started"})
    assert state.agents["coder"].status == "Working"

    state.consume("tool_invoked", {
        "agent": "coder", "tool": "write_file",
        "args": {"path": "secret.txt", "content": "private payload"},
    })
    assert state.agents["coder"].status == "Using tool"
    assert state.agents["coder"].activity == "Using write_file"
    assert "private payload" not in state.agents["coder"].activity

    state.consume("tool_completed", {"agent": "coder", "tool": "write_file", "result": "private payload"})
    assert state.agents["coder"].status == "Working"
    assert state.agents["coder"].activity == "Finished write_file; continuing task"
    state.consume("agent_final_response", {"agent": "coder"})
    assert state.agents["coder"].status == "Completed"


def test_progress_command_summary_does_not_expose_arguments():
    state = WorkflowState()
    state.consume("agent_progress", {
        "agent": "coder", "text": "Codex started powershell.exe -Command secret-value",
    })

    assert state.agents["coder"].activity == "Codex started powershell.exe"
    assert "secret-value" not in state.agents["coder"].activity


def test_process_and_agent_errors_update_status():
    state = WorkflowState()
    state.consume("agent_process_finished", {"agent": "coder", "exit_code": 2})
    assert state.agents["coder"].status == "Failed"
    state.consume("agent_error", {"agent": "architect", "text": "failed"})
    assert state.agents["architect"].status == "Failed"
    state.consume("agent_protocol_error", {"agent": "reviewer", "text": "bad event"})
    assert state.agents["reviewer"].status == "Failed"
    assert state.agents["reviewer"].activity == "Codex reported an error"


def test_run_started_sets_current_agent_task_and_activity():
    state = WorkflowState()

    state.consume("run_started", {
        "initial_agent": "architect", "prompt": "Build the feature", "workspace": ".",
    })

    assert state.status == "Running"
    assert state.agents["architect"].status == "Working"
    assert state.agents["architect"].task == "Build the feature"
    assert state.agents["architect"].activity == "Starting task"


def test_expanded_tree_shows_agent_details_and_refreshes_after_progress():
    pytest.importorskip("textual")
    from agent_orchestrator.tui import OrchestratorTUI

    async def exercise_tree():
        app = OrchestratorTUI(prompt="Build API")
        async with app.run_test():
            app.state.consume("agent_registered", {"name": "coder", "role": "Developer"})
            app.state.consume("run_started", {
                "initial_agent": "coder", "prompt": "Build API", "workspace": ".",
            })
            app._render_tree()
            tree = app.query_one("#agent-tree")
            agent_node = tree.root.children[0]

            assert agent_node.is_expanded
            assert agent_node.label.plain.startswith("coder [Working]")
            assert "Build API" in agent_node.label.plain
            assert [child.label.plain for child in agent_node.children] == [
                "Task: Build API", "Current: Starting task", "Role: Developer",
            ]

            # Progress arrives through the event queue and triggers the same tree
            # refresh path used by the running TUI.
            app.events.put(("agent_progress", {"agent": "coder", "text": "Indexing source files"}))
            app._drain_events()
            agent_node = app.query_one("#agent-tree").root.children[0]
            assert agent_node.label.plain.startswith("coder [Working]")
            assert agent_node.children[1].label.plain == "Current: Indexing source files"

    asyncio.run(exercise_tree())
