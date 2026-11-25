"""A build as pieces on a stud grid: what the agent places and removes.

The grid is BrickLayer's, as the agent is told:

- **x** counts studs to the viewer's right, looking at the model's front;
- **y** counts studs away from the viewer, toward the model's back;
- **z** counts plates up from the ground: a brick is 3 plates, a plate or a tile 1.

A piece's `x` and `y` are its corner cell with the smallest x and y, `z` its bottom. Its
`rotation` is 0, 90, 180 or 270 degrees counter-clockwise seen from above: at 0 its first
size runs along x, and a slope faces −y.

This module only knows the catalogue and the grid's bounds. Collisions, support and
connection are BrickLayer's to check (`checks.py`), so nothing here needs it.
"""

from collections import Counter
from dataclasses import asdict, dataclass
from typing import Literal

from legoin_api.catalogue import COLOR_IDS, PART_IDS, ColorId, PartId
from legoin_api.inventory import merge
from legoin_api.schemas import Locale, PartCount

GRID = 20
"""x and y run from 0 to GRID − 1."""
MAX_Z = 48
"""Plates: 16 bricks tall."""
MAX_PIECES = 60
MIN_PIECES = 3

Kind = Literal["brick", "plate", "slope", "tile", "round"]


@dataclass(frozen=True)
class Shape:
    width: int
    """Studs along x at rotation 0."""
    depth: int
    height: int
    """Plates."""
    kind: Kind


SHAPES: dict[PartId, Shape] = {
    "brick-1x1": Shape(1, 1, 3, "brick"),
    "brick-1x2": Shape(2, 1, 3, "brick"),
    "brick-1x3": Shape(3, 1, 3, "brick"),
    "brick-1x4": Shape(4, 1, 3, "brick"),
    "brick-2x2": Shape(2, 2, 3, "brick"),
    "brick-2x3": Shape(3, 2, 3, "brick"),
    "brick-2x4": Shape(4, 2, 3, "brick"),
    "plate-1x2": Shape(2, 1, 1, "plate"),
    "plate-2x2": Shape(2, 2, 1, "plate"),
    "plate-2x4": Shape(4, 2, 1, "plate"),
    "slope-2x2": Shape(2, 2, 3, "slope"),
    "tile-1x2": Shape(2, 1, 1, "tile"),
    "round-1x1": Shape(1, 1, 3, "round"),
}

ROTATIONS = (0, 90, 180, 270)

COLOR_NAMES: dict[ColorId, tuple[str, str]] = {
    "red": ("red", "merah"),
    "blue": ("blue", "biru"),
    "yellow": ("yellow", "kuning"),
    "green": ("green", "hijau"),
    "black": ("black", "hitam"),
    "white": ("white", "putih"),
    "light-gray": ("light gray", "abu-abu terang"),
    "dark-gray": ("dark gray", "abu-abu gelap"),
    "orange": ("orange", "oranye"),
    "brown": ("brown", "cokelat"),
}
"""The contract's catalogue names, as steps write them."""


@dataclass(frozen=True)
class Piece:
    id: str
    part: PartId
    color: ColorId
    x: int
    y: int
    z: int
    rotation: int

    @property
    def shape(self) -> Shape:
        return SHAPES[self.part]

    @property
    def footprint(self) -> tuple[int, int]:
        """Studs along x and y, as placed."""
        shape = self.shape
        if self.rotation in (0, 180):
            return shape.width, shape.depth
        return shape.depth, shape.width

    def describe(self) -> str:
        return (
            f"{self.id} {self.part} {self.color} at ({self.x},{self.y},{self.z}) r{self.rotation}"
        )

    def wire(self) -> dict:
        return asdict(self)


def problem_with(spec: dict) -> str | None:
    """Why a piece can't go on the grid, or None. The tool's schema already limits the part,
    color and rotation; they're checked again, since a fake or a future model may not."""
    part, color, rotation = spec.get("part"), spec.get("color"), spec.get("rotation")
    if part not in PART_IDS:
        return f"{part!r} isn't a catalogue part"
    if color not in COLOR_IDS:
        return f"{color!r} isn't a catalogue color"
    if rotation not in ROTATIONS:
        return "rotation must be 0, 90, 180 or 270"
    try:
        x, y, z = (int(spec[axis]) for axis in "xyz")
    except (KeyError, TypeError, ValueError):
        return "x, y and z must be whole numbers"
    shape = SHAPES[part]
    width, depth = (
        (shape.width, shape.depth) if rotation in (0, 180) else (shape.depth, shape.width)
    )
    if not (x >= 0 and x + width <= GRID and y >= 0 and y + depth <= GRID):
        return f"it must lie within x and y 0 to {GRID - 1}"
    if not 0 <= z <= MAX_Z - shape.height:
        return f"z must be from 0 to {MAX_Z - shape.height}"
    return None


class Build:
    """The pieces placed so far. Ids are `p1`, `p2`, … and never reused.

    A build from a scan (ADR-0013) may use only the pieces the scan found: `stock` counts each
    part and color pair, and `place` refuses a piece once every one of its pair is in the build.
    Without `stock`, any catalogue piece goes. Either way, at most `max_pieces`.
    """

    def __init__(self, stock: list[PartCount] | None = None, max_pieces: int = MAX_PIECES):
        self.pieces: dict[str, Piece] = {}
        self._next = 1
        self.stock: dict[tuple[str, str], int] | None = (
            None if stock is None else {(line.part, line.color): line.quantity for line in stock}
        )
        self.max_pieces = min(max_pieces, MAX_PIECES)

    def place(self, specs: list[dict]) -> list[dict]:
        """Places each piece that fits, in order: its id, or why it couldn't go."""
        answers: list[dict] = []
        for spec in specs:
            if problem := problem_with(spec) or self._out_of_stock(spec["part"], spec["color"]):
                answers.append({"error": problem})
                continue
            if len(self.pieces) >= self.max_pieces:
                answers.append(
                    {"error": f"the build already has {self.max_pieces} pieces, the most"}
                )
                continue
            piece = Piece(
                id=f"p{self._next}",
                part=spec["part"],
                color=spec["color"],
                x=int(spec["x"]),
                y=int(spec["y"]),
                z=int(spec["z"]),
                rotation=spec["rotation"],
            )
            self._next += 1
            self.pieces[piece.id] = piece
            answers.append({"id": piece.id})
        return answers

    def _out_of_stock(self, part: str, color: str) -> str | None:
        """Why the person has no more of this piece to place, or None."""
        if self.stock is None:
            return None
        have = self.stock.get((part, color), 0)
        if have == 0:
            return f"the person has no {color} {part}"
        placed = sum(p.part == part and p.color == color for p in self.pieces.values())
        if placed >= have:
            return f"the person has only {have} {color} {part}, and all are in the build"
        return None

    def left(self) -> list[dict]:
        """The pieces still unused, for `validate`'s report."""
        if self.stock is None:
            return []
        placed = Counter((p.part, p.color) for p in self.pieces.values())
        return [
            {"part": part, "color": color, "quantity": have - placed[(part, color)]}
            for (part, color), have in self.stock.items()
            if have > placed[(part, color)]
        ]

    def remove(self, ids: list[str]) -> list[str]:
        """Removes pieces by id; answers the ids it removed."""
        return [piece_id for piece_id in ids if self.pieces.pop(piece_id, None) is not None]

    def snapshot(self) -> tuple[Piece, ...]:
        return tuple(self.pieces.values())


def size_of(pieces: tuple[Piece, ...]) -> dict:
    """The build's extent: studs wide and deep, plates high."""
    if not pieces:
        return {"studsWide": 0, "studsDeep": 0, "plates": 0}
    xs = [p.x for p in pieces] + [p.x + p.footprint[0] for p in pieces]
    ys = [p.y for p in pieces] + [p.y + p.footprint[1] for p in pieces]
    zs = [p.z for p in pieces] + [p.z + p.shape.height for p in pieces]
    return {
        "studsWide": max(xs) - min(xs),
        "studsDeep": max(ys) - min(ys),
        "plates": max(zs) - min(zs),
    }


def parts_of(pieces: tuple[Piece, ...]) -> list[PartCount]:
    """Every part the build needs, sorted like an inventory."""
    return merge(PartCount(part=p.part, color=p.color, quantity=1) for p in pieces)


def part_name(part: PartId, color: ColorId, locale: Locale) -> str:
    """ "red 2×4 brick" in English; "brick 2×4 merah" in Indonesian, as the contract writes it."""
    kind, size = part.split("-")
    size = size.replace("x", "×")
    english, indonesian = COLOR_NAMES[color]
    return f"{kind} {size} {indonesian}" if locale == "id" else f"{english} {size} {kind}"


MAX_STEPS = 8


def layer_steps(pieces: tuple[Piece, ...], locale: Locale) -> list[str]:
    """Plain steps from the bottom up, one per level, merged to at most `MAX_STEPS`. For a
    build that ran out of budget before Claude wrote its own."""
    levels: dict[int, list[Piece]] = {}
    for piece in pieces:
        levels.setdefault(piece.z, []).append(piece)
    groups = [levels[z] for z in sorted(levels)]
    while len(groups) > MAX_STEPS:
        # Two neighbouring levels become one step, until there are few enough.
        groups = [
            [*groups[i], *(groups[i + 1] if i + 1 < len(groups) else [])]
            for i in range(0, len(groups), 2)
        ]
    lead = "Pasang" if locale == "id" else "Add"
    steps = []
    for group in groups:
        counts = Counter((p.part, p.color) for p in group)
        lines = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        names = [f"{count} × {part_name(part, color, locale)}" for (part, color), count in lines]
        steps.append(f"{lead} {', '.join(names)}.")
    return steps


def plain_text(pieces: tuple[Piece, ...], locale: Locale) -> tuple[str, str]:
    """A title and a summary for a build Claude didn't get to name."""
    if locale == "id":
        return "Rakitan dari kepinganmu", f"Rakitan {len(pieces)} keping dari kepinganmu."
    return "A build from your pieces", f"A {len(pieces)}-piece build from your pieces."
