"""Ids and times, as the contract writes them."""

import secrets
from datetime import UTC, datetime, timedelta


def new_id(prefix: str, nbytes: int = 9) -> str:
    """A random, URL-safe id such as `scn_4kd2p9xq...`, well under the contract's 64 characters."""
    return f"{prefix}_{secrets.token_urlsafe(nbytes)}"


def utc_now(later: timedelta | None = None) -> str:
    """ISO 8601 in UTC with milliseconds and a `Z`, so sorting the strings sorts by time.
    With `later`, the time that long from now."""
    now = datetime.now(UTC) + (later or timedelta())
    return now.isoformat(timespec="milliseconds").replace("+00:00", "Z")
