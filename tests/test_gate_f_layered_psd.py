from __future__ import annotations

import sys
import unittest

from spikes.gate_f_runner.contracts import StageContractError
from spikes.gate_f_runner.psd_reader import parse_layered_psd
from spikes.gate_f_runner.psd_writer import PsdLayer, write_layered_psd


def rgba(width: int, height: int, color: tuple[int, int, int, int]) -> bytes:
    return bytes(color) * (width * height)


class LayeredPsdTests(unittest.TestCase):
    def test_writer_and_independent_reader_roundtrip_layers(self) -> None:
        layers = (
            PsdLayer(1, "Read Me 说明", 0, 0, 4, 3, rgba(4, 3, (0, 0, 0, 0)), visible=False),
            PsdLayer(2, "Eye 左", 1, 0, 2, 1, rgba(2, 1, (255, 0, 0, 128))),
            PsdLayer(3, "Eye Fill", 1, 1, 2, 1, rgba(2, 1, (0, 255, 0, 255))),
            PsdLayer(4, "Source Reference", 0, 0, 4, 3, rgba(4, 3, (0, 0, 255, 255)), visible=False, locked=True),
        )
        merged = rgba(4, 3, (12, 34, 56, 255))
        first = write_layered_psd(4, 3, layers, merged)
        second = write_layered_psd(4, 3, layers, merged)
        self.assertEqual(first, second)
        parsed = parse_layered_psd(first)
        self.assertEqual((4, 3), (parsed.width, parsed.height))
        self.assertEqual([layer.name for layer in layers], [layer.name for layer in parsed.layers])
        self.assertEqual([layer.rgba for layer in layers], [layer.rgba for layer in parsed.layers])
        self.assertFalse(parsed.layers[0].visible)
        self.assertTrue(parsed.layers[-1].locked)
        self.assertEqual(merged, parsed.merged_rgba)

    def test_reader_does_not_import_writer(self) -> None:
        self.assertNotIn("spikes.gate_f_runner.psd_writer", sys.modules.get("spikes.gate_f_runner.psd_reader").__dict__.values())
        import spikes.gate_f_runner.psd_reader as reader
        self.assertFalse(hasattr(reader, "PsdLayer"))

    def test_writer_preflights_aggregate_size_before_channel_allocation(self) -> None:
        class SizedOnly:
            def __len__(self) -> int:
                return 4096 * 4096 * 4

        shared = b""
        oversized = tuple(PsdLayer(index + 1, f"Layer {index}", 0, 0, 4096, 4096, shared) for index in range(4))
        with self.assertRaisesRegex(StageContractError, "file-size limit"):
            write_layered_psd(4096, 4096, oversized, SizedOnly())  # type: ignore[arg-type]

    def test_writer_and_reader_fail_closed(self) -> None:
        with self.assertRaises(StageContractError):
            write_layered_psd(4, 3, (), rgba(4, 3, (0, 0, 0, 0)))
        layers = (
            PsdLayer(1, "A", 0, 0, 1, 1, rgba(1, 1, (1, 2, 3, 4))),
            PsdLayer(2, "B", 0, 0, 1, 1, rgba(1, 1, (5, 6, 7, 8))),
        )
        data = write_layered_psd(1, 1, layers, rgba(1, 1, (0, 0, 0, 0)))
        for broken in (b"BAD!" + data[4:], data[:-1], data + b"x"):
            with self.assertRaises(StageContractError):
                parse_layered_psd(broken)


if __name__ == "__main__":
    unittest.main()
