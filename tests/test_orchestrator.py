"""End-to-end integration tests for Orchestrator multi-agent delegation and workspace modifications."""

import json
import pytest
from pathlib import Path

from agent_orchestrator import (
    Agent,
    MockEngine,
    Orchestrator,
    WorkspaceManager,
    build_orchestrator,
    load_project_config,
)


def test_end_to_end_delegation_and_file_modification(tmp_path: Path):
    import asyncio

    async def _runner():
        # Setup workspace
        workspace = WorkspaceManager(project_dir=tmp_path)
        workspace.write_file("config.json", '{"version": "1.0.0"}')

        events = []
        orchestrator = Orchestrator(
            workspace=workspace,
            on_event=lambda evt, data: events.append((evt, data)),
        )

        # 1. Lead Agent delegates to Coder
        def lead_handler(prompt: str, kwargs: dict) -> str:
            if "results from your delegated" in prompt:
                return "Lead Agent: Workflow completed successfully based on coder results."
            return (
                "I will delegate the version bump to coder.\n"
                "<delegate target=\"coder\">Bump version to 2.0.0</delegate>"
            )

        # 2. Coder uses patch_file tool
        def coder_handler(prompt: str, kwargs: dict) -> str:
            if "Tool execution results" in prompt:
                return "Coder: Successfully bumped version in config.json."
            return (
                "TOOL_CALL: patch_file({\"path\": \"config.json\", \"target_snippet\": \"1.0.0\", \"replacement_snippet\": \"2.0.0\"})"
            )

        lead_agent = Agent(
            name="lead",
            role="Team Lead",
            engine=MockEngine(name="mock_lead", response_handler=lead_handler),
            can_delegate_to=["coder"],
        )

        coder_agent = Agent(
            name="coder",
            role="Coder",
            engine=MockEngine(name="mock_coder", response_handler=coder_handler),
            can_delegate_to=[],
        )

        orchestrator.register_agent(lead_agent)
        orchestrator.register_agent(coder_agent)

        result = await orchestrator.run("Upgrade the project version", initial_agent="lead")

        # Verify result
        assert "Workflow completed successfully" in result

        # Verify the file was actually modified in the workspace
        updated_config = workspace.read_file("config.json")
        assert '"version": "2.0.0"' in updated_config

        # Verify delegation event was recorded
        dispatched_events = [e for e, d in events if e == "delegation_dispatched"]
        assert len(dispatched_events) == 1

    asyncio.run(_runner())


def test_load_project_yaml_config(tmp_path: Path):
    yaml_content = """
name: "Test Project"
description: "Test description"
workspace:
  project_dir: "."
initial_agent: "architect"
agents:
  - name: "architect"
    role: "Architect"
    engine: "codex"
    can_delegate_to: ["developer"]
  - name: "developer"
    role: "Developer"
    engine: "codex"
    can_delegate_to: []
"""
    cfg_file = tmp_path / "test_config.yaml"
    cfg_file.write_text(yaml_content, encoding="utf-8")

    cfg = load_project_config(cfg_file)
    assert cfg.name == "Test Project"
    assert len(cfg.agents) == 2
    assert cfg.agents[0].engine == "codex"
    assert cfg.agents[1].engine == "codex"

    orchestrator = build_orchestrator(cfg, workspace_override=tmp_path)
    assert "architect" in orchestrator.agents
    assert "developer" in orchestrator.agents
    assert orchestrator.workspace.project_dir == tmp_path.resolve()


def test_disallowed_tool_is_rejected_before_execution(tmp_path: Path):
    import asyncio

    workspace = WorkspaceManager(project_dir=tmp_path, allow_shell_commands=True)
    events = []
    prompts = []

    def handler(prompt: str, kwargs: dict) -> str:
        prompts.append(prompt)
        if "Tool execution results" in prompt:
            return "Done"
        return 'TOOL_CALL: write_file({"path": "blocked.txt", "content": "no"})'

    orchestrator = Orchestrator(workspace=workspace, on_event=lambda evt, data: events.append((evt, data)))
    orchestrator.register_agent(Agent(
        name="reader", role="Reader", engine=MockEngine(response_handler=handler),
        allowed_tools=["read_file"],
    ))
    assert asyncio.run(orchestrator.run("Try a write")) == "Done"
    assert not (tmp_path / "blocked.txt").exists()
    assert "not allowed" in prompts[1]
    assert not any(evt == "file_edited" for evt, _ in events)


def test_tool_parser_handles_nested_json_and_quoted_braces(tmp_path: Path):
    orchestrator = Orchestrator(workspace=WorkspaceManager(project_dir=tmp_path))
    arguments = {"path": "data.json", "content": '{"nested": {"value": "}) and \\\"quoted\\\""}}'}
    named = f"TOOL_CALL: write_file({json.dumps(arguments)})"
    fenced = "```json\n" + json.dumps({"tool": "write_file", "arguments": arguments}) + "\n```"

    assert orchestrator._parse_tool_calls(named) == [("write_file", arguments)]
    assert orchestrator._parse_tool_calls(fenced) == [("write_file", arguments)]


def test_tool_parser_ignores_markers_inside_arguments_and_deduplicates(tmp_path: Path):
    orchestrator = Orchestrator(workspace=WorkspaceManager(project_dir=tmp_path))
    arguments = {
        "path": "example.txt",
        "content": 'Example text: TOOL_CALL: git_status({})',
    }
    named = f"TOOL_CALL: write_file({json.dumps(arguments)})"
    fenced = "```json\n" + json.dumps({"tool": "write_file", "arguments": arguments}) + "\n```"

    assert orchestrator._parse_tool_calls(named) == [("write_file", arguments)]
    assert orchestrator._parse_tool_calls(named + "\n" + fenced) == [("write_file", arguments)]
    assert orchestrator._parse_tool_calls("For example, " + named) == []


def test_shell_policy_loaded_from_config(tmp_path: Path):
    config_file = tmp_path / "shell.json"
    config_file.write_text(json.dumps({
        "name": "Trusted", "workspace": {"allow_shell_commands": True},
        "agents": [],
    }), encoding="utf-8")
    config = load_project_config(config_file)
    assert build_orchestrator(config, workspace_override=tmp_path).workspace.allow_shell_commands is True
