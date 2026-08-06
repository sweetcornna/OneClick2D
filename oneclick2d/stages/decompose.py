"""``DECOMPOSE``: semantic layer decomposition with explicit ontology completeness.

Every applicable ontology slot records ``PRESENT``, ``NOT_APPLICABLE`` or
``LOW_CONFIDENCE`` with instance IDs, confidence fact IDs, reason codes and
evidence (FR-007). Silent omission is invalid.

This stage is the designated seam for a model: a proposer supplies per-slot masks
and landmarks, and this code decides sides, ontology status, ordering and
topology. The built-in proposer is deterministic — anatomical layout priors
applied to the measured subject mask — so the pipeline is complete and testable
before any weights exist. Its output is honestly reported as
``LOW_CONFIDENCE``: layout priors locate regions, they do not recognise anatomy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final, Protocol

from ..errors import ContractError
from ..raster.image import Bounds, Image, Mask
from ..registries import Registries

CONFIDENCE_UNAVAILABLE: Final[str] = "unavailable"


@dataclass(frozen=True)
class SlotProposal:
    """A proposed instance of one ontology slot."""

    slot_id: str
    side: str
    mask: Mask
    instance_index: int = 0
    score: float | None = None
    evidence: str = "deterministic_layout_prior"


class SemanticProposer(Protocol):
    """Proposes per-slot coverage masks for a normalized subject.

    A model-backed implementation may replace this; deterministic code downstream
    still owns policy, sides, ontology status and topology.
    """

    proposer_id: str
    proposer_version: str
    producer_kind: str

    def propose(self, image: Image, subject: Mask) -> tuple[SlotProposal, ...]:
        """Return slot proposals covering the subject."""


@dataclass(frozen=True)
class SemanticLayer:
    """A decided semantic layer with a resolved slot, side and draw order."""

    layer_id: str
    slot_id: str
    side: str
    display_name: str
    mask: Mask
    bounds: Bounds
    draw_order: int
    status: str
    score: float | None
    evidence: str

    def as_summary(self) -> dict[str, Any]:
        return {
            "layer_id": self.layer_id,
            "slot_id": self.slot_id,
            "side": self.side,
            "draw_order": self.draw_order,
            "status": self.status,
            "bounds": self.bounds.as_cir(),
        }


@dataclass(frozen=True)
class Decomposition:
    """The decided layer set plus explicit completeness for every slot."""

    layers: tuple[SemanticLayer, ...]
    completion: tuple[dict[str, Any], ...]
    proposer_id: str
    proposer_version: str
    producer_kind: str
    notes: tuple[str, ...] = field(default_factory=tuple)

    def layer(self, slot_id: str) -> SemanticLayer | None:
        for item in self.layers:
            if item.slot_id == slot_id:
                return item
        return None


def _region_mask(subject: Mask, bounds: Bounds) -> Mask:
    """Intersect the subject with a rectangular region."""
    out = Mask(subject.width, subject.height)
    for y in range(max(0, bounds.y), min(subject.height, bounds.bottom)):
        row = y * subject.width
        left = max(0, bounds.x)
        right = min(subject.width, bounds.right)
        out.data[row + left : row + right] = subject.data[row + left : row + right]
    return out


class LayoutPriorProposer:
    """Deterministic proposer using anatomical layout priors.

    The priors follow the charter's restricted envelope: a single upright
    front-facing half-body subject. Proportions are taken from the measured
    subject bounding box, so the proposal tracks the actual figure rather than
    fixed canvas fractions.
    """

    proposer_id = "oneclick2d.semantic.layout-prior"
    proposer_version = "0.1.0"
    producer_kind = "deterministic"

    def propose(self, image: Image, subject: Mask) -> tuple[SlotProposal, ...]:
        bounds = subject.bounds_at_least(0)
        if bounds.empty:
            raise ContractError("subject mask is empty; decomposition cannot proceed")

        # Half-body proportions relative to the subject box.
        head_height = bounds.height * 0.42
        head_width = bounds.width * 0.58
        head_x = bounds.x + (bounds.width - head_width) / 2.0
        head_top = float(bounds.y)
        face_top = head_top + head_height * 0.22
        face_height = head_height * 0.62
        face_x = head_x + head_width * 0.16
        face_width = head_width * 0.68

        eye_row_top = face_top + face_height * 0.30
        eye_height = face_height * 0.20
        eye_width = face_width * 0.30
        brow_top = eye_row_top - face_height * 0.16
        brow_height = face_height * 0.12
        mouth_top = face_top + face_height * 0.70
        mouth_height = face_height * 0.18
        mouth_width = face_width * 0.34
        neck_top = head_top + head_height * 0.92
        torso_top = bounds.y + bounds.height * 0.46

        def box(x: float, y: float, width: float, height: float) -> Bounds:
            return Bounds(int(round(x)), int(round(y)), max(1, int(round(width))), max(1, int(round(height))))

        # Character-anatomical sides: the character's left eye appears on the
        # viewer's right, so the left slot takes the higher-x half of the face.
        face_mid = face_x + face_width / 2.0
        proposals = [
            SlotProposal("oc2d.character", "none", subject.copy(), evidence="subject_alpha"),
            SlotProposal(
                "oc2d.hair.back",
                "none",
                _region_mask(subject, box(head_x - head_width * 0.12, head_top, head_width * 1.24, head_height * 1.15)),
            ),
            SlotProposal("oc2d.torso", "none", _region_mask(subject, box(bounds.x, torso_top, bounds.width, bounds.bottom - torso_top))),
            SlotProposal("oc2d.neck", "none", _region_mask(subject, box(face_mid - face_width * 0.30, neck_top, face_width * 0.60, head_height * 0.18))),
            SlotProposal("oc2d.face.base", "center", _region_mask(subject, box(face_x, face_top, face_width, face_height))),
            SlotProposal(
                "oc2d.brow.right",
                "right",
                _region_mask(subject, box(face_x + face_width * 0.10, brow_top, eye_width, brow_height)),
            ),
            SlotProposal(
                "oc2d.brow.left",
                "left",
                _region_mask(subject, box(face_mid + face_width * 0.10, brow_top, eye_width, brow_height)),
            ),
            SlotProposal(
                "oc2d.eye.right",
                "right",
                _region_mask(subject, box(face_x + face_width * 0.10, eye_row_top, eye_width, eye_height)),
            ),
            SlotProposal(
                "oc2d.eye.left",
                "left",
                _region_mask(subject, box(face_mid + face_width * 0.10, eye_row_top, eye_width, eye_height)),
            ),
            SlotProposal(
                "oc2d.mouth",
                "center",
                _region_mask(subject, box(face_mid - mouth_width / 2.0, mouth_top, mouth_width, mouth_height)),
            ),
            SlotProposal(
                "oc2d.hair.front",
                "none",
                _region_mask(subject, box(head_x, head_top, head_width, head_height * 0.34)),
            ),
        ]
        return tuple(proposal for proposal in proposals if proposal.mask.count_at_least(0) > 0)


# Draw order is deterministic policy, not something a proposer may choose.
# Lower values sit further back; the compositor paints in ascending order.
DRAW_ORDER: Final[dict[str, int]] = {
    "oc2d.background": 0,
    "oc2d.accessory.back": 10,
    "oc2d.hair.back": 20,
    "oc2d.character": 30,
    "oc2d.torso": 40,
    "oc2d.clothing": 50,
    "oc2d.neck": 60,
    "oc2d.face.base": 70,
    "oc2d.brow.right": 80,
    "oc2d.brow.left": 81,
    "oc2d.eye.right": 90,
    "oc2d.eye.left": 91,
    "oc2d.mouth": 100,
    "oc2d.hair.side": 110,
    "oc2d.hair.front": 120,
    "oc2d.accessory.front": 130,
}
DISPLAY_NAMES: Final[dict[str, str]] = {
    "oc2d.background": "Background",
    "oc2d.character": "Character Base",
    "oc2d.face.base": "Face Base",
    "oc2d.eye.left": "Eye (character left)",
    "oc2d.eye.right": "Eye (character right)",
    "oc2d.brow.left": "Brow (character left)",
    "oc2d.brow.right": "Brow (character right)",
    "oc2d.mouth": "Mouth",
    "oc2d.hair.front": "Hair Front",
    "oc2d.hair.side": "Hair Side",
    "oc2d.hair.back": "Hair Back",
    "oc2d.neck": "Neck",
    "oc2d.torso": "Torso",
    "oc2d.clothing": "Clothing",
    "oc2d.accessory.front": "Accessory Front",
    "oc2d.accessory.back": "Accessory Back",
}


def decompose(
    image: Image,
    subject: Mask,
    registries: Registries,
    proposer: SemanticProposer | None = None,
) -> Decomposition:
    """Decide semantic layers and record explicit ontology completeness."""
    engine: SemanticProposer = proposer or LayoutPriorProposer()  # type: ignore[assignment]
    proposals = engine.propose(image, subject)

    seen: dict[str, SlotProposal] = {}
    for proposal in proposals:
        slot = registries.ontology_slot(proposal.slot_id)
        expected_side = slot.get("side")
        if expected_side is not None and proposal.side != expected_side:
            raise ContractError("proposed side contradicts the ontology registry")
        if proposal.slot_id in seen:
            raise ContractError("proposer returned duplicate instances for one slot")
        if proposal.mask.count_at_least(0) == 0:
            continue
        seen[proposal.slot_id] = proposal

    layers: list[SemanticLayer] = []
    for slot_id, proposal in seen.items():
        if slot_id not in DRAW_ORDER:
            raise ContractError("ontology slot has no deterministic draw order")
        bounds = proposal.mask.bounds_at_least(0)
        # A deterministic layout prior locates a region; it does not recognise
        # anatomy. Reporting PRESENT here would overstate the evidence.
        status = "PRESENT" if engine.producer_kind == "model_backed" else "LOW_CONFIDENCE"
        layers.append(
            SemanticLayer(
                layer_id=f"layer.{slot_id.replace('oc2d.', '').replace('.', '-')}",
                slot_id=slot_id,
                side=proposal.side,
                display_name=DISPLAY_NAMES.get(slot_id, slot_id),
                mask=proposal.mask,
                bounds=bounds,
                draw_order=DRAW_ORDER[slot_id],
                status=status,
                score=proposal.score,
                evidence=proposal.evidence,
            )
        )
    layers.sort(key=lambda item: item.draw_order)

    completion: list[dict[str, Any]] = []
    notes: list[str] = []
    for slot in registries.ontology_slots:
        slot_id = str(slot["id"])
        layer = next((item for item in layers if item.slot_id == slot_id), None)
        if layer is not None:
            completion.append(
                {
                    "slot_id": slot_id,
                    "status": layer.status,
                    "instance_ids": [layer.layer_id],
                    "confidence_fact_ids": [f"confidence.{layer.layer_id}"],
                    "reason_codes": [] if layer.status == "PRESENT" else ["ONTOLOGY_SLOT_LOW_CONFIDENCE"],
                    "evidence_artifact_ids": [],
                }
            )
            continue
        applicability = str(slot.get("applicability"))
        if applicability == "required":
            # A required slot with no instance is a blocking ontology gap. It is
            # recorded as LOW_CONFIDENCE with a reason code, never omitted.
            completion.append(
                {
                    "slot_id": slot_id,
                    "status": "LOW_CONFIDENCE",
                    "instance_ids": [],
                    "confidence_fact_ids": [],
                    "reason_codes": ["ONTOLOGY_SLOT_LOW_CONFIDENCE"],
                    "evidence_artifact_ids": [],
                }
            )
            notes.append(f"required slot without an instance: {slot_id}")
        else:
            completion.append(
                {
                    "slot_id": slot_id,
                    "status": "NOT_APPLICABLE",
                    "instance_ids": [],
                    "confidence_fact_ids": [],
                    "reason_codes": [],
                    "evidence_artifact_ids": [],
                }
            )

    return Decomposition(
        layers=tuple(layers),
        completion=tuple(completion),
        proposer_id=engine.proposer_id,
        proposer_version=engine.proposer_version,
        producer_kind=engine.producer_kind,
        notes=tuple(notes),
    )
