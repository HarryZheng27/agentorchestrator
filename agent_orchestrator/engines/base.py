"""Base abstraction for CLI and model execution engines."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class Message:
    """Represents a message in the conversation history."""
    role: str  # "system", "user", "assistant", "tool"
    content: str
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class EngineResponse:
    """Standardized response from an engine execution."""
    text: str
    raw_output: str = ""
    exit_code: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None

    @property
    def is_success(self) -> bool:
        return self.exit_code == 0 and self.error is None


class BaseEngine(ABC):
    """Abstract base class for all agent execution engines."""

    def __init__(self, name: str, cli_path: Optional[str] = None, **kwargs: Any) -> None:
        self.name = name
        self.cli_path = cli_path
        self.config = kwargs

    @abstractmethod
    async def execute(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        history: Optional[List[Message]] = None,
        cwd: Optional[Path | str] = None,
        tools_description: Optional[str] = None,
        **kwargs: Any,
    ) -> EngineResponse:
        """Executes the agent with the given prompt and context."""
        pass
