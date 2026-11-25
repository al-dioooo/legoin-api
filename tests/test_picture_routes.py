import io
import time

import pytest
from conftest import AUTH, FAST_HASHER, OTHER_AUTH
from fastapi.testclient import TestClient
from picture_fakes import jpeg
from PIL import Image
from pydantic import TypeAdapter
from test_picture_jobs import Runner

from legoin_api.main import create_app
from legoin_api.picture.schemas import PictureJob, PictureQuota, RunningPictureJob


@pytest.fixture
def runner():
    return Runner()


@pytest.fixture
def pictures(settings, runner):
    with TestClient(create_app(settings, password_hasher=FAST_HASHER, picture_runner=runner)) as c:
        yield c


def upload(client, picture=None, locale="en", headers=AUTH):
    files = {"picture": ("car.jpg", picture or jpeg((900, 600), exif=True), "image/jpeg")}
    return client.post("/v1/picture-jobs", files=files, data={"locale": locale}, headers=headers)


def finished(client, job_id, headers=AUTH, timeout=5.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get(f"/v1/picture-jobs/{job_id}", headers=headers).json()
        if job["status"] != "running":
            return job
        time.sleep(0.02)
    raise AssertionError(f"picture job {job_id} never ended")


def test_the_contract_examples_parse(example):
    jobs = TypeAdapter(PictureJob)
    for name in ("picture-job-accepted", "picture-job-done", "picture-job-failed"):
        body = example(name)
        assert jobs.validate_python(body).model_dump(mode="json", by_alias=True) == body
    quota = example("picture-quota")
    assert PictureQuota.model_validate(quota).wire() == quota


def test_every_route_but_the_files_needs_a_session(pictures):
    for method, path in [
        ("GET", "/v1/picture-quota"),
        ("POST", "/v1/picture-jobs"),
        ("GET", "/v1/picture-jobs/pjob_x"),
        ("DELETE", "/v1/picture-jobs/pjob_x"),
        ("GET", "/v1/picture-builds"),
        ("GET", "/v1/picture-builds/pic_x"),
        ("DELETE", "/v1/picture-builds/pic_x"),
    ]:
        answer = pictures.request(method, path)
        assert answer.status_code == 401, path
        assert answer.json()["error"]["code"] == "unauthorized"


def test_the_quota_says_whats_left_today(pictures):
    answer = pictures.get("/v1/picture-quota", headers=AUTH)

    assert answer.status_code == 200
    body = answer.json()
    assert (body["available"], body["used"], body["limit"]) == (True, 0, 2)
    assert "reason" not in body


def test_a_picture_becomes_a_saved_build(pictures, example):
    accepted = upload(pictures)

    assert accepted.status_code == 202
    assert RunningPictureJob.model_validate(accepted.json()).stage == "queued"
    job = finished(pictures, accepted.json()["id"])
    assert job["status"] == "done"
    build = job["build"]
    assert set(build) == set(example("picture-job-done")["build"])
    assert (build["title"], build["locale"], build["steps"]) == (
        "Little race car",
        "en",
        ["One.", "Two."],
    )
    assert build["picture"]["src"].startswith("http://testserver/v1/picture-files/")
    assert (build["picture"]["width"], build["picture"]["height"]) == (900, 600)
    assert build["preview"]["image"].endswith(".webp")
    assert build["preview"]["model"].endswith(".ldr")
    assert pictures.get("/v1/picture-builds", headers=AUTH).json() == {"builds": [build]}
    assert pictures.get("/v1/picture-quota", headers=AUTH).json()["used"] == 1


def test_files_load_without_a_header_and_the_picture_keeps_no_metadata(pictures):
    build = finished(pictures, upload(pictures).json()["id"])["build"]

    picture = pictures.get(build["picture"]["src"])
    model = pictures.get(build["preview"]["model"])
    preview = pictures.get(build["preview"]["image"])

    assert picture.headers["content-type"] == "image/jpeg"
    with Image.open(io.BytesIO(picture.content)) as kept:
        assert not kept.getexif()
    assert model.headers["content-type"].startswith("application/x-ldraw")
    assert model.headers["content-disposition"].startswith("attachment")
    assert preview.headers["content-type"] == "image/webp"
    assert pictures.get("/v1/picture-files/../legoin.db").status_code == 404
    assert pictures.get("/v1/picture-files/nope.jpg").status_code == 404


def test_a_build_is_checked_against_a_scan_on_request(pictures, seed, example):
    seed(example("scan-job-done")["scan"])
    build = finished(pictures, upload(pictures).json()["id"])["build"]

    checked = pictures.get(f"/v1/picture-builds/{build['id']}?scanId=scn_4kd2p9", headers=AUTH)
    unknown = pictures.get(f"/v1/picture-builds/{build['id']}?scanId=scn_nope", headers=AUTH)

    # The scan has one red 2×4 brick; the fake build needs one.
    assert checked.json()["check"] == {
        "scanId": "scn_4kd2p9",
        "used": [{"part": "brick-2x4", "color": "red", "quantity": 1}],
        "missing": [],
    }
    assert "check" not in unknown.json()
    assert "check" not in pictures.get("/v1/picture-builds", headers=AUTH).json()["builds"][0]


def test_another_accounts_builds_and_jobs_are_not_found(pictures):
    job = finished(pictures, upload(pictures).json()["id"])

    assert pictures.get(f"/v1/picture-jobs/{job['id']}", headers=OTHER_AUTH).status_code == 404
    other = pictures.get(f"/v1/picture-builds/{job['build']['id']}", headers=OTHER_AUTH)
    assert other.status_code == 404
    assert pictures.get("/v1/picture-builds", headers=OTHER_AUTH).json() == {"builds": []}
    pictures.delete(f"/v1/picture-builds/{job['build']['id']}", headers=OTHER_AUTH)
    assert pictures.get(f"/v1/picture-builds/{job['build']['id']}", headers=AUTH).status_code == 200


def test_deleting_a_build_deletes_its_files(pictures):
    build = finished(pictures, upload(pictures).json()["id"])["build"]

    assert pictures.delete(f"/v1/picture-builds/{build['id']}", headers=AUTH).status_code == 204
    assert pictures.delete(f"/v1/picture-builds/{build['id']}", headers=AUTH).status_code == 204

    assert pictures.get(f"/v1/picture-builds/{build['id']}", headers=AUTH).status_code == 404
    for url in (build["picture"]["src"], build["preview"]["image"], build["preview"]["model"]):
        assert pictures.get(url).status_code == 404


def test_cancelling_an_unknown_job_still_answers_204(pictures):
    assert pictures.delete("/v1/picture-jobs/pjob_gone", headers=AUTH).status_code == 204


@pytest.mark.parametrize(
    ("data", "files", "field"),
    [
        ({"locale": "en"}, {}, "picture"),
        ({"locale": "fr"}, {"picture": ("a.jpg", jpeg(), "image/jpeg")}, "locale"),
        ({}, {"picture": ("a.jpg", jpeg(), "image/jpeg")}, "locale"),
    ],
)
def test_a_bad_upload_is_invalid(pictures, data, files, field):
    answer = pictures.post("/v1/picture-jobs", data=data, files=files, headers=AUTH)

    assert answer.status_code == 400
    assert answer.json()["error"]["field"] == field


def test_a_picture_has_a_photos_limits(pictures):
    assert upload(pictures, picture=b"not an image").status_code == 415


def test_the_daily_limit_answers_429_with_retry_after(pictures):
    for _ in range(2):
        finished(pictures, upload(pictures).json()["id"])

    answer = upload(pictures)

    assert answer.status_code == 429
    assert answer.json()["error"]["code"] == "picture_limit"
    assert int(answer.headers["retry-after"]) > 0
    assert pictures.get("/v1/picture-quota", headers=AUTH).json()["reason"] == "limit"


def test_with_picture_builds_off_uploads_are_unavailable_and_builds_still_read(client):
    answer = upload(client)

    assert answer.status_code == 503
    assert answer.json()["error"]["code"] == "unavailable"
    assert "retry-after" not in answer.headers
    quota = client.get("/v1/picture-quota", headers=AUTH).json()
    assert (quota["available"], quota["reason"]) == (False, "off")
    assert client.get("/v1/picture-builds", headers=AUTH).json() == {"builds": []}
    assert client.get("/health").json()["pictureBuilds"] == "off"


def test_health_says_picture_builds_are_on(pictures):
    health = pictures.get("/health").json()

    assert (health["pictureBuilds"], health["pictureModel"]) == ("anthropic", "claude-opus-5-5")
