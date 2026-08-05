"""Mutable 8-bit sRGB straight-alpha image and coverage-mask primitives.

Compositing follows ``docs/CIR_SPEC.md`` §2: top-left origin, X right, Y down,
sRGB persisted images, linear coverage masks and straight alpha. ``source-over``
compositing is performed on premultiplied intermediates and converted back to
straight alpha so that repeated composition does not darken transparent edges.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from ..errors import ContractError

MAX_DIMENSION: Final[int] = 16384
MAX_PIXELS: Final[int] = 64 * 1024 * 1024


def _check_size(width: int, height: int) -> None:
    if not isinstance(width, int) or not isinstance(height, int):
        raise ContractError("raster dimensions must be integers")
    if not 1 <= width <= MAX_DIMENSION or not 1 <= height <= MAX_DIMENSION:
        raise ContractError("raster dimensions are out of range")
    if width * height > MAX_PIXELS:
        raise ContractError("raster pixel budget exceeded")


@dataclass(frozen=True)
class Bounds:
    """Integer pixel bounds in source space, right/bottom exclusive."""

    x: int
    y: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height

    @property
    def empty(self) -> bool:
        return self.width <= 0 or self.height <= 0

    def as_cir(self) -> dict[str, object]:
        return {
            "x": float(self.x),
            "y": float(self.y),
            "width": float(self.width),
            "height": float(self.height),
            "unit": "pixel",
            "space": "source",
        }


class Mask:
    """An 8-bit single-channel linear coverage mask."""

    __slots__ = ("width", "height", "data")

    def __init__(self, width: int, height: int, data: bytearray | None = None) -> None:
        _check_size(width, height)
        if data is None:
            data = bytearray(width * height)
        elif len(data) != width * height:
            raise ContractError("mask payload length mismatch")
        self.width = width
        self.height = height
        self.data = data

    def copy(self) -> "Mask":
        return Mask(self.width, self.height, bytearray(self.data))

    def count_at_least(self, threshold: int) -> int:
        """Number of samples with coverage strictly greater than ``threshold``."""
        return sum(1 for value in self.data if value > threshold)

    def bounds_at_least(self, threshold: int) -> Bounds:
        """Tight bounds of samples above ``threshold``; empty bounds when none."""
        min_x, min_y, max_x, max_y = self.width, self.height, -1, -1
        width = self.width
        for index, value in enumerate(self.data):
            if value > threshold:
                y, x = divmod(index, width)
                if x < min_x:
                    min_x = x
                if x > max_x:
                    max_x = x
                if y < min_y:
                    min_y = y
                if y > max_y:
                    max_y = y
        if max_x < 0:
            return Bounds(0, 0, 0, 0)
        return Bounds(min_x, min_y, max_x - min_x + 1, max_y - min_y + 1)

    def intersect(self, other: "Mask") -> "Mask":
        if (self.width, self.height) != (other.width, other.height):
            raise ContractError("mask geometry mismatch")
        return Mask(
            self.width,
            self.height,
            bytearray(min(a, b) for a, b in zip(self.data, other.data, strict=True)),
        )

    def subtract(self, other: "Mask") -> "Mask":
        if (self.width, self.height) != (other.width, other.height):
            raise ContractError("mask geometry mismatch")
        return Mask(
            self.width,
            self.height,
            bytearray(max(0, a - b) for a, b in zip(self.data, other.data, strict=True)),
        )

    def union(self, other: "Mask") -> "Mask":
        if (self.width, self.height) != (other.width, other.height):
            raise ContractError("mask geometry mismatch")
        return Mask(
            self.width,
            self.height,
            bytearray(max(a, b) for a, b in zip(self.data, other.data, strict=True)),
        )

    def binarize(self, threshold: int) -> "Mask":
        return Mask(
            self.width,
            self.height,
            bytearray(255 if value > threshold else 0 for value in self.data),
        )

    def dilate(self, radius: int) -> "Mask":
        """Dilate with a square structuring element of the given radius."""
        if radius <= 0:
            return self.copy()
        horizontal = self._sweep_rows(radius)
        return horizontal._sweep_columns(radius)

    def _sweep_rows(self, radius: int) -> "Mask":
        out = bytearray(len(self.data))
        width, height = self.width, self.height
        for y in range(height):
            row = y * width
            for x in range(width):
                low = max(0, x - radius)
                high = min(width - 1, x + radius)
                out[row + x] = max(self.data[row + low : row + high + 1])
        return Mask(width, height, out)

    def _sweep_columns(self, radius: int) -> "Mask":
        out = bytearray(len(self.data))
        width, height = self.width, self.height
        for x in range(width):
            column = [self.data[y * width + x] for y in range(height)]
            for y in range(height):
                low = max(0, y - radius)
                high = min(height - 1, y + radius)
                out[y * width + x] = max(column[low : high + 1])
        return Mask(width, height, out)

    def feather(self, radius: int) -> "Mask":
        """Box-blur the mask so that generated edges have a bounded ramp."""
        if radius <= 0:
            return self.copy()
        width, height = self.width, self.height
        window = radius * 2 + 1
        horizontal = bytearray(len(self.data))
        for y in range(height):
            row = y * width
            for x in range(width):
                low = max(0, x - radius)
                high = min(width - 1, x + radius)
                total = sum(self.data[row + low : row + high + 1])
                horizontal[row + x] = total // window if high - low + 1 == window else total // (high - low + 1)
        out = bytearray(len(self.data))
        for x in range(width):
            column = [horizontal[y * width + x] for y in range(height)]
            for y in range(height):
                low = max(0, y - radius)
                high = min(height - 1, y + radius)
                total = sum(column[low : high + 1])
                out[y * width + x] = total // window if high - low + 1 == window else total // (high - low + 1)
        return Mask(width, height, out)

    def to_png(self) -> bytes:
        from .png import encode_gray_png

        return encode_gray_png(self.width, self.height, bytes(self.data))


class Image:
    """An 8-bit sRGB straight-alpha RGBA raster."""

    __slots__ = ("width", "height", "data")

    def __init__(self, width: int, height: int, data: bytearray | None = None) -> None:
        _check_size(width, height)
        if data is None:
            data = bytearray(width * height * 4)
        elif len(data) != width * height * 4:
            raise ContractError("image payload length mismatch")
        self.width = width
        self.height = height
        self.data = data

    @property
    def size(self) -> tuple[int, int]:
        return self.width, self.height

    def copy(self) -> "Image":
        return Image(self.width, self.height, bytearray(self.data))

    @classmethod
    def from_png(cls, data: bytes) -> "Image":
        from .png import decode_png

        width, height, rgba = decode_png(data)
        return cls(width, height, rgba)

    def to_png(self) -> bytes:
        from .png import encode_rgba_png

        return encode_rgba_png(self.width, self.height, bytes(self.data))

    def pixel(self, x: int, y: int) -> tuple[int, int, int, int]:
        if not 0 <= x < self.width or not 0 <= y < self.height:
            raise ContractError("pixel coordinate is out of range")
        offset = (y * self.width + x) * 4
        return tuple(self.data[offset : offset + 4])  # type: ignore[return-value]

    def set_pixel(self, x: int, y: int, value: tuple[int, int, int, int]) -> None:
        if not 0 <= x < self.width or not 0 <= y < self.height:
            raise ContractError("pixel coordinate is out of range")
        offset = (y * self.width + x) * 4
        self.data[offset : offset + 4] = bytes(value)

    def alpha_mask(self) -> Mask:
        return Mask(self.width, self.height, bytearray(self.data[3::4]))

    def with_alpha_mask(self, mask: Mask) -> "Image":
        """Return a copy whose alpha is replaced by ``mask`` coverage."""
        if (mask.width, mask.height) != (self.width, self.height):
            raise ContractError("mask geometry mismatch")
        out = bytearray(self.data)
        out[3::4] = mask.data
        return Image(self.width, self.height, out)

    def multiply_alpha_mask(self, mask: Mask) -> "Image":
        """Return a copy whose alpha is multiplied by ``mask`` coverage."""
        if (mask.width, mask.height) != (self.width, self.height):
            raise ContractError("mask geometry mismatch")
        out = bytearray(self.data)
        alpha = out[3::4]
        out[3::4] = bytearray((a * m + 127) // 255 for a, m in zip(alpha, mask.data, strict=True))
        return Image(self.width, self.height, out)

    def crop(self, bounds: Bounds) -> "Image":
        if bounds.empty:
            raise ContractError("cannot crop to empty bounds")
        if (
            bounds.x < 0
            or bounds.y < 0
            or bounds.right > self.width
            or bounds.bottom > self.height
        ):
            raise ContractError("crop bounds fall outside the canvas")
        out = bytearray(bounds.width * bounds.height * 4)
        row_bytes = bounds.width * 4
        for row in range(bounds.height):
            source = ((bounds.y + row) * self.width + bounds.x) * 4
            target = row * row_bytes
            out[target : target + row_bytes] = self.data[source : source + row_bytes]
        return Image(bounds.width, bounds.height, out)

    def composite_over(self, top: "Image") -> "Image":
        """Return ``top`` composited over ``self`` using straight-alpha source-over."""
        if top.size != self.size:
            raise ContractError("composite geometry mismatch")
        out = bytearray(self.data)
        source = top.data
        for offset in range(0, len(out), 4):
            source_alpha = source[offset + 3]
            if source_alpha == 0:
                continue
            if source_alpha == 255:
                out[offset : offset + 4] = source[offset : offset + 4]
                continue
            base_alpha = out[offset + 3]
            # straight-alpha source-over in 0..255 fixed point
            result_alpha = source_alpha + (base_alpha * (255 - source_alpha) + 127) // 255
            if result_alpha == 0:
                out[offset : offset + 4] = b"\x00\x00\x00\x00"
                continue
            for channel in range(3):
                numerator = (
                    source[offset + channel] * source_alpha * 255
                    + out[offset + channel] * base_alpha * (255 - source_alpha)
                )
                out[offset + channel] = min(255, numerator // (result_alpha * 255))
            out[offset + 3] = result_alpha
        return Image(self.width, self.height, out)

    def channel_planes(self) -> tuple[bytes, bytes, bytes, bytes]:
        """Return separated R, G, B, A planes for raw-channel serialization."""
        return (
            bytes(self.data[0::4]),
            bytes(self.data[1::4]),
            bytes(self.data[2::4]),
            bytes(self.data[3::4]),
        )
