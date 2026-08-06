"""Dual-output publication: build, independently verify, then release.

``docs/PACKAGE_CONFORMANCE.md`` §4 defines the algorithm: build every member from
one immutable revision, validate members, write the archive with fixed ordering
and timestamps, have an independent reader re-open and re-render it, create a
versioned immutable release record *outside* the archive binding both artifacts
to the same payload digest, and publish atomically only when both pass.

PSD failure is not a silent downgrade to ``.oc2d`` only: the charter requires
both outputs, so a PSD failure blocks the release
(``docs/PSD_EXPORT_PROFILE.md`` §7).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from ..errors import ExportVerificationFailed
from ..jsonschema import Validator, load_validator
from ..raster.image import Image
from ..strict_json import canonical_bytes, sha256_hex
from .oc2d import build_package, open_package
from .psd import (
    GENERATED_PREFIX,
    READ_ME_NAME,
    SOURCE_REFERENCE_NAME,
    ParsedPsd,
    PsdLayer,
    parse_layered_psd,
    write_layered_psd,
)

RELEASE_FORMAT: Final[str] = "oneclick2d.dual-output-release"
RELEASE_FORMAT_VERSION: Final[str] = "0.1.0"
RELEASE_SCHEMA_PATH: Final[Path] = (
    Path(__file__).resolve().parents[2]
    / "schemas"
    / "release"
    / "v0.1"
    / "dual-output-release.schema.json"
)
_RELEASE_VALIDATOR: Validator | None = None


def release_validator() -> Validator:
    """Return the compiled dual-output release record validator."""
    global _RELEASE_VALIDATOR
    if _RELEASE_VALIDATOR is None:
        _RELEASE_VALIDATOR = load_validator(RELEASE_SCHEMA_PATH)
    return _RELEASE_VALIDATOR
# Global and local tolerance for comparing the PSD composite to the CIR neutral
# render. Both projections come from the same bytes, so the intended tolerance is
# zero; a nonzero local bound would let a real projection defect hide.
PSD_COMPOSITE_TOLERANCE: Final[int] = 0
READ_ME_TEXT: Final[str] = (
    "OneClick2D automatic draft. Layers, generated fills and safe ranges require "
    "review. Generated fills are plausible continuation, not recovered original "
    "content. This file is a raster interchange projection and carries no rig, "
    "physics or tracking data."
)


@dataclass(frozen=True)
class DualOutputRelease:
    """An immutable release record plus both verified artifacts."""

    record: dict[str, Any]
    package_bytes: bytes
    psd_bytes: bytes

    @property
    def release_id(self) -> str:
        return str(self.record["release_id"])

    @property
    def record_bytes(self) -> bytes:
        return canonical_bytes(self.record)


def _flatten(canvas_width: int, canvas_height: int, layers: tuple[PsdLayer, ...]) -> Image:
    """Composite visible layers in panel order, bottom to top."""
    result = Image(canvas_width, canvas_height)
    for layer in layers:
        if layer.visible:
            result = result.composite_over(layer.image)
    return result


def project_to_psd_layers(
    document: dict[str, Any],
    payloads: dict[str, bytes],
) -> tuple[PsdLayer, ...]:
    """Project a validated CIR revision onto the canonical PSD panel order.

    Returned bottom-to-top, which is the order the PSD format stores and the
    reverse of the Photoshop panel. Each generated fill is emitted immediately
    below the visible layer that owns it.
    """
    canvas = document["canvas"]
    width, height = int(canvas["width"]), int(canvas["height"])
    source_entry = next(
        (entry for entry in document["artifacts"] if entry["role"] == "source"), None
    )
    if source_entry is None:
        raise ExportVerificationFailed("project declares no source artifact to reference")
    source = Image.from_png(payloads[str(source_entry["id"])])

    regions_by_owner: dict[str, list[dict[str, Any]]] = {}
    for region in document["generated_regions"]:
        regions_by_owner.setdefault(str(region["owner_layer_id"]), []).append(region)

    layers: list[PsdLayer] = []
    next_id = 2

    # Bottom of the panel: the hidden, locked source reference.
    layers.append(
        PsdLayer(layer_id=1, name=SOURCE_REFERENCE_NAME, image=source, visible=False, locked=True)
    )

    for entry in sorted(document["layers"], key=lambda item: int(item["draw_order"])):
        layer_id = str(entry["id"])
        texture = Image.from_png(payloads[str(entry["texture_artifact_id"])])
        display_name = str(entry["display_name"])

        # The generated fill goes immediately beneath its visible layer, which in
        # bottom-to-top storage order means it is appended first.
        for region in regions_by_owner.get(layer_id, []):
            mask_image = Image.from_png(payloads[str(region["mask_artifact_id"])])
            fill = Image(width, height, bytearray(texture.data))
            coverage = mask_image.data[0::4]
            fill_alpha = bytearray(
                min(existing, coverage[index])
                for index, existing in enumerate(fill.data[3::4])
            )
            fill.data[3::4] = fill_alpha
            layers.append(
                PsdLayer(
                    layer_id=next_id,
                    name=f"{GENERATED_PREFIX}{display_name}",
                    image=fill,
                    visible=True,
                )
            )
            next_id += 1

        layers.append(
            PsdLayer(
                layer_id=next_id,
                name=display_name,
                image=texture,
                visible=bool(entry["visible"]),
                opacity=int(round(float(entry["opacity"]) * 255)),
            )
        )
        next_id += 1

    # Top of the panel: the hidden read-me.
    read_me = Image(width, height)
    layers.append(PsdLayer(layer_id=next_id, name=READ_ME_NAME, image=read_me, visible=False))
    return tuple(layers)


def _verify_psd(
    psd_bytes: bytes,
    document: dict[str, Any],
    expected_layers: tuple[PsdLayer, ...],
    neutral: Image,
) -> ParsedPsd:
    """Re-read the PSD and check every requirement in §5 of the export profile."""
    parsed = parse_layered_psd(psd_bytes)
    canvas = document["canvas"]
    if (parsed.width, parsed.height) != (int(canvas["width"]), int(canvas["height"])):
        raise ExportVerificationFailed("PSD canvas does not match the project canvas")
    if not parsed.has_srgb_profile:
        raise ExportVerificationFailed("PSD does not declare the sRGB profile")
    if len(parsed.layers) != len(expected_layers):
        raise ExportVerificationFailed("PSD layer count does not match the projection")

    for expected, actual in zip(expected_layers, parsed.layers, strict=True):
        if actual.name != expected.name:
            raise ExportVerificationFailed("PSD panel order does not match the projection")
        if actual.visible != expected.visible:
            raise ExportVerificationFailed("PSD layer visibility does not match the projection")
        if actual.opacity != expected.opacity:
            raise ExportVerificationFailed("PSD layer opacity does not match the projection")
        if actual.locked != expected.locked:
            raise ExportVerificationFailed("PSD layer lock state does not match the projection")
        if actual.blend_mode != "norm":
            raise ExportVerificationFailed("PSD layer blend mode is outside the profile")
        if actual.image.data != expected.image.data:
            raise ExportVerificationFailed("PSD layer pixels do not match the projection")

    names = [layer.name for layer in parsed.layers]
    if names[0] != SOURCE_REFERENCE_NAME:
        raise ExportVerificationFailed("PSD source reference is not at the bottom of the panel")
    if not parsed.layers[0].locked or parsed.layers[0].visible:
        raise ExportVerificationFailed("PSD source reference must be hidden and locked")
    if names[-1] != READ_ME_NAME:
        raise ExportVerificationFailed("PSD read-me is not at the top of the panel")

    # Each generated fill must sit directly below the visible layer it names.
    for index, name in enumerate(names):
        if not name.startswith(GENERATED_PREFIX):
            continue
        owner = name[len(GENERATED_PREFIX) :]
        if index + 1 >= len(names) or names[index + 1] != owner:
            raise ExportVerificationFailed(
                "PSD generated fill is not immediately below its visible layer"
            )

    # Compositing in reverse panel order must reproduce the CIR neutral result.
    flattened = _flatten(parsed.width, parsed.height, tuple(
        PsdLayer(
            layer_id=layer.layer_id,
            name=layer.name,
            image=layer.image,
            visible=layer.visible,
            opacity=layer.opacity,
            locked=layer.locked,
        )
        for layer in parsed.layers
    ))
    worst = 0
    for index in range(0, len(flattened.data), 4):
        for channel in range(4):
            worst = max(worst, abs(flattened.data[index + channel] - neutral.data[index + channel]))
    if worst > PSD_COMPOSITE_TOLERANCE:
        raise ExportVerificationFailed(
            "PSD composite does not reproduce the CIR neutral result within tolerance"
        )
    if parsed.merged.data != neutral.data:
        raise ExportVerificationFailed("PSD merged composite does not match the CIR neutral result")
    return parsed


def publish_dual_output(
    *,
    release_id: str,
    account_id: str,
    project_id: str,
    document: dict[str, Any],
    payloads: dict[str, bytes],
    validation_report: dict[str, Any],
    run_manifest: dict[str, Any],
    registry_snapshots: dict[str, bytes],
    neutral: Image,
    created_at: str,
    preview_png: bytes | None = None,
) -> DualOutputRelease:
    """Build, independently verify and atomically release both artifacts."""
    manifest_bytes = canonical_bytes(document)
    payload_digest = sha256_hex(manifest_bytes)

    if validation_report.get("project_payload_sha256") != payload_digest:
        raise ExportVerificationFailed("validation report is not bound to this payload")
    if str(validation_report.get("status")) not in ("pass", "pass_with_review"):
        # A blocking finding must stop the release, never be published anyway.
        raise ExportVerificationFailed("validation status does not permit export")
    if run_manifest.get("project_payload_sha256") != payload_digest:
        raise ExportVerificationFailed("run manifest is not bound to this payload")

    package_bytes, _index = build_package(
        manifest_bytes=manifest_bytes,
        artifacts=payloads,
        validation_bytes=canonical_bytes(validation_report),
        run_manifest_bytes=canonical_bytes(run_manifest),
        registry_snapshots=registry_snapshots,
        preview_png=preview_png,
    )

    # Independent reader: re-open the archive we just wrote and re-render it.
    opened = open_package(package_bytes)
    if opened.manifest_bytes != manifest_bytes:
        raise ExportVerificationFailed("re-opened manifest does not match the published payload")
    if set(opened.artifacts) != set(payloads):
        raise ExportVerificationFailed("re-opened package artifacts do not match the payload")
    for artifact_id, data in opened.artifacts.items():
        if data != payloads[artifact_id]:
            raise ExportVerificationFailed("re-opened artifact bytes differ from the payload")

    from ..validation import validate_project
    from ..registries import load_registries

    reopened_report = validate_project(opened.manifest, opened.artifacts, load_registries())
    if not reopened_report.export_ready:
        raise ExportVerificationFailed("re-opened package failed independent validation")
    if reopened_report.project_payload_sha256 != payload_digest:
        raise ExportVerificationFailed("re-opened package payload digest changed")

    psd_layers = project_to_psd_layers(document, payloads)
    psd_bytes = write_layered_psd(neutral, psd_layers, neutral)
    _verify_psd(psd_bytes, document, psd_layers, neutral)

    record = {
        "format": RELEASE_FORMAT,
        "format_version": RELEASE_FORMAT_VERSION,
        "release_id": release_id,
        "account_id": account_id,
        "project_id": project_id,
        "project_revision_id": str(document["revision_id"]),
        "project_payload_sha256": payload_digest,
        # Both artifacts re-opened and re-rendered successfully above; anything
        # less would have raised before reaching this point.
        "status": "verified",
        "created_at": created_at,
        "oc2d": {
            "role": "oc2d",
            "media_type": "application/vnd.oneclick2d.project+zip",
            "byte_length": len(package_bytes),
            "sha256": sha256_hex(package_bytes),
            "verifier_id": "oneclick2d.verifier.oc2d-reopen",
            "verifier_version": "0.1.0",
            "verifier_config_sha256": sha256_hex(b"oneclick2d.verifier.oc2d-reopen/0.1.0"),
            "reopen_passed": True,
            "render_or_composite_passed": True,
        },
        "layered_psd": {
            "role": "layered_psd",
            "media_type": "image/vnd.adobe.photoshop",
            "byte_length": len(psd_bytes),
            "sha256": sha256_hex(psd_bytes),
            "verifier_id": "oneclick2d.verifier.psd-reopen",
            "verifier_version": "0.1.0",
            "verifier_config_sha256": sha256_hex(b"oneclick2d.verifier.psd-reopen/0.1.0"),
            "reopen_passed": True,
            "render_or_composite_passed": True,
        },
    }
    release_validator().check(record, label="dual-output release record")
    return DualOutputRelease(record=record, package_bytes=package_bytes, psd_bytes=psd_bytes)
