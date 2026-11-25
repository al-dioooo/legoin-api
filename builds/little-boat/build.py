"""Little boat: a brown hull, a white cabin and a yellow funnel."""

import bricklayer as bl
from brickwright import TestContext

META = {
    "theme": "vehicles",
    "difficulty": "easy",
    "title": {"en": "Little boat", "id": "Perahu kecil"},
    "summary": {
        "en": "Brown hull, white cabin, a funnel on top.",
        "id": "Lambung cokelat, kabin putih, cerobong di atas.",
    },
    "steps": {
        "en": [
            "Use the brown 1×3 brick as the hull.",
            "Put the white 1×1 brick on top as the cabin.",
            "Finish with a yellow round on the cabin as the funnel.",
        ],
        "id": [
            "Pakai brick 1×3 cokelat sebagai lambung.",
            "Pasang brick 1×1 putih di atasnya sebagai kabin.",
            "Tutup dengan round kuning di atas kabin sebagai cerobong.",
        ],
    },
}

# The front is -y and +x is the left. A quarter turn sets a 1×3 brick 1 wide and 3 deep.
ALONG = bl.Rotation.DEG_90


def create_build():
    build = bl.LegoBuild()
    build.add(bl.Element("3622", bl.Color.REDDISH_BROWN), (0, 0, 0), ALONG, name="hull")
    build.add(bl.Element("3005", bl.Color.WHITE), (0, 2, 1), name="cabin")
    build.add(bl.Element("3062b", bl.Color.YELLOW), (0, 2, 2), name="funnel")
    return build


def run_tests():
    build = create_build()
    tests = TestContext(build)
    tests.check_no_floating()
    tests.check_no_collisions()
    tests.check_connected(expected_components=1)
    return tests.report()
