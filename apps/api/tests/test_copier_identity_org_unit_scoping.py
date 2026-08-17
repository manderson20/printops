"""The OU filter that decides who counts as trackable staff for copier
accounting.

Regression cover for a real bug: the copier PIN roster export filtered by
staff_org_unit_path while the auto-created StaffCopierIdentity rows
filtered by nothing at all, so every student with an Employee ID set was
registered as a staff copier identity (2054 of 2325 rows in one district —
1918 students plus 136 inactive employees). Anything reading that roster to
provision device accounts would have pushed students onto staff copiers.
"""

import pytest

from app.integrations.google_workspace import (
    org_unit_included,
    org_unit_matches,
    resolve_copier_identity_org_units,
)
from app.models.google_workspace import GoogleWorkspaceSettings


def _settings(**kwargs) -> GoogleWorkspaceSettings:
    return GoogleWorkspaceSettings(**kwargs)


class TestResolveOrgUnits:
    def test_falls_back_to_staff_org_unit_path(self):
        """An org that only ever configured staff_org_unit_path keeps
        working without touching the new fields."""
        includes, excludes = resolve_copier_identity_org_units(
            _settings(staff_org_unit_path="/Employees")
        )
        assert includes == ["/Employees"]
        assert excludes == []

    def test_explicit_includes_win_over_the_fallback(self):
        includes, _ = resolve_copier_identity_org_units(
            _settings(
                staff_org_unit_path="/Employees",
                copier_identity_org_unit_paths=["/Employees/High School"],
            )
        )
        assert includes == ["/Employees/High School"]

    def test_nothing_configured_means_no_filter(self):
        assert resolve_copier_identity_org_units(_settings()) == ([], [])

    def test_blank_entries_are_dropped(self):
        includes, excludes = resolve_copier_identity_org_units(
            _settings(
                copier_identity_org_unit_paths=["/Employees", "", None],
                copier_identity_excluded_org_unit_paths=[""],
            )
        )
        assert includes == ["/Employees"]
        assert excludes == []


class TestOrgUnitIncluded:
    def test_student_with_an_employee_id_is_excluded(self):
        """The actual bug: students do have Employee IDs set."""
        assert not org_unit_included("/Students/High School/10th Grade", ["/Employees"], [])

    def test_staff_and_nested_staff_ous_are_included(self):
        assert org_unit_included("/Employees", ["/Employees"], [])
        assert org_unit_included("/Employees/Elementary School", ["/Employees"], [])

    def test_exclude_beats_include_for_a_nested_ou(self):
        """Inactive Employees sits *under* the staff OU, so an include-only
        filter structurally cannot remove it."""
        assert not org_unit_included(
            "/Employees/Inactive Employees",
            ["/Employees"],
            ["/Employees/Inactive Employees"],
        )

    def test_exclude_applies_to_nested_children_too(self):
        assert not org_unit_included(
            "/Employees/Inactive Employees/Retired",
            ["/Employees"],
            ["/Employees/Inactive Employees"],
        )

    def test_sibling_ou_is_not_caught_by_a_prefix(self):
        """ "/Employees/Inactive Employees" must not swallow
        "/Employees/Inactive Employees Archive"-style siblings by string
        prefix — matching is path-segment aware."""
        assert org_unit_included(
            "/Employees/Active Employees", ["/Employees"], ["/Employees/Inact"]
        )

    def test_excludes_apply_even_with_no_include_filter(self):
        """So an admin can strip one sub-OU without enumerating every other
        OU that should stay."""
        excludes = ["/Employees/Inactive Employees"]
        assert not org_unit_included("/Employees/Inactive Employees", [], excludes)
        assert org_unit_included("/Employees/High School", [], excludes)

    def test_no_filter_includes_everyone(self):
        assert org_unit_included("/Students/Whatever", [], [])

    def test_user_with_no_org_unit_is_excluded_when_a_filter_is_set(self):
        assert not org_unit_included(None, ["/Employees"], [])

    @pytest.mark.parametrize(
        "path,expected",
        [
            ("/Employees/Elementary School", True),
            ("/Employees/School Nurse", True),
            ("/Employees/Substitute Teachers", True),
            ("/Employees/Inactive Employees", False),
            ("/Students/Elementary School/3rd Grade", False),
            ("/Students/Early Childhood", False),
        ],
    )
    def test_real_district_ou_layout(self, path, expected):
        """The OU layout this was actually found against."""
        includes, excludes = resolve_copier_identity_org_units(
            _settings(
                staff_org_unit_path="/Employees",
                copier_identity_excluded_org_unit_paths=["/Employees/Inactive Employees"],
            )
        )
        assert org_unit_included(path, includes, excludes) is expected


class TestRootOrgUnit:
    """ "/" is the whole directory. It normalizes to "/", so a naive nested
    check compares against "//" and matches nothing but the bare root — an
    install with staff_org_unit_path="/" would silently lose every user in
    a child OU on the next sync."""

    def test_root_matches_nested_org_units(self):
        assert org_unit_matches("/Employees", "/")
        assert org_unit_matches("/Employees/Elementary School", "/")
        assert org_unit_matches("/Students/3rd Grade", "/")

    def test_root_as_the_configured_staff_ou_includes_everyone(self):
        includes, excludes = resolve_copier_identity_org_units(_settings(staff_org_unit_path="/"))
        assert includes == ["/"]
        assert org_unit_included("/Employees/High School", includes, excludes)
        assert org_unit_included("/Students/3rd Grade", includes, excludes)

    def test_excludes_still_apply_under_a_root_include(self):
        includes, excludes = resolve_copier_identity_org_units(
            _settings(
                staff_org_unit_path="/",
                copier_identity_excluded_org_unit_paths=["/Students"],
            )
        )
        assert org_unit_included("/Employees/High School", includes, excludes)
        assert not org_unit_included("/Students/3rd Grade", includes, excludes)
