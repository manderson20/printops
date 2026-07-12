"""Shared field validators for admin-supplied config values that end up
building outbound request URLs (integration base_url/customer_id fields).
These are trusted-but-not-infallible admin inputs — the real trust boundary
is the `admin` role check on the settings endpoints, not this validation —
but rejecting malformed values here still closes off a class of mistakes
(a pasted value with a stray path/query segment, embedded whitespace, or a
non-http(s) scheme) that would otherwise reach `httpx` unexamined, which is
what CodeQL's py/partial-ssrf check flags on these integrations."""

from urllib.parse import urlsplit

_ALLOWED_SCHEMES = {"http", "https"}


def validate_base_url(value: str | None) -> str | None:
    """Requires a well-formed absolute http(s) URL with no embedded
    whitespace, query string, or fragment — e.g. "https://classguard.
    example.org" or "https://businessapi.mosyle.com/v1" (a path is allowed;
    several of these vendor APIs are versioned under one, e.g. Mosyle's
    /v2). Returns None/blank unchanged — these fields are all optional; a
    blank base_url means "not configured yet")."""
    if value is None or value == "":
        return value
    stripped = value.strip()
    if stripped != value or any(c.isspace() for c in stripped):
        raise ValueError("must not contain whitespace")
    parts = urlsplit(stripped)
    if parts.scheme not in _ALLOWED_SCHEMES:
        raise ValueError(f"must start with http:// or https:// (got {stripped!r})")
    if not parts.netloc:
        raise ValueError("must include a host, e.g. https://example.org")
    if parts.query or parts.fragment:
        raise ValueError(
            "must not include a ?query string or #fragment — a base host/path only"
        )
    return stripped.rstrip("/")


def validate_safe_identifier(value: str | None) -> str | None:
    """For short identifier-style fields (e.g. a Google Workspace customer
    ID) that get interpolated directly into a request path — restricts to
    letters, digits, underscore, and hyphen so nothing here can smuggle a
    path segment ("/../"), query string, or other URL-structural
    character into the request."""
    if value is None or value == "":
        return value
    if not all(c.isalnum() or c in "_-" for c in value):
        raise ValueError(
            "must contain only letters, digits, underscores, and hyphens"
        )
    return value
