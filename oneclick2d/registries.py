"""Immutable registry snapshots for ontology, parameters and reason codes.

The CIR references each registry by ID, semantic version and SHA-256
(``docs/CIR_SPEC.md`` §8). Registries are authored in ``registries/`` as YAML for
review and mirrored to canonical JSON, which is the digested form: digests are
always taken over RFC 8785 canonical bytes so a reader can resolve every
reference offline without a YAML parser in the trust path.

``scripts/check_registry_mirrors.py`` proves the JSON mirrors match their YAML
sources, so the two never drift.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from .errors import ContractError
from .strict_json import canonical_bytes, loads_strict, sha256_hex

REGISTRY_ROOT: Final[Path] = Path(__file__).resolve().parents[1] / "registries"
ONTOLOGY_FILE: Final[str] = "ontology-v0.1.json"
PARAMETERS_FILE: Final[str] = "parameters-v0.1.json"
REASON_CODES_FILE: Final[str] = "reason-codes.json"
CANDIDATE_CAPABILITIES: Final[frozenset[str]] = frozenset(
    {"candidate_mandatory", "candidate_optional", "research_optional"}
)
COMPLETION_STATUSES: Final[tuple[str, ...]] = ("PRESENT", "NOT_APPLICABLE", "LOW_CONFIDENCE")
CARDINALITIES: Final[frozenset[str]] = frozenset(
    {"exactly_one", "zero_or_one", "zero_or_many", "one_or_many"}
)
APPLICABILITIES: Final[frozenset[str]] = frozenset({"required", "optional", "conditional"})


@dataclass(frozen=True)
class RegistrySnapshot:
    """An immutable registry payload with its canonical digest."""

    registry_id: str
    version: str
    payload: dict[str, Any]

    @property
    def canonical(self) -> bytes:
        return canonical_bytes(self.payload)

    @property
    def sha256(self) -> str:
        return sha256_hex(self.canonical)

    def as_reference(self) -> dict[str, str]:
        return {"id": self.registry_id, "version": self.version, "sha256": self.sha256}


def _load(filename: str) -> dict[str, Any]:
    path = REGISTRY_ROOT / filename
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ContractError("registry file could not be read") from exc
    payload = loads_strict(data)
    if not isinstance(payload, dict):
        raise ContractError("registry payload must be a JSON object")
    for field in ("registry", "version"):
        if not isinstance(payload.get(field), str):
            raise ContractError("registry payload is missing its identity fields")
    return payload


def load_ontology_registry() -> RegistrySnapshot:
    """Load and validate the ontology slot registry snapshot."""
    payload = _load(ONTOLOGY_FILE)
    if payload.get("side_convention") != "character-anatomical":
        raise ContractError("ontology registry must use character-anatomical sides")
    if list(payload.get("completion_statuses") or ()) != list(COMPLETION_STATUSES):
        raise ContractError("ontology completion statuses do not match the specification")
    slots = payload.get("slots")
    if not isinstance(slots, list) or not slots:
        raise ContractError("ontology registry declares no slots")
    identifiers: set[str] = set()
    for slot in slots:
        if not isinstance(slot, dict) or not isinstance(slot.get("id"), str):
            raise ContractError("ontology slot entry is malformed")
        if slot["id"] in identifiers:
            raise ContractError("ontology slot id is duplicated")
        identifiers.add(slot["id"])
        if slot.get("cardinality") not in CARDINALITIES:
            raise ContractError("ontology slot cardinality is unsupported")
        if slot.get("applicability") not in APPLICABILITIES:
            raise ContractError("ontology slot applicability is unsupported")
        side = slot.get("side")
        if side is not None and side not in ("left", "right", "center"):
            raise ContractError("ontology slot side is unsupported")
    # Parents must resolve and the slot graph must be acyclic.
    for slot in slots:
        parent = slot.get("parent")
        if parent is not None:
            if not isinstance(parent, str) or parent not in identifiers:
                raise ContractError("ontology slot parent does not resolve")
    by_id = {str(slot["id"]): slot for slot in slots}
    for slot in slots:
        seen: set[str] = set()
        cursor: Any = slot
        while cursor is not None:
            slot_id = str(cursor["id"])
            if slot_id in seen:
                raise ContractError("ontology slot graph contains a cycle")
            seen.add(slot_id)
            parent = cursor.get("parent")
            cursor = by_id.get(str(parent)) if parent is not None else None
    return RegistrySnapshot(str(payload["registry"]), str(payload["version"]), payload)


def load_parameter_registry() -> RegistrySnapshot:
    """Load and validate the parameter capability registry snapshot."""
    payload = _load(PARAMETERS_FILE)
    evaluation = payload.get("evaluation")
    if not isinstance(evaluation, dict):
        raise ContractError("parameter registry declares no evaluation rules")
    if evaluation.get("interpolation_1d") != "linear" or evaluation.get("extrapolation") != "clamp":
        raise ContractError("parameter registry evaluation rules do not match the specification")
    parameters = payload.get("parameters")
    if not isinstance(parameters, list) or not parameters:
        raise ContractError("parameter registry declares no parameters")
    identifiers: set[str] = set()
    for parameter in parameters:
        if not isinstance(parameter, dict) or not isinstance(parameter.get("id"), str):
            raise ContractError("parameter entry is malformed")
        if parameter["id"] in identifiers:
            raise ContractError("parameter id is duplicated")
        identifiers.add(parameter["id"])
        if parameter.get("capability") not in CANDIDATE_CAPABILITIES:
            raise ContractError("parameter capability is not a Gate F candidate state")
        template_range = parameter.get("template_range")
        if not isinstance(template_range, list) or len(template_range) != 2:
            raise ContractError("parameter template range is malformed")
        low, high = template_range
        neutral = parameter.get("neutral")
        for value in (low, high, neutral):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ContractError("parameter range values must be numbers")
        if not float(low) < float(high):
            raise ContractError("parameter template range must be ordered")
        if not float(low) <= float(neutral) <= float(high):
            raise ContractError("parameter neutral value lies outside its template range")
        roles = parameter.get("roles")
        if not isinstance(roles, list) or not roles:
            raise ContractError("parameter declares no roles")
        for role in roles:
            if role not in ("manual", "tracking"):
                raise ContractError("parameter role is unsupported")
        if "manual" not in roles:
            # FR-013 requires manual coverage of every enabled capability.
            raise ContractError("every parameter must remain manually operable")
    return RegistrySnapshot(str(payload["registry"]), str(payload["version"]), payload)


def load_reason_code_registry() -> RegistrySnapshot:
    """Load and validate the reason-code registry snapshot."""
    payload = _load(REASON_CODES_FILE)
    causes = payload.get("operational_causes")
    findings = payload.get("quality_findings")
    if not isinstance(causes, list) or not isinstance(findings, list):
        raise ContractError("reason code registry is missing its two axes")
    identifiers: set[str] = set()
    for entry in list(causes) + list(findings):
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
            raise ContractError("reason code entry is malformed")
        if entry["id"] in identifiers:
            raise ContractError("reason code id is duplicated across axes")
        identifiers.add(entry["id"])
    for entry in findings:
        if entry.get("severity") not in ("info", "review", "blocking"):
            raise ContractError("quality finding severity is unsupported")
    return RegistrySnapshot(str(payload["registry"]), str(payload["version"]), payload)


@dataclass(frozen=True)
class Registries:
    """The three registry snapshots a published project must resolve."""

    ontology: RegistrySnapshot
    parameters: RegistrySnapshot
    reason_codes: RegistrySnapshot

    @property
    def ontology_slots(self) -> tuple[dict[str, Any], ...]:
        return tuple(dict(slot) for slot in self.ontology.payload["slots"])

    @property
    def ontology_slot_ids(self) -> tuple[str, ...]:
        return tuple(str(slot["id"]) for slot in self.ontology.payload["slots"])

    def ontology_slot(self, slot_id: str) -> dict[str, Any]:
        for slot in self.ontology.payload["slots"]:
            if slot["id"] == slot_id:
                return dict(slot)
        raise ContractError("ontology slot does not resolve")

    def required_slot_ids(self) -> tuple[str, ...]:
        return tuple(
            str(slot["id"])
            for slot in self.ontology.payload["slots"]
            if slot.get("applicability") == "required"
        )

    @property
    def parameters_list(self) -> tuple[dict[str, Any], ...]:
        return tuple(dict(item) for item in self.parameters.payload["parameters"])

    @property
    def parameter_ids(self) -> tuple[str, ...]:
        return tuple(str(item["id"]) for item in self.parameters.payload["parameters"])

    def parameter(self, parameter_id: str) -> dict[str, Any]:
        for item in self.parameters.payload["parameters"]:
            if item["id"] == parameter_id:
                return dict(item)
        raise ContractError("parameter does not resolve")

    def mandatory_parameter_ids(self) -> tuple[str, ...]:
        return tuple(
            str(item["id"])
            for item in self.parameters.payload["parameters"]
            if item.get("capability") == "candidate_mandatory"
        )

    def reason_code_ids(self) -> frozenset[str]:
        payload = self.reason_codes.payload
        return frozenset(
            str(entry["id"])
            for key in ("operational_causes", "quality_findings")
            for entry in payload[key]
        )

    def quality_finding(self, code: str) -> dict[str, Any]:
        for entry in self.reason_codes.payload["quality_findings"]:
            if entry["id"] == code:
                return dict(entry)
        raise ContractError("quality finding code does not resolve")

    def require_reason_code(self, code: str) -> str:
        if code not in self.reason_code_ids():
            raise ContractError("reason code does not resolve to the bound registry")
        return code


def load_registries() -> Registries:
    """Load and validate all three registry snapshots."""
    return Registries(
        ontology=load_ontology_registry(),
        parameters=load_parameter_registry(),
        reason_codes=load_reason_code_registry(),
    )
