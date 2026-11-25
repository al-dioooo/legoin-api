"""BrickLayer's checks and the LDraw export, for real. They need the `picture` extra, which CI
doesn't install:

    uv run --extra picture pytest tests/test_picture_checks.py
"""

import pytest
from picture_fakes import CAR

from legoin_api.picture.grid import Build


def car(*more):
    build = Build()
    build.place([*CAR, *more])
    return build.snapshot()


bricklayer = pytest.importorskip("bricklayer", reason="needs the picture extra")
from legoin_api.picture.checks import BrickLayerChecks, check_colors, ldraw  # noqa: E402


def test_the_catalogues_colors_are_bricklayers():
    check_colors()


def test_the_example_car_is_valid():
    assert BrickLayerChecks().problems(car()) == []


def test_problems_name_the_pieces_at_fault():
    floating = {"part": "tile-1x2", "color": "white", "x": 8, "y": 8, "z": 9, "rotation": 0}
    clashing = {"part": "brick-1x1", "color": "blue", "x": 0, "y": 1, "z": 1, "rotation": 0}

    problems = BrickLayerChecks().problems(car(floating, clashing))

    assert any(p.startswith("1 collisions: ") and "p8" in p for p in problems)
    assert "1 floating, not held by a stud below or the ground: p7" in problems
    assert any(p.startswith("2 separate assemblies") for p in problems)


def test_the_ldr_carries_the_credit_and_one_line_per_piece():
    text = ldraw(car(), "Race\ncar")

    lines = text.splitlines()
    assert lines[0] == "0 Race car"
    assert "0 // Parts: the LDraw parts library, CC BY 4.0, https://library.ldraw.org" in lines
    assert sum(line.startswith("1 ") for line in lines) == len(CAR)
