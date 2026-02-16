"""Manifest models — the contract between tools and the bridge.

A manifest is a JSON file that describes one or more tools. The bridge reads
manifests to know what tools exist, what parameters they accept, and how to
invoke them. Adapters generate manifests automatically from PowerShell modules,
Python packages, or any other source. You can also write manifests by hand for
any CLI tool.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional


class Runtime(str, Enum):
    """Supported execution runtimes."""
    POWERSHELL = "powershell"
    PYTHON = "python"
    BASH = "bash"
    NODE = "node"
    CLI = "cli"


@dataclass
class ParameterDef:
    """Definition of a single tool parameter."""
    type: str = "string"            # string, integer, number, boolean, array
    description: str = ""
    required: bool = False
    default: Any = None
    enum: Optional[list[str]] = None        # ValidateSet / choices
    minimum: Optional[float] = None         # ValidateRange min
    maximum: Optional[float] = None         # ValidateRange max

    def to_json_schema(self) -> dict:
        """Convert to JSON Schema for MCP tool registration."""
        type_map = {
            "string": "string",
            "String": "string",
            "int": "integer",
            "Int32": "integer",
            "integer": "integer",
            "float": "number",
            "Double": "number",
            "number": "number",
            "bool": "boolean",
            "Boolean": "boolean",
            "boolean": "boolean",
            "SwitchParameter": "boolean",
            "array": "array",
            "String[]": "array",
        }
        schema: dict[str, Any] = {
            "type": type_map.get(self.type, "string"),
            "description": self.description,
        }
        if self.default is not None:
            schema["default"] = self.default
        if self.enum:
            schema["enum"] = self.enum
        if self.minimum is not None:
            schema["minimum"] = self.minimum
        if self.maximum is not None:
            schema["maximum"] = self.maximum
        return schema


@dataclass
class ToolDef:
    """Definition of a single tool."""
    name: str
    description: str = ""
    runtime: Runtime = Runtime.CLI
    module: Optional[str] = None        # PS module name, Python module, etc.
    function: Optional[str] = None      # Function/cmdlet name (defaults to name)
    command: Optional[str] = None       # CLI command template
    script: Optional[str] = None        # Script file path
    working_directory: Optional[str] = None
    timeout: int = 120
    output_format: str = "text"         # text, json
    parameters: dict[str, ParameterDef] = field(default_factory=dict)
    shell: Optional[str] = None         # Override shell for CLI runtime

    def get_mcp_schema(self) -> dict:
        """Generate MCP-compatible JSON Schema for this tool's parameters."""
        properties = {}
        required = []
        for param_name, param_def in self.parameters.items():
            properties[param_name] = param_def.to_json_schema()
            if param_def.required:
                required.append(param_name)
        schema = {
            "type": "object",
            "properties": properties,
        }
        if required:
            schema["required"] = required
        return schema


@dataclass
class ToolManifest:
    """A manifest file containing one or more tool definitions."""
    version: str = "1.0"
    description: str = ""
    # Defaults inherited by all tools in this manifest
    default_runtime: Optional[Runtime] = None
    default_module: Optional[str] = None
    default_timeout: int = 120
    tools: list[ToolDef] = field(default_factory=list)

    @classmethod
    def from_file(cls, path: Path) -> ToolManifest:
        """Load a manifest from a JSON file."""
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict) -> ToolManifest:
        """Parse a manifest from a dictionary."""
        defaults = data.get("defaults", {})
        default_runtime = defaults.get("runtime")
        default_module = defaults.get("module")
        default_timeout = defaults.get("timeout", 120)

        manifest = cls(
            version=data.get("version", "1.0"),
            description=data.get("description", ""),
            default_runtime=Runtime(default_runtime) if default_runtime else None,
            default_module=default_module,
            default_timeout=default_timeout,
        )

        for tool_data in data.get("tools", []):
            params = {}
            for pname, pdata in tool_data.get("parameters", {}).items():
                params[pname] = ParameterDef(
                    type=pdata.get("type", "string"),
                    description=pdata.get("description", ""),
                    required=pdata.get("required", False),
                    default=pdata.get("default"),
                    enum=pdata.get("enum"),
                    minimum=pdata.get("minimum"),
                    maximum=pdata.get("maximum"),
                )

            runtime_str = tool_data.get("runtime") or default_runtime
            tool = ToolDef(
                name=tool_data["name"],
                description=tool_data.get("description", ""),
                runtime=Runtime(runtime_str) if runtime_str else Runtime.CLI,
                module=tool_data.get("module") or default_module,
                function=tool_data.get("function"),
                command=tool_data.get("command"),
                script=tool_data.get("script"),
                working_directory=tool_data.get("working_directory"),
                timeout=tool_data.get("timeout", default_timeout),
                output_format=tool_data.get("output_format", "text"),
                parameters=params,
                shell=tool_data.get("shell"),
            )
            manifest.tools.append(tool)

        return manifest

    def to_dict(self) -> dict:
        """Serialize manifest to a dictionary (for saving)."""
        data: dict[str, Any] = {
            "version": self.version,
            "description": self.description,
        }

        defaults: dict[str, Any] = {}
        if self.default_runtime:
            defaults["runtime"] = self.default_runtime.value
        if self.default_module:
            defaults["module"] = self.default_module
        if self.default_timeout != 120:
            defaults["timeout"] = self.default_timeout
        if defaults:
            data["defaults"] = defaults

        tools_list = []
        for tool in self.tools:
            tool_data: dict[str, Any] = {"name": tool.name}
            if tool.description:
                tool_data["description"] = tool.description
            # Only include runtime/module if different from defaults
            if tool.runtime and tool.runtime != self.default_runtime:
                tool_data["runtime"] = tool.runtime.value
            if tool.module and tool.module != self.default_module:
                tool_data["module"] = tool.module
            if tool.function:
                tool_data["function"] = tool.function
            if tool.command:
                tool_data["command"] = tool.command
            if tool.script:
                tool_data["script"] = tool.script
            if tool.timeout != self.default_timeout:
                tool_data["timeout"] = tool.timeout
            if tool.output_format != "text":
                tool_data["output_format"] = tool.output_format
            if tool.shell:
                tool_data["shell"] = tool.shell

            if tool.parameters:
                params = {}
                for pname, pdef in tool.parameters.items():
                    pdata: dict[str, Any] = {"type": pdef.type}
                    if pdef.description:
                        pdata["description"] = pdef.description
                    if pdef.required:
                        pdata["required"] = True
                    if pdef.default is not None:
                        pdata["default"] = pdef.default
                    if pdef.enum:
                        pdata["enum"] = pdef.enum
                    if pdef.minimum is not None:
                        pdata["minimum"] = pdef.minimum
                    if pdef.maximum is not None:
                        pdata["maximum"] = pdef.maximum
                    params[pname] = pdata
                tool_data["parameters"] = params

            tools_list.append(tool_data)

        data["tools"] = tools_list
        return data

    def save(self, path: Path) -> None:
        """Save manifest to a JSON file."""
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)
        print(f"Manifest saved to {path}")


def load_manifests(directory: Path) -> list[ToolDef]:
    """Load all manifests from a directory and return a flat list of tools."""
    tools: list[ToolDef] = []
    if not directory.exists():
        return tools

    for manifest_file in sorted(directory.glob("*.json")):
        try:
            manifest = ToolManifest.from_file(manifest_file)
            tools.extend(manifest.tools)
            print(f"  Loaded {len(manifest.tools)} tools from {manifest_file.name}")
        except Exception as e:
            print(f"  WARNING: Failed to load {manifest_file.name}: {e}")

    return tools
