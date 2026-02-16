"""Tool execution engine — routes tool calls to the correct runtime.

The executor is the only part of the bridge that knows how to actually run things.
Each runtime (PowerShell, Python, Bash, CLI, Node) has its own execution strategy.
The bridge never touches this — it just passes ToolDef + arguments to execute().
"""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import shutil
import sys
from typing import Any

from .manifest import Runtime, ToolDef


class ExecutionError(Exception):
    """Raised when tool execution fails."""

    def __init__(self, message: str, exit_code: int = 1, stderr: str = ""):
        super().__init__(message)
        self.exit_code = exit_code
        self.stderr = stderr


class ToolExecutor:
    """Executes tools in their declared runtime."""

    def __init__(self) -> None:
        self._pwsh_path: str | None = None
        self._python_path: str | None = None
        self._node_path: str | None = None

    # ── Public API ──────────────────────────────────────────────────

    async def execute(self, tool: ToolDef, arguments: dict[str, Any]) -> str:
        """Execute a tool and return its output as a string."""
        runtime_handlers = {
            Runtime.POWERSHELL: self._exec_powershell,
            Runtime.PYTHON: self._exec_python,
            Runtime.BASH: self._exec_bash,
            Runtime.NODE: self._exec_node,
            Runtime.CLI: self._exec_cli,
        }

        handler = runtime_handlers.get(tool.runtime)
        if not handler:
            raise ExecutionError(f"Unsupported runtime: {tool.runtime}")

        return await handler(tool, arguments)

    # ── PowerShell ──────────────────────────────────────────────────

    async def _exec_powershell(self, tool: ToolDef, arguments: dict[str, Any]) -> str:
        """Execute a PowerShell function."""
        pwsh = self._find_powershell()

        # Build parameter string
        param_parts = []
        for key, value in arguments.items():
            param_parts.append(self._format_ps_param(key, value))

        # Build command
        cmd_parts = []
        if tool.module:
            cmd_parts.append(f"Import-Module '{tool.module}' -ErrorAction Stop")

        func_name = tool.function or tool.name
        func_call = f"{func_name} {' '.join(param_parts)}".strip()

        # Pipe to JSON for structured output
        if tool.output_format == "json":
            func_call += " | ConvertTo-Json -Depth 10 -Compress"

        cmd_parts.append(func_call)
        command = "; ".join(cmd_parts)

        return await self._run_process(
            [pwsh, "-NoProfile", "-NonInteractive", "-Command", command],
            timeout=tool.timeout,
            cwd=tool.working_directory,
        )

    def _format_ps_param(self, key: str, value: Any) -> str:
        """Format a single PowerShell parameter."""
        if isinstance(value, bool):
            return f"-{key}" if value else ""
        if isinstance(value, (int, float)):
            return f"-{key} {value}"
        if isinstance(value, list):
            items = ",".join(f"'{v}'" for v in value)
            return f"-{key} @({items})"
        # String — escape single quotes
        safe_value = str(value).replace("'", "''")
        return f"-{key} '{safe_value}'"

    def _find_powershell(self) -> str:
        """Find the PowerShell executable."""
        if self._pwsh_path:
            return self._pwsh_path

        # Prefer pwsh (PS 7+), fall back to powershell.exe (5.1)
        for candidate in ["pwsh", "powershell"]:
            path = shutil.which(candidate)
            if path:
                self._pwsh_path = path
                return path

        # Windows-specific fallback
        for candidate in [
            r"C:\Program Files\PowerShell\7\pwsh.exe",
            r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
        ]:
            if os.path.isfile(candidate):
                self._pwsh_path = candidate
                return candidate

        raise ExecutionError("PowerShell not found. Install pwsh or ensure powershell.exe is in PATH.")

    # ── Python ──────────────────────────────────────────────────────

    async def _exec_python(self, tool: ToolDef, arguments: dict[str, Any]) -> str:
        """Execute a Python function or script."""
        python = self._find_python()

        if tool.script:
            # Run a script file with arguments as JSON via stdin
            args_json = json.dumps(arguments)
            return await self._run_process(
                [python, tool.script],
                input_data=args_json,
                timeout=tool.timeout,
                cwd=tool.working_directory,
            )
        elif tool.module and tool.function:
            # Import module and call function
            code = (
                f"import json, sys; "
                f"from {tool.module} import {tool.function}; "
                f"args = json.loads(sys.stdin.read()); "
                f"result = {tool.function}(**args); "
                f"print(json.dumps(result) if isinstance(result, (dict, list)) else str(result))"
            )
            args_json = json.dumps(arguments)
            return await self._run_process(
                [python, "-c", code],
                input_data=args_json,
                timeout=tool.timeout,
                cwd=tool.working_directory,
            )
        else:
            raise ExecutionError("Python tool must specify 'script' or both 'module' and 'function'.")

    def _find_python(self) -> str:
        """Find the Python executable."""
        if self._python_path:
            return self._python_path

        for candidate in ["python3", "python"]:
            path = shutil.which(candidate)
            if path:
                self._python_path = path
                return path

        raise ExecutionError("Python not found in PATH.")

    # ── Bash ────────────────────────────────────────────────────────

    async def _exec_bash(self, tool: ToolDef, arguments: dict[str, Any]) -> str:
        """Execute a Bash command or script."""
        if tool.script:
            cmd = ["bash", tool.script]
            # Pass arguments as environment variables
            env = {**os.environ, **{f"TOOL_{k.upper()}": str(v) for k, v in arguments.items()}}
            return await self._run_process(
                cmd, timeout=tool.timeout, cwd=tool.working_directory, env=env,
            )
        elif tool.command:
            command = self._interpolate_command(tool.command, arguments)
            return await self._run_process(
                ["bash", "-c", command],
                timeout=tool.timeout,
                cwd=tool.working_directory,
            )
        else:
            raise ExecutionError("Bash tool must specify 'command' or 'script'.")

    # ── Node.js ─────────────────────────────────────────────────────

    async def _exec_node(self, tool: ToolDef, arguments: dict[str, Any]) -> str:
        """Execute a Node.js script."""
        node = self._find_node()

        if tool.script:
            args_json = json.dumps(arguments)
            return await self._run_process(
                [node, tool.script],
                input_data=args_json,
                timeout=tool.timeout,
                cwd=tool.working_directory,
            )
        elif tool.command:
            code = tool.command
            return await self._run_process(
                [node, "-e", code],
                input_data=json.dumps(arguments),
                timeout=tool.timeout,
                cwd=tool.working_directory,
            )
        else:
            raise ExecutionError("Node tool must specify 'script' or 'command'.")

    def _find_node(self) -> str:
        """Find the Node.js executable."""
        if self._node_path:
            return self._node_path

        path = shutil.which("node")
        if path:
            self._node_path = path
            return path

        raise ExecutionError("Node.js not found in PATH.")

    # ── CLI (generic) ───────────────────────────────────────────────

    async def _exec_cli(self, tool: ToolDef, arguments: dict[str, Any]) -> str:
        """Execute a generic CLI command."""
        if not tool.command:
            raise ExecutionError("CLI tool must specify 'command'.")

        command = self._interpolate_command(tool.command, arguments)
        shell_cmd = tool.shell or ("bash" if sys.platform != "win32" else "cmd")

        if shell_cmd == "cmd":
            return await self._run_process(
                ["cmd", "/c", command],
                timeout=tool.timeout,
                cwd=tool.working_directory,
            )
        else:
            return await self._run_process(
                [shell_cmd, "-c", command],
                timeout=tool.timeout,
                cwd=tool.working_directory,
            )

    # ── Shared execution ────────────────────────────────────────────

    @staticmethod
    def _interpolate_command(template: str, arguments: dict[str, Any]) -> str:
        """Safely interpolate arguments into a command template.

        Template uses {param_name} placeholders. Values are shell-escaped.
        """
        safe_args = {}
        for key, value in arguments.items():
            if isinstance(value, (int, float, bool)):
                safe_args[key] = str(value)
            else:
                safe_args[key] = shlex.quote(str(value))
        try:
            return template.format(**safe_args)
        except KeyError as e:
            raise ExecutionError(f"Missing required parameter in command template: {e}")

    @staticmethod
    async def _run_process(
        cmd: list[str],
        timeout: int = 120,
        cwd: str | None = None,
        input_data: str | None = None,
        env: dict | None = None,
    ) -> str:
        """Run a subprocess and return stdout."""
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.PIPE if input_data else None,
                cwd=cwd,
                env=env,
            )

            stdin_bytes = input_data.encode() if input_data else None
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=stdin_bytes),
                timeout=timeout,
            )

            stdout_text = stdout.decode("utf-8", errors="replace").strip()
            stderr_text = stderr.decode("utf-8", errors="replace").strip()

            if proc.returncode != 0:
                error_msg = stderr_text or stdout_text or f"Process exited with code {proc.returncode}"
                raise ExecutionError(error_msg, exit_code=proc.returncode, stderr=stderr_text)

            # Include stderr as a note if present (warnings, verbose output)
            if stderr_text and stdout_text:
                return f"{stdout_text}\n\n[stderr]\n{stderr_text}"

            return stdout_text or stderr_text or "(no output)"

        except asyncio.TimeoutError:
            proc.kill()
            raise ExecutionError(f"Tool execution timed out after {timeout} seconds.")
        except FileNotFoundError:
            raise ExecutionError(f"Executable not found: {cmd[0]}")
