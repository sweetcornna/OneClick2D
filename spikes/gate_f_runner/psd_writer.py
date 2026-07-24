"""Narrow standard-library PSD v1 writer for purpose-created layered preflights."""

from __future__ import annotations

import struct
from dataclasses import dataclass

from .contracts import StageContractError


@dataclass(frozen=True)
class PsdLayer:
    layer_id: int
    name: str
    left: int
    top: int
    width: int
    height: int
    rgba: bytes
    visible: bool = True
    opacity: int = 255
    locked: bool = False


def _u32(value: int) -> bytes:
    if not 0 <= value <= 0xFFFFFFFF:
        raise StageContractError("PSD section length exceeds uint32")
    return struct.pack(">I", value)


def _additional(key: bytes, payload: bytes) -> bytes:
    padding = b"\0" if len(payload) & 1 else b""
    return b"8BIM" + key + _u32(len(payload)) + payload + padding


def _layer_extra(layer: PsdLayer) -> bytes:
    fallback = layer.name.encode("ascii", errors="replace")[:255]
    pascal = bytes([len(fallback)]) + fallback
    pascal += bytes((-len(pascal)) % 4)
    utf16 = layer.name.encode("utf-16-be")
    extra = _u32(0) + _u32(0) + pascal
    extra += _additional(b"luni", _u32(len(utf16) // 2) + utf16)
    extra += _additional(b"lyid", _u32(layer.layer_id))
    if layer.locked:
        extra += _additional(b"lspf", _u32(7))
    return extra


def _channel_planes(layer: PsdLayer) -> tuple[bytes, bytes, bytes, bytes]:
    expected = layer.width * layer.height * 4
    if len(layer.rgba) != expected:
        raise StageContractError("PSD layer RGBA length mismatch")
    return tuple(layer.rgba[channel::4] for channel in range(4))  # type: ignore[return-value]


def write_layered_psd(width: int, height: int, layers: tuple[PsdLayer, ...], merged_rgba: bytes) -> bytes:
    if not 1 <= width <= 4096 or not 1 <= height <= 4096 or not 2 <= len(layers) <= 64:
        raise StageContractError("PSD preflight limits exceeded")
    if len(merged_rgba) != width * height * 4:
        raise StageContractError("PSD merged RGBA length mismatch")
    if len({layer.layer_id for layer in layers}) != len(layers):
        raise StageContractError("PSD layer IDs must be unique")
    decoded_layer_bytes = sum(layer.width * layer.height * 4 for layer in layers)
    estimated_size = 26 + 12 + decoded_layer_bytes + width * height * 4 + len(layers) * 1024
    if decoded_layer_bytes < 0 or estimated_size > 256 * 1024 * 1024 or estimated_size > 0xFFFFFFFF:
        raise StageContractError("PSD preflight file-size limit exceeded")

    records = bytearray()
    channel_data = bytearray()
    for layer in layers:
        if (
            not 0 <= layer.left < layer.left + layer.width <= width
            or not 0 <= layer.top < layer.top + layer.height <= height
            or not 0 <= layer.opacity <= 255
            or not layer.name
        ):
            raise StageContractError("PSD layer bounds or fields are invalid")
        planes = _channel_planes(layer)
        records.extend(struct.pack(">iiiiH", layer.top, layer.left, layer.top + layer.height, layer.left + layer.width, 4))
        for channel_id, plane in zip((0, 1, 2, -1), planes, strict=True):
            records.extend(struct.pack(">hI", channel_id, 2 + len(plane)))
            channel_data.extend(struct.pack(">H", 0) + plane)
        flags = 0 if layer.visible else 2
        records.extend(b"8BIMnorm" + bytes([layer.opacity, 0, flags, 0]))
        extra = _layer_extra(layer)
        records.extend(_u32(len(extra)) + extra)

    layer_info = struct.pack(">h", -len(layers)) + records + channel_data
    if len(layer_info) & 1:
        layer_info += b"\0"
    layer_and_mask = _u32(len(layer_info)) + layer_info + _u32(0)

    header = b"8BPS" + struct.pack(">H", 1) + b"\0" * 6 + struct.pack(">HIIHH", 4, height, width, 8, 3)
    merged_planes = b"".join(merged_rgba[channel::4] for channel in range(4))
    return header + _u32(0) + _u32(0) + _u32(len(layer_and_mask)) + layer_and_mask + struct.pack(">H", 0) + merged_planes
