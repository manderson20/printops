import re
import subprocess
from pathlib import Path

# apps/api/app/integrations/git_update.py -> repo root is 4 levels up.
REPO_ROOT = Path(__file__).resolve().parents[4]
VERSION_FILE = REPO_ROOT / "VERSION"


class GitUpdateError(Exception):
    pass


def _run_git(args: list[str], timeout: int = 30) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise GitUpdateError(f"`git {' '.join(args)}` timed out after {timeout}s") from exc
    if result.returncode != 0:
        raise GitUpdateError(
            f"`git {' '.join(args)}` failed: {(result.stderr or result.stdout).strip()[:300]}"
        )
    return result.stdout


def get_current_version() -> str:
    return VERSION_FILE.read_text().strip()


def get_latest_version() -> str:
    """Fetches origin/main (without merging into the working tree — this
    is a read-only check, the actual pull happens later in
    infra/update-watcher/apply-update.sh) and reads its VERSION file.

    This reuses the git credentials already configured on this host (the
    `gh` CLI's credential helper, since this server both pulls and pushes
    this same repo directly) rather than a separate GitHub API token —
    unlike ClassGuard, which runs containerized across multiple hosts and
    needs a token setting for its GitHub Contents API calls, PrintOps runs
    natively from a single git working tree that already has push/pull
    access."""
    _run_git(["fetch", "origin", "main", "--quiet"])
    return _run_git(["show", "origin/main:VERSION"]).strip()


def is_newer_version(candidate: str, baseline: str) -> bool:
    """True only if `candidate` is a strictly greater version than
    `baseline` — plain `!=` isn't enough, since origin/main can legitimately
    be *behind* the locally running working tree (e.g. commits made and
    deployed directly on this box, not yet pushed) and that must never be
    reported as "an update is available".

    Falls back to `candidate != baseline` for anything that doesn't parse
    as dotted integers (e.g. a hand-edited non-numeric VERSION file) —
    same conservative "don't block surfacing a real difference" spirit as
    get_changelog_section's own fallback below, just inverted: an
    unparseable version can't be proven newer, so it's treated as not an
    update rather than crashing the check."""

    def _parse(value: str) -> tuple[int, ...] | None:
        parts = value.strip().split(".")
        try:
            return tuple(int(p) for p in parts)
        except ValueError:
            return None

    candidate_parts = _parse(candidate)
    baseline_parts = _parse(baseline)
    if candidate_parts is None or baseline_parts is None:
        return candidate != baseline
    return candidate_parts > baseline_parts


def get_changelog_section(version: str) -> str | None:
    """Best-effort — a missing/malformed changelog section shouldn't block
    surfacing that an update is available."""
    try:
        changelog = _run_git(["show", "origin/main:CHANGELOG.md"])
    except GitUpdateError:
        return None
    escaped = re.escape(version)
    match = re.search(rf"## \[{escaped}\].*?(?=\n## \[|\Z)", changelog, re.DOTALL)
    return match.group(0).strip() if match else None


def has_uncommitted_changes() -> bool:
    return bool(_run_git(["status", "--porcelain"]).strip())
