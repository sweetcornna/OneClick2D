"""``PLAN_AND_BOUNDED_COMPLETE`` + ``SYNTHESIZE_LAYERS`` (FR-008).

Occluded slots need pixels that do not exist in the upload. This stage plans a
bounded completion region per layer, fills it, and records provenance: mask,
motion-reveal envelope, feather width, confidence fact, producer stage, model,
config, seed and source ID.

The hard invariant is the source-pixel guarantee: generated coverage must not
replace visible original pixels outside the feather tolerance. It is enforced
here by construction *and* re-checked independently in
``oneclick2d.validation``, because this is the guarantee the product's honesty
rests on: output is an inspectable draft, never a claim to have recovered hidden
content.

The built-in filler is deterministic edge-extension. It is explicitly not a
generative model; it produces plausible continuation, and its provenance says so.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final, Protocol, Sequence

from ..errors import ContractError
from ..raster.image import Bounds, Image, Mask
from .decompose import Decomposition, SemanticLayer

DEFAULT_FEATHER_PX: Final[int] = 3
DEFAULT_REVEAL_MARGIN_PX: Final[int] = 24
VISIBLE_ALPHA_THRESHOLD: Final[int] = 31
# Slots that can be revealed by motion and therefore need bounded completion
# behind them. Values are the reveal margin in pixels.
REVEAL_MARGINS: Final[dict[str, int]] = {
    "oc2d.eye.left": 10,
    "oc2d.eye.right": 10,
    "oc2d.mouth": 12,
    "oc2d.face.base": 16,
    "oc2d.neck": 20,
    "oc2d.torso": 24,
    "oc2d.hair.back": 28,
    "oc2d.character": 24,
}


class CompletionFiller(Protocol):
    """Fills a bounded region that motion may reveal."""

    filler_id: str
    filler_version: str
    producer_kind: str
    model_id: str | None

    def fill(self, image: Image, known: Mask, target: Mask) -> Image:
        """Return an image whose ``target`` samples are filled."""


class EdgeExtensionFiller:
    """Deterministic completion by nearest-known-sample extension.

    Each unknown sample takes the colour of the nearest known sample, found by a
    multi-source breadth-first sweep from the known boundary. This is a bounded,
    reproducible continuation of existing pixels, not generation of new content.
    """

    filler_id = "oneclick2d.completion.edge-extension"
    filler_version = "0.1.0"
    producer_kind = "deterministic"
    model_id = None

    def fill(self, image: Image, known: Mask, target: Mask) -> Image:
        if (known.width, known.height) != (image.width, image.height):
            raise ContractError("known mask geometry mismatch")
        if (target.width, target.height) != (image.width, image.height):
            raise ContractError("target mask geometry mismatch")
        width, height = image.width, image.height
        out = bytearray(image.data)
        # Seed the sweep with every known sample adjacent to an unknown one.
        frontier: list[int] = []
        source_of: dict[int, int] = {}
        for index in range(width * height):
            if known.data[index] == 0:
                continue
            y, x = divmod(index, width)
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = x + dx, y + dy
                if 0 <= nx < width and 0 <= ny < height:
                    neighbour = ny * width + nx
                    if known.data[neighbour] == 0 and target.data[neighbour] > 0:
                        frontier.append(index)
                        source_of[index] = index
                        break
        while frontier:
            next_frontier: list[int] = []
            for index in frontier:
                origin = source_of[index]
                y, x = divmod(index, width)
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nx, ny = x + dx, y + dy
                    if not (0 <= nx < width and 0 <= ny < height):
                        continue
                    neighbour = ny * width + nx
                    if neighbour in source_of or known.data[neighbour] > 0:
                        continue
                    if target.data[neighbour] == 0:
                        continue
                    source_of[neighbour] = origin
                    offset = neighbour * 4
                    source_offset = origin * 4
                    out[offset : offset + 3] = image.data[source_offset : source_offset + 3]
                    out[offset + 3] = target.data[neighbour]
                    next_frontier.append(neighbour)
            frontier = next_frontier
        return Image(width, height, out)


@dataclass(frozen=True)
class GeneratedRegion:
    """Provenance for one bounded completion region (FR-008)."""

    region_id: str
    owner_layer_id: str
    mask: Mask
    reveal_bounds: Bounds
    feather_width_px: int
    producer_stage: str
    producer_id: str
    producer_version: str
    producer_kind: str
    model_id: str | None
    config_digest: str
    seed: str
    source_id: str

    def as_summary(self) -> dict[str, Any]:
        return {
            "region_id": self.region_id,
            "owner_layer_id": self.owner_layer_id,
            "reveal_bounds": self.reveal_bounds.as_cir(),
            "feather_width_px": float(self.feather_width_px),
            "producer_stage": self.producer_stage,
            "producer_kind": self.producer_kind,
            "generated_sample_count": self.mask.count_at_least(0),
        }


@dataclass(frozen=True)
class SynthesizedLayer:
    """A composited layer texture plus its optional generated region."""

    layer: SemanticLayer
    texture: Image
    visible_mask: Mask
    generated: GeneratedRegion | None

    @property
    def layer_id(self) -> str:
        return self.layer.layer_id


@dataclass(frozen=True)
class Synthesis:
    layers: tuple[SynthesizedLayer, ...]
    regions: tuple[GeneratedRegion, ...]
    filler_id: str
    filler_version: str
    producer_kind: str


def _expand_within_subject(mask: Mask, subject: Mask, margin: int) -> Mask:
    """Grow ``mask`` by ``margin`` but never beyond the subject silhouette."""
    if margin <= 0:
        return mask.copy()
    return mask.dilate(margin).intersect(subject)


def _occluder_unions(layers: Sequence[SemanticLayer]) -> dict[str, Mask]:
    """For each layer, the union of the visible masks of everything in front.

    Completion may only place generated pixels where a layer *in front* covers
    them. That is what makes the pixels genuinely hidden at neutral and
    revealable by motion, and it is what keeps generated content from landing on
    top of visible original artwork.
    """
    ordered = sorted(layers, key=lambda item: item.draw_order)
    unions: dict[str, Mask] = {}
    if not ordered:
        return unions
    width, height = ordered[0].mask.width, ordered[0].mask.height
    accumulated = Mask(width, height)
    for layer in reversed(ordered):
        unions[layer.layer_id] = accumulated.copy()
        accumulated = accumulated.union(layer.mask.binarize(0))
    return unions


def synthesize(
    image: Image,
    subject: Mask,
    decomposition: Decomposition,
    *,
    seed: str,
    config_digest: str,
    source_id: str,
    filler: CompletionFiller | None = None,
    feather_px: int = DEFAULT_FEATHER_PX,
) -> Synthesis:
    """Build layer textures and bounded completion regions."""
    engine: CompletionFiller = filler or EdgeExtensionFiller()  # type: ignore[assignment]
    if feather_px < 0:
        raise ContractError("feather width must not be negative")

    occluders = _occluder_unions(decomposition.layers)
    synthesized: list[SynthesizedLayer] = []
    regions: list[GeneratedRegion] = []
    for layer in decomposition.layers:
        visible = layer.mask.binarize(0)
        margin = REVEAL_MARGINS.get(layer.slot_id, DEFAULT_REVEAL_MARGIN_PX)
        # Only grow into area that something in front actually covers, so
        # generated pixels are hidden at neutral instead of overpainting
        # visible original artwork.
        occluded = occluders.get(layer.layer_id)
        reveal = _expand_within_subject(visible, subject, margin)
        target = reveal.subtract(visible)
        if occluded is not None:
            target = target.intersect(occluded)
            reveal = visible.union(target)

        texture = image.multiply_alpha_mask(visible)
        generated: GeneratedRegion | None = None
        if target.count_at_least(0) > 0:
            filled = engine.fill(image, visible, target)
            # Feather only the generated ramp, then keep the union so the
            # original visible coverage is preserved exactly.
            # Feathering must not spill outside the occluded target, or the ramp
            # would land on visible original pixels.
            softened = (target.feather(feather_px) if feather_px else target).intersect(target)
            coverage = visible.union(softened)
            texture = filled.multiply_alpha_mask(coverage)
            # Re-assert the source-pixel guarantee by construction: wherever the
            # upload was visible, restore its exact RGB and alpha.
            texture = _restore_visible_source(texture, image, visible)
            generated = GeneratedRegion(
                region_id=f"generated.{layer.layer_id.replace('layer.', '')}",
                owner_layer_id=layer.layer_id,
                mask=softened.subtract(visible),
                reveal_bounds=reveal.bounds_at_least(0),
                feather_width_px=feather_px,
                producer_stage="PLAN_AND_BOUNDED_COMPLETE",
                producer_id=engine.filler_id,
                producer_version=engine.filler_version,
                producer_kind=engine.producer_kind,
                model_id=engine.model_id,
                config_digest=config_digest,
                seed=seed,
                source_id=source_id,
            )
            regions.append(generated)

        synthesized.append(
            SynthesizedLayer(
                layer=layer,
                texture=texture,
                visible_mask=visible,
                generated=generated,
            )
        )

    return Synthesis(
        layers=tuple(synthesized),
        regions=tuple(regions),
        filler_id=engine.filler_id,
        filler_version=engine.filler_version,
        producer_kind=engine.producer_kind,
    )


def _restore_visible_source(texture: Image, source: Image, visible: Mask) -> Image:
    """Force originally visible samples back to their exact source values."""
    out = bytearray(texture.data)
    for index, coverage in enumerate(visible.data):
        if coverage == 0:
            continue
        offset = index * 4
        out[offset : offset + 4] = source.data[offset : offset + 4]
    return Image(texture.width, texture.height, out)


def compose_neutral(canvas_width: int, canvas_height: int, synthesis: Synthesis) -> Image:
    """Composite every layer in ascending draw order into the neutral image."""
    result = Image(canvas_width, canvas_height)
    for item in sorted(synthesis.layers, key=lambda entry: entry.layer.draw_order):
        result = result.composite_over(item.texture)
    return result
