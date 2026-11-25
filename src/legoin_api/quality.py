"""Why a photo with no pieces failed: too dark, too blurry, or simply no pieces.

Only asked when the detector found nothing; a photo with pieces in it is never failed for
its light or focus. The thresholds are provisional, picked on the detector's real photos
and blurred or darkened copies of them, until labeled phone photos exist.
"""

from PIL import Image, ImageFilter, ImageStat

from legoin_api.schemas import ErrorCode

DARK_MEAN = 35
"""Mean brightness, 0 to 255, below which a photo is too dark to read."""

BLUR_VARIANCE = 12
"""Variance of the Laplacian, on a 512 px copy, below which a photo is too blurry."""

_LAPLACIAN = ImageFilter.Kernel((3, 3), [0, 1, 0, 1, -4, 1, 0, 1, 0], scale=1, offset=128)


def sharpness(image: Image.Image) -> float:
    gray = image.convert("L")
    gray.thumbnail((512, 512))
    return ImageStat.Stat(gray.filter(_LAPLACIAN)).var[0]


def brightness(image: Image.Image) -> float:
    gray = image.convert("L")
    gray.thumbnail((512, 512))
    return ImageStat.Stat(gray).mean[0]


def photo_problem(image: Image.Image) -> ErrorCode:
    if brightness(image) < DARK_MEAN:
        return "too_dark"
    if sharpness(image) < BLUR_VARIANCE:
        return "too_blurry"
    return "no_pieces"


MESSAGES: dict[ErrorCode, str] = {
    "too_dark": "The photo is too dark to find pieces.",
    "too_blurry": "The photo is too blurry to find pieces.",
    "no_pieces": "No pieces were found in the photo.",
}
