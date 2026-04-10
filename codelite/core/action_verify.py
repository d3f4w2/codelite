from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class VerifyActionResult:
    ok: bool
    action_type: str
    message: str
    details: dict[str, Any]
    suggestions: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_LAYER_RULES: list[tuple[int, tuple[str, ...]]] = [
    (0, ("codelite/storage", "codelite/config")),
    (1, ("codelite/memory",)),
    (2, ("codelite/core",)),
    (3, ("codelite/hooks",)),
    (4, ("codelite/tui", "codelite/cli.py", "scripts")),
    (5, ("tests", "docs", "harness")),
]


def _normalize_ref(raw: str) -> str:
    text = raw.strip().strip("\"'").replace("\\", "/")
    if text.startswith("./"):
        text = text[2:]
    if text.startswith("/"):
        text = text[1:]
    if "." in text and "/" not in text and not text.endswith(".py"):
        text = text.replace(".", "/")
    return text


def _layer_of(ref: str) -> int | None:
    normalized = _normalize_ref(ref)
    for layer, prefixes in _LAYER_RULES:
        for prefix in prefixes:
            if normalized == prefix or normalized.startswith(prefix + "/"):
                return layer
    return None


def verify_create_file(workspace_root: Path, path: str) -> VerifyActionResult:
    normalized = _normalize_ref(path)
    target = (workspace_root / normalized).resolve()
    try:
        target.relative_to(workspace_root.resolve())
    except ValueError:
        return VerifyActionResult(
            ok=False,
            action_type="create_file",
            message=f"target path escapes workspace: {normalized}",
            details={"path": normalized},
            suggestions=["Choose a path under the workspace root."],
        )

    basename = target.name
    if " " in basename:
        return VerifyActionResult(
            ok=False,
            action_type="create_file",
            message=f"filename contains spaces: {basename}",
            details={"path": normalized},
            suggestions=["Use snake_case or kebab-case names without spaces."],
        )

    layer = _layer_of(normalized)
    suggestions: list[str] = []
    if layer is None:
        suggestions.append("Path is outside known layered directories; ensure this location is intentional.")
    else:
        suggestions.append(f"Assigned to layer {layer}. Keep imports flowing from higher layers to this layer.")

    return VerifyActionResult(
        ok=True,
        action_type="create_file",
        message=f"create file allowed: {normalized}",
        details={"path": normalized, "layer": layer},
        suggestions=suggestions,
    )


def verify_import(workspace_root: Path, source: str, target: str) -> VerifyActionResult:
    del workspace_root
    source_ref = _normalize_ref(source)
    target_ref = _normalize_ref(target)
    source_layer = _layer_of(source_ref)
    target_layer = _layer_of(target_ref)

    if source_layer is None or target_layer is None:
        return VerifyActionResult(
            ok=False,
            action_type="import",
            message=f"unable to resolve layers for import: {source_ref} -> {target_ref}",
            details={
                "source": source_ref,
                "target": target_ref,
                "source_layer": source_layer,
                "target_layer": target_layer,
            },
            suggestions=[
                "Use explicit repo paths (for example codelite/core/... from codelite/tui/...).",
                "If this is a new package family, add it to the layer map.",
            ],
        )

    if source_layer < target_layer:
        return VerifyActionResult(
            ok=False,
            action_type="import",
            message=f"layer violation: L{source_layer} cannot import higher layer L{target_layer}",
            details={
                "source": source_ref,
                "target": target_ref,
                "source_layer": source_layer,
                "target_layer": target_layer,
            },
            suggestions=[
                "Move the dependency inversion to an interface in a lower layer.",
                "Pass required data through parameters instead of direct import.",
            ],
        )

    if source_layer == 0 and target_layer != 0:
        return VerifyActionResult(
            ok=False,
            action_type="import",
            message="layer violation: layer 0 must not depend on higher internal layers",
            details={
                "source": source_ref,
                "target": target_ref,
                "source_layer": source_layer,
                "target_layer": target_layer,
            },
            suggestions=[
                "Keep layer 0 pure types/storage contracts only.",
                "Move behavior to core/higher layers.",
            ],
        )

    return VerifyActionResult(
        ok=True,
        action_type="import",
        message=f"import allowed: {source_ref} -> {target_ref}",
        details={
            "source": source_ref,
            "target": target_ref,
            "source_layer": source_layer,
            "target_layer": target_layer,
        },
        suggestions=["Dependency direction is valid under current layer rules."],
    )


def verify_action_text(workspace_root: Path, action: str) -> VerifyActionResult:
    text = action.strip()
    import_match = re.match(r"^\s*import\s+(.+?)\s+from\s+(.+?)\s*$", text, flags=re.IGNORECASE)
    if import_match:
        return verify_import(workspace_root, source=import_match.group(2), target=import_match.group(1))

    create_match = re.match(r"^\s*create(?:\s+file)?\s+(.+?)\s*$", text, flags=re.IGNORECASE)
    if create_match:
        return verify_create_file(workspace_root, create_match.group(1))

    return VerifyActionResult(
        ok=False,
        action_type="unknown",
        message="unsupported action format",
        details={"action": text},
        suggestions=[
            'Use "create file <path>" for file placement checks.',
            'Use "import <target> from <source>" for dependency direction checks.',
        ],
    )
