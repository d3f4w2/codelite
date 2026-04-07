from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path


README_TEMPLATE = """# {title}

日期：{bundle_date}

本阶段已完成机制：

- 

本阶段未纳入验收的机制：

- 

人工验收时重点关注：

- 

样本产物说明：

- `artifacts/command-output/`
- `artifacts/runtime/`
"""


COMMANDS_TEMPLATE = """# 手工验收命令

## 1. 基础入口

```powershell
# 在这里填写本阶段最基础的入口命令
```

预期结果：

- 

解释：

- 
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Scaffold a phase acceptance bundle.")
    parser.add_argument("slug", help="phase slug, for example: phase-1-worktree")
    parser.add_argument("--title", required=True, help="bundle title")
    parser.add_argument(
        "--date",
        default=date.today().isoformat(),
        help="bundle date in YYYY-MM-DD format",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    bundle_dir = root / "docs" / "acceptance" / f"{args.date}-{args.slug}"
    command_output_dir = bundle_dir / "artifacts" / "command-output"
    runtime_dir = bundle_dir / "artifacts" / "runtime"

    command_output_dir.mkdir(parents=True, exist_ok=True)
    runtime_dir.mkdir(parents=True, exist_ok=True)

    readme_path = bundle_dir / "README.md"
    commands_path = bundle_dir / "manual-commands.md"

    if not readme_path.exists():
        readme_path.write_text(
            README_TEMPLATE.format(title=args.title, bundle_date=args.date),
            encoding="utf-8",
        )

    if not commands_path.exists():
        commands_path.write_text(COMMANDS_TEMPLATE, encoding="utf-8")

    print(bundle_dir)
    print(readme_path)
    print(commands_path)
    print(command_output_dir)
    print(runtime_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
