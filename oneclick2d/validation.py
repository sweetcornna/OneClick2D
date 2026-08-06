"""Whole-project semantic validation (FR-010, ``docs/CIR_SPEC.md`` §11).

Schema validation covers wire shape. This module covers what a schema cannot
express: every reference resolves to the right kind of target, digests match the
bytes actually present, the geometry ABI is well formed, parameter ranges are
ordered, binding samples strictly increase, the neutral composite protects
original pixels within tolerance, and rendering at neutral, per-parameter
extremes, fixed combinations and a seeded trajectory stays finite and stable.

The source-pixel check is deliberately independent of the synthesis stage that
already enforces it by construction: a guarantee this central should be proven by
a second implementation reading the published bytes, not by trusting the producer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final

from .cir import project_validator
from .errors import ContractError
from .geometry import (
    Mesh,
    decode_deltas,
    decode_indices,
    decode_vertices,
    check_mesh,
)
from .raster.image import Image, Mask
from .registries import Registries
from .render import RenderLayer, pose_layers, render_layers
from .strict_json import canonical_bytes, sha256_hex

VISIBLE_ALPHA_THRESHOLD: Final[int] = 31
# A seeded trajectory exercises parameter combinations no single extreme does.
TRAJECTORY_STEPS: Final[int] = 12
# Content addressing unifies identical bytes, so one digest can legitimately
# serve two roles: a layer covering the whole subject has a texture equal to the
# normalized source. The artifact keeps its most specific role, and a reference
# expecting one of these equivalents still resolves. Mask, mesh and delta roles
# are deliberately absent: those must never be interchangeable, because
# mistaking a mask for a texture would silently corrupt the projection.
INTERCHANGEABLE_ROLES: Final[dict[str, frozenset[str]]] = {
    "source": frozenset({"layer_texture"}),
}


@dataclass(frozen=True)
class Finding:
    """One validation finding bound to a registry reason code."""

    code: str
    severity: str
    instance_id: str
    summary: str
    detail: dict[str, Any] = field(default_factory=dict)

    def as_report(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "finding_instance_id": self.instance_id,
            "summary": self.summary,
            "detail": dict(self.detail),
        }


@dataclass(frozen=True)
class ValidationReport:
    status: str
    findings: tuple[Finding, ...]
    project_payload_sha256: str
    checks: dict[str, Any]

    @property
    def blocking(self) -> tuple[Finding, ...]:
        return tuple(item for item in self.findings if item.severity == "blocking")

    @property
    def export_ready(self) -> bool:
        return self.status in ("pass", "pass_with_review")


def _decode_artifacts(document: dict[str, Any], payloads: dict[str, bytes]) -> dict[str, dict[str, Any]]:
    table: dict[str, dict[str, Any]] = {}
    for entry in document["artifacts"]:
        artifact_id = str(entry["id"])
        if artifact_id in table:
            raise ContractError("artifact id is duplicated")
        data = payloads.get(artifact_id)
        if data is None:
            raise ContractError("artifact referenced by the project has no bytes")
        if sha256_hex(data) != entry["sha256"]:
            raise ContractError("artifact digest does not match its bytes")
        if len(data) != int(entry["byte_length"]):
            raise ContractError("artifact byte length does not match its bytes")
        if artifact_id != f"sha256:{entry['sha256']}":
            raise ContractError("artifact id is not the content address of its bytes")
        table[artifact_id] = dict(entry)
    return table


def _check_references(document: dict[str, Any], artifacts: dict[str, dict[str, Any]]) -> list[Finding]:
    findings: list[Finding] = []
    layer_ids = {str(layer["id"]) for layer in document["layers"]}
    mesh_ids = {str(mesh["id"]) for mesh in document["meshes"]}
    parameter_ids = {str(item["id"]) for item in document["parameters"]}
    fact_ids = {str(fact["id"]) for fact in document["confidence_facts"]}
    provenance_ids = {str(item["id"]) for item in document["provenance"]}
    region_ids = {str(region["id"]) for region in document["generated_regions"]}

    for collection, identifiers in (
        ("layers", [str(item["id"]) for item in document["layers"]]),
        ("meshes", [str(item["id"]) for item in document["meshes"]]),
        ("parameters", [str(item["id"]) for item in document["parameters"]]),
        ("bindings", [str(item["id"]) for item in document["bindings"]]),
        ("confidence_facts", [str(item["id"]) for item in document["confidence_facts"]]),
        ("generated_regions", [str(item["id"]) for item in document["generated_regions"]]),
        ("provenance", [str(item["id"]) for item in document["provenance"]]),
    ):
        if len(identifiers) != len(set(identifiers)):
            findings.append(
                Finding(
                    code="PACKAGE_INTEGRITY_FAILED",
                    severity="blocking",
                    instance_id=f"duplicate-id.{collection}",
                    summary=f"{collection} contains duplicate identifiers",
                )
            )

    # Entity kinds must not collide across collections either.
    all_ids = (
        [str(item["id"]) for item in document["layers"]]
        + [str(item["id"]) for item in document["meshes"]]
        + [str(item["id"]) for item in document["bindings"]]
        + [str(item["id"]) for item in document["confidence_facts"]]
        + [str(item["id"]) for item in document["generated_regions"]]
        + [str(item["id"]) for item in document["provenance"]]
    )
    if len(all_ids) != len(set(all_ids)):
        findings.append(
            Finding(
                code="PACKAGE_INTEGRITY_FAILED",
                severity="blocking",
                instance_id="entity-id.cross-kind-collision",
                summary="entity identifiers collide across entity kinds",
            )
        )

    def require_artifact(artifact_id: str, role: str, instance: str) -> None:
        entry = artifacts.get(artifact_id)
        if entry is None:
            findings.append(
                Finding(
                    code="PACKAGE_INTEGRITY_FAILED",
                    severity="blocking",
                    instance_id=instance,
                    summary="reference does not resolve to a declared artifact",
                )
            )
            return
        declared = str(entry["role"])
        if declared != role and role not in INTERCHANGEABLE_ROLES.get(declared, frozenset()):
            findings.append(
                Finding(
                    code="PACKAGE_INTEGRITY_FAILED",
                    severity="blocking",
                    instance_id=instance,
                    summary="reference resolves to an artifact of the wrong role",
                )
            )

    draw_orders: list[int] = []
    for layer in document["layers"]:
        require_artifact(str(layer["texture_artifact_id"]), "layer_texture", f"layer-texture.{layer['id']}")
        draw_orders.append(int(layer["draw_order"]))
    if len(draw_orders) != len(set(draw_orders)):
        findings.append(
            Finding(
                code="PACKAGE_INTEGRITY_FAILED",
                severity="blocking",
                instance_id="layer.draw-order-not-stable",
                summary="draw order must be unique so layer ordering is stable",
            )
        )

    for mesh in document["meshes"]:
        if str(mesh["layer_id"]) not in layer_ids:
            findings.append(
                Finding(
                    code="MESH_INVALID",
                    severity="blocking",
                    instance_id=f"mesh-layer.{mesh['id']}",
                    summary="mesh references an unknown layer",
                )
            )
        require_artifact(str(mesh["vertex_payload_artifact_id"]), "mesh_vertices", f"mesh-vertices.{mesh['id']}")
        require_artifact(str(mesh["index_payload_artifact_id"]), "mesh_indices", f"mesh-indices.{mesh['id']}")

    for region in document["generated_regions"]:
        if str(region["owner_layer_id"]) not in layer_ids:
            findings.append(
                Finding(
                    code="PACKAGE_INTEGRITY_FAILED",
                    severity="blocking",
                    instance_id=f"region-owner.{region['id']}",
                    summary="generated region references an unknown layer",
                )
            )
        require_artifact(str(region["mask_artifact_id"]), "generated_mask", f"region-mask.{region['id']}")
        if str(region["confidence_fact_id"]) not in fact_ids:
            findings.append(
                Finding(
                    code="PACKAGE_INTEGRITY_FAILED",
                    severity="blocking",
                    instance_id=f"region-confidence.{region['id']}",
                    summary="generated region references an unknown confidence fact",
                )
            )
        if str(region["provenance_id"]) not in provenance_ids:
            findings.append(
                Finding(
                    code="PACKAGE_INTEGRITY_FAILED",
                    severity="blocking",
                    instance_id=f"region-provenance.{region['id']}",
                    summary="generated region references unknown provenance",
                )
            )

    for fact in document["confidence_facts"]:
        target = str(fact["target_id"])
        if target not in layer_ids | region_ids | mesh_ids:
            findings.append(
                Finding(
                    code="PACKAGE_INTEGRITY_FAILED",
                    severity="blocking",
                    instance_id=f"confidence-target.{fact['id']}",
                    summary="confidence fact targets an unknown entity",
                )
            )
        if str(fact["provenance_id"]) not in provenance_ids:
            findings.append(
                Finding(
                    code="PACKAGE_INTEGRITY_FAILED",
                    severity="blocking",
                    instance_id=f"confidence-provenance.{fact['id']}",
                    summary="confidence fact references unknown provenance",
                )
            )

    for binding in document["bindings"]:
        if str(binding["parameter_id"]) not in parameter_ids:
            findings.append(
                Finding(
                    code="PARAMETER_RANGE_UNSAFE",
                    severity="blocking",
                    instance_id=f"binding-parameter.{binding['id']}",
                    summary="binding references an unknown parameter",
                )
            )
        if str(binding["target_mesh_id"]) not in mesh_ids:
            findings.append(
                Finding(
                    code="MESH_INVALID",
                    severity="blocking",
                    instance_id=f"binding-mesh.{binding['id']}",
                    summary="binding references an unknown mesh",
                )
            )
        for index, sample in enumerate(binding["samples"]):
            require_artifact(
                str(sample["delta_artifact_id"]),
                "mesh_delta",
                f"binding-delta.{binding['id']}.{index}",
            )

    return findings


def _check_ontology(document: dict[str, Any], registries: Registries) -> list[Finding]:
    findings: list[Finding] = []
    recorded = {str(entry["slot_id"]): entry for entry in document["ontology_completion"]}
    for slot_id in registries.ontology_slot_ids:
        entry = recorded.get(slot_id)
        if entry is None:
            findings.append(
                Finding(
                    code="ONTOLOGY_SLOT_LOW_CONFIDENCE",
                    severity="blocking",
                    instance_id=f"ontology-missing.{slot_id}",
                    summary="ontology slot has no completeness record",
                )
            )
            continue
        if entry["status"] == "LOW_CONFIDENCE":
            findings.append(
                Finding(
                    code="ONTOLOGY_SLOT_LOW_CONFIDENCE",
                    severity="review",
                    instance_id=f"ontology-low-confidence.{slot_id}",
                    summary="ontology slot is recorded as low confidence",
                    detail={"slot_id": slot_id},
                )
            )
        for code in entry["reason_codes"]:
            registries.require_reason_code(str(code))
    extra = set(recorded) - set(registries.ontology_slot_ids)
    for slot_id in sorted(extra):
        findings.append(
            Finding(
                code="PACKAGE_INTEGRITY_FAILED",
                severity="blocking",
                instance_id=f"ontology-unknown.{slot_id}",
                summary="completeness recorded for a slot outside the bound registry",
            )
        )
    return findings


def _check_parameters(document: dict[str, Any], registries: Registries) -> list[Finding]:
    findings: list[Finding] = []
    bound = {str(binding["parameter_id"]) for binding in document["bindings"]}
    for parameter in document["parameters"]:
        parameter_id = str(parameter["id"])
        minimum = float(parameter["minimum"])
        maximum = float(parameter["maximum"])
        default = float(parameter["default"])
        safe_minimum = float(parameter["safe_minimum"])
        safe_maximum = float(parameter["safe_maximum"])
        if not minimum <= safe_minimum <= default <= safe_maximum <= maximum:
            findings.append(
                Finding(
                    code="PARAMETER_RANGE_UNSAFE",
                    severity="blocking",
                    instance_id=f"parameter-range.{parameter_id}",
                    summary="parameter range ordering is invalid",
                )
            )
        registry_entry = registries.parameter(parameter_id)
        low, high = (float(value) for value in registry_entry["template_range"])
        if minimum < low or maximum > high:
            findings.append(
                Finding(
                    code="PARAMETER_RANGE_UNSAFE",
                    severity="blocking",
                    instance_id=f"parameter-registry-range.{parameter_id}",
                    summary="parameter range exceeds the registry template range",
                )
            )
        if float(registry_entry["neutral"]) != default:
            findings.append(
                Finding(
                    code="PARAMETER_RANGE_UNSAFE",
                    severity="blocking",
                    instance_id=f"parameter-neutral.{parameter_id}",
                    summary="parameter default disagrees with the registry neutral value",
                )
            )
        if str(parameter["capability"]) == "candidate_mandatory":
            if not parameter["manual_enabled"]:
                findings.append(
                    Finding(
                        code="PARAMETER_RANGE_UNSAFE",
                        severity="blocking",
                        instance_id=f"parameter-manual.{parameter_id}",
                        summary="mandatory parameter is not manually operable",
                    )
                )
            if parameter_id not in bound:
                findings.append(
                    Finding(
                        code="PARAMETER_RANGE_UNSAFE",
                        severity="blocking",
                        instance_id=f"parameter-unbound.{parameter_id}",
                        summary="mandatory parameter has no binding",
                    )
                )
    return findings


def _load_meshes(
    document: dict[str, Any], payloads: dict[str, bytes]
) -> dict[str, Mesh]:
    meshes: dict[str, Mesh] = {}
    for entry in document["meshes"]:
        vertices = decode_vertices(payloads[str(entry["vertex_payload_artifact_id"])])
        if len(vertices) != int(entry["vertex_count"]):
            raise ContractError("declared vertex count does not match the payload")
        triangles = decode_indices(
            payloads[str(entry["index_payload_artifact_id"])],
            str(entry["index_payload_format"]),
            len(vertices),
        )
        if len(triangles) != int(entry["triangle_count"]):
            raise ContractError("declared triangle count does not match the payload")
        mesh = Mesh(vertices, triangles)
        check_mesh(mesh)
        meshes[str(entry["id"])] = mesh
    return meshes


def _check_source_pixel_protection(
    document: dict[str, Any],
    payloads: dict[str, bytes],
) -> tuple[list[Finding], dict[str, Any]]:
    """Prove the neutral composite protects the original artwork.

    ``docs/CIR_SPEC.md`` §11.4 states the invariant over the *neutral composite*:
    wherever the upload was visible, the composited result must still show the
    original pixels within tolerance. Checking generated masks against
    source-visible coordinates instead would be wrong, because a pixel hidden
    behind a front layer is legitimately generated even though the source had
    content there — that is precisely what motion later reveals.

    This is computed independently from the published bytes: the composite is
    rebuilt from the layer textures in declared draw order and compared to the
    decoded source artifact, so it does not trust the synthesis stage.
    """
    findings: list[Finding] = []
    source_entry = next(
        (entry for entry in document["artifacts"] if entry["role"] == "source"), None
    )
    if source_entry is None:
        return (
            [
                Finding(
                    code="PACKAGE_INTEGRITY_FAILED",
                    severity="blocking",
                    instance_id="source.missing",
                    summary="project declares no source artifact",
                )
            ],
            {},
        )
    source = Image.from_png(payloads[str(source_entry["id"])])
    visible_source = source.alpha_mask().binarize(VISIBLE_ALPHA_THRESHOLD)

    composite = Image(source.width, source.height)
    for layer in sorted(document["layers"], key=lambda item: int(item["draw_order"])):
        if not bool(layer["visible"]):
            continue
        texture = Image.from_png(payloads[str(layer["texture_artifact_id"])])
        if texture.size != source.size:
            findings.append(
                Finding(
                    code="PACKAGE_INTEGRITY_FAILED",
                    severity="blocking",
                    instance_id=f"layer-geometry.{layer['id']}",
                    summary="layer texture geometry does not match the canvas",
                )
            )
            return findings, {}
        composite = composite.composite_over(texture)

    deviating = 0
    worst = 0
    for index, protected in enumerate(visible_source.data):
        if protected == 0:
            continue
        offset = index * 4
        for channel in range(3):
            delta = abs(source.data[offset + channel] - composite.data[offset + channel])
            if delta:
                worst = max(worst, delta)
        if source.data[offset : offset + 3] != composite.data[offset : offset + 3]:
            deviating += 1

    if deviating > 0:
        findings.append(
            Finding(
                code="GENERATED_REGION_IDENTITY_RISK",
                severity="blocking",
                instance_id="neutral-composite-overwrites-source",
                summary="neutral composite does not preserve visible original pixels",
                detail={"deviating_samples": deviating, "worst_channel_delta": worst},
            )
        )

    # Every generated sample must be hidden at neutral: something drawn in front
    # has to cover it, or it is not a hidden-region completion at all.
    order = {str(layer["id"]): int(layer["draw_order"]) for layer in document["layers"]}
    textures = {
        str(layer["id"]): Image.from_png(payloads[str(layer["texture_artifact_id"])])
        for layer in document["layers"]
    }
    for region in document["generated_regions"]:
        mask_image = Image.from_png(payloads[str(region["mask_artifact_id"])])
        generated = Mask(mask_image.width, mask_image.height, bytearray(mask_image.data[0::4]))
        if (generated.width, generated.height) != (source.width, source.height):
            findings.append(
                Finding(
                    code="PACKAGE_INTEGRITY_FAILED",
                    severity="blocking",
                    instance_id=f"region-geometry.{region['id']}",
                    summary="generated mask geometry does not match the canvas",
                )
            )
            continue
        owner_order = order.get(str(region["owner_layer_id"]))
        if owner_order is None:
            continue
        front = Mask(source.width, source.height)
        for layer_id, texture in textures.items():
            if order[layer_id] > owner_order:
                front = front.union(texture.alpha_mask().binarize(VISIBLE_ALPHA_THRESHOLD))
        exposed = sum(
            1
            for index, coverage in enumerate(generated.data)
            if coverage > 0 and front.data[index] == 0 and visible_source.data[index] > 0
        )
        if exposed:
            findings.append(
                Finding(
                    code="GENERATED_REGION_IDENTITY_RISK",
                    severity="blocking",
                    instance_id=f"region-exposed-over-source.{region['id']}",
                    summary="generated coverage is visible at neutral over original artwork",
                    detail={"exposed_samples": exposed},
                )
            )

    return findings, {
        "neutral_composite_deviating_samples": deviating,
        "source_visible_samples": visible_source.count_at_least(0),
    }


def _check_render(
    document: dict[str, Any],
    payloads: dict[str, bytes],
    meshes: dict[str, Mesh],
    registries: Registries,
) -> tuple[list[Finding], dict[str, Any]]:
    """Render neutral, extremes, combinations and a seeded trajectory (FR-010)."""
    findings: list[Finding] = []
    canvas = document["canvas"]
    width, height = int(canvas["width"]), int(canvas["height"])

    layers: list[RenderLayer] = []
    mesh_by_layer = {str(entry["layer_id"]): str(entry["id"]) for entry in document["meshes"]}
    for layer in document["layers"]:
        layer_id = str(layer["id"])
        mesh_id = mesh_by_layer.get(layer_id)
        if mesh_id is None:
            findings.append(
                Finding(
                    code="MESH_INVALID",
                    severity="blocking",
                    instance_id=f"layer-without-mesh.{layer_id}",
                    summary="layer has no mesh and cannot be rendered",
                )
            )
            continue
        layers.append(
            RenderLayer(
                layer_id=layer_id,
                texture=Image.from_png(payloads[str(layer["texture_artifact_id"])]),
                mesh=meshes[mesh_id],
                draw_order=int(layer["draw_order"]),
                opacity=float(layer["opacity"]),
                visible=bool(layer["visible"]),
            )
        )
    if findings:
        return findings, {}

    bindings = [
        (
            str(binding["parameter_id"]),
            str(binding["target_mesh_id"]),
            [
                (
                    float(sample["parameter_value"]),
                    decode_deltas(
                        payloads[str(sample["delta_artifact_id"])],
                        meshes[str(binding["target_mesh_id"])].vertex_count,
                    ),
                )
                for sample in binding["samples"]
            ],
        )
        for binding in document["bindings"]
    ]

    parameters = {str(item["id"]): item for item in document["parameters"]}
    neutral_values = {key: float(item["default"]) for key, item in parameters.items()}

    poses: dict[str, dict[str, float]] = {"neutral": dict(neutral_values)}
    for parameter_id, item in parameters.items():
        for label, key in (("min", "minimum"), ("max", "maximum")):
            values = dict(neutral_values)
            values[parameter_id] = float(item[key])
            poses[f"{parameter_id}.{label}"] = values
    # Fixed combinations: mandatory capabilities driven together.
    mandatory = [key for key in registries.mandatory_parameter_ids() if key in parameters]
    if mandatory:
        low = dict(neutral_values)
        high = dict(neutral_values)
        for parameter_id in mandatory:
            low[parameter_id] = float(parameters[parameter_id]["minimum"])
            high[parameter_id] = float(parameters[parameter_id]["maximum"])
        poses["combination.mandatory-min"] = low
        poses["combination.mandatory-max"] = high
    # Seeded trajectory: a deterministic sweep through the safe range.
    for step in range(TRAJECTORY_STEPS):
        values = dict(neutral_values)
        phase = step / max(1, TRAJECTORY_STEPS - 1)
        for parameter_id in mandatory:
            item = parameters[parameter_id]
            low_value = float(item["safe_minimum"])
            high_value = float(item["safe_maximum"])
            values[parameter_id] = low_value + (high_value - low_value) * phase
        poses[f"trajectory.{step:02d}"] = values

    coverage: dict[str, int] = {}
    for label, values in poses.items():
        posed = pose_layers(layers, bindings, values)
        for layer in posed:
            for vertex in layer.mesh.vertices:
                if not (-1e6 < vertex.x < 1e6 and -1e6 < vertex.y < 1e6):
                    findings.append(
                        Finding(
                            code="MESH_INVALID",
                            severity="blocking",
                            instance_id=f"pose-nonfinite.{label}.{layer.layer_id}",
                            summary="posed vertex left the representable range",
                        )
                    )
            try:
                check_mesh(layer.mesh)
            except ContractError as exc:
                # A pose that inverts or degenerates a triangle would tear or
                # flip the artwork; that is a blocking geometry defect.
                findings.append(
                    Finding(
                        code="MESH_INVALID",
                        severity="blocking",
                        instance_id=f"pose-degenerate.{label}.{layer.layer_id}",
                        summary=f"posed mesh is invalid: {exc}",
                    )
                )
        rendered = render_layers(width, height, posed)
        visible = sum(1 for value in rendered.data[3::4] if value > VISIBLE_ALPHA_THRESHOLD)
        coverage[label] = visible
        if visible == 0:
            findings.append(
                Finding(
                    code="MESH_INVALID",
                    severity="blocking",
                    instance_id=f"pose-empty.{label}",
                    summary="pose renders nothing visible",
                )
            )

    neutral_coverage = coverage.get("neutral", 0)
    for label, visible in coverage.items():
        if neutral_coverage and visible < neutral_coverage * 0.25:
            findings.append(
                Finding(
                    code="MESH_INVALID",
                    severity="review",
                    instance_id=f"pose-coverage-collapse.{label}",
                    summary="pose loses most of the neutral coverage",
                    detail={"neutral": neutral_coverage, "posed": visible},
                )
            )

    return findings, {"pose_count": len(poses), "neutral_visible_samples": neutral_coverage}


def validate_project(
    document: dict[str, Any],
    payloads: dict[str, bytes],
    registries: Registries,
) -> ValidationReport:
    """Validate a project document against schema and semantic invariants."""
    project_validator().check(document, label="CIR project")
    artifacts = _decode_artifacts(document, payloads)

    findings: list[Finding] = []
    findings.extend(_check_references(document, artifacts))
    findings.extend(_check_ontology(document, registries))
    findings.extend(_check_parameters(document, registries))

    checks: dict[str, Any] = {
        "artifact_count": len(artifacts),
        "layer_count": len(document["layers"]),
        "mesh_count": len(document["meshes"]),
        "binding_count": len(document["bindings"]),
    }

    if not any(item.severity == "blocking" for item in findings):
        meshes = _load_meshes(document, payloads)
        source_findings, source_checks = _check_source_pixel_protection(document, payloads)
        findings.extend(source_findings)
        checks.update(source_checks)
        render_findings, render_checks = _check_render(document, payloads, meshes, registries)
        findings.extend(render_findings)
        checks.update(render_checks)

    for finding in findings:
        registries.require_reason_code(finding.code)

    if any(item.severity == "blocking" for item in findings):
        status = "blocked"
    elif any(item.severity == "review" for item in findings):
        status = "pass_with_review"
    else:
        status = "pass"

    return ValidationReport(
        status=status,
        findings=tuple(findings),
        project_payload_sha256=sha256_hex(canonical_bytes(document)),
        checks=checks,
    )
