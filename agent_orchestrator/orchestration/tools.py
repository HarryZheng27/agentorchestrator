"""Tool registry and built-in workspace / git / delegation tools for agents."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from agent_orchestrator.workspace import WorkspaceManager


@dataclass
class ToolDefinition:
    name: str
    description: str
    parameters: Dict[str, Any]
    handler: Callable[..., Any]


class ToolRegistry:
    """Manages available tools for agents."""

    def __init__(self, workspace: Optional[WorkspaceManager] = None) -> None:
        self.workspace = workspace or WorkspaceManager()
        self.tools: Dict[str, ToolDefinition] = {}
        self._register_default_tools()

    def register(self, name: str, description: str, parameters: Dict[str, Any], handler: Callable[..., Any]) -> None:
        self.tools[name] = ToolDefinition(
            name=name,
            description=description,
            parameters=parameters,
            handler=handler,
        )

    def _register_default_tools(self) -> None:
        ws = self.workspace

        # File & Codebase Tools
        self.register(
            name="read_file",
            description="Reads lines from a file in the workspace.",
            parameters={"path": "str", "start_line": "optional int", "end_line": "optional int"},
            handler=ws.read_file,
        )
        self.register(
            name="write_file",
            description="Writes full content to a file in the workspace.",
            parameters={"path": "str", "content": "str", "overwrite": "optional bool"},
            handler=ws.write_file,
        )
        self.register(
            name="patch_file",
            description="Replaces a unique target code snippet with a replacement snippet in an existing file.",
            parameters={"path": "str", "target_snippet": "str", "replacement_snippet": "str"},
            handler=ws.patch_file,
        )
        self.register(
            name="list_files",
            description="Lists workspace files matching a glob pattern.",
            parameters={"sub_dir": "optional str", "pattern": "optional str", "recursive": "optional bool"},
            handler=ws.list_files,
        )
        self.register(
            name="search_code",
            description="Searches code across workspace files for a text or regex query.",
            parameters={"query": "str", "pattern": "optional str"},
            handler=ws.search_code,
        )
        self.register(
            name="run_command",
            description="Executes a shell command (e.g. pytest, npm test, linter) in the workspace directory.",
            parameters={"command": "str", "timeout_sec": "optional int"},
            handler=ws.run_command,
        )

        # Git Tools
        self.register(
            name="git_status",
            description="Returns git status of the project repository.",
            parameters={},
            handler=ws.git_manager.status,
        )
        self.register(
            name="git_diff",
            description="Returns the git diff of modifications in the repository.",
            parameters={"cached": "optional bool"},
            handler=ws.git_manager.diff,
        )
        self.register(
            name="git_create_branch",
            description="Creates and switches to a new git branch.",
            parameters={"branch_name": "str"},
            handler=ws.git_manager.create_branch,
        )

    def execute_tool(
        self, tool_name: str, arguments: Dict[str, Any], allowed_tools: Optional[List[str]] = None
    ) -> Any:
        """Executes a registered tool by name with arguments."""
        if allowed_tools is not None and tool_name not in allowed_tools:
            return f"Error: Tool '{tool_name}' is not allowed for this agent."
        if tool_name not in self.tools:
            return f"Error: Tool '{tool_name}' not found. Available tools: {list(self.tools.keys())}"
        try:
            handler = self.tools[tool_name].handler
            return handler(**arguments)
        except Exception as e:
            return f"Tool '{tool_name}' execution error: {str(e)}"

    def get_descriptions(self, allowed_tools: Optional[List[str]] = None) -> str:
        """Generates markdown documentation of available tools for system prompts."""
        lines = [
            "You can invoke workspace tools using JSON blocks or tool calls.",
            "Format: `TOOL_CALL: tool_name({\"arg\": \"val\"})` or JSON blocks with `{\"tool\": \"tool_name\", \"arguments\": {...}}`.",
            "",
            "Available Tools:",
        ]
        for name, tool in self.tools.items():
            if allowed_tools is not None and name not in allowed_tools:
                continue
            if name == "run_command" and not self.workspace.allow_shell_commands:
                continue
            lines.append(f"- `{name}({json.dumps(tool.parameters)})`: {tool.description}")
        return "\n".join(lines)
