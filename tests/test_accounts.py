"""Passwords and the limit on failed log-ins (ADR-0011)."""

from argon2 import PasswordHasher

from legoin_api.accounts import (
    FAILED_LOGIN_WINDOW_S,
    MAX_FAILED_LOGINS,
    FailedLogins,
    Passwords,
)

FAST = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)


def test_the_default_hash_is_argon2id_at_rfc_9106s_second_profile():
    hasher = PasswordHasher()
    assert (hasher.time_cost, hasher.memory_cost, hasher.parallelism) == (3, 65536, 4)
    assert hasher.hash("baseplate-2x4").startswith("$argon2id$v=19$m=65536,t=3,p=4$")


def test_a_password_matches_only_its_own_hash():
    passwords = Passwords(FAST)
    stored = passwords.hash("baseplate-2x4")
    assert passwords.verify(stored, "baseplate-2x4")
    assert not passwords.verify(stored, "baseplate-2x2")
    assert not passwords.verify("not a hash", "baseplate-2x4")


def test_no_account_never_matches():
    assert not Passwords(FAST).verify(None, "baseplate-2x4")


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_failures_age_out_of_the_window():
    clock = Clock()
    failed = FailedLogins(clock)
    for _ in range(MAX_FAILED_LOGINS):
        assert failed.retry_after("ana@example.com") is None
        failed.failed("ana@example.com")
        clock.now += 10
    # The first failure was 100 s ago, so it leaves the window in 800 s.
    assert failed.retry_after("ana@example.com") == FAILED_LOGIN_WINDOW_S - 100
    clock.now += FAILED_LOGIN_WINDOW_S - 100
    assert failed.retry_after("ana@example.com") is None


def test_success_clears_the_count():
    failed = FailedLogins(Clock())
    for _ in range(MAX_FAILED_LOGINS):
        failed.failed("ana@example.com")
    failed.succeeded("ana@example.com")
    assert failed.retry_after("ana@example.com") is None
