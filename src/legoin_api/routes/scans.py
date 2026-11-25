"""Scans: history, one scan, corrections and deletion (contract section 5)."""

from fastapi import APIRouter, Response

from legoin_api.deps import CurrentAccount, PhotosDep, PublicUrlsDep, ScanBuildsDep, StoreDep
from legoin_api.errors import ApiError
from legoin_api.ids import new_id
from legoin_api.inventory import apply_changes, same_inventory
from legoin_api.schemas import DetectionChanges, Scan, ScanList
from legoin_api.store import StoredScan

router = APIRouter(prefix="/v1", tags=["scans"])


def scan_not_found(scan_id: str) -> ApiError:
    return ApiError("not_found", f"No scan {scan_id}.")


@router.get("/scans")
def list_scans(account: CurrentAccount, store: StoreDep, urls: PublicUrlsDep) -> ScanList:
    return ScanList(scans=[scan.to_wire(urls) for scan in store.list_scans(account.id)])


@router.get("/scans/{scan_id}")
def get_scan(scan_id: str, account: CurrentAccount, store: StoreDep, urls: PublicUrlsDep) -> Scan:
    scan = store.get_scan(account.id, scan_id)
    if scan is None:
        raise scan_not_found(scan_id)
    return scan.to_wire(urls)


@router.patch("/scans/{scan_id}/detections")
def update_detections(
    scan_id: str,
    body: DetectionChanges,
    account: CurrentAccount,
    store: StoreDep,
    urls: PublicUrlsDep,
    scan_builds: ScanBuildsDep,
) -> Scan:
    def correct(scan: StoredScan) -> StoredScan:
        taken = {d.id for d in scan.detections}

        def detection_id() -> str:
            while (candidate := new_id("det", 6)) in taken:
                pass
            taken.add(candidate)
            return candidate

        detections = apply_changes(scan.detections, body.changes, detection_id)
        # Ideas were checked against the pieces as they were; they stay only if those didn't change.
        ideas = scan.ideas if same_inventory(scan.detections, detections) else None
        return scan.with_changes(detections=detections, ideas=ideas)

    scan = store.modify_scan(account.id, scan_id, correct)
    if scan is None:
        raise scan_not_found(scan_id)
    if scan.ideas is None:
        # The ideas went with the pieces they were checked against, and so do their builds.
        scan_builds.forget(account.id, scan_id)
    return scan.to_wire(urls)


@router.delete("/scans/{scan_id}", status_code=204)
def delete_scan(
    scan_id: str,
    account: CurrentAccount,
    store: StoreDep,
    photos: PhotosDep,
    scan_builds: ScanBuildsDep,
) -> Response:
    scan = store.delete_scan(account.id, scan_id)
    if scan is not None:
        photos.delete(scan.photo_file)
        scan_builds.forget(account.id, scan_id)
    return Response(status_code=204)
