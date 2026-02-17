# ToolBridge-MCP

A universal MCP server that exposes tools from **any language** to **any AI**. Write tools in PowerShell, Python, Bash, Node.js, or any CLI — ToolBridge makes them callable through the Model Context Protocol.

## The Idea

The bridge is dumb on purpose. It reads JSON manifests that describe tools — name, parameters, how to invoke them — and serves them over MCP. It doesn't know or care what language the tools are written in.

**Either side can change. The bridge stays universal.**

- Want to expose a PowerShell module? Generate a manifest.
- Want to wrap a Bash script? Write a 10-line manifest.
- Want to switch from Claude to another AI? The tools don't change.
- Want to add a new tool? Drop a JSON file in the manifests folder. No code changes.

## Architecture

```
┌──────────────┐     ┌──────────────────┐     ┌────────────────────┐
│   AI Client  │────▶│    ToolBridge     │────▶│   Your Tools       │
│              │ MCP │   (the bridge)    │     │                    │
│  Claude Code │◀────│                   │◀────│  PowerShell modules│
│  Cursor      │     │  Reads manifests  │     │  Python scripts    │
│  VS Code     │     │  Routes execution │     │  Bash commands     │
│  Custom app  │     │  Returns results  │     │  Node.js           │
└──────────────┘     └──────────────────┘     │  Any CLI tool      │
                                               └────────────────────┘
```

The manifest is the contract:
```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   Adapter    │────▶│   Manifest   │◀────│   Bridge     │
│              │     │   (JSON)     │     │              │
│  Auto-scans  │     │              │     │  Reads these │
│  PS modules  │     │  - name      │     │  Serves via  │
│  Py packages │     │  - params    │     │  MCP         │
│  Or: by hand │     │  - runtime   │     │              │
└──────────────┘     │  - command   │     └──────────────┘
                     └──────────────┘
```

## Quick Start

### 1. Generate manifests from your PowerShell modules

```bash
python generate_manifest.py --powershell AD-SecurityAudit M365-SecurityBaseline EntraID-SecurityAudit
```

This auto-discovers every exported function, reads parameter types, ValidateSet values, ValidateRange bounds, mandatory flags, and help text — then saves a manifest JSON per module.

### 2. Start the MCP server

```bash
python run_server.py
```

### 3. Connect from Claude Code

Add to your Claude Code MCP config (`~/.claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "toolbridge": {
      "command": "python",
      "args": ["C:/Users/larro/Projects/ToolBridge-MCP/run_server.py"]
    }
  }
}
```

Now Claude can call your PowerShell tools directly.

## Writing Manifests by Hand

For any CLI tool, write a manifest — no adapter needed:

```json
{
  "version": "1.0",
  "description": "Network diagnostic tools",
  "tools": [
    {
      "name": "ping-host",
      "description": "Ping a host and return results",
      "runtime": "cli",
      "command": "ping -c 4 {host}",
      "parameters": {
        "host": {
          "type": "string",
          "description": "Hostname or IP to ping",
          "required": true
        }
      }
    },
    {
      "name": "check-ssl",
      "description": "Check SSL certificate expiration",
      "runtime": "bash",
      "command": "echo | openssl s_client -connect {host}:443 2>/dev/null | openssl x509 -noout -dates",
      "parameters": {
        "host": {
          "type": "string",
          "description": "Hostname to check",
          "required": true
        }
      }
    }
  ]
}
```

Save as `manifests/network-tools.json` and restart the server. Done.

## Manifest Format

```json
{
  "version": "1.0",
  "description": "What this manifest contains",
  "defaults": {
    "runtime": "powershell",
    "module": "My-Module",
    "timeout": 120
  },
  "tools": [
    {
      "name": "Get-Something",
      "description": "Human-readable description shown to AI",
      "runtime": "powershell|python|bash|node|cli",
      "module": "Module-Name",
      "function": "Function-Name",
      "command": "cli command with {param} placeholders",
      "script": "path/to/script.py",
      "timeout": 120,
      "output_format": "text|json",
      "parameters": {
        "ParamName": {
          "type": "string|integer|number|boolean|array",
          "description": "Shown to AI for context",
          "required": true,
          "default": "default-value",
          "enum": ["Option1", "Option2"],
          "minimum": 1,
          "maximum": 100
        }
      }
    }
  ]
}
```

### Supported Runtimes

| Runtime | Invoke via | Best for |
|---|---|---|
| `powershell` | `module` + `function` | PowerShell modules and cmdlets |
| `python` | `module` + `function` or `script` | Python packages and scripts |
| `bash` | `command` or `script` | Shell commands, Linux tools |
| `node` | `script` or `command` | Node.js scripts |
| `cli` | `command` | Any command-line tool |

### Auto-Discovery Adapters

Adapters scan existing tool sources and generate manifests automatically:

```bash
# PowerShell: reads Get-Command, parameter metadata, Get-Help
python generate_manifest.py --powershell Module-Name

# From a specific path (module not installed globally)
python generate_manifest.py --powershell Module-Name --path "/path/to/module"
```

## Design Principles

1. **The bridge is universal.** It knows MCP protocol and manifest format. It never knows about PowerShell, Python, or any specific tool.

2. **Manifests are the contract.** Tools declare what they do. The bridge serves them. Neither side needs to know about the other.

3. **Zero runtime dependencies.** The bridge uses only Python standard library. No pip installs, no version conflicts, no supply chain risk.

4. **Auto-discovery is optional.** Adapters generate manifests as a convenience. You can always write manifests by hand for any tool in any language.

5. **Security by design.** The bridge only executes tools defined in local manifest files. Parameter values are shell-escaped. Timeouts are enforced.

## Project Structure

```
ToolBridge-MCP/
├── toolbridge/
│   ├── __init__.py             # Package version
│   ├── server.py               # MCP server (the bridge)
│   ├── manifest.py             # Manifest models and loader
│   ├── executor.py             # Runtime execution engine
│   └── adapters/
│       ├── __init__.py
│       └── powershell.py       # PS module → manifest generator
├── manifests/                  # Drop manifest JSONs here
├── generate_manifest.py        # CLI: auto-generate manifests
├── run_server.py               # CLI: start the server
├── tests/
│   └── test_toolbridge.py      # pytest suite
├── pyproject.toml
├── requirements.txt            # Zero dependencies
├── LICENSE
└── README.md
```

## Requirements

- **Python 3.10+** (standard library only — no pip dependencies)
- **PowerShell 5.1+ or 7+** (only if using PowerShell tools)
- Any other runtime your tools need

## Feedback & Contributions

This tool was built to solve real admin pain points. If you have ideas for improvement, find a bug, or want to suggest a feature:

- **Open an issue** on this repo — [Issues](../../issues)
- Feature requests, bug reports, and general feedback are all welcome
- Pull requests are appreciated if you want to contribute directly

If you find this useful, check out my other tools at [larro1991.github.io](https://larro1991.github.io)

## License

MIT License - see [LICENSE](LICENSE) for details.
