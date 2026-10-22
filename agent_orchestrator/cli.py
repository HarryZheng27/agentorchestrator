"""Command-line interface for the Agent Orchestrator."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from agent_orchestrator.config import (
    AgentConfig,
    ProjectConfig,
    WorkspaceConfig,
    build_orchestrator,
    load_project_config,
)
from agent_orchestrator.engines.codex import CodexCLIEngine
from agent_orchestrator.git_manager import GitManager
from agent_orchestrator.workspace import WorkspaceManager

# Default template for modifying existing codebases
DEFAULT_MODIFY_CONFIG = """# Multi-Agent Workflow for Modifying Existing Projects
name: "Codebase Modifier Team"
description: "Coordinates multiple Codex CLI agents to inspect, refactor, and test existing codebases."

workspace:
  project_dir: "."
  auto_clone: true
  allow_shell_commands: false

initial_agent: "architect"
max_delegation_depth: 4
max_turns_per_agent: 6

agents:
  - name: "architect"
    role: "System Architect"
    engine: "codex"
    extra_args: ["--model", "gpt-6-luna", "-c", 'model_reasoning_effort="high"']
    system_prompt: |
      You are the Lead Software Architect.
      Your responsibility is to analyze the existing project codebase and user request.
      Formulate a step-by-step implementation plan.
      Follow this delegation sequence:
      1. Analyze the request and codebase, then make a step-by-step implementation plan.
      2. Delegate implementation to 'coder' using:
        <delegate target="coder" model="gpt-6-sol" reasoning_effort="high">Precise coding instructions</delegate>
      3. After implementation, delegate test coverage inspection and validation recommendations to 'test_analyst' using:
        <delegate target="test_analyst" model="gpt-6-luna" reasoning_effort="high">Inspect relevant tests and recommend validation; do not execute commands</delegate>
      4. Then delegate a read-only review of the changes to 'reviewer' using:
        <delegate target="reviewer" model="gpt-6-luna" reasoning_effort="high">Review the diff and report concrete issues; do not edit files or execute commands</delegate>
      5. Resolve any reported issues by delegating follow-up implementation to 'coder', then request another read-only review if needed.
      Choose the model and reasoning effort for each delegated task using the optional model and reasoning_effort attributes.
      Synthesize the final summary for the user.
    can_delegate_to: ["coder", "test_analyst", "reviewer"]
    allowed_tools: ["read_file", "list_files", "search_code", "git_status", "git_diff"]

  - name: "coder"
    role: "Code Implementer"
    engine: "codex"
    system_prompt: |
      You are an expert developer utilizing Codex.
      Your task is to inspect the codebase and modify files to fulfill the architect's request.
      Use 'read_file', 'write_file', and 'patch_file' to implement changes cleanly.
      Explain what edits you performed.
    can_delegate_to: []
    allowed_tools: ["read_file", "write_file", "patch_file", "list_files", "search_code"]

  - name: "reviewer"
    role: "Read-only Code Reviewer"
    engine: "codex"
    system_prompt: |
      You are a meticulous, read-only code reviewer.
      Inspect the requested changes and git diff for correctness, regressions, and missing edge cases.
      Report concrete findings with file and line references, ordered by severity.
      Do not edit files or execute shell commands. The test_analyst handles test coverage and validation recommendations.
    can_delegate_to: []
    allowed_tools: ["read_file", "list_files", "search_code", "git_status", "git_diff"]

  - name: "test_analyst"
    role: "Test Coverage Analyst"
    engine: "codex"
    system_prompt: |
      You are a read-only test coverage analyst.
      Inspect the relevant test files and implementation to identify existing coverage, gaps, and the most useful validation commands.
      Recommend focused tests or commands, but do not edit files or execute commands.
      Report your findings and recommendations to the architect.
    can_delegate_to: []
    allowed_tools: ["read_file", "list_files", "search_code", "git_status", "git_diff"]
"""


_active_agent: Optional[str] = None


def cli_event_logger(event_type: str, data: Dict[str, Any]) -> None:
    """Print workflow activity for the orchestrator, subagents, and tools."""
    global _active_agent

    if event_type == "run_started":
        _active_agent = str(data.get("initial_agent", "")) or None
    elif event_type in {"agent_turn_start", "agent_response"}:
        agent_name = str(data.get("agent", ""))
        if agent_name:
            _active_agent = agent_name
    elif event_type == "run_completed":
        _active_agent = None
    _print_cli_event(event_type, data)


def _format_tool_call_summary(tool: str, args: Dict[str, Any]) -> str:
    """Format a concise human-readable description of a tool invocation."""
    if tool == "read_file":
        path = Path(str(args.get("path", "")).replace("\\", "/")).name or str(args.get("path", ""))
        start = args.get("start_line")
        end = args.get("end_line")
        if start is not None or end is not None:
            return f"Reading {path} (lines {start or 1}–{end or 'end'})"
        return f"Reading {path}"
    if tool == "write_file":
        path = Path(str(args.get("path", "")).replace("\\", "/")).name or str(args.get("path", ""))
        return f"Writing {path}"
    if tool == "patch_file":
        path = Path(str(args.get("path", "")).replace("\\", "/")).name or str(args.get("path", ""))
        return f"Patching {path}"
    if tool == "list_files":
        sub_dir = args.get("sub_dir") or "."
        pattern = args.get("pattern") or "*"
        return f"Listing files in {sub_dir} ({pattern})"
    if tool == "search_code":
        query = args.get("query", "")
        pattern = args.get("pattern")
        if pattern:
            return f"Searching code for '{query}' ({pattern})"
        return f"Searching code for '{query}'"
    if tool == "run_command":
        return f"Running command: {args.get('command', '')}"
    if tool == "git_status":
        return "Checking git status"
    if tool == "git_diff":
        return "Inspecting git diff"
    if tool == "git_create_branch":
        return f"Creating git branch '{args.get('branch_name', '')}'"
    return f"Running tool {tool}"


def _print_cli_event(event_type: str, data: Dict[str, Any]) -> None:
    """Print concise workflow, subagent, command, file, and response activity."""
    def color(text: str, code: str) -> str:
        if getattr(sys.stdout, "isatty", lambda: False)():
            return f"\033[{code}m{text}\033[0m"
        return text

    def file_names(paths: Any) -> str:
        if not isinstance(paths, (list, tuple)):
            paths = [paths]
        names = sorted({Path(str(path).replace("\\", "/")).name for path in paths if path})
        return ", ".join(names) if names else "none"

    def agent_prefix(data_dict: Dict[str, Any]) -> str:
        agent = str(data_dict.get("agent") or _active_agent or "").strip()
        if not agent:
            return "  "
        return f"  [{color(agent, '36')}] "

    if event_type == "run_started":
        print(f"\nStarting with {data['initial_agent']}  ·  {data['workspace']}")
        print(f"Task: {data['prompt']}\n")
    elif event_type == "agent_created":
        creator = data.get("created_by")
        role = data.get("role", "")
        name = data.get("name", "agent")
        model = data.get("model")
        effort = data.get("reasoning_effort")
        meta_parts = [p for p in [role, f"model={model}" if model else "", f"effort={effort}" if effort else ""] if p]
        meta_str = f" ({', '.join(meta_parts)})" if meta_parts else ""
        by_str = f" by {creator}" if creator else ""
        print(f"\n{color('Created subagent', '1;35')} {color(str(name), '1;36')}{meta_str}{by_str}")
        if data.get("task"):
            print(f"  Task: {data['task']}")
    elif event_type == "delegation_dispatched":
        from_agent = data.get("from_agent", "agent")
        to_agent = data.get("to_agent", "subagent")
        role = data.get("role")
        model = data.get("model")
        effort = data.get("reasoning_effort")
        meta_parts = [p for p in [role, f"model={model}" if model else "", f"effort={effort}" if effort else ""] if p]
        meta_str = f" ({', '.join(meta_parts)})" if meta_parts else ""
        print(f"\n{color(str(from_agent), '1;36')} → {color(str(to_agent), '1;33')}{meta_str}  ·  {data.get('task', '')}")
    elif event_type == "agent_turn_start":
        agent = str(data.get("agent", "agent"))
        role = data.get("role")
        call_stack = data.get("call_stack") or []
        prompt = str(data.get("prompt") or "").strip()
        role_str = f" ({role})" if role else ""
        stack_str = f"  [{' → '.join(str(a) for a in call_stack)}]" if len(call_stack) > 1 else ""
        print(f"\n{color(agent, '1;36')}{role_str} working{stack_str}")
        if len(call_stack) > 1 and prompt:
            single_line = " ".join(prompt.splitlines())
            if len(single_line) > 120:
                single_line = single_line[:119] + "…"
            print(f"  Task: {single_line}")
    elif event_type == "agent_reasoning":
        text = str(data.get("text") or "").strip()
        if text:
            single_line = " ".join(text.splitlines())
            if len(single_line) > 140:
                single_line = single_line[:139] + "…"
            print(f"{agent_prefix(data)}Thinking: {single_line}")
    elif event_type == "tool_invoked":
        tool = str(data.get("tool") or "")
        args = data.get("args") if isinstance(data.get("args"), dict) else {}
        if tool and tool not in {"read_file", "list_files", "search_code", "run_command", "write_file", "patch_file"}:
            print(f"{agent_prefix(data)}{_format_tool_call_summary(tool, args)}")
    elif event_type == "command_run":
        print(f"{agent_prefix(data)}{color('$ ' + str(data.get('command', '')), '36')}")
    elif event_type == "file_accessed":
        print(f"{agent_prefix(data)}Read: {file_names(data.get('paths', []))}")
    elif event_type == "file_edited":
        print(f"{agent_prefix(data)}Edited: {file_names([data.get('path', '')])}")
        in_hunk = False
        for line in str(data.get("diff") or "").splitlines():
            if line.startswith("@@"):
                in_hunk = True
                continue
            if not in_hunk and line.startswith(("+++ ", "--- ")):
                continue
            if line.startswith("+"):
                print(f"    {color(line, '32')}")
            elif line.startswith("-"):
                print(f"    {color(line, '31')}")
    elif event_type == "agent_output":
        text = str(data.get("text") or "").strip()
        if text:
            print(f"{agent_prefix(data)}{text}")
    elif event_type == "agent_final_response":
        agent = str(data.get("agent", "Agent"))
        role = data.get("role")
        role_str = f" ({role})" if role else ""
        print(f"\n{color(agent + role_str + ' completed:', '1;32')}")
        print(data.get("text", ""))
    elif event_type == "run_completed":
        print("\nWorkflow finished.")


def cmd_run(args: argparse.Namespace) -> None:
    """Executes a multi-agent run from configuration."""
    if args.config:
        config = load_project_config(args.config)
    else:
        # Create default config in memory
        import tempfile
        tmp = tempfile.NamedTemporaryFile("w", delete=False, suffix=".yaml", encoding="utf-8")
        tmp.write(DEFAULT_MODIFY_CONFIG)
        tmp.close()
        config = load_project_config(tmp.name)
        os.unlink(tmp.name)

    orchestrator = build_orchestrator(
        config=config,
        workspace_override=args.project_dir,
        repo_override=args.repo,
        on_event=cli_event_logger,
    )

    prompt = args.prompt
    if not prompt:
        print("Error: --prompt is required.")
        sys.exit(1)

    initial_agent = args.agent or config.initial_agent

    async def run_session() -> None:
        current_prompt = prompt
        try:
            while True:
                result = await orchestrator.run(prompt=current_prompt, initial_agent=initial_agent)
                print("\n[Result Summary]:\n")
                print(result)

                if not sys.stdin.isatty():
                    break

                print("\nSession context is retained. Enter another request, or type /exit to end the session.")
                try:
                    current_prompt = input("\nYou: ").strip()
                except EOFError:
                    break
                if current_prompt.lower() in {"/exit", "exit", "quit"}:
                    break
                if not current_prompt:
                    continue
        finally:
            orchestrator.close()

    asyncio.run(run_session())


def cmd_inspect(args: argparse.Namespace) -> None:
    """Inspects the workspace, git repo, CLI availability, and configured agents."""
    ws = WorkspaceManager(
        project_dir=args.project_dir,
        github_repo=args.repo,
        auto_clone=True,
    )
    summary = ws.get_summary()

    print("\n=== Agent Orchestrator Environment & Workspace Status ===", flush=True)
    print(f"Workspace Directory : {summary['project_dir']}", flush=True)
    print(f"Files Detected      : {summary['files_count']} files", flush=True)

    git_info = summary["git"]
    if git_info.get("is_git_repo"):
        print(f"Git Repository      : Yes", flush=True)
        print(f"Current Branch      : {git_info.get('branch')}", flush=True)
        print(f"Commit HEAD         : {git_info.get('commit')}", flush=True)
        print(f"Dirty Changes       : {'Yes (uncommitted files)' if git_info.get('is_dirty') else 'No (clean)'}", flush=True)
        if git_info.get("remotes"):
            print("Git Remotes         :", flush=True)
            for r in git_info["remotes"]:
                print(f"  - {r}", flush=True)
    else:
        print(f"Git Repository      : No (local non-git directory)", flush=True)

    print("\n--- CLI Engine Executable Discovery ---", flush=True)
    found = CodexCLIEngine().resolved_cli
    status = f"Available ({found})" if found != "codex" else "Not found (specify cli_path or install)"
    print(f"  - Codex   : {status}", flush=True)

    # Check for agent config
    config_file = args.config
    if not config_file:
        for candidate in ["orchestrator.yaml", "orchestrator.yml", "examples/multi_cli_dev_team.yaml"]:
            if Path(candidate).exists():
                config_file = candidate
                break

    if config_file and Path(config_file).exists():
        try:
            cfg = load_project_config(config_file)
            print(f"\n--- Configured Agents from '{config_file}' ({len(cfg.agents)} agents) ---", flush=True)
            print(f"Team / Project Name : {cfg.name}", flush=True)
            print(f"Initial Lead Agent  : {cfg.initial_agent or (cfg.agents[0].name if cfg.agents else 'None')}", flush=True)
            for ag in cfg.agents:
                delegates = ", ".join(ag.can_delegate_to) if ag.can_delegate_to else "None"
                tools = ", ".join(ag.allowed_tools) if ag.allowed_tools else "All default tools"
                print(f"\n  [Agent: {ag.name}]", flush=True)
                print(f"    Role         : {ag.role}", flush=True)
                print(f"    Engine       : {ag.engine}", flush=True)
                print(f"    Can Delegate : {delegates}", flush=True)
                print(f"    Tools        : {tools}", flush=True)
        except Exception as e:
            print(f"\nWarning: Could not parse config '{config_file}': {e}", flush=True)
    else:
        print("\n--- Configured Agents ---", flush=True)
        print("  No agent config file provided. Use '--config <file.yaml>' to inspect team configuration,", flush=True)
        print("  or run 'agent-orchestrator init' to generate a starter config.", flush=True)


def cmd_init(args: argparse.Namespace) -> None:
    """Initializes a new multi-agent project configuration."""
    target_path = Path(args.output or "orchestrator.yaml").resolve()
    if target_path.exists() and not args.force:
        print(f"Error: {target_path} already exists. Use --force to overwrite.")
        sys.exit(1)

    target_path.write_text(DEFAULT_MODIFY_CONFIG, encoding="utf-8")
    print(f"Created multi-agent project configuration template at: {target_path}")
    print("You can run it with:")
    print(f"  agent-orchestrator run --config {target_path.name} --prompt \"Your project goal here\"")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="agent-orchestrator",
        description="Multi-Agent AI Orchestrator powered by Codex CLI with delegation and codebase modification.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # run command
    run_parser = subparsers.add_parser("run", help="Run multi-agent workflow on a project or GitHub repository")
    run_parser.add_argument("--config", "-c", type=str, help="Path to YAML/JSON multi-agent configuration file")
    run_parser.add_argument("--prompt", "-p", type=str, required=True, help="Task or instruction for the multi-agent team")
    run_parser.add_argument("--project-dir", "-d", type=str, help="Target project workspace directory to inspect or modify")
    run_parser.add_argument("--repo", "-r", type=str, help="GitHub repository URL or 'owner/repo' slug")
    run_parser.add_argument("--agent", "-a", type=str, help="Starting agent name (default: initial_agent from config)")

    # modify command (alias for running directly on an existing project)
    mod_parser = subparsers.add_parser("modify", help="Inspect and modify an existing project using multi-agent team")
    mod_parser.add_argument("--project-dir", "-d", type=str, required=True, help="Path to existing local project codebase")
    mod_parser.add_argument("--prompt", "-p", type=str, required=True, help="Description of modifications or bug fix needed")
    mod_parser.add_argument("--config", "-c", type=str, help="Optional custom multi-agent config file")
    mod_parser.add_argument("--repo", "-r", type=str, help="Optional GitHub repository URL")
    mod_parser.add_argument("--agent", "-a", type=str, help="Starting agent name")

    # inspect command
    inspect_parser = subparsers.add_parser("inspect", help="Inspect workspace, git status, and Codex CLI engine")
    inspect_parser.add_argument("--config", "-c", type=str, help="Path to YAML/JSON multi-agent configuration file to inspect")
    inspect_parser.add_argument("--project-dir", "-d", type=str, default=".", help="Project directory to inspect")
    inspect_parser.add_argument("--repo", "-r", type=str, help="GitHub repository URL or slug to inspect")

    # GUI command
    gui_parser = subparsers.add_parser("gui", help="Open the graphical workflow monitor")
    gui_parser.add_argument("--config", "-c", type=str, help="Path to YAML/JSON multi-agent configuration file")
    gui_parser.add_argument("--project-dir", "-d", type=str, help="Target project workspace directory")
    gui_parser.add_argument("--repo", "-r", type=str, help="GitHub repository URL or slug")
    gui_parser.add_argument("--prompt", "-p", type=str, default="", help="Optional task prompt to prefill")
    gui_parser.add_argument("--agent", "-a", type=str, help="Starting agent name")

    # Optional terminal interface, with the same launch inputs as the GUI.
    tui_parser = subparsers.add_parser("tui", help="Open the terminal workflow monitor")
    tui_parser.add_argument("--config", "-c", type=str, help="Path to YAML/JSON multi-agent configuration file")
    tui_parser.add_argument("--project-dir", "-d", type=str, help="Target project workspace directory")
    tui_parser.add_argument("--repo", "-r", type=str, help="GitHub repository URL or slug")
    tui_parser.add_argument("--prompt", "-p", type=str, default="", help="Optional task prompt to prefill")
    tui_parser.add_argument("--agent", "-a", type=str, help="Starting agent name")

    # init command
    init_parser = subparsers.add_parser("init", help="Create a starter multi-agent configuration file")
    init_parser.add_argument("--output", "-o", type=str, default="orchestrator.yaml", help="Destination YAML file path")
    init_parser.add_argument("--force", "-f", action="store_true", help="Overwrite if file exists")

    args = parser.parse_args()

    if args.command in ["run", "modify"]:
        cmd_run(args)
    elif args.command == "inspect":
        cmd_inspect(args)
    elif args.command == "gui":
        from agent_orchestrator.gui import launch_gui
        launch_gui(config=args.config, project_dir=args.project_dir, repo=args.repo, prompt=args.prompt, agent=args.agent)
    elif args.command == "tui":
        try:
            from agent_orchestrator.tui import launch_tui
        except ModuleNotFoundError as exc:
            if exc.name == "textual" or (exc.name or "").startswith("textual."):
                parser.exit(2, "The TUI requires Textual. Install it with: pip install 'agent-orchestrator[tui]'\n")
            raise
        launch_tui(config=args.config, project_dir=args.project_dir, repo=args.repo, prompt=args.prompt, agent=args.agent)
    elif args.command == "init":
        cmd_init(args)


if __name__ == "__main__":
    main()
