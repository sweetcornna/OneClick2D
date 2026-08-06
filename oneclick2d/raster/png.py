"""Bounded standard-library PNG decoder and encoder.

Narrow, explicit profile. Supported on read: bit depth 8 and 16 (narrowed to 8),
colour types 0/2/3/4/6, non-interlaced, single ``IDAT`` stream or a bounded
chain. Rejected with stable reason codes: Adam7 interlacing, bit depths other
than the above, malformed chunks, CRC mismatch, decompression bombs and any
declared size beyond the intake budget.

Written output is always non-interlaced 8-bit with an explicit ``sRGB`` chunk so
that colour handling never depends on an ambient profile.
"""

from __future__ import annotations

import struct
import zlib
from typing import Final

from ..errors import ContractError, IntakeRejected, ResourceLimitError

PNG_SIGNATURE: Final[bytes] = b"\x89PNG\r\n\x1a\n"
MAX_PNG_BYTES: Final[int] = 64 * 1024 * 1024
MAX_PNG_DIMENSION: Final[int] = 16384
MAX_PNG_PIXELS: Final[int] = 64 * 1024 * 1024
MAX_DECOMPRESSED_BYTES: Final[int] = 1024 * 1024 * 1024
MAX_CHUNKS: Final[int] = 4096
_CRITICAL: Final[frozenset[bytes]] = frozenset({b"IHDR", b"PLTE", b"IDAT", b"IEND"})
_CHANNELS: Final[dict[int, int]] = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}


def _rejected(message: str) -> IntakeRejected:
    return IntakeRejected(message, reason_code="INPUT_UNSUPPORTED")


def _decompress_bounded(data: bytes, limit: int) -> bytes:
    decompressor = zlib.decompressobj()
    out = bytearray()
    chunk = decompressor.decompress(data, limit + 1)
    out.extend(chunk)
    while decompressor.unconsumed_tail and len(out) <= limit:
        out.extend(decompressor.decompress(decompressor.unconsumed_tail, limit + 1 - len(out)))
    if len(out) > limit:
        raise ResourceLimitError("PNG decompression limit exceeded")
    tail = decompressor.flush()
    out.extend(tail)
    if len(out) > limit:
        raise ResourceLimitError("PNG decompression limit exceeded")
    if not decompressor.eof:
        raise _rejected("PNG compressed stream is truncated")
    return bytes(out)


def _unfilter(raw: bytes, stride: int, height: int, bytes_per_pixel: int) -> bytearray:
    channels = bytes_per_pixel
    expected = (stride + 1) * height
    if len(raw) != expected:
        raise _rejected("PNG scanline payload length is invalid")
    out = bytearray(stride * height)
    previous = bytearray(stride)
    offset = 0
    for row in range(height):
        filter_type = raw[offset]
        offset += 1
        line = bytearray(raw[offset : offset + stride])
        offset += stride
        if filter_type == 0:
            pass
        elif filter_type == 1:
            for index in range(channels, stride):
                line[index] = (line[index] + line[index - channels]) & 0xFF
        elif filter_type == 2:
            for index in range(stride):
                line[index] = (line[index] + previous[index]) & 0xFF
        elif filter_type == 3:
            for index in range(stride):
                left = line[index - channels] if index >= channels else 0
                line[index] = (line[index] + ((left + previous[index]) >> 1)) & 0xFF
        elif filter_type == 4:
            for index in range(stride):
                left = line[index - channels] if index >= channels else 0
                upper_left = previous[index - channels] if index >= channels else 0
                upper = previous[index]
                p = left + upper - upper_left
                pa = abs(p - left)
                pb = abs(p - upper)
                pc = abs(p - upper_left)
                if pa <= pb and pa <= pc:
                    predictor = left
                elif pb <= pc:
                    predictor = upper
                else:
                    predictor = upper_left
                line[index] = (line[index] + predictor) & 0xFF
        else:
            raise _rejected("PNG scanline filter type is unsupported")
        start = row * stride
        out[start : start + stride] = line
        previous = line
    return out


def _expand_subbyte(rows: bytearray, width: int, height: int, bit_depth: int) -> bytearray:
    per_byte = 8 // bit_depth
    mask = (1 << bit_depth) - 1
    packed_stride = (width * bit_depth + 7) // 8
    out = bytearray(width * height)
    for row in range(height):
        source = row * packed_stride
        target = row * width
        for column in range(width):
            byte = rows[source + column // per_byte]
            shift = 8 - bit_depth * (column % per_byte + 1)
            out[target + column] = (byte >> shift) & mask
    return out


def decode_png(data: bytes) -> tuple[int, int, bytearray]:
    """Decode ``data`` into ``(width, height, rgba)`` with straight alpha.

    The returned buffer is ``width * height * 4`` bytes of 8-bit RGBA.
    """
    if len(data) > MAX_PNG_BYTES:
        raise ResourceLimitError("PNG byte limit exceeded")
    if not data.startswith(PNG_SIGNATURE):
        raise _rejected("PNG signature is invalid")

    offset = len(PNG_SIGNATURE)
    header: tuple[int, int, int, int, int, int, int] | None = None
    palette: bytes = b""
    transparency: bytes = b""
    idat = bytearray()
    seen_end = False
    chunks = 0
    while offset < len(data):
        if seen_end:
            raise _rejected("PNG contains data after IEND")
        chunks += 1
        if chunks > MAX_CHUNKS:
            raise ResourceLimitError("PNG chunk count limit exceeded")
        if offset + 8 > len(data):
            raise _rejected("PNG chunk header is truncated")
        (length,) = struct.unpack_from(">I", data, offset)
        chunk_type = data[offset + 4 : offset + 8]
        if length > MAX_PNG_BYTES:
            raise ResourceLimitError("PNG chunk length limit exceeded")
        body_start = offset + 8
        body_end = body_start + length
        if body_end + 4 > len(data):
            raise _rejected("PNG chunk body is truncated")
        body = data[body_start:body_end]
        (expected_crc,) = struct.unpack_from(">I", data, body_end)
        if zlib.crc32(chunk_type + body) & 0xFFFFFFFF != expected_crc:
            raise _rejected("PNG chunk CRC mismatch")
        offset = body_end + 4

        if chunk_type == b"IHDR":
            if header is not None or length != 13:
                raise _rejected("PNG header chunk is invalid")
            header = struct.unpack(">IIBBBBB", body)
        elif header is None:
            raise _rejected("PNG header chunk must come first")
        elif chunk_type == b"PLTE":
            if palette or length == 0 or length % 3 or length > 768:
                raise _rejected("PNG palette chunk is invalid")
            palette = body
        elif chunk_type == b"tRNS":
            if transparency:
                raise _rejected("PNG transparency chunk is duplicated")
            transparency = body
        elif chunk_type == b"IDAT":
            if len(idat) + length > MAX_PNG_BYTES:
                raise ResourceLimitError("PNG compressed data limit exceeded")
            idat.extend(body)
        elif chunk_type == b"IEND":
            if length:
                raise _rejected("PNG IEND chunk must be empty")
            seen_end = True
        elif chunk_type in _CRITICAL or not chunk_type[0:1].islower():
            raise _rejected("PNG critical chunk is unsupported")

    if header is None or not seen_end or not idat:
        raise _rejected("PNG is missing required chunks")
    width, height, bit_depth, colour_type, compression, filter_method, interlace = header
    if not 1 <= width <= MAX_PNG_DIMENSION or not 1 <= height <= MAX_PNG_DIMENSION:
        raise _rejected("PNG dimensions are unsupported")
    if width * height > MAX_PNG_PIXELS:
        raise ResourceLimitError("PNG pixel limit exceeded")
    if compression != 0 or filter_method != 0:
        raise _rejected("PNG compression or filter method is unsupported")
    if interlace != 0:
        raise _rejected("PNG interlacing is unsupported")
    if colour_type not in _CHANNELS:
        raise _rejected("PNG colour type is unsupported")
    if colour_type == 3:
        if bit_depth not in (1, 2, 4, 8) or not palette:
            raise _rejected("PNG palette profile is unsupported")
    elif bit_depth not in (8, 16):
        raise _rejected("PNG bit depth is unsupported")

    channels = _CHANNELS[colour_type]
    sample_bytes = 2 if bit_depth == 16 else 1
    if colour_type == 3:
        stride = (width * bit_depth + 7) // 8
    else:
        stride = width * channels * sample_bytes
    needed = (stride + 1) * height
    if needed > MAX_DECOMPRESSED_BYTES:
        raise ResourceLimitError("PNG decompressed size limit exceeded")
    raw = _decompress_bounded(bytes(idat), needed)
    rows = _unfilter(raw, stride, height, channels * sample_bytes if colour_type != 3 else 1)

    rgba = bytearray(width * height * 4)
    pixels = width * height
    if colour_type == 3:
        indices = _expand_subbyte(rows, width, height, bit_depth) if bit_depth != 8 else rows
        entries = len(palette) // 3
        for index in range(pixels):
            entry = indices[index]
            if entry >= entries:
                raise _rejected("PNG palette index is out of range")
            target = index * 4
            rgba[target : target + 3] = palette[entry * 3 : entry * 3 + 3]
            rgba[target + 3] = transparency[entry] if entry < len(transparency) else 255
        return width, height, rgba

    step = channels * sample_bytes
    transparent_sample: tuple[int, ...] | None = None
    if transparency:
        if colour_type == 0 and len(transparency) == 2:
            transparent_sample = (struct.unpack(">H", transparency)[0] >> (8 if bit_depth == 16 else 0),)
        elif colour_type == 2 and len(transparency) == 6:
            values = struct.unpack(">HHH", transparency)
            transparent_sample = tuple(value >> (8 if bit_depth == 16 else 0) for value in values)
        else:
            raise _rejected("PNG transparency chunk does not match the colour type")

    for index in range(pixels):
        source = index * step
        samples = [rows[source + channel * sample_bytes] for channel in range(channels)]
        target = index * 4
        if colour_type == 0:
            grey = samples[0]
            rgba[target] = rgba[target + 1] = rgba[target + 2] = grey
            rgba[target + 3] = 0 if transparent_sample == (grey,) else 255
        elif colour_type == 2:
            rgba[target] = samples[0]
            rgba[target + 1] = samples[1]
            rgba[target + 2] = samples[2]
            rgba[target + 3] = 0 if transparent_sample == tuple(samples) else 255
        elif colour_type == 4:
            grey = samples[0]
            rgba[target] = rgba[target + 1] = rgba[target + 2] = grey
            rgba[target + 3] = samples[1]
        else:
            rgba[target] = samples[0]
            rgba[target + 1] = samples[1]
            rgba[target + 2] = samples[2]
            rgba[target + 3] = samples[3]
    return width, height, rgba


def _chunk(chunk_type: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + chunk_type + body + struct.pack(
        ">I", zlib.crc32(chunk_type + body) & 0xFFFFFFFF
    )


def _encode(width: int, height: int, samples: bytes, colour_type: int, channels: int) -> bytes:
    if not 1 <= width <= MAX_PNG_DIMENSION or not 1 <= height <= MAX_PNG_DIMENSION:
        raise ContractError("PNG output dimensions are out of range")
    if len(samples) != width * height * channels:
        raise ContractError("PNG output payload length mismatch")
    stride = width * channels
    raw = bytearray()
    for row in range(height):
        raw.append(0)
        raw.extend(samples[row * stride : (row + 1) * stride])
    header = struct.pack(">IIBBBBB", width, height, 8, colour_type, 0, 0, 0)
    return b"".join(
        (
            PNG_SIGNATURE,
            _chunk(b"IHDR", header),
            _chunk(b"sRGB", b"\x00"),
            _chunk(b"IDAT", zlib.compress(bytes(raw), 9)),
            _chunk(b"IEND", b""),
        )
    )


def encode_rgba_png(width: int, height: int, rgba: bytes) -> bytes:
    """Encode an 8-bit straight-alpha RGBA buffer as a deterministic PNG."""
    return _encode(width, height, rgba, 6, 4)


def encode_gray_png(width: int, height: int, grey: bytes) -> bytes:
    """Encode an 8-bit single-channel coverage mask as a deterministic PNG."""
    return _encode(width, height, grey, 0, 1)
