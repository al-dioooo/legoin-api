"""Lighthouse: a red and white striped tower on a dark gray base, with a yellow light."""

import bricklayer as bl
from brickwright import TestContext

META = {
    "theme": "buildings",
    "difficulty": "medium",
    "title": {"en": "Lighthouse", "id": "Mercusuar"},
    "summary": {
        "en": "A striped tower with a yellow light.",
        "id": "Menara bergaris dengan lampu kuning.",
    },
    "steps": {
        "en": [
            "Start with a dark gray 2×2 plate as the base.",
            "Stack 2×2 bricks red, white, red, white.",
            "Finish with a yellow round on top as the light.",
        ],
        "id": [
            "Mulai dengan plate 2×2 abu-abu gelap sebagai dasar.",
            "Tumpuk brick 2×2 merah, putih, merah, putih.",
            "Tutup dengan round kuning di atas sebagai lampu.",
        ],
    },
}


def create_build():
    build = bl.LegoBuild()
    below = build.add(bl.Element("3022", bl.Color.DARK_BLUISH_GRAY), (0, 0, 0), name="base")
    for level, color in enumerate((bl.Color.RED, bl.Color.WHITE) * 2, start=1):
        below = build.stack(bl.Element("3003", color), on=below, name=f"stripe_{level}")
    build.stack(bl.Element("3062b", bl.Color.YELLOW), on=below, offset=(1, 0, 0), name="light")
    return build


def run_tests():
    build = create_build()
    tests = TestContext(build)
    tests.check_no_floating()
    tests.check_no_collisions()
    tests.check_connected(expected_components=1)
    return tests.report()
