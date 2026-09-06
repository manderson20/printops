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


class RateLimiter:
    """A sliding window of failures per key.

    Only failures are counted. A correct answer clears the key, so
    somebody who mistypes twice and then succeeds is not one attempt away
    from being locked out for the rest of the window.
    """

    def __init__(self, *, max_failures: int, window_seconds: float, message: str) -> None:
        self.max_failures = max_failures
        self.window_seconds = window_seconds
        self.message = message
        self._failures: dict[str, list[float]] = defaultdict(list)

    def check(self, key: str) -> None:
        """Raise 429 if this key has failed too often, too recently.

        Called *before* the credential is checked, so a locked-out caller
        is turned away without the server doing the expensive part —
        which for a password means a bcrypt verify, and is what makes
        repeated guessing a way to load the machine as well as a way to
        find the password.
        """
        now = time.monotonic()
        recent = [at for at in self._failures[key] if now - at < self.window_seconds]
        self._failures[key] = recent
        if len(recent) >= self.max_failures:
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=self.message)

    def record_failure(self, key: str) -> None:
        self._failures[key].append(time.monotonic())

    def record_success(self, key: str) -> None:
        self._failures.pop(key, None)

    def reset(self) -> None:
        """For tests, which would otherwise inherit a previous test's
        failures through the module-level instances."""
        self._failures.clear()


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
