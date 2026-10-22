"""Small, UI-independent projection of orchestrator events for the TUI."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Deque, Dict, Optional


@dataclass
class AgentStatus:
    name: str
    role: str = ""
    parent: Optional[str] = None
    status: str = "Ready"
    task: str = ""


class WorkflowState:
    def __init__(self) -> None:
        self.agents: Dict[str, AgentStatus] = {}
        self.lines: Deque[str] = deque(maxlen=500)
        self.status = "Ready"
        self.final_response = ""
        self.outputs: Dict[str, str] = {}

    def agent(self, name: str) -> AgentStatus:
        if name not in self.agents:
            self.agents[name] = AgentStatus(name=name)
        return self.agents[name]

    @staticmethod
    def _brief(value: Any, limit: int = 160) -> str:
        text = " ".join(str(value or "").split())
        return text if len(text) <= limit else text[: limit - 1] + "…"

    @staticmethod
    def _names(paths: Any) -> str:
        if not isinstance(paths, (list, tuple)):
            paths = [paths]
        names = sorted({Path(str(path).replace("\\", "/")).name for path in paths if path})
        return ", ".join(names) if names else "none"

    def consume(self, event: str, data: Dict[str, Any]) -> Optional[str]:
        """Update agent state and return a concise line for the activity log."""
        line: Optional[str] = None
        if event == "agent_registered":
            agent = self.agent(str(data["name"]))
            agent.role = str(data.get("role") or "")
            line = f"Agent ready: {agent.name} ({agent.role})"
        elif event == "agent_created":
            agent = self.agent(str(data["name"]))
            agent.role = str(data.get("role") or agent.role)
            agent.parent = str(data.get("created_by") or "") or None
            agent.status = "Created"
            agent.task = self._brief(data.get("task"))
            line = f"Agent created: {agent.name} by {agent.parent or 'agent'}"
        elif event == "run_started":
            self.status = "Running"
            agent = self.agent(str(data["initial_agent"]))
            agent.parent = None
            line = f"Started in {data.get('workspace', '.')} with {agent.name}"
        elif event == "agent_turn_start":
            agent = self.agent(str(data["agent"]))
            stack = data.get("call_stack") or []
            if len(stack) > 1:
                agent.parent = str(stack[-2])
            agent.role = str(data.get("role") or agent.role)
            agent.status = "Working"
            if not agent.task:
                agent.task = self._brief(data.get("prompt"))
            line = f"{agent.name} started: {agent.task}"
        elif event == "agent_process_started":
            agent = self.agent(str(data.get("agent", "Agent")))
            agent.status = "Running Codex"
            details = []
            if data.get("model"):
                details.append(f"model {data['model']}")
            if data.get("reasoning_effort"):
                details.append(f"reasoning {data['reasoning_effort']}")
            if data.get("ephemeral"):
                details.append("ephemeral session")
            suffix = f" ({', '.join(details)})" if details else ""
            line = f"{agent.name}: Codex process started{suffix}"
        elif event == "agent_progress":
            agent = self.agent(str(data.get("agent", "Agent")))
            agent.status = "Working"
            line = f"{agent.name}: {self._brief(data.get('text'), 240)}"
        elif event == "agent_protocol_error":
            agent = self.agent(str(data.get("agent", "Agent")))
            agent.status = "Failed"
            line = f"{agent.name} Codex error: {self._brief(data.get('text'), 240)}"
        elif event == "agent_process_finished":
            agent = self.agent(str(data.get("agent", "Agent")))
            code = data.get("exit_code", 0)
            if code:
                agent.status = "Failed"
                line = f"{agent.name}: Codex exited with code {code}"
            else:
                agent.status = "Reviewing response"
                line = f"{agent.name}: Codex process finished; reading response"
        elif event == "agent_error":
            agent = self.agent(str(data.get("agent", "Agent")))
            agent.status = "Failed"
            line = f"{agent.name} error (exit {data.get('exit_code', '?')}): {self._brief(data.get('text'))}"
        elif event == "agent_diagnostic":
            diagnostic = self._brief(data.get("text"), 240)
            if diagnostic:
                line = f"{data.get('agent', 'Agent')} diagnostic: {diagnostic}"
        elif event == "delegation_dispatched":
            parent = self.agent(str(data["from_agent"]))
            child = self.agent(str(data["to_agent"]))
            child.parent = parent.name
            child.role = str(data.get("role") or child.role)
            child.status = "Delegated"
            child.task = self._brief(data.get("task"))
            parent.status = "Delegating"
            line = f"{parent.name} → {child.name}: {child.task}"
        elif event == "agent_final_response":
            agent = self.agent(str(data["agent"]))
            if agent.status != "Failed":
                agent.status = "Completed"
            if agent.parent and agent.parent in self.agents:
                self.agents[agent.parent].status = "Working"
            line = f"{agent.name} completed"
        elif event == "run_completed":
            self.status = "Failed" if any(agent.status == "Failed" for agent in self.agents.values()) else "Completed"
            self.final_response = str(data.get("final_response") or "")
            line = "Workflow completed"
        elif event == "file_accessed":
            line = f"{data.get('agent', 'Agent')} read: {self._names(data.get('paths', []))}"
        elif event == "file_edited":
            line = f"{data.get('agent', 'Agent')} edited: {self._names(data.get('path', ''))}"
        elif event == "command_run":
            line = f"{data.get('agent', 'Agent')} $ {self._brief(data.get('command'))}"
        elif event == "agent_reasoning":
            line = f"{data.get('agent', 'Agent')} thinking: {self._brief(data.get('text'))}"
        elif event == "agent_response":
            name = str(data.get("agent", "Agent"))
            self.outputs[name] = str(data.get("text") or "")
            line = f"{name}: {self._brief(data.get('text'))}"
        elif event == "agent_output":
            name = str(data.get("agent", "Agent"))
            text = str(data.get("text") or "")
            if data.get("replace", True):
                self.outputs[name] = text
            else:
                self.outputs[name] = self.outputs.get(name, "") + text
        elif event == "tool_invoked":
            tool = data.get("tool", "tool")
            if tool not in {"read_file", "list_files", "search_code", "run_command", "write_file", "patch_file"}:
                line = f"{data.get('agent', 'Agent')} started {tool}"
        if line:
            self.lines.append(line)
        return line
