"""What routes share: the device header, the store, and public URLs."""

from typing import Annotated

from fastapi import Depends, Header, Request

from legoin_api.builds import BuildLibrary
from legoin_api.photos import PhotoStore
from legoin_api.schemas import IdeaSet
from legoin_api.store import Store

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


def get_store(request: Request) -> Store:
    return request.app.state.store


def get_photos(request: Request) -> PhotoStore:
    return request.app.state.photos


def get_builds(request: Request) -> BuildLibrary:
    return request.app.state.builds


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
