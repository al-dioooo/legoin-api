import threading
import time

import pytest
from conftest import DEVICE
from fastapi.testclient import TestClient
from images import empty_table, encode, flat, pieces, soft_gradient
from PIL import Image

from legoin_api.main import create_app
from legoin_api.photos import MAX_PHOTO_BYTES
from legoin_api.pipeline import Found, Reading

HEADERS = {"Legoin-Device-Id": DEVICE}


def upload(client, data: bytes, *, name="photo.png", type="image/png", headers=HEADERS):
    return client.post("/v1/scan-jobs", headers=headers, files={"photo": (name, data, type)})


def poll(client, job_id: str, headers=HEADERS, seconds: float = 5) -> dict:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        job = client.get(f"/v1/scan-jobs/{job_id}", headers=headers).json()
        if job.get("status") != "running":
            return job
        time.sleep(0.01)
    raise AssertionError(f"job {job_id} still running")


def drain(client) -> None:
    """Waits for the single scan worker to finish what it has."""
    client.app.state.jobs._executor.submit(lambda: None).result(timeout=5)


def test_a_photo_becomes_a_saved_scan(client):
    accepted = upload(client, encode(pieces()))

    assert accepted.status_code == 202
    assert accepted.json()["status"] == "running"
    assert accepted.json()["stage"] == "detecting"
    job = poll(client, accepted.json()["id"])
    assert job["status"] == "done"
    scan = job["scan"]
    assert scan["id"].startswith("scn_")
    assert scan["createdAt"].endswith("Z")
    assert (scan["photo"]["width"], scan["photo"]["height"]) == (600, 400)
    assert scan["detections"]
    for detection in scan["detections"]:
        box = detection["box"]
        assert box["x"] >= 0 and box["x"] + box["width"] <= 1
        assert 0 < detection["confidence"] <= 1
        assert "edited" not in detection
    assert client.get("/v1/scans", headers=HEADERS).json()["scans"] == [scan]
    assert client.get(scan["photo"]["src"]).headers["content-type"] == "image/jpeg"


def test_the_same_photo_gives_the_same_pieces(client):
    first = poll(client, upload(client, encode(pieces())).json()["id"])["scan"]
    second = poll(client, upload(client, encode(pieces())).json()["id"])["scan"]

    def readings(scan):
        return [(d["part"], d["color"], d["box"], d["confidence"]) for d in scan["detections"]]

    assert readings(first) == readings(second)


def test_the_photo_is_stored_upright_and_without_exif(client):
    # 300 × 200 pixels, with EXIF saying "rotate 90° clockwise to view".
    exif = Image.Exif()
    exif[0x0112] = 6
    data = encode(pieces(300, 200), "JPEG", exif=exif.tobytes())

    scan = poll(client, upload(client, data, name="photo.jpg", type="image/jpeg").json()["id"])
    stored = scan["scan"]["photo"]

    assert (stored["width"], stored["height"]) == (200, 300)
    photo = client.get(stored["src"])
    with Image.open(__import__("io").BytesIO(photo.content)) as image:
        assert image.size == (200, 300)
        assert not image.getexif()


@pytest.mark.parametrize(
    ("image", "code"),
    [
        (flat((10, 10, 12)), "too_dark"),
        (soft_gradient(), "too_blurry"),
        (empty_table(), "no_pieces"),
        (flat((236, 233, 226)), "no_pieces"),
    ],
)
def test_a_photo_with_no_pieces_ends_the_job_with_its_problem(client, image, code):
    job = poll(client, upload(client, encode(image)).json()["id"])

    assert job["status"] == "failed"
    assert job["error"]["code"] == code
    assert set(job) == {"id", "status", "error"}
    assert client.get("/v1/scans", headers=HEADERS).json() == {"scans": []}
    assert not any(client.app.state.photos.directory.iterdir())


def test_an_upload_needs_a_photo_part(client):
    response = client.post("/v1/scan-jobs", headers=HEADERS, files={"picture": ("a.png", b"x")})

    assert response.status_code == 400
    assert response.json()["error"] == {
        "code": "invalid_request",
        "message": "There's no photo part.",
        "field": "photo",
    }


def test_an_upload_needs_the_device_header(client):
    response = upload(client, encode(pieces()), headers={})

    assert response.status_code == 400
    assert response.json()["error"]["field"] == "Legoin-Device-Id"


@pytest.mark.parametrize(
    "data",
    [encode(pieces(), "GIF"), b"not an image at all", encode(pieces(), "BMP")],
)
def test_only_jpeg_png_and_webp_are_accepted(client, data):
    response = upload(client, data)

    assert response.status_code == 415
    assert response.json()["error"]["code"] == "photo_type"


def test_webp_is_accepted(client):
    response = upload(client, encode(pieces(), "WEBP"), name="p.webp", type="image/webp")

    assert response.status_code == 202


def test_a_photo_over_10_mb_is_too_large(client):
    response = upload(client, b"\xff" * (MAX_PHOTO_BYTES + 1))

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "photo_too_large"


def test_a_much_larger_body_is_refused_before_it_is_read(client):
    response = upload(client, b"\xff" * (MAX_PHOTO_BYTES + 200_000))

    assert response.status_code == 413


def test_an_unknown_job_is_not_found_and_cancelling_it_is_fine(client):
    response = client.get("/v1/scan-jobs/job_nope", headers=HEADERS)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
    assert client.delete("/v1/scan-jobs/job_nope", headers=HEADERS).status_code == 204


def test_a_job_belongs_to_its_device(client):
    job_id = upload(client, encode(pieces())).json()["id"]
    other = {"Legoin-Device-Id": "someone-else"}

    assert client.get(f"/v1/scan-jobs/{job_id}", headers=other).status_code == 404
    assert client.delete(f"/v1/scan-jobs/{job_id}", headers=other).status_code == 204
    assert poll(client, job_id)["status"] == "done"


def test_cancelling_a_finished_job_deletes_its_scan(client):
    job = poll(client, upload(client, encode(pieces())).json()["id"])

    assert client.delete(f"/v1/scan-jobs/{job['id']}", headers=HEADERS).status_code == 204
    assert client.get(f"/v1/scans/{job['scan']['id']}", headers=HEADERS).status_code == 404
    assert client.get(f"/v1/scan-jobs/{job['id']}", headers=HEADERS).status_code == 404
    assert not any(client.app.state.photos.directory.iterdir())


class GatedPipeline:
    """Each stage waits for its gate, so a test can look at the job in between."""

    def __init__(self):
        self.entered = {stage: threading.Event() for stage in ("detect", "classify", "color")}
        self.gates = {stage: threading.Event() for stage in ("detect", "classify", "color")}

    def _pass(self, stage: str) -> None:
        self.entered[stage].set()
        assert self.gates[stage].wait(5)

    def detect(self, image):
        self._pass("detect")
        return [Found(10, 20, 110, 220, score=0.97)]

    def classify(self, image, found):
        self._pass("classify")
        return [Reading("brick-2x4", 0.81)]

    def color(self, image, found):
        self._pass("color")
        return [Reading("red", 0.55)]

    def open_all(self):
        for gate in self.gates.values():
            gate.set()


@pytest.fixture
def gated(settings):
    pipeline = GatedPipeline()
    with TestClient(create_app(settings, pipeline=pipeline)) as client:
        yield client, pipeline
        pipeline.open_all()


def stage_of(client, job_id: str) -> str:
    return client.get(f"/v1/scan-jobs/{job_id}", headers=HEADERS).json()["stage"]


def test_a_job_reports_each_stage_in_order(gated):
    client, pipeline = gated
    job_id = upload(client, encode(pieces(400, 400))).json()["id"]

    for stage, name in [
        ("detect", "detecting"),
        ("classify", "classifying"),
        ("color", "coloring"),
    ]:
        assert pipeline.entered[stage].wait(5)
        assert stage_of(client, job_id) == name
        pipeline.gates[stage].set()

    job = poll(client, job_id)
    detection = job["scan"]["detections"][0]
    assert detection["box"] == {"x": 0.025, "y": 0.05, "width": 0.25, "height": 0.5}
    # The lowest of the box, part and color scores.
    assert detection["confidence"] == 0.55
    assert (detection["part"], detection["color"]) == ("brick-2x4", "red")


def test_cancelling_a_running_job_saves_nothing(gated):
    client, pipeline = gated
    job_id = upload(client, encode(pieces())).json()["id"]
    assert pipeline.entered["detect"].wait(5)

    assert client.delete(f"/v1/scan-jobs/{job_id}", headers=HEADERS).status_code == 204
    pipeline.open_all()
    drain(client)

    assert not pipeline.entered["classify"].is_set()
    assert client.get(f"/v1/scan-jobs/{job_id}", headers=HEADERS).status_code == 404
    assert client.get("/v1/scans", headers=HEADERS).json() == {"scans": []}
    assert not any(client.app.state.photos.directory.iterdir())


def test_a_job_past_its_deadline_fails_with_timeout_and_saves_nothing(settings):
    pipeline = GatedPipeline()
    fast = settings.model_copy(update={"scan_timeout_s": 0.2})
    with TestClient(create_app(fast, pipeline=pipeline)) as client:
        job_id = upload(client, encode(pieces())).json()["id"]
        assert pipeline.entered["detect"].wait(5)
        time.sleep(0.3)

        job = client.get(f"/v1/scan-jobs/{job_id}", headers=HEADERS).json()
        pipeline.open_all()
        drain(client)

        assert job["status"] == "failed"
        assert job["error"]["code"] == "timeout"
        assert client.get(f"/v1/scan-jobs/{job_id}", headers=HEADERS).json() == job
        assert client.get("/v1/scans", headers=HEADERS).json() == {"scans": []}
        assert not any(client.app.state.photos.directory.iterdir())


def test_a_crashing_pipeline_fails_the_job_as_internal(settings):
    class Broken(GatedPipeline):
        def detect(self, image):
            raise RuntimeError("model fell over")

    with TestClient(create_app(settings, pipeline=Broken())) as client:
        job = poll(client, upload(client, encode(pieces())).json()["id"])

    assert job["status"] == "failed"
    assert job["error"]["code"] == "internal"
