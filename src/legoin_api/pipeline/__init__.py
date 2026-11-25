"""The scan pipeline: detect the pieces, then name each one's part and color.

`Pipeline` is what the scan worker calls, one stage at a time, so it can report each stage
and stop between them. `LEGOIN_PIPELINE` picks the implementation.
"""

from dataclasses import dataclass
from typing import Protocol

from PIL import Image

from legoin_api.catalogue import ColorId, PartId


@dataclass(frozen=True)
class Found:
    """A piece the detector found: its box in pixels of the upright photo, and the box score."""

    x1: float
    y1: float
    x2: float
    y2: float
    score: float


@dataclass(frozen=True)
class Reading[T]:
    """One stage's answer for one piece: a catalogue id and its confidence, 0 to 1."""

    value: T
    confidence: float


class Pipeline(Protocol):
    def detect(self, image: Image.Image) -> list[Found]:
        """Every piece in the photo. Never drops one to make the photo look tidier."""
        ...

    def classify(self, image: Image.Image, found: list[Found]) -> list[Reading[PartId]]:
        """The part of each piece found, in the same order."""
        ...

    def color(self, image: Image.Image, found: list[Found]) -> list[Reading[ColorId]]:
        """The color of each piece found, in the same order."""
        ...
