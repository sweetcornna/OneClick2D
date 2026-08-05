"""Standard-library PNG/JPEG codecs and raster primitives."""

from __future__ import annotations

import struct
import unittest
import zlib

from oneclick2d.errors import ContractError, IntakeRejected, ResourceLimitError
from oneclick2d.raster.image import Bounds, Image, Mask
from oneclick2d.raster.jpeg import decode_jpeg
from oneclick2d.raster.png import (
    PNG_SIGNATURE,
    decode_png,
    encode_gray_png,
    encode_rgba_png,
)


def _gradient(width: int, height: int) -> bytes:
    data = bytearray()
    for y in range(height):
        for x in range(width):
            data.extend(bytes(((x * 9) % 256, (y * 7) % 256, (x + y) % 256, (x * 3 + y) % 256)))
    return bytes(data)


class PngRoundTripTests(unittest.TestCase):
    def test_rgba_round_trip_is_lossless(self) -> None:
        payload = _gradient(23, 17)
        width, height, decoded = decode_png(encode_rgba_png(23, 17, payload))
        self.assertEqual((width, height), (23, 17))
        self.assertEqual(bytes(decoded), payload)

    def test_encoding_is_deterministic(self) -> None:
        payload = _gradient(16, 16)
        self.assertEqual(encode_rgba_png(16, 16, payload), encode_rgba_png(16, 16, payload))

    def test_grayscale_round_trip_and_srgb_chunk(self) -> None:
        grey = bytes((value * 3) % 256 for value in range(32 * 8))
        encoded = encode_gray_png(32, 8, grey)
        self.assertIn(b"sRGB", encoded)
        width, height, decoded = decode_png(encoded)
        self.assertEqual((width, height), (32, 8))
        self.assertEqual(bytes(decoded[0::4]), grey)
        self.assertEqual(set(decoded[3::4]), {255})

    def test_all_five_scanline_filters_decode_to_the_same_pixels(self) -> None:
        """Encode one image five times, once per filter type, by applying the
        filter equations directly. All five must decode back to the original.
        """
        width, height, channels = 8, 4, 4
        stride = width * channels
        rows = [
            bytes(((x * 11 + y * 37) % 256) for x in range(stride))
            for y in range(height)
        ]
        expected = b"".join(rows)

        for filter_type in range(5):
            with self.subTest(filter_type=filter_type):
                raw = bytearray()
                previous = bytes(stride)
                for row in rows:
                    raw.append(filter_type)
                    for index in range(stride):
                        left = row[index - channels] if index >= channels else 0
                        upper = previous[index]
                        upper_left = previous[index - channels] if index >= channels else 0
                        if filter_type == 0:
                            predictor = 0
                        elif filter_type == 1:
                            predictor = left
                        elif filter_type == 2:
                            predictor = upper
                        elif filter_type == 3:
                            predictor = (left + upper) >> 1
                        else:
                            estimate = left + upper - upper_left
                            deltas = (
                                (abs(estimate - left), left),
                                (abs(estimate - upper), upper),
                                (abs(estimate - upper_left), upper_left),
                            )
                            # Ties must resolve left, then upper, then upper-left.
                            if deltas[0][0] <= deltas[1][0] and deltas[0][0] <= deltas[2][0]:
                                predictor = left
                            elif deltas[1][0] <= deltas[2][0]:
                                predictor = upper
                            else:
                                predictor = upper_left
                        raw.append((row[index] - predictor) & 0xFF)
                    previous = row

                header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
                chunks = [PNG_SIGNATURE]
                for name, body in (
                    (b"IHDR", header),
                    (b"IDAT", zlib.compress(bytes(raw), 9)),
                    (b"IEND", b""),
                ):
                    chunks.append(struct.pack(">I", len(body)) + name + body)
                    chunks.append(struct.pack(">I", zlib.crc32(name + body) & 0xFFFFFFFF))
                _, _, decoded = decode_png(b"".join(chunks))
                self.assertEqual(bytes(decoded), expected)

    def test_payload_length_must_match_declared_size(self) -> None:
        with self.assertRaises(ContractError):
            encode_rgba_png(4, 4, b"\x00" * 10)


class PngRejectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.valid = encode_rgba_png(16, 16, _gradient(16, 16))

    def _patch_header(self, **fields: int) -> bytes:
        width, height, depth, colour, compression, filt, interlace = struct.unpack(
            ">IIBBBBB", self.valid[16:29]
        )
        values = {
            "width": width,
            "height": height,
            "depth": depth,
            "colour": colour,
            "compression": compression,
            "filt": filt,
            "interlace": interlace,
        }
        values.update(fields)
        header = struct.pack(
            ">IIBBBBB",
            values["width"],
            values["height"],
            values["depth"],
            values["colour"],
            values["compression"],
            values["filt"],
            values["interlace"],
        )
        chunk = struct.pack(">I", 13) + b"IHDR" + header
        chunk += struct.pack(">I", zlib.crc32(b"IHDR" + header) & 0xFFFFFFFF)
        return self.valid[:8] + chunk + self.valid[8 + 25 :]

    def test_bad_signature_is_rejected(self) -> None:
        with self.assertRaises(IntakeRejected):
            decode_png(b"not a png" + b"\x00" * 40)

    def test_truncation_is_rejected(self) -> None:
        with self.assertRaises((IntakeRejected, ResourceLimitError)):
            decode_png(self.valid[:40])

    def test_crc_tamper_is_rejected(self) -> None:
        tampered = bytearray(self.valid)
        tampered[30] ^= 0xFF
        with self.assertRaises(IntakeRejected):
            decode_png(bytes(tampered))

    def test_trailing_bytes_after_iend_are_rejected(self) -> None:
        with self.assertRaises(IntakeRejected):
            decode_png(self.valid + b"trailing")

    def test_adam7_interlacing_is_rejected(self) -> None:
        with self.assertRaises(IntakeRejected):
            decode_png(self._patch_header(interlace=1))

    def test_unsupported_bit_depth_is_rejected(self) -> None:
        with self.assertRaises(IntakeRejected):
            decode_png(self._patch_header(depth=4))

    def test_unsupported_colour_type_is_rejected(self) -> None:
        with self.assertRaises(IntakeRejected):
            decode_png(self._patch_header(colour=7))

    def test_unsupported_compression_or_filter_method_is_rejected(self) -> None:
        for field in ("compression", "filt"):
            with self.subTest(field=field), self.assertRaises(IntakeRejected):
                decode_png(self._patch_header(**{field: 1}))

    def test_unknown_critical_chunk_is_rejected(self) -> None:
        body = b"x"
        chunk = struct.pack(">I", len(body)) + b"ZZZZ" + body
        chunk += struct.pack(">I", zlib.crc32(b"ZZZZ" + body) & 0xFFFFFFFF)
        with self.assertRaises(IntakeRejected):
            decode_png(self.valid[: 8 + 25] + chunk + self.valid[8 + 25 :])

    def test_ancillary_chunk_is_tolerated(self) -> None:
        body = b"k\x00v"
        chunk = struct.pack(">I", len(body)) + b"tEXt" + body
        chunk += struct.pack(">I", zlib.crc32(b"tEXt" + body) & 0xFFFFFFFF)
        width, height, decoded = decode_png(self.valid[: 8 + 25] + chunk + self.valid[8 + 25 :])
        self.assertEqual((width, height), (16, 16))

    def test_declared_size_larger_than_payload_is_rejected(self) -> None:
        with self.assertRaises((IntakeRejected, ResourceLimitError)):
            decode_png(self._patch_header(width=16000, height=16000))

    def test_inflate_bomb_is_bounded(self) -> None:
        huge = zlib.compress(b"\x00" * (17 * 16 * 8), 9)
        tampered = self.valid[: 8 + 25] + struct.pack(">I", len(huge)) + b"IDAT" + huge
        tampered += struct.pack(">I", zlib.crc32(b"IDAT" + huge) & 0xFFFFFFFF)
        tampered += self.valid[-12:]
        with self.assertRaises((IntakeRejected, ResourceLimitError)):
            decode_png(tampered)

    def test_signature_only_input_is_rejected(self) -> None:
        with self.assertRaises(IntakeRejected):
            decode_png(PNG_SIGNATURE)


class JpegTests(unittest.TestCase):
    def test_non_jpeg_is_rejected(self) -> None:
        with self.assertRaises(IntakeRejected):
            decode_jpeg(b"\x00\x01" + b"\xff" * 40)

    def test_truncated_jpeg_is_rejected(self) -> None:
        with self.assertRaises(IntakeRejected):
            decode_jpeg(b"\xff\xd8\xff\xc0\x00\x0b")

    def test_empty_input_is_rejected(self) -> None:
        with self.assertRaises(IntakeRejected):
            decode_jpeg(b"")

    def test_progressive_profile_is_rejected(self) -> None:
        # SOF2 marker: progressive DCT is outside the supported profile and must
        # be refused rather than partially decoded.
        payload = b"\xff\xd8" + b"\xff\xc2" + struct.pack(">H", 11)
        payload += struct.pack(">BHHB", 8, 16, 16, 1) + bytes((1, 0x11, 0))
        with self.assertRaises(IntakeRejected):
            decode_jpeg(payload)


class MaskTests(unittest.TestCase):
    def test_count_and_bounds_track_coverage(self) -> None:
        mask = Mask(8, 8)
        mask.data[2 * 8 + 3] = 200
        mask.data[5 * 8 + 6] = 40
        self.assertEqual(mask.count_at_least(31), 2)
        bounds = mask.bounds_at_least(31)
        self.assertEqual((bounds.x, bounds.y, bounds.width, bounds.height), (3, 2, 4, 4))

    def test_empty_mask_has_empty_bounds(self) -> None:
        self.assertTrue(Mask(4, 4).bounds_at_least(0).empty)

    def test_set_operations(self) -> None:
        left = Mask(4, 1, bytearray((255, 255, 0, 0)))
        right = Mask(4, 1, bytearray((0, 255, 255, 0)))
        self.assertEqual(bytes(left.intersect(right).data), bytes((0, 255, 0, 0)))
        self.assertEqual(bytes(left.union(right).data), bytes((255, 255, 255, 0)))
        self.assertEqual(bytes(left.subtract(right).data), bytes((255, 0, 0, 0)))

    def test_geometry_mismatch_is_rejected(self) -> None:
        with self.assertRaises(ContractError):
            Mask(4, 1).intersect(Mask(2, 1))

    def test_dilate_grows_only_by_the_radius(self) -> None:
        mask = Mask(9, 9)
        mask.data[4 * 9 + 4] = 255
        grown = mask.dilate(1)
        self.assertEqual(grown.count_at_least(0), 9)
        self.assertEqual(mask.dilate(0).count_at_least(0), 1)

    def test_feather_produces_a_bounded_ramp(self) -> None:
        mask = Mask(9, 1, bytearray((0, 0, 0, 255, 255, 255, 0, 0, 0)))
        feathered = mask.feather(1)
        self.assertLessEqual(max(feathered.data), 255)
        self.assertGreater(feathered.data[2], 0)


class ImageTests(unittest.TestCase):
    def test_alpha_mask_and_replacement(self) -> None:
        image = Image(2, 1, bytearray((10, 20, 30, 128, 40, 50, 60, 255)))
        self.assertEqual(bytes(image.alpha_mask().data), bytes((128, 255)))
        replaced = image.with_alpha_mask(Mask(2, 1, bytearray((0, 64))))
        self.assertEqual(bytes(replaced.alpha_mask().data), bytes((0, 64)))

    def test_multiply_alpha_mask_scales_coverage(self) -> None:
        image = Image(1, 1, bytearray((10, 20, 30, 255)))
        scaled = image.multiply_alpha_mask(Mask(1, 1, bytearray((128,))))
        self.assertEqual(scaled.pixel(0, 0)[3], 128)

    def test_opaque_source_over_replaces_the_base(self) -> None:
        base = Image(1, 1, bytearray((0, 0, 0, 255)))
        top = Image(1, 1, bytearray((10, 20, 30, 255)))
        self.assertEqual(base.composite_over(top).pixel(0, 0), (10, 20, 30, 255))

    def test_transparent_source_over_preserves_the_base(self) -> None:
        base = Image(1, 1, bytearray((10, 20, 30, 255)))
        top = Image(1, 1, bytearray((99, 99, 99, 0)))
        self.assertEqual(base.composite_over(top).pixel(0, 0), (10, 20, 30, 255))

    def test_composite_over_transparent_base_keeps_source_colour(self) -> None:
        base = Image(1, 1)
        top = Image(1, 1, bytearray((10, 20, 30, 128)))
        self.assertEqual(base.composite_over(top).pixel(0, 0), (10, 20, 30, 128))

    def test_crop_respects_bounds(self) -> None:
        image = Image(4, 4, bytearray(_gradient(4, 4)))
        cropped = image.crop(Bounds(1, 1, 2, 2))
        self.assertEqual(cropped.size, (2, 2))
        self.assertEqual(cropped.pixel(0, 0), image.pixel(1, 1))

    def test_crop_outside_the_canvas_is_rejected(self) -> None:
        with self.assertRaises(ContractError):
            Image(4, 4).crop(Bounds(3, 3, 4, 4))

    def test_channel_planes_split_and_preserve_order(self) -> None:
        image = Image(2, 1, bytearray((1, 2, 3, 4, 5, 6, 7, 8)))
        self.assertEqual(image.channel_planes(), (b"\x01\x05", b"\x02\x06", b"\x03\x07", b"\x04\x08"))

    def test_oversized_dimensions_are_rejected(self) -> None:
        with self.assertRaises(ContractError):
            Image(0, 10)
        with self.assertRaises(ContractError):
            Image(10, 100000)

    def test_payload_length_mismatch_is_rejected(self) -> None:
        with self.assertRaises(ContractError):
            Image(2, 2, bytearray(3))


if __name__ == "__main__":
    unittest.main()
