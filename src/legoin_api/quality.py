"""Why a photo with no pieces failed: too dark, too blurry, or simply no pieces.

Only asked when the detector found nothing; a photo with pieces in it is never failed for
its light or focus. The thresholds are provisional, until labeled phone photos exist. They
were checked on 80 real photos from the detector's test split and on copies of them:

- brightness: the real photos are 50–180, copies darkened to 20% are 10–35;
- sharpness: the real photos are at least 6.5 (5% under 12.6), copies blurred by 2 px at 512 px
  have a median of 3.2 and copies blurred by 4 px are at most 3.5;
- structure: the real photos, sharp or blurred, are at least 14. A featureless photo, such as a
  plain empty table, has almost none, so it's `no_pieces` rather than "too blurry".
"""

from PIL import Image, ImageFilter, ImageStat

from legoin_api.schemas import ErrorCode

DARK_MEAN = 35
"""Mean brightness, 0 to 255, below which a photo is too dark to read."""

BLUR_VARIANCE = 5
"""Variance of the Laplacian, on a 512 px copy, below which a photo is too blurry."""

FLAT_STDDEV = 8
"""Brightness spread, on a 64 px copy, below which a photo has nothing in it to be blurry."""

_LAPLACIAN = ImageFilter.Kernel((3, 3), [0, 1, 0, 1, -4, 1, 0, 1, 0], scale=1, offset=128)


def sharpness(image: Image.Image) -> float:
    gray = image.convert("L")
    gray.thumbnail((512, 512))
    edges = gray.filter(_LAPLACIAN)
    # Pillow copies the outermost pixels unfiltered; leave them out.
    return ImageStat.Stat(edges.crop((1, 1, edges.width - 1, edges.height - 1))).var[0]


def structure(image: Image.Image) -> float:
    gray = image.convert("L")
    gray.thumbnail((64, 64))
    return ImageStat.Stat(gray).stddev[0]


def brightness(image: Image.Image) -> float:
    gray = image.convert("L")
    gray.thumbnail((512, 512))
    return ImageStat.Stat(gray).mean[0]


def photo_problem(image: Image.Image) -> ErrorCode:
    if brightness(image) < DARK_MEAN:
        return "too_dark"
    # Blurry means something is there but soft: coarse shapes without fine detail.
    if structure(image) >= FLAT_STDDEV and sharpness(image) < BLUR_VARIANCE:
        return "too_blurry"
    return "no_pieces"


MESSAGES: dict[ErrorCode, str] = {
    "too_dark": "The photo is too dark to find pieces.",
    "too_blurry": "The photo is too blurry to find pieces.",
    "no_pieces": "No pieces were found in the photo.",
}
