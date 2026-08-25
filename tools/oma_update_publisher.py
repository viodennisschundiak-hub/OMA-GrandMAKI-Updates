#!/usr/bin/env python3
"""Offline-only publisher for signed OMA suite updates.

The private Ed25519 seed is read only by this tool. It must never be copied into
the Launcher, Basement, update bundle, diagnostics, or the target laptop.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import secrets
import uuid
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from oma_basement.hashing import (
    LAUNCHER_RUNTIME_MARKERS,
    calculate_bridge_build_sha256,
    calculate_launcher_tree_sha256,
    file_sha256,
)
from oma_basement.update_crypto import _BASE, _ORDER, _encode_point, _multiply, canonical_json_bytes


BLOCKED_NAMES = frozenset({".env", "secrets.dpapi.json"})
BLOCKED_DIRECTORIES = frozenset({
    ".git", ".venv", ".pytest_cache", ".mypy_cache", "__pycache__", "_oma_backups"
})
BLOCKED_SUFFIXES = (".db", ".db-wal", ".db-shm", ".db-journal", ".sqlite", ".sqlite3", ".log", ".tmp")


def _write_json(path: Path, payload: Any, *, private: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(path, flags, 0o600 if private else 0o644)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")


def _load_seed(path: Path) -> bytes:
    document = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(document, dict) or document.get("algorithm") != "Ed25519":
        raise ValueError("Private Schlüsseldatei hat ein ungültiges Format.")
    encoded = document.get("seed_base64")
    if not isinstance(encoded, str):
        raise ValueError("Private Schlüsseldatei enthält keinen Seed.")
    try:
        seed = base64.b64decode(encoded, validate=True)
    except Exception as exc:
        raise ValueError("Privater Seed ist kein gültiges Base64.") from exc
    if len(seed) != 32:
        raise ValueError("Ed25519-Seed muss exakt 32 Byte lang sein.")
    return seed


def _public_key(seed: bytes) -> bytes:
    expanded = hashlib.sha512(seed).digest()
    scalar = int.from_bytes(expanded[:32], "little")
    scalar &= (1 << 254) - 8
    scalar |= 1 << 254
    return _encode_point(_multiply(_BASE, scalar))


def _sign(seed: bytes, message: bytes) -> bytes:
    expanded = hashlib.sha512(seed).digest()
    scalar = int.from_bytes(expanded[:32], "little")
    scalar &= (1 << 254) - 8
    scalar |= 1 << 254
    public_key = _encode_point(_multiply(_BASE, scalar))
    nonce = int.from_bytes(hashlib.sha512(expanded[32:] + message).digest(), "little") % _ORDER
    encoded_r = _encode_point(_multiply(_BASE, nonce))
    challenge = int.from_bytes(
        hashlib.sha512(encoded_r + public_key + message).digest(), "little"
    ) % _ORDER
    scalar_s = (nonce + challenge * scalar) % _ORDER
    return encoded_r + scalar_s.to_bytes(32, "little")


def _signed_document(payload: dict[str, Any], seed: bytes, key_id: str) -> dict[str, Any]:
    signature = _sign(seed, canonical_json_bytes(payload))
    document = dict(payload)
    document["signatures"] = [{
        "algorithm": "Ed25519",
        "key_id": key_id,
        "signature": base64.b64encode(signature).decode("ascii"),
    }]
    return document


def _safe_files(root: Path, *, launcher: bool = False) -> Iterable[Path]:
    root = root.resolve()
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix().casefold()):
        relative = path.relative_to(root)
        if any(part in BLOCKED_DIRECTORIES for part in relative.parts):
            continue
        if path.is_symlink():
            raise RuntimeError(f"Symlink ist nicht erlaubt: {path}")
        if not path.is_file():
            continue
        lowered = path.name.casefold()
        if lowered in BLOCKED_NAMES or lowered.endswith(BLOCKED_SUFFIXES):
            raise RuntimeError(f"Geheimnis-/Laufzeitdatei im Publisher-Quellbaum blockiert: {path}")
        if launcher and lowered in LAUNCHER_RUNTIME_MARKERS:
            continue
        yield path


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat()


def command_generate_key(args: argparse.Namespace) -> None:
    seed = secrets.token_bytes(32)
    _write_json(
        args.private_out,
        {
            "schema_version": 1,
            "algorithm": "Ed25519",
            "key_id": args.key_id,
            "seed_base64": base64.b64encode(seed).decode("ascii"),
            "created_at": _iso(datetime.now(UTC)),
            "handling": "OFFLINE_PRIVATE_KEY_DO_NOT_COPY_TO_OMA_CLIENT",
        },
        private=True,
    )
    _write_json(
        args.public_out,
        {args.key_id: base64.b64encode(_public_key(seed)).decode("ascii")},
    )


def command_build_bundle(args: argparse.Namespace) -> None:
    seed = _load_seed(args.seed_file)
    now = datetime.now(UTC)
    bridge_root = args.bridge_root.resolve()
    launcher_root = args.launcher_root.resolve()
    bridge_hash = calculate_bridge_build_sha256(bridge_root)
    launcher_hash = calculate_launcher_tree_sha256(launcher_root)
    payload = {
        "schema_version": 2,
        "product": "oma-grandmaki-suite",
        "channel": args.channel,
        "sequence": args.sequence,
        "issued_at": _iso(now),
        "expires_at": _iso(now + timedelta(days=args.expires_days)),
        "release": {
            "release_id": str(uuid.uuid4()),
            "version": args.release_version,
            "build_sha256": bridge_hash,
            "minimum_basement_version": args.minimum_basement_version,
            "grandma_commands_executed": 0,
        },
        "launcher": {
            "version": args.launcher_version,
            "tree_sha256": launcher_hash,
            "minimum_launcher_version": args.minimum_launcher_version,
            "grandma_commands_executed": 0,
        },
    }
    document = _signed_document(payload, seed, args.key_id)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        raise FileExistsError(f"Ausgabedatei existiert bereits: {args.output}")
    with zipfile.ZipFile(args.output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        archive.writestr("OMA_UPDATE_MANIFEST.json", json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        for path in _safe_files(bridge_root):
            archive.write(path, "release/" + path.relative_to(bridge_root).as_posix())
        for path in _safe_files(launcher_root, launcher=True):
            archive.write(path, "launcher/" + path.relative_to(launcher_root).as_posix())
    print(json.dumps({
        "bundle": str(args.output),
        "bundle_sha256": file_sha256(args.output),
        "bundle_size_bytes": args.output.stat().st_size,
        "bridge_build_sha256": bridge_hash,
        "launcher_tree_sha256": launcher_hash,
        "private_key_embedded": False,
    }, indent=2, sort_keys=True))


def command_build_channel(args: argparse.Namespace) -> None:
    seed = _load_seed(args.seed_file)
    bundle = args.bundle.resolve()
    if not bundle.is_file():
        raise FileNotFoundError(f"Update-Bundle fehlt: {bundle}")
    if not args.bundle_url.startswith("https://"):
        raise ValueError("Produktiver Bundle-URL muss HTTPS verwenden.")
    now = datetime.now(UTC)
    payload = {
        "schema_version": 1,
        "product": "oma-update-channel",
        "channel": args.channel,
        "sequence": args.sequence,
        "issued_at": _iso(now),
        "expires_at": _iso(now + timedelta(days=args.expires_days)),
        "release": {
            "version": args.version,
            "bundle_url": args.bundle_url,
            "bundle_sha256": file_sha256(bundle),
            "bundle_size_bytes": bundle.stat().st_size,
        },
    }
    _write_json(args.output, _signed_document(payload, seed, args.key_id))


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Offline OMA Ed25519 update publisher")
    sub = result.add_subparsers(dest="command", required=True)
    key = sub.add_parser("generate-key")
    key.add_argument("--key-id", required=True)
    key.add_argument("--private-out", required=True, type=Path)
    key.add_argument("--public-out", required=True, type=Path)
    key.set_defaults(handler=command_generate_key)

    bundle = sub.add_parser("build-bundle")
    bundle.add_argument("--seed-file", required=True, type=Path)
    bundle.add_argument("--key-id", required=True)
    bundle.add_argument("--bridge-root", required=True, type=Path)
    bundle.add_argument("--launcher-root", required=True, type=Path)
    bundle.add_argument("--release-version", required=True)
    bundle.add_argument("--launcher-version", required=True)
    bundle.add_argument("--minimum-basement-version", default="0.5.0")
    bundle.add_argument("--minimum-launcher-version", default="1.1.5")
    bundle.add_argument("--channel", choices=("stable", "beta"), default="stable")
    bundle.add_argument("--sequence", required=True, type=int)
    bundle.add_argument("--expires-days", default=30, type=int)
    bundle.add_argument("--output", required=True, type=Path)
    bundle.set_defaults(handler=command_build_bundle)

    channel = sub.add_parser("build-channel")
    channel.add_argument("--seed-file", required=True, type=Path)
    channel.add_argument("--key-id", required=True)
    channel.add_argument("--bundle", required=True, type=Path)
    channel.add_argument("--bundle-url", required=True)
    channel.add_argument("--version", required=True)
    channel.add_argument("--channel", choices=("stable", "beta"), default="stable")
    channel.add_argument("--sequence", required=True, type=int)
    channel.add_argument("--expires-days", default=7, type=int)
    channel.add_argument("--output", required=True, type=Path)
    channel.set_defaults(handler=command_build_channel)
    return result


def main() -> int:
    args = parser().parse_args()
    if getattr(args, "sequence", 1) < 1:
        raise ValueError("Sequenz muss positiv sein.")
    if getattr(args, "expires_days", 1) < 1:
        raise ValueError("Gültigkeitsdauer muss positiv sein.")
    args.handler(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
