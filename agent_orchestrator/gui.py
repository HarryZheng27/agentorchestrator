"""Tkinter interface for running and monitoring an agent workflow."""

from __future__ import annotations

import asyncio
import os
import queue
import threading
from pathlib import Path
from tkinter import BOTH, END, LEFT, RIGHT, X, Y, filedialog, messagebox, ttk
import tkinter as tk
from typing import Any, Dict, Optional

from agent_orchestrator.config import build_orchestrator, load_project_config


class OrchestratorGUI:
    def __init__(self, config: Optional[str] = None, project_dir: Optional[str] = None, repo: Optional[str] = None,
                 prompt: str = "", agent: Optional[str] = None) -> None:
        self.root = tk.Tk()
        self.root.title("Agent Orchestrator")
        self.root.geometry("1100x900")
        self.events: queue.Queue[tuple[str, Dict[str, Any]]] = queue.Queue()
        self.running = False
        self.repo = repo
        self.agent = agent
        self.config_var = tk.StringVar(value=config or "")
        self.project_var = tk.StringVar(value=project_dir or "")
        self.prompt_var = tk.StringVar(value=prompt)
        self.status_var = tk.StringVar(value="Ready")
        self.rows: Dict[str, str] = {}
        self._build()
        self.root.after(100, self._drain_events)

    def _build(self) -> None:
        main = ttk.Frame(self.root, padding=12)
        main.pack(fill=BOTH, expand=True)
        form = ttk.LabelFrame(main, text="Workflow", padding=10)
        form.pack(fill=X)
        ttk.Label(form, text="Config").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=4)
        ttk.Entry(form, textvariable=self.config_var).grid(row=0, column=1, sticky="ew", pady=4)
        ttk.Button(form, text="Browse…", command=self._choose_config).grid(row=0, column=2, padx=(8, 0))
        ttk.Label(form, text="Project directory").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=4)
        ttk.Entry(form, textvariable=self.project_var).grid(row=1, column=1, sticky="ew", pady=4)
        ttk.Button(form, text="Browse…", command=self._choose_project).grid(row=1, column=2, padx=(8, 0))
        ttk.Label(form, text="Task prompt").grid(row=2, column=0, sticky="nw", padx=(0, 8), pady=4)
        self.prompt_text = tk.Text(form, height=4, wrap="word")
        self.prompt_text.grid(row=2, column=1, columnspan=2, sticky="ew", pady=4)
        if self.prompt_var.get():
            self.prompt_text.insert("1.0", self.prompt_var.get())
        form.columnconfigure(1, weight=1)
        actions = ttk.Frame(form)
        actions.grid(row=3, column=1, columnspan=2, sticky="e", pady=(6, 0))
        self.run_button = ttk.Button(actions, text="Run workflow", command=self._start)
        self.run_button.pack(side=RIGHT)
        ttk.Label(actions, textvariable=self.status_var).pack(side=RIGHT, padx=12)

        body = ttk.Panedwindow(main, orient="vertical")
        body.pack(fill=BOTH, expand=True, pady=(12, 0))
        agents_frame = ttk.LabelFrame(body, text="Agents and tasks", padding=6)
        self.tree = ttk.Treeview(agents_frame, columns=("agent", "status", "task"), show="headings", height=9)
        self.tree.heading("agent", text="Agent")
        self.tree.heading("status", text="Status")
        self.tree.heading("task", text="Current task")
        self.tree.column("agent", width=140, stretch=False)
        self.tree.column("status", width=120, stretch=False)
        self.tree.column("task", width=620)
        scroll = ttk.Scrollbar(agents_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side=LEFT, fill=BOTH, expand=True)
        scroll.pack(side=RIGHT, fill=Y)
        body.add(agents_frame, weight=2)

        activity_frame = ttk.LabelFrame(body, text="Activity", padding=6)
        self.activity = tk.Text(activity_frame, height=9, wrap="word", state="disabled")
        activity_scroll = ttk.Scrollbar(activity_frame, orient="vertical", command=self.activity.yview)
        self.activity.configure(yscrollcommand=activity_scroll.set)
        self.activity.pack(side=LEFT, fill=BOTH, expand=True)
        activity_scroll.pack(side=RIGHT, fill=Y)
        body.add(activity_frame, weight=2)

        response_frame = ttk.LabelFrame(body, text="Final response", padding=6)
        self.final_response = tk.Text(response_frame, height=12, wrap="word", state="disabled")
        response_scroll = ttk.Scrollbar(response_frame, orient="vertical", command=self.final_response.yview)
        self.final_response.configure(yscrollcommand=response_scroll.set)
        self.final_response.pack(side=LEFT, fill=BOTH, expand=True)
        response_scroll.pack(side=RIGHT, fill=Y)
        body.add(response_frame, weight=3)

    def _choose_config(self) -> None:
        path = filedialog.askopenfilename(title="Choose orchestrator config", filetypes=[("Config files", "*.yaml *.yml *.json"), ("All files", "*.*")])
        if path:
            self.config_var.set(path)

    def _choose_project(self) -> None:
        path = filedialog.askdirectory(title="Choose project directory")
        if path:
            self.project_var.set(path)

    def _start(self) -> None:
        if self.running:
            return
        prompt = self.prompt_text.get("1.0", END).strip()
        if not prompt:
            messagebox.showerror("Prompt required", "Enter a task prompt before starting the workflow.")
            return
        try:
            if self.config_var.get().strip():
                config = load_project_config(self.config_var.get().strip())
            else:
                from agent_orchestrator.cli import DEFAULT_MODIFY_CONFIG
                import tempfile
                with tempfile.NamedTemporaryFile("w", delete=False, suffix=".yaml", encoding="utf-8") as f:
                    f.write(DEFAULT_MODIFY_CONFIG)
                    default_path = f.name
                try:
                    config = load_project_config(default_path)
                finally:
                    os.unlink(default_path)
        except Exception as exc:
            messagebox.showerror("Configuration error", str(exc))
            return

        self.tree.delete(*self.tree.get_children())
        self.rows.clear()
        self.activity.configure(state="normal")
        self.activity.delete("1.0", END)
        self.activity.configure(state="disabled")
        self.final_response.configure(state="normal")
        self.final_response.delete("1.0", END)
        self.final_response.configure(state="disabled")
        self.running = True
        self.run_button.configure(state="disabled")
        self.status_var.set("Running")

        def on_event(name: str, data: Dict[str, Any]) -> None:
            self.events.put((name, data))

        def worker() -> None:
            orchestrator = None
            try:
                orchestrator = build_orchestrator(config, workspace_override=self.project_var.get().strip() or None,
                                                   repo_override=self.repo, on_event=on_event)
                result = asyncio.run(orchestrator.run(prompt=prompt, initial_agent=self.agent or config.initial_agent))
                self.events.put(("gui_result", {"text": result}))
            except Exception as exc:
                self.events.put(("gui_error", {"text": str(exc)}))
            finally:
                if orchestrator:
                    orchestrator.close()
                self.events.put(("gui_finished", {}))

        threading.Thread(target=worker, daemon=True).start()

    def _row(self, name: str, status: str, task: str) -> None:
        if name in self.rows:
            self.tree.item(self.rows[name], values=(name, status, task))
        else:
            self.rows[name] = self.tree.insert("", END, values=(name, status, task))

    @staticmethod
    def _file_name(path: Any) -> str:
        return Path(str(path).replace("\\", "/")).name or str(path)

    def _tool_summary(self, data: Dict[str, Any]) -> str:
        tool = data.get("tool", "tool")
        args = data.get("args") or {}
        if tool == "read_file":
            files = [self._file_name(args.get("path", ""))]
        elif tool == "list_files":
            result = data.get("result") or []
            files = sorted({self._file_name(p) for p in result}) if isinstance(result, (list, tuple)) else []
            if not files:
                files = ["No matching files"]
        elif tool == "search_code":
            result = data.get("result") or []
            files = sorted({self._file_name(item.get("file", "")) for item in result if isinstance(item, dict) and item.get("file")}) if isinstance(result, (list, tuple)) else []
            if not files:
                files = ["No matching files"]
        else:
            return f"{data.get('agent', 'Agent')} ran {tool}"
        return f"{data.get('agent', 'Agent')} {tool}: " + ", ".join(files)

    def _handle_event(self, name: str, data: Dict[str, Any]) -> None:
        if name == "agent_registered":
            self._row(data["name"], "Ready", data.get("role", ""))
            self._log(f"Agent ready: {data['name']} ({data.get('role', '')})")
        elif name == "agent_created":
            self._row(data["name"], "Created", "Awaiting task")
            self._log(f"Agent created: {data['name']} by {data.get('created_by', 'agent')}")
        elif name == "agent_turn_start":
            values = self.tree.item(self.rows[data["agent"]], "values") if data["agent"] in self.rows else ("", "", "")
            existing = values[2]
            if values[1] == "Ready" or not existing or existing in {"Working on assigned task", "Awaiting task"}:
                existing = data.get("prompt", "Working on assigned task")
            self._row(data["agent"], "Working", existing)
            self._log(f"{data['agent']} started a task")
        elif name == "delegation_dispatched":
            self._row(data["to_agent"], "Delegated", data.get("task", ""))
            self._row(data["from_agent"], "Delegating", f"Assigned task to {data['to_agent']}")
            self._log(f"Delegation: {data['from_agent']} → {data['to_agent']}: {data.get('task', '')}")
        elif name == "tool_completed":
            if data.get("tool") not in {
                "read_file", "list_files", "search_code", "run_command", "write_file", "patch_file"
            }:
                self._log(self._tool_summary(data))
        elif name == "tool_invoked":
            # Completion event supplies safe file-name summaries; avoid raw args here.
            if data.get("tool") not in {"read_file", "list_files", "search_code"}:
                self._log(f"{data.get('agent', 'Agent')} started {data.get('tool', 'tool')}")
        elif name == "command_run":
            self._log(f"$ {data.get('command', '')}")
        elif name == "file_accessed":
            self._log("Read: " + self._file_names(data.get("paths", [])))
        elif name == "file_edited":
            self._log("Edited: " + self._file_name(data.get("path", "")))
        elif name == "run_completed":
            self.status_var.set("Completed")
            self._set_final_response(data.get("final_response", ""))
            self._log("Workflow completed")
        elif name == "gui_result":
            self._set_final_response(data.get("text", ""))
        elif name == "gui_error":
            self.status_var.set("Failed")
            self._log(f"Workflow error: {data.get('text', '')}")
            messagebox.showerror("Workflow failed", data.get("text", "Unknown error"))
        elif name == "gui_finished":
            self.running = False
            self.run_button.configure(state="normal")
            if self.status_var.get() == "Running":
                self.status_var.set("Finished")

    def _log(self, text: str) -> None:
        self.activity.configure(state="normal")
        self.activity.insert(END, text + "\n")
        self.activity.see(END)
        self.activity.configure(state="disabled")

    def _file_names(self, paths: Any) -> str:
        if not isinstance(paths, (list, tuple)):
            paths = [paths]
        names = sorted({self._file_name(path) for path in paths if path})
        return ", ".join(names) if names else "none"

    def _set_final_response(self, text: Any) -> None:
        self.final_response.configure(state="normal")
        self.final_response.delete("1.0", END)
        self.final_response.insert("1.0", str(text or ""))
        self.final_response.configure(state="disabled")
        self.final_response.see("1.0")

    def _drain_events(self) -> None:
        try:
            while True:
                name, data = self.events.get_nowait()
                self._handle_event(name, data)
        except queue.Empty:
            pass
        self.root.after(100, self._drain_events)

    def run(self) -> None:
        self.root.mainloop()


def launch_gui(config: Optional[str] = None, project_dir: Optional[str] = None, repo: Optional[str] = None,
               prompt: str = "", agent: Optional[str] = None) -> None:
    OrchestratorGUI(config=config, project_dir=project_dir, repo=repo, prompt=prompt, agent=agent).run()
