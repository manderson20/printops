import pytest

from app.integrations.google_workspace import (
    extract_employee_id,
    normalize_org_unit_path,
    org_unit_matches,
)


def test_extract_employee_id_present():
    user = {"externalIds": [{"type": "organization", "value": "10023"}]}
    assert extract_employee_id(user) == "10023"


def test_extract_employee_id_missing_external_ids():
    assert extract_employee_id({}) is None


def test_extract_employee_id_empty_external_ids():
    assert extract_employee_id({"externalIds": []}) is None


def test_extract_employee_id_wrong_type_only():
    user = {"externalIds": [{"type": "custom", "customType": "badge", "value": "X-1"}]}
    assert extract_employee_id(user) is None


def test_extract_employee_id_picks_organization_among_multiple():
    user = {
        "externalIds": [
            {"type": "custom", "customType": "badge", "value": "X-1"},
            {"type": "organization", "value": "10023"},
        ]
    }
    assert extract_employee_id(user) == "10023"


def test_extract_employee_id_ignores_blank_value():
    user = {"externalIds": [{"type": "organization", "value": ""}]}
    assert extract_employee_id(user) is None


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("/Employees", "/Employees"),
        ("/Employees/", "/Employees"),
        (" /Employees ", "/Employees"),
        ("Employees", "/Employees"),
        ("/", "/"),
    ],
)
def test_normalize_org_unit_path(raw, expected):
    assert normalize_org_unit_path(raw) == expected


def test_org_unit_matches_exact():
    assert org_unit_matches("/Employees", "/Employees") is True


def test_org_unit_matches_nested_sub_ou():
    assert org_unit_matches("/Employees/Teachers", "/Employees") is True


def test_org_unit_matches_rejects_similarly_named_ou():
    # A naive prefix/LIKE match would incorrectly treat this as nested.
    assert org_unit_matches("/EmployeesOld", "/Employees") is False


def test_org_unit_matches_rejects_unrelated_ou():
    assert org_unit_matches("/Students", "/Employees") is False


def test_org_unit_matches_none_path():
    assert org_unit_matches(None, "/Employees") is False


def test_org_unit_matches_tolerates_trailing_slash_on_setting():
    assert org_unit_matches("/Employees/Teachers", "/Employees/") is True
