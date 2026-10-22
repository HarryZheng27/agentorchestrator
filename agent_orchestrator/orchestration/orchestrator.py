"""Main Orchestrator runtime managing multi-agent execution loops, delegation, and workspace tools."""

from __future__ import annotations

import json
import re
import difflib
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from agent_orchestrator.engines import ENGINE_REGISTRY, get_engine
from agent_orchestrator.orchestration.agent import Agent
from agent_orchestrator.orchestration.delegation import DelegationManager
from agent_orchestrator.orchestration.memory import SharedBlackboard
from agent_orchestrator.orchestration.tools import ToolRegistry
from agent_orchestrator.workspace import WorkspaceManager


def _model_arg_keys(args: List[str]) -> set[str]:
    keys: set[str] = set()
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in {"--model", "-m"}:
            keys.add("model")
            i += 2
            continue
        if arg.startswith("--model=") or arg.startswith("-cmodel="):
            keys.add("model")
        if arg in {"-c", "--config"} and i + 1 < len(args):
            setting = args[i + 1].split("=", 1)[0].strip()
            if setting in {"model", "model_reasoning_effort"}:
                keys.add(setting)
            i += 2
            continue
        if arg.startswith("-cmodel_reasoning_effort="):
            keys.add("model_reasoning_effort")
        i += 1
    return keys


def _strip_model_args(agent_args: List[str], keys: Optional[set[str]] = None) -> List[str]:
    """Remove explicit model and effort selections from Codex CLI arguments."""
    remove = keys or {"model", "model_reasoning_effort"}
    stripped: List[str] = []
    i = 0
    while i < len(agent_args):
        arg = agent_args[i]
        if arg in {"--model", "-m"} and "model" in remove and i + 1 < len(agent_args):
            i += 2
            continue
        if arg in {"-c", "--config"} and i + 1 < len(agent_args):
            setting = agent_args[i + 1].split("=", 1)[0].strip()
            if setting in remove:
                i += 2
                continue
        if "model" in remove and (arg.startswith("--model=") or arg.startswith("-cmodel=")):
            i += 1
            continue
        if "model_reasoning_effort" in remove and arg.startswith("-cmodel_reasoning_effort="):
            i += 1
            continue
        stripped.append(arg)
        i += 1
    return stripped


def _merge_model_args(agent_args: List[str], override_args: List[str]) -> List[str]:
    """Use explicitly requested model settings while keeping other child options."""
    if not override_args:
        return list(agent_args)
    return _strip_model_args(agent_args, _model_arg_keys(override_args)) + override_args


def _model_override_args(model: Optional[str], reasoning_effort: Optional[str]) -> List[str]:
    args: List[str] = []
    if model:
        args.extend(["--model", model])
    if reasoning_effort:
        args.extend(["-c", f"model_reasoning_effort={json.dumps(reasoning_effort)}"])
    return args


class Orchestrator:
    """Manages agents, workspaces, delegation pipelines, and shared state."""

    def __init__(
        self,
        workspace: Optional[WorkspaceManager] = None,
        max_turns_per_agent: int = 6,
        max_delegation_depth: int = 5,
        on_event: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> None:
        self.workspace = workspace or WorkspaceManager()
        self.tool_registry = ToolRegistry(workspace=self.workspace)
        self.delegation_manager = DelegationManager(max_depth=max_delegation_depth)
        self.blackboard = SharedBlackboard()
        self.agents: Dict[str, Agent] = {}
        self.max_turns_per_agent = max_turns_per_agent
        self.on_event = on_event

    def emit_event(self, event_type: str, data: Dict[str, Any]) -> None:
        if self.on_event:
            self.on_event(event_type, data)

    def register_agent(self, agent: Agent) -> None:
        """Registers an agent into the orchestrator."""
        if agent.name in self.agents:
            raise ValueError(f"Agent '{agent.name}' is already registered.")
        self.agents[agent.name] = agent
        self.emit_event("agent_registered", {"name": agent.name, "role": agent.role, "engine": agent.engine.name})

    async def create_and_run_agent(
        self,
        source_agent: Agent,
        request: Any,
        call_stack: List[str],
    ) -> str:
        """Create a worker using the source agent's engine and tool permissions, then run its task."""
        if not source_agent.can_create_agents:
            return f"Agent Creation Error: '{source_agent.name}' is not allowed to create agents."
        if len(call_stack) >= self.delegation_manager.max_depth:
            return f"Agent Creation Error: Maximum delegation depth of {self.delegation_manager.max_depth} reached."
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", request.name):
            return "Agent Creation Error: Names must start with a letter and contain only letters, numbers, underscores, or hyphens (up to 64 characters)."
        if request.name in self.agents:
            return f"Agent Creation Error: Agent '{request.name}' already exists. Choose a unique name."

        engine_kwargs = dict(source_agent.engine.config)
        engine_kwargs["extra_args"] = _strip_model_args(
            list(engine_kwargs.get("extra_args") or [])
        )
        cli_path = getattr(source_agent.engine, "resolved_cli", None) or source_agent.engine.cli_path
        if cli_path:
            engine_kwargs["cli_path"] = cli_path
        engine_key = next(
            (name for name, engine_type in ENGINE_REGISTRY.items() if isinstance(source_agent.engine, engine_type)),
            None,
        )
        if engine_key:
            engine = get_engine(engine_key, **engine_kwargs)
        else:
            engine = type(source_agent.engine)(**engine_kwargs)
        worker = Agent(
            name=request.name,
            role=request.role,
            engine=engine,
            system_prompt=request.system_prompt or f"You are a {request.role}. Complete the assigned task and report your findings.",
            can_delegate_to=["*"] if source_agent.can_create_agents else [],
            allowed_tools=list(source_agent.allowed_tools) if source_agent.allowed_tools is not None else None,
            can_create_agents=source_agent.can_create_agents,
            metadata={"created_dynamically": True, "created_by": source_agent.name},
        )
        self.register_agent(worker)
        self.emit_event("agent_created", {
            "name": worker.name,
            "role": worker.role,
            "created_by": source_agent.name,
            "task": request.task,
            "model": getattr(request, "model", None),
            "reasoning_effort": getattr(request, "reasoning_effort", None),
            "depth": len(call_stack),
        })
        self.emit_event("delegation_dispatched", {
            "from_agent": source_agent.name,
            "to_agent": worker.name,
            "role": worker.role,
            "task": request.task,
            "model": getattr(request, "model", None),
            "reasoning_effort": getattr(request, "reasoning_effort", None),
            "depth": len(call_stack),
        })
        return await self.run_agent_turn(
            agent=worker,
            prompt=request.task,
            call_stack=call_stack + [worker.name],
            model=getattr(request, "model", None),
            reasoning_effort=getattr(request, "reasoning_effort", None),
        )

    def get_agent(self, name: str) -> Optional[Agent]:
        return self.agents.get(name)

    def close(self) -> None:
        """Discard all in-memory conversation and shared session context."""
        for agent in self.agents.values():
            agent.memory.clear()
        self.blackboard.clear()

    async def run(
        self,
        prompt: str,
        initial_agent: Optional[str] = None,
    ) -> str:
        """Starts the multi-agent workflow with a starting agent."""
        if not self.agents:
            raise ValueError("No agents registered in Orchestrator.")

        starting_agent_name = initial_agent or next(iter(self.agents))
        if starting_agent_name not in self.agents:
            raise ValueError(f"Initial agent '{starting_agent_name}' not found. Available: {list(self.agents.keys())}")

        root_agent = self.agents[starting_agent_name]
        self.emit_event("run_started", {
            "initial_agent": starting_agent_name,
            "prompt": prompt,
            "workspace": str(self.workspace.project_dir),
        })

        final_response = await self.run_agent_turn(
            agent=root_agent,
            prompt=prompt,
            call_stack=[starting_agent_name],
        )

        self.emit_event("run_completed", {
            "initial_agent": starting_agent_name,
            "final_response": final_response,
        })
        return final_response

    async def run_agent_turn(
        self,
        agent: Agent,
        prompt: str,
        call_stack: List[str],
        model: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
    ) -> str:
        """Runs an execution loop for a specific agent turn, resolving tool calls and delegations."""
        current_prompt = prompt
        turn_count = 0
        configured_args = list(agent.engine.config.get("extra_args") or [])
        extra_args = _merge_model_args(
            configured_args, _model_override_args(model, reasoning_effort)
        )

        self.emit_event("agent_turn_start", {
            "agent": agent.name,
            "role": agent.role,
            "call_stack": call_stack,
            "prompt": prompt,
            "model": model,
            "reasoning_effort": reasoning_effort,
        })

        while turn_count < self.max_turns_per_agent:
            turn_count += 1

            # Prepare tool & delegation descriptions for system prompt
            tools_desc = self.tool_registry.get_descriptions(allowed_tools=agent.allowed_tools)
            delegation_desc = self.delegation_manager.get_delegation_instructions(
                source_agent=agent,
                available_agents=self.agents,
            )
            combined_tools_desc = f"{tools_desc}\n\n{delegation_desc}"

            # Execute agent's engine
            run_kwargs: Dict[str, Any] = {}
            if agent.engine.name == "codex":
                run_kwargs["extra_args"] = extra_args
                run_kwargs["activity_callback"] = lambda name, data: self.emit_event(
                    name, {"agent": agent.name, **data}
                )
            response = await agent.run(
                prompt=current_prompt,
                cwd=self.workspace.project_dir,
                tools_description=combined_tools_desc,
                **run_kwargs,
            )

            response_text = response.text or (response.error or "")
            if response.exit_code != 0:
                self.emit_event("agent_error", {
                    "agent": agent.name,
                    "exit_code": response.exit_code,
                    "text": response.error or response_text or "Agent process failed.",
                })
            self.emit_event("agent_response", {
                "agent": agent.name,
                "text": response_text,
                "exit_code": response.exit_code,
            })

            # Create and immediately run requested specialist workers.
            creations = self.delegation_manager.parse_agent_creations(response_text)
            if creations:
                creation_results: List[str] = []
                for request in creations:
                    result = await self.create_and_run_agent(
                        agent, request, call_stack
                    )
                    creation_results.append(f"[New agent '{request.name}' result]:\n{result}")
                current_prompt = (
                    "Here are the results from the agents you created:\n\n"
                    + "\n\n".join(creation_results)
                    + "\n\nPlease incorporate these results into your final answer or continue the task."
                )
                continue

            # 1. Check for delegation requests
            delegations = self.delegation_manager.parse_delegations(response_text)
            if delegations:
                delegation_results: List[str] = []
                for req in delegations:
                    target_obj = self.agents.get(req.target_agent)
                    self.emit_event("delegation_dispatched", {
                        "from_agent": agent.name,
                        "to_agent": req.target_agent,
                        "role": target_obj.role if target_obj else "",
                        "task": req.task,
                        "model": req.model,
                        "reasoning_effort": req.reasoning_effort,
                        "depth": len(call_stack),
                    })
                    sub_result = await self.delegation_manager.execute_delegation(
                        orchestrator=self,
                        source_agent=agent,
                        request=req,
                        call_stack=call_stack,
                    )
                    delegation_results.append(
                        f"[Delegation to '{req.target_agent}' result]:\n{sub_result}"
                    )

                # Feed delegation results back to the agent for synthesis or next step
                current_prompt = (
                    "Here are the results from your delegated sub-tasks:\n\n"
                    + "\n\n".join(delegation_results)
                    + "\n\nPlease incorporate these results into your final answer or proceed with next steps."
                )
                continue

            # 2. Check for tool calls
            tool_calls = self._parse_tool_calls(response_text)
            if tool_calls:
                tool_results: List[str] = []
                for tool_name, args in tool_calls:
                    self.emit_event("tool_invoked", {
                        "agent": agent.name,
                        "tool": tool_name,
                        "args": args,
                    })
                    if agent.allowed_tools is not None and tool_name not in agent.allowed_tools:
                        tool_output = self.tool_registry.execute_tool(
                            tool_name, args, allowed_tools=agent.allowed_tools
                        )
                        self.emit_event("tool_completed", {
                            "agent": agent.name,
                            "tool": tool_name,
                            "args": args,
                            "result": tool_output,
                        })
                        tool_results.append(f"[Tool '{tool_name}' Output]:\n{tool_output}")
                        continue
                    file_path = args.get("path") if tool_name in {"write_file", "patch_file"} else None
                    before_edit = None
                    if file_path:
                        try:
                            resolved = self.workspace.resolve_path(file_path)
                            if resolved.is_file():
                                before_edit = resolved.read_text(encoding="utf-8", errors="replace")
                        except (OSError, PermissionError, ValueError):
                            before_edit = None

                    tool_output = self.tool_registry.execute_tool(
                        tool_name, args, allowed_tools=agent.allowed_tools
                    )

                    if tool_name == "read_file":
                        self.emit_event("file_accessed", {"agent": agent.name, "paths": [args.get("path", "")]})
                    elif tool_name == "list_files":
                        paths = tool_output if isinstance(tool_output, list) else []
                        self.emit_event("file_accessed", {"agent": agent.name, "paths": paths})
                    elif tool_name == "search_code":
                        paths = [item.get("file") for item in tool_output if isinstance(item, dict)] if isinstance(tool_output, list) else []
                        self.emit_event("file_accessed", {"agent": agent.name, "paths": paths})
                    elif tool_name == "run_command" and self.workspace.allow_shell_commands:
                        self.emit_event("command_run", {"agent": agent.name, "command": args.get("command", "")})
                    elif tool_name in {"write_file", "patch_file"} and isinstance(tool_output, str) and tool_output.startswith("Successfully"):
                        try:
                            after_edit = self.workspace.resolve_path(file_path).read_text(encoding="utf-8", errors="replace")
                            diff = "\n".join(difflib.unified_diff(
                                (before_edit or "").splitlines(),
                                after_edit.splitlines(),
                                fromfile="before",
                                tofile="after",
                                lineterm="",
                            ))
                            self.emit_event("file_edited", {"agent": agent.name, "path": file_path, "diff": diff})
                        except (OSError, PermissionError, ValueError):
                            pass
                    self.emit_event("tool_completed", {
                        "agent": agent.name,
                        "tool": tool_name,
                        "args": args,
                        "result": tool_output,
                    })
                    tool_results.append(f"[Tool '{tool_name}' Output]:\n{tool_output}")

                # Feed tool results back to agent
                current_prompt = (
                    "Tool execution results:\n\n"
                    + "\n\n".join(tool_results)
                    + "\n\nPlease continue based on these results."
                )
                continue

            # No delegations or tool calls left - final response reached
            self.emit_event("agent_final_response", {
                "agent": agent.name,
                "role": agent.role,
                "call_stack": call_stack,
                "text": response_text,
            })
            return response_text

        return f"Agent '{agent.name}' exceeded maximum turn limit ({self.max_turns_per_agent})."

    def _parse_tool_calls(self, text: str) -> List[tuple[str, Dict[str, Any]]]:
        """Extract JSON tool calls, including nested objects and braces in strings."""
        calls: List[tuple[str, Dict[str, Any]]] = []
        decoder = json.JSONDecoder()

        def add_call(tool_name: str, args: Dict[str, Any]) -> None:
            # An answer may include the same call in both supported formats.
            # Treat that as one request so it cannot execute twice.
            if not any(existing_name == tool_name and existing_args == args for existing_name, existing_args in calls):
                calls.append((tool_name, args))

        def decode_object(start: int) -> tuple[Dict[str, Any], int] | None:
            try:
                value, end = decoder.raw_decode(text, start)
            except json.JSONDecodeError:
                return None
            return (value, end) if isinstance(value, dict) else None

        fenced_spans: List[tuple[int, int]] = []
        for match in re.finditer(r"```(?:json)?\s*", text, re.IGNORECASE):
            decoded = decode_object(match.end())
            if decoded is None:
                continue
            data, end = decoded
            closing = re.match(r"\s*```", text[end:])
            if closing is None:
                continue
            fenced_spans.append((match.start(), end + closing.end()))
            tool_name = data.get("tool")
            args = data.get("arguments", data.get("args", {}))
            if isinstance(tool_name, str) and tool_name != "delegate_task" and isinstance(args, dict):
                add_call(tool_name, args)

        pattern = re.compile(r"^[ \t]*TOOL_CALL:\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\(\s*", re.MULTILINE)
        # Advance past each successfully decoded call. This is important because
        # argument strings may themselves contain text that looks like a tool call.
        search_from = 0
        while match := pattern.search(text, search_from):
            fenced_span = next((span for span in fenced_spans if span[0] <= match.start() < span[1]), None)
            if fenced_span is not None:
                search_from = fenced_span[1]
                continue
            tool_name = match.group(1)
            decoded = decode_object(match.end())
            if decoded is None:
                search_from = match.end()
                continue
            args, end = decoded
            close = re.match(r"\s*\)", text[end:])
            if close is not None:
                if tool_name != "delegate_task":
                    add_call(tool_name, args)
                search_from = end + close.end()
            else:
                search_from = match.end()

        return calls
