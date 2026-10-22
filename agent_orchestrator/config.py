"""Configuration loader and schema models for multi-agent project definitions."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import yaml
except ImportError:
    yaml = None  # Fallback to JSON if pyyaml is missing

from agent_orchestrator.engines import get_engine
from agent_orchestrator.orchestration.agent import Agent
from agent_orchestrator.orchestration.orchestrator import Orchestrator
from agent_orchestrator.workspace import WorkspaceManager


@dataclass
class AgentConfig:
    name: str
    role: str
    engine: str
    system_prompt: str = ""
    cli_path: Optional[str] = None
    can_delegate_to: List[str] = field(default_factory=list)
    allowed_tools: Optional[List[str]] = None
    extra_args: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    can_create_agents: bool = True


@dataclass
class WorkspaceConfig:
    project_dir: Optional[str] = None
    github_repo: Optional[str] = None
    auto_clone: bool = True
    allow_shell_commands: bool = False


@dataclass
class ProjectConfig:
    name: str
    description: str = ""
    workspace: WorkspaceConfig = field(default_factory=WorkspaceConfig)
    initial_agent: Optional[str] = None
    max_delegation_depth: int = 5
    max_turns_per_agent: int = 6
    agents: List[AgentConfig] = field(default_factory=list)


def load_project_config(config_path: Path | str) -> ProjectConfig:
    """Loads a ProjectConfig from a YAML or JSON file."""
    path = Path(config_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found: {path}")

    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in [".yaml", ".yml"]:
        if yaml is None:
            raise RuntimeError("PyYAML is required to parse .yaml files. Run `pip install pyyaml`.")
        raw = yaml.safe_load(text)
    else:
        raw = json.loads(text)

    if not isinstance(raw, dict):
        raise ValueError("Invalid configuration file: Root must be a dictionary/mapping.")

    ws_raw = raw.get("workspace", {}) or {}
    ws_config = WorkspaceConfig(
        project_dir=ws_raw.get("project_dir"),
        github_repo=ws_raw.get("github_repo"),
        auto_clone=ws_raw.get("auto_clone", True),
        allow_shell_commands=ws_raw.get("allow_shell_commands", False),
    )

    agents_list: List[AgentConfig] = []
    for ag in raw.get("agents", []):
        agents_list.append(
            AgentConfig(
                name=ag["name"],
                role=ag.get("role", ag["name"]),
                engine=ag.get("engine", "mock"),
                system_prompt=ag.get("system_prompt", ""),
                cli_path=ag.get("cli_path"),
                can_delegate_to=ag.get("can_delegate_to", []),
                allowed_tools=ag.get("allowed_tools"),
                can_create_agents=ag.get("can_create_agents", True),
                extra_args=ag.get("extra_args", []),
                metadata=ag.get("metadata", {}),
            )
        )

    return ProjectConfig(
        name=raw.get("name", "Untitled Project"),
        description=raw.get("description", ""),
        workspace=ws_config,
        initial_agent=raw.get("initial_agent"),
        max_delegation_depth=raw.get("max_delegation_depth", 5),
        max_turns_per_agent=raw.get("max_turns_per_agent", 6),
        agents=agents_list,
    )


def build_orchestrator(
    config: ProjectConfig,
    workspace_override: Optional[str | Path] = None,
    repo_override: Optional[str] = None,
    on_event: Optional[Any] = None,
) -> Orchestrator:
    """Instantiates and wires an Orchestrator from a ProjectConfig."""
    project_dir = workspace_override or config.workspace.project_dir
    github_repo = repo_override or config.workspace.github_repo

    workspace = WorkspaceManager(
        project_dir=project_dir,
        github_repo=github_repo,
        auto_clone=config.workspace.auto_clone,
        allow_shell_commands=config.workspace.allow_shell_commands,
    )

    orchestrator = Orchestrator(
        workspace=workspace,
        max_turns_per_agent=config.max_turns_per_agent,
        max_delegation_depth=config.max_delegation_depth,
        on_event=on_event,
    )

    for ag_cfg in config.agents:
        engine = get_engine(
            engine_name=ag_cfg.engine,
            cli_path=ag_cfg.cli_path,
            extra_args=ag_cfg.extra_args,
        )
        agent = Agent(
            name=ag_cfg.name,
            role=ag_cfg.role,
            engine=engine,
            system_prompt=ag_cfg.system_prompt,
            can_delegate_to=ag_cfg.can_delegate_to,
            allowed_tools=ag_cfg.allowed_tools,
            can_create_agents=ag_cfg.can_create_agents,
            metadata=ag_cfg.metadata,
        )
        orchestrator.register_agent(agent)

    return orchestrator
