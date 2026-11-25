"""Delivery truck: a blue cab and a white cargo box on a black chassis with round wheels."""

import bricklayer as bl
from brickwright import TestContext

META = {
    "theme": "vehicles",
    "difficulty": "medium",
    "title": {"en": "Delivery truck", "id": "Truk pengantar"},
    "summary": {
        "en": "Blue cab, white cargo box, round wheels.",
        "id": "Kabin biru, bak putih, roda bulat.",
    },
    "steps": {
        "en": [
            "Lay the black 2×4 plate and the black 2×2 plate end to end as the chassis.",
            "Put the blue 2×2 brick on the front as the cab, then stack two white 2×4 bricks behind it, across both plates, as the cargo box.",
            "Turn it over and press four black 1×1 rounds under the front and the back as wheels.",
        ],
        "id": [
            "Letakkan plate 2×4 hitam dan plate 2×2 hitam berjajar sebagai sasis.",
            "Pasang brick 2×2 biru di depan sebagai kabin, lalu tumpuk dua brick 2×4 putih di belakangnya, menyambung kedua plate, sebagai bak.",
            "Balik truknya, lalu pasang empat round 1×1 hitam di bawah bagian depan dan belakang sebagai roda.",
        ],
    },
}

# The front is -y and +x is the left. A quarter turn sets a 4×2 part 2 wide and 4 deep.
ALONG = bl.Rotation.DEG_90


def create_build():
    build = bl.LegoBuild()
    for x, y in ((0, 0), (1, 0), (0, 4), (1, 4)):
        build.add(bl.Element("3062b", bl.Color.BLACK), (x, y, 0), name=f"wheel_{x}_{y}")
    build.add(bl.Element("3020", bl.Color.BLACK), (0, 0, 1), ALONG, name="chassis_front")
    build.add(bl.Element("3022", bl.Color.BLACK), (0, 4, 1), name="chassis_back")
    build.add(bl.Element("3003", bl.Color.BLUE), (0, 0, 1 + bl.PLATE), name="cab")
    lower = build.add(bl.Element("3001", bl.Color.WHITE), (0, 2, 1 + bl.PLATE), ALONG, name="box")
    build.stack(bl.Element("3001", bl.Color.WHITE), on=lower, rotation=ALONG, name="box_top")
    return build


def run_tests():
    build = create_build()
    tests = TestContext(build)
    tests.check_no_floating()
    tests.check_no_collisions()
    tests.check_connected(expected_components=1)
    return tests.report()
