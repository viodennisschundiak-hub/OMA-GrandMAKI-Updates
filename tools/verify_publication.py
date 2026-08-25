#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path

from oma_basement.hashing import calculate_bridge_build_sha256, calculate_launcher_tree_sha256
from oma_basement.update_crypto import verify_signed_document


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--channel", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--trusted-keys", type=Path, required=True)
    parser.add_argument("--release-config", type=Path, required=True)
    parser.add_argument("--bridge-root", type=Path, required=True)
    parser.add_argument("--launcher-root", type=Path, required=True)
    args = parser.parse_args()

    trusted = json.loads(args.trusted_keys.read_text(encoding="utf-8-sig"))
    config = json.loads(args.release_config.read_text(encoding="utf-8-sig"))
    channel_document = json.loads(args.channel.read_text(encoding="utf-8-sig"))
    channel = verify_signed_document(channel_document, trusted)["payload"]
    if channel.get("product") != "oma-update-channel" or channel.get("sequence") != config["sequence"]:
        raise RuntimeError("Kanalprodukt oder Sequenz stimmt nicht.")
    channel_release = channel.get("release", {})
    if channel_release.get("bundle_sha256") != sha256(args.bundle):
        raise RuntimeError("Kanal-SHA-256 stimmt nicht mit dem Bundle überein.")
    if channel_release.get("bundle_size_bytes") != args.bundle.stat().st_size:
        raise RuntimeError("Kanalgröße stimmt nicht mit dem Bundle überein.")

    with zipfile.ZipFile(args.bundle) as archive:
        names = archive.namelist()
        if names.count("OMA_UPDATE_MANIFEST.json") != 1:
            raise RuntimeError("Bundle-Manifest fehlt oder ist mehrfach vorhanden.")
        if not any(name.startswith("release/") and not name.endswith("/") for name in names):
            raise RuntimeError("Bridge-Quellbaum fehlt im Bundle.")
        if not any(name.startswith("launcher/") and not name.endswith("/") for name in names):
            raise RuntimeError("Launcher-Quellbaum fehlt im Bundle.")
        manifest_document = json.loads(archive.read("OMA_UPDATE_MANIFEST.json").decode("utf-8-sig"))
    manifest = verify_signed_document(manifest_document, trusted)["payload"]
    if manifest.get("schema_version") != 2 or manifest.get("product") != "oma-grandmaki-suite":
        raise RuntimeError("Bundle ist kein koordiniertes Suite-Update.")
    if manifest.get("sequence") != config["sequence"]:
        raise RuntimeError("Bundle-Sequenz stimmt nicht.")
    bridge_hash = calculate_bridge_build_sha256(args.bridge_root)
    launcher_hash = calculate_launcher_tree_sha256(args.launcher_root)
    if bridge_hash != config["expected_bridge_build_sha256"]:
        raise RuntimeError("Bridge-Quellstand weicht von der Freigabe ab.")
    if launcher_hash != config["expected_launcher_tree_sha256"]:
        raise RuntimeError("Launcher-Quellstand weicht von der Freigabe ab.")
    if manifest.get("release", {}).get("build_sha256") != bridge_hash:
        raise RuntimeError("Signierter Bridge-Hash stimmt nicht.")
    if manifest.get("launcher", {}).get("tree_sha256") != launcher_hash:
        raise RuntimeError("Signierter Launcher-Hash stimmt nicht.")
    print(json.dumps({
        "ok": True,
        "verified_key_ids": verify_signed_document(manifest_document, trusted)["verified_key_ids"],
        "bundle_sha256": sha256(args.bundle),
        "bridge_build_sha256": bridge_hash,
        "launcher_tree_sha256": launcher_hash,
        "grandma_commands_executed": 0,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
