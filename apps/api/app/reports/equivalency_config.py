"""Every constant behind "Your Printing, Explained" — the equivalency
figures, the milestone ladders, and the period definitions.

Deliberately one module, and deliberately plain frozen dataclasses rather
than another settings table. These are not admin knobs: a district does
not tune how thick a sheet of paper is, and a value that drifted between
two deploys would silently change every historical fun fact on the lobby
screen. The numbers that *are* local policy — cost per page, cost per
sheet — stay in ReportFormulaSettings where an admin can already edit
them in Settings without a deploy (app/models/report.py).

Every figure carries the basis it came from. Where a figure is a rough
public estimate rather than a measured local value, the comment says so:
these drive friendly comparisons, not billing, and a reader who wants to
know whether "14 trees" is precise deserves to find the answer here.

Ladders are ordered smallest first and are sorted defensively at use
(app/reports/equivalency.py) — a rung inserted out of order should be a
harmless edit, not a silently wrong milestone.
"""

from dataclasses import dataclass

# --- unit conversions -------------------------------------------------
# Exact by definition, so no citation needed beyond the definitions.
INCHES_PER_FOOT = 12.0
FEET_PER_MILE = 5280.0
MM_PER_FOOT = 304.8

# --- paper -------------------------------------------------------------

# US Letter long edge. Pages are laid end to end along their 11" side —
# the orientation that makes the "laid end to end" image literal.
PAGE_LENGTH_IN = 11.0

# 20 lb bond office paper: a 500-sheet ream stands about 2 inches, giving
# ~0.1 mm per sheet. Rounded to one figure on purpose — the stack facts
# are illustrative, and false precision here would imply a measurement
# nobody took.
SHEET_THICKNESS_MM = 0.1

# A 500-sheet ream of 20 lb bond weighs ~5 lb, i.e. ~1 lb per 100 sheets.
LB_PER_100_SHEETS = 1.0

SHEETS_PER_REAM = 500
REAMS_PER_CASE = 10

# One ton of virgin uncoated office paper takes 24 trees, at 40 ft tall
# and 6-8 inches in diameter, through the kraft process. A ton is about
# 400 reams — 200,000 sheets — so 200,000 / 24 = 8,333 sheets a tree.
#
# Derivable rather than remembered, which matters: this is the number the
# tree illustration is built on, and a figure a reader can check is worth
# more than one they have to believe. It also now matches
# ReportFormulaSettings.sheets_per_tree, which had 8333.0 while this
# carried 8300.0 — two constants for one quantity, differing by a
# rounding nobody had chosen.
#
# Conservatree, who publish the 24-trees figure, warn that the older "17
# trees per ton" number everybody quotes was calculated for *newsprint*
# in the 1970s and then applied to office paper for decades. Tree size
# swamps everything else regardless: Harvard's BioNumbers puts one large
# pine at 119,100 sheets, fourteen times this.
SHEETS_PER_TREE = 8333.0
SHEETS_PER_TREE_SOURCE = "https://conservatree.org/learn/EnviroIssues/TreeStats.shtml"

# Litres of *water footprint* per sheet — green plus blue, which counts
# the rain that fell on the trees, not water from a tap. The Water
# Footprint Network puts printing and writing paper at 300-2600 m3/ton,
# which they give as 2-13 litres for an A4 sheet; 10 is the upper-middle
# of that range.
#
# The distinction has to survive into the wording. Process water is
# nearer 0.1-0.4 litres a sheet, so a reader comparing "10 litres" to
# their kitchen tap is being misled by a factor of thirty — see
# app/reports/fun_facts.py, which says "counting the rain that grew the
# trees" for exactly this reason.
LITRES_PER_SHEET = 10.0
LITRES_PER_SHEET_SOURCE = "https://waterfootprint.org/resources/Report46-WaterFootprintPaper.pdf"

# Grams of CO2e per *sheet*, from the Environmental Paper Network's Paper
# Calculator: 5,601 lb CO2e per ton of virgin copy paper, over 200,000
# sheets, is 12.7 g a sheet.
#
# Per sheet, not per page, and that is a correction rather than a
# refinement. This used to be 5 g per *page*, so a duplexed sheet was
# charged twice for carbon that one piece of paper emitted once — and
# double-siding, which the same page recommends, made no difference to
# the CO2 fact at all. The emission is in the paper.
CO2_G_PER_SHEET = 12.7
CO2_G_PER_SHEET_SOURCE = "https://environmentalpaper.org/wp-content/uploads/2017/08/Paperwork.pdf"

# ~400 g CO2 per mile for an average passenger vehicle — the divisor that
# turns a CO2 mass into the "miles driven" comparison people can picture.
G_PER_MILE_DRIVEN = 400.0

# --- local facts -------------------------------------------------------

# PLACEHOLDER — awaiting the real enrolment figure from the district.
# Drives "enough to hand every student N sheets", so a wrong value here
# is visible on every screen showing that fact.
STUDENT_COUNT = 930

# Gutenberg's press is conventionally described as producing around 250
# impressions a day. Used only for the "his press would have needed N
# days" comparison.
GUTENBERG_PAGES_PER_DAY = 250.0

# --- anonymity guard ---------------------------------------------------

# The district fun-facts view is visible to every signed-in user, so it
# must never become a channel for inferring one person's usage. Below
# this many distinct contributors in the period, the view refuses to
# render facts at all rather than showing a total two people could
# de-anonymize between them. Enforced server-side in the endpoint.
MIN_CONTRIBUTORS_FOR_DISTRICT_FACTS = 10

# --- milestone ladders -------------------------------------------------


@dataclass(frozen=True)
class Milestone:
    """One rung. `value` is in its ladder's `unit`, never a display unit —
    formatting (miles vs feet, say) is the caller's decision, because the
    same rung reads naturally at different scales on the personal and
    district views.

    `short` is the same rung named for the middle of a sentence, where
    the full name would repeat something already said: a journey rung
    reads as "Brookfield to Jefferson City" when it is the achievement,
    but "17% of the way to Brookfield to Jefferson City" is not English.
    Defaults to `name`, so a rung only carries one when the two differ.
    """

    name: str
    value: float
    short: str | None = None

    @property
    def label(self) -> str:
        """The rung named for use mid-sentence."""
        return self.short or self.name


@dataclass(frozen=True)
class Ladder:
    key: str
    unit: str
    rungs: tuple[Milestone, ...]


# Distance, in feet. Ordered by true value, which is not quite the order
# the feature brief listed: a football field's perimeter (1,040 ft) is
# shorter than a quarter-mile track lap (1,320 ft), so it comes first.
# Sorting by the listed order instead would have made the progress bar
# run backwards between those two rungs.
#
# City distances are road miles from Brookfield and are the figures most
# worth localizing — they are the ones a reader can check against their
# own drive.
DISTANCE_LADDER = Ladder(
    key="distance",
    unit="feet",
    rungs=(
        Milestone("a lap of the football field", 1_040.0),  # 360x160 ft perimeter
        # A quarter mile exactly (1,320 ft). A modern 400 m track is
        # 1,312 ft, seven feet short of that, but nobody calls a lap
        # "0.2485 miles" — the quarter mile is what the distance is known
        # as, so the ladder uses the figure that matches the name.
        Milestone(
            "a quarter-mile lap of the track",
            0.25 * FEET_PER_MILE,
            short="a quarter mile",
        ),
        Milestone("Brookfield to Marceline", 12.0 * FEET_PER_MILE, short="Marceline"),
        Milestone("Brookfield to Jefferson City", 120.0 * FEET_PER_MILE, short="Jefferson City"),
        Milestone("all the way across Missouri", 300.0 * FEET_PER_MILE, short="across Missouri"),
        Milestone("Brookfield to Chicago", 410.0 * FEET_PER_MILE, short="Chicago"),
        Milestone("coast to coast", 2_800.0 * FEET_PER_MILE),
        Milestone(
            "all the way around the Earth", 24_901.0 * FEET_PER_MILE, short="around the Earth"
        ),
        Milestone("all the way to the Moon", 238_900.0 * FEET_PER_MILE, short="the Moon"),
    ),
)

# Stack height, in feet.
STACK_LADDER = Ladder(
    key="stack_height",
    unit="feet",
    rungs=(
        Milestone("a ream of paper", 2.0 / INCHES_PER_FOOT),
        Milestone("a doorway", 80.0 / INCHES_PER_FOOT),
        Milestone("a basketball hoop", 10.0),
        Milestone("a giraffe", 18.0),
        Milestone("the Gateway Arch", 630.0),
        Milestone("the Empire State Building", 1_454.0),
        Milestone("a cruising airliner", 35_000.0),
        Milestone("the edge of space", 62.0 * FEET_PER_MILE),
    ),
)

# Weight, in pounds.
WEIGHT_LADDER = Ladder(
    key="weight",
    unit="pounds",
    rungs=(
        Milestone("a vending machine", 600.0),
        Milestone("a pickup truck", 5_000.0),
        Milestone("a school bus", 25_000.0),
        Milestone("a blue whale", 300_000.0),
    ),
)

LADDERS = (DISTANCE_LADDER, STACK_LADDER, WEIGHT_LADDER)

# --- skip-if-trivial ---------------------------------------------------

# A fact whose headline rounds to zero at the precision it displays is
# noise, not information ("You used 0.0 trees"), so it is dropped rather
# than shown. Same instinct app/reports/fun_facts.py already follows by
# returning None for a fact it cannot support.
MIN_MEANINGFUL_VALUE = 0.05

# A progress bar this close to empty reads as broken rather than
# encouraging, so the milestone is reported without one.
MIN_MEANINGFUL_PROGRESS = 0.01

# --- period definitions ------------------------------------------------

# The district's school year starts 1 July. PLACEHOLDER pending
# confirmation — "school year to date" is the default period on every
# view, so this decides the headline number everyone sees.
SCHOOL_YEAR_START_MONTH = 7
SCHOOL_YEAR_START_DAY = 1

# Fall semester runs from the school year start; spring begins 1 January.
# A two-semester year, so these two boundaries define both.
SPRING_SEMESTER_START_MONTH = 1
SPRING_SEMESTER_START_DAY = 1

# Weeks start Monday — a school week, not an ISO-arbitrary choice.
WEEK_STARTS_ON = 0  # date.weekday(): Monday == 0
