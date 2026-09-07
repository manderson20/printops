"""Whether CUPS is keeping documents long enough to measure them.

Coverage depends on a spool file surviving until the loop reaches it. CUPS
deletes a job's data as soon as it prints unless `PreserveJobFiles` says
otherwise, and its default is off — so an installation that never set it gets
no measurements at all, and gets them as a silent absence rather than an error.

This deployment happened to have retention configured already, which is exactly
why it needed checking: a feature that works on the machine it was written on
and nowhere else looks finished.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

CUPSD_CONF = Path("/etc/cups/cupsd.conf")

# Below this there is no point running the loop: the settle window alone is
# five minutes, and a job printed just before a restart needs longer still.
MINIMUM_SECONDS = 3600

_DIRECTIVE = re.compile(r"^\s*PreserveJobFiles\s+(\S+)", re.IGNORECASE | re.MULTILINE)


def configured_retention_seconds(conf: Path = CUPSD_CONF) -> int | None:
    """How long CUPS keeps job data, in seconds.

    Returns 0 for an explicit "No", and None when the file cannot be read —
    which is not the same as "not configured" and should not be reported as if
    it were.
    """
    try:
        text = conf.read_text()
    except OSError:
        return None

    match = _DIRECTIVE.search(text)
    if match is None:
        # Absent means CUPS's own default, which is off.
        return 0

    value = match.group(1).strip().lower()
    if value in {"no", "off", "false"}:
        return 0
    if value in {"yes", "on", "true"}:
        # Kept indefinitely.
        return 10**9
    try:
        return int(value)
    except ValueError:
        return None


def warn_if_documents_are_not_retained(conf: Path = CUPSD_CONF) -> str | None:
    """Log a specific, actionable warning when measurement cannot work.

    Returns the message logged, or None when retention is adequate — so a
    health endpoint can report the same fact rather than restating the rule.
    """
    seconds = configured_retention_seconds(conf)

    if seconds is None:
        message = (
            f"Could not read {conf} to check PreserveJobFiles; ink coverage "
            "measurement may not work."
        )
    elif seconds == 0:
        message = (
            "CUPS is not keeping job documents (PreserveJobFiles is off), so ink "
            "coverage cannot be measured — every job will be recorded as expired. "
            "Set 'PreserveJobFiles 259200' in /etc/cups/cupsd.conf and restart "
            "CUPS to keep documents for three days."
        )
    elif seconds < MINIMUM_SECONDS:
        message = (
            f"CUPS keeps job documents for only {seconds}s, which is too short for "
            "ink coverage measurement to reach them. Raise PreserveJobFiles to at "
            f"least {MINIMUM_SECONDS}s in /etc/cups/cupsd.conf."
        )
    else:
        return None

    logger.warning(message)
    return message
