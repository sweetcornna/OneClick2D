"""``MESH_AND_MINIMAL_RIG``: deterministic meshes and minimal parameter bindings.

Mesh topology, parameter capability, ranges, signs and interpolation are
deterministic policy (FR-009). Every mandatory capability in the parameter
registry gets a binding with at least two strictly increasing samples, is
manually operable (FR-013), and has a safe range inside its full range.

Deltas are plain vertex displacements: ``docs/CIR_SPEC.md`` §8 restricts v0.2 to
linear interpolation with clamped extrapolation, so nothing here needs a
non-commutative or multi-dimensional deformer.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Final

from ..errors import ContractError
from ..geometry import Mesh, grid_mesh
from ..raster.image import Bounds
from ..registries import Registries
from .decompose import SemanticLayer
from .synthesize import Synthesis

# Grid density per slot. Face features need finer grids than the torso because
# their deltas are larger relative to their bounds.
GRID_DENSITY: Final[dict[str, tuple[int, int]]] = {
    "oc2d.eye.left": (4, 3),
    "oc2d.eye.right": (4, 3),
    "oc2d.brow.left": (4, 2),
    "oc2d.brow.right": (4, 2),
    "oc2d.mouth": (4, 3),
    "oc2d.face.base": (6, 6),
    "oc2d.neck": (3, 3),
    "oc2d.torso": (6, 6),
    "oc2d.hair.front": (5, 4),
    "oc2d.hair.back": (5, 5),
    "oc2d.hair.side": (4, 4),
    "oc2d.character": (6, 8),
}
DEFAULT_DENSITY: Final[tuple[int, int]] = (4, 4)
# Fraction of the full template range that is considered safe without review.
SAFE_RANGE_FRACTION: Final[float] = 0.8
# How far a closing lid or mouth may draw toward its midline. Strictly below 1.0
# so opposing rows never meet: a zero-area triangle flips winding, which
# whole-project validation blocks.
MAX_COLLAPSE_FRACTION: Final[float] = 0.92


@dataclass(frozen=True)
class ParameterSpec:
    """A resolved parameter capability bound to this project."""

    parameter_id: str
    capability: str
    unit: str
    minimum: float
    default: float
    maximum: float
    safe_minimum: float
    safe_maximum: float
    manual_enabled: bool
    tracking_enabled: bool
    sign: str

    def as_cir(self) -> dict[str, Any]:
        """Project to the CIR v0.2 parameter shape.

        ``unit`` and ``sign`` are not carried here: the CIR resolves them through
        the immutable parameter registry reference, so duplicating them in the
        project would create a second, divergable source of truth.
        """
        return {
            "id": self.parameter_id,
            "capability": self.capability,
            "minimum": self.minimum,
            "default": self.default,
            "maximum": self.maximum,
            "safe_minimum": self.safe_minimum,
            "safe_maximum": self.safe_maximum,
            "clamp": True,
            "manual_enabled": self.manual_enabled,
            "tracking_enabled": self.tracking_enabled,
        }

    def validate(self) -> None:
        if not (
            self.minimum <= self.safe_minimum <= self.default <= self.safe_maximum <= self.maximum
        ):
            raise ContractError("parameter range ordering is invalid")
        for value in (self.minimum, self.default, self.maximum, self.safe_minimum, self.safe_maximum):
            if not math.isfinite(value):
                raise ContractError("parameter range values must be finite")
        if self.capability == "candidate_mandatory" and not self.manual_enabled:
            raise ContractError("mandatory parameters must remain manually operable")


@dataclass(frozen=True)
class BindingSample:
    parameter_value: float
    deltas: tuple[tuple[float, float], ...]


@dataclass(frozen=True)
class Binding:
    """A 1-D binding from one parameter to one mesh."""

    binding_id: str
    parameter_id: str
    target_mesh_id: str
    samples: tuple[BindingSample, ...]

    def validate(self, vertex_count: int) -> None:
        if len(self.samples) < 2:
            raise ContractError("binding requires at least two samples")
        previous: float | None = None
        for sample in self.samples:
            if previous is not None and sample.parameter_value <= previous:
                raise ContractError("binding samples must strictly increase")
            previous = sample.parameter_value
            if len(sample.deltas) != vertex_count:
                raise ContractError("binding sample delta count mismatch")


@dataclass(frozen=True)
class LayerRig:
    layer_id: str
    mesh_id: str
    mesh: Mesh


@dataclass(frozen=True)
class Rig:
    meshes: tuple[LayerRig, ...]
    parameters: tuple[ParameterSpec, ...]
    bindings: tuple[Binding, ...]

    def mesh_for(self, layer_id: str) -> LayerRig:
        for item in self.meshes:
            if item.layer_id == layer_id:
                return item
        raise ContractError("layer has no mesh")


def _resolve_parameters(registries: Registries) -> tuple[ParameterSpec, ...]:
    specs: list[ParameterSpec] = []
    for entry in registries.parameters_list:
        low, high = (float(value) for value in entry["template_range"])
        neutral = float(entry["neutral"])
        span = high - low
        margin = span * (1.0 - SAFE_RANGE_FRACTION) / 2.0
        safe_low = min(max(low + margin, low), neutral)
        safe_high = max(min(high - margin, high), neutral)
        roles = list(entry.get("roles") or ())
        spec = ParameterSpec(
            parameter_id=str(entry["id"]),
            capability=str(entry["capability"]),
            unit=str(entry["unit"]),
            minimum=low,
            default=neutral,
            maximum=high,
            safe_minimum=safe_low,
            safe_maximum=safe_high,
            manual_enabled="manual" in roles,
            tracking_enabled="tracking" in roles,
            sign=str(entry["sign"]),
        )
        spec.validate()
        specs.append(spec)
    return tuple(specs)


def _target_slots(parameter_id: str) -> tuple[str, ...]:
    """Which semantic slots a parameter is allowed to move."""
    mapping: dict[str, tuple[str, ...]] = {
        "head.yaw": ("oc2d.face.base", "oc2d.hair.front", "oc2d.hair.back", "oc2d.eye.left", "oc2d.eye.right", "oc2d.brow.left", "oc2d.brow.right", "oc2d.mouth", "oc2d.neck"),
        "head.pitch": ("oc2d.face.base", "oc2d.hair.front", "oc2d.hair.back", "oc2d.eye.left", "oc2d.eye.right", "oc2d.brow.left", "oc2d.brow.right", "oc2d.mouth", "oc2d.neck"),
        "eye.left.open": ("oc2d.eye.left",),
        "eye.right.open": ("oc2d.eye.right",),
        "eye.gaze.x": ("oc2d.eye.left", "oc2d.eye.right"),
        "eye.gaze.y": ("oc2d.eye.left", "oc2d.eye.right"),
        "mouth.open": ("oc2d.mouth",),
        "mouth.form": ("oc2d.mouth",),
        "body.lean": ("oc2d.torso", "oc2d.character"),
        "breath": ("oc2d.torso",),
    }
    return mapping.get(parameter_id, ())


def _deltas_for(
    parameter_id: str,
    value: float,
    spec: ParameterSpec,
    mesh: Mesh,
    bounds: Bounds,
) -> tuple[tuple[float, float], ...]:
    """Compute vertex displacements for one parameter value.

    Displacements are bounded fractions of the target layer's own bounds, so a
    small feature never receives a delta scaled to the whole canvas. Signs follow
    the parameter registry: ``head.yaw`` positive moves the nose toward screen
    right, ``head.pitch`` positive looks up, and eye/mouth ``open`` positive
    opens further.
    """
    span = max(abs(spec.minimum), abs(spec.maximum)) or 1.0
    normalized = value / span
    width = float(bounds.width or 1)
    height = float(bounds.height or 1)
    centre_x = bounds.x + width / 2.0
    centre_y = bounds.y + height / 2.0

    deltas: list[tuple[float, float]] = []
    for vertex in mesh.vertices:
        if parameter_id == "head.yaw":
            # Horizontal shear with depth falloff from the vertical centre line.
            depth = 1.0 - min(1.0, abs(vertex.y - centre_y) / (height / 2.0)) * 0.35
            deltas.append((normalized * width * 0.06 * depth, 0.0))
        elif parameter_id == "head.pitch":
            # Positive looks up: features move up (negative Y in a Y-down space).
            depth = 1.0 - min(1.0, abs(vertex.x - centre_x) / (width / 2.0)) * 0.25
            deltas.append((0.0, -normalized * height * 0.05 * depth))
        elif parameter_id in ("eye.left.open", "eye.right.open", "mouth.open"):
            # Closing draws the layer toward its horizontal midline. The pull is
            # proportional to each vertex's own distance from that midline, and
            # capped below 1.0, so rows approach each other without ever meeting:
            # a full collapse would zero the triangle area and flip winding,
            # which FR-010 treats as a blocking mesh defect.
            offset = (spec.default - value) / (spec.maximum - spec.minimum or 1.0)
            pull = max(-MAX_COLLAPSE_FRACTION, min(MAX_COLLAPSE_FRACTION, offset))
            deltas.append((0.0, (centre_y - vertex.y) * pull))
        elif parameter_id == "eye.gaze.x":
            deltas.append((normalized * width * 0.10, 0.0))
        elif parameter_id == "eye.gaze.y":
            deltas.append((0.0, -normalized * height * 0.10))
        elif parameter_id == "mouth.form":
            # Positive widens, negative narrows, about the horizontal centre.
            deltas.append(((vertex.x - centre_x) / (width / 2.0) * normalized * width * 0.06, 0.0))
        elif parameter_id == "body.lean":
            falloff = max(0.0, 1.0 - (vertex.y - bounds.y) / height)
            deltas.append((normalized * width * 0.05 * falloff, 0.0))
        elif parameter_id == "breath":
            falloff = max(0.0, 1.0 - (vertex.y - bounds.y) / height)
            deltas.append((0.0, -normalized * height * 0.012 * falloff))
        else:
            deltas.append((0.0, 0.0))
    return tuple(deltas)


def build_rig(
    synthesis: Synthesis,
    registries: Registries,
    canvas_width: int,
    canvas_height: int,
) -> Rig:
    """Build meshes for every layer and bindings for every applicable parameter."""
    meshes: list[LayerRig] = []
    bounds_by_layer: dict[str, Bounds] = {}
    slot_by_layer: dict[str, str] = {}
    for item in synthesis.layers:
        layer: SemanticLayer = item.layer
        # The mesh must cover the layer's full coverage, including any generated
        # reveal margin, or motion would tear at the generated boundary.
        coverage = item.texture.alpha_mask().bounds_at_least(0)
        if coverage.empty:
            coverage = layer.bounds
        columns, rows = GRID_DENSITY.get(layer.slot_id, DEFAULT_DENSITY)
        mesh = grid_mesh(
            coverage.x,
            coverage.y,
            coverage.width,
            coverage.height,
            columns,
            rows,
            canvas_width,
            canvas_height,
        )
        mesh_id = f"mesh.{layer.layer_id.replace('layer.', '')}"
        meshes.append(LayerRig(layer_id=layer.layer_id, mesh_id=mesh_id, mesh=mesh))
        bounds_by_layer[layer.layer_id] = coverage
        slot_by_layer[layer.layer_id] = layer.slot_id

    parameters = _resolve_parameters(registries)
    bindings: list[Binding] = []
    for spec in parameters:
        targets = _target_slots(spec.parameter_id)
        for rig_mesh in meshes:
            if slot_by_layer[rig_mesh.layer_id] not in targets:
                continue
            bounds = bounds_by_layer[rig_mesh.layer_id]
            # Sample the full declared range plus neutral so extrapolation is
            # always a clamp, never an invention.
            values = sorted({spec.minimum, spec.default, spec.maximum})
            samples = tuple(
                BindingSample(
                    parameter_value=value,
                    deltas=_deltas_for(spec.parameter_id, value, spec, rig_mesh.mesh, bounds),
                )
                for value in values
            )
            binding = Binding(
                binding_id=f"binding.{spec.parameter_id.replace('.', '-')}.{rig_mesh.mesh_id.replace('mesh.', '')}",
                parameter_id=spec.parameter_id,
                target_mesh_id=rig_mesh.mesh_id,
                samples=samples,
            )
            binding.validate(rig_mesh.mesh.vertex_count)
            bindings.append(binding)

    mandatory = set(registries.mandatory_parameter_ids())
    bound = {binding.parameter_id for binding in bindings}
    missing = sorted(mandatory - bound)
    if missing:
        # FR-009: a result missing a mandatory capability is unusable, so this
        # fails closed rather than shipping a partially rigged project.
        raise ContractError("mandatory parameter capability has no binding")

    return Rig(meshes=tuple(meshes), parameters=parameters, bindings=tuple(bindings))
