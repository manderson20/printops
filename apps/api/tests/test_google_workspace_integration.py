from app.integrations.google_workspace import extract_employee_id


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
