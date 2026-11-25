"""Snail: a dark gray shell on a long brown body, with a yellow eye."""

import bricklayer as bl
from brickwright import TestContext

META = {
    "theme": "animals",
    "difficulty": "easy",
    "title": {"en": "Snail", "id": "Siput"},
    "summary": {
        "en": "A dark gray shell on a brown body.",
        "id": "Cangkang abu-abu gelap di atas badan cokelat.",
    },
    "steps": {
        "en": [
            "Lay the brown 1×3 brick down as the body.",
            "Put the dark gray 2×2 brick on the back half as the shell.",
            "Add the yellow round at the front as the eye.",
        ],
        "id": [
            "Letakkan brick 1×3 cokelat sebagai badan.",
            "Pasang brick 2×2 abu-abu gelap di setengah belakang sebagai cangkang.",
            "Tambahkan round kuning di depan sebagai mata.",
        ],
    },
}

# The front is -y and +x is the left. A quarter turn sets a 1×3 brick 1 wide and 3 deep.
ALONG = bl.Rotation.DEG_90


def create_build():
    build = bl.LegoBuild()
    build.add(bl.Element("3622", bl.Color.REDDISH_BROWN), (0, 0, 0), ALONG, name="body")
    build.add(bl.Element("3003", bl.Color.DARK_BLUISH_GRAY), (0, 1, 1), name="shell")
    build.add(bl.Element("3062b", bl.Color.YELLOW), (0, 0, 1), name="eye")
    return build


def run_tests():
    build = create_build()
    tests = TestContext(build)
    tests.check_no_floating()
    tests.check_no_collisions()
    tests.check_connected(expected_components=1)
    return tests.report()
