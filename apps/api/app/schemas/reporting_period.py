"""What an admin can say about their organisation's year.

The validation here is not ceremony. An earlier calendar setting accepted a
month and a day as independent bounded integers, which let somebody save
31 February and turned every Insights screen into a 500 the moment anything
tried to build a date out of it. Month and day are only meaningful together,
so they are validated together.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.reporting_period import LABEL_STYLES

# A non-leap year on purpose. 29 February is not a date most years have, and a
# term that starts on it would fail to resolve in three years out of four —
# better refused at the point somebody types it than three years later.
_VALIDATION_YEAR = 2001


def _check_day_exists(month: int, day: int, what: str) -> None:
    try:
        date(_VALIDATION_YEAR, month, day)
    except ValueError:
        raise ValueError(
            f"{what}: {day} is not a day in month {month}"
            + (" in most years" if (month, day) == (2, 29) else "")
        ) from None


class ReportingTermIn(BaseModel):
    """One term of the repeating pattern."""

    name: str = Field(min_length=1, max_length=60)
    start_month: int = Field(ge=1, le=12)
    start_day: int = Field(ge=1, le=31)

    @model_validator(mode="after")
    def _day_is_real(self) -> ReportingTermIn:
        _check_day_exists(self.start_month, self.start_day, self.name)
        return self


class ReportingTermOut(ReportingTermIn):
    position: int


class ReportingCalendarIn(BaseModel):
    """The organisation's year, and the terms it divides into.

    Terms arrive as a whole list rather than one at a time. The editor shows a
    year at once and an admin thinks about it that way — and per-term edits
    would make it easy to leave a year with two Q2s or a gap where a term used
    to be, which is exactly the half-configured state the resolver cannot
    report on sensibly.
    """

    year_start_month: int = Field(ge=1, le=12)
    year_start_day: int = Field(ge=1, le=31)
    year_noun: str = Field(min_length=1, max_length=40)
    label_style: str = "auto"
    terms: list[ReportingTermIn] = Field(default_factory=list, max_length=24)

    @field_validator("label_style")
    @classmethod
    def _known_style(cls, value: str) -> str:
        if value not in LABEL_STYLES:
            raise ValueError(f"label_style must be one of {', '.join(LABEL_STYLES)}")
        return value

    @model_validator(mode="after")
    def _consistent(self) -> ReportingCalendarIn:
        _check_day_exists(self.year_start_month, self.year_start_day, "Year start")

        names = [term.name.strip().casefold() for term in self.terms]
        if len(set(names)) != len(names):
            raise ValueError("two terms cannot share a name")

        starts = [(term.start_month, term.start_day) for term in self.terms]
        if len(set(starts)) != len(starts):
            raise ValueError("two terms cannot start on the same day")

        return self


class ResolvedPeriodOut(BaseModel):
    key: str
    label: str
    start: date
    end: date
    kind: str


class ReportingCalendarOut(BaseModel):
    year_start_month: int
    year_start_day: int
    year_noun: str
    label_style: str
    terms: list[ReportingTermOut]
    # What the current settings actually produce, so the editor can show the
    # admin the year they have just described rather than asking them to
    # imagine it. A calendar that reads correctly and resolves wrongly is the
    # failure mode worth spending a round trip to prevent.
    preview: list[ResolvedPeriodOut]


class ReportingYearOut(BaseModel):
    """One reporting year the calendar produces, with its segments.

    Generated from the pattern rather than stored, which is the point: no year
    needs data entered for it, and a year from before PrintOps was installed
    still has segments to report against.
    """

    year: int
    key: str
    label: str
    start: date
    end: date
    segments: list[ResolvedPeriodOut]
    # True when this year's dates were stated explicitly rather than generated
    # — the year a term really did start late.
    overridden: bool
    # False for a year that ended before this installation collected anything.
    # Offered anyway, but marked, because an empty report reads as "nobody
    # printed" rather than "we were not watching yet".
    has_data: bool


class ReportingYearOverrideIn(BaseModel):
    """Explicit dates for one year's segments.

    A whole year at a time, never one segment: a year that is half generated
    and half stated has gaps and overlaps that depend on which rows happen to
    exist, and nobody can reason about it afterwards.
    """

    segments: list[ReportingSegmentOverrideIn] = Field(min_length=1, max_length=24)

    @model_validator(mode="after")
    def _ordered_and_disjoint(self) -> ReportingYearOverrideIn:
        ordered = sorted(self.segments, key=lambda segment: segment.start_date)
        for earlier, later in zip(ordered, ordered[1:]):
            if earlier.end_date >= later.start_date:
                raise ValueError(
                    f"{earlier.name} ends on or after {later.name} begins; segments cannot overlap"
                )
        return self


class ReportingSegmentOverrideIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    start_date: date
    # Inclusive: the last day the segment covers, which is how a calendar is
    # published. Converted to an exclusive bound once, at resolution.
    end_date: date
    # Ties this segment to its counterpart a year earlier, which is what makes
    # "the same segment last year" answerable.
    position: int | None = None

    @model_validator(mode="after")
    def _ends_after_it_starts(self) -> ReportingSegmentOverrideIn:
        if self.end_date < self.start_date:
            raise ValueError(f"{self.name}: ends before it starts")
        return self
