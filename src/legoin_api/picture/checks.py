"""BrickLayer's checks and LDraw export for a picture build. Needs the `picture` extra.

The checks are BrickWright's: no collisions, nothing floating, one connected assembly (its
`TestContext.check_no_collisions`, `check_no_floating` and `check_connected`), read straight
from the `LegoBuild` so each problem names the agent's piece ids.
"""

from legoin_api.catalogue import DESIGN_IDS, REBRICKABLE_COLOR_IDS
from legoin_api.picture.grid import Piece

# BrickLayer's names for the catalogue's colors. Their LDraw codes are the Rebrickable ids.
BRICKLAYER_COLORS = {
    "red": "RED",
    "blue": "BLUE",
    "yellow": "YELLOW",
    "green": "GREEN",
    "black": "BLACK",
    "white": "WHITE",
    "light-gray": "LIGHT_BLUISH_GRAY",
    "dark-gray": "DARK_BLUISH_GRAY",
    "orange": "ORANGE",
    "brown": "REDDISH_BROWN",
}
ROTATIONS = {0: "DEG_0", 90: "DEG_90", 180: "DEG_180", 270: "DEG_270"}
SHOWN = 20
"""Problems named per kind, at most."""


def to_bricklayer(pieces: tuple[Piece, ...]):
    """The pieces as a `LegoBuild`, each named by its id. BrickLayer's axes are the grid's: its
    +x is the model's left, the viewer's right."""
    import bricklayer as bl

    build = bl.LegoBuild()
    for piece in pieces:
        element = bl.Element(DESIGN_IDS[piece.part], bl.Color[BRICKLAYER_COLORS[piece.color]])
        build.add(
            element,
            (piece.x, piece.y, piece.z * bl.PLATE),
            bl.Rotation[ROTATIONS[piece.rotation]],
            name=piece.id,
        )
    return build


class BrickLayerChecks:
    def problems(self, pieces: tuple[Piece, ...]) -> list[str]:
        build = to_bricklayer(pieces)

        def name(placement) -> str:
            return build.name_of(placement) or "?"

        problems = []
        if pairs := build.colliding_placements:
            named = "; ".join(f"{name(a)} overlaps {name(b)}" for a, b in pairs[:SHOWN])
            problems.append(f"{len(pairs)} collisions: {named}")
        if floating := build.floating_placements:
            named = ", ".join(sorted(name(p) for p in floating)[:SHOWN])
            problems.append(
                f"{len(floating)} floating, not held by a stud below or the ground: {named}"
            )
        groups = build.connected_components
        if len(groups) != 1:
            biggest_first = sorted((sorted(name(p) for p in g) for g in groups), key=len)[::-1]
            named = " | ".join(", ".join(group[:12]) for group in biggest_first[:6])
            problems.append(f"{len(groups)} separate assemblies, not one: {named}")
        return problems


def check_colors() -> None:
    """Fails loudly if BrickLayer's colors aren't the catalogue's LDraw codes."""
    import bricklayer as bl

    for color, name in BRICKLAYER_COLORS.items():
        code = bl.Color[name].ldraw_id
        if code != REBRICKABLE_COLOR_IDS[color]:
            raise RuntimeError(f"BrickLayer's {name} is LDraw {code}, not {color}")


CREDIT = (
    "0 Author: Legoin, designed by Claude from a picture, built with BrickLayer\n"
    "0 // Parts: the LDraw parts library, CC BY 4.0, https://library.ldraw.org\n"
    "0 // LEGO is a trademark of the LEGO Group, which does not sponsor, authorize or\n"
    "0 // endorse this file.\n"
)
"""The credit header the library's `.ldr` files carry (ADR-0010), naming who designed it."""


def ldraw(pieces: tuple[Piece, ...], title: str) -> str:
    """The build as an LDraw file under the credit header."""
    from bricklayer import to_ldraw

    # One line each: a title can't add lines to the header.
    title = " ".join(title.split())
    return f"0 {title}\n0 Name: legoin-picture-build.ldr\n{CREDIT}{to_ldraw(to_bricklayer(pieces))}"
