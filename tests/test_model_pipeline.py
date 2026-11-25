"""The real pipeline's pieces. They need the `models` extra (`uv sync --extra models`), so CI
skips them."""

import json
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("torch")
pytest.importorskip("skimage")
plotting = pytest.importorskip("ultralytics.utils.plotting")

import torch  # noqa: E402
from images import pieces  # noqa: E402
from PIL import Image  # noqa: E402

from legoin_api.pipeline import Found  # noqa: E402
from legoin_api.pipeline.color import ColorParameters, putuskan, warna_kepingan  # noqa: E402
from legoin_api.pipeline.models import letterbox, widen  # noqa: E402

COLOR_JSON = json.loads((Path(__file__).parent / "fixtures/legoin_warna-v0.2.0.json").read_text())
"""`legoin_warna.json` from legoin-ml v0.2.0, as released."""


def test_crops_match_ultralytics_save_one_box():
    photo = pieces(640, 480)
    bgr = np.ascontiguousarray(np.asarray(photo)[:, :, ::-1])
    rng = np.random.default_rng(0)
    for _ in range(500):
        x1, y1 = rng.uniform(-20, 640), rng.uniform(-20, 480)
        box = np.array([x1, y1, x1 + rng.uniform(1, 300), y1 + rng.uniform(1, 300)], np.float32)
        found = Found(*map(float, box), score=0.9)
        reference = plotting.save_one_box(torch.tensor(box), bgr, save=False)
        mine = np.asarray(photo.crop(widen(found, 640, 480)))
        assert mine.shape == reference.shape
        assert np.array_equal(mine, reference)


def test_letterbox_keeps_the_aspect_ratio_on_the_pad_color():
    square = letterbox(Image.new("RGB", (100, 25), (255, 0, 0)))

    assert square.size == (224, 224)
    assert square.getpixel((112, 112)) == (255, 0, 0)
    assert square.getpixel((112, 10)) == (124, 116, 104)


def brick(color: tuple[int, int, int]) -> Image.Image:
    """A brick filling the middle of a white crop, as the detector's widened box cuts it."""
    crop = Image.new("RGB", (100, 80), (245, 245, 245))
    crop.paste(Image.new("RGB", (70, 50), color), (15, 15))
    return crop


@pytest.mark.parametrize(
    ("rgb", "expected"),
    [((201, 26, 9), "red"), ((0, 85, 191), "blue"), ((242, 205, 55), "yellow")],
)
def test_the_color_port_names_catalogue_colors(rgb, expected):
    parameters = ColorParameters.from_json(COLOR_JSON)

    ids, confidence = putuskan(warna_kepingan(brick(rgb), parameters), parameters)

    assert ids == [expected]
    assert confidence[0] >= 0.6


def test_color_parameters_must_use_the_catalogue():
    with pytest.raises(ValueError):
        ColorParameters.from_json(COLOR_JSON | {"colors": ["red", "purple"]})
