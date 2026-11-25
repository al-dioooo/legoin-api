"""Penguin: orange feet, a white belly, a black back and head, and a yellow beak."""

import bricklayer as bl
from brickwright import TestContext

META = {
    "theme": "animals",
    "difficulty": "medium",
    "title": {"en": "Penguin", "id": "Penguin"},
    "summary": {
        "en": "Black body, white belly, orange feet.",
        "id": "Badan hitam, perut putih, kaki oranye.",
    },
    "steps": {
        "en": [
            "Start with the orange 2×2 plate as the feet.",
            "Put the white 1×2 brick on its front row as the belly, and the black 1×2 brick behind it as the back.",
            "Put the black 2×2 brick on the black brick, sticking out one stud at the back, as the head, and the yellow 1×1 round on the belly in front of it as the beak.",
        ],
        "id": [
            "Mulai dengan plate 2×2 oranye sebagai kaki.",
            "Pasang brick 1×2 putih di baris depannya sebagai perut, lalu brick 1×2 hitam di belakangnya sebagai punggung.",
            "Pasang brick 2×2 hitam di atas brick hitam, menjorok satu stud ke belakang, sebagai kepala, lalu round 1×1 kuning di atas perut, di depan kepala, sebagai paruh.",
        ],
    },
}


def create_build():
    build = bl.LegoBuild()
    build.add(bl.Element("3022", bl.Color.ORANGE), (0, 0, 0), name="feet")
    build.add(bl.Element("3004", bl.Color.WHITE), (0, 0, bl.PLATE), name="belly")
    build.add(bl.Element("3004", bl.Color.BLACK), (0, 1, bl.PLATE), name="back")
    # The head sits on the back and leans back a stud, leaving the belly's top for the beak.
    build.add(bl.Element("3003", bl.Color.BLACK), (0, 1, 1 + bl.PLATE), name="head")
    build.add(bl.Element("3062b", bl.Color.YELLOW), (1, 0, 1 + bl.PLATE), name="beak")
    return build


def run_tests():
    build = create_build()
    tests = TestContext(build)
    tests.check_no_floating()
    tests.check_no_collisions()
    tests.check_connected(expected_components=1)
    return tests.report()
