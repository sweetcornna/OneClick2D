"""Independent strict reader for the disposable raw-channel PSD subset."""

from __future__ import annotations

import struct
from dataclasses import dataclass

from .contracts import StageContractError


@dataclass(frozen=True)
class ParsedLayer:
    layer_id: int
    name: str
    left: int
    top: int
    width: int
    height: int
    rgba: bytes
    visible: bool
    opacity: int
    locked: bool


@dataclass(frozen=True)
class ParsedPsd:
    width: int
    height: int
    layers: tuple[ParsedLayer, ...]
    merged_rgba: bytes


class Cursor:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.offset = 0

    def take(self, size: int) -> bytes:
        if size < 0 or self.offset + size > len(self.data):
            raise StageContractError("PSD is truncated")
        result = self.data[self.offset : self.offset + size]
        self.offset += size
        return result

    def u16(self) -> int:
        return struct.unpack(">H", self.take(2))[0]

    def i16(self) -> int:
        return struct.unpack(">h", self.take(2))[0]

    def u32(self) -> int:
        return struct.unpack(">I", self.take(4))[0]

    def i32(self) -> int:
        return struct.unpack(">i", self.take(4))[0]

    def subcursor(self, size: int) -> "Cursor":
        return Cursor(self.take(size))

    def done(self) -> None:
        if self.offset != len(self.data):
            raise StageContractError("PSD section has trailing bytes")


def _parse_additional(cursor: Cursor, layer_name: str) -> tuple[int | None, str, bool]:
    layer_id = None
    unicode_name = layer_name
    locked = False
    while cursor.offset < len(cursor.data):
        if cursor.take(4) != b"8BIM":
            raise StageContractError("PSD additional block signature is invalid")
        key = cursor.take(4)
        length = cursor.u32()
        payload = Cursor(cursor.take(length))
        if length & 1:
            if cursor.take(1) != b"\0":
                raise StageContractError("PSD additional padding is nonzero")
        if key == b"luni":
            count = payload.u32()
            raw = payload.take(count * 2)
            try:
                unicode_name = raw.decode("utf-16-be")
            except UnicodeDecodeError as exc:
                raise StageContractError("PSD Unicode layer name is invalid") from exc
        elif key == b"lyid":
            layer_id = payload.u32()
        elif key == b"lspf":
            locked = payload.u32() == 7
        else:
            raise StageContractError("PSD additional block is unsupported")
        payload.done()
    return layer_id, unicode_name, locked


def parse_layered_psd(data: bytes) -> ParsedPsd:
    if len(data) > 512 * 1024 * 1024:
        raise StageContractError("PSD file limit exceeded")
    cursor = Cursor(data)
    if cursor.take(4) != b"8BPS" or cursor.u16() != 1 or cursor.take(6) != b"\0" * 6:
        raise StageContractError("PSD header is invalid")
    channels = cursor.u16()
    height = cursor.u32()
    width = cursor.u32()
    depth = cursor.u16()
    mode = cursor.u16()
    if channels != 4 or not 1 <= width <= 4096 or not 1 <= height <= 4096 or depth != 8 or mode != 3:
        raise StageContractError("PSD profile is unsupported")
    if cursor.u32() != 0 or cursor.u32() != 0:
        raise StageContractError("PSD color data or resources are unsupported")
    layer_mask = cursor.subcursor(cursor.u32())
    layer_info = layer_mask.subcursor(layer_mask.u32())
    layer_count_signed = layer_info.i16()
    if layer_count_signed >= 0:
        raise StageContractError("PSD merged transparency marker is missing")
    layer_count = -layer_count_signed
    if not 2 <= layer_count <= 64:
        raise StageContractError("PSD layer count is invalid")

    records: list[dict[str, object]] = []
    for _ in range(layer_count):
        top, left, bottom, right = layer_info.i32(), layer_info.i32(), layer_info.i32(), layer_info.i32()
        if not 0 <= left < right <= width or not 0 <= top < bottom <= height:
            raise StageContractError("PSD layer bounds are invalid")
        channel_count = layer_info.u16()
        channels_info = tuple((layer_info.i16(), layer_info.u32()) for _ in range(channel_count))
        if tuple(item[0] for item in channels_info) != (0, 1, 2, -1):
            raise StageContractError("PSD layer channels are invalid")
        if layer_info.take(8) != b"8BIMnorm":
            raise StageContractError("PSD blend mode is unsupported")
        opacity, clipping, flags, filler = layer_info.take(4)
        if clipping != 0 or filler != 0:
            raise StageContractError("PSD clipping or filler is invalid")
        extra = layer_info.subcursor(layer_info.u32())
        if extra.u32() != 0 or extra.u32() != 0:
            raise StageContractError("PSD masks or blending ranges are unsupported")
        name_length = extra.take(1)[0]
        fallback = extra.take(name_length).decode("ascii", errors="replace")
        extra.take((-1 - name_length) % 4)
        layer_id, name, locked = _parse_additional(extra, fallback)
        if layer_id is None:
            raise StageContractError("PSD stable layer ID is missing")
        records.append({"id": layer_id, "name": name, "left": left, "top": top, "width": right - left, "height": bottom - top, "channels": channels_info, "visible": flags & 2 == 0, "opacity": opacity, "locked": locked})

    parsed: list[ParsedLayer] = []
    for record in records:
        pixel_count = int(record["width"]) * int(record["height"])
        planes = []
        for channel_id, length in record["channels"]:  # type: ignore[union-attr]
            if length != pixel_count + 2 or layer_info.u16() != 0:
                raise StageContractError("PSD raw channel length is invalid")
            planes.append(layer_info.take(pixel_count))
        rgba = bytes(value for index in range(pixel_count) for value in (planes[0][index], planes[1][index], planes[2][index], planes[3][index]))
        parsed.append(ParsedLayer(int(record["id"]), str(record["name"]), int(record["left"]), int(record["top"]), int(record["width"]), int(record["height"]), rgba, bool(record["visible"]), int(record["opacity"]), bool(record["locked"])))
    if layer_info.offset < len(layer_info.data):
        padding = layer_info.take(len(layer_info.data) - layer_info.offset)
        if padding != b"\0":
            raise StageContractError("PSD layer-info padding is invalid")
    layer_info.done()
    if layer_mask.u32() != 0:
        raise StageContractError("PSD global layer mask is unsupported")
    layer_mask.done()

    if cursor.u16() != 0:
        raise StageContractError("PSD merged compression is unsupported")
    pixel_count = width * height
    merged_planes = tuple(cursor.take(pixel_count) for _ in range(4))
    merged = bytes(value for index in range(pixel_count) for value in (merged_planes[0][index], merged_planes[1][index], merged_planes[2][index], merged_planes[3][index]))
    cursor.done()
    if len({layer.layer_id for layer in parsed}) != len(parsed):
        raise StageContractError("PSD stable layer IDs are duplicated")
    return ParsedPsd(width, height, tuple(parsed), merged)
