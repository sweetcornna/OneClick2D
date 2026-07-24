from __future__ import annotations

import base64
import hashlib
import io
import struct
import tempfile
import unittest
from pathlib import Path

from spikes.gate_f_runner.contracts import StageContractError
from spikes.gate_f_runner.model_psd_validator import _FileCursor, _parse_rle_row, validate_model_psd
from spikes.gate_f_runner.psd_reader import parse_layered_psd

# Purpose-created 2x2 PSD emitted by psd-tools 1.14.2 with RGBA pixel
# layers named "face" and "mouth". It contains no user or artistic content.
_PSD_TOOLS_FIXTURE = base64.b64decode(
    "OEJQUwABAAAAAAAAAAQAAAACAAAAAgAIAAMAAAAAAAAAXjhCSU0EIQAAAAAAUQAAAAEBAAAAEABwAHMAZAAtAHQAbwBvAGwAcwAgADEALgAxADQALgAyAAAAEABwAHMAZAAtAHQAbwBvAGwAcwAgADEALgAxADQALgAyAAAAAQAAAAFgAAABWAACAAAAAAAAAAAAAAABAAAAAQAF//8AAAAGAAAAAAAGAAEAAAAGAAIAAAAG//4AAAAGOEJJTW5vcm3/AAgAAAAATAAAABQAAAAAAAAAAAAAAAEAAAABAAAAAAAAACgAAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//BGZhY2UAAAAAAAABAAAAAQAAAAIAAAACAAX//wAAAAYAAAAAAAYAAQAAAAYAAgAAAAb//gAAAAY4QklNbm9ybf8ACAAAAABMAAAAFAAAAAEAAAABAAAAAgAAAAIAAAAAAAAAKAAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8FbW91dGgAAAABAAIA/wABAAIA/wABAAIAAAABAAIAAAABAAIA/wABAAIA/wABAAIAAAABAAIAAAABAAIAAAABAAIA/wAAAAAAAAAA/wAAAAAAAAAAAAAA/////w=="
)


class GateFModelPsdTests(unittest.TestCase):
    def _validate(self, data: bytes):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "model.psd"
            path.write_bytes(data)
            return validate_model_psd(path)

    def test_accepts_pinned_psd_tools_profile(self) -> None:
        self.assertEqual(
            "d9933abee7d2d91b5acd6c44e5815ed7f358bf2077a01f7f19009bab888f5cab",
            hashlib.sha256(_PSD_TOOLS_FIXTURE).hexdigest(),
        )
        parsed = self._validate(_PSD_TOOLS_FIXTURE)
        self.assertEqual((2, 2, 4), (parsed.width, parsed.height, parsed.document_channels))
        self.assertEqual(("face", "mouth"), tuple(layer.name for layer in parsed.layers))
        self.assertTrue(all(layer.has_user_mask for layer in parsed.layers))
        self.assertEqual(len(_PSD_TOOLS_FIXTURE), parsed.byte_length)
        self.assertEqual(hashlib.sha256(_PSD_TOOLS_FIXTURE).hexdigest(), parsed.sha256)

    def test_upstream_profile_stays_separate_from_project_psd(self) -> None:
        with self.assertRaises(StageContractError):
            parse_layered_psd(_PSD_TOOLS_FIXTURE)

    def test_rejects_truncation_at_every_boundary(self) -> None:
        for size in range(len(_PSD_TOOLS_FIXTURE)):
            with self.subTest(size=size), self.assertRaises(StageContractError):
                self._validate(_PSD_TOOLS_FIXTURE[:size])

    def test_rejects_trailing_data(self) -> None:
        with self.assertRaisesRegex(StageContractError, "merged image is invalid"):
            self._validate(_PSD_TOOLS_FIXTURE + b"\0")

    def test_rejects_missing_required_semantic_name(self) -> None:
        modified = _PSD_TOOLS_FIXTURE.replace(b"\x05mouth", b"\x05noses", 1)
        with self.assertRaisesRegex(StageContractError, "semantic inventory is incomplete"):
            self._validate(modified)

    def test_rejects_duplicate_semantic_names(self) -> None:
        modified = _PSD_TOOLS_FIXTURE.replace(b"\x05mouth", b"\x04face\0", 1)
        with self.assertRaises(StageContractError):
            self._validate(modified)

    def test_rejects_noncanonical_rle_packet(self) -> None:
        modified = bytearray(_PSD_TOOLS_FIXTURE)
        modified[0x1A2] = 0x80
        with self.assertRaises(StageContractError):
            self._validate(bytes(modified))

    def test_rejects_alternative_packbits_encoding(self) -> None:
        for encoded, width in ((b"\x01aa", 2), (b"\0a\0b", 2)):
            with self.subTest(encoded=encoded):
                stream = io.BytesIO(encoded)
                cursor = _FileCursor(stream, 0, len(encoded))
                with self.assertRaisesRegex(StageContractError, "noncanonical"):
                    _parse_rle_row(cursor, width)

    def test_rejects_misaligned_layer_info_length(self) -> None:
        modified = bytearray(_PSD_TOOLS_FIXTURE)
        layer_mask_length = struct.unpack(">I", modified[0x7E:0x82])[0]
        layer_info_length = struct.unpack(">I", modified[0x82:0x86])[0]
        modified[0x7E:0x82] = struct.pack(">I", layer_mask_length + 1)
        modified[0x82:0x86] = struct.pack(">I", layer_info_length + 1)
        insert_at = 0x82 + 4 + layer_info_length
        modified[insert_at:insert_at] = b"\0"
        with self.assertRaises(StageContractError):
            self._validate(bytes(modified))


if __name__ == "__main__":
    unittest.main()
