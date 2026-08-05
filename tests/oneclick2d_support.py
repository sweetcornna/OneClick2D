"""Shared purpose-created fixtures for the production-path tests.

Every fixture is synthetic and generated in code: no user artwork, no customer
content and no rights-unclear asset enters the repository (``CLAUDE.md``).

Canvases here are deliberately small. The production intake floor is 1,024 px per
side, so tests that exercise intake bounds use that; tests that only need the
generation path use a reduced canvas and bypass the intake floor, because the
rasterizer is pure Python and full-size runs would make the fixed test command
unusably slow.
"""

from __future__ import annotations

from oneclick2d.raster.image import Image


def synthetic_subject(size: int = 256, *, cut_out: bool = True) -> Image:
    """A synthetic upright half-body figure: head, neck and tapering torso.

    ``cut_out`` produces a transparent background, which is the input precondition
    the product expects. With ``cut_out=False`` the canvas is fully opaque, which
    exercises the background-not-separated policy path.
    """
    image = Image(size, size)
    centre_x = size / 2.0
    head_radius = size * 0.19
    head_centre_y = size * 0.26
    shoulder_y = size * 0.42

    for y in range(size):
        for x in range(size):
            inside = ((x - centre_x) ** 2 + (y - head_centre_y) ** 2) ** 0.5 < head_radius
            if not inside and y > shoulder_y:
                half_width = size * (0.16 + 0.20 * (y - shoulder_y) / (size - shoulder_y))
                inside = abs(x - centre_x) < half_width
            if inside:
                # A deterministic, clearly non-uniform pattern so that pixel
                # preservation checks cannot pass by accident on flat colour.
                image.set_pixel(
                    x,
                    y,
                    (
                        (x * 7) % 200 + 40,
                        (y * 5) % 180 + 50,
                        (x * y) % 150 + 60,
                        255,
                    ),
                )
            elif not cut_out:
                image.set_pixel(x, y, (18, 24, 32, 255))
    return image


def flat_image(size: int, colour: tuple[int, int, int, int]) -> Image:
    """A uniformly filled canvas."""
    return Image(size, size, bytearray(bytes(colour) * (size * size)))
