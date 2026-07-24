"""Shared fixed-point Gate F frame sequence for disposable arm preflights."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from fractions import Fraction
from typing import Any

from .contracts import StageContractError
from .runtime import canonical_json_bytes, digest_framed

PROFILE_ID = "oc2d.spike.gate-f-frame-sequence.v1"
ALGORITHM_ID = "sha256-waypoint-linear-fixed-point.v1"
PARAMETER_SCALE = 1000
PARAMETER_ORDER = (
    "head.yaw",
    "head.pitch",
    "eye.left.open",
    "eye.right.open",
    "mouth.open",
)
PARAMETER_RANGES = (
    (-15000, 15000),
    (-10000, 10000),
    (0, 1000),
    (0, 1000),
    (0, 1000),
)
MANDATORY_TICKS = (
    ("neutral", (0, 0, 1000, 1000, 0)),
    ("yaw.min", (-15000, 0, 1000, 1000, 0)),
    ("yaw.max", (15000, 0, 1000, 1000, 0)),
    ("pitch.min", (0, -10000, 1000, 1000, 0)),
    ("pitch.max", (0, 10000, 1000, 1000, 0)),
    ("eye.left.closed", (0, 0, 0, 1000, 0)),
    ("eye.right.closed", (0, 0, 1000, 0, 0)),
    ("eyes.closed", (0, 0, 0, 0, 0)),
    ("mouth.max", (0, 0, 1000, 1000, 1000)),
    ("yaw.min-pitch.min", (-15000, -10000, 1000, 1000, 0)),
    ("yaw.max-eyes.closed", (15000, 0, 0, 0, 0)),
    ("yaw.min-pitch.max-mouth.max", (-15000, 10000, 1000, 1000, 1000)),
)
MANDATORY_FRAME_COUNT = len(MANDATORY_TICKS)
TRAJECTORY_FRAME_COUNT = 25
FRAME_COUNT = MANDATORY_FRAME_COUNT + TRAJECTORY_FRAME_COUNT


@dataclass(frozen=True)
class GateFFrameSequenceConfig:
    profile_id: str
    seed_u64: str
    canonical_sha256: str


@dataclass(frozen=True)
class GateFFrame:
    id: str
    source: str
    parameter_ticks: tuple[int, int, int, int, int]

    def parameter_fractions(self) -> dict[str, Fraction]:
        return {
            parameter_id: Fraction(tick, PARAMETER_SCALE)
            for parameter_id, tick in zip(PARAMETER_ORDER, self.parameter_ticks, strict=True)
        }

    def parameter_document(self) -> dict[str, int | float]:
        document: dict[str, int | float] = {}
        for parameter_id, tick in zip(PARAMETER_ORDER, self.parameter_ticks, strict=True):
            value = Fraction(tick, PARAMETER_SCALE)
            document[parameter_id] = value.numerator if value.denominator == 1 else float(value)
        return document


@dataclass(frozen=True)
class GateFFrameSequence:
    profile_id: str
    algorithm_id: str
    seed_u64: str
    sha256: str
    frames: tuple[GateFFrame, ...]


def parse_gate_f_frame_sequence_config(value: object) -> GateFFrameSequenceConfig:
    keys = {"format", "format_version", "profile_id", "seed_u64"}
    if not isinstance(value, dict) or set(value) != keys:
        raise StageContractError("frame-sequence config has unknown or missing fields")
    seed = value["seed_u64"]
    if (
        value["format"] != "oneclick2d.gate-f-frame-sequence-config"
        or value["format_version"] != "0.1.0"
        or value["profile_id"] != PROFILE_ID
        or not isinstance(seed, str)
        or len(seed) != 20
        or not seed.isascii()
        or not seed.isdigit()
        or int(seed) > 18_446_744_073_709_551_615
    ):
        raise StageContractError("unsupported frame-sequence config")
    return GateFFrameSequenceConfig(PROFILE_ID, seed, hashlib.sha256(canonical_json_bytes(value)).hexdigest())


def _waypoint(seed: int, ordinal: int) -> tuple[int, int, int, int, int]:
    values: list[int] = []
    for parameter_id, (minimum, maximum) in zip(PARAMETER_ORDER, PARAMETER_RANGES, strict=True):
        payload = (
            b"oneclick2d.gate-f.frame-sequence.waypoint.v1\0"
            + seed.to_bytes(8, "big")
            + ordinal.to_bytes(2, "big")
            + b"\0"
            + parameter_id.encode("ascii")
        )
        raw = int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")
        slot_count = (maximum - minimum) // 4 + 1
        values.append(minimum + 4 * (raw % slot_count))
    return tuple(values)  # type: ignore[return-value]


def _trajectory(seed: int) -> tuple[GateFFrame, ...]:
    neutral = MANDATORY_TICKS[0][1]
    points = (neutral, *(_waypoint(seed, ordinal) for ordinal in range(1, 6)), neutral)
    ticks = [neutral]
    for start, end in zip(points, points[1:]):
        for step in range(1, 5):
            ticks.append(tuple((left * (4 - step) + right * step) // 4 for left, right in zip(start, end, strict=True)))
    return tuple(GateFFrame(f"trajectory.{index:03d}", "seeded-trajectory", value) for index, value in enumerate(ticks))


def build_gate_f_frame_sequence(config: GateFFrameSequenceConfig) -> GateFFrameSequence:
    mandatory = tuple(GateFFrame(frame_id, "mandatory", ticks) for frame_id, ticks in MANDATORY_TICKS)
    frames = mandatory + _trajectory(int(config.seed_u64))
    if len(frames) != FRAME_COUNT:
        raise StageContractError("frame-sequence cardinality is invalid")
    for frame in frames:
        if any(not minimum <= tick <= maximum for tick, (minimum, maximum) in zip(frame.parameter_ticks, PARAMETER_RANGES, strict=True)):
            raise StageContractError("frame-sequence parameter is outside the registered range")
    identity = {
        "profile_id": PROFILE_ID,
        "algorithm_id": ALGORITHM_ID,
        "seed_u64": config.seed_u64,
        "parameter_order": list(PARAMETER_ORDER),
        "parameter_scale": PARAMETER_SCALE,
        "frames": [
            {"index": index, "id": frame.id, "source": frame.source, "parameter_ticks": list(frame.parameter_ticks)}
            for index, frame in enumerate(frames)
        ],
    }
    digest = digest_framed("oneclick2d.gate-f.frame-sequence.v1", (canonical_json_bytes(identity),))
    return GateFFrameSequence(PROFILE_ID, ALGORITHM_ID, config.seed_u64, digest, frames)
