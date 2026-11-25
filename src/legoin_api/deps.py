"""What routes share: the session, the store, and public URLs."""

from typing import Annotated

from fastapi import Depends, Header, Request

from legoin_api.accounts import FailedLogins, Passwords, token_hash
from legoin_api.builds import BuildLibrary
from legoin_api.errors import ApiError
from legoin_api.photos import PhotoStore
from legoin_api.schemas import IdeaSet
from legoin_api.store import Store, StoredAccount

DEVICE_HEADER = "Legoin-Device-Id"

DeviceId = Annotated[
    str,
    Header(
        alias=DEVICE_HEADER,
        pattern=r"^[A-Za-z0-9._-]{1,128}$",
        description="The contract's placeholder for accounts: a random id the browser keeps.",
    ),
]
"""Every contract route needs it; the backend only shows a device its own scans."""


def bearer_token(authorization: str | None) -> str | None:
    """The token in `Authorization: Bearer <token>`, or None if there's no such header."""
    scheme, _, token = (authorization or "").partition(" ")
    token = token.strip()
    return token if scheme.lower() == "bearer" and token else None


def current_account(
    request: Request,
    authorization: Annotated[str | None, Header(include_in_schema=False)] = None,
) -> StoredAccount:
    """The logged-in account, or `401 unauthorized` before any other check (ADR-0011)."""
    token = bearer_token(authorization)
    account = request.app.state.store.session_account(token_hash(token)) if token else None
    if account is None:
        raise ApiError(
            "unauthorized",
            "Log in first: this needs a valid session in Authorization: Bearer <token>.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return account


def get_store(request: Request) -> Store:
    return request.app.state.store


def get_photos(request: Request) -> PhotoStore:
    return request.app.state.photos


def get_builds(request: Request) -> BuildLibrary:
    return request.app.state.builds


def get_passwords(request: Request) -> Passwords:
    return request.app.state.passwords


def get_failed_logins(request: Request) -> FailedLogins:
    return request.app.state.failed_logins


class PublicUrls:
    """Absolute URLs of photos and previews, from `LEGOIN_PUBLIC_URL` when set, else from the
    request."""

    def __init__(self, request: Request):
        public_url = request.app.state.settings.public_url
        self.base = (public_url or str(request.base_url)).rstrip("/")
        self.builds = get_builds(request)

    def photo(self, name: str) -> str:
        return f"{self.base}/v1/photos/{name}"

    def with_previews(self, ideas: IdeaSet) -> IdeaSet:
        return self.builds.with_previews(ideas, self.base)


StoreDep = Annotated[Store, Depends(get_store)]
PhotosDep = Annotated[PhotoStore, Depends(get_photos)]
BuildsDep = Annotated[BuildLibrary, Depends(get_builds)]
PublicUrlsDep = Annotated[PublicUrls, Depends(PublicUrls)]
PasswordsDep = Annotated[Passwords, Depends(get_passwords)]
FailedLoginsDep = Annotated[FailedLogins, Depends(get_failed_logins)]
CurrentAccount = Annotated[StoredAccount, Depends(current_account)]
"""Every route that needs a session takes it."""
