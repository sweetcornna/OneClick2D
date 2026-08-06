"""``INGEST_SCAN_NORMALIZE``: isolated intake and normalization (FR-001).

Accepts static PNG and JPEG only. The declared container type must match the
sniffed bytes, the image is fully decoded in isolation before acceptance, and
metadata is dropped by re-encoding from decoded samples rather than by editing
the original container. Output is sRGB straight-alpha 8-bit RGBA.

Bounds come from FR-001: 1,024-8,192 px per side, at most 40 MP, at most 25 MiB.
These are the charter's provisional values; they live in one place so a Gate
decision can change them without hunting through the pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

from ..errors import IntakeRejected, ResourceLimitError
from ..raster.image import Image
from ..raster.jpeg import decode_jpeg
from ..raster.png import PNG_SIGNATURE, decode_png
from ..strict_json import sha256_hex

MIN_SIDE: Final[int] = 1024
MAX_SIDE: Final[int] = 8192
MAX_PIXELS: Final[int] = 40 * 1000 * 1000
MAX_UPLOAD_BYTES: Final[int] = 25 * 1024 * 1024
JPEG_SIGNATURE: Final[bytes] = b"\xff\xd8\xff"
SUPPORTED_MEDIA_TYPES: Final[frozenset[str]] = frozenset({"image/png", "image/jpeg"})


@dataclass(frozen=True)
class DimensionEnvelope:
    """Accepted upload dimensions.

    The product default is the FR-001 envelope. It is injectable so that tests
    can exercise the real pipeline on small canvases: the rasterizer is pure
    Python, and full-size runs would make the repository's fixed test command
    unusably slow. Callers in production must not narrow or widen this without a
    Gate decision, since these are charter-provisional values.
    """

    min_side: int = MIN_SIDE
    max_side: int = MAX_SIDE
    max_pixels: int = MAX_PIXELS

    def check(self, width: int, height: int) -> None:
        if width * height > self.max_pixels:
            raise ResourceLimitError("upload exceeds the accepted pixel budget")
        if not self.min_side <= width <= self.max_side or not self.min_side <= height <= self.max_side:
            raise IntakeRejected(
                "upload dimensions fall outside the accepted envelope",
                reason_code="INPUT_UNSUPPORTED",
            )


DEFAULT_ENVELOPE: Final[DimensionEnvelope] = DimensionEnvelope()


@dataclass(frozen=True)
class NormalizedInput:
    """A decoded, normalized upload plus the facts intake observed."""

    image: Image
    declared_media_type: str
    sniffed_media_type: str
    upload_sha256: str
    upload_byte_length: int
    normalized_png: bytes
    normalized_sha256: str
    had_alpha: bool
    colour_space_assumed_srgb: bool

    def as_report(self) -> dict[str, Any]:
        return {
            "declared_media_type": self.declared_media_type,
            "sniffed_media_type": self.sniffed_media_type,
            "upload_sha256": self.upload_sha256,
            "upload_byte_length": self.upload_byte_length,
            "normalized_sha256": self.normalized_sha256,
            "normalized_byte_length": len(self.normalized_png),
            "canvas": {
                "width": self.image.width,
                "height": self.image.height,
                "image_origin": "top-left",
                "color_space": "srgb",
                "alpha_mode": "straight",
            },
            "had_alpha_channel": self.had_alpha,
            "colour_space_assumed_srgb": self.colour_space_assumed_srgb,
        }


def sniff_media_type(data: bytes) -> str:
    """Identify the container from its magic bytes, never from a file name."""
    if data.startswith(PNG_SIGNATURE):
        return "image/png"
    if data.startswith(JPEG_SIGNATURE):
        return "image/jpeg"
    raise IntakeRejected(
        "upload container is not a supported still image", reason_code="INPUT_UNSUPPORTED"
    )


def normalize_upload(
    data: bytes,
    declared_media_type: str,
    *,
    envelope: DimensionEnvelope = DEFAULT_ENVELOPE,
) -> NormalizedInput:
    """Decode and normalize an upload, or reject it with a stable reason code."""
    if not isinstance(data, bytes) or not data:
        raise IntakeRejected("upload is empty", reason_code="INPUT_UNSUPPORTED")
    if len(data) > MAX_UPLOAD_BYTES:
        raise ResourceLimitError("upload exceeds the accepted byte budget")
    if declared_media_type not in SUPPORTED_MEDIA_TYPES:
        raise IntakeRejected(
            "declared media type is not supported", reason_code="INPUT_UNSUPPORTED"
        )

    sniffed = sniff_media_type(data)
    if sniffed != declared_media_type:
        # A mismatch is the classic type-confusion vector; refuse rather than
        # trusting either side.
        raise IntakeRejected(
            "declared media type does not match the container", reason_code="INPUT_UNSUPPORTED"
        )

    if sniffed == "image/png":
        width, height, rgba = decode_png(data)
        had_alpha = bytes(rgba[3::4]).count(255) != width * height
        assumed_srgb = True
    else:
        width, height, rgba = decode_jpeg(data)
        had_alpha = False
        assumed_srgb = True

    envelope.check(width, height)

    image = Image(width, height, rgba)
    # Re-encoding from decoded samples is what actually strips metadata: no
    # EXIF, ICC, XMP or ancillary chunk from the upload survives this step.
    normalized_png = image.to_png()
    return NormalizedInput(
        image=image,
        declared_media_type=declared_media_type,
        sniffed_media_type=sniffed,
        upload_sha256=sha256_hex(data),
        upload_byte_length=len(data),
        normalized_png=normalized_png,
        normalized_sha256=sha256_hex(normalized_png),
        had_alpha=had_alpha,
        colour_space_assumed_srgb=assumed_srgb,
    )
