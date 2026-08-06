"""Minimal geometry ABI codecs and validity checks.

Implements ``docs/CIR_SPEC.md`` §7: ``oc2d.mesh.xyuv.f32le.v1`` vertices,
tightly packed uint16/uint32 little-endian index triples and
``oc2d.delta.xy.f32le.v1`` deformation deltas. Every payload declares byte
length, element count, target mesh and SHA-256; lengths are verified before any
element is read and host byte order is never relied upon.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from typing import Final, Sequence

from .errors import ContractError

VERTEX_FORMAT: Final[str] = "oc2d.mesh.xyuv.f32le.v1"
INDEX_FORMAT_U16: Final[str] = "oc2d.indices.u16le.v1"
INDEX_FORMAT_U32: Final[str] = "oc2d.indices.u32le.v1"
DELTA_FORMAT: Final[str] = "oc2d.delta.xy.f32le.v1"
VERTEX_STRIDE: Final[int] = 16
DELTA_STRIDE: Final[int] = 8
MAX_VERTICES: Final[int] = 1_000_000
MAX_TRIANGLES: Final[int] = 2_000_000
WINDING: Final[str] = "clockwise"


@dataclass(frozen=True)
class Vertex:
    x: float
    y: float
    u: float
    v: float


@dataclass(frozen=True)
class Mesh:
    """A validated triangle mesh in source-pixel space with UV in ``[0, 1]``."""

    vertices: tuple[Vertex, ...]
    triangles: tuple[tuple[int, int, int], ...]

    @property
    def vertex_count(self) -> int:
        return len(self.vertices)

    @property
    def triangle_count(self) -> int:
        return len(self.triangles)

    @property
    def index_format(self) -> str:
        return INDEX_FORMAT_U16 if len(self.vertices) <= 0xFFFF else INDEX_FORMAT_U32


def _check_finite(value: float, label: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value)):
        raise ContractError(f"{label} must be a finite number")
    return float(value)


def _round_trip_f32(value: float) -> float:
    """Return ``value`` as it will exist after float32 serialization."""
    return struct.unpack("<f", struct.pack("<f", value))[0]


def encode_vertices(vertices: Sequence[Vertex]) -> bytes:
    """Serialize vertices as ``oc2d.mesh.xyuv.f32le.v1``."""
    if not 3 <= len(vertices) <= MAX_VERTICES:
        raise ContractError("vertex count is out of range")
    out = bytearray()
    for vertex in vertices:
        x = _check_finite(vertex.x, "vertex x")
        y = _check_finite(vertex.y, "vertex y")
        u = _check_finite(vertex.u, "vertex u")
        v = _check_finite(vertex.v, "vertex v")
        if not 0.0 <= u <= 1.0 or not 0.0 <= v <= 1.0:
            raise ContractError("vertex UV must lie in [0, 1]")
        out.extend(struct.pack("<ffff", x, y, u, v))
    return bytes(out)


def decode_vertices(payload: bytes) -> tuple[Vertex, ...]:
    """Parse and validate an ``oc2d.mesh.xyuv.f32le.v1`` payload."""
    if len(payload) % VERTEX_STRIDE:
        raise ContractError("vertex payload length is not a whole number of vertices")
    count = len(payload) // VERTEX_STRIDE
    if not 3 <= count <= MAX_VERTICES:
        raise ContractError("vertex count is out of range")
    vertices = []
    for index in range(count):
        x, y, u, v = struct.unpack_from("<ffff", payload, index * VERTEX_STRIDE)
        for value, label in ((x, "vertex x"), (y, "vertex y"), (u, "vertex u"), (v, "vertex v")):
            if not math.isfinite(value):
                raise ContractError(f"{label} must be finite")
        if not 0.0 <= u <= 1.0 or not 0.0 <= v <= 1.0:
            raise ContractError("vertex UV must lie in [0, 1]")
        vertices.append(Vertex(x, y, u, v))
    return tuple(vertices)


def encode_indices(triangles: Sequence[tuple[int, int, int]], vertex_count: int) -> tuple[bytes, str]:
    """Serialize triangle indices, choosing the narrowest legal element type."""
    if not 1 <= len(triangles) <= MAX_TRIANGLES:
        raise ContractError("triangle count is out of range")
    wide = vertex_count > 0xFFFF
    code = "<I" if wide else "<H"
    out = bytearray()
    for triangle in triangles:
        if len(triangle) != 3:
            raise ContractError("triangle must have exactly three indices")
        for index in triangle:
            if not isinstance(index, int) or isinstance(index, bool):
                raise ContractError("triangle index must be an integer")
            if not 0 <= index < vertex_count:
                raise ContractError("triangle index is out of range")
            out.extend(struct.pack(code, index))
    return bytes(out), (INDEX_FORMAT_U32 if wide else INDEX_FORMAT_U16)


def decode_indices(payload: bytes, index_format: str, vertex_count: int) -> tuple[tuple[int, int, int], ...]:
    """Parse and validate a tightly packed index payload."""
    if index_format == INDEX_FORMAT_U16:
        element = 2
        code = "<H"
    elif index_format == INDEX_FORMAT_U32:
        element = 4
        code = "<I"
    else:
        raise ContractError("index payload format is unsupported")
    if len(payload) % (element * 3):
        raise ContractError("index payload length is not a whole number of triangles")
    count = len(payload) // (element * 3)
    if not 1 <= count <= MAX_TRIANGLES:
        raise ContractError("triangle count is out of range")
    triangles = []
    for index in range(count):
        base = index * element * 3
        a = struct.unpack_from(code, payload, base)[0]
        b = struct.unpack_from(code, payload, base + element)[0]
        c = struct.unpack_from(code, payload, base + element * 2)[0]
        for value in (a, b, c):
            if not 0 <= value < vertex_count:
                raise ContractError("triangle index is out of range")
        if a == b or b == c or a == c:
            raise ContractError("degenerate triangle references a repeated vertex")
        triangles.append((a, b, c))
    return tuple(triangles)


def encode_deltas(deltas: Sequence[tuple[float, float]], vertex_count: int) -> bytes:
    """Serialize deformation deltas as ``oc2d.delta.xy.f32le.v1``."""
    if len(deltas) != vertex_count:
        raise ContractError("delta count must match the target mesh vertex count")
    out = bytearray()
    for dx, dy in deltas:
        out.extend(
            struct.pack("<ff", _check_finite(dx, "delta dx"), _check_finite(dy, "delta dy"))
        )
    return bytes(out)


def decode_deltas(payload: bytes, vertex_count: int) -> tuple[tuple[float, float], ...]:
    """Parse and validate an ``oc2d.delta.xy.f32le.v1`` payload."""
    if len(payload) % DELTA_STRIDE:
        raise ContractError("delta payload length is not a whole number of deltas")
    count = len(payload) // DELTA_STRIDE
    if count != vertex_count:
        raise ContractError("delta count must match the target mesh vertex count")
    deltas = []
    for index in range(count):
        dx, dy = struct.unpack_from("<ff", payload, index * DELTA_STRIDE)
        if not math.isfinite(dx) or not math.isfinite(dy):
            raise ContractError("delta components must be finite")
        deltas.append((dx, dy))
    return tuple(deltas)


def signed_area(a: Vertex, b: Vertex, c: Vertex) -> float:
    """Twice the signed area of a triangle using the standard cross product.

    The CIR source space is Y-down (``docs/CIR_SPEC.md`` §2), which mirrors the
    usual Y-up orientation. A triangle that appears clockwise on screen
    therefore yields a positive value here.
    """
    return (b.x - a.x) * (c.y - a.y) - (c.x - a.x) * (b.y - a.y)


def check_winding(mesh: Mesh) -> None:
    """Reject any triangle that is degenerate or wound counter-clockwise."""
    for a, b, c in mesh.triangles:
        area = signed_area(mesh.vertices[a], mesh.vertices[b], mesh.vertices[c])
        if area == 0.0:
            raise ContractError("triangle is geometrically degenerate")
        if area < 0.0:
            raise ContractError("triangle winding is not clockwise")


def check_mesh(mesh: Mesh) -> None:
    """Validate index bounds, duplicate triangles, winding and UV domain."""
    if not 3 <= mesh.vertex_count <= MAX_VERTICES:
        raise ContractError("vertex count is out of range")
    if not 1 <= mesh.triangle_count <= MAX_TRIANGLES:
        raise ContractError("triangle count is out of range")
    seen: set[tuple[int, int, int]] = set()
    for triangle in mesh.triangles:
        key = tuple(sorted(triangle))  # type: ignore[assignment]
        if key in seen:
            raise ContractError("duplicate triangle references the same vertex set")
        seen.add(key)  # type: ignore[arg-type]
    check_winding(mesh)


def apply_deltas(mesh: Mesh, deltas: Sequence[tuple[float, float]]) -> Mesh:
    """Return ``mesh`` displaced by ``deltas``, preserving UV assignments."""
    if len(deltas) != mesh.vertex_count:
        raise ContractError("delta count must match the target mesh vertex count")
    moved = tuple(
        Vertex(vertex.x + dx, vertex.y + dy, vertex.u, vertex.v)
        for vertex, (dx, dy) in zip(mesh.vertices, deltas, strict=True)
    )
    return Mesh(moved, mesh.triangles)


def interpolate_deltas(
    samples: Sequence[tuple[float, Sequence[tuple[float, float]]]],
    value: float,
    vertex_count: int,
) -> tuple[tuple[float, float], ...]:
    """Evaluate a 1-D binding with linear interpolation and clamped extrapolation.

    ``samples`` must be sorted by strictly increasing parameter value and hold at
    least two entries, matching ``docs/CIR_SPEC.md`` §8.
    """
    if len(samples) < 2:
        raise ContractError("binding requires at least two samples")
    previous: float | None = None
    for parameter_value, deltas in samples:
        _check_finite(parameter_value, "binding sample value")
        if previous is not None and parameter_value <= previous:
            raise ContractError("binding samples must strictly increase")
        previous = parameter_value
        if len(deltas) != vertex_count:
            raise ContractError("binding sample delta count mismatch")
    _check_finite(value, "parameter value")

    if value <= samples[0][0]:
        return tuple((float(dx), float(dy)) for dx, dy in samples[0][1])
    if value >= samples[-1][0]:
        return tuple((float(dx), float(dy)) for dx, dy in samples[-1][1])
    for index in range(len(samples) - 1):
        low_value, low_deltas = samples[index]
        high_value, high_deltas = samples[index + 1]
        if low_value <= value <= high_value:
            span = high_value - low_value
            weight = 0.0 if span == 0 else (value - low_value) / span
            return tuple(
                (
                    low[0] + (high[0] - low[0]) * weight,
                    low[1] + (high[1] - low[1]) * weight,
                )
                for low, high in zip(low_deltas, high_deltas, strict=True)
            )
    raise ContractError("parameter value could not be located in the binding domain")


def grid_mesh(
    x: int,
    y: int,
    width: int,
    height: int,
    columns: int,
    rows: int,
    canvas_width: int,
    canvas_height: int,
) -> Mesh:
    """Build a deterministic clockwise-wound quad grid over a layer's bounds.

    UVs are normalized against the full canvas so that every layer samples its
    texture in the same source space.
    """
    if columns < 1 or rows < 1:
        raise ContractError("grid must have at least one cell")
    if width <= 0 or height <= 0:
        raise ContractError("grid bounds must be non-empty")
    vertices: list[Vertex] = []
    for row in range(rows + 1):
        for column in range(columns + 1):
            px = x + width * column / columns
            py = y + height * row / rows
            vertices.append(
                Vertex(
                    _round_trip_f32(px),
                    _round_trip_f32(py),
                    _round_trip_f32(min(1.0, max(0.0, px / canvas_width))),
                    _round_trip_f32(min(1.0, max(0.0, py / canvas_height))),
                )
            )
    triangles: list[tuple[int, int, int]] = []
    stride = columns + 1
    for row in range(rows):
        for column in range(columns):
            top_left = row * stride + column
            top_right = top_left + 1
            bottom_left = top_left + stride
            bottom_right = bottom_left + 1
            # Y-down space: these orderings are clockwise on screen.
            triangles.append((top_left, top_right, bottom_right))
            triangles.append((top_left, bottom_right, bottom_left))
    mesh = Mesh(tuple(vertices), tuple(triangles))
    check_mesh(mesh)
    return mesh
