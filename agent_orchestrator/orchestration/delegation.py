"""Delegation protocol and execution manager for multi-agent workflows."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

if TYPE_CHECKING:
    from agent_orchestrator.orchestration.agent import Agent
    from agent_orchestrator.orchestration.orchestrator import Orchestrator


@dataclass
class DelegationRequest:
    target_agent: str
    task: str
    context: Optional[str] = None
    raw_match: str = ""
    model: Optional[str] = None
    reasoning_effort: Optional[str] = None


@dataclass
class AgentCreationRequest:
    name: str
    role: str
    task: str
    system_prompt: Optional[str] = None
    model: Optional[str] = None
    reasoning_effort: Optional[str] = None


class DelegationManager:
    """Parses, validates, and dispatches delegation tasks between agents."""

    # Regex patterns for various delegation styles
    XML_PATTERN_A = re.compile(r"<delegate\s+(?P<attrs>[^>]*)>(?P<task>.*?)</delegate>", re.DOTALL | re.IGNORECASE)
    XML_PATTERN_B = re.compile(r"<delegate\s+(?P<attrs>[^>]*)/>", re.DOTALL | re.IGNORECASE)
    ATTRIBUTE_PATTERN = re.compile(r"([\w-]+)\s*=\s*([\"'])(.*?)\2", re.DOTALL)
    TOOL_CALL_PATTERN = re.compile(
        r"TOOL_CALL:\s*delegate_task\s*\(\s*(?P<args>\{.*?\})\s*\)",
        re.DOTALL | re.IGNORECASE,
    )
    CREATE_AGENT_PATTERN = re.compile(
        r"TOOL_CALL:\s*create_agent\s*\(\s*(?P<args>\{.*?\})\s*\)",
        re.DOTALL | re.IGNORECASE,
    )

    def __init__(self, max_depth: int = 5) -> None:
        self.max_depth = max_depth

    def _attributes(self, text: str) -> Dict[str, str]:
        return {key.lower().replace("-", "_"): value for key, _, value in self.ATTRIBUTE_PATTERN.findall(text)}

    def _delegation_request(self, attrs_text: str, task: str, raw_match: str) -> Optional[DelegationRequest]:
        attrs = self._attributes(attrs_text)
        target = attrs.get("target") or attrs.get("agent")
        task_text = attrs.get("task") or attrs.get("prompt") or task
        if not target or not task_text:
            return None
        return DelegationRequest(
            target_agent=target.strip(),
            task=task_text.strip(),
            context=attrs.get("context"),
            raw_match=raw_match,
            model=attrs.get("model"),
            reasoning_effort=attrs.get("reasoning_effort") or attrs.get("effort"),
        )

    def parse_delegations(self, text: str) -> List[DelegationRequest]:
        """Extracts any delegation directives from agent response text."""
        requests: List[DelegationRequest] = []

        # 1. XML tags can specify model and reasoning effort with attributes.
        for match in self.XML_PATTERN_A.finditer(text):
            request = self._delegation_request(match.group("attrs"), match.group("task"), match.group(0))
            if request:
                requests.append(request)

        # 2. Self-closing XML tags <delegate agent="agent" prompt="..."/>
        for match in self.XML_PATTERN_B.finditer(text):
            # Avoid duplicate if already matched
            if not any(r.raw_match == match.group(0) for r in requests):
                request = self._delegation_request(match.group("attrs"), "", match.group(0))
                if request:
                    requests.append(request)

        # 3. Tool call pattern: TOOL_CALL: delegate_task({...})
        for match in self.TOOL_CALL_PATTERN.finditer(text):
            try:
                args = json.loads(match.group("args"))
                target = args.get("target_agent") or args.get("target") or args.get("agent")
                task = args.get("task") or args.get("prompt")
                if target and task:
                    requests.append(
                        DelegationRequest(
                            target_agent=str(target).strip(),
                            task=str(task).strip(),
                            context=args.get("context"),
                            raw_match=match.group(0),
                            model=args.get("model"),
                            reasoning_effort=args.get("reasoning_effort", args.get("effort")),
                        )
                    )
            except Exception:
                continue

        # 4. JSON block format: ```json { "action": "delegate", "target": "...", "task": "..." } ```
        json_blocks = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        for block in json_blocks:
            try:
                data = json.loads(block)
                if isinstance(data, dict):
                    if data.get("action") == "delegate" or "delegate" in data:
                        delegate_data = data.get("delegate") if isinstance(data.get("delegate"), dict) else {}
                        target = data.get("target") or delegate_data.get("target")
                        task = data.get("task") or data.get("prompt") or delegate_data.get("task")
                        if target and task and not any(r.target_agent == target and r.task == task for r in requests):
                            requests.append(
                                DelegationRequest(
                                    target_agent=str(target).strip(),
                                    task=str(task).strip(),
                                    context=data.get("context"),
                                    raw_match=block,
                                    model=data.get("model") or delegate_data.get("model"),
                                    reasoning_effort=data.get("reasoning_effort", data.get("effort", delegate_data.get("reasoning_effort", delegate_data.get("effort")))),
                                )
                            )
            except Exception:
                continue

        return requests

    def parse_agent_creations(self, text: str) -> List[AgentCreationRequest]:
        """Parse explicit requests to create and run a new worker agent."""
        requests: List[AgentCreationRequest] = []
        for match in self.CREATE_AGENT_PATTERN.finditer(text):
            try:
                data = json.loads(match.group("args"))
                name = str(data.get("name", "")).strip()
                role = str(data.get("role", "")).strip()
                task = str(data.get("task", data.get("prompt", ""))).strip()
                if name and role and task:
                    requests.append(AgentCreationRequest(
                        name=name,
                        role=role,
                        task=task,
                        system_prompt=str(data["system_prompt"]).strip() if data.get("system_prompt") else None,
                        model=data.get("model"),
                        reasoning_effort=data.get("reasoning_effort", data.get("effort")),
                    ))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
        return requests

    def get_delegation_instructions(self, source_agent: Agent, available_agents: Dict[str, Agent]) -> str:
        """Returns delegation documentation formatted for an agent's prompt."""
        allowed_targets: List[str] = []
        for name in available_agents:
            if name == source_agent.name:
                continue
            if "*" in source_agent.can_delegate_to or name in source_agent.can_delegate_to:
                allowed_targets.append(name)

        lines = ["Task Delegation:"]
        if allowed_targets:
            lines.extend([
                f"You may delegate sub-tasks to these configured agents: {allowed_targets}",
                'Use `<delegate target="agent_name" model="gpt-6-luna" reasoning_effort="high">Specific task</delegate>` or ',
                '`TOOL_CALL: delegate_task({"target_agent":"agent_name","task":"...","model":"gpt-6-sol","reasoning_effort":"medium"})`.',
                "Choose a model and reasoning_effort for each delegated task when useful; omitted values use the child agent's own configuration or Codex default.",
            ])
        else:
            lines.append("You do not have permission to delegate to configured agents.")
        if source_agent.can_create_agents:
            lines.extend([
                "You may create a specialized worker when the configured agents are not a good fit.",
                'Use `TOOL_CALL: create_agent({"name":"unique_name","role":"Worker role","task":"Specific task","model":"gpt-6-luna","reasoning_effort":"high"})`.',
                "Choose the new worker's model and reasoning_effort when useful. It inherits your engine and tool permissions, but not your model choice.",
                "Use a unique name containing only letters, numbers, underscores, or hyphens.",
            ])
        return "\n".join(lines)

    async def execute_delegation(
        self,
        orchestrator: Orchestrator,
        source_agent: Agent,
        request: DelegationRequest,
        call_stack: List[str],
    ) -> str:
        """Validates and executes a delegation request, guarding against cycles and recursion limits."""
        target_name = request.target_agent
        if target_name not in orchestrator.agents:
            return f"Delegation Error: Target agent '{target_name}' does not exist. Available: {list(orchestrator.agents.keys())}"

        # Permission check
        if "*" not in source_agent.can_delegate_to and target_name not in source_agent.can_delegate_to:
            return (
                f"Delegation Error: Agent '{source_agent.name}' is not allowed to delegate to '{target_name}'. "
                f"Allowed: {source_agent.can_delegate_to}"
            )

        # Depth check
        if len(call_stack) >= self.max_depth:
            return f"Delegation Error: Maximum delegation depth of {self.max_depth} reached. Call stack: {' -> '.join(call_stack)}"

        # Cycle check
        if target_name in call_stack:
            return f"Delegation Warning: Cycle detected in call stack ({' -> '.join(call_stack + [target_name])}). Halting delegation loop."

        target_agent = orchestrator.agents[target_name]
        updated_stack = call_stack + [target_name]

        # Build child prompt with context if provided
        child_prompt = request.task
        if request.context:
            child_prompt = f"Context from delegating agent '{source_agent.name}':\n{request.context}\n\nTask:\n{request.task}"

        result_text = await orchestrator.run_agent_turn(
            agent=target_agent,
            prompt=child_prompt,
            call_stack=updated_stack,
            model=request.model,
            reasoning_effort=request.reasoning_effort,
        )

        return result_text
