"""PowerShell adapter — discovers functions from PS modules and generates manifests.

This adapter runs PowerShell to introspect a module's exported functions, reads
parameter metadata (types, ValidateSet, ValidateRange, Mandatory, help text),
and generates a ToolBridge manifest JSON file.

Usage:
    python generate_manifest.py --powershell AD-SecurityAudit
    python generate_manifest.py --powershell M365-SecurityBaseline --output manifests/
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from ..manifest import ParameterDef, Runtime, ToolDef, ToolManifest

# Common parameters to exclude (PowerShell adds these to every advanced function)
COMMON_PARAMS = {
    "Verbose", "Debug", "ErrorAction", "WarningAction", "InformationAction",
    "ErrorVariable", "WarningVariable", "InformationVariable", "OutVariable",
    "OutBuffer", "PipelineVariable", "ProgressAction", "WhatIf", "Confirm",
}


def find_powershell() -> str:
    """Find the PowerShell executable."""
    for candidate in ["pwsh", "powershell"]:
        path = shutil.which(candidate)
        if path:
            return path

    for candidate in [
        r"C:\Program Files\PowerShell\7\pwsh.exe",
        r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
    ]:
        if os.path.isfile(candidate):
            return candidate

    raise RuntimeError("PowerShell not found.")


def discover_module(module_name: str, module_path: str | None = None) -> ToolManifest:
    """Discover functions from a PowerShell module and return a ToolManifest.

    Args:
        module_name: Name of the PS module (e.g., 'AD-SecurityAudit')
        module_path: Optional path to the module directory (for non-installed modules)
    """
    pwsh = find_powershell()

    import_line = f"Import-Module '{module_path}'" if module_path else f"Import-Module '{module_name}'"

    # PowerShell script that introspects a module and outputs JSON
    ps_script = f"""
$ErrorActionPreference = 'Stop'
{import_line} -Force

$mod = Get-Module '{module_name}'
if (-not $mod) {{ $mod = Get-Module (Split-Path '{module_path or module_name}' -Leaf) -ErrorAction SilentlyContinue }}

$functions = Get-Command -Module $mod.Name -CommandType Function
$result = @()

foreach ($func in $functions) {{
    $help = Get-Help $func.Name -ErrorAction SilentlyContinue
    $synopsis = if ($help.Synopsis) {{ $help.Synopsis.Trim() }} else {{ '' }}

    $params = @{{}}
    foreach ($p in $func.Parameters.GetEnumerator()) {{
        $pName = $p.Key
        $pInfo = $p.Value

        # Skip common parameters
        if ($pName -in @({', '.join(f"'{p}'" for p in COMMON_PARAMS)})) {{ continue }}

        $paramData = @{{
            type = $pInfo.ParameterType.Name
            mandatory = $false
            description = ''
        }}

        foreach ($attr in $pInfo.Attributes) {{
            if ($attr -is [System.Management.Automation.ParameterAttribute]) {{
                $paramData.mandatory = $attr.Mandatory
                if ($attr.HelpMessage) {{ $paramData.description = $attr.HelpMessage }}
            }}
            if ($attr -is [System.Management.Automation.ValidateSetAttribute]) {{
                $paramData['validateSet'] = @($attr.ValidValues)
            }}
            if ($attr -is [System.Management.Automation.ValidateRangeAttribute]) {{
                $paramData['minimum'] = $attr.MinRange
                $paramData['maximum'] = $attr.MaxRange
            }}
        }}

        # Try to get description from help
        if (-not $paramData.description -and $help.parameters) {{
            $helpParam = $help.parameters.parameter | Where-Object {{ $_.Name -eq $pName }}
            if ($helpParam.description) {{
                $desc = ($helpParam.description | Out-String).Trim()
                if ($desc) {{ $paramData.description = $desc }}
            }}
        }}

        $params[$pName] = $paramData
    }}

    $result += @{{
        name = $func.Name
        description = $synopsis
        parameters = $params
    }}
}}

$result | ConvertTo-Json -Depth 5 -Compress
"""

    proc = subprocess.run(
        [pwsh, "-NoProfile", "-NonInteractive", "-Command", ps_script],
        capture_output=True,
        text=True,
        timeout=60,
    )

    if proc.returncode != 0:
        raise RuntimeError(f"PowerShell discovery failed:\n{proc.stderr}")

    raw = proc.stdout.strip()
    if not raw:
        raise RuntimeError(f"No functions found in module '{module_name}'.")

    functions_data = json.loads(raw)

    # PowerShell returns a single object (not array) when there's only one function
    if isinstance(functions_data, dict):
        functions_data = [functions_data]

    # Build manifest
    manifest = ToolManifest(
        version="1.0",
        description=f"Auto-generated from PowerShell module: {module_name}",
        default_runtime=Runtime.POWERSHELL,
        default_module=module_path or module_name,
    )

    for func_data in functions_data:
        params = {}
        for pname, pdata in func_data.get("parameters", {}).items():
            params[pname] = ParameterDef(
                type=pdata.get("type", "String"),
                description=pdata.get("description", ""),
                required=pdata.get("mandatory", False),
                enum=pdata.get("validateSet"),
                minimum=pdata.get("minimum"),
                maximum=pdata.get("maximum"),
            )

        tool = ToolDef(
            name=func_data["name"],
            description=func_data.get("description", ""),
            runtime=Runtime.POWERSHELL,
            module=module_path or module_name,
            function=func_data["name"],
            parameters=params,
        )
        manifest.tools.append(tool)

    return manifest


def generate_manifest_file(
    module_name: str,
    output_dir: Path,
    module_path: str | None = None,
) -> Path:
    """Discover a PowerShell module and save as a manifest JSON file."""
    manifest = discover_module(module_name, module_path)
    output_file = output_dir / f"{module_name}.json"
    manifest.save(output_file)
    return output_file
