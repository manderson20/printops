"""What a person can ask for at submission time, and what the printer will take.

PrintOps already discovers each printer's finishing capabilities — Discovered
Capabilities on the printer's Overview tab has listed them since capability
detection was built — but nothing exposed them at the point a job is submitted.
Someone uploading through Print got whatever the queue's defaults happened to
be, with no way to ask for double-sided or a staple, on machines that support
both.

Two deliberate narrowings:

Only the options a printer actually reports are offered. Showing a staple
checkbox on a machine with no stapler produces a job that silently comes out
unstapled, which is the same class of problem as the colour default on a
monochrome printer (#94) — an option that does nothing is worse than no option,
because the user believes they used it.

And of the finishings IPP defines, only staple and punch are offered. The
devices here report up to fifteen, most of them positional variants
(staple-dual-left, punch-triple-top). A self-service upload page is not the
place to choose which corner; asking for a staple and letting the printer apply
its own default position is what somebody at a web form means.
"""

from typing import Any

# IPP finishing values (RFC 8011 §5.2.6), by the label capabilities.py stores.
# Only the two worth offering: see the module docstring.
OFFERABLE_FINISHINGS: dict[str, int] = {
    "staple": 4,
    "punch": 5,
}

# What `lp -o sides=` accepts, and what each means to a reader.
SIDES_CHOICES: dict[str, str] = {
    "one-sided": "One-sided",
    "two-sided-long-edge": "Double-sided",
    "two-sided-short-edge": "Double-sided, flipped on the short edge",
}

DEFAULT_SIDES = "one-sided"


def offered_finishings(capabilities: dict[str, Any] | None) -> list[str]:
    """The finishing options to show for a printer, in a stable order.

    A printer PrintOps has never successfully probed reports nothing, and gets
    nothing offered. That is the honest answer: the alternative is guessing, and
    a guess here becomes a job that quietly comes out wrong.
    """
    reported = set((capabilities or {}).get("finishings") or [])
    return [name for name in OFFERABLE_FINISHINGS if name in reported]


def supports_duplex(capabilities: dict[str, Any] | None) -> bool:
    return bool((capabilities or {}).get("duplex_supported"))


def lp_options(
    *, sides: str | None, finishings: list[str] | None, capabilities: dict[str, Any] | None
) -> list[str]:
    """The `-o` arguments for a submission, filtered to what the device supports.

    Filtered here rather than trusted from the request. The browser only shows
    what a printer offers, but the endpoint is reachable directly, and an
    unsupported finishing sent to a queue is at best ignored and at worst
    rejects the job — either way the person is told their document printed.
    """
    options: list[str] = []

    if sides and sides != DEFAULT_SIDES and sides in SIDES_CHOICES:
        if supports_duplex(capabilities):
            options += ["-o", f"sides={sides}"]

    allowed = set(offered_finishings(capabilities))
    codes = sorted(OFFERABLE_FINISHINGS[name] for name in (finishings or []) if name in allowed)
    for code in codes:
        # Repeated rather than comma-joined: CUPS accepts a 1setOf here, and one
        # flag per value is what its own tools emit.
        options += ["-o", f"finishings={code}"]

    return options
