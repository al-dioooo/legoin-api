"""Accounts: register, log in, log out and read the account (contract section 4, ADR-0011).

The routes are plain functions, so FastAPI runs them in its thread pool: Argon2 takes tens of
milliseconds and never blocks the event loop.
"""

from typing import Annotated

from fastapi import APIRouter, Header, Response

from legoin_api.accounts import SESSION_LIFETIME, new_token, token_hash
from legoin_api.deps import (
    CurrentAccount,
    FailedLoginsDep,
    PasswordsDep,
    StoreDep,
    bearer_token,
)
from legoin_api.errors import ApiError
from legoin_api.ids import new_id, utc_now
from legoin_api.schemas import Account, LogIn, Registration, Session
from legoin_api.store import Store, StoredAccount

router = APIRouter(prefix="/v1/auth", tags=["accounts"])


def start_session(store: Store, account: StoredAccount) -> Session:
    token = new_token()
    expires_at = utc_now(SESSION_LIFETIME)
    store.insert_session(token_hash(token), account.id, expires_at)
    return Session(token=token, expires_at=expires_at, account=account.to_wire())


@router.post("/register", status_code=201)
def register(body: Registration, store: StoreDep, passwords: PasswordsDep) -> Session:
    account = StoredAccount(
        id=new_id("acc"),
        email=body.email,
        password_hash=passwords.hash(body.password),
        created_at=utc_now(),
    )
    if not store.insert_account(account):
        raise ApiError("email_taken", "This email already has an account.")
    return start_session(store, account)


@router.post("/login")
def log_in(
    body: LogIn, store: StoreDep, passwords: PasswordsDep, failed: FailedLoginsDep
) -> Session:
    wait = failed.retry_after(body.email)
    if wait is not None:
        raise ApiError(
            "rate_limited",
            "Too many failed log-ins for this email. Try again later.",
            headers={"Retry-After": str(wait)},
        )
    account = store.account_by_email(body.email)
    # An unknown email is checked against a stand-in hash, so both failures take as long.
    if not passwords.verify(account.password_hash if account else None, body.password):
        failed.failed(body.email)
        raise ApiError("invalid_credentials", "Wrong email or password.")
    assert account is not None
    failed.succeeded(body.email)
    if passwords.needs_rehash(account.password_hash):
        store.set_password_hash(account.id, passwords.hash(body.password))
    return start_session(store, account)


@router.post("/logout", status_code=204)
def log_out(
    store: StoreDep,
    authorization: Annotated[str | None, Header(include_in_schema=False)] = None,
) -> Response:
    """Ends this session. Answers 204 whatever the token, so logging out never fails."""
    if token := bearer_token(authorization):
        store.delete_session(token_hash(token))
    return Response(status_code=204)


@router.get("/me")
def me(account: CurrentAccount) -> Account:
    return account.to_wire()
