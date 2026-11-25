"""Passwords, session tokens and the limit on failed log-ins (ADR-0011)."""

import hashlib
import secrets
import threading
import time
from collections import deque
from collections.abc import Callable
from datetime import timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

SESSION_LIFETIME = timedelta(days=30)
"""A session lasts this long from logging in, with no refresh."""

MAX_FAILED_LOGINS = 10
FAILED_LOGIN_WINDOW_S = 15 * 60.0
"""After `MAX_FAILED_LOGINS` failures for one email within the window, logging in to it waits."""

MAX_EMAILS_KEPT = 10_000
"""Past this many emails with failures, the ones whose failures are all old are dropped."""

HASHES_AT_ONCE = 2
"""Each Argon2 hash at the defaults takes 64 MiB, so only this many run at a time."""


def new_token() -> str:
    """`lgn_` and 32 random bytes in URL-safe base64. Only its hash is kept."""
    return f"lgn_{secrets.token_urlsafe(32)}"


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Passwords:
    """Argon2id hashes, from `argon2-cffi` at its defaults (RFC 9106's second profile) unless
    a test passes a cheaper hasher."""

    def __init__(self, hasher: PasswordHasher | None = None):
        self._hasher = hasher or PasswordHasher()
        self._slots = threading.BoundedSemaphore(HASHES_AT_ONCE)
        # Checked against when an email has no account, so the time taken doesn't tell.
        self._stand_in = self._hasher.hash(secrets.token_urlsafe(16))

    def hash(self, password: str) -> str:
        with self._slots:
            return self._hasher.hash(password)

    def verify(self, password_hash: str | None, password: str) -> bool:
        """Whether `password` matches. With no hash, it checks a stand-in and says no."""
        with self._slots:
            try:
                self._hasher.verify(password_hash or self._stand_in, password)
            except (VerificationError, InvalidHashError):
                return False
        return password_hash is not None

    def needs_rehash(self, password_hash: str) -> bool:
        """True when the hash was made with other settings than today's."""
        return self._hasher.check_needs_rehash(password_hash)


class FailedLogins:
    """Failed log-ins per email, in memory: ADR-0008's one process keeps them, and a restart
    forgets them."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self.clock = clock
        self._lock = threading.Lock()
        self._failures: dict[str, deque[float]] = {}

    def _recent(self, email: str, now: float) -> deque[float]:
        failures = self._failures.get(email, deque())
        while failures and failures[0] <= now - FAILED_LOGIN_WINDOW_S:
            failures.popleft()
        if not failures:
            self._failures.pop(email, None)
        return failures

    def retry_after(self, email: str) -> int | None:
        """Seconds until logging in to `email` is allowed again, or None if it is now."""
        with self._lock:
            now = self.clock()
            failures = self._recent(email, now)
            if len(failures) < MAX_FAILED_LOGINS:
                return None
            # Allowed again once the oldest of the last ten is out of the window.
            oldest = failures[-MAX_FAILED_LOGINS]
            return max(1, int(oldest + FAILED_LOGIN_WINDOW_S - now + 0.999))

    def failed(self, email: str) -> None:
        with self._lock:
            now = self.clock()
            failures = self._recent(email, now)
            failures.append(now)
            self._failures[email] = failures
            if len(self._failures) > MAX_EMAILS_KEPT:
                # Many emails tried once each: forget the ones with no recent failure.
                for stale in list(self._failures):
                    self._recent(stale, now)

    def succeeded(self, email: str) -> None:
        with self._lock:
            self._failures.pop(email, None)
