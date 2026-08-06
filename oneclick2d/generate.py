"""End-to-end generation: upload in, verified dual output out.

Wires the stage DAG from ``docs/ARCHITECTURE.md`` §7 into one callable so the
whole product path has a single entry point. Each stage keeps its own identity,
seed, budget and typed outcome; this module only sequences them and carries
state forward.

A blocking suitability decision or a blocking validation finding stops the run
with a stable reason code instead of publishing a degraded result.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from .cir import build_project
from .errors import SuitabilityBlocked, ValidationBlocked
from .export.release import DualOutputRelease, publish_dual_output
from .pipeline.context import CancellationToken, derive_seed, format_seed
from .registries import Registries, load_registries
from .render import RenderLayer, render_layers
from .stages.decompose import SemanticProposer, decompose
from .stages.intake import DEFAULT_ENVELOPE, DimensionEnvelope, normalize_upload
from .stages.rig import build_rig
from .stages.suitability import Decision, evaluate_suitability
from .stages.synthesize import CompletionFiller, compose_neutral, synthesize
from .strict_json import canonical_bytes, sha256_hex

VALIDATION_REPORT_FORMAT: Final[str] = "oneclick2d.validation-report"
VALIDATION_REPORT_VERSION: Final[str] = "0.2.0"
RUN_MANIFEST_FORMAT: Final[str] = "oneclick2d.run-manifest"
RUN_MANIFEST_VERSION: Final[str] = "0.2.0"
APPLICATION_VERSION: Final[str] = "0.1.0"


@dataclass(frozen=True)
class GenerationResult:
    """Everything one successful run produced."""

    release: DualOutputRelease
    project_document: dict[str, Any]
    validation_report: dict[str, Any]
    run_manifest: dict[str, Any]
    suitability: dict[str, Any]
    neutral_png: bytes
    preview_png: bytes


def generate(
    *,
    upload: bytes,
    declared_media_type: str,
    account_id: str,
    project_id: str,
    revision_id: str,
    run_id: str,
    release_id: str,
    created_at: str,
    root_seed: int = 0,
    registries: Registries | None = None,
    proposer: SemanticProposer | None = None,
    filler: CompletionFiller | None = None,
    cancellation: CancellationToken | None = None,
    workspace: Path | None = None,
    envelope: DimensionEnvelope = DEFAULT_ENVELOPE,
) -> GenerationResult:
    """Run the whole product path for one upload."""
    from .validation import validate_project

    token = cancellation or CancellationToken()
    bound_registries = registries or load_registries()
    seed = format_seed(root_seed)

    # INGEST_SCAN_NORMALIZE
    token.checkpoint()
    normalized = normalize_upload(upload, declared_media_type, envelope=envelope)
    canvas_width, canvas_height = normalized.image.size

    # VALIDATE (suitability policy)
    token.checkpoint()
    suitability = evaluate_suitability(normalized.image)
    if suitability.decision is Decision.BLOCK:
        raise SuitabilityBlocked(
            "suitability policy blocked the upload", reason_code="INPUT_UNSUPPORTED"
        )

    # DECOMPOSE
    token.checkpoint()
    decomposition = decompose(
        normalized.image, suitability.subject_mask, bound_registries, proposer
    )

    # PLAN_AND_BOUNDED_COMPLETE + SYNTHESIZE_LAYERS
    token.checkpoint()
    synthesis = synthesize(
        normalized.image,
        suitability.subject_mask,
        decomposition,
        seed=derive_seed(seed, "PLAN_AND_BOUNDED_COMPLETE"),
        config_digest=sha256_hex(b"completion/0.1.0"),
        source_id=f"sha256:{sha256_hex(upload)}",
        filler=filler,
    )

    # MESH_AND_MINIMAL_RIG
    token.checkpoint()
    rig = build_rig(synthesis, bound_registries, canvas_width, canvas_height)

    built = build_project(
        project_id=project_id,
        revision_id=revision_id,
        created_at=created_at,
        source_png=upload if declared_media_type == "image/png" else normalized.normalized_png,
        normalized_png=normalized.normalized_png,
        synthesis=synthesis,
        decomposition=decomposition,
        rig=rig,
        registries=bound_registries,
        canvas_width=canvas_width,
        canvas_height=canvas_height,
        root_seed=seed,
    )

    # VERIFY_PROJECT
    token.checkpoint()
    report = validate_project(built.document, built.artifacts.payloads, bound_registries)
    if not report.export_ready:
        raise ValidationBlocked(
            "whole-project validation produced a blocking finding",
            reason_code="EXPORT_VERIFICATION_FAILED",
        )

    # COMPILE_PREVIEW
    token.checkpoint()
    neutral = compose_neutral(canvas_width, canvas_height, synthesis)
    preview = render_layers(
        canvas_width,
        canvas_height,
        [
            RenderLayer(
                layer_id=item.layer.layer_id,
                texture=item.texture,
                mesh=rig.mesh_for(item.layer.layer_id).mesh,
                draw_order=item.layer.draw_order,
            )
            for item in synthesis.layers
        ],
    )

    validation_report = {
        "format": VALIDATION_REPORT_FORMAT,
        "format_version": VALIDATION_REPORT_VERSION,
        "report_id": f"report.{revision_id.replace('revision.', '')}",
        "project_revision_id": revision_id,
        "project_payload_sha256": built.payload_sha256,
        "policy_version": APPLICATION_VERSION,
        "status": report.status,
        "findings": [finding.as_report() for finding in report.findings],
        "acknowledgments": [],
        "export_readiness": {"oc2d": True, "layered_psd": True},
        "checks": report.checks,
    }
    run_manifest = {
        "format": RUN_MANIFEST_FORMAT,
        "format_version": RUN_MANIFEST_VERSION,
        "run_id": run_id,
        "project_revision_id": revision_id,
        "project_payload_sha256": built.payload_sha256,
        "root_seed_u64": seed,
        "terminal_status": "succeeded",
        "application_version": APPLICATION_VERSION,
        "source_sha256": sha256_hex(upload),
        "normalized_sha256": normalized.normalized_sha256,
        "suitability": suitability.as_report(),
        "stages": [
            {
                "stage_type": stage_type,
                "producer_kind": producer_kind,
                "seed_u64": derive_seed(seed, stage_type),
            }
            for stage_type, producer_kind in (
                ("INGEST_SCAN_NORMALIZE", "deterministic"),
                ("VALIDATE", "deterministic"),
                ("DECOMPOSE", decomposition.producer_kind),
                ("PLAN_AND_BOUNDED_COMPLETE", synthesis.producer_kind),
                ("SYNTHESIZE_LAYERS", "deterministic"),
                ("MESH_AND_MINIMAL_RIG", "deterministic"),
                ("VERIFY_PROJECT", "deterministic"),
                ("COMPILE_PREVIEW", "deterministic"),
                ("EXPORT_OC2D", "deterministic"),
                ("EXPORT_PSD", "deterministic"),
                ("VERIFY_EXPORTS", "deterministic"),
            )
        ],
    }

    # EXPORT_OC2D + EXPORT_PSD + VERIFY_EXPORTS
    token.checkpoint()
    preview_png = preview.to_png()
    release = publish_dual_output(
        release_id=release_id,
        account_id=account_id,
        project_id=project_id,
        document=built.document,
        payloads=built.artifacts.payloads,
        validation_report=validation_report,
        run_manifest=run_manifest,
        registry_snapshots={
            "ontology": bound_registries.ontology.canonical,
            "parameters": bound_registries.parameters.canonical,
            "reason-codes": bound_registries.reason_codes.canonical,
        },
        neutral=neutral,
        created_at=created_at,
        preview_png=preview_png,
    )

    if workspace is not None:
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / f"{project_id}.{revision_id}.oc2d").write_bytes(release.package_bytes)
        (workspace / f"{project_id}.{revision_id}.psd").write_bytes(release.psd_bytes)
        (workspace / f"{release_id}.release.json").write_bytes(canonical_bytes(release.record))

    return GenerationResult(
        release=release,
        project_document=built.document,
        validation_report=validation_report,
        run_manifest=run_manifest,
        suitability=suitability.as_report(),
        neutral_png=neutral.to_png(),
        preview_png=preview_png,
    )
