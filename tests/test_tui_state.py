"""Checks for the event projection used by the terminal monitor."""

from agent_orchestrator.tui_state import WorkflowState


def test_delegated_agent_is_nested_and_parent_resumes_after_completion():
    state = WorkflowState()
    state.consume("agent_registered", {"name": "architect", "role": "Lead"})
    state.consume("agent_registered", {"name": "coder", "role": "Developer"})
    state.consume("run_started", {"initial_agent": "architect", "workspace": "."})
    state.consume("agent_turn_start", {"agent": "architect", "prompt": "Build feature"})
    state.consume("delegation_dispatched", {
        "from_agent": "architect", "to_agent": "coder", "task": "Implement API",
    })

    assert state.agents["coder"].parent == "architect"
    assert state.agents["coder"].task == "Implement API"
    assert state.agents["architect"].status == "Delegating"

    state.consume("agent_final_response", {"agent": "coder"})
    assert state.agents["coder"].status == "Completed"
    assert state.agents["architect"].status == "Working"

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
