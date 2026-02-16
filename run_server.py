#!/usr/bin/env python3
"""Start the ToolBridge MCP server.

Usage:
    python run_server.py                          # Load from ./manifests/
    python run_server.py --manifests /path/to/dir # Custom manifest directory
    python run_server.py --name "my-tools"        # Custom server name
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from toolbridge.server import ToolBridgeServer


def main():
    parser = argparse.ArgumentParser(description="ToolBridge MCP Server")
    parser.add_argument(
        "--manifests", "-m",
        type=str,
        default="manifests",
        help="Directory containing manifest JSON files (default: manifests/)",
    )
    parser.add_argument(
        "--name",
        type=str,
        default="toolbridge",
        help="Server name reported to MCP clients (default: toolbridge)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging",
    )

    args = parser.parse_args()

    # Configure logging to stderr (stdout is for MCP protocol)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        stream=sys.stderr,
    )

    manifest_dir = Path(args.manifests)
    if not manifest_dir.exists():
        print(f"Manifest directory not found: {manifest_dir}", file=sys.stderr)
        print("Run generate_manifest.py first to create tool manifests.", file=sys.stderr)
        sys.exit(1)

    server = ToolBridgeServer(manifest_dir=manifest_dir, server_name=args.name)
    count = server.load_tools()

    if count == 0:
        print("No tools loaded. Add manifest files to the manifests/ directory.", file=sys.stderr)
        sys.exit(1)

    asyncio.run(server.run_stdio())


if __name__ == "__main__":
    main()
