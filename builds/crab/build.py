"""Crab: a wide red body with a claw sticking out at each front corner, and two black eyes."""

import bricklayer as bl
from brickwright import TestContext

META = {
    "theme": "animals",
    "difficulty": "medium",
    "title": {"en": "Crab", "id": "Kepiting"},
    "summary": {
        "en": "A red body with two claws and black eyes.",
        "id": "Badan merah dengan dua capit dan mata hitam.",
    },
    "steps": {
        "en": [
            "Use the red 2×4 brick as the body.",
            "Put a red 1×2 brick on each front corner, sticking out one stud to the side, as claws.",
            "Add the two black 1×1 rounds between the claws as eyes.",
        ],
        "id": [
            "Pakai brick 2×4 merah sebagai badan.",
            "Pasang brick 1×2 merah di tiap sudut depan, menjorok satu stud ke samping, sebagai capit.",
            "Tambahkan dua round 1×1 hitam di antara capit sebagai mata.",
        ],
    },
}


def create_build():
    build = bl.LegoBuild()
    # The body lies across: 4 studs wide, 2 deep. Its front is -y.
    build.add(bl.Element("3001", bl.Color.RED), (0, 0, 0), name="body")
    build.add(bl.Element("3004", bl.Color.RED), (-1, 0, 1), name="claw_right")
    build.add(bl.Element("3004", bl.Color.RED), (3, 0, 1), name="claw_left")
    build.add(bl.Element("3062b", bl.Color.BLACK), (1, 0, 1), name="eye_right")
    build.add(bl.Element("3062b", bl.Color.BLACK), (2, 0, 1), name="eye_left")
    return build


def run_tests():
    build = create_build()
    tests = TestContext(build)
    tests.check_no_floating()
    tests.check_no_collisions()
    tests.check_connected(expected_components=1)
    return tests.report()
