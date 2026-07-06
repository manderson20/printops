import pytest

from app.integrations.git_update import is_newer_version


@pytest.mark.parametrize(
    "candidate,baseline,expected",
    [
        ("1.1.0", "1.0.0", True),
        ("1.0.0", "1.0.0", False),
        ("0.7.0", "0.12.0", False),  # origin behind local — never "newer"
        ("0.12.0", "0.7.0", True),
        ("1.0.10", "1.0.9", True),  # numeric, not lexicographic, comparison
        ("1.0.9", "1.0.10", False),
        ("2.0.0", "1.9.9", True),
        ("1.2", "1.10", False),
    ],
)
def test_is_newer_version(candidate, baseline, expected):
    assert is_newer_version(candidate, baseline) is expected


def test_is_newer_version_falls_back_to_inequality_for_unparseable_versions():
    assert is_newer_version("abc", "abc") is False
    assert is_newer_version("abc", "def") is True
