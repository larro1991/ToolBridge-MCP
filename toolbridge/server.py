"""ToolBridge MCP Server — the universal bridge.

This is the core of ToolBridge. It reads tool manifests, registers them as MCP
tools, and routes execution to the appropriate runtime via the executor. It does
not know or care what language the tools are written in.

To add tools: drop a manifest JSON in the manifests/ directory.
To add runtimes: extend the executor.
This file should never need to change.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any

from .executor import ExecutionError, ToolExecutor
from .manifest import ToolDef, load_manifests

logger = logging.getLogger("toolbridge")


class ToolBridgeServer:
    """MCP-compatible server that bridges any tool to any AI."""

    def __init__(self, manifest_dir: Path, server_name: str = "toolbridge") -> None:
        self.server_name = server_name
        self.manifest_dir = manifest_dir
        self.executor = ToolExecutor()
        self.tools: dict[str, ToolDef] = {}

    def load_tools(self) -> int:
        """Load all tools from manifests. Returns count of tools loaded."""
        print(f"Loading manifests from {self.manifest_dir}")
        tool_list = load_manifests(self.manifest_dir)
        self.tools = {tool.name: tool for tool in tool_list}
        print(f"Total tools registered: {len(self.tools)}")
        return len(self.tools)

    def get_tool_list(self) -> list[dict[str, Any]]:
        """Return MCP tools/list response."""
        return [
            {
                "name": tool.name,
                "description": tool.description or f"Execute {tool.name}",
                "inputSchema": tool.get_mcp_schema(),
            }
            for tool in self.tools.values()
        ]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """Execute a tool by name with given arguments."""
        tool = self.tools.get(name)
        if not tool:
            available = ", ".join(sorted(self.tools.keys()))
            raise ExecutionError(f"Unknown tool: {name}. Available: {available}")

        logger.info(f"Executing {name} (runtime={tool.runtime.value})")
        result = await self.executor.execute(tool, arguments)
        logger.info(f"Completed {name} ({len(result)} chars)")
        return result

    # ── MCP Protocol (JSON-RPC over stdio) ──────────────────────────

    async def run_stdio(self) -> None:
        """Run as an MCP server over stdin/stdout (JSON-RPC).

        Reads one JSON-RPC message per line from stdin, processes it,
        and writes the response as one JSON line to stdout.
        Compatible with Windows and Unix.
        """
        print(f"ToolBridge MCP Server v1.0.0 — {len(self.tools)} tools loaded", file=sys.stderr)
        print("Listening on stdio...", file=sys.stderr)

        loop = asyncio.get_event_loop()

        # Use thread-based stdin reading for cross-platform compatibility
        # (Windows ProactorEventLoop doesn't support pipe reading natively)
        import concurrent.futures
        reader_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)

        def read_line() -> str | None:
            try:
                line = sys.stdin.readline()
                return line if line else None
            except (EOFError, OSError):
                return None

        writer = sys.stdout

        while True:
            try:
                line = await loop.run_in_executor(reader_pool, read_line)
                if line is None:
                    break  # EOF

                line_str = line.strip()
                if not line_str:
                    continue

                request = json.loads(line_str)
                response = await self._handle_jsonrpc(request)

                if response is not None:
                    writer.write(json.dumps(response) + "\n")
                    writer.flush()

            except json.JSONDecodeError as e:
                logger.error(f"Invalid JSON: {e}")
                error_response = self._error_response(None, -32700, f"Parse error: {e}")
                writer.write(json.dumps(error_response) + "\n")
                writer.flush()
            except Exception as e:
                logger.error(f"Unexpected error: {e}")

        reader_pool.shutdown(wait=False)

    async def _handle_jsonrpc(self, request: dict) -> dict | None:
        """Handle a single JSON-RPC request."""
        method = request.get("method", "")
        req_id = request.get("id")
        params = request.get("params", {})

        # Notifications (no id) don't get responses
        if req_id is None and method.startswith("notifications/"):
            return None

        if method == "initialize":
            return self._success_response(req_id, {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {"listChanged": False},
                },
                "serverInfo": {
                    "name": self.server_name,
                    "version": "1.0.0",
                },
            })

        elif method == "tools/list":
            return self._success_response(req_id, {
                "tools": self.get_tool_list(),
            })

        elif method == "tools/call":
            tool_name = params.get("name", "")
            arguments = params.get("arguments", {})
            try:
                result = await self.call_tool(tool_name, arguments)
                return self._success_response(req_id, {
                    "content": [{"type": "text", "text": result}],
                    "isError": False,
                })
            except ExecutionError as e:
                return self._success_response(req_id, {
                    "content": [{"type": "text", "text": f"Error: {e}"}],
                    "isError": True,
                })
            except Exception as e:
                return self._success_response(req_id, {
                    "content": [{"type": "text", "text": f"Unexpected error: {e}"}],
                    "isError": True,
                })

        elif method == "ping":
            return self._success_response(req_id, {})

        else:
            # Unknown method — return error for requests, ignore notifications
            if req_id is not None:
                return self._error_response(req_id, -32601, f"Method not found: {method}")
            return None

    @staticmethod
    def _success_response(req_id: Any, result: Any) -> dict:
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    @staticmethod
    def _error_response(req_id: Any, code: int, message: str) -> dict:
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}
