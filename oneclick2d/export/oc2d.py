"""``.oc2d`` package writer and independent strict reader.

Layout and digest domains follow ``docs/PACKAGE_CONFORMANCE.md``:

    manifest.json
    artifacts/<sha256>.<ext>
    reports/validation.json
    provenance/run-manifest.json
    registries/<name>.json
    package-index.json
    thumbnails/preview.png      # optional, non-authoritative

The digest domains are acyclic: ``manifest.json`` is the authoritative project
and contains no report hash; ``validation.json`` binds the project payload
digest; ``package-index.json`` records every other member; and the final archive
digest lives outside the archive in the release record.

The reader is written to distrust the writer: it re-reads members, re-computes
digests and rejects traversal, links, duplicates, unknown members, bombs and
unsupported versions.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from typing import Any, Final

from ..errors import ContractError, ExportVerificationFailed, ResourceLimitError
from ..strict_json import canonical_bytes, loads_strict, sha256_hex

MANIFEST_NAME: Final[str] = "manifest.json"
VALIDATION_NAME: Final[str] = "reports/validation.json"
RUN_MANIFEST_NAME: Final[str] = "provenance/run-manifest.json"
INDEX_NAME: Final[str] = "package-index.json"
PREVIEW_NAME: Final[str] = "thumbnails/preview.png"
REGISTRY_PREFIX: Final[str] = "registries/"
ARTIFACT_PREFIX: Final[str] = "artifacts/"
PACKAGE_FORMAT: Final[str] = "oneclick2d.package-index"
PACKAGE_FORMAT_VERSION: Final[str] = "0.2.0"

MAX_MEMBERS: Final[int] = 8192
MAX_MEMBER_BYTES: Final[int] = 512 * 1024 * 1024
MAX_TOTAL_BYTES: Final[int] = 2 * 1024 * 1024 * 1024
MAX_COMPRESSION_RATIO: Final[int] = 200
MAX_NAME_BYTES: Final[int] = 255
MAX_PATH_DEPTH: Final[int] = 4
# Deterministic archive timestamp: packages built from one revision must be
# byte-identical, so the wall clock must not leak into member metadata.
FIXED_TIMESTAMP: Final[tuple[int, int, int, int, int, int]] = (1980, 1, 1, 0, 0, 0)


@dataclass(frozen=True)
class PackageMember:
    path: str
    media_type: str
    role: str
    byte_length: int
    sha256: str

    def as_index(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "media_type": self.media_type,
            "role": self.role,
            "byte_length": self.byte_length,
            "sha256": self.sha256,
        }


def _check_member_name(name: str) -> None:
    """Reject every path shape ``docs/PACKAGE_CONFORMANCE.md`` §1 forbids."""
    if not name or name != name.strip():
        raise ContractError("package member name is empty or padded")
    if len(name.encode("utf-8")) > MAX_NAME_BYTES:
        raise ContractError("package member name exceeds the length bound")
    if name.startswith("/") or name.endswith("/"):
        raise ContractError("package member name must be relative")
    if "\\" in name:
        raise ContractError("package member name must not contain backslashes")
    if len(name) >= 2 and name[1] == ":":
        raise ContractError("package member name must not contain a drive letter")
    segments = name.split("/")
    if len(segments) > MAX_PATH_DEPTH:
        raise ContractError("package member nesting exceeds the depth bound")
    for segment in segments:
        if not segment or segment in (".", ".."):
            raise ContractError("package member name contains a traversal segment")
        if not all(character.isalnum() or character in "._-" for character in segment):
            raise ContractError("package member name contains an unsupported character")


def build_package(
    *,
    manifest_bytes: bytes,
    artifacts: dict[str, bytes],
    validation_bytes: bytes,
    run_manifest_bytes: bytes,
    registry_snapshots: dict[str, bytes],
    preview_png: bytes | None = None,
) -> tuple[bytes, dict[str, Any]]:
    """Write a deterministic ``.oc2d`` archive and return it with its index."""
    members: list[PackageMember] = [
        PackageMember(MANIFEST_NAME, "application/json", "project_manifest", len(manifest_bytes), sha256_hex(manifest_bytes)),
    ]
    payload_by_path: dict[str, bytes] = {MANIFEST_NAME: manifest_bytes}

    for artifact_id, data in sorted(artifacts.items()):
        if not artifact_id.startswith("sha256:"):
            raise ContractError("artifact id is not a content address")
        digest = artifact_id.split(":", 1)[1]
        if sha256_hex(data) != digest:
            raise ContractError("artifact bytes do not match the declared content address")
        extension = _extension_for(data)
        path = f"{ARTIFACT_PREFIX}{digest}.{extension}"
        payload_by_path[path] = data
        members.append(
            PackageMember(path, _media_type_for(extension), "artifact", len(data), digest)
        )

    payload_by_path[VALIDATION_NAME] = validation_bytes
    members.append(
        PackageMember(VALIDATION_NAME, "application/json", "validation_report", len(validation_bytes), sha256_hex(validation_bytes))
    )
    payload_by_path[RUN_MANIFEST_NAME] = run_manifest_bytes
    members.append(
        PackageMember(RUN_MANIFEST_NAME, "application/json", "run_manifest", len(run_manifest_bytes), sha256_hex(run_manifest_bytes))
    )
    for name, data in sorted(registry_snapshots.items()):
        path = f"{REGISTRY_PREFIX}{name}.json"
        payload_by_path[path] = data
        members.append(
            PackageMember(path, "application/json", "registry_snapshot", len(data), sha256_hex(data))
        )
    if preview_png is not None:
        payload_by_path[PREVIEW_NAME] = preview_png
        members.append(
            PackageMember(PREVIEW_NAME, "image/png", "preview", len(preview_png), sha256_hex(preview_png))
        )

    for member in members:
        _check_member_name(member.path)
    paths = [member.path for member in members]
    if len(paths) != len(set(paths)):
        raise ContractError("package contains duplicate member paths")
    lowered = [path.lower() for path in paths]
    if len(lowered) != len(set(lowered)):
        raise ContractError("package contains case-colliding member paths")
    if len(members) + 1 > MAX_MEMBERS:
        raise ResourceLimitError("package member count limit exceeded")
    total = sum(member.byte_length for member in members)
    if total > MAX_TOTAL_BYTES:
        raise ResourceLimitError("package total size limit exceeded")

    index = {
        "format": PACKAGE_FORMAT,
        "format_version": PACKAGE_FORMAT_VERSION,
        "members": [member.as_index() for member in sorted(members, key=lambda item: item.path)],
    }
    index_bytes = canonical_bytes(index)
    payload_by_path[INDEX_NAME] = index_bytes

    buffer = _write_zip(payload_by_path)
    if len(buffer) > MAX_TOTAL_BYTES:
        raise ResourceLimitError("package archive size limit exceeded")
    return buffer, index


def _extension_for(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data[:1] in (b"{", b"["):
        return "json"
    return "bin"


def _media_type_for(extension: str) -> str:
    return {
        "png": "image/png",
        "json": "application/json",
        "bin": "application/octet-stream",
    }[extension]


def _write_zip(payload_by_path: dict[str, bytes]) -> bytes:
    import io

    buffer = io.BytesIO()
    # Fixed member order and timestamps keep the archive byte-reproducible.
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(payload_by_path):
            info = zipfile.ZipInfo(path, date_time=FIXED_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            info.create_system = 3
            archive.writestr(info, payload_by_path[path])
    return buffer.getvalue()


@dataclass(frozen=True)
class OpenedPackage:
    """A package that an independent reader accepted."""

    manifest: dict[str, Any]
    manifest_bytes: bytes
    artifacts: dict[str, bytes]
    validation: dict[str, Any]
    run_manifest: dict[str, Any]
    registries: dict[str, bytes]
    index: dict[str, Any]
    preview_png: bytes | None
    archive_sha256: str


def open_package(data: bytes) -> OpenedPackage:
    """Independently re-open and verify an ``.oc2d`` archive.

    Written as a distrusting reader: nothing is taken from the writer's word.
    Every member is bounded, re-digested and cross-checked against the index, and
    the manifest payload digest is recomputed from canonical bytes.
    """
    import io

    if len(data) > MAX_TOTAL_BYTES:
        raise ResourceLimitError("package archive exceeds the accepted size")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ExportVerificationFailed("package is not a readable archive") from exc

    with archive:
        infos = archive.infolist()
        if len(infos) > MAX_MEMBERS:
            raise ResourceLimitError("package member count limit exceeded")
        names = [info.filename for info in infos]
        if len(names) != len(set(names)):
            raise ExportVerificationFailed("package contains duplicate member paths")
        if len({name.lower() for name in names}) != len(names):
            raise ExportVerificationFailed("package contains case-colliding member paths")

        payloads: dict[str, bytes] = {}
        total = 0
        for info in infos:
            try:
                _check_member_name(info.filename)
            except ContractError as exc:
                raise ExportVerificationFailed(f"package member path is unsafe: {exc}") from exc
            # Directory entries and links have no place in a package.
            if info.is_dir():
                raise ExportVerificationFailed("package must not contain directory entries")
            mode = info.external_attr >> 16
            if mode & 0o170000 == 0o120000:
                raise ExportVerificationFailed("package must not contain symbolic links")
            if info.file_size > MAX_MEMBER_BYTES:
                raise ResourceLimitError("package member exceeds the size bound")
            total += info.file_size
            if total > MAX_TOTAL_BYTES:
                raise ResourceLimitError("package total size limit exceeded")
            if info.compress_size > 0:
                ratio = info.file_size / info.compress_size
                if ratio > MAX_COMPRESSION_RATIO:
                    raise ResourceLimitError("package member compression ratio limit exceeded")
            with archive.open(info) as handle:
                body = handle.read(MAX_MEMBER_BYTES + 1)
            if len(body) > MAX_MEMBER_BYTES:
                raise ResourceLimitError("package member exceeds the size bound")
            if len(body) != info.file_size:
                raise ExportVerificationFailed("package member length disagrees with its header")
            payloads[info.filename] = body

    for required in (MANIFEST_NAME, VALIDATION_NAME, RUN_MANIFEST_NAME, INDEX_NAME):
        if required not in payloads:
            raise ExportVerificationFailed("package is missing a required member")

    index = loads_strict(payloads[INDEX_NAME])
    if not isinstance(index, dict):
        raise ExportVerificationFailed("package index is not an object")
    if index.get("format") != PACKAGE_FORMAT:
        raise ExportVerificationFailed("package index format is unsupported")
    if index.get("format_version") != PACKAGE_FORMAT_VERSION:
        # An unknown major/minor must fail closed rather than be guessed at.
        raise ExportVerificationFailed("package index version is unsupported")
    entries = index.get("members")
    if not isinstance(entries, list) or not entries:
        raise ExportVerificationFailed("package index lists no members")

    indexed: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ExportVerificationFailed("package index entry is malformed")
        path = str(entry.get("path"))
        if path in indexed:
            raise ExportVerificationFailed("package index lists a member twice")
        indexed[path] = entry

    # The index covers every member except itself: no gaps, no extras.
    expected = set(payloads) - {INDEX_NAME}
    if expected != set(indexed):
        raise ExportVerificationFailed("package index does not match the archive members")

    for path, entry in indexed.items():
        body = payloads[path]
        if len(body) != int(entry.get("byte_length", -1)):
            raise ExportVerificationFailed("package member length disagrees with the index")
        if sha256_hex(body) != entry.get("sha256"):
            raise ExportVerificationFailed("package member digest disagrees with the index")

    manifest = loads_strict(payloads[MANIFEST_NAME])
    if not isinstance(manifest, dict):
        raise ExportVerificationFailed("package manifest is not an object")
    if manifest.get("format") != "oneclick2d.project":
        raise ExportVerificationFailed("package manifest format is unsupported")
    if manifest.get("format_version") != "0.2.0":
        raise ExportVerificationFailed("package manifest version is unsupported")
    # Canonical re-serialization must reproduce the stored bytes, or the payload
    # digest the release record binds would be meaningless.
    if canonical_bytes(manifest) != payloads[MANIFEST_NAME]:
        raise ExportVerificationFailed("package manifest is not canonical JSON")

    validation = loads_strict(payloads[VALIDATION_NAME])
    run_manifest = loads_strict(payloads[RUN_MANIFEST_NAME])
    if not isinstance(validation, dict) or not isinstance(run_manifest, dict):
        raise ExportVerificationFailed("package report member is not an object")

    payload_digest = sha256_hex(payloads[MANIFEST_NAME])
    if validation.get("project_payload_sha256") != payload_digest:
        raise ExportVerificationFailed("validation report is bound to a different project payload")
    if run_manifest.get("project_payload_sha256") != payload_digest:
        raise ExportVerificationFailed("run manifest is bound to a different project payload")
    if run_manifest.get("project_revision_id") != manifest.get("revision_id"):
        raise ExportVerificationFailed("run manifest is bound to a different revision")

    artifacts: dict[str, bytes] = {}
    for path, body in payloads.items():
        if not path.startswith(ARTIFACT_PREFIX):
            continue
        digest = path[len(ARTIFACT_PREFIX) :].rsplit(".", 1)[0]
        if sha256_hex(body) != digest:
            raise ExportVerificationFailed("artifact member is not stored under its digest")
        artifacts[f"sha256:{digest}"] = body

    declared = {str(entry["id"]) for entry in manifest.get("artifacts", [])}
    if declared != set(artifacts):
        raise ExportVerificationFailed("package artifacts do not match the manifest")

    registries = {
        path[len(REGISTRY_PREFIX) :].rsplit(".", 1)[0]: body
        for path, body in payloads.items()
        if path.startswith(REGISTRY_PREFIX)
    }
    for reference_key in ("parameter_registry", "reason_code_registry"):
        reference = manifest.get(reference_key)
        if not isinstance(reference, dict):
            raise ExportVerificationFailed("manifest registry reference is malformed")
        snapshot = next(
            (body for body in registries.values() if sha256_hex(body) == reference.get("sha256")),
            None,
        )
        if snapshot is None:
            raise ExportVerificationFailed("manifest registry reference has no bound snapshot")

    return OpenedPackage(
        manifest=manifest,
        manifest_bytes=payloads[MANIFEST_NAME],
        artifacts=artifacts,
        validation=validation,
        run_manifest=run_manifest,
        registries=registries,
        index=index,
        preview_png=payloads.get(PREVIEW_NAME),
        archive_sha256=sha256_hex(data),
    )
