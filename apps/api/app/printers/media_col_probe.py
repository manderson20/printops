"""Finds out whether a printer can actually accept the `media-col` attribute
it says it supports.

CUPS' `ipp` backend builds a `media-col` collection — a *nested* one, with a
`media-size` collection inside it — for any job that carries a page size, and
it does so whenever the device advertises `media-col-supported`. The LCACTC
Graphic Arts Kyocera (ECOSYS P8060cdn) advertises it and then closes the
connection without answering when one arrives. CUPS reports that as
`Validate-Job: server-error-internal-error (Invalid argument)`, the backend
exits 4, and cupsd stops the queue — so a single Acrobat print (Acrobat always
sends a page size; Chrome often doesn't) takes the printer off the air for
everyone until someone runs `cupsenable` by hand.

Bisected against the live device on 2026-08-24, one attribute at a time:
`media-col` containing a nested `media-size` kills the connection; the same
`media-col` with only margins or only `media-source` is answered normally, as
is a plain `media` keyword and every other job attribute CUPS sends. So the
fault is specifically a nested collection, not page-size selection as such.

Auto-detected rather than configured, for the same reason the rest of
app/printers/capabilities.py is: an admin has no way to know what `media-col`
is, and a printer with this firmware bug would otherwise stop its queue for
hours before anyone connected the two. Detection also un-sets itself if
Kyocera ever ships a fix, which a checkbox would not.

`None` (couldn't tell) is deliberately distinct from `False` (answered fine),
exactly as with capabilities.py:_parse_airprint_supported: the caller acts on
this by dropping the page size from every job sent to the printer, and doing
that on the strength of one timed-out probe would quietly downgrade a healthy
printer.
"""

import logging
import re
import shutil
import subprocess
import tempfile

logger = logging.getLogger(__name__)

IPPTOOL_TIMEOUT_SECONDS = 12

# Validate-Job asks the device to check a job it will never receive: no
# document follows, no job is created, nothing is queued or printed. It is the
# operation IPP provides for exactly this question, and it is what CUPS' own
# backend sends first for every real job anyway — this probe is the same
# request the printer is already being asked hundreds of times a day.
_CONTROL_REQUEST = """{
    OPERATION Validate-Job
    GROUP operation-attributes-tag
    ATTR charset attributes-charset utf-8
    ATTR naturalLanguage attributes-natural-language en
    ATTR uri printer-uri $uri
    ATTR name requesting-user-name printops
    ATTR name job-name "PrintOps capability check"
    ATTR mimeMediaType document-format application/pdf
}
"""

# Identical to the control above plus the one attribute under test, shaped
# exactly as CUPS' `ipp` backend shapes it (US Letter in hundredths of a
# millimetre, with the PPD's margins).
_MEDIA_COL_REQUEST = """{
    OPERATION Validate-Job
    GROUP operation-attributes-tag
    ATTR charset attributes-charset utf-8
    ATTR naturalLanguage attributes-natural-language en
    ATTR uri printer-uri $uri
    ATTR name requesting-user-name printops
    ATTR name job-name "PrintOps capability check"
    ATTR mimeMediaType document-format application/pdf
    GROUP job-attributes-tag
    ATTR collection media-col {
      MEMBER collection media-size {
        MEMBER integer x-dimension 21590
        MEMBER integer y-dimension 27940
      }
      MEMBER integer media-bottom-margin 400
      MEMBER integer media-left-margin 400
      MEMBER integer media-right-margin 400
      MEMBER integer media-top-margin 400
    }
}
"""

_STATUS_CODE_RE = re.compile(r"^\s*status-code = (\S+)", re.MULTILINE)

# What ipptool prints when the connection closed with nothing on it. This, not
# the status code beside it, is the signal: ipptool reports a dropped
# connection as `server-error-internal-error`, the same code a device could
# legitimately answer with, and the two must not be confused — one is a printer
# talking and the other is a printer gone.
_NOTHING_CAME_BACK = "RECEIVED: 0 bytes in response"


def probe_uri(ip_address: str, port: int, tls: bool, ipp_path: str | None) -> str:
    scheme = "ipps" if tls else "ipp"
    return f"{scheme}://{ip_address}:{port}{ipp_path or '/ipp/print'}"


def _validate_job_status(uri: str, request: str) -> str | None:
    """Runs one Validate-Job and returns the status the device answered with
    ("successful" when ipptool's own check passed), or None if it did not
    answer at all — a dropped connection, a timeout, or ipptool failing to run.
    That difference is the whole signal here, so the two cases must not
    collapse into one return value."""
    with tempfile.NamedTemporaryFile("w", suffix=".test", delete=True) as handle:
        handle.write(request)
        handle.flush()
        try:
            result = subprocess.run(
                ["ipptool", "-t", uri, handle.name],
                capture_output=True,
                text=True,
                timeout=IPPTOOL_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
    if _NOTHING_CAME_BACK in result.stdout:
        return None
    if result.returncode == 0:
        # ipptool prints no status-code line for a request that simply worked.
        return "successful"
    match = _STATUS_CODE_RE.search(result.stdout)
    return match.group(1) if match else None


def detect_media_col_broken(uri: str) -> bool | None:
    """True when the device answers a plain Validate-Job but not the same one
    carrying a nested media-col; False when both are answered; None when the
    device wasn't in a state to tell us either way.

    The plain request runs first as a control, and again after a failure, so
    that a printer which simply went busy or offline mid-probe reads as
    "couldn't tell" rather than as broken. Only a device that answers, then
    doesn't, then answers again is making a statement about media-col."""
    if shutil.which("ipptool") is None:
        logger.warning("ipptool is not installed — cannot check media-col support for %s.", uri)
        return None

    control = _validate_job_status(uri, _CONTROL_REQUEST)
    if control is None or not control.startswith("successful"):
        return None

    if _validate_job_status(uri, _MEDIA_COL_REQUEST) is not None:
        # Answered — including with an IPP error status, which is a device
        # declining an attribute in the way the protocol provides for. CUPS
        # handles that by retrying without the attribute; it is not what stops
        # a queue, so it is not what this reports.
        return False

    recheck = _validate_job_status(uri, _CONTROL_REQUEST)
    if recheck is None or not recheck.startswith("successful"):
        return None

    logger.info("%s stops answering IPP when a job carries a page size (media-col).", uri)
    return True
