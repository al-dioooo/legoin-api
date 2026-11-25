"""Rubber duck: a yellow body and head, with an orange beak sticking out in front."""

import bricklayer as bl
from brickwright import TestContext

META = {
    "theme": "animals",
    "difficulty": "easy",
    "title": {"en": "Rubber duck", "id": "Bebek karet"},
    "summary": {
        "en": "Yellow body and head, orange beak.",
        "id": "Badan dan kepala kuning, paruh oranye.",
    },
    "steps": {
        "en": [
            "Use the yellow 2×4 brick as the body.",
            "Put the orange 2×2 plate on its front end, sticking out one stud, as the beak, and the yellow 1×2 plate right behind it.",
            "Put the yellow 2×2 brick on the back of the beak and the yellow plate as the head.",
        ],
        "id": [
            "Pakai brick 2×4 kuning sebagai badan.",
            "Pasang plate 2×2 oranye di ujung depan, menjorok satu stud, sebagai paruh, lalu plate 1×2 kuning tepat di belakangnya.",
            "Pasang brick 2×2 kuning di atas bagian belakang paruh dan plate kuning sebagai kepala.",
        ],
    },
}

# The front is -y and +x is the left. A quarter turn sets a 4×2 part 2 wide and 4 deep.
ALONG = bl.Rotation.DEG_90


def create_build():
    build = bl.LegoBuild()
    build.add(bl.Element("3001", bl.Color.YELLOW), (0, 1, 0), ALONG, name="body")
    # The beak overhangs the body by a stud; the head holds its back row.
    build.add(bl.Element("3022", bl.Color.ORANGE), (0, 0, 1), name="beak")
    build.add(bl.Element("3023", bl.Color.YELLOW), (0, 2, 1), name="neck")
    build.add(bl.Element("3003", bl.Color.YELLOW), (0, 1, 1 + bl.PLATE), name="head")
    return build


def run_tests():
    build = create_build()
    tests = TestContext(build)
    tests.check_no_floating()
    tests.check_no_collisions()
    tests.check_connected(expected_components=1)
    return tests.report()
