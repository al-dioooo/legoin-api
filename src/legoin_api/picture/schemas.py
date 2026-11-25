"""Picture builds on the wire (contract section 13), in the contract's camelCase."""

from typing import Annotated, Literal

from pydantic import Field

from legoin_api.schemas import (
    ErrorDetail,
    Id,
    Locale,
    PartCount,
    Photo,
    Preview,
    WireModel,
    _omit_none,
)

PictureStage = Literal["queued", "designing", "checking", "rendering"]
PICTURE_STAGES: tuple[PictureStage, ...] = ("queued", "designing", "checking", "rendering")


class PictureCheck(WireModel):
    scan_id: Id
    used: list[PartCount]
    missing: list[PartCount]


class PictureBuild(WireModel):
    id: Id
    created_at: str
    locale: Locale
    title: str
    summary: str
    steps: list[str]
    parts: list[PartCount]
    picture: Photo
    preview: Preview
    plain: Literal[True] | None = Field(**_omit_none)
    check: PictureCheck | None = Field(**_omit_none)


class PictureBuildList(WireModel):
    builds: list[PictureBuild]


class RunningPictureJob(WireModel):
    id: Id
    status: Literal["running"] = "running"
    stage: PictureStage


class DonePictureJob(WireModel):
    id: Id
    status: Literal["done"] = "done"
    build: PictureBuild


class FailedPictureJob(WireModel):
    id: Id
    status: Literal["failed"] = "failed"
    error: ErrorDetail


PictureJob = Annotated[
    RunningPictureJob | DonePictureJob | FailedPictureJob, Field(discriminator="status")
]


class PictureQuota(WireModel):
    available: bool
    reason: Literal["off", "limit", "budget"] | None = Field(**_omit_none)
    used: int
    limit: int
    resets_at: str
