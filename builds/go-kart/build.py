"""Go-kart: a low black chassis, an orange nose, a yellow steering wheel and a blue seat."""

import bricklayer as bl
from brickwright import TestContext

META = {
    "theme": "vehicles",
    "difficulty": "easy",
    "title": {"en": "Go-kart", "id": "Gokar"},
    "summary": {"en": "A low kart with a blue seat.", "id": "Gokar rendah dengan kursi biru."},
    "steps": {
        "en": [
            "Use the black 2×4 plate as the chassis.",
            "Put the blue 2×3 brick at the back as the seat.",
            "Add the orange 1×2 brick at the front and the yellow round as the steering wheel.",
        ],
        "id": [
            "Pakai plate 2×4 hitam sebagai sasis.",
            "Pasang brick 2×3 biru di belakang sebagai kursi.",
            "Tambahkan brick 1×2 oranye di depan dan round kuning sebagai setir.",
        ],
    },
}

# The front is -y and +x is the left. A quarter turn sets 4×2 and 3×2 parts lengthwise.
ALONG = bl.Rotation.DEG_90


def create_build():
    build = bl.LegoBuild()
    build.add(bl.Element("3020", bl.Color.BLACK), (0, 0, 0), ALONG, name="chassis")
    build.add(bl.Element("3004", bl.Color.ORANGE), (0, 0, bl.PLATE), name="nose")
    build.add(bl.Element("3002", bl.Color.BLUE), (0, 1, bl.PLATE), ALONG, name="seat")
    build.add(bl.Element("3062b", bl.Color.YELLOW), (1, 0, 1 + bl.PLATE), name="steering_wheel")
    return build


def run_tests():
    build = create_build()
    tests = TestContext(build)
    tests.check_no_floating()
    tests.check_no_collisions()
    tests.check_connected(expected_components=1)
    return tests.report()
