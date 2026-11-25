import pytest
from fastapi.testclient import TestClient

from legoin_api.errors import ApiError, field_path
from legoin_api.main import create_app
from legoin_api.schemas import DetectionChanges


@pytest.mark.parametrize(
    ("loc", "path"),
    [
        (("body", "changes", 1, "add", "quantity"), "changes[1].quantity"),
        (("body", "changes", 0, "restore", "detection", "box", "x"), "changes[0].detection.box.x"),
        (("body", "changes"), "changes"),
        (("body", "exclude", 3), "exclude[3]"),
        (("header", "Legoin-Device-Id"), "Legoin-Device-Id"),
        (("body",), ""),
    ],
)
def test_field_path_writes_paths_like_the_contract(loc, path):
    assert field_path(loc) == path


@pytest.fixture
def app_with_test_routes(settings):
    app = create_app(settings)

    @app.post("/test/changes")
    def changes(body: DetectionChanges) -> dict:
        return {"count": len(body.changes)}

    @app.get("/test/conflict")
    def conflict() -> None:
        raise ApiError("no_pieces", "The scan has no pieces left to build with.")

    @app.get("/test/crash")
    def crash() -> None:
        raise RuntimeError("boom")

    return app


def test_an_invalid_change_names_its_field(app_with_test_routes, example):
    client = TestClient(app_with_test_routes)
    body = {
        "changes": [
            {"type": "edit", "id": "det_a3", "color": "black"},
            {"type": "add", "part": "plate-1x2", "color": "blue", "quantity": 21},
        ]
    }

    response = client.post("/test/changes", json=body)

    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "invalid_request"
    # The same field as the contract's example error.
    assert error["field"] == example("error-invalid-request")["error"]["field"]


def test_a_body_that_isnt_json_is_an_invalid_request(app_with_test_routes):
    client = TestClient(app_with_test_routes)

    response = client.post(
        "/test/changes", content=b"{not json", headers={"Content-Type": "application/json"}
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"
    assert "field" not in response.json()["error"]


def test_api_errors_answer_with_their_status_and_code(app_with_test_routes):
    response = TestClient(app_with_test_routes).get("/test/conflict")

    assert response.status_code == 409
    assert response.json() == {
        "error": {"code": "no_pieces", "message": "The scan has no pieces left to build with."}
    }


def test_an_unknown_route_is_not_found(client):
    response = client.get("/v1/nothing-here")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_a_wrong_method_is_an_invalid_request(client):
    response = client.post("/health")

    assert response.status_code == 405
    assert response.json()["error"]["code"] == "invalid_request"


def test_a_crash_is_internal_and_still_readable_by_the_web_app(app_with_test_routes):
    client = TestClient(app_with_test_routes, raise_server_exceptions=False)

    response = client.get("/test/crash", headers={"Origin": "http://localhost:3000"})

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal"
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
