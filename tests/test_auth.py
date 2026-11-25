"""Accounts and sessions (contract section 4, ADR-0011)."""

import re

import pytest
from conftest import DEVICE

from legoin_api.accounts import MAX_FAILED_LOGINS, token_hash

EMAIL = "ana@example.com"
PASSWORD = "baseplate-2x4"


def register(client, email=EMAIL, password=PASSWORD, **extra):
    return client.post("/v1/auth/register", json={"email": email, "password": password, **extra})


def log_in(client, email=EMAIL, password=PASSWORD, **extra):
    return client.post("/v1/auth/login", json={"email": email, "password": password, **extra})


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_register_answers_a_session_shaped_like_the_contract(client, example):
    body = example("auth-register-request")
    response = client.post("/v1/auth/register", json=body)
    assert response.status_code == 201
    session = response.json()
    assert session.keys() == example("auth-session").keys()
    assert session["account"].keys() == example("auth-me").keys()
    assert session["account"]["email"] == body["email"]
    assert re.fullmatch(r"lgn_[A-Za-z0-9_-]{43}", session["token"])
    assert session["expiresAt"] > session["account"]["createdAt"]


def test_the_session_reads_the_account(client):
    session = register(client).json()
    response = client.get("/v1/auth/me", headers=bearer(session["token"]))
    assert response.status_code == 200
    assert response.json() == session["account"]


def test_the_email_is_trimmed_and_lowercased(client):
    account = register(client, email="  Ana@Example.COM ").json()["account"]
    assert account["email"] == EMAIL
    assert log_in(client, email="ANA@example.com").status_code == 200


def test_one_email_has_one_account(client):
    register(client)
    response = register(client, email="Ana@example.com", password="another-password")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "email_taken"


@pytest.mark.parametrize(
    ("email", "password", "field"),
    [
        ("not-an-email", PASSWORD, "email"),
        ("ana@example", PASSWORD, "email"),
        ("a" * 250 + "@x.co", PASSWORD, "email"),
        (EMAIL, "short", "password"),
        (EMAIL, "x" * 129, "password"),
    ],
)
def test_register_checks_the_rules(client, email, password, field):
    response = register(client, email=email, password=password)
    assert response.status_code == 400
    assert response.json()["error"] == {
        "code": "invalid_request",
        "message": response.json()["error"]["message"],
        "field": field,
    }


def test_a_device_id_must_look_like_the_old_header(client):
    response = register(client, deviceId="not a/valid id")
    assert response.status_code == 400
    assert response.json()["error"]["field"] == "deviceId"


def test_the_password_is_kept_only_as_an_argon2id_hash(client):
    register(client)
    account = client.app.state.store.account_by_email(EMAIL)
    assert account.password_hash.startswith("$argon2id$")
    assert PASSWORD not in account.password_hash


def test_log_in_starts_a_new_session_each_time(client):
    register(client)
    first, second = log_in(client), log_in(client)
    assert first.status_code == second.status_code == 200
    assert first.json()["token"] != second.json()["token"]
    for session in (first.json(), second.json()):
        assert client.get("/v1/auth/me", headers=bearer(session["token"])).status_code == 200


def test_only_a_hash_of_the_token_is_kept(client):
    session = register(client).json()
    rows = client.app.state.store._db.execute(
        "SELECT token_hash FROM sessions WHERE account_id = ?", (session["account"]["id"],)
    ).fetchall()
    assert [row[0] for row in rows] == [token_hash(session["token"])]


@pytest.mark.parametrize(
    ("email", "password"), [(EMAIL, "wrong-password"), ("nobody@example.com", PASSWORD)]
)
def test_a_wrong_email_or_password_isnt_told_apart(client, email, password):
    register(client)
    response = log_in(client, email=email, password=password)
    assert response.status_code == 401
    assert response.json() == {
        "error": {"code": "invalid_credentials", "message": "Wrong email or password."}
    }


def test_log_in_only_checks_presence_and_length(client):
    """A password made under older rules still logs in, so a short one is just wrong."""
    register(client)
    response = log_in(client, password="short")
    assert response.status_code == 401
    assert log_in(client, password="").status_code == 400


def test_too_many_failures_wait(client):
    register(client)
    for _ in range(MAX_FAILED_LOGINS):
        assert log_in(client, password="wrong-password").status_code == 401
    response = log_in(client)
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "rate_limited"
    assert 0 < int(response.headers["Retry-After"]) <= 15 * 60
    # Another email isn't held up.
    register(client, email="budi@example.com")
    assert log_in(client, email="budi@example.com").status_code == 200


def test_a_log_in_that_works_clears_the_failures(client):
    register(client)
    for _ in range(MAX_FAILED_LOGINS - 1):
        log_in(client, password="wrong-password")
    assert log_in(client).status_code == 200
    for _ in range(MAX_FAILED_LOGINS - 1):
        log_in(client, password="wrong-password")
    assert log_in(client).status_code == 200


def test_log_out_ends_only_this_session(client):
    register(client)
    one, other = log_in(client).json()["token"], log_in(client).json()["token"]
    assert client.post("/v1/auth/logout", headers=bearer(one)).status_code == 204
    assert client.get("/v1/auth/me", headers=bearer(one)).status_code == 401
    assert client.get("/v1/auth/me", headers=bearer(other)).status_code == 200


@pytest.mark.parametrize("headers", [{}, bearer("lgn_unknown"), {"Authorization": "Basic abc"}])
def test_log_out_never_fails(client, headers):
    response = client.post("/v1/auth/logout", headers=headers)
    assert response.status_code == 204
    assert response.content == b""


@pytest.mark.parametrize(
    "headers",
    [{}, bearer("lgn_unknown"), {"Authorization": "Basic abc"}, {"Authorization": "Bearer "}],
)
def test_no_valid_session_is_unauthorized(client, headers):
    response = client.get("/v1/auth/me", headers=headers)
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json()["error"]["code"] == "unauthorized"


def test_the_scheme_is_case_insensitive(client):
    token = register(client).json()["token"]
    response = client.get("/v1/auth/me", headers={"Authorization": f"bearer {token}"})
    assert response.status_code == 200


def test_an_expired_session_is_unauthorized_and_deleted(client):
    token = register(client).json()["token"]
    store = client.app.state.store
    kept = "SELECT COUNT(*) FROM sessions WHERE token_hash = ?"
    store._db.execute(
        "UPDATE sessions SET expires_at = '2026-01-01T00:00:00.000Z' WHERE token_hash = ?",
        (token_hash(token),),
    )
    assert client.get("/v1/auth/me", headers=bearer(token)).status_code == 401
    assert store._db.execute(kept, (token_hash(token),)).fetchone()[0] == 0


def test_an_old_hash_is_redone_on_log_in(client):
    from argon2 import PasswordHasher

    register(client)
    store = client.app.state.store
    account = store.account_by_email(EMAIL)
    old = PasswordHasher(time_cost=2, memory_cost=16, parallelism=1).hash(PASSWORD)
    store.set_password_hash(account.id, old)
    assert log_in(client).status_code == 200
    assert store.account_by_email(EMAIL).password_hash != old


def put_device_scan(client, scan_id: str, device: str = DEVICE) -> None:
    """A scan as it was saved before accounts: by its device, with no account."""
    client.app.state.store._db.execute(
        "INSERT INTO scans (id, device_id, created_at, photo_file, photo_width, photo_height,"
        " detections) VALUES (?, ?, '2026-11-09T03:12:45.120Z', 'a.jpg', 10, 10, '[]')",
        (scan_id, device),
    )


@pytest.mark.parametrize("route", [register, log_in])
def test_scans_from_before_accounts_move_to_the_account(client, route):
    if route is log_in:
        register(client)
    put_device_scan(client, "scn_mine")
    put_device_scan(client, "scn_theirs", device="another-device")

    session = route(client, deviceId=DEVICE).json()

    scans = client.get("/v1/scans", headers=bearer(session["token"])).json()["scans"]
    assert [scan["id"] for scan in scans] == ["scn_mine"]


def test_a_moved_scan_never_moves_again(client):
    put_device_scan(client, "scn_mine")
    first = register(client, deviceId=DEVICE).json()
    second = register(client, email="budi@example.com", deviceId=DEVICE).json()

    assert len(client.get("/v1/scans", headers=bearer(first["token"])).json()["scans"]) == 1
    assert client.get("/v1/scans", headers=bearer(second["token"])).json() == {"scans": []}


def test_a_failed_log_in_moves_nothing(client):
    register(client)
    put_device_scan(client, "scn_mine")
    assert log_in(client, password="wrong-password", deviceId=DEVICE).status_code == 401

    session = log_in(client).json()
    assert client.get("/v1/scans", headers=bearer(session["token"])).json() == {"scans": []}
