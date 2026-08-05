"""CIR v0.2 project assembly.

Builds the authoritative project document defined by ``docs/CIR_SPEC.md`` and
``schemas/cir/v0.2/project.schema.json``. The CIR is the authority; PSD and
preview buffers are read-only projections.

Every artifact is content-addressed as ``sha256:<digest>``, every reference
resolves, and any authoritative change produces a new revision with a new payload
digest (``docs/CIR_SPEC.md`` §9).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from .errors import ContractError
from .geometry import encode_deltas, encode_indices, encode_vertices
from .jsonschema import Validator, load_validator
from .pipeline.context import derive_seed, require_entity_id
from .registries import Registries
from .stages.decompose import Decomposition
from .stages.rig import Rig
from .stages.synthesize import Synthesis
from .strict_json import canonical_bytes, sha256_hex

CIR_FORMAT: Final[str] = "oneclick2d.project"
CIR_FORMAT_VERSION: Final[str] = "0.2.0"
SCHEMA_PATH: Final[Path] = (
    Path(__file__).resolve().parents[1] / "schemas" / "cir" / "v0.2" / "project.schema.json"
)
_VALIDATOR: Validator | None = None


def project_validator() -> Validator:
    """Return the compiled CIR v0.2 schema validator."""
    global _VALIDATOR
    if _VALIDATOR is None:
        _VALIDATOR = load_validator(SCHEMA_PATH)
    return _VALIDATOR


# When one digest legitimately serves several roles, the most specific role
# wins so the stored label does not depend on registration order.
ROLE_PRIORITY: Final[tuple[str, ...]] = (
    "source",
    "layer_texture",
    "generated_mask",
    "visible_mask",
    "mesh_vertices",
    "mesh_indices",
    "mesh_delta",
    "override_mask",
    "preview",
    "evidence",
)
# Likewise the most restrictive sensitivity wins.
SENSITIVITY_PRIORITY: Final[tuple[str, ...]] = (
    "user_content",
    "derived_user_content",
    "non_sensitive",
)


@dataclass
class ArtifactTable:
    """Content-addressed artifact store for a project payload."""

    entries: dict[str, dict[str, Any]] = field(default_factory=dict)
    payloads: dict[str, bytes] = field(default_factory=dict)
    roles: dict[str, set[str]] = field(default_factory=dict)

    def add(self, data: bytes, *, media_type: str, role: str, sensitivity: str) -> str:
        """Register bytes and return their content address.

        Identical bytes are one artifact, and that collision is expected rather
        than exceptional: a layer covering the whole subject has a texture equal
        to the normalized source, and a neutral delta sample is all zeros for
        every mesh of the same vertex count. Because the CIR gives an artifact a
        single ``role``, the digest keeps the highest-priority role it serves
        (see ``ROLE_PRIORITY``) and the more restrictive sensitivity, so the
        stored label never depends on registration order.
        ``oneclick2d.validation`` knows which roles may legitimately coincide.
        """
        digest = sha256_hex(data)
        artifact_id = f"sha256:{digest}"
        extension = {
            "image/png": "png",
            "application/octet-stream": "bin",
            "application/json": "json",
        }.get(media_type)
        if extension is None:
            raise ContractError("artifact media type is not supported in a package")
        entry: dict[str, Any] = {
            "id": artifact_id,
            "uri": f"artifacts/{digest}.{extension}",
            "sha256": digest,
            "byte_length": len(data),
            "media_type": media_type,
            "role": role,
            "sensitivity": sensitivity,
        }
        existing = self.entries.get(artifact_id)
        if existing is not None:
            if existing["media_type"] != media_type:
                raise ContractError("identical bytes were registered under two media types")
            self.roles.setdefault(artifact_id, set()).add(role)
            if ROLE_PRIORITY.index(role) < ROLE_PRIORITY.index(str(existing["role"])):
                existing["role"] = role
            if SENSITIVITY_PRIORITY.index(sensitivity) < SENSITIVITY_PRIORITY.index(
                str(existing["sensitivity"])
            ):
                existing["sensitivity"] = sensitivity
            return artifact_id
        self.entries[artifact_id] = entry
        self.payloads[artifact_id] = data
        self.roles[artifact_id] = {role}
        return artifact_id

    def as_list(self) -> list[dict[str, Any]]:
        return [self.entries[key] for key in sorted(self.entries)]


@dataclass(frozen=True)
class BuiltProject:
    """A CIR project document plus the artifact bytes it references."""

    document: dict[str, Any]
    artifacts: ArtifactTable

    @property
    def payload_bytes(self) -> bytes:
        return canonical_bytes(self.document)

    @property
    def payload_sha256(self) -> str:
        return sha256_hex(self.payload_bytes)

    @property
    def revision_id(self) -> str:
        return str(self.document["revision_id"])


def build_project(
    *,
    project_id: str,
    revision_id: str,
    created_at: str,
    source_png: bytes,
    normalized_png: bytes,
    synthesis: Synthesis,
    decomposition: Decomposition,
    rig: Rig,
    registries: Registries,
    canvas_width: int,
    canvas_height: int,
    root_seed: str,
    parent_revision_id: str | None = None,
) -> BuiltProject:
    """Assemble a schema-valid, self-consistent CIR v0.2 project."""
    require_entity_id(project_id, label="project id")
    require_entity_id(revision_id, label="revision id")

    artifacts = ArtifactTable()
    source_id = artifacts.add(
        source_png,
        media_type="image/png",
        role="source",
        sensitivity="user_content",
    )
    source_digest = source_id.split(":", 1)[1]
    normalized_digest = sha256_hex(normalized_png)

    def deterministic_provenance(
        entity_id: str,
        stage_id: str,
        producer_id: str,
        version: str,
        inputs: list[str],
    ) -> dict[str, Any]:
        return {
            "id": entity_id,
            "producer_kind": "deterministic",
            "stage_id": stage_id,
            "implementation_version": f"{producer_id}/{version}",
            "config_sha256": sha256_hex(f"{producer_id}/{version}".encode()),
            "seed_u64": derive_seed(root_seed, stage_id),
            "input_sha256": inputs,
        }

    if decomposition.producer_kind != "deterministic":
        # A model-backed proposer must supply immutable model identity, weights
        # digest and a rights-register record before its provenance is legal.
        raise ContractError("model-backed decomposition provenance is not yet approved")
    if synthesis.producer_kind != "deterministic":
        raise ContractError("model-backed completion provenance is not yet approved")

    provenance: list[dict[str, Any]] = [
        deterministic_provenance(
            "provenance.ingest",
            "stage.ingest-scan-normalize",
            "oneclick2d.intake.normalize",
            "0.1.0",
            [source_digest],
        ),
        deterministic_provenance(
            "provenance.decompose",
            "stage.decompose",
            decomposition.proposer_id,
            decomposition.proposer_version,
            [normalized_digest],
        ),
        deterministic_provenance(
            "provenance.complete",
            "stage.plan-and-bounded-complete",
            synthesis.filler_id,
            synthesis.filler_version,
            [normalized_digest],
        ),
        deterministic_provenance(
            "provenance.rig",
            "stage.mesh-and-minimal-rig",
            "oneclick2d.rig.deterministic-grid",
            "0.1.0",
            [normalized_digest],
        ),
    ]

    layers: list[dict[str, Any]] = []
    confidence_facts: list[dict[str, Any]] = []
    generated_regions: list[dict[str, Any]] = []

    for item in synthesis.layers:
        layer = item.layer
        texture_id = artifacts.add(
            item.texture.to_png(),
            media_type="image/png",
            role="layer_texture",
            sensitivity="derived_user_content",
        )
        mask_id = artifacts.add(
            item.visible_mask.to_png(),
            media_type="image/png",
            role="visible_mask",
            sensitivity="derived_user_content",
        )
        layers.append(
            {
                "id": layer.layer_id,
                "display_name": layer.display_name,
                "slot_id": layer.slot_id,
                "side": layer.side,
                "texture_artifact_id": texture_id,
                "bounds": layer.bounds.as_cir(),
                "visible": True,
                "opacity": 1.0,
                "blend_mode": "normal",
                "draw_order": layer.draw_order,
            }
        )
        confidence_facts.append(
            {
                "id": f"confidence.{layer.layer_id}",
                "target_id": layer.layer_id,
                # No calibration dataset exists before Gate F, so a score would
                # be fabricated. ``unavailable`` is the honest value.
                "score": "unavailable",
                "calibration_dataset_id": "unavailable",
                "calibration_method": "none",
                "calibration_version": "0.0.0",
                "threshold_band": "unavailable",
                "evidence_artifact_ids": [mask_id],
                "provenance_id": "provenance.decompose",
            }
        )

        if item.generated is not None:
            region = item.generated
            region_mask_id = artifacts.add(
                region.mask.to_png(),
                media_type="image/png",
                role="generated_mask",
                sensitivity="derived_user_content",
            )
            confidence_facts.append(
                {
                    "id": f"confidence.{region.region_id}",
                    "target_id": region.region_id,
                    "score": "unavailable",
                    "calibration_dataset_id": "unavailable",
                    "calibration_method": "none",
                    "calibration_version": "0.0.0",
                    "threshold_band": "unavailable",
                    "evidence_artifact_ids": [region_mask_id],
                    "provenance_id": "provenance.complete",
                }
            )
            generated_regions.append(
                {
                    "id": region.region_id,
                    "owner_layer_id": region.owner_layer_id,
                    "mask_artifact_id": region_mask_id,
                    "reveal_bounds": region.reveal_bounds.as_cir(),
                    "feather_width_px": float(region.feather_width_px),
                    "confidence_fact_id": f"confidence.{region.region_id}",
                    "provenance_id": "provenance.complete",
                }
            )

    meshes: list[dict[str, Any]] = []
    for rig_mesh in rig.meshes:
        mesh = rig_mesh.mesh
        vertex_payload = encode_vertices(mesh.vertices)
        index_payload, index_format = encode_indices(mesh.triangles, mesh.vertex_count)
        vertex_id = artifacts.add(
            vertex_payload,
            media_type="application/octet-stream",
            role="mesh_vertices",
            sensitivity="derived_user_content",
        )
        index_id = artifacts.add(
            index_payload,
            media_type="application/octet-stream",
            role="mesh_indices",
            sensitivity="derived_user_content",
        )
        meshes.append(
            {
                "id": rig_mesh.mesh_id,
                "layer_id": rig_mesh.layer_id,
                "vertex_payload_artifact_id": vertex_id,
                "vertex_payload_format": "oc2d.mesh.xyuv.f32le.v1",
                "index_payload_artifact_id": index_id,
                "index_payload_format": index_format,
                "vertex_count": mesh.vertex_count,
                "triangle_count": mesh.triangle_count,
                "winding": "clockwise",
            }
        )

    bindings: list[dict[str, Any]] = []
    mesh_vertex_counts = {item.mesh_id: item.mesh.vertex_count for item in rig.meshes}
    for binding in sorted(rig.bindings, key=lambda entry: (entry.parameter_id, entry.binding_id)):
        samples: list[dict[str, Any]] = []
        for sample in binding.samples:
            delta_payload = encode_deltas(sample.deltas, mesh_vertex_counts[binding.target_mesh_id])
            delta_id = artifacts.add(
                delta_payload,
                media_type="application/octet-stream",
                role="mesh_delta",
                sensitivity="derived_user_content",
            )
            samples.append(
                {
                    "parameter_value": sample.parameter_value,
                    "delta_artifact_id": delta_id,
                    "delta_format": "oc2d.delta.xy.f32le.v1",
                }
            )
        bindings.append(
            {
                "id": binding.binding_id,
                "parameter_id": binding.parameter_id,
                "target_mesh_id": binding.target_mesh_id,
                "samples": samples,
                "interpolation": "linear",
                "extrapolation": "clamp",
            }
        )

    document: dict[str, Any] = {
        "format": CIR_FORMAT,
        "format_version": CIR_FORMAT_VERSION,
        "project_id": project_id,
        "revision_id": revision_id,
        "created_at": created_at,
        "canvas": {
            "width": canvas_width,
            "height": canvas_height,
            "image_origin": "top-left",
            "color_space": "srgb",
            "alpha_mode": "straight",
        },
        "ontology_registry_version": registries.ontology.version,
        "ontology_completion": list(decomposition.completion),
        "reason_code_registry": registries.reason_codes.as_reference(),
        "parameter_registry": registries.parameters.as_reference(),
        "artifacts": artifacts.as_list(),
        "confidence_facts": confidence_facts,
        "layers": layers,
        "generated_regions": generated_regions,
        "meshes": meshes,
        "parameters": [spec.as_cir() for spec in rig.parameters],
        "bindings": bindings,
        "provenance": provenance,
        "user_overrides": [],
        "extensions": {},
    }
    if parent_revision_id is not None:
        document["parent_revision_id"] = require_entity_id(
            parent_revision_id, label="parent revision id"
        )

    project_validator().check(document, label="CIR project")
    return BuiltProject(document=document, artifacts=artifacts)
