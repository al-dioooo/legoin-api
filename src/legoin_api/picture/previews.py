"""Previews of ideas: the ideas' side (ADR-0012, "Idea previews"; contract section 13).

The picture engine builds a preview of each idea Claude wrote itself, after the ideas are
answered, in `jobs.PictureJobs`' queue. This module keeps each idea's `previewStatus` in its
scan's set, and applies a built preview to its idea:

- **After a new set is kept** (`after_ideas`), the old set's previews are cancelled and their
  builds deleted. With `all`, each idea Claude wrote is queued and is `pending`, or
  `unavailable` when it can't be; with `on_open`, it waits as `on_open`. Library ideas keep
  their own preview and get no status.
- **When an `on_open` idea is read** (`on_open`), its preview is queued.
- **A built preview becomes the idea** (`built`): its `buildId`, its summary and steps, and
  `used` and `missing` from `cek_inventori` of the build's own parts against the scan as it is
  now. Its title stays. A preview whose idea is gone, its set replaced, is dropped.
- **A preview that fails** (`failed`) leaves the idea text-only, `unavailable`.
- **A scan deleted, or ideas dropped by a correction** (`forget`), cancels and deletes its
  previews.

Nothing here ever fails a request for ideas: callers log and go on.
"""

import logging

from legoin_api.config import IdeaPreviewMode
from legoin_api.inventory import cek_inventori, merge, to_inventory
from legoin_api.picture.jobs import PictureJob, PictureJobs
from legoin_api.picture.store import StoredIdeaBuild
from legoin_api.schemas import Idea, PartCount, PreviewStatus
from legoin_api.store import Store, StoredScan

log = logging.getLogger(__name__)


def brief(idea: Idea) -> dict:
    """What a preview is built from: the idea as written, with every part it lists."""
    return {
        "title": idea.title,
        "summary": idea.summary,
        "steps": list(idea.steps),
        "parts": [line.wire() for line in merge([*idea.used, *idea.missing])],
    }


class IdeaPreviews:
    def __init__(self, mode: IdeaPreviewMode, jobs: PictureJobs, scans: Store):
        self.mode = mode
        self.jobs = jobs
        self.scans = scans
        jobs.idea_sink = self

    @property
    def on(self) -> bool:
        return self.mode is not IdeaPreviewMode.OFF and self.jobs.previews_on

    # Hooks for the routes

    def after_ideas(self, account_id: str, scan_id: str) -> None:
        """A new set was kept for the scan: drop the old set's previews, start the new set's."""
        scan = self.scans.get_scan(account_id, scan_id)
        if scan is None or scan.ideas is None:
            return
        self.forget(account_id, scan_id, keep=frozenset(i.id for i in scan.ideas.items))
        if not self.on:
            return
        written = [idea for idea in scan.ideas.items if not idea.build_id]
        if self.mode is IdeaPreviewMode.ON_OPEN:
            self._set(account_id, scan_id, dict.fromkeys((i.id for i in written), "on_open"))
            return
        self._start(account_id, scan, written)

    def on_open(self, account_id: str, scan_id: str, idea_id: str) -> None:
        """An idea's page was opened: an `on_open` preview starts now."""
        scan = self.scans.get_scan(account_id, scan_id)
        ideas = scan.ideas.items if scan and scan.ideas else []
        waiting = [i for i in ideas if i.id == idea_id and i.preview_status == "on_open"]
        if scan and waiting and self.on:
            self._start(account_id, scan, waiting)

    def forget(self, account_id: str, scan_id: str, keep: frozenset[str] = frozenset()) -> None:
        """Cancels and deletes the scan's previews, but those of the ideas in `keep`."""
        self.jobs.cancel_ideas(account_id, scan_id, keep)
        self.jobs.forget_idea_builds(account_id, scan_id, keep)

    # The jobs' side

    def built(self, job: PictureJob, build: StoredIdeaBuild, result: dict) -> bool:
        assert job.idea is not None
        idea_id = job.idea["ideaId"]
        applied = False

        def apply(scan: StoredScan) -> StoredScan:
            nonlocal applied
            if scan.ideas is None:
                return scan
            items = []
            for idea in scan.ideas.items:
                if idea.id == idea_id and idea.preview_status == "pending":
                    parts = [PartCount.model_validate(line) for line in result["parts"]]
                    used, missing = cek_inventori(parts, to_inventory(scan.detections))
                    # Plain steps come with a plain summary, written for pictures: keep the idea's.
                    summary = idea.summary if result.get("plain") else result["summary"]
                    idea = idea.model_copy(
                        update={
                            "summary": summary,
                            "steps": list(result["steps"]),
                            "used": used,
                            "missing": missing,
                            "build_id": build.id,
                            "preview_status": "ready",
                        }
                    )
                    applied = True
                items.append(idea)
            return scan.with_changes(ideas=scan.ideas.model_copy(update={"items": items}))

        self.scans.modify_scan(job.account_id, build.scan_id, apply)
        return applied

    def failed(self, job: PictureJob) -> None:
        assert job.idea is not None
        self._set(job.account_id, job.idea["scanId"], {job.idea["ideaId"]: "unavailable"})

    # Under the hood

    def _start(self, account_id: str, scan: StoredScan, ideas: list[Idea]) -> None:
        """Marks the ideas `pending`, then queues them; any that can't be queued become
        `unavailable`. In that order, so a preview that finishes at once finds its idea
        `pending`."""
        assert scan.ideas is not None
        self._set(account_id, scan.id, dict.fromkeys((i.id for i in ideas), "pending"))
        refused = {}
        for idea in ideas:
            status = self.jobs.submit_idea(
                account_id, scan.id, idea.id, brief(idea), scan.ideas.locale
            )
            if status == "unavailable":
                refused[idea.id] = "unavailable"
        if refused:
            self._set(account_id, scan.id, refused)

    def _set(self, account_id: str, scan_id: str, statuses: dict[str, PreviewStatus]) -> None:
        """Sets these ideas' statuses, if they're still in the scan's set and not `ready`."""
        if not statuses:
            return

        def change(scan: StoredScan) -> StoredScan:
            if scan.ideas is None:
                return scan
            items = [
                idea.model_copy(update={"preview_status": statuses[idea.id]})
                if idea.id in statuses and idea.preview_status != "ready"
                else idea
                for idea in scan.ideas.items
            ]
            return scan.with_changes(ideas=scan.ideas.model_copy(update={"items": items}))

        self.scans.modify_scan(account_id, scan_id, change)
