"""Scan jobs: upload a photo, poll its stages, cancel (contract section 4)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.types import Message, Receive

from legoin_api.deps import CurrentAccount, PublicUrlsDep, StoreDep
from legoin_api.errors import ApiError
from legoin_api.jobs import JobManager, QueueFull
from legoin_api.photos import MAX_PHOTO_BYTES, read_photo
from legoin_api.schemas import DoneJob, FailedJob, RunningJob, ScanJob

router = APIRouter(prefix="/v1", tags=["scan jobs"])

RETRY_AFTER_S = 5
"""When a full queue is worth trying again: a scan takes about half a second on the models."""

# Room for the multipart boundaries and headers around a photo at the limit.
MAX_BODY_BYTES = MAX_PHOTO_BYTES + 64 * 1024

UPLOAD_SCHEMA = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": ["photo"],
                    "properties": {
                        "photo": {
                            "type": "string",
                            "format": "binary",
                            "description": "JPEG, PNG or WebP, at most 10 MB",
                        }
                    },
                }
            }
        },
    }
}


def get_jobs(request: Request) -> JobManager:
    return request.app.state.jobs


JobsDep = Annotated[JobManager, Depends(get_jobs)]


def _limit_body(receive: Receive, limit: int) -> Receive:
    """Counts the body's bytes as they arrive and refuses it once past `limit`.

    A chunked upload has no `Content-Length` to check first, and would otherwise be spooled
    to disk in full before the photo's size is known.
    """
    seen = 0

    async def limited() -> Message:
        nonlocal seen
        message = await receive()
        if message["type"] == "http.request":
            seen += len(message.get("body", b""))
            if seen > limit:
                raise ApiError("photo_too_large", "The photo is over 10 MB.")
        return message

    return limited


async def _read_upload(request: Request) -> bytes:
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > MAX_BODY_BYTES:
        raise ApiError("photo_too_large", "The photo is over 10 MB.")
    counted = Request(request.scope, _limit_body(request.receive, MAX_BODY_BYTES))
    try:
        form = await counted.form(max_files=1, max_fields=10)
    except ApiError:
        raise
    except Exception as error:
        raise ApiError("invalid_request", "The body isn't multipart/form-data.") from error
    photo = form.get("photo")
    if not isinstance(photo, UploadFile):
        raise ApiError("invalid_request", "There's no photo part.", field="photo")
    # Read one byte past the limit: enough to know it's too large, without reading it all.
    data = await photo.read(MAX_PHOTO_BYTES + 1)
    await form.close()
    return data


@router.post("/scan-jobs", status_code=202, openapi_extra=UPLOAD_SCHEMA)
async def create_scan_job(request: Request, account: CurrentAccount, jobs: JobsDep) -> RunningJob:
    data = await _read_upload(request)
    # A place in the queue first, so no more photos are decoded or held than it allows.
    try:
        jobs.reserve()
    except QueueFull as error:
        raise ApiError(
            "unavailable",
            "Too many scans are waiting. Try again in a few seconds.",
            headers={"Retry-After": str(RETRY_AFTER_S)},
        ) from error
    try:
        # Decoding takes a while for a large photo, so it runs off the event loop.
        image = await run_in_threadpool(read_photo, data)
        job = jobs.submit(account.id, image, reserved=True)
    except BaseException:
        jobs.release()
        raise
    return RunningJob(id=job.id, stage=job.stage)


@router.get("/scan-jobs/{job_id}")
def get_scan_job(
    job_id: str, account: CurrentAccount, jobs: JobsDep, store: StoreDep, urls: PublicUrlsDep
) -> ScanJob:
    job = jobs.get(account.id, job_id)
    if job is None:
        raise ApiError("not_found", f"No scan job {job_id}.")
    if job.status == "running":
        return RunningJob(id=job.id, stage=job.stage)
    if job.status == "failed":
        assert job.error is not None
        return FailedJob(id=job.id, error=job.error)
    scan = store.get_scan(account.id, job.scan_id or "")
    if scan is None:
        # The scan was deleted since; the job has nothing left to show.
        raise ApiError("not_found", f"No scan job {job_id}.")
    return DoneJob(id=job.id, scan=scan.to_wire(urls))


@router.delete("/scan-jobs/{job_id}", status_code=204)
def cancel_scan_job(job_id: str, account: CurrentAccount, jobs: JobsDep) -> Response:
    jobs.cancel(account.id, job_id)
    return Response(status_code=204)
