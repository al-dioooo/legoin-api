import io
import sqlite3
import struct
import threading
import time

import pytest
from conftest import DEVICE
from fastapi.testclient import TestClient
from images import empty_table, encode, flat, huge_png, pieces, soft_gradient
from PIL import Image, ImageFile

from legoin_api.main import create_app
from legoin_api.photos import MAX_PHOTO_BYTES, MAX_PHOTO_PIXELS
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


def test_the_stored_photo_keeps_no_comment_or_xmp(client):
    xmp = b'<x:xmpmeta xmlns:x="adobe:ns:meta/">secret-xmp-packet</x:xmpmeta>'
    data = encode(pieces(), "JPEG", comment=b"secret-jpeg-comment", xmp=xmp)
    assert b"secret-jpeg-comment" in data and b"secret-xmp-packet" in data

    job = poll(client, upload(client, data, name="photo.jpg", type="image/jpeg").json()["id"])
    stored = client.get(job["scan"]["photo"]["src"]).content

    assert b"secret-jpeg-comment" not in stored
    assert b"secret-xmp-packet" not in stored
    with Image.open(io.BytesIO(stored)) as image:
        assert not {"comment", "xmp", "exif", "icc_profile"} & set(image.info)


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


@pytest.mark.parametrize(
    "error",
    [
        ValueError("bad value"),
        TypeError("bad EXIF"),
        struct.error("short"),
        OSError(),
    ],
)
def test_any_decode_error_is_photo_type_not_500(client, monkeypatch, error):
    # Fuzzing raised these from Pillow, `TypeError` from `exif_transpose`.
    def broken(image):
        raise error

    monkeypatch.setattr("legoin_api.photos.ImageOps.exif_transpose", broken)

    response = upload(client, encode(pieces()))

    assert response.status_code == 415
    assert response.json()["error"]["code"] == "photo_type"


def test_a_truncated_photo_is_photo_type(client):
    data = encode(pieces(), "JPEG")

    response = upload(client, data[: len(data) // 2], name="p.jpg", type="image/jpeg")

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


def test_a_tiny_png_with_huge_dimensions_is_refused_without_decoding(client, monkeypatch):
    # The review's case: a 191 KB PNG of 7745 × 7745 px stalled the server for 0.8 s and took
    # 458 MiB. It's refused from its header, before a single pixel is decoded.
    decoded = []
    load = ImageFile.ImageFile.load
    monkeypatch.setattr(ImageFile.ImageFile, "load", lambda im: decoded.append(im) or load(im))
    data = huge_png(7745, 7745)
    assert len(data) < 200_000

    started = time.perf_counter()
    response = upload(client, data)

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "photo_too_large"
    assert not decoded
    assert time.perf_counter() - started < 0.5
    assert client.app.state.jobs._executor._work_queue.empty()


def test_photos_are_capped_near_the_web_apps_size(client):
    assert MAX_PHOTO_PIXELS == 4096 * 4096
    # A 12 MP phone photo fits; one pixel row more than the cap doesn't.
    assert MAX_PHOTO_PIXELS >= 4032 * 3024
    assert upload(client, huge_png(4096, 4097)).status_code == 413


def test_the_photo_is_decoded_off_the_event_loop(client, monkeypatch):
    import asyncio

    from legoin_api.routes import scan_jobs

    loops = []
    read_photo = scan_jobs.read_photo

    def spy(data):
        try:
            loops.append(asyncio.get_running_loop())
        except RuntimeError:
            loops.append(None)
        return read_photo(data)

    monkeypatch.setattr(scan_jobs, "read_photo", spy)

    assert upload(client, encode(pieces())).status_code == 202
    assert loops == [None]


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


def test_a_full_queue_answers_503_with_retry_after(settings):
    pipeline = GatedPipeline()
    small = settings.model_copy(update={"scan_queue_size": 2})
    with TestClient(create_app(small, pipeline=pipeline)) as client:
        running = upload(client, encode(pieces())).json()["id"]
        assert pipeline.entered["detect"].wait(5)
        waiting = upload(client, encode(pieces())).json()["id"]

        refused = upload(client, encode(pieces()))

        assert refused.status_code == 503
        assert refused.json()["error"]["code"] == "unavailable"
        assert refused.headers["retry-after"] == "5"

        pipeline.open_all()
        assert poll(client, running)["status"] == "done"
        assert poll(client, waiting)["status"] == "done"
        drain(client)
        assert upload(client, encode(pieces())).status_code == 202


def test_a_photo_that_is_refused_gives_its_place_back(settings):
    pipeline = GatedPipeline()
    single = settings.model_copy(update={"scan_queue_size": 1})
    with TestClient(create_app(single, pipeline=pipeline)) as client:
        assert upload(client, b"not an image").status_code == 415
        assert upload(client, huge_png(5000, 5000)).status_code == 413

        assert upload(client, encode(pieces())).status_code == 202
        assert upload(client, encode(pieces())).status_code == 503
        pipeline.open_all()


def test_a_scan_that_cannot_be_saved_leaves_no_photo(client, monkeypatch):
    def disk_full(scan):
        raise sqlite3.OperationalError("database or disk is full")

    monkeypatch.setattr(client.app.state.store, "insert_scan", disk_full)

    job = poll(client, upload(client, encode(pieces())).json()["id"])

    assert job["status"] == "failed"
    assert job["error"]["code"] == "internal"
    assert not any(client.app.state.photos.directory.iterdir())
