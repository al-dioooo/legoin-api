"""Little house: white walls on a green lawn, a red roof and a gray door step."""

import bricklayer as bl
from brickwright import TestContext

META = {
    "theme": "buildings",
    "difficulty": "easy",
    "title": {"en": "Little house", "id": "Rumah kecil"},
    "summary": {
        "en": "White walls, a red roof and a door step.",
        "id": "Dinding putih, atap merah, dan anak tangga pintu.",
    },
    "steps": {
        "en": [
            "Lay the green 2×4 plate down as the lawn.",
            "Stack two white 2×4 bricks on it, one stud back, as the walls.",
            "Put two red 2×2 slopes on top, back to back, as the roof.",
            "Add the light gray 1×2 tile on the lawn's front row as the door step.",
        ],
        "id": [
            "Letakkan plate 2×4 hijau sebagai halaman.",
            "Tumpuk dua brick 2×4 putih di atasnya, mundur satu stud, sebagai dinding.",
            "Pasang dua slope 2×2 merah di atasnya, saling membelakangi, sebagai atap.",
            "Tambahkan tile 1×2 abu-abu terang di baris depan halaman sebagai anak tangga.",
        ],
    },
}

# The front is -y and +x is the left. A quarter turn sets a 4×2 part 2 wide and 4 deep.
ALONG = bl.Rotation.DEG_90


def create_build():
    build = bl.LegoBuild()
    build.add(bl.Element("3020", bl.Color.GREEN), (0, 0, 0), ALONG, name="lawn")
    # The walls sit a stud back, so the lawn's front row holds the step.
    lower = build.add(bl.Element("3001", bl.Color.WHITE), (0, 1, bl.PLATE), ALONG, name="wall")
    upper = build.stack(
        bl.Element("3001", bl.Color.WHITE), on=lower, rotation=ALONG, name="wall_top"
    )
    build.stack(bl.Element("3039", bl.Color.RED), on=upper, name="roof_front")
    build.stack(
        bl.Element("3039", bl.Color.RED),
        on=upper,
        offset=(0, 2, 0),
        rotation=bl.Rotation.BACK,
        name="roof_back",
    )
    build.add(bl.Element("3069b", bl.Color.LIGHT_BLUISH_GRAY), (0, 0, bl.PLATE), name="step")
    return build


def run_tests():
    build = create_build()
    tests = TestContext(build)
    tests.check_no_floating()
    tests.check_no_collisions()
    tests.check_connected(expected_components=1)
    return tests.report()
