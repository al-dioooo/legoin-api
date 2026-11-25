"""Ideas built from the scan: the ideas' side of ADR-0013.

When builds can run, a request for ideas is answered at once with a set of three slots, one
per theme slot as before:

- **A library build** (ADR-0010) fills a slot when the person owns every piece of one in the
  slot's theme, or all but one, and hasn't seen it: the same rule Claude followed when it
  wrote ideas. It's an idea with its picture straight away.
- **Every other slot is built** by Claude from the scan's photo and pieces, in one job for the
  set (`jobs.BuildJobs`). Until its build ends, the slot is in the set's `building`, with the
  id its idea will have.

`plan` answers None when the set can't be built (builds are off, or the account, the budget or
the queue can't take it), and the route writes the ideas as text instead, as before.

- **A finished build becomes its idea** (`built`): Claude's title, summary and steps, a
  difficulty from its piece count, its `buildId` and preview, and `used` and `missing` from
  `cek_inventori` of its parts against the scan as it is then. `missing` is empty unless a
  correction took pieces away meanwhile. A build whose idea is no longer in the set is dropped.
- **A build that fails** (`failed`) is replaced by an idea written as text for its slot, by the
  same generator as the fallback, so a set always ends with its ideas.
- **A new set, a correction that drops the ideas, or a deleted scan** (`forget`) cancels the
  old set's builds and deletes the ones it made.

Nothing here fails a request for ideas: callers log and go on.
"""

import asyncio
import logging
import re
from collections.abc import Coroutine
from dataclasses import dataclass

from legoin_api.builds import BuildLibrary, LibraryBuild
from legoin_api.errors import ApiError
from legoin_api.ideas.generator import IdeaGenerator, IdeaRequest, ShownIdea
from legoin_api.ideas.service import IDEA_THEMES, IDEAS_PER_SET, acceptable
from legoin_api.ids import new_id
from legoin_api.inventory import cek_inventori, to_inventory
from legoin_api.photos import PhotoStore
from legoin_api.picture.jobs import BuildJob, BuildJobs, Slot
from legoin_api.picture.prompt import kind_for
from legoin_api.picture.store import StoredIdeaBuild
from legoin_api.schemas import BuildingIdea, Idea, IdeaSet, IdeasRequest, PartCount
from legoin_api.store import Store, StoredScan

log = logging.getLogger(__name__)

MAX_LIBRARY_MISSING = 1
"""A library build fills a slot for the person's own pieces: all of them, or all but one."""
MAX_AVOID = 6
"""Titles of builds the person has seen, passed on so new builds differ."""
WRITE_INSTEAD_S = 30.0


@dataclass(frozen=True)
class Plan:
    """A set as it's answered: its library ideas, and the slots being built."""

    ideas: IdeaSet
    keys: list[str]
    """Each library idea's generator key, for "Try other ideas"."""
    inventory: list[PartCount]
    slots: list[Slot]


def key_of(title: str) -> str:
    """A built idea's generator key, as for an idea Claude wrote: from its title."""
    return "claude:" + re.sub(r"[^a-z0-9]+", "-", title.casefold()).strip("-")


class ScanBuilds:
    def __init__(
        self,
        jobs: BuildJobs,
        scans: Store,
        photos: PhotoStore,
        library: BuildLibrary,
        generator: IdeaGenerator,
        loop: asyncio.AbstractEventLoop | None = None,
    ):
        self.jobs = jobs
        self.scans = scans
        self.photos = photos
        self.library = library
        self.generator = generator
        """Writes an idea as text for a build that failed."""
        self.loop = loop
        """The app's event loop, where those ideas are written."""
        jobs.sink = self

    @property
    def on(self) -> bool:
        return self.jobs.enabled

    # Hooks for the routes

    def plan(
        self, account_id: str, scan: StoredScan, body: IdeasRequest, written: list[ShownIdea]
    ) -> Plan | None:
        """The set to answer with, its library picks and its slots to build; None when the
        ideas are written as text instead."""
        if not self.on:
            return None
        inventory = to_inventory(scan.detections)
        if not inventory:
            raise ApiError("no_pieces", f"Scan {scan.id} has no pieces to build with.")
        surprise = body.theme == "surprise"
        themes = IDEA_THEMES if surprise else [body.theme] * IDEAS_PER_SET
        by_id = {idea.id: idea for idea in written}
        excluded = [by_id[idea_id] for idea_id in body.exclude if idea_id in by_id]
        seen = {idea.key for idea in excluded}
        avoid = self._avoid(excluded, written)
        taken = set(by_id)
        items: list[Idea] = []
        keys: list[str] = []
        slots: list[Slot] = []
        for index, theme in enumerate(themes):
            while (idea_id := new_id("idea", 6)) in taken:
                pass
            taken.add(idea_id)
            if pick := self._library_pick(theme, inventory, seen):
                seen.add(pick.key)
                proposal = pick.propose(body.locale)
                used, missing = cek_inventori(proposal.requires, inventory)
                items.append(
                    Idea(
                        id=idea_id,
                        theme=theme,
                        difficulty=proposal.difficulty,
                        title=proposal.title,
                        summary=proposal.summary,
                        steps=proposal.steps,
                        used=used,
                        missing=missing,
                        build_id=proposal.build_id,
                    )
                )
                keys.append(pick.key)
            else:
                slots.append(Slot(idea_id, theme, kind_for(theme, index, surprise), avoid))
        if slots and (refusal := self.jobs.refusal(account_id, len(slots))):
            log.info("Ideas for scan %s written as text: builds are %s", scan.id, refusal)
            return None
        building = [BuildingIdea(id=slot.idea_id, theme=slot.theme) for slot in slots]
        ideas = IdeaSet(
            theme=body.theme, locale=body.locale, items=items, building=building or None
        )
        return Plan(ideas, keys, inventory, slots)

    def after_ideas(self, account_id: str, kept: StoredScan, plan: Plan | None) -> None:
        """A new set was kept: the old set's builds go, and the new set's start."""
        ideas = kept.ideas
        if ideas is None:
            return
        ids = {idea.id for idea in ideas.items} | {b.id for b in ideas.building or []}
        self.forget(account_id, kept.id, keep=frozenset(ids))
        if plan is None or not plan.slots:
            return
        photo = self.photos.path(kept.photo_file)
        status = "busy" if photo is None else None
        if photo is not None:
            status = self.jobs.submit(
                account_id,
                kept.id,
                ideas.locale,
                photo,
                to_inventory(kept.detections),
                plan.slots,
            )
        if status != "pending":
            # Taken by another request since `plan` checked: write these ideas instead.
            log.info("Scan %s's builds weren't queued (%s): writing them instead", kept.id, status)
            for slot in plan.slots:
                self._soon(self._write_instead(account_id, kept.id, slot))

    def forget(self, account_id: str, scan_id: str, keep: frozenset[str] = frozenset()) -> None:
        """Cancels the scan's builds and deletes what they made, but those of the ideas in
        `keep`."""
        self.jobs.cancel(account_id, scan_id, keep)
        self.jobs.forget_builds(account_id, scan_id, keep)

    # The jobs' side

    def built(self, job: BuildJob, slot: Slot, build: StoredIdeaBuild, result: dict) -> bool:
        parts = [PartCount.model_validate(line) for line in result["parts"]]
        title = str(result["title"])

        def idea_for(scan: StoredScan) -> Idea:
            used, missing = cek_inventori(parts, to_inventory(scan.detections))
            return Idea(
                id=slot.idea_id,
                theme=slot.theme,
                difficulty=result.get("difficulty", "medium"),
                title=title,
                summary=str(result["summary"]),
                steps=[str(step) for step in result["steps"]],
                used=used,
                missing=missing,
                build_id=build.id,
            )

        return self._settle(job.account_id, job.scan_id, slot, idea_for, key_of(title))

    def failed(self, job: BuildJob, slot: Slot) -> None:
        self._soon(self._write_instead(job.account_id, job.scan_id, slot))

    # Under the hood

    def _library_pick(
        self, theme: str, inventory: list[PartCount], seen: set[str]
    ) -> LibraryBuild | None:
        """The library build that best fits the person's pieces in this theme, if one fits."""
        best: tuple[tuple[int, int], LibraryBuild] | None = None
        for build in self.library:
            if build.theme != theme or build.key in seen:
                continue
            used, missing = cek_inventori(build.parts, inventory)
            lacking = sum(line.quantity for line in missing)
            if lacking > MAX_LIBRARY_MISSING:
                continue
            rank = (lacking, -sum(line.quantity for line in used))
            if best is None or rank < best[0]:
                best = (rank, build)
        return best[1] if best else None

    @staticmethod
    def _avoid(excluded: list[ShownIdea], written: list[ShownIdea]) -> list[str]:
        """Titles of ideas the person has seen that weren't library builds, newest first."""
        titles: list[str] = []
        for idea in [*excluded, *reversed(written)]:
            if not idea.key.startswith("template:") and idea.title not in titles:
                titles.append(idea.title)
        return titles[:MAX_AVOID]

    def _settle(self, account_id: str, scan_id: str, slot: Slot, idea_for, key: str) -> bool:
        """Puts the slot's idea in the set in place of its `building` entry, if the slot is
        still there. Whether it was."""
        settled = False

        def change(scan: StoredScan) -> StoredScan:
            nonlocal settled
            ideas = scan.ideas
            if ideas is None or not any(b.id == slot.idea_id for b in ideas.building or []):
                return scan
            building = [b for b in ideas.building or [] if b.id != slot.idea_id]
            items = list(ideas.items)
            if (idea := idea_for(scan)) is not None:
                items.append(idea)
            settled = idea is not None
            update = {"items": items, "building": building or None}
            return scan.with_changes(ideas=ideas.model_copy(update=update))

        scan = self.scans.modify_scan(account_id, scan_id, change)
        if settled and scan is not None:
            idea = next(i for i in scan.ideas.items if i.id == slot.idea_id)  # type: ignore[union-attr]
            self.scans.record_written(scan_id, idea.id, key, idea.theme, idea.title)
        return settled

    async def _write_instead(self, account_id: str, scan_id: str, slot: Slot) -> None:
        """Writes the slot's idea as text, as the fallback does; with no idea to give, the slot
        just goes."""
        scan = self.scans.get_scan(account_id, scan_id)
        if scan is None or scan.ideas is None:
            return
        if not any(b.id == slot.idea_id for b in scan.ideas.building or []):
            return
        inventory = to_inventory(scan.detections)
        written = self.scans.written_ideas(account_id, scan_id)
        current = {idea.id for idea in scan.ideas.items}
        proposal = None
        if inventory:
            try:
                async with asyncio.timeout(WRITE_INSTEAD_S):
                    proposals = await self.generator.propose(
                        IdeaRequest(
                            themes=[slot.theme],  # type: ignore[list-item]
                            locale=scan.ideas.locale,
                            inventory=inventory,
                            excluded=[idea for idea in written if idea.id in current],
                            shown=written,
                        )
                    )
                if proposals and acceptable(proposals[0], slot.theme):  # type: ignore[arg-type]
                    proposal = proposals[0]
            except Exception:
                log.exception("Couldn't write an idea instead of scan %s's build", scan_id)

        def idea_for(scan: StoredScan) -> Idea | None:
            if proposal is None:
                return None
            used, missing = cek_inventori(proposal.requires, to_inventory(scan.detections))
            return Idea(
                id=slot.idea_id,
                theme=proposal.theme,
                difficulty=proposal.difficulty,
                title=proposal.title,
                summary=proposal.summary,
                steps=proposal.steps,
                used=used,
                missing=missing,
                build_id=proposal.build_id,
            )

        key = proposal.key if proposal else ""
        self._settle(account_id, scan_id, slot, idea_for, key)
        log.info("Scan %s's build %s failed; %s", scan_id, slot.idea_id,
                 "written instead" if proposal else "dropped")  # fmt: skip

    def _soon(self, work: Coroutine) -> None:
        """Runs `work` on the app's loop, from the jobs' thread; or here, with no loop."""
        loop = self.loop
        if loop is not None and loop.is_running():
            asyncio.run_coroutine_threadsafe(work, loop)
        else:
            asyncio.run(work)
