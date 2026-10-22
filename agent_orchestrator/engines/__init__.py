"""Engines package providing CLI and mock runners for Codex."""

from __future__ import annotations

from typing import Any, Dict, Type

from agent_orchestrator.engines.base import BaseEngine, EngineResponse, Message
from agent_orchestrator.engines.codex import CodexCLIEngine
from agent_orchestrator.engines.mock import MockEngine

ENGINE_REGISTRY: Dict[str, Type[BaseEngine]] = {
    "codex": CodexCLIEngine,
    "mock": MockEngine,
}


def get_engine(engine_name: str, **kwargs: Any) -> BaseEngine:
    """Factory to instantiate an engine by name."""
    normalized = engine_name.lower().strip()
    engine_cls = ENGINE_REGISTRY.get(normalized)
    if not engine_cls:
        raise ValueError(
            f"Unknown engine '{engine_name}'. Available engines: {list(ENGINE_REGISTRY.keys())}"
        )
    return engine_cls(**kwargs)


__all__ = [
    "BaseEngine",
    "EngineResponse",
    "Message",
    "CodexCLIEngine",
    "MockEngine",
    "get_engine",
    "ENGINE_REGISTRY",
]
