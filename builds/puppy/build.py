"""Puppy: a brown body on four legs, a head sticking out in front, black ears and nose."""

import bricklayer as bl
from brickwright import TestContext

META = {
    "theme": "animals",
    "difficulty": "hard",
    "title": {"en": "Puppy", "id": "Anak anjing"},
    "summary": {
        "en": "Brown body, black ears and a round nose.",
        "id": "Badan cokelat, telinga hitam, dan hidung bulat.",
    },
    "steps": {
        "en": [
            "Stand the brown 2×4 brick on four brown 1×1 bricks as the body and legs.",
            "Add the brown 2×2 brick on the front, sticking out one stud, as the head.",
            "Put the black 1×1 round on the front of the head as the nose, and the two black 1×2 plates on its back row, sticking out to the sides, as ears.",
        ],
        "id": [
            "Berdirikan brick 2×4 cokelat di atas empat brick 1×1 cokelat sebagai badan dan kaki.",
            "Pasang brick 2×2 cokelat di bagian depan, menjorok satu stud, sebagai kepala.",
            "Taruh round 1×1 hitam di depan kepala sebagai hidung, lalu dua plate 1×2 hitam di baris belakangnya, menjorok ke samping, sebagai telinga.",
        ],
    },
}

# The front is -y and +x is the left. A quarter turn sets a 4×2 part 2 wide and 4 deep.
ALONG = bl.Rotation.DEG_90
BROWN = bl.Color.REDDISH_BROWN


def create_build():
    build = bl.LegoBuild()
    for x, y in ((0, 1), (1, 1), (0, 4), (1, 4)):
        build.add(bl.Element("3005", BROWN), (x, y, 0), name=f"leg_{x}_{y}")
    build.add(bl.Element("3001", BROWN), (0, 1, 1), ALONG, name="body")
    build.add(bl.Element("3003", BROWN), (0, 0, 2), name="head")
    build.add(bl.Element("3062b", bl.Color.BLACK), (1, 0, 3), name="nose")
    build.add(bl.Element("3023", bl.Color.BLACK), (-1, 1, 3), name="ear_right")
    build.add(bl.Element("3023", bl.Color.BLACK), (1, 1, 3), name="ear_left")
    return build


def run_tests():
    build = create_build()
    tests = TestContext(build)
    tests.check_no_floating()
    tests.check_no_collisions()
    tests.check_connected(expected_components=1)
    return tests.report()
