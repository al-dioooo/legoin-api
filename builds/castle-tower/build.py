"""Castle tower: light gray walls on a dark gray base, with battlements on the corners."""

import bricklayer as bl
from brickwright import TestContext

META = {
    "theme": "buildings",
    "difficulty": "hard",
    "title": {"en": "Castle tower", "id": "Menara kastel"},
    "summary": {
        "en": "Gray walls with battlements on top.",
        "id": "Dinding abu-abu dengan benteng di atasnya.",
    },
    "steps": {
        "en": [
            "Start with the dark gray 2×4 plate as the base.",
            "Stack two light gray 2×4 bricks as the walls.",
            "Put four light gray 1×1 bricks on the corners as battlements.",
        ],
        "id": [
            "Mulai dengan plate 2×4 abu-abu gelap sebagai dasar.",
            "Tumpuk dua brick 2×4 abu-abu terang sebagai dinding.",
            "Pasang empat brick 1×1 abu-abu terang di sudut sebagai benteng.",
        ],
    },
}

GRAY = bl.Color.LIGHT_BLUISH_GRAY


def create_build():
    build = bl.LegoBuild()
    # The tower lies across: 4 studs wide, 2 deep.
    build.add(bl.Element("3020", bl.Color.DARK_BLUISH_GRAY), (0, 0, 0), name="base")
    for level in (0, 1):
        build.add(bl.Element("3001", GRAY), (0, 0, bl.PLATE + level), name=f"wall_{level}")
    for x in (0, 3):
        for y in (0, 1):
            build.add(bl.Element("3005", GRAY), (x, y, 2 + bl.PLATE), name=f"battlement_{x}_{y}")
    return build


def run_tests():
    build = create_build()
    tests = TestContext(build)
    tests.check_no_floating()
    tests.check_no_collisions()
    tests.check_connected(expected_components=1)
    return tests.report()
