"""Picture builds (contract section 13, ADR-0012): the quota, jobs, builds and their files.

Every route but the files needs a session; builds and jobs belong to the session's account,
through `CurrentAccount`, the one place that says whose they are.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from legoin_api.deps import CurrentAccount, PublicUrlsDep, StoreDep
from legoin_api.errors import ApiError
from legoin_api.inventory import cek_inventori, to_inventory
from legoin_api.photos import read_photo
from legoin_api.picture.files import MEDIA_TYPES, PictureFiles
from legoin_api.picture.jobs import PictureJobs
from legoin_api.picture.schemas import (
    DonePictureJob,
    FailedPictureJob,
    PictureBuild,
    PictureBuildList,
    PictureCheck,
    PictureJob,
    PictureQuota,
    RunningPictureJob,
)
from legoin_api.picture.store import PictureStore, StoredPictureBuild
from legoin_api.schemas import Photo, Preview
from legoin_api.uploads import read_upload

router = APIRouter(prefix="/v1", tags=["picture builds"])

UPLOAD_SCHEMA = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": ["picture", "locale"],
                    "properties": {
                        "picture": {
                            "type": "string",
                            "format": "binary",
                            "description": "JPEG, PNG or WebP, at most 10 MB",
                        },
                        "locale": {"type": "string", "enum": ["en", "id"]},
                    },
                }
            }
        },
    }
}


def get_picture_jobs(request: Request) -> PictureJobs:
    return request.app.state.picture_jobs


def get_picture_store(request: Request) -> PictureStore:
    return request.app.state.picture_store


def get_picture_files(request: Request) -> PictureFiles:
    return request.app.state.picture_files


PictureJobsDep = Annotated[PictureJobs, Depends(get_picture_jobs)]
PictureStoreDep = Annotated[PictureStore, Depends(get_picture_store)]
PictureFilesDep = Annotated[PictureFiles, Depends(get_picture_files)]


def to_wire(build: StoredPictureBuild, base: str) -> PictureBuild:
    def url(name: str) -> str:
        return f"{base}/v1/picture-files/{name}"

    return PictureBuild(
        id=build.id,
        created_at=build.created_at,
        locale=build.locale,
        title=build.title,
        summary=build.summary,
        steps=build.steps,
        parts=build.parts,
        picture=Photo(
            src=url(build.picture_file), width=build.picture_width, height=build.picture_height
        ),
        preview=Preview(image=url(build.preview_file), model=url(build.model_file)),
        plain=True if build.plain else None,
    )


@router.get("/picture-quota")
def get_picture_quota(account: CurrentAccount, jobs: PictureJobsDep) -> PictureQuota:
    return jobs.quota(account.id)


@router.post("/picture-jobs", status_code=202, openapi_extra=UPLOAD_SCHEMA)
async def create_picture_job(
    request: Request, account: CurrentAccount, jobs: PictureJobsDep
) -> RunningPictureJob:
    data, fields = await read_upload(request, "picture", ("locale",))
    locale = fields.get("locale")
    if locale not in ("en", "id"):
        raise ApiError("invalid_request", "locale must be en or id.", field="locale")
    if not jobs.enabled:
        raise ApiError("unavailable", "Picture builds are off on this server.")
    image = await run_in_threadpool(read_photo, data)
    job = await run_in_threadpool(jobs.submit, account.id, image, locale)
    return RunningPictureJob(id=job.id, stage=job.stage)


@router.get("/picture-jobs/{job_id}")
def get_picture_job(
    job_id: str,
    account: CurrentAccount,
    jobs: PictureJobsDep,
    builds: PictureStoreDep,
    urls: PublicUrlsDep,
) -> PictureJob:
    job = jobs.get(account.id, job_id)
    if job is None:
        raise ApiError("not_found", f"No picture job {job_id}.")
    if job.status == "running":
        return RunningPictureJob(id=job.id, stage=job.stage)
    if job.status == "failed":
        assert job.error is not None
        return FailedPictureJob(id=job.id, error=job.error)
    build = builds.get_build(account.id, job.build_id or "")
    if build is None:
        # The build was deleted since; the job has nothing left to show.
        raise ApiError("not_found", f"No picture job {job_id}.")
    return DonePictureJob(id=job.id, build=to_wire(build, urls.base))


@router.delete("/picture-jobs/{job_id}", status_code=204)
def cancel_picture_job(job_id: str, account: CurrentAccount, jobs: PictureJobsDep) -> Response:
    jobs.cancel(account.id, job_id)
    return Response(status_code=204)


@router.get("/picture-builds")
def list_picture_builds(
    account: CurrentAccount, builds: PictureStoreDep, urls: PublicUrlsDep
) -> PictureBuildList:
    return PictureBuildList(
        builds=[to_wire(build, urls.base) for build in builds.list_builds(account.id)]
    )


@router.get("/picture-builds/{build_id}")
def get_picture_build(
    build_id: str,
    account: CurrentAccount,
    builds: PictureStoreDep,
    scans: StoreDep,
    urls: PublicUrlsDep,
    scan_id: Annotated[str | None, Query(alias="scanId")] = None,
) -> PictureBuild:
    build = builds.get_build(account.id, build_id)
    if build is None:
        raise ApiError("not_found", f"No picture build {build_id}.")
    wire = to_wire(build, urls.base)
    # A scan that isn't this account's leaves `check` out: the build itself is there.
    scan = scans.get_scan(account.id, scan_id) if scan_id else None
    if scan is not None:
        used, missing = cek_inventori(build.parts, to_inventory(scan.detections))
        wire.check = PictureCheck(scan_id=scan.id, used=used, missing=missing)
    return wire


@router.delete("/picture-builds/{build_id}", status_code=204)
def delete_picture_build(build_id: str, account: CurrentAccount, jobs: PictureJobsDep) -> Response:
    jobs.delete_build(account.id, build_id)
    return Response(status_code=204)


@router.get("/picture-files/{name}", response_class=FileResponse)
def get_picture_file(name: str, files: PictureFilesDep) -> FileResponse:
    """A build's picture, preview or model: no header needed, an unguessable name instead."""
    path = files.path(name)
    if path is None or not path.is_file():
        raise ApiError("not_found", "No such picture file.")
    suffix = name.rsplit(".", 1)[1]
    headers = {"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"}
    if suffix == "ldr":
        headers["Content-Disposition"] = 'attachment; filename="legoin-picture-build.ldr"'
    return FileResponse(path, media_type=MEDIA_TYPES[suffix], headers=headers)
