"""Little race car: a red body on a black chassis, a green hood and yellow headlights."""

import bricklayer as bl
from brickwright import TestContext

META = {
    "theme": "vehicles",
    "difficulty": "easy",
    "title": {"en": "Little race car", "id": "Mobil balap kecil"},
    "summary": {
        "en": "Red body, green hood, yellow lights.",
        "id": "Badan merah, kap hijau, lampu kuning.",
    },
    "steps": {
        "en": [
            "Put the red 2×4 brick on the black 2×4 plate, one stud back, so the plate's front row shows.",
            "Add the green 2×2 slope on the front of the red brick as the hood, and the black 2×2 brick behind it as the cab.",
            "Put the two yellow 1×1 rounds on the plate's front row as headlights.",
        ],
        "id": [
            "Pasang brick 2×4 merah di atas plate 2×4 hitam, mundur satu stud, supaya baris depan plate kelihatan.",
            "Taruh slope 2×2 hijau di depan brick merah sebagai kap mesin, dan brick 2×2 hitam di belakangnya sebagai kabin.",
            "Pasang dua round 1×1 kuning di baris depan plate sebagai lampu depan.",
        ],
    },
}

# The front is -y and +x is the left. A quarter turn sets a 4×2 part 2 wide and 4 deep.
ALONG = bl.Rotation.DEG_90


def create_build():
    build = bl.LegoBuild()
    build.add(bl.Element("3020", bl.Color.BLACK), (0, 0, 0), ALONG, name="chassis")
    body = build.add(bl.Element("3001", bl.Color.RED), (0, 1, bl.PLATE), ALONG, name="body")
    build.stack(bl.Element("3039", bl.Color.GREEN), on=body, name="hood")
    build.stack(bl.Element("3003", bl.Color.BLACK), on=body, offset=(0, 2, 0), name="cab")
    build.add(bl.Element("3062b", bl.Color.YELLOW), (0, 0, bl.PLATE), name="light_right")
    build.add(bl.Element("3062b", bl.Color.YELLOW), (1, 0, bl.PLATE), name="light_left")
    return build


def run_tests():
    build = create_build()
    tests = TestContext(build)
    tests.check_no_floating()
    tests.check_no_collisions()
    tests.check_connected(expected_components=1)
    return tests.report()
