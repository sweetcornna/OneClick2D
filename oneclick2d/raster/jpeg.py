"""Bounded standard-library baseline JPEG decoder.

``FR-001`` requires accepting static PNG and JPEG. This decoder covers the
baseline sequential DCT profile (SOF0) plus the extended sequential Huffman
profile (SOF1) in greyscale and YCbCr, with 4:4:4 through 4:2:0 sampling.
Progressive (SOF2), arithmetic-coded, hierarchical, lossless, 12-bit and
CMYK/YCCK profiles are rejected with a stable reason code rather than being
partially decoded.

The decoder never trusts declared sizes: dimensions, component counts, sampling
factors, table indices and scan lengths are all bounded before allocation.
"""

from __future__ import annotations

import struct
from typing import Final

from ..errors import IntakeRejected, ResourceLimitError

MAX_JPEG_BYTES: Final[int] = 64 * 1024 * 1024
MAX_JPEG_DIMENSION: Final[int] = 16384
MAX_JPEG_PIXELS: Final[int] = 64 * 1024 * 1024
_ZIGZAG: Final[tuple[int, ...]] = (
    0, 1, 8, 16, 9, 2, 3, 10, 17, 24, 32, 25, 18, 11, 4, 5,
    12, 19, 26, 33, 40, 48, 41, 34, 27, 20, 13, 6, 7, 14, 21, 28,
    35, 42, 49, 56, 57, 50, 43, 36, 29, 22, 15, 23, 30, 37, 44, 51,
    58, 59, 52, 45, 38, 31, 39, 46, 53, 60, 61, 54, 47, 55, 62, 63,
)


def _rejected(message: str) -> IntakeRejected:
    return IntakeRejected(message, reason_code="INPUT_UNSUPPORTED")


class _Huffman:
    __slots__ = ("lookup",)

    def __init__(self, counts: bytes, symbols: bytes) -> None:
        self.lookup: dict[tuple[int, int], int] = {}
        code = 0
        index = 0
        for length in range(1, 17):
            for _ in range(counts[length - 1]):
                if index >= len(symbols):
                    raise _rejected("JPEG Huffman table is truncated")
                self.lookup[(length, code)] = symbols[index]
                index += 1
                code += 1
            code <<= 1
        if index != len(symbols):
            raise _rejected("JPEG Huffman table has unused symbols")


class _BitReader:
    __slots__ = ("data", "offset", "bits", "count")

    def __init__(self, data: bytes, offset: int) -> None:
        self.data = data
        self.offset = offset
        self.bits = 0
        self.count = 0

    def _fill(self) -> None:
        if self.offset >= len(self.data):
            # Pad with 1-bits at the end of the entropy-coded segment.
            self.bits = (self.bits << 8) | 0xFF
            self.count += 8
            return
        byte = self.data[self.offset]
        self.offset += 1
        if byte == 0xFF:
            if self.offset < len(self.data):
                marker = self.data[self.offset]
                if marker == 0x00:
                    self.offset += 1
                elif 0xD0 <= marker <= 0xD7:
                    raise _rejected("JPEG restart marker appeared mid-symbol")
                else:
                    # Marker reached: pad the rest of the segment.
                    self.offset = len(self.data)
                    byte = 0xFF
        self.bits = (self.bits << 8) | byte
        self.count += 8

    def bit(self) -> int:
        if self.count == 0:
            self._fill()
        self.count -= 1
        return (self.bits >> self.count) & 1

    def receive(self, length: int) -> int:
        value = 0
        for _ in range(length):
            value = (value << 1) | self.bit()
        return value

    def decode(self, table: _Huffman) -> int:
        code = 0
        for length in range(1, 17):
            code = (code << 1) | self.bit()
            symbol = table.lookup.get((length, code))
            if symbol is not None:
                return symbol
        raise _rejected("JPEG Huffman code is invalid")

    def align(self) -> None:
        self.bits = 0
        self.count = 0


def _extend(value: int, length: int) -> int:
    if length == 0:
        return 0
    return value if value >= (1 << (length - 1)) else value - (1 << length) + 1


def _idct_2d(block: list[int]) -> list[int]:
    """Separable integer IDCT using the AAN float constants at double precision."""
    import math

    output = [0] * 64
    cosines = _idct_2d._cosines  # type: ignore[attr-defined]
    for y in range(8):
        for x in range(8):
            total = 0.0
            for v in range(8):
                for u in range(8):
                    coefficient = block[v * 8 + u]
                    if coefficient:
                        total += coefficient * cosines[u][x] * cosines[v][y]
            value = int(math.floor(total / 4.0 + 128.5))
            output[y * 8 + x] = 0 if value < 0 else (255 if value > 255 else value)
    return output


def _build_cosines() -> list[list[float]]:
    import math

    table: list[list[float]] = []
    for u in range(8):
        scale = math.sqrt(0.5) if u == 0 else 1.0
        table.append([scale * math.cos((2 * x + 1) * u * math.pi / 16.0) for x in range(8)])
    return table


_idct_2d._cosines = _build_cosines()  # type: ignore[attr-defined]


def decode_jpeg(data: bytes) -> tuple[int, int, bytearray]:
    """Decode baseline/extended-sequential JPEG into ``(width, height, rgba)``."""
    if len(data) > MAX_JPEG_BYTES:
        raise ResourceLimitError("JPEG byte limit exceeded")
    if not data.startswith(b"\xff\xd8"):
        raise _rejected("JPEG signature is invalid")

    quant: dict[int, list[int]] = {}
    dc_tables: dict[int, _Huffman] = {}
    ac_tables: dict[int, _Huffman] = {}
    frame: dict[str, object] | None = None
    restart_interval = 0
    offset = 2

    while offset + 4 <= len(data):
        if data[offset] != 0xFF:
            raise _rejected("JPEG marker alignment is invalid")
        marker = data[offset + 1]
        offset += 2
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            continue
        if marker == 0xD9:
            break
        if offset + 2 > len(data):
            raise _rejected("JPEG segment header is truncated")
        (length,) = struct.unpack_from(">H", data, offset)
        if length < 2 or offset + length > len(data):
            raise _rejected("JPEG segment length is invalid")
        body = data[offset + 2 : offset + length]
        offset += length

        if marker == 0xDB:
            cursor = 0
            while cursor < len(body):
                precision_id = body[cursor]
                cursor += 1
                table_id = precision_id & 0x0F
                precision = precision_id >> 4
                if table_id > 3 or precision not in (0, 1):
                    raise _rejected("JPEG quantization table id is invalid")
                size = 128 if precision else 64
                if cursor + size > len(body):
                    raise _rejected("JPEG quantization table is truncated")
                if precision:
                    values = list(struct.unpack(">64H", body[cursor : cursor + 128]))
                else:
                    values = list(body[cursor : cursor + 64])
                cursor += size
                table = [0] * 64
                for index, value in enumerate(values):
                    table[_ZIGZAG[index]] = value
                quant[table_id] = table
        elif marker == 0xC4:
            cursor = 0
            while cursor < len(body):
                table_spec = body[cursor]
                cursor += 1
                table_class = table_spec >> 4
                table_id = table_spec & 0x0F
                if table_class > 1 or table_id > 3:
                    raise _rejected("JPEG Huffman table id is invalid")
                if cursor + 16 > len(body):
                    raise _rejected("JPEG Huffman counts are truncated")
                counts = body[cursor : cursor + 16]
                cursor += 16
                total = sum(counts)
                if total > 256 or cursor + total > len(body):
                    raise _rejected("JPEG Huffman symbol table is invalid")
                symbols = body[cursor : cursor + total]
                cursor += total
                built = _Huffman(counts, symbols)
                if table_class == 0:
                    dc_tables[table_id] = built
                else:
                    ac_tables[table_id] = built
        elif marker == 0xDD:
            if len(body) != 2:
                raise _rejected("JPEG restart interval segment is invalid")
            (restart_interval,) = struct.unpack(">H", body)
        elif marker in (0xC0, 0xC1):
            if frame is not None:
                raise _rejected("JPEG contains multiple frames")
            if len(body) < 6:
                raise _rejected("JPEG frame header is truncated")
            precision, height, width, components = struct.unpack(">BHHB", body[:6])
            if precision != 8:
                raise _rejected("JPEG sample precision is unsupported")
            if not 1 <= width <= MAX_JPEG_DIMENSION or not 1 <= height <= MAX_JPEG_DIMENSION:
                raise _rejected("JPEG dimensions are unsupported")
            if width * height > MAX_JPEG_PIXELS:
                raise ResourceLimitError("JPEG pixel limit exceeded")
            if components not in (1, 3):
                raise _rejected("JPEG component count is unsupported")
            if len(body) != 6 + components * 3:
                raise _rejected("JPEG frame component table is invalid")
            specs = []
            for index in range(components):
                identifier, sampling, table_id = body[6 + index * 3 : 9 + index * 3]
                horizontal, vertical = sampling >> 4, sampling & 0x0F
                if horizontal not in (1, 2) or vertical not in (1, 2) or table_id > 3:
                    raise _rejected("JPEG sampling factors are unsupported")
                specs.append(
                    {
                        "id": identifier,
                        "h": horizontal,
                        "v": vertical,
                        "quant": table_id,
                    }
                )
            frame = {"width": width, "height": height, "components": specs}
        elif marker in (0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            raise _rejected("JPEG compression profile is unsupported")
        elif marker == 0xDA:
            if frame is None:
                raise _rejected("JPEG scan precedes the frame header")
            if not body:
                raise _rejected("JPEG scan header is truncated")
            scan_components = body[0]
            specs = frame["components"]  # type: ignore[index]
            if scan_components != len(specs) or len(body) != 1 + scan_components * 2 + 3:
                raise _rejected("JPEG scan does not cover every component once")
            assignments: list[tuple[dict[str, object], int, int]] = []
            for index in range(scan_components):
                identifier, tables = body[1 + index * 2 : 3 + index * 2]
                match = next((spec for spec in specs if spec["id"] == identifier), None)
                if match is None:
                    raise _rejected("JPEG scan references an unknown component")
                dc_id, ac_id = tables >> 4, tables & 0x0F
                if dc_id not in dc_tables or ac_id not in ac_tables:
                    raise _rejected("JPEG scan references a missing Huffman table")
                assignments.append((match, dc_id, ac_id))
            return _decode_scan(data, offset, frame, assignments, quant, dc_tables, ac_tables, restart_interval)

    raise _rejected("JPEG contains no supported scan")


def _upsample(
    plane_info: tuple[bytearray, int, int],
    x_factor: int,
    y_factor: int,
    width: int,
    height: int,
) -> bytearray:
    """Upsample a chroma plane to full resolution.

    Uses the triangular filter libjpeg calls "fancy" upsampling, which weights
    the two nearest samples 3:1 along each subsampled axis. Nearest-neighbour
    replication differs from every mainstream decoder at sharp chroma edges, so
    the triangular filter is what keeps decoded pixels interoperable.
    """
    plane, plane_width, plane_height = plane_info
    if x_factor == 1 and y_factor == 1:
        out = bytearray(width * height)
        for y in range(height):
            source = y * plane_width
            out[y * width : (y + 1) * width] = plane[source : source + width]
        return out

    # Chroma sample coordinate for output column x is (x + 0.5) / factor - 0.5.
    def axis_taps(length: int, factor: int, limit: int) -> list[tuple[int, int, int]]:
        taps: list[tuple[int, int, int]] = []
        for index in range(length):
            position = (2 * index + 1) - factor  # 2 * factor * sample centre
            base = position // (2 * factor)
            remainder = position - base * 2 * factor
            near = min(max(base, 0), limit - 1)
            far = min(max(base + 1, 0), limit - 1)
            taps.append((near, far, remainder))
        return taps

    x_taps = axis_taps(width, x_factor, plane_width)
    y_taps = axis_taps(height, y_factor, plane_height)
    denominator = 2 * x_factor
    out = bytearray(width * height)
    for y in range(height):
        near_y, far_y, y_remainder = y_taps[y]
        row_near = near_y * plane_width
        row_far = far_y * plane_width
        y_weight = y_remainder
        y_denominator = 2 * y_factor
        for x in range(width):
            near_x, far_x, x_remainder = x_taps[x]
            top = (
                plane[row_near + near_x] * (denominator - x_remainder)
                + plane[row_near + far_x] * x_remainder
            )
            bottom = (
                plane[row_far + near_x] * (denominator - x_remainder)
                + plane[row_far + far_x] * x_remainder
            )
            total = top * (y_denominator - y_weight) + bottom * y_weight
            out[y * width + x] = (total + denominator * y_denominator // 2) // (denominator * y_denominator)
    return out


def _decode_scan(
    data: bytes,
    offset: int,
    frame: dict[str, object],
    assignments: list[tuple[dict[str, object], int, int]],
    quant: dict[int, list[int]],
    dc_tables: dict[int, _Huffman],
    ac_tables: dict[int, _Huffman],
    restart_interval: int,
) -> tuple[int, int, bytearray]:
    width = int(frame["width"])  # type: ignore[arg-type]
    height = int(frame["height"])  # type: ignore[arg-type]
    specs = [spec for spec, _, _ in assignments]
    max_h = max(int(spec["h"]) for spec in specs)
    max_v = max(int(spec["v"]) for spec in specs)
    mcu_width = 8 * max_h
    mcu_height = 8 * max_v
    mcus_x = (width + mcu_width - 1) // mcu_width
    mcus_y = (height + mcu_height - 1) // mcu_height
    if mcus_x * mcus_y > MAX_JPEG_PIXELS:
        raise ResourceLimitError("JPEG MCU count limit exceeded")

    planes: dict[int, tuple[bytearray, int, int]] = {}
    for spec in specs:
        plane_width = mcus_x * 8 * int(spec["h"])
        plane_height = mcus_y * 8 * int(spec["v"])
        planes[int(spec["id"])] = (bytearray(plane_width * plane_height), plane_width, plane_height)
        if int(spec["quant"]) not in quant:
            raise _rejected("JPEG component references a missing quantization table")

    reader = _BitReader(data, offset)
    predictions = {int(spec["id"]): 0 for spec in specs}
    processed = 0
    for mcu_y in range(mcus_y):
        for mcu_x in range(mcus_x):
            if restart_interval and processed and processed % restart_interval == 0:
                reader.align()
                position = reader.offset
                while position + 1 < len(data) and not (
                    data[position] == 0xFF and 0xD0 <= data[position + 1] <= 0xD7
                ):
                    position += 1
                if position + 1 < len(data):
                    reader.offset = position + 2
                predictions = {key: 0 for key in predictions}
            processed += 1
            for spec, dc_id, ac_id in assignments:
                identifier = int(spec["id"])
                plane, plane_width, _ = planes[identifier]
                table = quant[int(spec["quant"])]
                for block_y in range(int(spec["v"])):
                    for block_x in range(int(spec["h"])):
                        coefficients = [0] * 64
                        symbol = reader.decode(dc_tables[dc_id])
                        if symbol > 16:
                            raise _rejected("JPEG DC magnitude is invalid")
                        difference = _extend(reader.receive(symbol), symbol)
                        predictions[identifier] += difference
                        coefficients[0] = predictions[identifier] * table[0]
                        index = 1
                        while index < 64:
                            run_size = reader.decode(ac_tables[ac_id])
                            run, size = run_size >> 4, run_size & 0x0F
                            if size == 0:
                                if run == 15:
                                    index += 16
                                    continue
                                break
                            index += run
                            if index > 63:
                                raise _rejected("JPEG AC coefficient index is out of range")
                            position = _ZIGZAG[index]
                            coefficients[position] = _extend(reader.receive(size), size) * table[position]
                            index += 1
                        pixels = _idct_2d(coefficients)
                        origin_x = (mcu_x * int(spec["h"]) + block_x) * 8
                        origin_y = (mcu_y * int(spec["v"]) + block_y) * 8
                        for row in range(8):
                            target = (origin_y + row) * plane_width + origin_x
                            plane[target : target + 8] = bytes(pixels[row * 8 : row * 8 + 8])

    rgba = bytearray(width * height * 4)
    if len(specs) == 1:
        plane, plane_width, _ = planes[int(specs[0]["id"])]
        for y in range(height):
            for x in range(width):
                grey = plane[y * plane_width + x]
                target = (y * width + x) * 4
                rgba[target] = rgba[target + 1] = rgba[target + 2] = grey
                rgba[target + 3] = 255
        return width, height, rgba

    luma_spec, cb_spec, cr_spec = specs
    luma, luma_width, _ = planes[int(luma_spec["id"])]
    cb = _upsample(planes[int(cb_spec["id"])], max_h // int(cb_spec["h"]), max_v // int(cb_spec["v"]), width, height)
    cr = _upsample(planes[int(cr_spec["id"])], max_h // int(cr_spec["h"]), max_v // int(cr_spec["v"]), width, height)

    for y in range(height):
        for x in range(width):
            luminance = luma[y * luma_width + x]
            chroma_index = y * width + x
            blue_difference = cb[chroma_index] - 128
            red_difference = cr[chroma_index] - 128
            red = luminance + ((91881 * red_difference) >> 16)
            green = luminance - ((22554 * blue_difference + 46802 * red_difference) >> 16)
            blue = luminance + ((116130 * blue_difference) >> 16)
            target = (y * width + x) * 4
            rgba[target] = 0 if red < 0 else (255 if red > 255 else red)
            rgba[target + 1] = 0 if green < 0 else (255 if green > 255 else green)
            rgba[target + 2] = 0 if blue < 0 else (255 if blue > 255 else blue)
            rgba[target + 3] = 255
    return width, height, rgba
