"""Finding the document a job was printed from.

The CUPS spool cannot be listed. `/var/spool/cups` is mode 710 root:lp, so a
process in group `lp` may traverse into it and read a file whose exact name it
already knows, but `ls` and `find` are both denied. That is not an obstacle to
work around — it is the correct permission for a directory holding everybody's
documents — so the path is *derived* from the job's own CUPS id rather than
discovered.

CUPS names a job's data files `d{id:05d}-{document:03d}`. Almost every job has
one document; a client that sent several produces `-002` and onwards, and they
are measured together because they printed together.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

SPOOL = Path("/var/spool/cups")

# How far a spool file's timestamp may sit from the job PrintOps recorded
# before the pair is treated as coincidence rather than a match.
#
# CUPS job ids restart from 1 when the spool is cleared, so an id alone does
# not identify a job — d00042-001 may belong to today's job 42 or to one from
# two spools ago. Matching on the id and nothing else would eventually
# attribute one person's document to another, which is worse than not measuring
# it at all.
MATCH_WINDOW = timedelta(hours=6)


def document_paths(cups_job_id: int) -> list[Path]:
    """Every spool file for one CUPS job id, in document order.

    Existence is tested by stat rather than by listing the directory, which is
    the only option available and is also cheaper.
    """
    paths: list[Path] = []
    for document in range(1, 100):
        candidate = SPOOL / f"d{cups_job_id:05d}-{document:03d}"
        if not candidate.exists():
            break
        paths.append(candidate)
    return paths


def plausible_for(path: Path, job_created_at: datetime) -> bool:
    """Whether this file plausibly belongs to that job.

    A spool file written hours away from the job PrintOps recorded is a
    different job that happened to reuse the id. Refusing the match loses a
    measurement; accepting it silently attributes somebody's document — and its
    cost — to the wrong person.
    """
    try:
        modified = datetime.fromtimestamp(path.stat().st_mtime, tz=job_created_at.tzinfo)
    except OSError:
        return False
    return abs(modified - job_created_at) <= MATCH_WINDOW
