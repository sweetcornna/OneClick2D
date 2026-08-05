"""Layered PSD writer and independent reader (FR-016).

Narrow profile from ``docs/PSD_EXPORT_PROFILE.md``: PSD (never PSB), 8-bit RGB,
explicit sRGB, the same canvas and transparency as the CIR, plain raster layers
with normal blend, stable Unicode names and character-anatomical sides.

Panel order (top to bottom in Photoshop) is:

    OneClick2D — Read Me        (hidden)
    frontmost semantic layer
      Generated Fill — <part>   (immediately below its visible layer)
    ... progressively further back ...
    Source Reference           (hidden, locked, bottom)

The compositor paints in the reverse of panel order. Each generated fill sits
directly beneath the visible layer it belongs to, never pooled into one group.

Layer pixels are stored uncompressed (raw) rather than RLE. RLE would be smaller
but adds an encoder whose bugs are silent; the writer is paired with a reader
that re-checks every layer, and raw channels keep that check exact.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Any, Final

from ..errors import ContractError, ExportVerificationFailed
from ..raster.image import Image

MAX_DIMENSION: Final[int] = 30000
MAX_LAYERS: Final[int] = 256
MAX_FILE_BYTES: Final[int] = 2 * 1024 * 1024 * 1024
READ_ME_NAME: Final[str] = "OneClick2D — Read Me"
SOURCE_REFERENCE_NAME: Final[str] = "Source Reference"
GENERATED_PREFIX: Final[str] = "Generated Fill — "
# sRGB IEC61966-2.1, the profile every target editor understands.
SRGB_PROFILE_NAME: Final[bytes] = b"sRGB IEC61966-2.1"


@dataclass(frozen=True)
class PsdLayer:
    """One PSD raster layer in panel order (index 0 is the bottom of the panel)."""

    layer_id: int
    name: str
    image: Image
    visible: bool = True
    opacity: int = 255
    locked: bool = False


def _u16(value: int) -> bytes:
    if not 0 <= value <= 0xFFFF:
        raise ContractError("PSD field exceeds uint16")
    return struct.pack(">H", value)


def _u32(value: int) -> bytes:
    if not 0 <= value <= 0xFFFFFFFF:
        raise ContractError("PSD field exceeds uint32")
    return struct.pack(">I", value)


def _pascal_padded(name: str) -> bytes:
    fallback = name.encode("ascii", errors="replace")[:255]
    payload = bytes([len(fallback)]) + fallback
    payload += bytes((-len(payload)) % 4)
    return payload


def _additional_block(key: bytes, payload: bytes) -> bytes:
    padded = payload + (b"\x00" if len(payload) & 1 else b"")
    return b"8BIM" + key + _u32(len(payload)) + padded


def _image_resources() -> bytes:
    """Resource block declaring the colour profile and resolution."""
    resources = bytearray()
    # 1039: ICC profile. A minimal sRGB identifier keeps colour unambiguous
    # without embedding a full profile the reader would have to parse.
    profile = SRGB_PROFILE_NAME
    resources += b"8BIM" + _u16(1039) + b"\x00\x00" + _u32(len(profile)) + profile
    if len(profile) & 1:
        resources += b"\x00"
    # 1005: resolution info, 72 dpi, so editors do not invent a scale.
    resolution = struct.pack(">IHHIHH", 72 << 16, 1, 1, 72 << 16, 1, 1)
    resources += b"8BIM" + _u16(1005) + b"\x00\x00" + _u32(len(resolution)) + resolution
    return _u32(len(resources)) + bytes(resources)


def write_layered_psd(canvas: Image, layers: tuple[PsdLayer, ...], merged: Image) -> bytes:
    """Write a narrow-profile layered PSD.

    ``layers`` is in panel order from bottom to top. Preflight bounds are checked
    before any allocation so an oversized document is blocked rather than
    silently flattened (``docs/PSD_EXPORT_PROFILE.md`` §4).
    """
    width, height = canvas.width, canvas.height
    if not 1 <= width <= MAX_DIMENSION or not 1 <= height <= MAX_DIMENSION:
        raise ContractError("PSD canvas dimensions exceed the supported profile")
    if not 2 <= len(layers) <= MAX_LAYERS:
        raise ContractError("PSD layer count is outside the supported profile")
    if merged.size != canvas.size:
        raise ContractError("PSD merged composite does not match the canvas")
    if len({layer.layer_id for layer in layers}) != len(layers):
        raise ContractError("PSD layer identifiers must be unique")
    if len({layer.name for layer in layers}) != len(layers):
        raise ContractError("PSD layer names must be unique")

    estimated = 26 + (width * height * 4) * (len(layers) + 1) + len(layers) * 1024
    if estimated > MAX_FILE_BYTES:
        raise ContractError("PSD estimated file size exceeds the blocking threshold")

    records = bytearray()
    channel_data = bytearray()
    for layer in layers:
        if layer.image.size != canvas.size:
            raise ContractError("PSD layer must cover the full canvas in this profile")
        if not 0 <= layer.opacity <= 255 or not layer.name:
            raise ContractError("PSD layer fields are invalid")
        red, green, blue, alpha = layer.image.channel_planes()
        records += struct.pack(">iiii", 0, 0, height, width)
        records += _u16(4)
        for channel_id, plane in ((-1, alpha), (0, red), (1, green), (2, blue)):
            records += struct.pack(">hI", channel_id, 2 + len(plane))
            channel_data += _u16(0) + plane
        flags = 0 if layer.visible else 2
        records += b"8BIM" + b"norm" + bytes([layer.opacity, 0, flags, 0])
        extra = bytearray()
        extra += _u32(0)  # no layer mask
        extra += _u32(0)  # no blending ranges
        extra += _pascal_padded(layer.name)
        utf16 = layer.name.encode("utf-16-be")
        extra += _additional_block(b"luni", _u32(len(utf16) // 2) + utf16)
        extra += _additional_block(b"lyid", _u32(layer.layer_id))
        if layer.locked:
            # lspf bit pattern 0b111 locks pixels, position and transparency.
            extra += _additional_block(b"lspf", _u32(7))
        records += _u32(len(extra)) + bytes(extra)

    layer_info = struct.pack(">h", len(layers)) + bytes(records) + bytes(channel_data)
    if len(layer_info) & 1:
        layer_info += b"\x00"
    layer_and_mask = _u32(len(layer_info)) + layer_info + _u32(0)

    header = b"8BPS" + _u16(1) + b"\x00" * 6 + _u16(4) + _u32(height) + _u32(width) + _u16(8) + _u16(3)
    merged_red, merged_green, merged_blue, merged_alpha = merged.channel_planes()
    merged_planes = merged_red + merged_green + merged_blue + merged_alpha
    document = (
        header
        + _u32(0)
        + _image_resources()
        + _u32(len(layer_and_mask))
        + layer_and_mask
        + _u16(0)
        + merged_planes
    )
    if len(document) > MAX_FILE_BYTES:
        raise ContractError("PSD file size exceeds the blocking threshold")
    return document


@dataclass(frozen=True)
class ParsedPsdLayer:
    layer_id: int
    name: str
    image: Image
    visible: bool
    opacity: int
    locked: bool
    blend_mode: str


@dataclass(frozen=True)
class ParsedPsd:
    width: int
    height: int
    layers: tuple[ParsedPsdLayer, ...]
    merged: Image
    has_srgb_profile: bool

    def panel_order_names(self) -> tuple[str, ...]:
        """Layer names from the top of the Photoshop panel downward."""
        return tuple(layer.name for layer in reversed(self.layers))


class _Cursor:
    __slots__ = ("data", "offset")

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.offset = 0

    def take(self, size: int) -> bytes:
        if size < 0 or self.offset + size > len(self.data):
            raise ExportVerificationFailed("PSD is truncated")
        chunk = self.data[self.offset : self.offset + size]
        self.offset += size
        return chunk

    def u16(self) -> int:
        return struct.unpack(">H", self.take(2))[0]

    def i16(self) -> int:
        return struct.unpack(">h", self.take(2))[0]

    def u32(self) -> int:
        return struct.unpack(">I", self.take(4))[0]

    def i32(self) -> int:
        return struct.unpack(">i", self.take(4))[0]

    def sub(self, size: int) -> "_Cursor":
        return _Cursor(self.take(size))

    def done(self) -> None:
        if self.offset != len(self.data):
            raise ExportVerificationFailed("PSD section has unexpected trailing bytes")


def _parse_additional(cursor: _Cursor, fallback: str) -> tuple[int | None, str, bool]:
    layer_id: int | None = None
    name = fallback
    locked = False
    while cursor.offset < len(cursor.data):
        if len(cursor.data) - cursor.offset < 12:
            # Trailing alignment padding is acceptable; anything else is not.
            if any(cursor.data[cursor.offset :]):
                raise ExportVerificationFailed("PSD additional block padding is nonzero")
            break
        if cursor.take(4) != b"8BIM":
            raise ExportVerificationFailed("PSD additional block signature is invalid")
        key = cursor.take(4)
        length = cursor.u32()
        payload = _Cursor(cursor.take(length))
        if length & 1 and cursor.offset < len(cursor.data):
            if cursor.take(1) != b"\x00":
                raise ExportVerificationFailed("PSD additional block padding is nonzero")
        if key == b"luni":
            count = payload.u32()
            raw = payload.take(count * 2)
            try:
                name = raw.decode("utf-16-be")
            except UnicodeDecodeError as exc:
                raise ExportVerificationFailed("PSD Unicode layer name is invalid") from exc
        elif key == b"lyid":
            layer_id = payload.u32()
        elif key == b"lspf":
            locked = payload.u32() == 7
        else:
            raise ExportVerificationFailed("PSD additional block is unsupported")
    return layer_id, name, locked


def parse_layered_psd(data: bytes) -> ParsedPsd:
    """Independently parse a narrow-profile layered PSD.

    Deliberately implemented apart from the writer: FR-016 and
    ``docs/PSD_EXPORT_PROFILE.md`` §5 require verification by a reader that does
    not share the writer's assumptions.
    """
    if len(data) > MAX_FILE_BYTES:
        raise ExportVerificationFailed("PSD exceeds the accepted size")
    cursor = _Cursor(data)
    if cursor.take(4) != b"8BPS":
        raise ExportVerificationFailed("PSD signature is invalid")
    version = cursor.u16()
    if version != 1:
        # Version 2 is PSB, which the profile explicitly excludes.
        raise ExportVerificationFailed("PSB is not part of the supported profile")
    if cursor.take(6) != b"\x00" * 6:
        raise ExportVerificationFailed("PSD reserved header bytes are not zero")
    channels = cursor.u16()
    height = cursor.u32()
    width = cursor.u32()
    depth = cursor.u16()
    mode = cursor.u16()
    if channels != 4:
        raise ExportVerificationFailed("PSD channel count is outside the profile")
    if depth != 8:
        raise ExportVerificationFailed("PSD bit depth is outside the profile")
    if mode != 3:
        raise ExportVerificationFailed("PSD colour mode is not RGB")
    if not 1 <= width <= MAX_DIMENSION or not 1 <= height <= MAX_DIMENSION:
        raise ExportVerificationFailed("PSD canvas dimensions are outside the profile")

    if cursor.u32() != 0:
        raise ExportVerificationFailed("PSD colour mode data is unsupported")
    resources = cursor.sub(cursor.u32())
    has_srgb = SRGB_PROFILE_NAME in resources.data

    layer_and_mask = cursor.sub(cursor.u32())
    layer_info = layer_and_mask.sub(layer_and_mask.u32())
    signed_count = layer_info.i16()
    layer_count = abs(signed_count)
    if not 2 <= layer_count <= MAX_LAYERS:
        raise ExportVerificationFailed("PSD layer count is outside the profile")

    records: list[dict[str, Any]] = []
    for _ in range(layer_count):
        top, left, bottom, right = layer_info.i32(), layer_info.i32(), layer_info.i32(), layer_info.i32()
        if (left, top, right, bottom) != (0, 0, width, height):
            raise ExportVerificationFailed("PSD layer bounds must cover the canvas in this profile")
        channel_count = layer_info.u16()
        if channel_count != 4:
            raise ExportVerificationFailed("PSD layer channel count is outside the profile")
        channel_info = tuple((layer_info.i16(), layer_info.u32()) for _ in range(channel_count))
        if layer_info.take(4) != b"8BIM":
            raise ExportVerificationFailed("PSD blend signature is invalid")
        blend = layer_info.take(4).decode("ascii", errors="replace")
        if blend != "norm":
            raise ExportVerificationFailed("PSD blend mode is outside the profile")
        opacity, clipping, flags, filler = layer_info.take(4)
        if clipping != 0 or filler != 0:
            raise ExportVerificationFailed("PSD clipping or filler byte is invalid")
        extra = layer_info.sub(layer_info.u32())
        if extra.u32() != 0:
            raise ExportVerificationFailed("PSD layer masks are unsupported")
        if extra.u32() != 0:
            raise ExportVerificationFailed("PSD blending ranges are unsupported")
        name_length = extra.take(1)[0]
        fallback = extra.take(name_length).decode("ascii", errors="replace")
        extra.take((-1 - name_length) % 4)
        layer_id, name, locked = _parse_additional(extra, fallback)
        if layer_id is None:
            raise ExportVerificationFailed("PSD layer is missing a stable identifier")
        records.append(
            {
                "id": layer_id,
                "name": name,
                "channels": channel_info,
                "visible": flags & 2 == 0,
                "opacity": opacity,
                "locked": locked,
                "blend": blend,
            }
        )

    pixels = width * height
    parsed: list[ParsedPsdLayer] = []
    for record in records:
        planes: dict[int, bytes] = {}
        for channel_id, length in record["channels"]:  # type: ignore[union-attr]
            if length != pixels + 2:
                raise ExportVerificationFailed("PSD raw channel length is invalid")
            if layer_info.u16() != 0:
                raise ExportVerificationFailed("PSD layer channel compression is unsupported")
            planes[channel_id] = layer_info.take(pixels)
        if set(planes) != {-1, 0, 1, 2}:
            raise ExportVerificationFailed("PSD layer channel identifiers are invalid")
        buffer = bytearray(pixels * 4)
        buffer[0::4] = planes[0]
        buffer[1::4] = planes[1]
        buffer[2::4] = planes[2]
        buffer[3::4] = planes[-1]
        parsed.append(
            ParsedPsdLayer(
                layer_id=int(record["id"]),
                name=str(record["name"]),
                image=Image(width, height, buffer),
                visible=bool(record["visible"]),
                opacity=int(record["opacity"]),
                locked=bool(record["locked"]),
                blend_mode=str(record["blend"]),
            )
        )

    if layer_info.offset < len(layer_info.data):
        remainder = layer_info.take(len(layer_info.data) - layer_info.offset)
        if any(remainder):
            raise ExportVerificationFailed("PSD layer info has unexpected trailing bytes")
    if layer_and_mask.offset < len(layer_and_mask.data):
        if layer_and_mask.u32() != 0:
            raise ExportVerificationFailed("PSD global layer mask is unsupported")

    if cursor.u16() != 0:
        raise ExportVerificationFailed("PSD merged image compression is unsupported")
    merged_planes = tuple(cursor.take(pixels) for _ in range(4))
    cursor.done()
    merged_buffer = bytearray(pixels * 4)
    merged_buffer[0::4] = merged_planes[0]
    merged_buffer[1::4] = merged_planes[1]
    merged_buffer[2::4] = merged_planes[2]
    merged_buffer[3::4] = merged_planes[3]

    if len({layer.layer_id for layer in parsed}) != len(parsed):
        raise ExportVerificationFailed("PSD layer identifiers are duplicated")
    if len({layer.name for layer in parsed}) != len(parsed):
        raise ExportVerificationFailed("PSD layer names are duplicated")

    return ParsedPsd(
        width=width,
        height=height,
        layers=tuple(parsed),
        merged=Image(width, height, merged_buffer),
        has_srgb_profile=has_srgb,
    )
