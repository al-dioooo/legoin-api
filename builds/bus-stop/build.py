"""Bus stop: two white posts on a gray pavement, a flat black roof and a bench."""

import bricklayer as bl
from brickwright import TestContext

META = {
    "theme": "buildings",
    "difficulty": "easy",
    "title": {"en": "Bus stop", "id": "Halte bus"},
    "summary": {
        "en": "Two posts, a flat roof and a bench.",
        "id": "Dua tiang, atap datar, dan bangku.",
    },
    "steps": {
        "en": [
            "Lay the dark gray 2×4 plate down as the pavement.",
            "Stack two white 1×1 bricks on each back corner as the posts.",
            "Put the brown 1×2 brick in front, between the posts, with the light gray 1×2 tile on top, as a bench.",
            "Lay the black 2×4 plate on the posts as the roof.",
        ],
        "id": [
            "Letakkan plate 2×4 abu-abu gelap sebagai trotoar.",
            "Tumpuk dua brick 1×1 putih di tiap sudut belakang sebagai tiang.",
            "Pasang brick 1×2 cokelat di depan, di antara tiang, dengan tile 1×2 abu-abu terang di atasnya, sebagai bangku.",
            "Letakkan plate 2×4 hitam di atas tiang sebagai atap.",
        ],
    },
}


def create_build():
    build = bl.LegoBuild()
    # The stop lies across: 4 studs wide, 2 deep. Its front is -y.
    build.add(bl.Element("3020", bl.Color.DARK_BLUISH_GRAY), (0, 0, 0), name="pavement")
    for x in (0, 3):
        for level in (0, 1):
            build.add(
                bl.Element("3005", bl.Color.WHITE),
                (x, 1, bl.PLATE + level),
                name=f"post_{x}_{level}",
            )
    build.add(bl.Element("3004", bl.Color.REDDISH_BROWN), (1, 0, bl.PLATE), name="bench")
    build.add(bl.Element("3069b", bl.Color.LIGHT_BLUISH_GRAY), (1, 0, 1 + bl.PLATE), name="seat")
    build.add(bl.Element("3020", bl.Color.BLACK), (0, 0, 2 + bl.PLATE), name="roof")
    return build


def run_tests():
    build = create_build()
    tests = TestContext(build)
    tests.check_no_floating()
    tests.check_no_collisions()
    tests.check_connected(expected_components=1)
    return tests.report()
