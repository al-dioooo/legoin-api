"""Small bridge: two brown pillars, a black deck across them and a smooth gray road."""

import bricklayer as bl
from brickwright import TestContext

META = {
    "theme": "buildings",
    "difficulty": "hard",
    "title": {"en": "Small bridge", "id": "Jembatan kecil"},
    "summary": {
        "en": "Brown pillars, a plate deck and a smooth road.",
        "id": "Tiang cokelat, lantai plate, dan jalan yang mulus.",
    },
    "steps": {
        "en": [
            "Build two pillars, each of two brown 1×2 bricks stacked, two studs apart.",
            "Lay the black 2×4 plate across them as the deck.",
            "Cover the middle of the deck with the two light gray 1×2 tiles as the road.",
        ],
        "id": [
            "Buat dua tiang, masing-masing dari dua brick 1×2 cokelat yang ditumpuk, berjarak dua stud.",
            "Letakkan plate 2×4 hitam melintang di atasnya sebagai lantai jembatan.",
            "Tutup bagian tengah lantai dengan dua tile 1×2 abu-abu terang sebagai jalan.",
        ],
    },
}

# The bridge spans along x. A quarter turn sets a 1×2 brick 1 wide and 2 deep.
ALONG = bl.Rotation.DEG_90
BROWN = bl.Color.REDDISH_BROWN


def create_build():
    build = bl.LegoBuild()
    for x in (0, 3):
        for z in (0, 1):
            build.add(bl.Element("3004", BROWN), (x, 0, z), ALONG, name=f"pillar_{x}_{z}")
    build.add(bl.Element("3020", bl.Color.BLACK), (0, 0, 2), name="deck")
    for y in (0, 1):
        build.add(
            bl.Element("3069b", bl.Color.LIGHT_BLUISH_GRAY), (1, y, 2 + bl.PLATE), name=f"road_{y}"
        )
    return build


def run_tests():
    build = create_build()
    tests = TestContext(build)
    tests.check_no_floating()
    tests.check_no_collisions()
    tests.check_connected(expected_components=1)
    return tests.report()
