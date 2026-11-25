"""Scan jobs: upload a photo, poll its stages, cancel (contract section 4)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from starlette.datastructures import UploadFile

from legoin_api.deps import DeviceId, PhotoUrlsDep, StoreDep
from legoin_api.errors import ApiError
from legoin_api.jobs import JobManager
from legoin_api.photos import MAX_PHOTO_BYTES, read_photo
from legoin_api.schemas import DoneJob, FailedJob, RunningJob, ScanJob

router = APIRouter(prefix="/v1", tags=["scan jobs"])

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


async def _read_upload(request: Request) -> bytes:
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > MAX_BODY_BYTES:
        raise ApiError("photo_too_large", "The photo is over 10 MB.")
    try:
        form = await request.form(max_files=1, max_fields=10)
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
async def create_scan_job(request: Request, device: DeviceId, jobs: JobsDep) -> RunningJob:
    image = read_photo(await _read_upload(request))
    job = jobs.submit(device, image)
    return RunningJob(id=job.id, stage=job.stage)


@router.get("/scan-jobs/{job_id}")
def get_scan_job(
    job_id: str, device: DeviceId, jobs: JobsDep, store: StoreDep, urls: PhotoUrlsDep
) -> ScanJob:
    job = jobs.get(device, job_id)
    if job is None:
        raise ApiError("not_found", f"No scan job {job_id}.")
    if job.status == "running":
        return RunningJob(id=job.id, stage=job.stage)
    if job.status == "failed":
        assert job.error is not None
        return FailedJob(id=job.id, error=job.error)
    scan = store.get_scan(device, job.scan_id or "")
    if scan is None:
        # The scan was deleted since; the job has nothing left to show.
        raise ApiError("not_found", f"No scan job {job_id}.")
    return DoneJob(id=job.id, scan=scan.to_wire(urls))


@router.delete("/scan-jobs/{job_id}", status_code=204)
def cancel_scan_job(job_id: str, device: DeviceId, jobs: JobsDep) -> Response:
    jobs.cancel(device, job_id)
    return Response(status_code=204)
