import json
from pathlib import Path

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient

from legoin_api.accounts import token_hash
from legoin_api.config import Settings
from legoin_api.main import create_app
from legoin_api.store import Store, StoredAccount


@pytest.fixture
def settings(tmp_path) -> Settings:
    # Templates, even when ANTHROPIC_API_KEY is set: only the live test calls the API.
    return Settings(pipeline="stub", data_dir=tmp_path / "data", ideas_generator="templates")


FAST_HASHER = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)
"""Argon2id at its cheapest, so tests that log in stay fast. The app's own default is RFC
9106's second profile."""


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings, password_hasher=FAST_HASHER)) as client:
        yield client


CONTRACT_DIR = Path(__file__).parent / "contract"


@pytest.fixture
def example():
    """Loads one of the contract's example bodies from `tests/contract/`."""

    def load(name: str) -> dict:
        return json.loads((CONTRACT_DIR / f"{name}.json").read_text())

    return load


DEVICE = "3b0e6f1c-5d2a-4f7e-9c41-8a2d7e5b9f10"
"""The device id in the contract's examples, from before accounts."""

ACCOUNT, TOKEN = "acc_test", "lgn_test-account-token"
OTHER_ACCOUNT, OTHER_TOKEN = "acc_other", "lgn_other-account-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
OTHER_AUTH = {"Authorization": f"Bearer {OTHER_TOKEN}"}
"""The headers of two accounts every test store has, logged in."""


@pytest.fixture(autouse=True)
def test_accounts(monkeypatch):
    """Every store a test opens has `ACCOUNT` and `OTHER_ACCOUNT`, with sessions for `TOKEN`
    and `OTHER_TOKEN` that don't expire, so any app a test makes takes `AUTH`."""
    opened = Store.__init__

    def open_with_accounts(self, path):
        opened(self, path)
        for account_id, token in ((ACCOUNT, TOKEN), (OTHER_ACCOUNT, OTHER_TOKEN)):
            account = StoredAccount(account_id, f"{account_id}@legoin.test", "-", "2026-11-25")
            if self.insert_account(account):
                self.insert_session(token_hash(token), account_id, "2999-01-01T00:00:00.000Z")

    monkeypatch.setattr(Store, "__init__", open_with_accounts)


@pytest.fixture
def seed(client):
    """Puts a scan straight into the store, with a small photo file, and returns it."""
    from legoin_api.schemas import Detection, IdeaSet
    from legoin_api.store import StoredScan

    def put(scan: dict, account: str = ACCOUNT, **extra) -> StoredScan:
        photos = client.app.state.photos
        name = photos.new_name()
        (photos.directory / name).write_bytes(b"\xff\xd8\xff\xd9")
        stored = StoredScan(
            id=scan["id"],
            account_id=account,
            created_at=scan["createdAt"],
            photo_file=name,
            photo_width=scan["photo"]["width"],
            photo_height=scan["photo"]["height"],
            detections=[Detection.model_validate(d) for d in scan["detections"]],
            last_theme=extra.get("last_theme", scan.get("lastTheme")),
            ideas=IdeaSet.model_validate(extra["ideas"]) if extra.get("ideas") else None,
        )
        client.app.state.store.insert_scan(stored)
        return stored

    return put
