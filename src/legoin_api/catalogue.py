"""The catalogue: the contract's 13 parts and 10 colors, with the app's own ids.

The ids are legoin-web's (`src/lib/bricks.ts`). Each part maps to one LEGO design id and
each color to one Rebrickable color id, as in the contract's catalogue tables.
"""

from typing import Literal, get_args

PartId = Literal[
    "brick-1x1",
    "brick-1x2",
    "brick-1x3",
    "brick-1x4",
    "brick-2x2",
    "brick-2x3",
    "brick-2x4",
    "plate-1x2",
    "plate-2x2",
    "plate-2x4",
    "slope-2x2",
    "tile-1x2",
    "round-1x1",
]

ColorId = Literal[
    "red",
    "blue",
    "yellow",
    "green",
    "black",
    "white",
    "light-gray",
    "dark-gray",
    "orange",
    "brown",
]

PART_IDS: tuple[PartId, ...] = get_args(PartId)
COLOR_IDS: tuple[ColorId, ...] = get_args(ColorId)

DESIGN_IDS: dict[PartId, str] = {
    "brick-1x1": "3005",
    "brick-1x2": "3004",
    "brick-1x3": "3622",
    "brick-1x4": "3010",
    "brick-2x2": "3003",
    "brick-2x3": "3002",
    "brick-2x4": "3001",
    "plate-1x2": "3023",
    "plate-2x2": "3022",
    "plate-2x4": "3020",
    "slope-2x2": "3039",
    "tile-1x2": "3069b",
    "round-1x1": "3062b",
}

REBRICKABLE_COLOR_IDS: dict[ColorId, int] = {
    "red": 4,
    "blue": 1,
    "yellow": 14,
    "green": 2,
    "black": 0,
    "white": 15,
    "light-gray": 71,
    "dark-gray": 72,
    "orange": 25,
    "brown": 70,
}
