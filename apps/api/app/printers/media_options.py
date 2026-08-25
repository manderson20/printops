"""Removing the page size from a job's CUPS options.

The same job options reach a printer by two different roads, and a printer
that cannot parse the nested media-col collection CUPS builds out of them
(see app/printers/media_col_probe.py) has to be protected on both:

- an ordinary job goes through infra/cups/backends/printops, which strips the
  options itself before handing them to the real `ipp` backend;
- a job that was held — over quota, for PIN release, or because the printer
  was switched off — is replayed later by app/printers/release.py through the
  internal release queue, which has **no** custom backend on it. Without this,
  a held job for an affected printer fails on release exactly as it would have
  failed on submission, and worse: `lp` accepts it locally, so the release API
  records the job as forwarded and deletes its spool file while the delivery
  is already doomed.

The backend script is standalone (stdlib only, no package imports, installed
to /usr/lib/cups/backend/printops and run by cupsd as root), so it carries its
own copy of this logic rather than importing this module. Two copies of one
rule is a liability, so tests/test_media_options_parity.py asserts the two
agree on every case either of them is tested with — change one and that test
tells you about the other.
"""

# Every spelling of "page size" cupsd hands us in a job's options: the IPP
# name, the PPD keyword, and the pieces CUPS' `ipp` backend assembles a
# media-col collection out of.
MEDIA_OPTION_KEYS = frozenset(
    {
        "media",
        "media-col",
        "media-size",
        "media-top-margin",
        "media-bottom-margin",
        "media-left-margin",
        "media-right-margin",
        "PageSize",
        "PageRegion",
    }
)


def split_job_options(job_options: str) -> list[str]:
    """Splits an options string into whole `key=value` tokens, keeping each
    one exactly as cupsd wrote it.

    Quoting and backslash escapes are honoured rather than split on, because a
    value may legitimately contain a space — and a value may legitimately
    contain the text of another option, so a plain regex over the whole string
    can chop a job's options in half at a `media=` that was never an option at
    all."""
    tokens: list[str] = []
    current = ""
    quote: str | None = None
    escaped = False
    for char in job_options:
        if escaped:
            current += char
            escaped = False
        elif char == "\\":
            current += char
            escaped = True
        elif quote:
            current += char
            if char == quote:
                quote = None
        elif char in "\"'":
            current += char
            quote = char
        elif char.isspace():
            if current:
                tokens.append(current)
                current = ""
        else:
            current += char
    if current:
        tokens.append(current)
    return tokens


def strip_media_options(job_options: str) -> str:
    """Removes the page size from a job's options, leaving every other option
    exactly as it was.

    Dropping the size is safe in a way it would not be on a raster path: what
    goes to the device is the document itself, whose own page size the printer
    reads. The IPP attribute is a hint the device is refusing to hear."""
    kept = [
        token
        for token in split_job_options(job_options)
        if token.split("=", 1)[0] not in MEDIA_OPTION_KEYS
    ]
    return " ".join(kept)
