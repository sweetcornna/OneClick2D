"""Strict JSON profile and RFC 8785 canonicalization.

Implements ``docs/PACKAGE_CONFORMANCE.md`` §2: UTF-8 only, duplicate keys
rejected, ``NaN``/``Infinity`` rejected, bounded depth and member counts, and
I-JSON interoperable finite numbers. Digests are always taken over canonical
bytes so that a re-serialized document hashes identically.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any, Final

from .errors import StrictJsonError

MAX_JSON_BYTES: Final[int] = 64 * 1024 * 1024
MAX_JSON_DEPTH: Final[int] = 64
MAX_JSON_NODES: Final[int] = 2_000_000
MAX_JSON_MEMBERS: Final[int] = 100_000
MAX_JSON_STRING_BYTES: Final[int] = 1 * 1024 * 1024
MAX_SAFE_INTEGER: Final[int] = 2**53 - 1

_SHA256_RE: Final[re.Pattern[str]] = re.compile(r"\A[0-9a-f]{64}\Z")
_ESCAPES: Final[dict[int, str]] = {
    0x08: "\\b",
    0x09: "\\t",
    0x0A: "\\n",
    0x0C: "\\f",
    0x0D: "\\r",
    0x22: '\\"',
    0x5C: "\\\\",
}


def _reject_constant(name: str) -> Any:
    raise StrictJsonError(f"non-finite JSON constant is rejected: {name}")


def _pairs_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise StrictJsonError("duplicate JSON object key is rejected")
        result[key] = value
    if len(result) > MAX_JSON_MEMBERS:
        raise StrictJsonError("JSON object member limit exceeded")
    return result


def _check_value(value: Any, depth: int, nodes: list[int]) -> None:
    nodes[0] += 1
    if nodes[0] > MAX_JSON_NODES:
        raise StrictJsonError("JSON node limit exceeded")
    if depth > MAX_JSON_DEPTH:
        raise StrictJsonError("JSON depth limit exceeded")
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, int):
        if abs(value) > MAX_SAFE_INTEGER:
            raise StrictJsonError("integer exceeds the interoperable safe range")
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise StrictJsonError("non-finite JSON number is rejected")
        return
    if isinstance(value, str):
        if len(value.encode("utf-8")) > MAX_JSON_STRING_BYTES:
            raise StrictJsonError("JSON string byte limit exceeded")
        return
    if isinstance(value, list):
        if len(value) > MAX_JSON_MEMBERS:
            raise StrictJsonError("JSON array member limit exceeded")
        for item in value:
            _check_value(item, depth + 1, nodes)
        return
    if isinstance(value, dict):
        if len(value) > MAX_JSON_MEMBERS:
            raise StrictJsonError("JSON object member limit exceeded")
        for key, item in value.items():
            if not isinstance(key, str):
                raise StrictJsonError("JSON object keys must be strings")
            _check_value(item, depth + 1, nodes)
        return
    raise StrictJsonError("value is not representable in strict JSON")


def check_strict_value(value: Any) -> None:
    """Validate an in-memory value against the strict JSON profile."""
    _check_value(value, 0, [0])


def loads_strict(data: bytes) -> Any:
    """Parse ``data`` under the strict interoperable JSON profile."""
    if len(data) > MAX_JSON_BYTES:
        raise StrictJsonError("JSON byte limit exceeded")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise StrictJsonError("JSON must be valid UTF-8") from exc
    if text.startswith("﻿"):
        raise StrictJsonError("JSON must not start with a byte order mark")
    try:
        value = json.loads(
            text,
            object_pairs_hook=_pairs_without_duplicates,
            parse_constant=_reject_constant,
        )
    except json.JSONDecodeError as exc:
        raise StrictJsonError("invalid JSON") from exc
    check_strict_value(value)
    return value


def _canonical_number(value: int | float) -> str:
    if isinstance(value, bool):  # pragma: no cover - guarded by callers
        raise StrictJsonError("boolean is not a JSON number")
    if isinstance(value, int):
        return str(value)
    if not math.isfinite(value):
        raise StrictJsonError("non-finite JSON number is rejected")
    if value == 0:
        return "0"
    if value == int(value) and abs(value) < 1e21:
        return str(int(value))
    text = repr(value)
    if "e" in text or "E" in text:
        mantissa, _, exponent = text.partition("e")
        if "." in mantissa:
            mantissa = mantissa.rstrip("0").rstrip(".")
        sign = "-" if exponent.startswith("-") else "+"
        digits = exponent.lstrip("+-").lstrip("0") or "0"
        return f"{mantissa}e{sign}{digits}"
    return text


def _canonical_string(value: str) -> str:
    out = ['"']
    for character in value:
        code = ord(character)
        escape = _ESCAPES.get(code)
        if escape is not None:
            out.append(escape)
        elif code < 0x20:
            out.append(f"\\u{code:04x}")
        else:
            out.append(character)
    out.append('"')
    return "".join(out)


def _serialize(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return _canonical_number(value)
    if isinstance(value, str):
        return _canonical_string(value)
    if isinstance(value, list):
        return "[" + ",".join(_serialize(item) for item in value) + "]"
    if isinstance(value, dict):
        members = sorted(value.items(), key=lambda item: item[0].encode("utf-16-be"))
        return "{" + ",".join(f"{_canonical_string(key)}:{_serialize(item)}" for key, item in members) + "}"
    raise StrictJsonError("value is not representable in strict JSON")


def canonical_bytes(value: Any) -> bytes:
    """Serialize ``value`` to RFC 8785 canonical JSON bytes."""
    check_strict_value(value)
    return _serialize(value).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    """Return the lowercase SHA-256 hex digest of canonical JSON bytes."""
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def sha256_hex(data: bytes) -> str:
    """Return the lowercase SHA-256 hex digest of ``data``."""
    return hashlib.sha256(data).hexdigest()


def is_sha256_hex(value: object) -> bool:
    """Return whether ``value`` is a lowercase 64-character SHA-256 digest."""
    return isinstance(value, str) and _SHA256_RE.match(value) is not None


def require_sha256_hex(value: object) -> str:
    """Return ``value`` when it is a lowercase SHA-256 digest, else fail closed."""
    if not is_sha256_hex(value):
        raise StrictJsonError("value is not a lowercase SHA-256 digest")
    return str(value)
