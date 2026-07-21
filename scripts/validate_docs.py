#!/usr/bin/env python3
"""Validate the contract/documentation initiation baseline without dependencies."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_FILES = (
    "README.md",
    "CLAUDE.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    ".github/pull_request_template.md",
    "docs/index.md",
    "docs/PROJECT_CHARTER.md",
    "docs/PRODUCT_REQUIREMENTS.md",
    "docs/FEASIBILITY_SPIKE_PLAN.md",
    "docs/RESOURCE_AND_CRITICAL_PATH_PLAN.md",
    "docs/MVP_PLAN.md",
    "docs/ARCHITECTURE.md",
    "docs/CIR_SPEC.md",
    "docs/PACKAGE_CONFORMANCE.md",
    "docs/PSD_EXPORT_PROFILE.md",
    "docs/USER_RECOVERY_AND_FALLBACK_UX.md",
    "docs/QUALITY_PLAN.md",
    "docs/EVALUATION.md",
    "docs/DATA_ACQUISITION_AND_RIGHTS_PLAN.md",
    "docs/MEASUREMENT_TELEMETRY.md",
    "docs/CAPACITY_COST_CONTROL.md",
    "docs/PRIVACY_SECURITY.md",
    "docs/privacy/DATA_PROCESSING_INVENTORY.md",
    "docs/legal/THIRD_PARTY_LICENSE_AND_NOTICE_REGISTER.md",
    "docs/legal/LAUNCH_READINESS.md",
    "docs/operations/TAKEDOWN_ABUSE_AND_APPEALS_RUNBOOK.md",
    "docs/RISK_REGISTER.md",
    "docs/DEVELOPMENT_STANDARDS.md",
    "docs/OPEN_DECISIONS.md",
    "docs/RELEASE_CLAIMS_MATRIX.md",
    "docs/adr/0000-template.md",
    "docs/adr/0001-phase-1-product-and-format-boundary.md",
    "docs/gate-records/GATE_0.md",
    "docs/templates/MODEL_CARD.md",
    "docs/templates/DATASET_CARD.md",
    "registries/reason-codes.yaml",
    "registries/ontology-v0.1.yaml",
    "registries/parameters-v0.1.yaml",
    "schemas/cir/v0.2/project.schema.json",
    "schemas/run-manifest/v0.1/run-manifest.schema.json",
    "schemas/validation-report/v0.2/validation-report.schema.json",
    "schemas/release/v0.1/dual-output-release.schema.json",
)
JSON_GLOBS = ("schemas/**/*.json", "examples/**/*.json")
MARKDOWN_GLOBS = ("*.md", "docs/**/*.md", ".github/**/*.md")
INDEX_LINK_TARGETS = (
    "PROJECT_CHARTER.md",
    "PRODUCT_REQUIREMENTS.md",
    "FEASIBILITY_SPIKE_PLAN.md",
    "RESOURCE_AND_CRITICAL_PATH_PLAN.md",
    "MVP_PLAN.md",
    "ARCHITECTURE.md",
    "CIR_SPEC.md",
    "PACKAGE_CONFORMANCE.md",
    "PSD_EXPORT_PROFILE.md",
    "USER_RECOVERY_AND_FALLBACK_UX.md",
    "QUALITY_PLAN.md",
    "EVALUATION.md",
    "DATA_ACQUISITION_AND_RIGHTS_PLAN.md",
    "MEASUREMENT_TELEMETRY.md",
    "CAPACITY_COST_CONTROL.md",
    "PRIVACY_SECURITY.md",
    "RISK_REGISTER.md",
    "OPEN_DECISIONS.md",
    "RELEASE_CLAIMS_MATRIX.md",
    "gate-records/GATE_0.md",
    "templates/MODEL_CARD.md",
    "templates/DATASET_CARD.md",
)
LINK_RE = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
ENTITY_ID_RE = re.compile(r"^[a-z][a-z0-9_.-]{2,127}$")
REQUIREMENT_ID_RE = re.compile(r"^### (FR|NFR|SC|TEL)-\d{3}\b", re.MULTILINE)
REQUIRED_REQUIREMENT_PREFIXES = ("FR", "NFR", "SC", "TEL")
PROHIBITED_BRAND_PHRASES = (
    "live2d generator",
    "live2d-ready",
    "cubism-compatible",
    "cubism compatible",
)


def _json_files() -> list[Path]:
    return sorted({path for pattern in JSON_GLOBS for path in ROOT.glob(pattern)})


def _markdown_files() -> list[Path]:
    return sorted({path for pattern in MARKDOWN_GLOBS for path in ROOT.glob(pattern)})


def load_json_documents() -> tuple[dict[Path, Any], list[str]]:
    documents: dict[Path, Any] = {}
    errors: list[str] = []
    for path in _json_files():
        try:
            documents[path] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            errors.append(f"{path.relative_to(ROOT)}: invalid JSON: {exc}")
    return documents, errors


def validate_required_files() -> list[str]:
    return [f"missing required file: {relative}" for relative in REQUIRED_FILES if not (ROOT / relative).is_file()]


def validate_local_markdown_links() -> list[str]:
    errors: list[str] = []
    for path in _markdown_files():
        text = path.read_text(encoding="utf-8")
        for raw_target in LINK_RE.findall(text):
            target = raw_target.strip().split("#", 1)[0]
            if not target or target.startswith(("http://", "https://", "mailto:")):
                continue
            target_path = (path.parent / target).resolve()
            try:
                target_path.relative_to(ROOT.resolve())
            except ValueError:
                errors.append(f"{path.relative_to(ROOT)}: link escapes repository: {raw_target}")
                continue
            if not target_path.exists():
                errors.append(f"{path.relative_to(ROOT)}: broken local link: {raw_target}")
    return errors


def validate_index_coverage() -> list[str]:
    index_path = ROOT / "docs/index.md"
    if not index_path.is_file():
        return []
    text = index_path.read_text(encoding="utf-8")
    linked_targets = {
        raw_target.strip().split("#", 1)[0]
        for raw_target in LINK_RE.findall(text)
    }
    return [
        f"docs/index.md: missing baseline link: {target}"
        for target in INDEX_LINK_TARGETS
        if target not in linked_targets
    ]


def validate_product_boundary_and_requirements() -> list[str]:
    errors: list[str] = []
    requirements_path = ROOT / "docs/PRODUCT_REQUIREMENTS.md"
    if requirements_path.is_file():
        text = requirements_path.read_text(encoding="utf-8")
        prefixes = {match.group(1) for match in REQUIREMENT_ID_RE.finditer(text)}
        missing = sorted(set(REQUIRED_REQUIREMENT_PREFIXES) - prefixes)
        if missing:
            errors.append(
                "docs/PRODUCT_REQUIREMENTS.md: missing requirement headings for: "
                + ", ".join(missing)
            )

    for path in _markdown_files():
        text = path.read_text(encoding="utf-8").lower()
        for phrase in PROHIBITED_BRAND_PHRASES:
            if phrase in text:
                errors.append(
                    f"{path.relative_to(ROOT)}: prohibited unqualified Phase 1 claim: {phrase}"
                )
    return errors


def validate_schema_metadata(documents: dict[Path, Any]) -> list[str]:
    errors: list[str] = []
    for path, document in documents.items():
        if "schemas" not in path.parts:
            continue
        if not isinstance(document, dict):
            errors.append(f"{path.relative_to(ROOT)}: schema root must be an object")
            continue
        if document.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
            errors.append(f"{path.relative_to(ROOT)}: schema must declare JSON Schema draft 2020-12")
        if not document.get("$id"):
            errors.append(f"{path.relative_to(ROOT)}: schema must declare $id")
        if document.get("type") != "object":
            errors.append(f"{path.relative_to(ROOT)}: schema root type must be object")
    return errors


def validate_minimal_cir(manifest: Any) -> list[str]:
    """Check the legacy disposable v0.1 example without claiming conformance."""
    errors: list[str] = []
    if not isinstance(manifest, dict):
        return ["examples/cir-minimal/manifest.json: root must be an object"]

    required = {
        "format",
        "format_version",
        "project_id",
        "created_at",
        "canvas",
        "artifacts",
        "layers",
        "occlusion_edges",
        "meshes",
        "parameters",
        "deformers",
        "pose_bindings",
        "physics_worlds",
        "provenance",
        "validation",
    }
    missing = sorted(required - manifest.keys())
    if missing:
        errors.append(f"examples/cir-minimal/manifest.json: missing keys: {', '.join(missing)}")
        return errors

    if manifest["format"] != "oneclick2d.cir" or manifest["format_version"] != "0.1.0":
        errors.append("examples/cir-minimal/manifest.json: unexpected CIR format/version")
    if not isinstance(manifest["project_id"], str) or not ENTITY_ID_RE.fullmatch(manifest["project_id"]):
        errors.append("examples/cir-minimal/manifest.json: invalid project_id")

    canvas = manifest.get("canvas")
    if not isinstance(canvas, dict) or canvas.get("image_origin") != "top-left" or canvas.get("color_space") != "srgb" or canvas.get("alpha_mode") != "straight":
        errors.append("examples/cir-minimal/manifest.json: invalid canvas conventions")

    artifact_ids: set[str] = set()
    for index, artifact in enumerate(manifest.get("artifacts", [])):
        if not isinstance(artifact, dict):
            errors.append(f"examples/cir-minimal/manifest.json: artifact {index} must be an object")
            continue
        digest = artifact.get("sha256", "")
        artifact_id = artifact.get("id", "")
        if not SHA256_RE.fullmatch(digest) or artifact_id != f"sha256:{digest}":
            errors.append(f"examples/cir-minimal/manifest.json: artifact {index} has inconsistent digest/id")
        if artifact_id in artifact_ids:
            errors.append(f"examples/cir-minimal/manifest.json: duplicate artifact id {artifact_id}")
        artifact_ids.add(artifact_id)

    layer_ids = _unique_ids(manifest.get("layers", []), "layer", errors)
    mesh_ids = _unique_ids(manifest.get("meshes", []), "mesh", errors)
    parameter_ids = _unique_ids(manifest.get("parameters", []), "parameter", errors)
    deformer_ids = _unique_ids(manifest.get("deformers", []), "deformer", errors)
    _unique_ids(manifest.get("pose_bindings", []), "pose binding", errors)
    _unique_ids(manifest.get("physics_worlds", []), "physics world", errors)

    for layer in manifest.get("layers", []):
        if not isinstance(layer, dict):
            continue
        for field in ("texture_artifact_id", "visible_mask_artifact_id", "generated_mask_artifact_id"):
            reference = layer.get(field)
            if reference is not None and reference not in artifact_ids:
                errors.append(f"examples/cir-minimal/manifest.json: layer {layer.get('id')} has missing {field}")

    for mesh in manifest.get("meshes", []):
        if not isinstance(mesh, dict):
            continue
        if mesh.get("layer_id") not in layer_ids:
            errors.append(f"examples/cir-minimal/manifest.json: mesh {mesh.get('id')} references missing layer")
        for field in ("vertex_payload_artifact_id", "index_payload_artifact_id"):
            if mesh.get(field) not in artifact_ids:
                errors.append(f"examples/cir-minimal/manifest.json: mesh {mesh.get('id')} has missing {field}")
        for deformer_id in mesh.get("deformer_ids", []):
            if deformer_id not in deformer_ids:
                errors.append(f"examples/cir-minimal/manifest.json: mesh {mesh.get('id')} references missing deformer")

    graph: dict[str, set[str]] = {layer_id: set() for layer_id in layer_ids}
    for edge in manifest.get("occlusion_edges", []):
        if not isinstance(edge, dict):
            continue
        behind = edge.get("behind_layer_id")
        front = edge.get("in_front_layer_id")
        if behind not in layer_ids or front not in layer_ids:
            errors.append("examples/cir-minimal/manifest.json: occlusion edge references missing layer")
        elif behind == front:
            errors.append("examples/cir-minimal/manifest.json: self occlusion edge")
        else:
            graph[behind].add(front)
    if _has_cycle(graph):
        errors.append("examples/cir-minimal/manifest.json: occlusion graph contains a cycle")

    for parameter in manifest.get("parameters", []):
        if not isinstance(parameter, dict):
            continue
        low, default, high = parameter.get("minimum"), parameter.get("default"), parameter.get("maximum")
        if not all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in (low, default, high)) or not low <= default <= high:
            errors.append(f"examples/cir-minimal/manifest.json: parameter {parameter.get('id')} range/default invalid")

    for deformer in manifest.get("deformers", []):
        if not isinstance(deformer, dict):
            continue
        parent = deformer.get("parent_deformer_id")
        if parent is not None and parent not in deformer_ids:
            errors.append(f"examples/cir-minimal/manifest.json: deformer {deformer.get('id')} parent missing")
        for target in deformer.get("target_ids", []):
            if target not in mesh_ids and target not in deformer_ids:
                errors.append(f"examples/cir-minimal/manifest.json: deformer {deformer.get('id')} target missing")

    for binding in manifest.get("pose_bindings", []):
        if not isinstance(binding, dict):
            continue
        if binding.get("deformation_artifact_id") not in artifact_ids:
            errors.append(f"examples/cir-minimal/manifest.json: pose binding {binding.get('id')} artifact missing")
        unknown = set(binding.get("parameter_samples", {})) - parameter_ids
        if unknown:
            errors.append(f"examples/cir-minimal/manifest.json: pose binding {binding.get('id')} parameter missing")

    for field in ("provenance", "validation"):
        link = manifest.get(field, {})
        if not isinstance(link, dict) or not SHA256_RE.fullmatch(link.get("sha256", "")):
            errors.append(f"examples/cir-minimal/manifest.json: invalid {field} link")

    return errors


def _unique_ids(items: Any, label: str, errors: list[str]) -> set[str]:
    ids: set[str] = set()
    if not isinstance(items, list):
        errors.append(f"examples/cir-minimal/manifest.json: {label} collection must be an array")
        return ids
    for index, item in enumerate(items):
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not ENTITY_ID_RE.fullmatch(item["id"]):
            errors.append(f"examples/cir-minimal/manifest.json: invalid {label} id at index {index}")
            continue
        if item["id"] in ids:
            errors.append(f"examples/cir-minimal/manifest.json: duplicate {label} id {item['id']}")
        ids.add(item["id"])
    return ids


def _has_cycle(graph: dict[str, set[str]]) -> bool:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> bool:
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        if any(visit(neighbor) for neighbor in sorted(graph[node])):
            return True
        visiting.remove(node)
        visited.add(node)
        return False

    return any(visit(node) for node in sorted(graph))


def run_checks() -> list[str]:
    documents, errors = load_json_documents()
    errors.extend(validate_required_files())
    errors.extend(validate_local_markdown_links())
    errors.extend(validate_index_coverage())
    errors.extend(validate_product_boundary_and_requirements())
    errors.extend(validate_schema_metadata(documents))
    minimal_path = ROOT / "examples/cir-minimal/manifest.json"
    if minimal_path in documents:
        errors.extend(validate_minimal_cir(documents[minimal_path]))
    return sorted(errors)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quiet", action="store_true", help="print only validation failures")
    args = parser.parse_args()
    errors = run_checks()
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    if not args.quiet:
        print(
            "Initiation/documentation lint passed: "
            f"{len(_markdown_files())} Markdown and {len(_json_files())} JSON files checked. "
            "This is not schema/package conformance or feasibility evidence."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
