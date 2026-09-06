"""Counting recent failures, so guessing costs something.

Every credential this server accepts from outside is a short secret that
somebody could try repeatedly: the kiosk's PIN, and the break-glass admin
password. This host answers on the public internet — Let's Encrypt's
validation servers reach it, and Caddy's log carries requests from
addresses outside the district — so "nobody can get to it" is not a
control.

In-process and in-memory, which is right for the shape of this
deployment: one uvicorn worker, one machine. A restart forgets the
failures, and that is an acceptable trade for having no Redis in the
dependency list — an attacker who could restart the API already has more
than the login form gives them.
"""

import time
from collections import defaultdict

from fastapi import HTTPException, status

# How often the whole table is swept for keys whose failures have all
# aged out. Without this a key is only ever cleaned up when the same
# caller comes back — and the caller who never comes back is precisely
# the one a scanner produces, a fresh address each time.
SWEEP_INTERVAL_SECONDS = 60.0


class RateLimiter:
    """A sliding window of failures per key.

    Only failures are counted. A correct answer clears the key, so
    somebody who mistypes twice and then succeeds is not one attempt away
    from being locked out for the rest of the window.

    Keys are attacker-chosen — a source address on a public endpoint — so
    the table has to be able to shrink. It is swept on a timer rather than
    on every call: a sweep is O(keys) and a login is not, and once a
    minute is far more often than a table of this size needs.
    """

    def __init__(self, *, max_failures: int, window_seconds: float, message: str) -> None:
        self.max_failures = max_failures
        self.window_seconds = window_seconds
        self.message = message
        self._failures: dict[str, list[float]] = defaultdict(list)
        self._last_sweep = time.monotonic()

    def check(self, key: str) -> None:
        """Raise 429 if this key has failed too often, too recently.

        Called *before* the credential is checked, so a locked-out caller
        is turned away without the server doing the expensive part —
        which for a password means a bcrypt verify, and is what makes
        repeated guessing a way to load the machine as well as a way to
        find the password.
        """
        now = time.monotonic()
        self._sweep(now)

        recent = [at for at in self._failures[key] if now - at < self.window_seconds]
        if recent:
            self._failures[key] = recent
        else:
            # Nothing recent left, so the key is not evidence of anything.
            # Discarded rather than kept as an empty list — a defaultdict
            # hands one out on every read, and one scan per address would
            # otherwise leave a permanent row apiece.
            self._failures.pop(key, None)

        if len(recent) >= self.max_failures:
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=self.message)

    def _sweep(self, now: float) -> None:
        """Drop every key whose failures have all aged out."""
        if now - self._last_sweep < SWEEP_INTERVAL_SECONDS:
            return
        self._last_sweep = now
        cutoff = now - self.window_seconds
        for key in [k for k, ats in self._failures.items() if not any(at > cutoff for at in ats)]:
            del self._failures[key]

    def record_failure(self, key: str) -> None:
        self._failures[key].append(time.monotonic())

    def record_success(self, key: str) -> None:
        self._failures.pop(key, None)

    def reset(self) -> None:
        """For tests, which would otherwise inherit a previous test's
        failures through the module-level instances."""
        self._failures.clear()
        self._last_sweep = time.monotonic()

    @property
    def tracked_keys(self) -> int:
        """How many callers are currently being remembered. Exists so a
        test can assert the table shrinks."""
        return len(self._failures)


def client_key(request) -> str:
    """Who to hold responsible for a failed attempt.

    The API listens on 127.0.0.1 only, so every request arrives from
    Caddy and `request.client.host` is always the loopback address —
    which would put the whole district in one bucket and let one wrong
    password lock everybody out. The first entry of X-Forwarded-For is
    the original client, and it can be trusted precisely because nothing
    but the local proxy can reach this socket to forge it.

    Falls back to the socket address when the header is absent, which is
    the case in tests and for anything talking to the API directly on the
    box.
    """
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
