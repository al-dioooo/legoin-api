"""A stand-in pipeline for tests and CI: no torch, no weights, the same answer every time.

Its answers come from a hash of the photo's pixels, so one photo always gives the same
pieces. A photo with nothing on it, where every pixel is close to the median color, gives
none, so tests can reach the photo problems too.
"""

import hashlib

from PIL import Image, ImageStat

from legoin_api.catalogue import COLOR_IDS, PART_IDS, ColorId, PartId
from legoin_api.pipeline import Found, Reading

MAX_PIECES = 5


def _is_blank(image: Image.Image) -> bool:
    small = image.convert("RGB").resize((64, 64))
    median = ImageStat.Stat(small).median
    data = small.tobytes()
    differs = sum(
        1
        for i in range(0, len(data), 3)
        if max(abs(data[i + c] - median[c]) for c in range(3)) > 40
    )
    return differs < 0.02 * 64 * 64


class StubPipeline:
    def _digest(self, image: Image.Image) -> bytes:
        return hashlib.sha256(image.tobytes()).digest()

    def detect(self, image: Image.Image) -> list[Found]:
        if _is_blank(image):
            return []
        digest = self._digest(image)
        count = 1 + digest[0] % MAX_PIECES
        # One piece per cell of a 3 × 2 grid, inset from the cell's edges.
        cell_w, cell_h = image.width / 3, image.height / 2
        found = []
        for i in range(count):
            left, top = (i % 3) * cell_w, (i // 3) * cell_h
            found.append(
                Found(
                    x1=left + 0.15 * cell_w,
                    y1=top + 0.2 * cell_h,
                    x2=left + 0.85 * cell_w,
                    y2=top + 0.8 * cell_h,
                    score=0.65 + digest[1 + i] % 35 / 100,
                )
            )
        return found

    def classify(self, image: Image.Image, found: list[Found]) -> list[Reading[PartId]]:
        digest = self._digest(image)
        return [
            Reading(PART_IDS[digest[8 + i] % len(PART_IDS)], 0.5 + digest[16 + i] % 50 / 100)
            for i in range(len(found))
        ]

    def color(self, image: Image.Image, found: list[Found]) -> list[Reading[ColorId]]:
        digest = self._digest(image)
        return [
            Reading(COLOR_IDS[digest[12 + i] % len(COLOR_IDS)], 0.6 + digest[20 + i] % 40 / 100)
            for i in range(len(found))
        ]
