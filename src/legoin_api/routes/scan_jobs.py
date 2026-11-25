"""Scan jobs: upload a photo, poll its stages, cancel (contract section 5)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from starlette.concurrency import run_in_threadpool

from legoin_api.deps import CurrentAccount, PublicUrlsDep, StoreDep
from legoin_api.errors import ApiError
from legoin_api.jobs import JobManager, QueueFull
from legoin_api.photos import read_photo
from legoin_api.schemas import DoneJob, FailedJob, RunningJob, ScanJob
from legoin_api.uploads import read_upload

router = APIRouter(prefix="/v1", tags=["scan jobs"])

RETRY_AFTER_S = 5
"""When a full queue is worth trying again: a scan takes about half a second on the models."""

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


@router.post("/scan-jobs", status_code=202, openapi_extra=UPLOAD_SCHEMA)
async def create_scan_job(request: Request, account: CurrentAccount, jobs: JobsDep) -> RunningJob:
    data, _ = await read_upload(request, "photo")
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
