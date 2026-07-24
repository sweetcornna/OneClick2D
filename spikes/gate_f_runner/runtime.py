"""Integrity, workspace and bounded-output primitives for the Gate F spike."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
from pathlib import Path
from typing import Any

from .contracts import (
    ArtifactRef,
    CancellationRequested,
    ResourceLimitExceeded,
    ResourceLimits,
    SpecValidationError,
)

ID_RE = re.compile(r"^[a-z][a-z0-9_.-]{2,127}$")
SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
MAX_SAFE_INTEGER = 9_007_199_254_740_991
MAX_JSON_BYTES = 1_048_576
MAX_JSON_DEPTH = 32
MAX_JSON_NODES = 10_000
MAX_JSON_STRING = 16_384


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_bounded_file(path: Path, maximum: int = MAX_JSON_BYTES) -> bytes:
    try:
        if path.is_symlink() or not path.is_file():
            raise SpecValidationError("input must be a regular non-symlink file")
        with path.open("rb") as stream:
            data = stream.read(maximum + 1)
    except (OSError, ValueError) as exc:
        raise SpecValidationError("input file cannot be read") from exc
    if len(data) > maximum:
        raise SpecValidationError("input byte limit exceeded")
    return data


def _reject_constant(value: str) -> None:
    raise SpecValidationError(f"non-finite JSON number: {value}")


def _pairs_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SpecValidationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _validate_json_value(value: Any, *, depth: int = 0, nodes: list[int] | None = None) -> None:
    if nodes is None:
        nodes = [0]
    nodes[0] += 1
    if nodes[0] > MAX_JSON_NODES:
        raise SpecValidationError("JSON node limit exceeded")
    if depth > MAX_JSON_DEPTH:
        raise SpecValidationError("JSON depth limit exceeded")
    if isinstance(value, str):
        if len(value) > MAX_JSON_STRING:
            raise SpecValidationError("JSON string limit exceeded")
        if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
            raise SpecValidationError("JSON strings cannot contain Unicode surrogates")
    elif isinstance(value, bool) or value is None:
        return
    elif isinstance(value, int):
        if abs(value) > MAX_SAFE_INTEGER:
            raise SpecValidationError("JSON integer exceeds I-JSON safe range")
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise SpecValidationError("JSON number must be finite")
    elif isinstance(value, list):
        for item in value:
            _validate_json_value(item, depth=depth + 1, nodes=nodes)
    elif isinstance(value, dict):
        for key, item in value.items():
            _validate_json_value(key, depth=depth + 1, nodes=nodes)
            _validate_json_value(item, depth=depth + 1, nodes=nodes)
    else:
        raise SpecValidationError("unsupported JSON value")


def strict_load_json_bytes(data: bytes) -> Any:
    if len(data) > MAX_JSON_BYTES:
        raise SpecValidationError("JSON byte limit exceeded")
    try:
        text = data.decode("utf-8", errors="strict")
        value = json.loads(
            text,
            object_pairs_hook=_pairs_no_duplicates,
            parse_constant=_reject_constant,
        )
    except UnicodeDecodeError as exc:
        raise SpecValidationError("JSON must be valid UTF-8") from exc
    except json.JSONDecodeError as exc:
        raise SpecValidationError("invalid JSON") from exc
    _validate_json_value(value)
    return value


def _jcs_number(value: int | float) -> str:
    if isinstance(value, int):
        return str(value)
    if value == 0:
        return "0"
    negative = value < 0
    text = repr(abs(value)).lower()
    coefficient, exponent_text = (text.split("e", 1) + ["0"])[:2] if "e" in text else (text, "0")
    exponent = int(exponent_text)
    integer, dot, fraction = coefficient.partition(".")
    digits = (integer + fraction).lstrip("0") or "0"
    decimal_position = len(integer) + exponent - (len(integer + fraction) - len((integer + fraction).lstrip("0")))
    while len(digits) > 1 and digits.endswith("0"):
        digits = digits[:-1]
    scientific_exponent = decimal_position - 1
    if -6 <= scientific_exponent < 21:
        if decimal_position <= 0:
            result = "0." + "0" * (-decimal_position) + digits
        elif decimal_position >= len(digits):
            result = digits + "0" * (decimal_position - len(digits))
        else:
            result = digits[:decimal_position] + "." + digits[decimal_position:]
    else:
        result = digits[0]
        if len(digits) > 1:
            result += "." + digits[1:]
        result += "e" + ("+" if scientific_exponent >= 0 else "") + str(scientific_exponent)
    return "-" + result if negative else result


def _jcs_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _jcs_serialize(value: object) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return _jcs_number(value)
    if isinstance(value, str):
        return _jcs_string(value)
    if isinstance(value, list):
        return "[" + ",".join(_jcs_serialize(item) for item in value) + "]"
    if isinstance(value, dict):
        items = sorted(value.items(), key=lambda item: item[0].encode("utf-16-be"))
        return "{" + ",".join(_jcs_string(key) + ":" + _jcs_serialize(item) for key, item in items) + "}"
    raise SpecValidationError("value cannot be serialized as strict JSON")


def canonical_json_bytes(value: object) -> bytes:
    _validate_json_value(value)
    try:
        return _jcs_serialize(value).encode("utf-8")
    except (TypeError, UnicodeEncodeError, ValueError, OverflowError) as exc:
        raise SpecValidationError("value cannot be serialized as RFC 8785 JSON") from exc


def digest_framed(domain: str, fields: tuple[bytes, ...]) -> str:
    digest = hashlib.sha256()
    for field in (domain.encode("ascii"), *fields):
        digest.update(len(field).to_bytes(8, "big"))
        digest.update(field)
    return digest.hexdigest()


def derive_stage_seed(root_seed_u64: str, stage_id: str) -> str:
    if not re.fullmatch(r"[0-9]{20}", root_seed_u64):
        raise SpecValidationError("seed must be a zero-padded 20-digit decimal")
    value = int(root_seed_u64)
    if value > 18_446_744_073_709_551_615:
        raise SpecValidationError("seed exceeds unsigned 64-bit range")
    if not ID_RE.fullmatch(stage_id):
        raise SpecValidationError("invalid stage id")
    digest = hashlib.sha256(b"oneclick2d.gate-f.stage-seed.v1\0")
    digest.update(value.to_bytes(8, "big"))
    digest.update(b"\0")
    digest.update(stage_id.encode("ascii"))
    return f"{int.from_bytes(digest.digest()[:8], 'big'):020d}"


def resolve_safe_file(base: Path, relative: str) -> Path:
    if not relative or "\\" in relative or relative.startswith("/") or re.match(r"^[A-Za-z]:", relative):
        raise SpecValidationError("unsafe relative path")
    parts = Path(relative).parts
    if any(part in {"", ".", ".."} for part in parts):
        raise SpecValidationError("unsafe relative path")
    candidate = base.joinpath(*parts)
    if candidate.is_symlink():
        raise SpecValidationError("symlink inputs are prohibited")
    try:
        candidate.resolve(strict=True).relative_to(base.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise SpecValidationError("path escapes specification directory") from exc
    if not candidate.is_file():
        raise SpecValidationError("referenced path is not a file")
    return candidate


class CancellationToken:
    def __init__(self, sentinel: Path) -> None:
        self._sentinel = sentinel

    def checkpoint(self) -> None:
        if self._sentinel.exists():
            raise CancellationRequested("cancellation requested")


class ArtifactSink:
    def __init__(self, candidate_dir: Path, run_dir: Path, limits: ResourceLimits) -> None:
        self._candidate_dir = candidate_dir
        self._run_dir = run_dir
        self._limits = limits
        self._artifacts: list[ArtifactRef] = []
        self._names: set[str] = set()
        self._bytes = 0

    @property
    def artifacts(self) -> tuple[ArtifactRef, ...]:
        return tuple(self._artifacts)

    def open_binary(self, name: str, *, role: str, media_type: str) -> "BoundedArtifactWriter":
        if not re.fullmatch(r"[a-z][a-z0-9_.-]{2,127}", name) or name in self._names:
            raise ResourceLimitExceeded("invalid or duplicate output name")
        if len(self._names) + 1 > self._limits.max_output_files:
            raise ResourceLimitExceeded("output file limit exceeded")
        self._names.add(name)
        return BoundedArtifactWriter(self, name, role, media_type)

    def write_bytes(self, name: str, data: bytes, *, role: str, media_type: str) -> ArtifactRef:
        writer = self.open_binary(name, role=role, media_type=media_type)
        with writer:
            writer.write(data)
        return writer.artifact

    def _remaining_bytes(self, current_file_bytes: int) -> int:
        return self._limits.max_output_bytes - self._bytes - current_file_bytes

    def _complete(
        self,
        *,
        name: str,
        role: str,
        media_type: str,
        path: Path,
        sha256: str,
        byte_length: int,
    ) -> ArtifactRef:
        artifact = ArtifactRef(
            role=role,
            media_type=media_type,
            path=path,
            uri=path.relative_to(self._run_dir).as_posix(),
            sha256=sha256,
            byte_length=byte_length,
        )
        self._bytes += byte_length
        self._artifacts.append(artifact)
        return artifact

    def _abort(self, name: str) -> None:
        self._names.discard(name)


class BoundedArtifactWriter:
    """Sequential, hash-tracking writer bounded by the stage output budget."""

    def __init__(self, sink: ArtifactSink, name: str, role: str, media_type: str) -> None:
        self._sink = sink
        self._name = name
        self._role = role
        self._media_type = media_type
        self._path = sink._candidate_dir / name
        self._temp = self._path.with_suffix(self._path.suffix + ".tmp")
        self._stream = None
        self._digest = hashlib.sha256()
        self._byte_length = 0
        self._artifact: ArtifactRef | None = None

    @property
    def artifact(self) -> ArtifactRef:
        if self._artifact is None:
            raise ResourceLimitExceeded("output writer has not completed")
        return self._artifact

    @property
    def closed(self) -> bool:
        return self._stream is None or self._stream.closed

    def __enter__(self) -> "BoundedArtifactWriter":
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._stream = self._temp.open("xb")
        except Exception:
            self._sink._abort(self._name)
            raise
        return self

    def write(self, data: bytes | bytearray | memoryview) -> int:
        if self._stream is None or self._stream.closed:
            raise ValueError("output writer is closed")
        block = bytes(data)
        if len(block) > self._sink._remaining_bytes(self._byte_length):
            raise ResourceLimitExceeded("output byte limit exceeded")
        written = self._stream.write(block)
        if written != len(block):
            raise OSError("short output write")
        self._digest.update(block)
        self._byte_length += written
        return written

    def tell(self) -> int:
        return self._byte_length

    def flush(self) -> None:
        if self._stream is not None and not self._stream.closed:
            self._stream.flush()

    def writable(self) -> bool:
        return True

    def readable(self) -> bool:
        return False

    def seekable(self) -> bool:
        return False

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> bool:
        try:
            if self._stream is not None and not self._stream.closed:
                self._stream.close()
            if exc_type is None:
                os.replace(self._temp, self._path)
                self._artifact = self._sink._complete(
                    name=self._name,
                    role=self._role,
                    media_type=self._media_type,
                    path=self._path,
                    sha256=self._digest.hexdigest(),
                    byte_length=self._byte_length,
                )
            else:
                self._sink._abort(self._name)
                if self._temp.exists():
                    self._temp.unlink()
        except Exception:
            self._sink._abort(self._name)
            if self._temp.exists():
                self._temp.unlink()
            raise
        return False


class RunWorkspace:
    def __init__(self, root: Path, run_id: str) -> None:
        if not ID_RE.fullmatch(run_id):
            raise SpecValidationError("invalid run id")
        self.root = root
        self.run_id = run_id
        self.run_dir = root / run_id
        self.cancel_sentinel = self.run_dir / "cancel.request"

    def create(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        if self.root.is_symlink():
            raise SpecValidationError("workspace root cannot be a symlink")
        try:
            self.run_dir.mkdir(exist_ok=False)
        except FileExistsError as exc:
            raise SpecValidationError("run id already exists") from exc
        (self.run_dir / "spec" / "resolved-configs").mkdir(parents=True)
        (self.run_dir / "inputs").mkdir()
        (self.run_dir / "attempts").mkdir()
        (self.run_dir / "committed").mkdir()

    def materialize(self, spec_bytes: bytes, config_bytes: dict[str, bytes], source_bytes: bytes) -> tuple[Path, dict[str, Path], Path]:
        spec_copy = self.run_dir / "spec" / "run-spec.json"
        spec_copy.write_bytes(spec_bytes)
        config_copies: dict[str, Path] = {}
        for index, (relative, data) in enumerate(sorted(config_bytes.items())):
            destination = self.run_dir / "spec" / "resolved-configs" / f"config.{index:03d}.json"
            destination.write_bytes(data)
            config_copies[relative] = destination
        source_copy = self.run_dir / "inputs" / "source.bin"
        source_copy.write_bytes(source_bytes)
        return spec_copy, config_copies, source_copy

    def begin_attempt(self, stage_id: str, attempt_id: str) -> tuple[Path, Path, Path]:
        attempt = self.run_dir / "attempts" / stage_id / attempt_id
        candidate = attempt / "candidate"
        scratch = attempt / "scratch"
        candidate.mkdir(parents=True)
        scratch.mkdir()
        return attempt, candidate, scratch

    def commit(self, stage_id: str, attempt_id: str, candidate: Path, artifacts: tuple[ArtifactRef, ...]) -> tuple[ArtifactRef, ...]:
        destination = self.run_dir / "committed" / stage_id / attempt_id
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise ResourceLimitExceeded("committed output already exists")
        declared = {artifact.path.name: artifact for artifact in artifacts}
        actual: dict[str, Path] = {}
        for entry in candidate.iterdir():
            if entry.is_symlink() or not entry.is_file() or entry.name in actual:
                raise ResourceLimitExceeded("candidate output contains an undeclared or unsafe entry")
            actual[entry.name] = entry
        if set(actual) != set(declared):
            raise ResourceLimitExceeded("candidate output does not exactly match declared artifacts")
        for name, artifact in declared.items():
            path = actual[name]
            if path.stat().st_size != artifact.byte_length or sha256_file(path) != artifact.sha256:
                raise ResourceLimitExceeded("candidate artifact changed after declaration")
        os.replace(candidate, destination)
        committed: list[ArtifactRef] = []
        for name, artifact in declared.items():
            path = destination / name
            committed.append(
                ArtifactRef(
                    role=artifact.role,
                    media_type=artifact.media_type,
                    path=path,
                    uri=path.relative_to(self.run_dir).as_posix(),
                    sha256=artifact.sha256,
                    byte_length=artifact.byte_length,
                )
            )
        return tuple(committed)

    def write_atomic(self, relative: str, data: bytes) -> Path:
        path = self.run_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_bytes(data)
        os.replace(temp, path)
        return path

    def clean_attempt(self, attempt: Path, candidate: Path, scratch: Path) -> None:
        if candidate.exists():
            shutil.rmtree(candidate)
        if scratch.exists():
            shutil.rmtree(scratch)
        if attempt.exists() and not any(attempt.iterdir()):
            attempt.rmdir()
