"""Memory and blackboard management for multi-agent workflows."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from agent_orchestrator.engines.base import Message


@dataclass
class AgentMemory:
    """Maintains message history and conversation turns for an agent."""
    messages: List[Message] = field(default_factory=list)

    def add_message(self, role: str, content: str, metadata: Optional[Dict[str, Any]] = None) -> Message:
        msg = Message(role=role, content=content, metadata=metadata or {})
        self.messages.append(msg)
        return msg

    def clear(self) -> None:
        self.messages.clear()


class SharedBlackboard:
    """Shared state accessible across all agents in an orchestrator session."""

    def __init__(self) -> None:
        self._data: Dict[str, Any] = {}

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def dump(self) -> Dict[str, Any]:
        return dict(self._data)

    def clear(self) -> None:
        self._data.clear()
