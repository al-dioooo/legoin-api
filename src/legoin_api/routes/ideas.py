"""Ideas: three builds for a scan, and one idea read back (contract section 6)."""

import asyncio
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from starlette.concurrency import run_in_threadpool

from legoin_api.deps import CurrentAccount, PreviewsDep, PublicUrlsDep, StoreDep
from legoin_api.errors import ApiError
from legoin_api.ideas.generator import IdeaGenerator, IdeasUnavailable
from legoin_api.ideas.service import write_ideas
from legoin_api.routes.scans import scan_not_found
from legoin_api.schemas import IdeaSet, IdeasRequest, StoredIdea

router = APIRouter(prefix="/v1", tags=["ideas"])
log = logging.getLogger(__name__)


def get_generator(request: Request) -> IdeaGenerator:
    return request.app.state.ideas


GeneratorDep = Annotated[IdeaGenerator, Depends(get_generator)]


@router.post("/scans/{scan_id}/ideas")
async def create_ideas(
    scan_id: str,
    body: IdeasRequest,
    account: CurrentAccount,
    store: StoreDep,
    generator: GeneratorDep,
    urls: PublicUrlsDep,
    previews: PreviewsDep,
    request: Request,
) -> IdeaSet:
    scan = await run_in_threadpool(store.get_scan, account.id, scan_id)
    if scan is None:
        raise scan_not_found(scan_id)
    written = await run_in_threadpool(store.written_ideas, account.id, scan_id)
    timeout = request.app.state.settings.ideas_timeout_s
    try:
        async with asyncio.timeout(timeout):
            ideas, keys, inventory = await write_ideas(generator, scan, body, written)
    except TimeoutError as error:
        raise ApiError("timeout", f"Writing ideas took over {timeout:g} seconds.") from error
    except IdeasUnavailable as error:
        raise ApiError(
            "unavailable", "The idea writer is unavailable.", headers={"Retry-After": "10"}
        ) from error
    # A request that fails changes nothing; only now is the set kept, and what's kept is
    # what's answered.
    kept = await run_in_threadpool(store.keep_ideas, account.id, scan_id, ideas, keys, inventory)
    if kept is None or kept.ideas is None:
        raise scan_not_found(scan_id)
    # Previews start after the set is kept, and never fail the request (ADR-0012).
    try:
        await run_in_threadpool(previews.after_ideas, account.id, scan_id)
        kept = await run_in_threadpool(store.get_scan, account.id, scan_id) or kept
    except Exception:
        log.exception("Couldn't start the previews of scan %s's ideas", scan_id)
    if kept.ideas is None:
        raise scan_not_found(scan_id)
    return urls.with_previews(kept.ideas)


@router.get("/scans/{scan_id}/ideas/{idea_id}")
def get_idea(
    scan_id: str,
    idea_id: str,
    account: CurrentAccount,
    store: StoreDep,
    urls: PublicUrlsDep,
    previews: PreviewsDep,
) -> StoredIdea:
    # Opening an `on_open` idea starts its preview; the answer already says `pending`.
    try:
        previews.on_open(account.id, scan_id, idea_id)
    except Exception:
        log.exception("Couldn't start the preview of idea %s", idea_id)
    scan = store.get_scan(account.id, scan_id)
    if scan is None:
        raise scan_not_found(scan_id)
    ideas = urls.with_previews(scan.ideas) if scan.ideas else None
    idea = next((i for i in ideas.items if i.id == idea_id), None) if ideas else None
    if ideas is None or idea is None:
        raise ApiError("not_found", f"No idea {idea_id} in scan {scan_id}'s latest ideas.")
    return StoredIdea(idea=idea, set=ideas)
