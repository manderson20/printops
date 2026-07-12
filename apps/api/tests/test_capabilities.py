from enum import IntEnum

from app.printers.capabilities import parse_capabilities, sanitize_raw_attributes


class FakeFinishing(IntEnum):
    NONE = 3
    STAPLE = 4
    PUNCH = 5
    SADDLE_STITCH = 8


def test_full_featured_mfp():
    raw = {
        "printer-make-and-model": "Acme MegaPrint 9000",
        "printer-firmware-string-version": "1.2.3",
        "sides-supported": ["one-sided", "two-sided-long-edge", "two-sided-short-edge"],
        "color-supported": True,
        "copies-supported": [1, 999],
        "printer-resolution-supported": [(600, 600, 3), (300, 300, 3)],
        "media-supported": ["na_letter_8.5x11in", "na_legal_8.5x14in"],
        "media-source-supported": ["tray-1", "tray-2", "bypass"],
        "media-type-supported": ["stationery", "cardstock"],
        "output-bin-supported": ["face-up", "face-down"],
        "finishings-supported": [
            FakeFinishing.NONE,
            FakeFinishing.STAPLE,
            FakeFinishing.PUNCH,
            FakeFinishing.SADDLE_STITCH,
        ],
        "job-password-supported": 4,
        "job-account-id-supported": True,
        "multiple-document-handling-supported": [
            "separate-documents-uncollated-copies",
            "separate-documents-collated-copies",
        ],
    }

    caps = parse_capabilities(raw)

    assert caps["make_model"] == "Acme MegaPrint 9000"
    assert caps["firmware_version"] == "1.2.3"
    assert caps["duplex_supported"] is True
    assert caps["color_supported"] is True
    assert caps["copies_max"] == 999
    assert caps["resolutions"] == [
        {"x": 600, "y": 600, "unit": 3},
        {"x": 300, "y": 300, "unit": 3},
    ]
    assert caps["media_sizes"] == ["na_letter_8.5x11in", "na_legal_8.5x14in"]
    assert caps["output_bins"] == ["face-up", "face-down"]
    assert sorted(caps["finishings"]) == ["punch", "saddle-stitch", "staple"]
    assert caps["collation_supported"] is True
    assert caps["pin_printing_supported"] is True
    assert caps["accounting_supported"] is True


def test_minimal_raw_queue_reports_no_capabilities():
    # Mirrors what a CUPS "raw" queue (no driver) actually reports: most
    # capability attributes are simply absent, and a single-valued attribute
    # like finishings-supported comes back as a bare enum, not a list.
    raw = {
        "printer-make-and-model": "Local Raw Printer",
        "copies-supported": 9999,
        "finishings-supported": FakeFinishing.NONE,
        "multiple-document-handling-supported": [
            "separate-documents-uncollated-copies",
            "separate-documents-collated-copies",
        ],
    }

    caps = parse_capabilities(raw)

    assert caps["make_model"] == "Local Raw Printer"
    assert caps["duplex_supported"] is False
    assert caps["color_supported"] is False
    assert caps["copies_max"] == 9999
    assert caps["finishings"] == []  # "none" is filtered out, not a real finishing
    assert caps["collation_supported"] is True
    assert caps["pin_printing_supported"] is False
    assert caps["accounting_supported"] is False


def test_unknown_finishing_code_gets_generic_label():
    raw = {"finishings-supported": [4, 9999]}
    caps = parse_capabilities(raw)
    assert caps["finishings"] == ["staple", "finishing-9999"]


def test_empty_raw_dict_does_not_crash():
    caps = parse_capabilities({})
    assert caps["duplex_supported"] is False
    assert caps["finishings"] == []
    assert caps["copies_max"] is None


def test_multi_value_firmware_version_is_joined_not_left_as_a_list():
    """Confirmed live against a real Kyocera ECOSYS P8060cdn: it reports
    printer-firmware-string-version as a 1setOf with one entry per
    firmware component (engine, network card, fax...) instead of a
    single string — this crashed PrinterOut's response validation
    (firmware_version expects a plain string) for every printer, not
    just this one, since the endpoint returns a list."""
    raw = {"printer-firmware-string-version": ["2.0", "1.02", "1.7", "1.5", "MXTSN.240.246"]}
    caps = parse_capabilities(raw)
    assert caps["firmware_version"] == "2.0, 1.02, 1.7, 1.5, MXTSN.240.246"


def test_single_value_firmware_version_unaffected():
    raw = {"printer-firmware-string-version": "1.2.3"}
    caps = parse_capabilities(raw)
    assert caps["firmware_version"] == "1.2.3"


def test_missing_firmware_version_is_none():
    caps = parse_capabilities({})
    assert caps["firmware_version"] is None


def test_media_default_and_trays_parsed_from_full_mfp():
    raw = {
        "media-default": "na_letter_8.5x11in",
        "media-col-ready": [
            {
                "media-size": {"x-dimension": 21590, "y-dimension": 27940},
                "media-source": "tray-1",
                "media-type": "stationery",
            },
            {
                "media-size": {"x-dimension": 21590, "y-dimension": 35560},
                "media-source": "tray-2",
                "media-type": "stationery",
            },
        ],
    }
    caps = parse_capabilities(raw)

    assert caps["default_media_size"] == "na_letter_8.5x11in"
    assert caps["media_trays"] == [
        {"source": "tray-1", "type": "stationery", "width_in": 8.5, "height_in": 11.0},
        {"source": "tray-2", "type": "stationery", "width_in": 8.5, "height_in": 14.0},
    ]


def test_media_trays_missing_dimensions_still_included_by_source():
    raw = {
        "media-col-ready": [
            {"media-source": "manual"},
        ]
    }
    caps = parse_capabilities(raw)
    assert caps["media_trays"] == [
        {"source": "manual", "type": None, "width_in": None, "height_in": None}
    ]


def test_media_trays_entry_with_neither_source_nor_size_is_dropped():
    raw = {"media-col-ready": [{"media-type": "stationery"}]}
    caps = parse_capabilities(raw)
    assert caps["media_trays"] == []


def test_media_default_and_trays_absent_when_not_reported():
    caps = parse_capabilities({})
    assert caps["default_media_size"] is None
    assert caps["media_trays"] == []


def test_sanitize_raw_attributes_handles_enums_and_datetimes():
    import datetime

    raw = {
        "finishings-supported": [FakeFinishing.STAPLE],
        "printer-current-time": datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
        "nested": {"a": FakeFinishing.PUNCH},
    }
    safe = sanitize_raw_attributes(raw)

    import json

    json.dumps(safe)  # must not raise
    assert safe["finishings-supported"] == [4]
    assert safe["printer-current-time"] == "2026-01-01T00:00:00+00:00"
    assert safe["nested"] == {"a": 5}
