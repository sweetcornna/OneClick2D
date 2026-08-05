from __future__ import annotations

import base64
import hashlib
import io
import random
import struct
import tempfile
import unittest
from pathlib import Path

from spikes.gate_f_runner.contracts import StageContractError
from spikes.gate_f_runner.model_psd_validator import (
    _FileCursor,
    _encode_packbits,
    _parse_rle_row,
    validate_model_psd,
)
from spikes.gate_f_runner.psd_reader import parse_layered_psd

# Purpose-created 2x2 PSD emitted by psd-tools 1.14.2 with RGBA pixel
# layers named "face" and "mouth". It contains no user or artistic content.
_PSD_TOOLS_FIXTURE = base64.b64decode(
    "OEJQUwABAAAAAAAAAAQAAAACAAAAAgAIAAMAAAAAAAAAXjhCSU0EIQAAAAAAUQAAAAEBAAAAEABwAHMAZAAtAHQAbwBvAGwAcwAgADEALgAxADQALgAyAAAAEABwAHMAZAAtAHQAbwBvAGwAcwAgADEALgAxADQALgAyAAAAAQAAAAFgAAABWAACAAAAAAAAAAAAAAABAAAAAQAF//8AAAAGAAAAAAAGAAEAAAAGAAIAAAAG//4AAAAGOEJJTW5vcm3/AAgAAAAATAAAABQAAAAAAAAAAAAAAAEAAAABAAAAAAAAACgAAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//BGZhY2UAAAAAAAABAAAAAQAAAAIAAAACAAX//wAAAAYAAAAAAAYAAQAAAAYAAgAAAAb//gAAAAY4QklNbm9ybf8ACAAAAABMAAAAFAAAAAEAAAABAAAAAgAAAAIAAAAAAAAAKAAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8FbW91dGgAAAABAAIA/wABAAIA/wABAAIAAAABAAIAAAABAAIA/wABAAIA/wABAAIAAAABAAIAAAABAAIAAAABAAIA/wAAAAAAAAAA/wAAAAAAAAAAAAAA/////w=="
)


def _reference_encode_packbits(data: bytes) -> bytes:
    """Pre-optimization encoder, retained only as the equivalence oracle."""
    result = bytearray()
    length = len(data)
    if length == 1:
        return b"\0" + data
    index = 0
    scan = 0
    while index < length:
        if scan + 1 < length and data[scan] == data[scan + 1]:
            while scan < length:
                if scan - index >= 127 or scan + 1 >= length or data[scan] != data[scan + 1]:
                    break
                scan += 1
            result.extend((256 - (scan - index), data[index]))
            index = scan = scan + 1
        else:
            while scan < length:
                if scan - index >= 127:
                    break
                if scan + 1 < length and data[scan] != data[scan + 1]:
                    pass
                elif (
                    (scan + 2 == length or 127 - (scan - index) <= 2)
                    and scan + 1 != length
                    and data[scan] == data[scan + 1]
                ):
                    break
                elif scan + 2 < length and data[scan] == data[scan + 1] == data[scan + 2]:
                    break
                scan += 1
            result.append(scan - index - 1)
            result.extend(data[index:scan])
            index = scan
    return bytes(result)


def _decode_packbits(encoded: bytes) -> bytes:
    """Expose the decoding logic from _parse_rle_row for round-trip checks."""
    offset = 0
    decoded = bytearray()
    while offset < len(encoded):
        control = encoded[offset]
        offset += 1
        if control <= 127:
            count = control + 1
            if offset + count > len(encoded):
                raise AssertionError("truncated literal in test corpus")
            decoded.extend(encoded[offset : offset + count])
            offset += count
        elif control == 128:
            raise AssertionError("noncanonical packet in test corpus")
        else:
            count = 257 - control
            if offset >= len(encoded):
                raise AssertionError("truncated repeat in test corpus")
            decoded.extend((encoded[offset],) * count)
            offset += 1
    return bytes(decoded)


def _adjacent_distinct(length: int, offset: int = 0) -> bytes:
    return bytes((index + offset) % 251 for index in range(length))


def _packbits_equivalence_corpus():
    for length in range(9):
        for value in range(1 << length):
            yield f"binary-exhaustive-{length}-{value}", bytes(
                (value >> shift) & 1 for shift in range(length)
            )

    boundary_lengths = (1, 2, 3, 126, 127, 128, 129, 254, 255, 256)
    for length in boundary_lengths:
        tail_length = min(3, length)
        yield f"same-{length}", b"\x8d" * length
        yield f"distinct-{length}", bytes(range(length))
        yield f"alternating-{length}", (b"\x00\x01" * ((length + 1) // 2))[:length]
        yield f"tail-run-{length}", _adjacent_distinct(length - tail_length) + b"\xff" * tail_length

    mixed_prefix = b"\x80" * 260 + _adjacent_distinct(73) + b"\xfe\xfe"
    mixed_prefix += _adjacent_distinct(171, 17) + b"\x7f" * 389
    run_boundaries = _adjacent_distinct(17) + b"\xfb" * 127 + _adjacent_distinct(31, 23)
    run_boundaries += b"\xfc" * 128 + _adjacent_distinct(47, 41) + b"\xfd" * 129
    yield "width-1280-same", b"\x00" * 1280
    yield "width-1280-distinct", _adjacent_distinct(1280)
    yield "width-1280-alternating", b"\x00\x01" * 640
    yield "width-1280-mixed", mixed_prefix + _adjacent_distinct(1280 - len(mixed_prefix), 29)
    yield "width-1280-run-boundaries", run_boundaries + _adjacent_distinct(
        1280 - len(run_boundaries), 53
    )

    random_source = random.Random(0x5EED_CAFE)
    for alphabet_size in (2, 4, 16, 256):
        for case in range(1000):
            length = random_source.randrange(0, 1537)
            yield f"random-{alphabet_size}-{case}", bytes(
                random_source.randrange(alphabet_size) for _ in range(length)
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

    def test_packbits_encoder_is_byte_exact_with_reference(self) -> None:
        for label, data in _packbits_equivalence_corpus():
            encoded = _encode_packbits(data)
            self.assertEqual(_reference_encode_packbits(data), encoded, label)
            self.assertEqual(data, _decode_packbits(encoded), label)

            stream = io.BytesIO(encoded)
            cursor = _FileCursor(stream, 0, len(encoded))
            _parse_rle_row(cursor, len(data))

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
