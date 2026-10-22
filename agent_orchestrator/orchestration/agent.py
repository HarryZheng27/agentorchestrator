"""Agent abstraction representing an AI worker in the multi-agent system."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent_orchestrator.engines.base import BaseEngine, EngineResponse
from agent_orchestrator.orchestration.memory import AgentMemory


@dataclass
class Agent:
    """Represents an agent equipped with an engine, instructions, and delegation permissions."""

    name: str
    role: str
    engine: BaseEngine
    system_prompt: str = ""
    can_delegate_to: List[str] = field(default_factory=list)
    allowed_tools: Optional[List[str]] = None
    memory: AgentMemory = field(default_factory=AgentMemory)
    metadata: Dict[str, Any] = field(default_factory=dict)
    can_create_agents: bool = True

    async def run(
        self,
        prompt: str,
        cwd: Optional[Path | str] = None,
        tools_description: Optional[str] = None,
        **kwargs: Any,
    ) -> EngineResponse:
        """Executes the agent engine with current memory and supplied parameters."""
        self.memory.add_message("user", prompt)

        response = await self.engine.execute(
            prompt=prompt,
            system_prompt=self.system_prompt,
            history=self.memory.messages[:-1],  # previous turns
            cwd=cwd,
            tools_description=tools_description,
            **kwargs,
        )

        if response.text:
            self.memory.add_message("assistant", response.text)

        return response
