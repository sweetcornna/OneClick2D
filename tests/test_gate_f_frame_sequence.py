from __future__ import annotations

import unittest
from unittest.mock import patch

from spikes.gate_f_runner.contracts import StageContractError
from spikes.gate_f_runner.frame_sequence import (
    FRAME_COUNT,
    MANDATORY_TICKS,
    PARAMETER_RANGES,
    TRAJECTORY_FRAME_COUNT,
    build_gate_f_frame_sequence,
    parse_gate_f_frame_sequence_config,
)


def sequence_config(seed: str = "00000000000000000042") -> dict[str, object]:
    return {
        "format": "oneclick2d.gate-f-frame-sequence-config",
        "format_version": "0.1.0",
        "profile_id": "oc2d.spike.gate-f-frame-sequence.v1",
        "seed_u64": seed,
    }


class GateFFrameSequenceTests(unittest.TestCase):
    def test_seed_42_sequence_has_fixed_prefix_and_trajectory(self) -> None:
        sequence = build_gate_f_frame_sequence(parse_gate_f_frame_sequence_config(sequence_config()))
        self.assertEqual(FRAME_COUNT, len(sequence.frames))
        self.assertEqual(MANDATORY_TICKS, tuple((frame.id, frame.parameter_ticks) for frame in sequence.frames[:12]))
        trajectory = sequence.frames[12:]
        self.assertEqual(TRAJECTORY_FRAME_COUNT, len(trajectory))
        self.assertEqual([f"trajectory.{index:03d}" for index in range(25)], [frame.id for frame in trajectory])
        self.assertEqual(MANDATORY_TICKS[0][1], trajectory[0].parameter_ticks)
        self.assertEqual(MANDATORY_TICKS[0][1], trajectory[-1].parameter_ticks)
        self.assertTrue(all(frame.source == "seeded-trajectory" for frame in trajectory))
        for frame in sequence.frames:
            self.assertTrue(all(minimum <= tick <= maximum for tick, (minimum, maximum) in zip(frame.parameter_ticks, PARAMETER_RANGES, strict=True)))
        self.assertEqual("2b9c10df115be77ff3eb17807329a016d1350a3d387ea47bdaab2dd409b0ea8c", sequence.sha256)

    def test_seed_changes_only_seeded_trajectory(self) -> None:
        first = build_gate_f_frame_sequence(parse_gate_f_frame_sequence_config(sequence_config()))
        second = build_gate_f_frame_sequence(parse_gate_f_frame_sequence_config(sequence_config("00000000000000000043")))
        self.assertEqual(first.frames[:12], second.frames[:12])
        self.assertNotEqual(first.frames[12:], second.frames[12:])
        self.assertNotEqual(first.sha256, second.sha256)

    def test_config_is_strict_and_seed_is_unsigned_u64(self) -> None:
        for value in (
            {},
            {**sequence_config(), "extra": True},
            {**sequence_config(), "format_version": "0.2.0"},
            {**sequence_config(), "seed_u64": "1"},
            {**sequence_config(), "seed_u64": "18446744073709551616"},
        ):
            with self.assertRaises(StageContractError):
                parse_gate_f_frame_sequence_config(value)

    def test_generation_does_not_import_pillow(self) -> None:
        original_import = __import__

        def guarded_import(name: str, *args: object, **kwargs: object) -> object:
            if name == "PIL" or name.startswith("PIL."):
                raise ImportError
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=guarded_import):
            build_gate_f_frame_sequence(parse_gate_f_frame_sequence_config(sequence_config()))


if __name__ == "__main__":
    unittest.main()
