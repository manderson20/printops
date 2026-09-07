"""No organisation's vocabulary compiled into the product.

PrintOps is installed by schools, districts, businesses and libraries. Twice
now a school's words have been baked into code that everybody runs: first the
`PERIODS` tuple with "semester" in it, then "Together this school year" on the
district page — which rendered that phrase to every reader regardless of what
their organisation calls a year.

Both were invisible here because they were *correct* for the first deployment.
That is what makes this worth a test rather than a review habit: the failure
looks like working software right up until somebody else installs it.

Scope is deliberately narrow — strings that reach a user. Comments naming a
concrete school are provenance and stay; so do the opt-in templates on the
settings page, whose whole purpose is to offer a school calendar *among*
others.
"""

import re
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[2] / "web" / "src"

# Where a school's vocabulary legitimately appears in user-visible text.
#
# A directory rather than a list of files. The first version named one file
# exactly, and moving that file to its own settings page turned its own
# starting templates into a violation — a guard that breaks when code moves
# teaches people to widen it in a hurry, which is how a guard stops guarding.
ALLOWED_DIRS = (
    # The reporting-period editor. Its templates say "Two semesters", "School
    # year", "Fall Semester", and its help text explains that a school year and
    # a fiscal year are the same object. Offering a school calendar *alongside*
    # quarters and trimesters, and saying so, is the opposite of hardwiring one.
    "app/(dashboard)/settings/reporting-periods/",
)

# Not part of a longer identifier: `spring_semester_start_month` is the name of
# a field on a deprecated API type, not a word anybody reads on screen. The
# boundary excludes `_` on purpose, which is what separates the two cases.
SCHOOL_WORDS = re.compile(r"(?<![A-Za-z_])(semester|school year)(?![A-Za-z_])", re.IGNORECASE)

# A line that is entirely a comment. Provenance in comments is worth keeping —
# it records why the code is shaped as it is.
COMMENT = re.compile(r"^\s*(//|/\*|\*|\*/)")


def _user_visible_lines(path: Path):
    for number, line in enumerate(path.read_text().splitlines(), start=1):
        if COMMENT.match(line):
            continue
        if SCHOOL_WORDS.search(line):
            yield number, line.strip()


@pytest.mark.skipif(not WEB.exists(), reason="web sources not present")
def test_no_school_vocabulary_reaches_a_user():
    offenders = []
    for path in sorted(WEB.rglob("*.ts*")):
        relative = str(path.relative_to(WEB))
        if any(relative.startswith(allowed) for allowed in ALLOWED_DIRS):
            continue
        for number, line in _user_visible_lines(path):
            offenders.append(f"{relative}:{number}: {line}")

    assert not offenders, (
        "A school's vocabulary is compiled into code every installation runs. "
        "Period names come from the organisation's reporting calendar — see "
        "periodNoun() and usePeriodOptions() in insights/explained-ui.tsx.\n  "
        + "\n  ".join(offenders)
    )
