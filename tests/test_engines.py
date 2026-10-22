"""Tests for CLI engines and factory."""

import pytest
from agent_orchestrator.engines import (
    CodexCLIEngine,
    MockEngine,
    get_engine,
)


def test_engine_factory():
    codex = get_engine("codex")
    assert isinstance(codex, CodexCLIEngine)

    mock = get_engine("mock")
    assert isinstance(mock, MockEngine)

    with pytest.raises(ValueError, match="Unknown engine"):
        get_engine("unsupported_engine_xyz")

    with pytest.raises(ValueError, match="Unknown engine"):
        get_engine("gemini")

    with pytest.raises(ValueError, match="Unknown engine"):
        get_engine("claude")


def test_mock_engine_execution():
    import asyncio
    engine = MockEngine(default_response="Task complete")
    res = asyncio.run(engine.execute(prompt="Hello"))
    assert res.is_success
    assert res.text == "Task complete"
    assert len(engine.recorded_calls) == 1
    assert engine.recorded_calls[0]["prompt"] == "Hello"


def test_engine_command_resolution():
    codex = CodexCLIEngine(cli_path="custom-codex")
    assert codex.resolved_cli == "custom-codex"
    cmd = codex._build_command("write code", ["--flag"])
    assert "custom-codex" in cmd[0]
    assert "--quiet" not in cmd
    assert "--flag" in cmd
    assert "write code" in cmd
