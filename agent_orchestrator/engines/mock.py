"""Mock engine for testing and deterministic agent simulation."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Callable, List, Optional

from agent_orchestrator.engines.base import BaseEngine, EngineResponse, Message


class MockEngine(BaseEngine):
    """Configurable mock engine that can return scripted responses or dynamic handler outputs."""

    def __init__(
        self,
        name: str = "mock",
        default_response: str = "Mock response",
        response_handler: Optional[Callable[[str, Any], str]] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(name=name, **kwargs)
        self.default_response = default_response
        self.response_handler = response_handler
        self.recorded_calls: List[dict] = []

    async def execute(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        history: Optional[List[Message]] = None,
        cwd: Optional[Path | str] = None,
        tools_description: Optional[str] = None,
        **kwargs: Any,
    ) -> EngineResponse:
        self.recorded_calls.append({
            "prompt": prompt,
            "system_prompt": system_prompt,
            "history": history or [],
            "cwd": cwd,
            "tools_description": tools_description,
            "kwargs": kwargs,
        })

        if self.response_handler:
            output = self.response_handler(prompt, kwargs)
        else:
            output = self.default_response

        return EngineResponse(
            text=output,
            raw_output=output,
            exit_code=0,
            metadata={"engine": "mock", "calls_count": len(self.recorded_calls)},
        )
