from __future__ import annotations

import base64
import hashlib
import json
from typing import Any


_FIELD = 2**255 - 19
_ORDER = 2**252 + 27742317777372353535851937790883648493
_D = (-121665 * pow(121666, _FIELD - 2, _FIELD)) % _FIELD
_SQRT_M1 = pow(2, (_FIELD - 1) // 4, _FIELD)
_IDENTITY = (0, 1, 1, 0)
_IDENTITY_ENCODING = bytes([1]) + bytes(31)


def canonical_json_bytes(payload: Any) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _recover_x(y: int) -> int:
    numerator = (y * y - 1) % _FIELD
    denominator = (_D * y * y + 1) % _FIELD
    candidate = pow(numerator * pow(denominator, _FIELD - 2, _FIELD) % _FIELD, (_FIELD + 3) // 8, _FIELD)
    if (candidate * candidate - numerator * pow(denominator, _FIELD - 2, _FIELD)) % _FIELD:
        candidate = candidate * _SQRT_M1 % _FIELD
    if (candidate * candidate - numerator * pow(denominator, _FIELD - 2, _FIELD)) % _FIELD:
        raise ValueError("Ed25519-Punkt besitzt keine gültige Quadratwurzel.")
    return candidate


def _decode_point(encoded: bytes) -> tuple[int, int, int, int]:
    if len(encoded) != 32:
        raise ValueError("Ed25519-Punkt muss genau 32 Byte lang sein.")
    raw = int.from_bytes(encoded, "little")
    sign = raw >> 255
    y = raw & ((1 << 255) - 1)
    if y >= _FIELD:
        raise ValueError("Nicht-kanonische Ed25519-Punktkodierung.")
    x = _recover_x(y)
    if (x & 1) != sign:
        x = (-x) % _FIELD
    if (y * y - x * x - 1 - _D * x * x * y * y) % _FIELD:
        raise ValueError("Ed25519-Punkt liegt nicht auf der Kurve.")
    point = (x, y, 1, x * y % _FIELD)
    if _encode_point(point) != encoded:
        raise ValueError("Nicht-kanonische Ed25519-Punktkodierung.")
    return point


def _add(
    left: tuple[int, int, int, int],
    right: tuple[int, int, int, int],
) -> tuple[int, int, int, int]:
    x1, y1, z1, t1 = left
    x2, y2, z2, t2 = right
    a = (y1 - x1) * (y2 - x2) % _FIELD
    b = (y1 + x1) * (y2 + x2) % _FIELD
    c = 2 * _D * t1 * t2 % _FIELD
    d = 2 * z1 * z2 % _FIELD
    e = (b - a) % _FIELD
    f = (d - c) % _FIELD
    g = (d + c) % _FIELD
    h = (b + a) % _FIELD
    return e * f % _FIELD, g * h % _FIELD, f * g % _FIELD, e * h % _FIELD


def _multiply(point: tuple[int, int, int, int], scalar: int) -> tuple[int, int, int, int]:
    result = _IDENTITY
    addend = point
    value = scalar
    while value:
        if value & 1:
            result = _add(result, addend)
        addend = _add(addend, addend)
        value >>= 1
    return result


def _encode_point(point: tuple[int, int, int, int]) -> bytes:
    x, y, z, _ = point
    inverse = pow(z, _FIELD - 2, _FIELD)
    affine_x = x * inverse % _FIELD
    affine_y = y * inverse % _FIELD
    return (affine_y | ((affine_x & 1) << 255)).to_bytes(32, "little")


_BASE_Y = 4 * pow(5, _FIELD - 2, _FIELD) % _FIELD
_BASE_X = _recover_x(_BASE_Y)
if _BASE_X & 1:
    _BASE_X = (-_BASE_X) % _FIELD
_BASE = (_BASE_X, _BASE_Y, 1, _BASE_X * _BASE_Y % _FIELD)


def verify_ed25519(public_key: bytes, message: bytes, signature: bytes) -> bool:
    """Strict, dependency-free RFC 8032 Ed25519 verification.

    The verifier deliberately exposes no signing primitive. Update signing keys
    never belong in the runtime package.
    """
    if len(public_key) != 32 or len(signature) != 64:
        return False
    try:
        encoded_r = signature[:32]
        scalar_s = int.from_bytes(signature[32:], "little")
        if scalar_s >= _ORDER:
            return False
        point_a = _decode_point(public_key)
        point_r = _decode_point(encoded_r)
        # Reject identity and other small-order points. Merely checking [L]P=P0
        # is insufficient because the identity itself also satisfies that test.
        if public_key == _IDENTITY_ENCODING or encoded_r == _IDENTITY_ENCODING:
            return False
        if _encode_point(_multiply(point_a, 8)) == _IDENTITY_ENCODING:
            return False
        if _encode_point(_multiply(point_r, 8)) == _IDENTITY_ENCODING:
            return False
        if _encode_point(_multiply(point_a, _ORDER)) != _encode_point(_IDENTITY):
            return False
        if _encode_point(_multiply(point_r, _ORDER)) != _encode_point(_IDENTITY):
            return False
        challenge = int.from_bytes(
            hashlib.sha512(encoded_r + public_key + message).digest(), "little"
        ) % _ORDER
        left = _multiply(_BASE, scalar_s)
        right = _add(point_r, _multiply(point_a, challenge))
        return _encode_point(left) == _encode_point(right)
    except (TypeError, ValueError, OverflowError):
        return False


def decode_base64(value: str, *, expected_bytes: int, field: str) -> bytes:
    if not isinstance(value, str):
        raise ValueError(f"{field} muss Base64-Text sein.")
    try:
        decoded = base64.b64decode(value, validate=True)
    except Exception as exc:
        raise ValueError(f"{field} ist kein gültiges Base64.") from exc
    if len(decoded) != expected_bytes:
        raise ValueError(f"{field} muss dekodiert genau {expected_bytes} Byte lang sein.")
    return decoded


def verify_signed_document(document: dict[str, Any], trusted_keys: dict[str, str]) -> dict[str, Any]:
    if not isinstance(document, dict):
        raise ValueError("Signiertes Dokument muss ein JSON-Objekt sein.")
    signatures = document.get("signatures")
    if not isinstance(signatures, list) or not signatures:
        raise ValueError("Signiertes Dokument enthält keine Signatur.")
    payload = {key: value for key, value in document.items() if key != "signatures"}
    message = canonical_json_bytes(payload)
    verified_key_ids: list[str] = []
    for entry in signatures:
        if not isinstance(entry, dict) or entry.get("algorithm") != "Ed25519":
            continue
        key_id = entry.get("key_id")
        if not isinstance(key_id, str) or key_id not in trusted_keys:
            continue
        public_key = decode_base64(
            trusted_keys[key_id], expected_bytes=32, field=f"trusted_keys.{key_id}"
        )
        signature = decode_base64(
            entry.get("signature"), expected_bytes=64, field="signature"
        )
        if verify_ed25519(public_key, message, signature):
            verified_key_ids.append(key_id)
    if not verified_key_ids:
        raise RuntimeError("Keine Signatur stammt von einem freigegebenen Ed25519-Schlüssel.")
    return {"ok": True, "verified_key_ids": sorted(set(verified_key_ids)), "payload": payload}
