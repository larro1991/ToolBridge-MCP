"""Tests for ToolBridge-MCP core components."""

import asyncio
import json
import tempfile
from pathlib import Path

import pytest

from toolbridge.manifest import (
    ParameterDef,
    Runtime,
    ToolDef,
    ToolManifest,
    load_manifests,
)
from toolbridge.executor import ToolExecutor, ExecutionError
from toolbridge.server import ToolBridgeServer


# ── Manifest Tests ──────────────────────────────────────────────────


class TestParameterDef:
    def test_string_to_json_schema(self):
        p = ParameterDef(type="string", description="A name", required=True)
        schema = p.to_json_schema()
        assert schema["type"] == "string"
        assert schema["description"] == "A name"

    def test_integer_with_range(self):
        p = ParameterDef(type="Int32", minimum=1, maximum=90)
        schema = p.to_json_schema()
        assert schema["type"] == "integer"
        assert schema["minimum"] == 1
        assert schema["maximum"] == 90

    def test_enum_values(self):
        p = ParameterDef(type="string", enum=["High", "Medium", "Low"])
        schema = p.to_json_schema()
        assert schema["enum"] == ["High", "Medium", "Low"]

    def test_switch_parameter(self):
        p = ParameterDef(type="SwitchParameter")
        schema = p.to_json_schema()
        assert schema["type"] == "boolean"

    def test_ps_type_mapping(self):
        """PowerShell type names map to JSON Schema types."""
        assert ParameterDef(type="String").to_json_schema()["type"] == "string"
        assert ParameterDef(type="Int32").to_json_schema()["type"] == "integer"
        assert ParameterDef(type="Double").to_json_schema()["type"] == "number"
        assert ParameterDef(type="Boolean").to_json_schema()["type"] == "boolean"
        assert ParameterDef(type="String[]").to_json_schema()["type"] == "array"


class TestToolDef:
    def test_mcp_schema_basic(self):
        tool = ToolDef(
            name="Get-Something",
            parameters={
                "Name": ParameterDef(type="string", required=True),
                "Count": ParameterDef(type="integer", required=False, default=10),
            },
        )
        schema = tool.get_mcp_schema()
        assert schema["type"] == "object"
        assert "Name" in schema["properties"]
        assert schema["required"] == ["Name"]

    def test_mcp_schema_no_required(self):
        tool = ToolDef(
            name="Get-All",
            parameters={"Limit": ParameterDef(type="integer")},
        )
        schema = tool.get_mcp_schema()
        assert "required" not in schema


class TestToolManifest:
    def test_roundtrip_save_load(self, tmp_path):
        """Manifest survives save/load roundtrip."""
        manifest = ToolManifest(
            description="Test manifest",
            default_runtime=Runtime.POWERSHELL,
            default_module="TestModule",
            tools=[
                ToolDef(
                    name="Get-Thing",
                    description="Gets a thing",
                    runtime=Runtime.POWERSHELL,
                    module="TestModule",
                    parameters={
                        "Name": ParameterDef(type="string", required=True),
                        "Days": ParameterDef(type="integer", minimum=1, maximum=90),
                    },
                )
            ],
        )

        path = tmp_path / "test.json"
        manifest.save(path)

        loaded = ToolManifest.from_file(path)
        assert len(loaded.tools) == 1
        assert loaded.tools[0].name == "Get-Thing"
        assert loaded.tools[0].parameters["Name"].required is True
        assert loaded.tools[0].parameters["Days"].minimum == 1

    def test_defaults_inheritance(self):
        """Tools inherit runtime and module from defaults."""
        data = {
            "defaults": {"runtime": "powershell", "module": "MyModule"},
            "tools": [
                {"name": "Get-Foo", "description": "Gets foo"},
                {"name": "Get-Bar", "description": "Gets bar", "runtime": "bash", "command": "echo bar"},
            ],
        }
        manifest = ToolManifest.from_dict(data)
        assert manifest.tools[0].runtime == Runtime.POWERSHELL
        assert manifest.tools[0].module == "MyModule"
        # Override
        assert manifest.tools[1].runtime == Runtime.BASH

    def test_load_manifests_from_directory(self, tmp_path):
        """load_manifests reads all JSON files from a directory."""
        m1 = {"tools": [{"name": "tool-a", "runtime": "cli", "command": "echo a"}]}
        m2 = {"tools": [{"name": "tool-b", "runtime": "cli", "command": "echo b"}]}
        (tmp_path / "a.json").write_text(json.dumps(m1))
        (tmp_path / "b.json").write_text(json.dumps(m2))

        tools = load_manifests(tmp_path)
        names = [t.name for t in tools]
        assert "tool-a" in names
        assert "tool-b" in names

    def test_bad_manifest_skipped(self, tmp_path):
        """Invalid JSON files are skipped with a warning."""
        (tmp_path / "bad.json").write_text("not valid json{{{")
        (tmp_path / "good.json").write_text(json.dumps({
            "tools": [{"name": "ok-tool", "runtime": "cli", "command": "echo ok"}]
        }))

        tools = load_manifests(tmp_path)
        assert len(tools) == 1
        assert tools[0].name == "ok-tool"


# ── Executor Tests ──────────────────────────────────────────────────


class TestExecutor:
    @pytest.fixture
    def executor(self):
        return ToolExecutor()

    @pytest.mark.asyncio
    async def test_cli_echo(self, executor):
        """CLI runtime can execute a simple echo command."""
        tool = ToolDef(
            name="test-echo",
            runtime=Runtime.CLI,
            command="echo hello",
            timeout=10,
        )
        # Use bash on all platforms for this test
        tool.shell = "bash"
        result = await executor.execute(tool, {})
        assert "hello" in result

    @pytest.mark.asyncio
    async def test_cli_with_params(self, executor):
        """CLI runtime interpolates parameters into command template."""
        tool = ToolDef(
            name="test-echo-param",
            runtime=Runtime.CLI,
            command="echo {message}",
            timeout=10,
        )
        tool.shell = "bash"
        result = await executor.execute(tool, {"message": "world"})
        assert "world" in result

    @pytest.mark.asyncio
    async def test_timeout(self, executor):
        """Execution respects timeout."""
        tool = ToolDef(
            name="test-slow",
            runtime=Runtime.CLI,
            command="sleep 30",
            timeout=1,
        )
        tool.shell = "bash"
        with pytest.raises(ExecutionError, match="timed out"):
            await executor.execute(tool, {})

    @pytest.mark.asyncio
    async def test_nonzero_exit_raises(self, executor):
        """Non-zero exit code raises ExecutionError."""
        tool = ToolDef(
            name="test-fail",
            runtime=Runtime.CLI,
            command="exit 1",
            timeout=10,
        )
        tool.shell = "bash"
        with pytest.raises(ExecutionError):
            await executor.execute(tool, {})

    def test_ps_param_formatting(self, executor):
        """PowerShell parameter formatting handles types correctly."""
        assert executor._format_ps_param("Name", "Alice") == "-Name 'Alice'"
        assert executor._format_ps_param("Count", 42) == "-Count 42"
        assert executor._format_ps_param("Force", True) == "-Force"
        assert executor._format_ps_param("Force", False) == ""
        assert executor._format_ps_param("Tags", ["a", "b"]) == "-Tags @('a','b')"

    def test_ps_param_escapes_quotes(self, executor):
        """Single quotes in string values are escaped for PowerShell."""
        result = executor._format_ps_param("Name", "O'Brien")
        assert result == "-Name 'O''Brien'"

    def test_command_interpolation_escapes(self):
        """Command template interpolation shell-escapes values."""
        result = ToolExecutor._interpolate_command("echo {msg}", {"msg": "hello; rm -rf /"})
        # The value should be quoted/escaped
        assert "rm -rf" not in result or "'" in result


# ── Server Tests ────────────────────────────────────────────────────


class TestServer:
    @pytest.fixture
    def server_with_tools(self, tmp_path):
        manifest = {
            "tools": [
                {
                    "name": "test-echo",
                    "description": "Echo a message",
                    "runtime": "cli",
                    "command": "echo {message}",
                    "shell": "bash",
                    "parameters": {
                        "message": {"type": "string", "description": "What to echo", "required": True}
                    },
                }
            ],
        }
        (tmp_path / "test.json").write_text(json.dumps(manifest))

        server = ToolBridgeServer(manifest_dir=tmp_path)
        server.load_tools()
        return server

    def test_tool_list(self, server_with_tools):
        tools = server_with_tools.get_tool_list()
        assert len(tools) == 1
        assert tools[0]["name"] == "test-echo"
        assert "message" in tools[0]["inputSchema"]["properties"]

    @pytest.mark.asyncio
    async def test_call_tool(self, server_with_tools):
        result = await server_with_tools.call_tool("test-echo", {"message": "hello"})
        assert "hello" in result

    @pytest.mark.asyncio
    async def test_call_unknown_tool(self, server_with_tools):
        with pytest.raises(ExecutionError, match="Unknown tool"):
            await server_with_tools.call_tool("nonexistent", {})

    @pytest.mark.asyncio
    async def test_jsonrpc_initialize(self, server_with_tools):
        request = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
        response = await server_with_tools._handle_jsonrpc(request)
        assert response["result"]["serverInfo"]["name"] == "toolbridge"
        assert "tools" in response["result"]["capabilities"]

    @pytest.mark.asyncio
    async def test_jsonrpc_tools_list(self, server_with_tools):
        request = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
        response = await server_with_tools._handle_jsonrpc(request)
        assert len(response["result"]["tools"]) == 1

    @pytest.mark.asyncio
    async def test_jsonrpc_tools_call(self, server_with_tools):
        request = {
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "test-echo", "arguments": {"message": "test123"}},
        }
        response = await server_with_tools._handle_jsonrpc(request)
        assert response["result"]["isError"] is False
        assert "test123" in response["result"]["content"][0]["text"]
