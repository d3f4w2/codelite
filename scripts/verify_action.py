from __future__ import annotations

import argparse
import json
from pathlib import Path

from codelite.core.action_verify import verify_action_text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pre-verify structural actions before execution.")
    parser.add_argument("--action", required=True, help='Action text, e.g. "create file codelite/core/foo.py"')
    parser.add_argument("--workspace", default=".", help="Workspace root path (default: cwd)")
    parser.add_argument("--json", action="store_true", help="Print JSON result")
    args = parser.parse_args(argv)

    workspace_root = Path(args.workspace).resolve()
    result = verify_action_text(workspace_root, args.action)
    payload = result.to_dict()

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        badge = "VALID" if result.ok else "INVALID"
        symbol = "OK" if result.ok else "X"
        print(f"[{symbol}] {badge}: {result.message}")
        for suggestion in result.suggestions:
            print(f"  - {suggestion}")
    return 0 if result.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())

