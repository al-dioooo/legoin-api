"""Windmill: a white tower, brown sails sticking out at the top and a red roof."""

import bricklayer as bl
from brickwright import TestContext

META = {
    "theme": "buildings",
    "difficulty": "medium",
    "title": {"en": "Windmill", "id": "Kincir angin"},
    "summary": {
        "en": "A white tower, a red roof and sails.",
        "id": "Menara putih, atap merah, dan baling-baling.",
    },
    "steps": {
        "en": [
            "Stack three white 2×2 bricks as the tower.",
            "Put the two brown 1×2 plates along the front of the top, each sticking out one stud to its side, as sails, and the white 1×2 plate behind them.",
            "Top it with the red 2×2 slope as the roof.",
        ],
        "id": [
            "Tumpuk tiga brick 2×2 putih sebagai menara.",
            "Pasang dua plate 1×2 cokelat berjajar di sisi depan atasnya, masing-masing menjorok satu stud ke samping, sebagai baling-baling, lalu plate 1×2 putih di belakangnya.",
            "Tutup dengan slope 2×2 merah sebagai atap.",
        ],
    },
}


def create_build():
    build = bl.LegoBuild()
    for level in range(3):
        build.add(bl.Element("3003", bl.Color.WHITE), (0, 0, level), name=f"tower_{level}")
    build.add(bl.Element("3023", bl.Color.REDDISH_BROWN), (-1, 0, 3), name="sail_right")
    build.add(bl.Element("3023", bl.Color.REDDISH_BROWN), (1, 0, 3), name="sail_left")
    build.add(bl.Element("3023", bl.Color.WHITE), (0, 1, 3), name="filler")
    build.add(bl.Element("3039", bl.Color.RED), (0, 0, 3 + bl.PLATE), name="roof")
    return build


def run_tests():
    build = create_build()
    tests = TestContext(build)
    tests.check_no_floating()
    tests.check_no_collisions()
    tests.check_connected(expected_components=1)
    return tests.report()
