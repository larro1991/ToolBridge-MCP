#!/usr/bin/env python3
"""Generate tool manifests from PowerShell modules (or other sources).

Usage:
    # From an installed PowerShell module
    python generate_manifest.py --powershell AD-SecurityAudit

    # From a module path (not installed)
    python generate_manifest.py --powershell AD-SecurityAudit --path "C:/Users/larro/Projects/AD-SecurityAudit"

    # Multiple modules at once
    python generate_manifest.py --powershell AD-SecurityAudit M365-SecurityBaseline EntraID-SecurityAudit

    # Custom output directory
    python generate_manifest.py --powershell AD-SecurityAudit --output ./my-manifests/
"""

import argparse
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(
        description="Generate ToolBridge manifests from existing tool sources.",
    )
    parser.add_argument(
        "--powershell",
        nargs="+",
        metavar="MODULE",
        help="PowerShell module name(s) to discover",
    )
    parser.add_argument(
        "--path",
        type=str,
        default=None,
        help="Path to module directory (for modules not in PSModulePath)",
    )
    parser.add_argument(
        "--output", "-o",
        type=str,
        default="manifests",
        help="Output directory for manifest files (default: manifests/)",
    )

    args = parser.parse_args()

    if not args.powershell:
        parser.print_help()
        sys.exit(1)

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    from toolbridge.adapters.powershell import generate_manifest_file

    for module_name in args.powershell:
        print(f"\nDiscovering module: {module_name}")
        try:
            # If --path given, use it; otherwise try the Projects directory convention
            module_path = args.path
            if not module_path:
                # Check common locations
                projects_path = Path.home() / "Projects" / module_name
                if (projects_path / f"{module_name}.psd1").exists():
                    module_path = str(projects_path / f"{module_name}.psd1")

            output_file = generate_manifest_file(module_name, output_dir, module_path)
            print(f"  -> {output_file}")
        except Exception as e:
            print(f"  ERROR: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
