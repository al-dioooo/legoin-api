from conftest import DEVICE

HEADERS = {"Legoin-Device-Id": DEVICE}


def test_no_scans_is_an_empty_list(client):
    response = client.get("/v1/scans", headers=HEADERS)

    assert response.status_code == 200
    assert response.json() == {"scans": []}


def test_every_route_needs_the_device_header(client):
    response = client.get("/v1/scans")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"
    assert response.json()["error"]["field"] == "Legoin-Device-Id"


def test_a_device_id_must_be_url_safe(client):
    response = client.get("/v1/scans", headers={"Legoin-Device-Id": "not a/valid id"})

    assert response.status_code == 400


def test_history_is_newest_first_with_full_scans(client, seed, example):
    newest, older = example("scans-list")["scans"]
    seed(older)
    seed(newest)

    scans = client.get("/v1/scans", headers=HEADERS).json()["scans"]

    assert [scan["id"] for scan in scans] == ["scn_4kd2p9", "scn_9tq1m3"]
    for scan, expected in zip(scans, (newest, older), strict=True):
        assert scan["photo"]["src"].startswith("http://testserver/v1/photos/")
        assert scan | {"photo": expected["photo"]} == expected


def test_a_device_only_sees_its_own_scans(client, seed, example):
    seed(example("scan-job-done")["scan"], device="someone-else")

    assert client.get("/v1/scans", headers=HEADERS).json() == {"scans": []}
    assert client.get("/v1/scans/scn_4kd2p9", headers=HEADERS).status_code == 404


def test_an_unknown_scan_is_not_found_like_the_contract_says(client, example):
    response = client.get("/v1/scans/scn_4kd2p9", headers=HEADERS)

    assert response.status_code == 404
    assert response.json() == example("error-not-found")


def test_photo_urls_use_the_public_url_when_set(settings):
    from fastapi.testclient import TestClient

    from legoin_api.main import create_app
    from legoin_api.store import StoredScan

    app = create_app(settings.model_copy(update={"public_url": "https://api.legoin.example/"}))
    with TestClient(app) as client:
        name = app.state.photos.new_name()
        app.state.store.insert_scan(
            StoredScan("scn_1", DEVICE, "2026-11-09T03:12:45.120Z", name, 10, 10, [])
        )
        src = client.get("/v1/scans/scn_1", headers=HEADERS).json()["photo"]["src"]

    assert src == f"https://api.legoin.example/v1/photos/{name}"


def test_corrections_follow_the_contracts_example(client, seed, example):
    scan = example("scan-job-done")["scan"]
    earlier_ideas = {"theme": "vehicles", "locale": "en", "items": []}
    seed(scan, last_theme="vehicles", ideas=earlier_ideas)

    response = client.patch(
        "/v1/scans/scn_4kd2p9/detections",
        headers=HEADERS,
        json=example("patch-detections-request"),
    )

    assert response.status_code == 200
    body = response.json()
    expected = example("patch-detections-response")
    added = body["detections"][-1]
    assert added["id"].startswith("det_") and added["id"] != "det_b7"
    # The inventory changed, so the ideas are gone; lastTheme stays.
    assert "ideas" not in body
    assert body | {"photo": expected["photo"]} == expected | {
        "detections": [*expected["detections"][:-1], expected["detections"][-1] | added]
    }


def test_confirming_a_piece_keeps_the_ideas(client, seed, example):
    ideas = example("ideas-response")
    seed(example("scan-job-done")["scan"], last_theme="vehicles", ideas=ideas)

    body = client.patch(
        "/v1/scans/scn_4kd2p9/detections",
        headers=HEADERS,
        json={"changes": [{"type": "edit", "id": "det_a3", "part": "brick-2x2"}]},
    ).json()

    assert body["ideas"] == ideas
    assert body["lastTheme"] == "vehicles"
    assert body["detections"][2]["edited"] is True
    assert body["detections"][2]["confidence"] == 0.58


def test_corrections_are_all_or_nothing(client, seed, example):
    seed(example("scan-job-done")["scan"])

    response = client.patch(
        "/v1/scans/scn_4kd2p9/detections",
        headers=HEADERS,
        json={
            "changes": [
                {"type": "remove", "id": "det_a1"},
                {"type": "add", "part": "plate-1x2", "color": "blue", "quantity": 0},
            ]
        },
    )

    assert response.status_code == 400
    assert response.json()["error"]["field"] == "changes[1].quantity"
    scan = client.get("/v1/scans/scn_4kd2p9", headers=HEADERS).json()
    assert len(scan["detections"]) == 3


def test_a_correction_needs_at_least_one_change(client, seed, example):
    seed(example("scan-job-done")["scan"])

    response = client.patch(
        "/v1/scans/scn_4kd2p9/detections", headers=HEADERS, json={"changes": []}
    )

    assert response.status_code == 400
    assert response.json()["error"]["field"] == "changes"


def test_an_unknown_part_is_an_invalid_request(client, seed, example):
    seed(example("scan-job-done")["scan"])

    response = client.patch(
        "/v1/scans/scn_4kd2p9/detections",
        headers=HEADERS,
        json={"changes": [{"type": "edit", "id": "det_a1", "part": "brick-9x9"}]},
    )

    assert response.status_code == 400
    assert response.json()["error"]["field"] == "changes[0].part"


def test_correcting_an_unknown_scan_is_not_found(client):
    response = client.patch(
        "/v1/scans/scn_nope/detections",
        headers=HEADERS,
        json={"changes": [{"type": "remove", "id": "det_a1"}]},
    )

    assert response.status_code == 404


def test_deleting_removes_the_scan_and_its_photo_and_can_repeat(client, seed, example):
    stored = seed(example("scan-job-done")["scan"])
    src = client.get("/v1/scans/scn_4kd2p9", headers=HEADERS).json()["photo"]["src"]
    assert client.get(src).status_code == 200

    assert client.delete("/v1/scans/scn_4kd2p9", headers=HEADERS).status_code == 204
    assert client.get("/v1/scans/scn_4kd2p9", headers=HEADERS).status_code == 404
    assert client.get(src).status_code == 404
    assert not (client.app.state.photos.directory / stored.photo_file).exists()
    assert client.delete("/v1/scans/scn_4kd2p9", headers=HEADERS).status_code == 204


def test_another_device_cannot_delete_a_scan(client, seed, example):
    seed(example("scan-job-done")["scan"])

    client.delete("/v1/scans/scn_4kd2p9", headers={"Legoin-Device-Id": "someone-else"})

    assert client.get("/v1/scans/scn_4kd2p9", headers=HEADERS).status_code == 200


def test_photos_load_without_a_header_and_only_by_their_name(client, seed, example):
    seed(example("scan-job-done")["scan"])
    src = client.get("/v1/scans/scn_4kd2p9", headers=HEADERS).json()["photo"]["src"]

    photo = client.get(src)

    assert photo.status_code == 200
    assert photo.headers["content-type"] == "image/jpeg"
    assert client.get("/v1/photos/..%2Flegoin.db").status_code == 404
    assert client.get("/v1/photos/short.jpg").status_code == 404
