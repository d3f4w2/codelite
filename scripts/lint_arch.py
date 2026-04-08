from __future__ import annotations

import argparse
import json
from pathlib import Path


def build_report(workspace_root: Path) -> dict[str, object]:
    required_paths = {
        "AGENTS.md": workspace_root / "AGENTS.md",
        "scripts/validate.py": workspace_root / "scripts" / "validate.py",
        "hooks/pre_tool_use.py": workspace_root / "codelite" / "hooks" / "pre_tool_use.py",
        "hooks/post_tool_use.py": workspace_root / "codelite" / "hooks" / "post_tool_use.py",
        "hooks/on_validation_fail.py": workspace_root / "codelite" / "hooks" / "on_validation_fail.py",
    }
    checks = [
        {"name": name, "path": str(path), "ok": path.exists()}
        for name, path in required_paths.items()
    ]
    return {
        "workspace_root": str(workspace_root),
        "ok": all(item["ok"] for item in checks),
        "checks": checks,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CodeLite architecture lint")
    parser.add_argument("--json", action="store_true", help="print JSON")
    args = parser.parse_args(argv)

    report = build_report(Path.cwd())
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for item in report["checks"]:
            print(f"{item['name']}: {'ok' if item['ok'] else 'missing'}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
