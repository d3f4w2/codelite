from __future__ import annotations

import argparse
import json
import os
import platform
import shlex
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from codelite import __version__


@dataclass
class RuntimeInfo:
    version: str
    python: str
    platform: str
    cwd: str
    started_at_utc: str


class CodeLiteShell:
    def __init__(self) -> None:
        self.started_at = datetime.now(timezone.utc)
        self._running = True

    def runtime_info(self) -> RuntimeInfo:
        return RuntimeInfo(
            version=__version__,
            python=sys.version.split()[0],
            platform=f"{platform.system()} {platform.release()}",
            cwd=str(Path.cwd()),
            started_at_utc=self.started_at.isoformat(),
        )

    def run(self) -> int:
        print(f"CodeLite v{__version__}")
        print("输入 help 查看命令，输入 exit 退出。")
        while self._running:
            try:
                raw = input("codelite> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break

            if not raw:
                continue

            try:
                self._dispatch(raw)
            except Exception as exc:  # pragma: no cover - defensive
                print(f"[error] {exc}")
        return 0

    def _dispatch(self, raw: str) -> None:
        args = shlex.split(raw)
        cmd = args[0].lower()

        if cmd in {"exit", "quit", "q"}:
            self._running = False
            return

        if cmd in {"help", "?"}:
            self._print_help()
            return

        if cmd == "version":
            print(__version__)
            return

        if cmd == "pwd":
            print(Path.cwd())
            return

        if cmd == "status":
            print(json.dumps(asdict(self.runtime_info()), ensure_ascii=False, indent=2))
            return

        if cmd == "echo":
            text = raw.partition(" ")[2]
            print(text)
            return

        print(f"未知命令: {cmd}")
        print("输入 help 查看可用命令。")

    @staticmethod
    def _print_help() -> None:
        print("可用命令:")
        print("  help           显示帮助")
        print("  version        显示版本")
        print("  status         显示运行时信息")
        print("  pwd            显示当前目录")
        print("  echo <text>    输出文本")
        print("  exit           退出")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="codelite", description="CodeLite CLI")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("version", help="show version")

    status = sub.add_parser("status", help="show runtime info")
    status.add_argument("--json", action="store_true", help="print JSON")

    sub.add_parser("shell", help="start interactive shell")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "version":
        print(__version__)
        return 0

    if args.command == "status":
        info = RuntimeInfo(
            version=__version__,
            python=sys.version.split()[0],
            platform=f"{platform.system()} {platform.release()}",
            cwd=str(Path.cwd()),
            started_at_utc=datetime.now(timezone.utc).isoformat(),
        )
        if args.json:
            print(json.dumps(asdict(info), ensure_ascii=False, indent=2))
        else:
            print(f"version: {info.version}")
            print(f"python: {info.python}")
            print(f"platform: {info.platform}")
            print(f"cwd: {info.cwd}")
        return 0

    # Default behavior: entering shell (supports plain `codelite`)
    return CodeLiteShell().run()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
