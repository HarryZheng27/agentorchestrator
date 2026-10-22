"""Programmatic example showing how to build and execute a multi-agent team in Python."""

from __future__ import annotations

import asyncio
from pathlib import Path

from agent_orchestrator import (
    Agent,
    MockEngine,
    Orchestrator,
    WorkspaceManager,
)


def custom_architect_response(prompt: str, kwargs: dict) -> str:
    if "results from your delegated" in prompt:
        return (
            "Architecture Review Complete: The coder created the requested module and "
            "the reviewer verified everything works cleanly. Modification is finished!"
        )
    return (
        "I have analyzed the project structure. Delegating implementation to coder:\n"
        "<delegate target=\"coder\">Create math_utils.py with add(a, b) and multiply(a, b)</delegate>"
    )


def custom_coder_response(prompt: str, kwargs: dict) -> str:
    if "results from your delegated" in prompt:
        return "Coder: Implementation and verification completed cleanly."
    if "Tool execution results" in prompt:
        return (
            "Created math_utils.py. Now delegating verification to reviewer:\n"
            "<delegate target=\"reviewer\">Review math_utils.py and confirm functions</delegate>"
        )
    return (
        "I will now write the math_utils.py file.\n"
        "TOOL_CALL: write_file({\"path\": \"math_utils.py\", \"content\": \"def add(a, b):\\n    return a + b\\n\\ndef multiply(a, b):\\n    return a * b\\n\"})"
    )


def custom_reviewer_response(prompt: str, kwargs: dict) -> str:
    if "Tool execution results" in prompt:
        return "Verified: math_utils.py is properly written and ready."
    return (
        "TOOL_CALL: read_file({\"path\": \"math_utils.py\"})"
    )


async def main() -> None:
    # 1. Setup workspace (can target any local project directory or GitHub repo)
    workspace_dir = Path("./sample_workspace")
    workspace = WorkspaceManager(project_dir=workspace_dir)

    # 2. Initialize orchestrator
    orchestrator = Orchestrator(
        workspace=workspace,
        on_event=lambda evt, data: print(f"[{evt}] {data}"),
    )

    # 3. Register agents (all powered by Codex CLI; using MockEngine here for demo)
    architect = Agent(
        name="architect",
        role="System Architect",
        engine=MockEngine(name="mock_architect", response_handler=custom_architect_response),
        system_prompt="You plan and orchestrate project modifications.",
        can_delegate_to=["coder"],
    )

    coder = Agent(
        name="coder",
        role="Software Developer",
        engine=MockEngine(name="mock_coder", response_handler=custom_coder_response),
        system_prompt="You implement code changes using workspace tools.",
        can_delegate_to=["reviewer"],
    )

    reviewer = Agent(
        name="reviewer",
        role="QA Reviewer",
        engine=MockEngine(name="mock_reviewer", response_handler=custom_reviewer_response),
        system_prompt="You verify and review code modifications.",
        can_delegate_to=[],
    )

    orchestrator.register_agent(architect)
    orchestrator.register_agent(coder)
    orchestrator.register_agent(reviewer)

    # 4. Run the team!
    result = await orchestrator.run(
        prompt="Add math_utils module with basic arithmetic functions.",
        initial_agent="architect",
    )

    print("\n--- Final Workflow Result ---")
    print(result)

    # Clean up sample workspace
    import shutil
    if workspace_dir.exists():
        shutil.rmtree(workspace_dir)


if __name__ == "__main__":
    asyncio.run(main())
