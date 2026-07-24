"""Single Pillow RGBA renderer shared by disposable Gate F arms."""

from __future__ import annotations

import math
from dataclasses import dataclass
from fractions import Fraction
from typing import Any, Iterable

from .contracts import ArtifactRef, StageContext, StageContractError
from .raster import _verify_output_png

RENDERER_CONTRACT_ID = "oc2d.spike.pillow-rgba-renderer.v1"
RENDERER_PROFILE_ID = "pillow-12.1.0-bilinear-straight-srgb-source-over.v1"


@dataclass(frozen=True)
class Affine:
    a: Fraction
    b: Fraction
    c: Fraction
    d: Fraction
    e: Fraction
    f: Fraction

    @classmethod
    def identity(cls) -> "Affine":
        return cls(Fraction(1), Fraction(0), Fraction(0), Fraction(0), Fraction(1), Fraction(0))

    def compose(self, inner: "Affine") -> "Affine":
        """Return this transform applied after ``inner``."""
        return Affine(
            self.a * inner.a + self.b * inner.d,
            self.a * inner.b + self.b * inner.e,
            self.a * inner.c + self.b * inner.f + self.c,
            self.d * inner.a + self.e * inner.d,
            self.d * inner.b + self.e * inner.e,
            self.d * inner.c + self.e * inner.f + self.f,
        )

    def inverse_tuple(self) -> tuple[float, float, float, float, float, float]:
        determinant = self.a * self.e - self.b * self.d
        if determinant == 0:
            raise StageContractError("render transform is not invertible")
        values = (
            self.e / determinant,
            -self.b / determinant,
            (self.b * self.f - self.e * self.c) / determinant,
            -self.d / determinant,
            self.a / determinant,
            (self.d * self.c - self.a * self.f) / determinant,
        )
        result = tuple(float(value) for value in values)
        if not all(math.isfinite(value) for value in result):
            raise StageContractError("render transform is not finite")
        return result

    def map_point(self, x: Fraction, y: Fraction) -> tuple[Fraction, Fraction]:
        return self.a * x + self.b * y + self.c, self.d * x + self.e * y + self.f


@dataclass(frozen=True)
class RenderLayer:
    image: Any
    source_box_ltrb: tuple[int, int, int, int]
    transform: Affine


def render_rgba_layers(
    base: Any,
    layers: Iterable[RenderLayer],
    backend: Any,
    context: StageContext | None,
    *,
    premultiply_alpha: bool = False,
) -> Any:
    result = base.copy()
    for layer in layers:
        if context is not None:
            context.cancellation.checkpoint()
        inverse = layer.transform.inverse_tuple()
        left, top, _, _ = layer.source_box_ltrb
        local_inverse = (
            inverse[0],
            inverse[1],
            inverse[2] - left,
            inverse[3],
            inverse[4],
            inverse[5] - top,
        )
        source = layer.image.convert("RGBa") if premultiply_alpha else layer.image
        try:
            transformed = source.transform(
                base.size,
                backend.Image.Transform.AFFINE,
                local_inverse,
                resample=backend.Image.Resampling.BILINEAR,
                fillcolor=(0, 0, 0, 0),
            )
        finally:
            if source is not layer.image:
                source.close()
        try:
            if premultiply_alpha:
                straight = transformed.convert("RGBA")
                try:
                    result.alpha_composite(straight)
                finally:
                    straight.close()
            else:
                result.alpha_composite(transformed)
        finally:
            transformed.close()
    return result


def write_rgba_png(image: Any, name: str, role: str, context: StageContext, backend: Any) -> ArtifactRef:
    writer = context.sink.open_binary(name, role=role, media_type="image/png")
    pnginfo = backend.PngImagePlugin.PngInfo()
    pnginfo.add(b"sRGB", b"\x00")
    with writer:
        image.save(
            writer,
            format="PNG",
            optimize=False,
            compress_level=9,
            pnginfo=pnginfo,
            icc_profile=None,
            exif=b"",
        )
    artifact = writer.artifact
    _verify_output_png(artifact.path.read_bytes(), image.size)
    return artifact
