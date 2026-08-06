"""Deterministic CIR renderer.

The renderer consumes validated CIR and nothing else: no model weights, no
inference, no PSD library (``docs/ARCHITECTURE.md`` §4). This is what makes
FR-014 true — an ``.oc2d`` renders from its own contents.

Rasterization is affine-per-triangle with inverse mapping and bilinear texture
sampling, composited in ascending draw order. Sampling is performed on
premultiplied intermediates so transparent texels never bleed dark fringes into
neighbouring pixels, then converted back to straight alpha for storage.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from .errors import ContractError
from .geometry import Mesh, Vertex, apply_deltas, interpolate_deltas
from .raster.image import Image

RENDERER_ID = "oneclick2d.render.affine-bilinear-premultiplied.v1"


@dataclass(frozen=True)
class RenderLayer:
    """One layer ready to rasterize: a texture, a mesh and a draw order."""

    layer_id: str
    texture: Image
    mesh: Mesh
    draw_order: int
    opacity: float = 1.0
    visible: bool = True


def _sample_bilinear(texture: Image, u: float, v: float) -> tuple[int, int, int, int]:
    """Sample a texture at normalized UV using premultiplied bilinear weights."""
    width, height = texture.width, texture.height
    x = u * width - 0.5
    y = v * height - 0.5
    x0 = int(x // 1)
    y0 = int(y // 1)
    fx = x - x0
    fy = y - y0
    total_r = total_g = total_b = total_a = 0.0
    for dy in (0, 1):
        for dx in (0, 1):
            sx = min(max(x0 + dx, 0), width - 1)
            sy = min(max(y0 + dy, 0), height - 1)
            weight = (fx if dx else 1.0 - fx) * (fy if dy else 1.0 - fy)
            if weight <= 0.0:
                continue
            offset = (sy * width + sx) * 4
            alpha = texture.data[offset + 3] / 255.0
            # Premultiply before weighting so transparent texels carry no colour.
            total_r += texture.data[offset] * alpha * weight
            total_g += texture.data[offset + 1] * alpha * weight
            total_b += texture.data[offset + 2] * alpha * weight
            total_a += alpha * weight
    if total_a <= 0.0:
        return (0, 0, 0, 0)
    red = min(255, max(0, int(round(total_r / total_a))))
    green = min(255, max(0, int(round(total_g / total_a))))
    blue = min(255, max(0, int(round(total_b / total_a))))
    alpha_out = min(255, max(0, int(round(total_a * 255.0))))
    return (red, green, blue, alpha_out)


def _rasterize_triangle(
    target: Image,
    texture: Image,
    a: Vertex,
    b: Vertex,
    c: Vertex,
    opacity: float,
) -> None:
    """Rasterize one triangle with inverse barycentric mapping."""
    min_x = max(0, int(min(a.x, b.x, c.x) // 1))
    max_x = min(target.width - 1, int(max(a.x, b.x, c.x) // 1) + 1)
    min_y = max(0, int(min(a.y, b.y, c.y) // 1))
    max_y = min(target.height - 1, int(max(a.y, b.y, c.y) // 1) + 1)
    if min_x > max_x or min_y > max_y:
        return

    denominator = (b.x - a.x) * (c.y - a.y) - (c.x - a.x) * (b.y - a.y)
    if denominator == 0.0:
        return

    for py in range(min_y, max_y + 1):
        sample_y = py + 0.5
        for px in range(min_x, max_x + 1):
            sample_x = px + 0.5
            # Barycentric coordinates of the pixel centre.
            w0 = ((b.x - sample_x) * (c.y - sample_y) - (c.x - sample_x) * (b.y - sample_y)) / denominator
            w1 = ((c.x - sample_x) * (a.y - sample_y) - (a.x - sample_x) * (c.y - sample_y)) / denominator
            w2 = 1.0 - w0 - w1
            if w0 < 0.0 or w1 < 0.0 or w2 < 0.0:
                continue
            u = w0 * a.u + w1 * b.u + w2 * c.u
            v = w0 * a.v + w1 * b.v + w2 * c.v
            red, green, blue, alpha = _sample_bilinear(texture, u, v)
            if alpha == 0:
                continue
            if opacity < 1.0:
                alpha = int(round(alpha * opacity))
                if alpha == 0:
                    continue
            offset = (py * target.width + px) * 4
            base_alpha = target.data[offset + 3]
            if alpha == 255:
                target.data[offset : offset + 4] = bytes((red, green, blue, 255))
                continue
            result_alpha = alpha + (base_alpha * (255 - alpha) + 127) // 255
            if result_alpha == 0:
                continue
            for channel, value in enumerate((red, green, blue)):
                numerator = (
                    value * alpha * 255
                    + target.data[offset + channel] * base_alpha * (255 - alpha)
                )
                target.data[offset + channel] = min(255, numerator // (result_alpha * 255))
            target.data[offset + 3] = result_alpha


def render_layers(canvas_width: int, canvas_height: int, layers: Iterable[RenderLayer]) -> Image:
    """Rasterize layers in ascending draw order onto a transparent canvas."""
    target = Image(canvas_width, canvas_height)
    for layer in sorted(layers, key=lambda item: item.draw_order):
        if not layer.visible or layer.opacity <= 0.0:
            continue
        if not 0.0 <= layer.opacity <= 1.0:
            raise ContractError("layer opacity must lie in [0, 1]")
        mesh = layer.mesh
        for index_a, index_b, index_c in mesh.triangles:
            _rasterize_triangle(
                target,
                layer.texture,
                mesh.vertices[index_a],
                mesh.vertices[index_b],
                mesh.vertices[index_c],
                layer.opacity,
            )
    return target


def pose_layers(
    layers: Sequence[RenderLayer],
    bindings: Sequence[tuple[str, str, Sequence[tuple[float, Sequence[tuple[float, float]]]]]],
    values: dict[str, float],
) -> tuple[RenderLayer, ...]:
    """Apply parameter values to layer meshes.

    ``bindings`` entries are ``(parameter_id, target_mesh_id, samples)``. Multiple
    bindings targeting one mesh accumulate additively in the order supplied,
    which the caller must have already sorted by registry order then binding ID
    (``docs/CIR_SPEC.md`` §8).
    """
    by_mesh: dict[str, list[tuple[float, float]]] = {}
    for parameter_id, mesh_id, samples in bindings:
        value = values.get(parameter_id)
        if value is None:
            continue
        vertex_count = len(samples[0][1]) if samples else 0
        deltas = interpolate_deltas(samples, value, vertex_count)
        accumulated = by_mesh.setdefault(mesh_id, [(0.0, 0.0)] * vertex_count)
        if len(accumulated) != len(deltas):
            raise ContractError("binding delta count disagrees with the target mesh")
        by_mesh[mesh_id] = [
            (existing[0] + delta[0], existing[1] + delta[1])
            for existing, delta in zip(accumulated, deltas, strict=True)
        ]

    posed: list[RenderLayer] = []
    for layer in layers:
        mesh_id = f"mesh.{layer.layer_id.replace('layer.', '')}"
        deltas = by_mesh.get(mesh_id)
        mesh = apply_deltas(layer.mesh, deltas) if deltas else layer.mesh
        posed.append(
            RenderLayer(
                layer_id=layer.layer_id,
                texture=layer.texture,
                mesh=mesh,
                draw_order=layer.draw_order,
                opacity=layer.opacity,
                visible=layer.visible,
            )
        )
    return tuple(posed)
