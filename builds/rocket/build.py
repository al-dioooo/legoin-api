"""Rocket: a white tower standing on two red fins, with a red nose."""

import bricklayer as bl
from brickwright import TestContext

META = {
    "theme": "vehicles",
    "difficulty": "hard",
    "title": {"en": "Rocket", "id": "Roket"},
    "summary": {
        "en": "A white tower with red fins and a nose cone.",
        "id": "Menara putih dengan sirip merah dan hidung lancip.",
    },
    "steps": {
        "en": [
            "Stack three white 2×2 bricks as the body.",
            "Press the two red 1×2 bricks under the body at opposite corners, each sticking out one stud to the side, as fins.",
            "Top it with the red 2×2 slope as the nose.",
        ],
        "id": [
            "Tumpuk tiga brick 2×2 putih sebagai badan.",
            "Pasang dua brick 1×2 merah di bawah badan, di sudut yang berseberangan, masing-masing menjorok satu stud ke samping, sebagai sirip.",
            "Tutup dengan slope 2×2 merah sebagai hidung roket.",
        ],
    },
}


def create_build():
    build = bl.LegoBuild()
    # Each fin holds one stud of the body and sticks out one stud: right at the front,
    # left at the back.
    build.add(bl.Element("3004", bl.Color.RED), (-1, 0, 0), name="fin_right")
    build.add(bl.Element("3004", bl.Color.RED), (1, 1, 0), name="fin_left")
    below = build.add(bl.Element("3003", bl.Color.WHITE), (0, 0, 1), name="body_1")
    for level in (2, 3):
        below = build.stack(bl.Element("3003", bl.Color.WHITE), on=below, name=f"body_{level}")
    build.stack(bl.Element("3039", bl.Color.RED), on=below, name="nose")
    return build


def run_tests():
    build = create_build()
    tests = TestContext(build)
    tests.check_no_floating()
    tests.check_no_collisions()
    tests.check_connected(expected_components=1)
    return tests.report()
