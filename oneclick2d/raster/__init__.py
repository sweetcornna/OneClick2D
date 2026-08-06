"""Standard-library raster primitives for the production path.

The product path decodes, normalizes, composites and encodes 8-bit sRGB
straight-alpha rasters without any third-party imaging dependency. This keeps
D-005 (language/framework/toolchain) open: no production framework choice is
implied by the pipeline being able to run.
"""

from __future__ import annotations

from .image import Image, Mask
from .png import decode_png, encode_gray_png, encode_rgba_png

__all__ = ["Image", "Mask", "decode_png", "encode_gray_png", "encode_rgba_png"]
