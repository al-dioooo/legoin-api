import pytest

from legoin_api.catalogue import PART_IDS
from legoin_api.picture.grid import (
    GRID,
    MAX_PIECES,
    MAX_STEPS,
    SHAPES,
    Build,
    layer_steps,
    part_name,
    parts_of,
    plain_text,
    problem_with,
    size_of,
)
from legoin_api.schemas import PartCount


def piece(part="brick-2x4", color="red", x=0, y=0, z=0, rotation=0) -> dict:
    return {"part": part, "color": color, "x": x, "y": y, "z": z, "rotation": rotation}


def test_every_catalogue_part_has_a_shape():
    assert set(SHAPES) == set(PART_IDS)


def test_a_build_gives_ids_in_order_and_never_reuses_them():
    build = Build()

    assert build.place([piece(), piece(z=3)]) == [{"id": "p1"}, {"id": "p2"}]
    assert build.remove(["p1", "p9"]) == ["p1"]
    assert build.place([piece()]) == [{"id": "p3"}]
    assert [p.id for p in build.snapshot()] == ["p2", "p3"]


@pytest.mark.parametrize(
    ("spec", "problem"),
    [
        (piece(part="brick-9x9"), "isn't a catalogue part"),
        (piece(color="pink"), "isn't a catalogue color"),
        (piece(rotation=45), "rotation must be"),
        (piece(x=GRID - 3), "must lie within"),
        (piece(x=-1), "must lie within"),
        (piece(rotation=90, y=GRID - 3), "must lie within"),
        (piece(z=46), "z must be from 0 to 45"),
        ({"part": "brick-1x1", "color": "red", "rotation": 0}, "whole numbers"),
    ],
)
def test_pieces_off_the_catalogue_or_the_grid_are_refused(spec, problem):
    assert problem in problem_with(spec)
    assert "error" in Build().place([spec])[0]


def test_a_rotation_turns_the_footprint():
    build = Build()
    build.place([piece(rotation=0), piece(rotation=90, x=10), piece(rotation=180, z=3)])

    assert [p.footprint for p in build.snapshot()] == [(4, 2), (2, 4), (4, 2)]
    # At 90°, a 2×4 brick at the grid's last row but three is 4 deep: it just fits.
    assert problem_with(piece(rotation=90, y=GRID - 4)) is None


def test_a_build_stops_at_the_most_pieces():
    build = Build()
    answers = build.place(
        [piece(part="brick-1x1", x=i % GRID, z=3 * (i // GRID)) for i in range(65)]
    )

    assert len(build.pieces) == MAX_PIECES
    assert all("error" in answer for answer in answers[MAX_PIECES:])


def test_parts_and_size_come_from_the_pieces():
    build = Build()
    build.place(
        [
            piece(part="plate-2x4", color="black", rotation=90),
            piece(part="brick-2x4", color="red", y=1, z=1, rotation=90),
            piece(part="round-1x1", color="yellow", z=1),
            piece(part="round-1x1", color="yellow", x=1, z=1),
        ]
    )
    pieces = build.snapshot()

    assert parts_of(pieces) == [
        PartCount(part="round-1x1", color="yellow", quantity=2),
        PartCount(part="brick-2x4", color="red", quantity=1),
        PartCount(part="plate-2x4", color="black", quantity=1),
    ]
    assert size_of(pieces) == {"studsWide": 2, "studsDeep": 5, "plates": 4}


def test_part_names_follow_the_contract():
    assert part_name("brick-2x4", "red", "en") == "red 2×4 brick"
    assert part_name("plate-1x2", "light-gray", "id") == "plate 1×2 abu-abu terang"


def test_plain_steps_go_level_by_level_and_stay_short():
    build = Build()
    build.place([piece(part="brick-1x1", color="red", z=3 * level) for level in range(12)])
    build.place([piece(part="plate-2x2", color="blue", x=5)])

    steps = layer_steps(build.snapshot(), "id")

    # 12 levels are too many: each pair of levels becomes one step.
    assert len(steps) == 6 <= MAX_STEPS
    assert steps[0] == "Pasang 2 × brick 1×1 merah, 1 × plate 2×2 biru."
    assert layer_steps(build.snapshot()[:1], "en") == ["Add 1 × red 1×1 brick."]
    assert plain_text(build.snapshot(), "en") == (
        "Your picture build",
        "A 13-piece build of your picture.",
    )


def test_an_ideas_build_takes_only_its_pairs_and_its_count():
    build = Build(allowed=frozenset({("brick-2x4", "red")}), max_pieces=2)

    answers = build.place([piece(), piece(color="blue", z=3), piece(z=3), piece(z=6)])

    assert answers[0] == {"id": "p1"}
    assert answers[1] == {"error": "blue brick-2x4 isn't one of this idea's parts"}
    assert answers[2] == {"id": "p2"}
    assert answers[3] == {"error": "the build already has 2 pieces, the most"}
