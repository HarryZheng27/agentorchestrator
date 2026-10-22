"""Optional Textual interface for launching and monitoring workflows."""

from __future__ import annotations

import asyncio
import json
import os
import queue
import tempfile
import traceback
from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict, Optional

from rich.markdown import Markdown
from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Input, Label, RichLog, Static, TabbedContent, TabPane, TextArea, Tree

from agent_orchestrator.config import build_orchestrator, load_project_config
from agent_orchestrator.tui_state import AgentStatus, WorkflowState


class DebugScreen(Screen[None]):
    """Detailed event and exception log for diagnosing workflow failures."""

    BINDINGS = [("escape", "close", "Close debug")]
    CSS = """
    #debug-log { height: 1fr; border: round $secondary; margin: 1; }
    #debug-help { height: 1; padding: 0 1; }
    """

    def __init__(self, lines: Deque[str]) -> None:
        super().__init__()
        self.lines = lines
        self.rendered: tuple[str, ...] = ()

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("Workflow debug log · Esc to return", id="debug-help")
        yield RichLog(id="debug-log", wrap=True, max_lines=2000, markup=False)
        yield Footer()

    def on_mount(self) -> None:
        self.set_interval(0.25, self._refresh)
        self._refresh()

    def action_close(self) -> None:
        self.app.pop_screen()

    def _refresh(self) -> None:
        snapshot = tuple(self.lines)
        if snapshot == self.rendered:
            return
        self.rendered = snapshot
        log = self.query_one("#debug-log", RichLog)
        log.clear()
        for line in snapshot:
            log.write(line)


class OrchestratorTUI(App[None]):
    """A run form, delegation tree, filterable event log, and final response."""

    TITLE = "Agent Orchestrator"
    BINDINGS = [
        ("ctrl+r", "run_workflow", "Run workflow"),
        ("ctrl+d", "show_debug", "Debug log"),
        ("ctrl+1", "show_activity", "Activity"),
        ("ctrl+2", "show_agent_output", "Agent output"),
        ("ctrl+3", "show_final_response", "Final response"),
        ("ctrl+q", "quit", "Quit"),
    ]
    CSS = """
    Screen { layout: vertical; }
    #setup { height: auto; padding: 0 1; border: round $primary; }
    #fields { height: 3; }
    #fields Input { width: 1fr; margin-bottom: 0; }
    #fields #repo { width: 2fr; }
    #prompt { height: 3; }
    #run-row { height: 3; align: left middle; }
    #run-status { width: 1fr; padding: 0 1; }
    #debug-button { width: 16; }
    #body { height: 1fr; }
    #agent-pane { width: 35%; border: round $secondary; }
    #agent-tree { height: 1fr; }
    #detail-pane { width: 65%; height: 1fr; }
    #detail-tabs { height: 1fr; }
    TabPane { height: 1fr; padding: 0 1; }
    #activity-filter { height: 3; }
    #activity { height: 1fr; }
    #agent-output { height: 1fr; }
    #result { height: 1fr; }
    .pane-title { padding: 0 1; text-style: bold; }
    """

    def __init__(self, config: Optional[str] = None, project_dir: Optional[str] = None,
                 repo: Optional[str] = None, prompt: str = "", agent: Optional[str] = None) -> None:
        super().__init__()
        self.initial_config = config or ""
        self.initial_project = project_dir or ""
        self.initial_repo = repo or ""
        self.initial_prompt = prompt
        self.initial_agent = agent or ""
        self.state = WorkflowState()
        self.events: queue.SimpleQueue[tuple[str, Dict[str, Any]]] = queue.SimpleQueue()
        self.debug_lines: Deque[str] = deque(maxlen=2000)
        self.running = False

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="setup"):
            yield Label("Task prompt")
            yield TextArea(self.initial_prompt, id="prompt")
            with Horizontal(id="run-row"):
                yield Button("Run workflow", id="run", variant="primary")
                yield Static("Ready", id="run-status")
                yield Button("Debug log", id="debug-button")
            with Horizontal(id="fields"):
                yield Input(value=self.initial_config, placeholder="Config (blank: default team)", id="config")
                yield Input(value=self.initial_project, placeholder="Project directory", id="project")
                yield Input(value=self.initial_repo, placeholder="Repository URL or owner/repo", id="repo")
                yield Input(value=self.initial_agent, placeholder="Starting agent (blank: config default)", id="agent")
        with Horizontal(id="body"):
            with Vertical(id="agent-pane"):
                yield Label("Agents and delegation", classes="pane-title")
                yield Tree("Agents", id="agent-tree")
            with Vertical(id="detail-pane"):
                with TabbedContent(initial="activity-tab", id="detail-tabs"):
                    with TabPane("Activity", id="activity-tab"):
                        yield Input(placeholder="Filter activity", id="activity-filter")
                        yield RichLog(id="activity", wrap=True, max_lines=500)
                    with TabPane("Agent output", id="output-tab"):
                        yield RichLog(id="agent-output", wrap=True, max_lines=5000)
                    with TabPane("Final response", id="result-tab"):
                        yield RichLog(id="result", wrap=True)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#agent-tree", Tree).root.expand()
        self.query_one("#prompt", TextArea).focus()
        self.set_interval(0.1, self._drain_events)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "run":
            self.action_run_workflow()
        elif event.button.id == "debug-button":
            self.action_show_debug()

    def action_show_debug(self) -> None:
        self.push_screen(DebugScreen(self.debug_lines))

    def action_show_activity(self) -> None:
        self.query_one("#detail-tabs", TabbedContent).active = "activity-tab"

    def action_show_agent_output(self) -> None:
        self.query_one("#detail-tabs", TabbedContent).active = "output-tab"

    def action_show_final_response(self) -> None:
        self.query_one("#detail-tabs", TabbedContent).active = "result-tab"

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "activity-filter":
            self._render_log()

    def action_run_workflow(self) -> None:
        if self.running:
            return
        prompt = self.query_one("#prompt", TextArea).text.strip()
        if not prompt:
            self.query_one("#run-status", Static).update("Enter a task prompt.")
            self.query_one("#prompt", TextArea).focus()
            return
        config_path = self.query_one("#config", Input).value.strip()
        project = self.query_one("#project", Input).value.strip() or None
        repo = self.query_one("#repo", Input).value.strip() or None
        agent = self.query_one("#agent", Input).value.strip() or None
        self.state = WorkflowState()
        self.debug_lines.clear()
        self._record_debug("Workflow run requested")
        self.state.status = "Starting"
        self.query_one("#agent-tree", Tree).clear()
        self.query_one("#activity", RichLog).clear()
        self.query_one("#agent-output", RichLog).clear()
        self.query_one("#result", RichLog).clear()
        self.query_one("#detail-tabs", TabbedContent).active = "activity-tab"
        self.running = True
        self.query_one("#run", Button).disabled = True
        self.query_one("#run-status", Static).update("Starting...")
        self.run_worker(self._execute(config_path, project, repo, prompt, agent), exclusive=True)

    @staticmethod
    def _load_config(path: str) -> Any:
        if path:
            return load_project_config(path)
        from agent_orchestrator.cli import DEFAULT_MODIFY_CONFIG
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".yaml", encoding="utf-8") as file:
            file.write(DEFAULT_MODIFY_CONFIG)
            default_path = file.name
        try:
            return load_project_config(default_path)
        finally:
            os.unlink(default_path)

    async def _execute(self, config_path: str, project: Optional[str], repo: Optional[str],
                       prompt: str, agent: Optional[str]) -> None:
        orchestrator = None
        try:
            self.events.put(("workflow_status", {"status": "Loading workflow configuration..."}))
            config = self._load_config(config_path)
            # Workspace setup may clone a repository, so leave the UI responsive.
            self.events.put(("workflow_status", {"status": "Preparing workspace and agents..."}))
            orchestrator = await asyncio.to_thread(
                build_orchestrator, config, workspace_override=project, repo_override=repo,
                on_event=lambda name, data: self.events.put((name, data)),
            )
            await orchestrator.run(prompt=prompt, initial_agent=agent or config.initial_agent)
        except Exception as exc:
            self.events.put(("tui_error", {
                "text": str(exc),
                "traceback": traceback.format_exc(),
            }))
        finally:
            if orchestrator is not None:
                orchestrator.close()
            self.events.put(("tui_finished", {}))

    @staticmethod
    def _agent_label(agent: AgentStatus) -> Text:
        task = WorkflowState._brief(agent.task, 80)
        detail = f" — {task}" if task else (f" — {agent.role}" if agent.role else "")
        return Text(f"{agent.name} [{agent.status}]{detail}")

    def _render_tree(self) -> None:
        tree = self.query_one("#agent-tree", Tree)
        tree.clear()
        tree.root.expand()
        attached: set[str] = set()

        def add(name: str, parent: Any) -> None:
            if name in attached:
                return
            attached.add(name)
            node = parent.add(self._agent_label(self.state.agents[name]), expand=True)
            for child in self.state.agents.values():
                if child.parent == name and child.name not in attached:
                    add(child.name, node)

        for agent in self.state.agents.values():
            if not agent.parent or agent.parent not in self.state.agents:
                add(agent.name, tree.root)
        # Cycles should not happen, but an inconsistent event stream must stay visible.
        for name in self.state.agents:
            add(name, tree.root)

    def _render_log(self) -> None:
        log = self.query_one("#activity", RichLog)
        log.clear()
        filter_text = self.query_one("#activity-filter", Input).value.casefold().strip()
        for line in self.state.lines:
            if filter_text in line.casefold():
                log.write(line)

    def _render_agent_output(self) -> None:
        log = self.query_one("#agent-output", RichLog)
        log.clear()
        if not self.state.outputs:
            log.write("Waiting for agent output...")
            return
        for name, output in self.state.outputs.items():
            log.write(Text(name, style="bold cyan"))
            if output:
                log.write(Markdown(output))
            else:
                log.write("(No text output yet)")

    def _drain_events(self) -> None:
        structural = False
        log_changed = False
        output_changed = False
        while True:
            try:
                event, data = self.events.get_nowait()
            except queue.Empty:
                break
            debug_data = data
            if event in {"agent_output", "agent_response"} and len(str(data.get("text", ""))) > 800:
                debug_data = dict(data)
                debug_data["text"] = str(data.get("text", ""))[:800] + "... (truncated)"
            self._record_debug(f"EVENT {event}: {json.dumps(debug_data, ensure_ascii=False, default=str)}")
            if event == "tui_error":
                self.state.status = "Failed"
                self.state.lines.append(f"Workflow error: {data.get('text', '')}")
                if data.get("traceback"):
                    self._record_debug("TRACEBACK:\n" + str(data["traceback"]))
                log_changed = True
            elif event == "tui_finished":
                self.running = False
                self.query_one("#run", Button).disabled = False
            else:
                log_changed |= self.state.consume(event, data) is not None
                output_changed |= event in {"agent_output", "agent_response"}
                structural |= event in {
                    "agent_registered", "agent_created", "agent_turn_start",
                    "delegation_dispatched", "run_started", "agent_final_response",
                    "agent_process_started", "agent_process_finished", "agent_error",
                    "agent_progress", "agent_protocol_error",
                }
                if event == "workflow_status":
                    self.state.status = str(data.get("status") or self.state.status)
                if event == "run_completed":
                    result = self.query_one("#result", RichLog)
                    result.clear()
                    if self.state.final_response:
                        result.write(Markdown(self.state.final_response))
                    else:
                        result.write("(No response)")
                    self.query_one("#detail-tabs", TabbedContent).active = "result-tab"
        if structural:
            self._render_tree()
        if log_changed:
            self._render_log()
        if output_changed:
            self._render_agent_output()
        status = self.state.status
        if status == "Failed":
            status = "Failed · Ctrl+D for details"
        self.query_one("#run-status", Static).update(status)

    def _record_debug(self, text: str) -> None:
        self.debug_lines.append(text)


def launch_tui(config: Optional[str] = None, project_dir: Optional[str] = None,
               repo: Optional[str] = None, prompt: str = "", agent: Optional[str] = None) -> None:
    OrchestratorTUI(config=config, project_dir=project_dir, repo=repo, prompt=prompt, agent=agent).run()
