"""Turtle: a green slope for a shell on a flat green base, with its head in front."""

import bricklayer as bl
from brickwright import TestContext

META = {
    "theme": "animals",
    "difficulty": "easy",
    "title": {"en": "Turtle", "id": "Kura-kura"},
    "summary": {
        "en": "A green slope shell on a flat base.",
        "id": "Tempurung slope hijau di atas alas datar.",
    },
    "steps": {
        "en": [
            "Lay a green 2×4 plate as the base.",
            "Put the green 2×2 slope in the middle as the shell.",
            "Add a green 1×1 brick at the front as the head.",
        ],
        "id": [
            "Letakkan plate 2×4 hijau sebagai alas.",
            "Pasang slope 2×2 hijau di tengah sebagai tempurung.",
            "Tambahkan brick 1×1 hijau di depan sebagai kepala.",
        ],
    },
}

# The front is -y and +x is the left. A quarter turn sets a 4×2 part 2 wide and 4 deep.
ALONG = bl.Rotation.DEG_90


def create_build():
    build = bl.LegoBuild()
    build.add(bl.Element("3020", bl.Color.GREEN), (0, 0, 0), ALONG, name="base")
    build.add(bl.Element("3039", bl.Color.GREEN), (0, 1, bl.PLATE), name="shell")
    build.add(bl.Element("3005", bl.Color.GREEN), (1, 0, bl.PLATE), name="head")
    return build


def run_tests():
    build = create_build()
    tests = TestContext(build)
    tests.check_no_floating()
    tests.check_no_collisions()
    tests.check_connected(expected_components=1)
    return tests.report()
