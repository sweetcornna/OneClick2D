"""Bounded structural validator for PSDs emitted by the pinned model spike."""

from __future__ import annotations

import hashlib
import os
import re
import stat
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from .contracts import StageContractError

MAX_PSD_BYTES = 512 * 1024 * 1024
MAX_IMAGE_RESOURCES_BYTES = 64 * 1024
MAX_RESOURCE_PAYLOAD_BYTES = 4 * 1024
MAX_LAYER_EXTRA_BYTES = 4 * 1024
MAX_LAYER_COUNT = 64
MAX_LAYER_CHANNELS = 5
MAX_TOTAL_LAYER_CHANNELS = MAX_LAYER_COUNT * MAX_LAYER_CHANNELS
MAX_LAYER_NAME_BYTES = 64
MAX_CANVAS_OVERSCAN = 64
MAX_TOTAL_LAYER_PIXELS = 64 * 1024 * 1024
_LAYER_NAME_RE = re.compile(r"[a-z][a-z0-9]*(?:[ -][a-z0-9]+)*\Z")
_PACKBITS_REPEAT_RE = re.compile(rb"(.)(?:\1)+", re.DOTALL)
_PACKBITS_TRIPLE_RE = re.compile(rb"(.)\1\1", re.DOTALL)
_DEFAULT_BLENDING_RANGES = b"\x00\x00\xff\xff" * 10


@dataclass(frozen=True)
class ModelPsdLayer:
    name: str
    top: int
    left: int
    bottom: int
    right: int
    channel_ids: tuple[int, ...]
    has_user_mask: bool


@dataclass(frozen=True)
class ModelPsdStructure:
    width: int
    height: int
    document_channels: int
    color_mode: int
    merged_transparency_declared: bool
    layers: tuple[ModelPsdLayer, ...]
    byte_length: int
    sha256: str


class _FileCursor:
    def __init__(self, stream: BinaryIO, start: int, end: int) -> None:
        if not 0 <= start <= end:
            raise StageContractError("model PSD section bounds are invalid")
        self.stream = stream
        self.start = start
        self.offset = start
        self.end = end

    @property
    def remaining(self) -> int:
        return self.end - self.offset

    def read(self, size: int) -> bytes:
        if size < 0 or size > self.remaining:
            raise StageContractError("model PSD is truncated")
        self.stream.seek(self.offset)
        data = self.stream.read(size)
        if len(data) != size:
            raise StageContractError("model PSD is truncated")
        self.offset += size
        return data

    def skip(self, size: int) -> None:
        if size < 0 or size > self.remaining:
            raise StageContractError("model PSD is truncated")
        self.offset += size

    def u8(self) -> int:
        return self.read(1)[0]

    def u16(self) -> int:
        return struct.unpack(">H", self.read(2))[0]

    def i16(self) -> int:
        return struct.unpack(">h", self.read(2))[0]

    def u32(self) -> int:
        return struct.unpack(">I", self.read(4))[0]

    def i32(self) -> int:
        return struct.unpack(">i", self.read(4))[0]

    def subsection(self, size: int, maximum: int, label: str) -> "_FileCursor":
        if size < 0 or size > maximum or size > self.remaining:
            raise StageContractError(f"model PSD {label} is outside its bound")
        result = _FileCursor(self.stream, self.offset, self.offset + size)
        self.offset += size
        return result

    def u32_subsection(self, maximum: int, label: str) -> "_FileCursor":
        return self.subsection(self.u32(), maximum, label)

    def require_end(self, label: str) -> None:
        if self.remaining:
            raise StageContractError(f"model PSD {label} has trailing data")


def _zero_padding(cursor: _FileCursor, size: int, label: str) -> None:
    if size and cursor.read(size) != b"\0" * size:
        raise StageContractError(f"model PSD {label} padding is invalid")


def _unicode_string(cursor: _FileCursor, *, maximum: int = MAX_LAYER_NAME_BYTES) -> str:
    count = cursor.u32()
    if count > maximum:
        raise StageContractError("model PSD Unicode string is too long")
    try:
        return cursor.read(count * 2).decode("utf-16-be", errors="strict")
    except UnicodeDecodeError as exc:
        raise StageContractError("model PSD Unicode string is invalid") from exc


def _parse_image_resources(cursor: _FileCursor) -> None:
    resources = cursor.u32_subsection(MAX_IMAGE_RESOURCES_BYTES, "image resources")
    count = 0
    while resources.remaining:
        count += 1
        if count > 1 or resources.read(4) != b"8BIM" or resources.u16() != 1057:
            raise StageContractError("model PSD image resource is unsupported")
        name_length = resources.u8()
        if name_length:
            raise StageContractError("model PSD image resource name is invalid")
        _zero_padding(resources, 1, "image resource name")
        payload_length = resources.u32()
        payload = resources.subsection(payload_length, MAX_RESOURCE_PAYLOAD_BYTES, "image resource payload")
        if (
            payload.u32() != 1
            or payload.u8() != 1
            or _unicode_string(payload) != "psd-tools 1.14.2"
            or _unicode_string(payload) != "psd-tools 1.14.2"
            or payload.u32() != 1
        ):
            raise StageContractError("model PSD writer identity is invalid")
        payload.require_end("image resource payload")
        _zero_padding(resources, payload_length & 1, "image resource")
    if count != 1:
        raise StageContractError("model PSD writer resource is missing")


def _validate_rectangle(top: int, left: int, bottom: int, right: int, width: int, height: int) -> None:
    if (
        left >= right
        or top >= bottom
        or left < -MAX_CANVAS_OVERSCAN
        or top < -MAX_CANVAS_OVERSCAN
        or right > width + MAX_CANVAS_OVERSCAN
        or bottom > height + MAX_CANVAS_OVERSCAN
        or left >= width
        or top >= height
        or right <= 0
        or bottom <= 0
    ):
        raise StageContractError("model PSD layer rectangle is invalid")


def _parse_layer_name(extra: _FileCursor) -> str:
    length = extra.u8()
    if not 1 <= length <= MAX_LAYER_NAME_BYTES:
        raise StageContractError("model PSD layer name length is invalid")
    try:
        name = extra.read(length).decode("mac_roman", errors="strict")
    except UnicodeDecodeError as exc:
        raise StageContractError("model PSD layer name is invalid") from exc
    _zero_padding(extra, (-1 - length) % 4, "layer name")
    if name == "undefined" or _LAYER_NAME_RE.fullmatch(name) is None:
        raise StageContractError("model PSD layer name is outside the profile")
    return name


def _parse_additional_blocks(extra: _FileCursor, name: str) -> None:
    count = 0
    while extra.remaining:
        count += 1
        if count > 1 or extra.read(4) not in (b"8BIM", b"8B64") or extra.read(4) != b"luni":
            raise StageContractError("model PSD additional layer data is unsupported")
        length = extra.u32()
        payload = extra.subsection(length, 4 + MAX_LAYER_NAME_BYTES * 2, "Unicode layer name")
        if _unicode_string(payload) != name:
            raise StageContractError("model PSD Unicode layer name does not match")
        payload.require_end("Unicode layer name")
        _zero_padding(extra, length & 1, "additional layer data")


def _encode_packbits(data: bytes) -> bytes:
    result = bytearray()
    length = len(data)
    if length == 1:
        return b"\0" + data
    index = 0
    while index < length:
        repeat = _PACKBITS_REPEAT_RE.match(data, index)
        if repeat is not None:
            run_length = repeat.end() - index
            full_chunks, remainder = divmod(run_length, 128)
            if full_chunks:
                result.extend(bytes((129, data[index])) * full_chunks)
            if remainder >= 2:
                result.extend((257 - remainder, data[index]))
                index = repeat.end()
            elif remainder == 1:
                index = repeat.end() - 1
            else:
                index = repeat.end()
            continue

        stop = min(index + 127, length)

        # The old byte scanner stopped a literal before the first three-byte
        # run, or before a two-byte run at the input/capacity boundary.
        triple = _PACKBITS_TRIPLE_RE.search(data, index, min(length, stop + 2))
        if triple is not None and triple.start() < stop:
            stop = triple.start()
        for pair_start in (index + 125, index + 126, length - 2):
            if (
                index <= pair_start < stop
                and pair_start + 1 < length
                and data[pair_start] == data[pair_start + 1]
            ):
                stop = pair_start

        result.append(stop - index - 1)
        result.extend(data[index:stop])
        index = stop
    return bytes(result)


def _parse_rle_row(row: _FileCursor, width: int) -> None:
    encoded = row.read(row.remaining)
    offset = 0
    decoded = bytearray()
    while offset < len(encoded):
        control = encoded[offset]
        offset += 1
        if control <= 127:
            count = control + 1
            if offset + count > len(encoded):
                raise StageContractError("model PSD RLE literal is truncated")
            decoded.extend(encoded[offset : offset + count])
            offset += count
        elif control == 128:
            raise StageContractError("model PSD RLE row is noncanonical")
        else:
            count = 257 - control
            if offset >= len(encoded):
                raise StageContractError("model PSD RLE repeat is truncated")
            decoded.extend((encoded[offset],) * count)
            offset += 1
        if len(decoded) > width:
            raise StageContractError("model PSD RLE row expands beyond its width")
    if len(decoded) != width or _encode_packbits(bytes(decoded)) != encoded:
        raise StageContractError("model PSD RLE row is noncanonical")


def _parse_channel_data(layer_info: _FileCursor, channel_length: int, width: int, height: int) -> None:
    channel = layer_info.subsection(channel_length, layer_info.remaining, "layer channel")
    if channel.u16() != 1:
        raise StageContractError("model PSD layer compression is unsupported")
    if height > channel.remaining // 2:
        raise StageContractError("model PSD RLE row table is truncated")
    row_lengths = tuple(channel.u16() for _ in range(height))
    if sum(row_lengths) != channel.remaining:
        raise StageContractError("model PSD RLE row lengths are invalid")
    for length in row_lengths:
        if length > 2 * width + 2:
            raise StageContractError("model PSD RLE row exceeds its bound")
        row = channel.subsection(length, 2 * width + 2, "RLE row")
        _parse_rle_row(row, width)
        row.require_end("RLE row")
    channel.require_end("layer channel")


def _parse_layers(
    cursor: _FileCursor,
    width: int,
    height: int,
    document_channels: int,
    profile: str,
) -> tuple[bool, tuple[ModelPsdLayer, ...]]:
    layer_mask = cursor.u32_subsection(MAX_PSD_BYTES, "layer and mask section")
    layer_info_length = layer_mask.u32()
    if layer_info_length % 4:
        raise StageContractError("model PSD layer info alignment is invalid")
    layer_info = layer_mask.subsection(layer_info_length, MAX_PSD_BYTES, "layer info")
    signed_count = layer_info.i16()
    count = abs(signed_count)
    if not 1 <= count <= MAX_LAYER_COUNT or (signed_count < 0 and document_channels != 4):
        raise StageContractError("model PSD layer count is invalid")

    records: list[tuple[ModelPsdLayer, tuple[tuple[int, int], ...]]] = []
    names: set[str] = set()
    total_channels = 0
    total_layer_pixels = 0
    for _ in range(count):
        top, left, bottom, right = layer_info.i32(), layer_info.i32(), layer_info.i32(), layer_info.i32()
        _validate_rectangle(top, left, bottom, right, width, height)
        total_layer_pixels += (right - left) * (bottom - top)
        if total_layer_pixels > MAX_TOTAL_LAYER_PIXELS:
            raise StageContractError("model PSD layer area is too large")
        channel_count = layer_info.u16()
        expected_channel_counts = {4, 5} if profile == "rgb" else {2, 3}
        if channel_count not in expected_channel_counts:
            raise StageContractError("model PSD layer channel count is invalid")
        total_channels += channel_count
        if total_channels > MAX_TOTAL_LAYER_CHANNELS:
            raise StageContractError("model PSD channel inventory is too large")
        channels = tuple((layer_info.i16(), layer_info.u32()) for _ in range(channel_count))
        channel_ids = tuple(item[0] for item in channels)
        allowed_channel_ids = (
            ((-1, 0, 1, 2), (-1, 0, 1, 2, -2))
            if profile == "rgb"
            else ((-1, 0), (-1, 0, -2))
        )
        if channel_ids not in allowed_channel_ids or any(item[1] < 2 for item in channels):
            raise StageContractError("model PSD layer channels are invalid")
        if (
            layer_info.read(4) != b"8BIM"
            or layer_info.read(4) != b"norm"
            or layer_info.u8() != 255
            or layer_info.u8() != 0
            or layer_info.u8() != 8
            or layer_info.u8() != 0
        ):
            raise StageContractError("model PSD layer settings are unsupported")
        extra = layer_info.u32_subsection(MAX_LAYER_EXTRA_BYTES, "layer extra data")
        mask = extra.u32_subsection(20, "layer mask")
        has_user_mask = -2 in channel_ids
        if has_user_mask:
            if mask.remaining != 20:
                raise StageContractError("model PSD layer mask is invalid")
            if (mask.i32(), mask.i32(), mask.i32(), mask.i32()) != (top, left, bottom, right):
                raise StageContractError("model PSD mask rectangle does not match")
            if mask.u8() != 0 or mask.u8() != 0 or mask.read(2) != b"\0\0":
                raise StageContractError("model PSD layer mask settings are invalid")
        elif mask.remaining:
            raise StageContractError("model PSD unexpected layer mask")
        mask.require_end("layer mask")
        blending = extra.u32_subsection(40, "blending ranges")
        if blending.read(blending.remaining) != _DEFAULT_BLENDING_RANGES:
            raise StageContractError("model PSD blending ranges are unsupported")
        name = _parse_layer_name(extra)
        _parse_additional_blocks(extra, name)
        extra.require_end("layer extra data")
        if name in names:
            raise StageContractError("model PSD layer names are duplicated")
        names.add(name)
        records.append((ModelPsdLayer(name, top, left, bottom, right, channel_ids, has_user_mask), channels))

    for layer, channels in records:
        channel_width = layer.right - layer.left
        channel_height = layer.bottom - layer.top
        for _, channel_length in channels:
            _parse_channel_data(layer_info, channel_length, channel_width, channel_height)
    if layer_info.remaining > 3:
        raise StageContractError("model PSD layer info has trailing data")
    expected_padding = (-(layer_info.offset - layer_info.start)) % 4
    if layer_info.remaining != expected_padding:
        raise StageContractError("model PSD layer info padding is invalid")
    _zero_padding(layer_info, expected_padding, "layer info")
    layer_info.require_end("layer info")
    global_mask = layer_mask.u32_subsection(0, "global layer mask")
    global_mask.require_end("global layer mask")
    layer_mask.require_end("layer and mask section")
    if not {"face", "mouth"}.issubset(names):
        raise StageContractError("model PSD semantic inventory is incomplete")
    return signed_count < 0, tuple(item[0] for item in records)


def validate_model_psd(path: Path, *, profile: str = "rgb") -> ModelPsdStructure:
    if profile not in {"rgb", "grayscale"}:
        raise StageContractError("model PSD validation profile is invalid")
    if path.is_symlink():
        raise StageContractError("model PSD path is invalid")
    try:
        with path.open("rb") as stream:
            file_info = os.fstat(stream.fileno())
            if not stat.S_ISREG(file_info.st_mode) or not 1 <= file_info.st_size <= MAX_PSD_BYTES:
                raise StageContractError("model PSD file is outside its bound")
            cursor = _FileCursor(stream, 0, file_info.st_size)
            if cursor.read(4) != b"8BPS" or cursor.u16() != 1 or cursor.read(6) != b"\0" * 6:
                raise StageContractError("model PSD header is invalid")
            document_channels = cursor.u16()
            height = cursor.u32()
            width = cursor.u32()
            depth = cursor.u16()
            color_mode = cursor.u16()
            expected_document_channels = {3, 4} if profile == "rgb" else {1}
            expected_color_mode = 3 if profile == "rgb" else 1
            if (
                document_channels not in expected_document_channels
                or not 1 <= width <= 4096
                or not 1 <= height <= 4096
                or depth != 8
                or color_mode != expected_color_mode
            ):
                raise StageContractError("model PSD document profile is unsupported")
            color_data = cursor.u32_subsection(0, "color mode data")
            color_data.require_end("color mode data")
            _parse_image_resources(cursor)
            merged_transparency, layers = _parse_layers(cursor, width, height, document_channels, profile)
            if cursor.u16() != 0 or cursor.remaining != width * height * document_channels:
                raise StageContractError("model PSD merged image is invalid")
            cursor.skip(cursor.remaining)
            cursor.require_end("file")
            final_info = os.fstat(stream.fileno())
            if (
                final_info.st_dev != file_info.st_dev
                or final_info.st_ino != file_info.st_ino
                or final_info.st_size != file_info.st_size
                or final_info.st_mtime_ns != file_info.st_mtime_ns
                or final_info.st_ctime_ns != file_info.st_ctime_ns
            ):
                raise StageContractError("model PSD changed during validation")
            stream.seek(0)
            digest = hashlib.sha256()
            remaining = file_info.st_size
            while remaining:
                chunk = stream.read(min(1024 * 1024, remaining))
                if not chunk:
                    raise StageContractError("model PSD changed during validation")
                digest.update(chunk)
                remaining -= len(chunk)
            after_hash = os.fstat(stream.fileno())
            if (
                after_hash.st_dev != final_info.st_dev
                or after_hash.st_ino != final_info.st_ino
                or after_hash.st_size != final_info.st_size
                or after_hash.st_mtime_ns != final_info.st_mtime_ns
                or after_hash.st_ctime_ns != final_info.st_ctime_ns
            ):
                raise StageContractError("model PSD changed during validation")
    except OSError as exc:
        raise StageContractError("model PSD could not be read") from exc
    return ModelPsdStructure(
        width,
        height,
        document_channels,
        color_mode,
        merged_transparency,
        layers,
        file_info.st_size,
        digest.hexdigest(),
    )
