from __future__ import annotations

import hashlib
from pathlib import Path


LAUNCHER_RUNTIME_MARKERS = frozenset({"installed_launcher.json"})


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def calculate_bridge_build_sha256(repo_root: Path) -> str:
    """Use the exact deterministic algorithm from the active OMA Bridge."""
    root = repo_root.resolve()
    candidates = list((root / "src").rglob("*.py"))
    for relative in ("pyproject.toml", "uv.lock", "run_mcp.py"):
        path = root / relative
        if path.is_file():
            candidates.append(path)

    if not candidates:
        raise ValueError(f"Keine Bridge-Quelldateien unter {root} gefunden.")

    digest = hashlib.sha256()
    for path in sorted(set(candidates), key=lambda item: item.as_posix()):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        content = path.read_bytes()
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def calculate_launcher_tree_sha256(launcher_root: Path) -> str:
    """Hash the complete immutable launcher tree, excluding local install metadata."""
    root = launcher_root.resolve()
    if not root.is_dir():
        raise ValueError(f"Launcher-Quellbaum fehlt: {root}")

    candidates: list[Path] = []
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"Symlink im Launcher-Quellbaum ist nicht erlaubt: {path}")
        if path.is_file() and path.name.casefold() not in LAUNCHER_RUNTIME_MARKERS:
            candidates.append(path)
    if not candidates:
        raise ValueError(f"Keine Launcher-Dateien unter {root} gefunden.")

    digest = hashlib.sha256()
    for path in sorted(candidates, key=lambda item: item.relative_to(root).as_posix().casefold()):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        content = path.read_bytes()
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()
