#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any

from oma_basement.hashing import (
    LAUNCHER_RUNTIME_MARKERS,
    calculate_bridge_build_sha256,
    calculate_launcher_tree_sha256,
)


def _single_match(pattern: str, text: str, label: str) -> str:
    matches = re.findall(pattern, text, flags=re.MULTILINE)
    if len(matches) != 1:
        raise RuntimeError(f"{label} fehlt oder ist nicht eindeutig.")
    return matches[0]


def _read_json(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(document, dict):
        raise RuntimeError(f"JSON-Dokument ist kein Objekt: {path}")
    return document


def _verify_launcher_sha256_manifest(launcher_root: Path) -> None:
    manifest_path = launcher_root / "SHA256SUMS.txt"
    if not manifest_path.is_file():
        raise RuntimeError("Launcher-SHA256SUMS.txt fehlt.")

    entries: dict[str, str] = {}
    for line_number, line in enumerate(
        manifest_path.read_text(encoding="utf-8-sig").splitlines(),
        start=1,
    ):
        if not line:
            continue
        match = re.fullmatch(r"([0-9a-f]{64})  ([^\\\r\n]+)", line)
        if match is None:
            raise RuntimeError(f"Ungültige Launcher-Prüfsummenzeile {line_number}.")
        expected, relative_text = match.groups()
        relative = PurePosixPath(relative_text)
        if relative.is_absolute() or ".." in relative.parts or relative_text in entries:
            raise RuntimeError(f"Unsicherer oder doppelter Launcher-Pfad: {relative_text}")
        entries[relative_text] = expected

    actual_files: set[str] = set()
    root = launcher_root.resolve()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise RuntimeError(f"Symlink im Launcher-Quellbaum: {path}")
        if not path.is_file():
            continue
        if path.name.casefold() in LAUNCHER_RUNTIME_MARKERS:
            continue
        relative = path.relative_to(root).as_posix()
        if relative == "SHA256SUMS.txt":
            continue
        actual_files.add(relative)

    if set(entries) != actual_files:
        missing = sorted(actual_files - set(entries))
        unexpected = sorted(set(entries) - actual_files)
        raise RuntimeError(
            f"Launcher-Prüfsummenabdeckung stimmt nicht: missing={missing}, unexpected={unexpected}"
        )

    for relative, expected in entries.items():
        path = root / relative
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            raise RuntimeError(f"Launcher-Dateihash stimmt nicht: {relative}")


def verify_release_binding(
    *,
    config: dict[str, Any],
    bridge_root: Path,
    launcher_root: Path,
) -> dict[str, Any]:
    bridge_root = bridge_root.resolve()
    launcher_root = launcher_root.resolve()
    actual_bridge_hash = calculate_bridge_build_sha256(bridge_root)
    actual_launcher_hash = calculate_launcher_tree_sha256(launcher_root)

    if actual_bridge_hash != config["expected_bridge_build_sha256"]:
        raise RuntimeError("Bridge-Quellstand weicht von der Freigabe ab.")
    if actual_launcher_hash != config["expected_launcher_tree_sha256"]:
        raise RuntimeError("Launcher-Quellstand weicht von der Freigabe ab.")

    marker = (bridge_root / "OMA_EXPECTED_BUILD_SHA256.txt").read_text(
        encoding="utf-8-sig"
    ).strip()
    if marker != actual_bridge_hash:
        raise RuntimeError("Bridge-Buildmarker stimmt nicht mit dem tatsächlichen Build überein.")

    contract_path = bridge_root / "OMA_RUNTIME_CONTRACT.json"
    contract_bytes = contract_path.read_bytes()
    contract = json.loads(contract_bytes.decode("utf-8-sig"))
    expected_release = {
        "suite_version": config["bridge_version"],
        "bridge_version": config["runtime_bridge_version"],
        "launcher_version": config["launcher_version"],
        "basement_version": config["minimum_basement_version"],
        "update_sequence": config["sequence"],
    }
    if (
        contract.get("contract_id") != "oma-runtime-contract/v1"
        or contract.get("schema_version") != 1
        or contract.get("release") != expected_release
    ):
        raise RuntimeError("Runtime-Vertrag passt nicht exakt zur Publisher-Freigabe.")

    runtime_contract_source = (bridge_root / "src" / "runtime_contract.py").read_text(
        encoding="utf-8-sig"
    )
    bound_contract_hash = _single_match(
        r'^RUNTIME_CONTRACT_FILE_SHA256\s*=\s*"([0-9a-f]{64})"\s*$',
        runtime_contract_source,
        "Runtime-Vertragshash",
    )
    if bound_contract_hash != hashlib.sha256(contract_bytes).hexdigest():
        raise RuntimeError("Runtime-Vertrag und Bridge-Quellbindung stimmen nicht überein.")

    launcher_script = (launcher_root / "OMA_GrandMAKI_BOT.ps1").read_text(
        encoding="utf-8-sig"
    )
    launcher_version = _single_match(
        r"^\$script:AppVersion\s*=\s*'([^']+)'\s*$",
        launcher_script,
        "Launcher-AppVersion",
    )
    launcher_bridge_hash = _single_match(
        r"^\$script:PackagedBridgeBuildSha256\s*=\s*'([0-9a-f]{64})'\s*$",
        launcher_script,
        "Launcher-Bridgebindung",
    )
    if launcher_version != config["launcher_version"]:
        raise RuntimeError("Launcher-AppVersion passt nicht zur Freigabe.")
    if launcher_bridge_hash != actual_bridge_hash:
        raise RuntimeError("Launcher ist nicht an den freigegebenen Bridge-Build gebunden.")

    start_cmd = (launcher_root / "OMA_GrandMAKI_BOT_Start.cmd").read_text(
        encoding="utf-8-sig"
    )
    bootstrap = (launcher_root / "OMA_Launcher_Bootstrap.ps1").read_text(
        encoding="utf-8-sig"
    )
    expected_version = config["launcher_version"]
    if f"launcher\\{expected_version}" not in start_cmd:
        raise RuntimeError("Start-CMD verweist nicht auf die freigegebene Launcher-Version.")
    if f"Bootstrap {expected_version} gestartet" not in bootstrap:
        raise RuntimeError("Bootstrap-Version passt nicht zur Freigabe.")

    _verify_launcher_sha256_manifest(launcher_root)
    return {
        "ok": True,
        "suite_version": config["bridge_version"],
        "runtime_bridge_version": config["runtime_bridge_version"],
        "launcher_version": config["launcher_version"],
        "sequence": config["sequence"],
        "bridge_build_sha256": actual_bridge_hash,
        "launcher_tree_sha256": actual_launcher_hash,
        "runtime_contract_sha256": hashlib.sha256(contract_bytes).hexdigest(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-config", type=Path, required=True)
    parser.add_argument("--bridge-root", type=Path, required=True)
    parser.add_argument("--launcher-root", type=Path, required=True)
    args = parser.parse_args()
    result = verify_release_binding(
        config=_read_json(args.release_config),
        bridge_root=args.bridge_root,
        launcher_root=args.launcher_root,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
