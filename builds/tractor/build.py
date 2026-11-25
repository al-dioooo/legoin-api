"""Tractor: a green body on four black wheels, with a white cab at the back."""

import bricklayer as bl
from brickwright import TestContext

META = {
    "theme": "vehicles",
    "difficulty": "medium",
    "title": {"en": "Tractor", "id": "Traktor"},
    "summary": {
        "en": "Green body, white cab, black wheels.",
        "id": "Badan hijau, kabin putih, roda hitam.",
    },
    "steps": {
        "en": [
            "Lay the green 2×4 brick down as the body.",
            "Put the white 2×2 brick at the back as the cab.",
            "Press four black 1×1 bricks under its corners as wheels.",
        ],
        "id": [
            "Letakkan brick 2×4 hijau sebagai badan.",
            "Pasang brick 2×2 putih di belakang sebagai kabin.",
            "Pasang empat brick 1×1 hitam di bawah keempat sudutnya sebagai roda.",
        ],
    },
}

# The front is -y and +x is the left. A quarter turn sets a 4×2 part 2 wide and 4 deep.
ALONG = bl.Rotation.DEG_90


def create_build():
    build = bl.LegoBuild()
    for x, y in ((0, 0), (1, 0), (0, 3), (1, 3)):
        build.add(bl.Element("3005", bl.Color.BLACK), (x, y, 0), name=f"wheel_{x}_{y}")
    body = build.add(bl.Element("3001", bl.Color.GREEN), (0, 0, 1), ALONG, name="body")
    build.stack(bl.Element("3003", bl.Color.WHITE), on=body, offset=(0, 2, 0), name="cab")
    return build


def run_tests():
    build = create_build()
    tests = TestContext(build)
    tests.check_no_floating()
    tests.check_no_collisions()
    tests.check_connected(expected_components=1)
    return tests.report()
