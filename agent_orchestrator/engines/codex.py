"""Codex CLI agent engine."""

from __future__ import annotations

import asyncio
import codecs
import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any, List, Optional

from agent_orchestrator.engines.base import BaseEngine, EngineResponse, Message


class CodexCLIEngine(BaseEngine):
    """Engine that executes agents via the Codex CLI (`codex`)."""

    def __init__(self, cli_path: Optional[str] = None, **kwargs: Any) -> None:
        super().__init__(name="codex", cli_path=cli_path, **kwargs)
        self.resolved_cli = self._resolve_cli(cli_path)

    def _resolve_cli(self, explicit_path: Optional[str]) -> Optional[str]:
        if explicit_path:
            return explicit_path
        # Check standard PATH
        found = shutil.which("codex")
        if found:
            return found

        if sys.platform == "win32":
            # npm's Windows shims live under AppData when that directory is set.
            appdata = os.environ.get("APPDATA")
            if appdata:
                npm_dir = Path(appdata) / "npm"
                for candidate in ("codex.cmd", "codex.ps1", "codex"):
                    p = npm_dir / candidate
                    if p.is_file():
                        return str(p)
        else:
            # npm may install outside PATH in a user prefix or an nvm version.
            home = Path.home()
            prefix = os.environ.get("NPM_CONFIG_PREFIX") or os.environ.get("npm_config_prefix")
            bins = [Path(prefix) / "bin"] if prefix else []
            bins.extend((home / ".npm-global" / "bin", home / ".local" / "bin"))
            nvm_dir = Path(os.environ.get("NVM_DIR") or home / ".nvm")
            bins.extend(sorted(
                nvm_dir.glob("versions/node/*/bin"),
                key=lambda bin_dir: tuple(int(part) for part in re.findall(r"\d+", bin_dir.parent.name)),
                reverse=True,
            ))
            bins.extend((Path("/usr/local/bin"), Path("/usr/bin")))
            for bin_dir in bins:
                p = bin_dir / "codex"
                if p.is_file() and os.access(p, os.X_OK):
                    return str(p)
        return "codex"

    def _build_command(self, prompt: str, extra_args: List[str]) -> List[str]:
        cmd: List[str] = []
        is_windows = sys.platform == "win32"
        cli = self.resolved_cli or "codex"

        if is_windows and cli.lower().endswith((".cmd", ".ps1")):
            # npm installs Codex behind a PowerShell/batch shim. Passing a
            # multiline prompt through those shells can split or alter the
            # argument. Run the JS entrypoint with Node directly so argv is
            # preserved exactly.
            npm_dir = Path(cli).parent
            entrypoint = npm_dir / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
            node = npm_dir / "node.exe"
            if not node.exists():
                node = Path(shutil.which("node") or "node")
            if entrypoint.exists():
                cmd = [str(node), str(entrypoint)]
            elif cli.lower().endswith(".ps1"):
                cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", cli]
            else:
                cmd = ["cmd.exe", "/c", cli]
        elif Path(cli).is_absolute() and Path(cli).is_symlink():
            # An nvm npm shim can be found even when its Node bin is not on PATH.
            entrypoint = Path(cli).resolve()
            node = Path(cli).parent / "node"
            if entrypoint.suffix == ".js" and node.is_file() and os.access(node, os.X_OK):
                cmd = [str(node), str(entrypoint)]
            else:
                cmd = [cli]
        else:
            cmd = [cli]

        # Use the `exec` subcommand for non-interactive execution
        cmd.append("exec")

        if extra_args:
            cmd.extend(extra_args)

        # Codex CLI syntax: `codex exec "<prompt>"`
        cmd.append(prompt)
        return cmd

    @staticmethod
    def _content_text(value: Any) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            return "".join(
                CodexCLIEngine._content_text(item.get("text", ""))
                if isinstance(item, dict) else CodexCLIEngine._content_text(item)
                for item in value
            )
        if isinstance(value, dict):
            return CodexCLIEngine._content_text(value.get("text", ""))
        return ""

    @staticmethod
    def _emit_activity(callback: Any, event: str, **data: Any) -> None:
        if callback:
            callback(event, data)

    @staticmethod
    def _configured_model_settings(args: List[str]) -> dict[str, Optional[str]]:
        """Extract effective model settings from Codex CLI arguments for diagnostics."""
        settings: dict[str, Optional[str]] = {"model": None, "reasoning_effort": None}
        i = 0
        while i < len(args):
            arg = args[i]
            if arg in {"--model", "-m"} and i + 1 < len(args):
                settings["model"] = args[i + 1]
                i += 2
                continue
            if arg in {"-c", "--config"} and i + 1 < len(args):
                setting = args[i + 1]
                key, separator, value = setting.partition("=")
                if separator:
                    try:
                        parsed_value = json.loads(value)
                    except json.JSONDecodeError:
                        parsed_value = value
                    if key == "model":
                        settings["model"] = str(parsed_value)
                    elif key == "model_reasoning_effort":
                        settings["reasoning_effort"] = str(parsed_value)
                i += 2
                continue
            if arg.startswith("--model="):
                settings["model"] = arg.split("=", 1)[1]
            i += 1
        return settings

    def _handle_json_event(
        self,
        event: Dict[str, Any],
        callback: Any,
        assistant_messages: Dict[str, str],
        seen_command_ids: set[str],
    ) -> None:
        event_type = event.get("type", "")
        item = event.get("item") or {}
        if not isinstance(item, dict):
            item = {}
        item_type = item.get("type") or item.get("kind", "")
        item_id = str(item.get("id", ""))

        if item_type in {"command_execution", "local_shell_call"}:
            command = item.get("command") or item.get("cmd") or ""
            if isinstance(command, list):
                command = " ".join(str(part) for part in command)
            if command and (not item_id or item_id not in seen_command_ids):
                self._emit_activity(callback, "command_run", command=str(command))
                if item_id:
                    seen_command_ids.add(item_id)
                accessed = re.findall(
                    r"(?<![\w.-])(?:[A-Za-z]:[\\/])?[\w./\\-]+\.(?:py|pyi|js|jsx|ts|tsx|json|ya?ml|toml|md|txt|html|css|sql|sh|ps1|bat|cmd|go|rs|java|kt|cs|cpp|h|c)(?![\w])",
                    str(command),
                    flags=re.IGNORECASE,
                )
                if accessed:
                    self._emit_activity(
                        callback,
                        "file_accessed",
                        paths=sorted({Path(path.replace("\\", "/")).name for path in accessed}),
                    )

        if item_type in {"file_change", "file_changes"} and event_type in {"item.completed", "item.updated"}:
            changes = item.get("changes") or item.get("files") or []
            if isinstance(changes, list):
                for change in changes:
                    if not isinstance(change, dict):
                        continue
                    path = change.get("path") or change.get("file_path") or ""
                    diff = change.get("diff") or change.get("patch") or ""
                    if path:
                        self._emit_activity(callback, "file_edited", path=str(path), diff=str(diff))

        if item_type in {"agent_message", "assistant_message"} and event_type in {"item.completed", "item.updated"}:
            message = self._content_text(item.get("text") or item.get("content") or "")
            if message:
                message_id = item_id or "agent-message"
                assistant_messages[message_id] = message
                self._emit_activity(
                    callback, "agent_output", text=message,
                    output_id=message_id, replace=True,
                    complete=event_type == "item.completed",
                )

        if item_type in {"read_file", "file_read"} and event_type in {"item.completed", "item.updated"}:
            paths = item.get("paths") or [item.get("path") or item.get("file_path")]
            self._emit_activity(callback, "file_accessed", paths=[str(p) for p in paths if p])

        if item_type == "reasoning" and event_type in {"item.completed", "item.updated"}:
            summary = self._content_text(item.get("summary") or item.get("text") or "")
            if summary and (not item_id or item_id not in seen_command_ids):
                if item_id:
                    seen_command_ids.add(item_id)
                self._emit_activity(callback, "agent_reasoning", text=summary.strip())

        if item_type in {"mcp_tool_call", "function_call", "tool_call"} and event_type in {"item.started", "item.completed"}:
            tool_name = item.get("name") or item.get("tool") or ""
            if tool_name and (not item_id or f"tool:{item_id}" not in seen_command_ids):
                if item_id:
                    seen_command_ids.add(f"tool:{item_id}")
                self._emit_activity(callback, "tool_invoked", tool=str(tool_name), args=item.get("arguments") or {})

        if event_type == "turn.completed" and not assistant_messages:
            turn = event.get("turn") or {}
            response = turn.get("final_response") or turn.get("last_agent_message") or ""
            if response:
                message = self._content_text(response)
                assistant_messages["turn-final"] = message
                self._emit_activity(
                    callback, "agent_output", text=message,
                    output_id="turn-final", replace=True, complete=True,
                )

    async def execute(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        history: Optional[List[Message]] = None,
        cwd: Optional[Path | str] = None,
        tools_description: Optional[str] = None,
        **kwargs: Any,
    ) -> EngineResponse:
        full_prompt_parts: List[str] = []

        if system_prompt:
            full_prompt_parts.append(f"System Instructions:\n{system_prompt}\n")

        if tools_description:
            full_prompt_parts.append(f"Available Tools & Delegation Capability:\n{tools_description}\n")

        if history:
            full_prompt_parts.append("Conversation History:")
            for msg in history:
                full_prompt_parts.append(f"[{msg.role.upper()}]: {msg.content}")
            full_prompt_parts.append("")

        full_prompt_parts.append(f"User Request:\n{prompt}")
        final_prompt = "\n".join(full_prompt_parts)

        extra_args = list(kwargs.get("extra_args") or self.config.get("extra_args") or [])
        if "--json" not in extra_args:
            extra_args.append("--json")
        # The orchestrator may be pointed at any workspace, including a Git
        # repository Codex has not individually trusted. The CLI's trust check
        # is separate from its sandbox/approval policy; skip only that check so
        # Codex can start in the selected workspace.
        if "--skip-git-repo-check" not in extra_args:
            extra_args.append("--skip-git-repo-check")
        # `codex exec` otherwise defaults to read-only. This orchestrator is
        # intended to modify the selected project, so grant writes only within
        # that workspace unless the agent config explicitly chooses a mode.
        has_sandbox_setting = any(
            arg in {"--sandbox", "-s"} or arg.startswith("--sandbox=")
            for arg in extra_args
        )
        if not has_sandbox_setting:
            extra_args.extend(["--sandbox", "workspace-write"])
        cmd = self._build_command(final_prompt, extra_args)
        working_dir = str(cwd) if cwd else None
        activity_callback = kwargs.get("activity_callback")
        model_settings = self._configured_model_settings(extra_args)

        try:
            self._emit_activity(
                activity_callback, "agent_process_started", executable=cmd[0],
                cwd=working_dir, ephemeral="--ephemeral" in extra_args, **model_settings,
            )
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=working_dir,
            )

            stdout_chunks: List[bytes] = []
            stderr_chunks: List[bytes] = []
            assistant_messages: Dict[str, str] = {}
            seen_command_ids: set[str] = set()
            protocol_errors: List[str] = []

            async def read_json_events(reader: asyncio.StreamReader) -> None:
                decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
                pending = ""

                def handle_line(line: str) -> None:
                    decoded = line.strip()
                    if not decoded:
                        return
                    try:
                        event = json.loads(decoded)
                    except json.JSONDecodeError:
                        self._emit_activity(activity_callback, "agent_output", text=decoded)
                        return
                    if isinstance(event, dict):
                        event_type = event.get("type")
                        if event_type in {"error", "turn.failed"}:
                            error_data = event.get("error") or event
                            if isinstance(error_data, dict):
                                message = str(error_data.get("message") or error_data)
                            else:
                                message = str(error_data)
                            protocol_errors.append(message)
                            self._emit_activity(
                                activity_callback, "agent_protocol_error", text=message,
                                event_type=event_type,
                            )
                            # Error and failed-turn events are terminal failures.
                            # Stop the process so a missing terminal event cannot
                            # leave the UI waiting indefinitely.
                            if process.returncode is None:
                                process.terminate()
                        elif event_type == "turn.started":
                            self._emit_activity(
                                activity_callback, "agent_progress", text="Codex turn started",
                            )
                        elif event_type == "thread.started":
                            self._emit_activity(
                                activity_callback, "agent_progress",
                                text=f"Codex thread started ({event.get('thread_id', 'id unavailable')})",
                            )
                        elif event_type == "item.started":
                            item = event.get("item") or {}
                            if isinstance(item, dict):
                                item_type = item.get("type") or item.get("kind") or "item"
                                label = item.get("name") or item.get("command") or item_type
                                self._emit_activity(
                                    activity_callback, "agent_progress",
                                    text=f"Codex started {label}",
                                )
                        self._handle_json_event(
                            event, activity_callback, assistant_messages, seen_command_ids
                        )

                while True:
                    chunk = await reader.read(4096)
                    if not chunk:
                        break
                    stdout_chunks.append(chunk)
                    pending += decoder.decode(chunk)
                    while "\n" in pending:
                        line, pending = pending.split("\n", 1)
                        handle_line(line)
                pending += decoder.decode(b"", final=True)
                if pending:
                    handle_line(pending)

            async def read_stderr(reader: asyncio.StreamReader) -> None:
                decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
                pending = ""

                def report(line: str) -> None:
                    diagnostic = line.strip()
                    if diagnostic:
                        self._emit_activity(activity_callback, "agent_diagnostic", text=diagnostic)
                    lowered = diagnostic.casefold()
                    if "thread/resume failed" in lowered or "failed to attach to task" in lowered:
                        protocol_errors.append(diagnostic)
                        self._emit_activity(
                            activity_callback, "agent_protocol_error",
                            text=diagnostic, event_type="stderr",
                        )
                        if process.returncode is None:
                            process.terminate()

                while True:
                    chunk = await reader.read(1024)
                    if not chunk:
                        break
                    stderr_chunks.append(chunk)
                    # Surface diagnostics while the process is running. Codex
                    # normally keeps progress in JSON stdout, but startup and
                    # authentication failures are often reported on stderr.
                    pending += decoder.decode(chunk)
                    while "\n" in pending:
                        line, pending = pending.split("\n", 1)
                        report(line)
                pending += decoder.decode(b"", final=True)
                if pending:
                    report(pending)

            await asyncio.gather(read_json_events(process.stdout), read_stderr(process.stderr))
            await process.wait()

            stdout_str = b"".join(stdout_chunks).decode("utf-8", errors="replace").strip()
            stderr_str = b"".join(stderr_chunks).decode("utf-8", errors="replace").strip()
            response_text = "\n".join(assistant_messages.values()).strip()
            protocol_error = "\n".join(protocol_errors)
            exit_code = 1 if protocol_error else (process.returncode or 0)
            self._emit_activity(
                activity_callback, "agent_process_finished",
                exit_code=exit_code,
            )

            return EngineResponse(
                text=response_text or stdout_str or stderr_str,
                raw_output=stdout_str,
                exit_code=exit_code,
                error=protocol_error or (stderr_str if exit_code != 0 else None),
                metadata={"engine": "codex", "cmd": cmd},
            )
        except FileNotFoundError:
            return EngineResponse(
                text="",
                exit_code=127,
                error=f"Codex CLI executable not found at '{self.resolved_cli}'. Ensure 'codex' is installed.",
                metadata={"engine": "codex"},
            )
        except Exception as e:
            return EngineResponse(
                text="",
                exit_code=1,
                error=f"Codex CLI execution failed: {str(e)}",
                metadata={"engine": "codex"},
            )
