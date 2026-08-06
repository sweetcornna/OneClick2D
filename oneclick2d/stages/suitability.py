"""``VALIDATE``: deterministic suitability policy (FR-003, FR-004).

Produces ``PASS``, ``PASS_WITH_WARNINGS`` or ``BLOCK``. Every result carries a
stable code, a confidence fact or an explicit ``unavailable``, the consequence
and exactly one approved next step.

The measurements here are deterministic image statistics, not a model. A model
may later *propose* suitability, but this policy stays the decider, per the
"ML proposes; deterministic code constrains" rule. Because no calibration
dataset exists before Gate F, scores are reported as ``unavailable`` rather than
inventing a number that would look calibrated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Final

from ..raster.image import Image, Mask

VISIBLE_ALPHA_THRESHOLD: Final[int] = 31
MIN_SUBJECT_COVERAGE: Final[float] = 0.10
MAX_SUBJECT_COVERAGE: Final[float] = 0.95
MIN_UPPER_BODY_MARGIN: Final[float] = 0.0
EDGE_CONTACT_TOLERANCE: Final[int] = 2


class Decision(str, Enum):
    PASS = "PASS"
    PASS_WITH_WARNINGS = "PASS_WITH_WARNINGS"
    BLOCK = "BLOCK"


@dataclass(frozen=True)
class Observation:
    """One suitability check with its consequence and single next step."""

    code: str
    severity: str
    confidence: str
    consequence: str
    next_step: str
    measurement: dict[str, Any] = field(default_factory=dict)

    def as_report(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "confidence": self.confidence,
            "consequence": self.consequence,
            "next_step": self.next_step,
            "measurement": dict(self.measurement),
        }


@dataclass(frozen=True)
class SuitabilityReport:
    decision: Decision
    observations: tuple[Observation, ...]
    subject_mask: Mask
    subject_coverage: float
    background_is_transparent: bool

    @property
    def blocking_codes(self) -> tuple[str, ...]:
        return tuple(item.code for item in self.observations if item.severity == "blocking")

    @property
    def review_codes(self) -> tuple[str, ...]:
        return tuple(item.code for item in self.observations if item.severity == "review")

    def as_report(self) -> dict[str, Any]:
        return {
            "decision": self.decision.value,
            "observations": [item.as_report() for item in self.observations],
            "subject_coverage_ratio": round(self.subject_coverage, 6),
            "background_is_transparent": self.background_is_transparent,
        }


def _subject_mask(image: Image) -> tuple[Mask, bool]:
    """Derive the subject coverage mask.

    A transparent-background upload defines its subject by alpha. A fully opaque
    upload has no alpha to read, so the whole canvas is the subject and the
    caller is told the background was not cut out. This distinction is exactly
    the input precondition the Gate F spike learned the hard way: treating an
    opaque background as subject makes coverage-style gates unreachable.
    """
    alpha = image.alpha_mask()
    visible = alpha.count_at_least(VISIBLE_ALPHA_THRESHOLD)
    total = image.width * image.height
    if visible == total:
        return alpha.binarize(-1), False
    return alpha.binarize(VISIBLE_ALPHA_THRESHOLD), True


def evaluate_suitability(image: Image) -> SuitabilityReport:
    """Apply the deterministic suitability policy to a normalized upload."""
    observations: list[Observation] = []
    mask, transparent_background = _subject_mask(image)
    total = image.width * image.height
    covered = mask.count_at_least(0)
    coverage = covered / total if total else 0.0

    if not transparent_background:
        observations.append(
            Observation(
                code="INPUT_BACKGROUND_NOT_SEPARATED",
                severity="review",
                confidence="unavailable",
                consequence="generated_layers_will_not_cover_the_background",
                next_step="replace_input_with_cut_out_subject",
                measurement={"opaque_pixel_ratio": 1.0},
            )
        )

    if coverage < MIN_SUBJECT_COVERAGE:
        observations.append(
            Observation(
                code="INPUT_POSE_OUTSIDE_ENVELOPE",
                severity="blocking",
                confidence="unavailable",
                consequence="subject_too_small_for_semantic_decomposition",
                next_step="replace_input",
                measurement={"subject_coverage_ratio": round(coverage, 6)},
            )
        )
    elif coverage > MAX_SUBJECT_COVERAGE and transparent_background:
        observations.append(
            Observation(
                code="INPUT_POSE_OUTSIDE_ENVELOPE",
                severity="review",
                confidence="unavailable",
                consequence="subject_may_be_cropped_at_the_canvas_edge",
                next_step="inspect_or_replace",
                measurement={"subject_coverage_ratio": round(coverage, 6)},
            )
        )

    bounds = mask.bounds_at_least(0)
    if not bounds.empty:
        touches_top = bounds.y <= EDGE_CONTACT_TOLERANCE
        touches_left = bounds.x <= EDGE_CONTACT_TOLERANCE
        touches_right = bounds.right >= image.width - EDGE_CONTACT_TOLERANCE
        # A half-body portrait is expected to run off the bottom edge; the head
        # running off the top, or the body off both sides, is not.
        if touches_top and transparent_background:
            observations.append(
                Observation(
                    code="INPUT_POSE_OUTSIDE_ENVELOPE",
                    severity="review",
                    confidence="unavailable",
                    consequence="head_may_be_cropped_by_the_canvas",
                    next_step="inspect_or_replace",
                    measurement={"subject_top_px": bounds.y},
                )
            )
        if touches_left and touches_right and coverage > 0.85:
            observations.append(
                Observation(
                    code="INPUT_POSE_OUTSIDE_ENVELOPE",
                    severity="review",
                    confidence="unavailable",
                    consequence="composition_may_exceed_the_half_body_envelope",
                    next_step="inspect_or_replace",
                    measurement={"subject_width_px": bounds.width},
                )
            )

    if image.width != image.height:
        ratio = max(image.width, image.height) / min(image.width, image.height)
        if ratio > 2.0:
            observations.append(
                Observation(
                    code="INPUT_POSE_OUTSIDE_ENVELOPE",
                    severity="review",
                    confidence="unavailable",
                    consequence="extreme_aspect_ratio_may_distort_layout_priors",
                    next_step="inspect_or_replace",
                    measurement={"aspect_ratio": round(ratio, 6)},
                )
            )

    observations.append(
        Observation(
            code="RASTER_UNTAGGED_ASSUMED_SRGB",
            severity="info",
            confidence="unavailable",
            consequence="colour_handled_as_srgb",
            next_step="provide_embedded_icc_or_png_srgb",
            measurement={},
        )
    )

    if any(item.severity == "blocking" for item in observations):
        decision = Decision.BLOCK
    elif any(item.severity == "review" for item in observations):
        decision = Decision.PASS_WITH_WARNINGS
    else:
        decision = Decision.PASS

    return SuitabilityReport(
        decision=decision,
        observations=tuple(observations),
        subject_mask=mask,
        subject_coverage=coverage,
        background_is_transparent=transparent_background,
    )
