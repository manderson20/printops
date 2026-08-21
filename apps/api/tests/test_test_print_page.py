"""The test page is drawn server-side, so its clock doesn't come from the
reader's browser the way the rest of PrintOps' timestamps do — the zone is
passed in instead. See app/printers/test_print.py."""

from datetime import UTC

from app.printers.test_print import _resolve_timezone


def test_named_zone_resolves():
    assert _resolve_timezone("America/Chicago").key == "America/Chicago"


def test_missing_zone_falls_back_to_utc():
    assert _resolve_timezone(None) is UTC
    assert _resolve_timezone("") is UTC


def test_unrecognised_zone_falls_back_instead_of_raising():
    # A stale or hand-edited value must never cost someone their test print.
    assert _resolve_timezone("Mars/Olympus_Mons") is UTC
    assert _resolve_timezone("../../etc/passwd") is UTC


def test_central_prints_its_own_abbreviation_and_offset():
    """The point of the change: a Central admin should read "CDT"/"CST" and
    a wall clock that matches the one on their desk, not UTC."""
    from datetime import datetime

    tz = _resolve_timezone("America/Chicago")
    summer = datetime(2026, 8, 21, 12, 0, tzinfo=UTC).astimezone(tz)
    winter = datetime(2026, 1, 21, 12, 0, tzinfo=UTC).astimezone(tz)

    assert summer.strftime("%H:%M %Z") == "07:00 CDT"
    assert winter.strftime("%H:%M %Z") == "06:00 CST"


# --------------------------------------------------------------------------
# the sheet's content
# --------------------------------------------------------------------------


class _FakeCartridge:
    def __init__(self, color, percent):
        self.color = color
        self.current_level_percent = percent


class _FakePrinter:
    """Only the attributes build_page_info reads. A real Printer would drag
    the whole DB in; this keeps the flattening logic testable on its own."""

    def __init__(self, **overrides):
        defaults = dict(
            id="1df55b5a-33ea-4a18-aaaf-19b02a4d2cbe",
            name="Graphic Arts Kyocera",
            manufacturer="Kyocera",
            model="ECOSYS P8060cdn",
            serial_number="KM8B370F",
            ip_address="10.50.1.37",
            hostname=None,
            port=443,
            use_tls=True,
            ipp_path="/ipp/print",
            ipp_path_detected=None,
            building="LCACTC",
            room="Room 118",
            department="Graphic Arts",
            status="online",
            capabilities=None,
            capabilities_detected_at=None,
            page_count_total=66055,
            page_count_print=60003,
            page_count_copy=6052,
            page_count_checked_at=None,
        )
        defaults.update(overrides)
        for key, value in defaults.items():
            setattr(self, key, value)


def test_page_info_flattens_capabilities():
    from app.printers.test_print import build_page_info

    printer = _FakePrinter(
        capabilities={
            "make_model": "Kyocera ECOSYS P8060cdn",
            "firmware_version": "2FV_2000.005.011",
            "color_supported": True,
            "duplex_supported": True,
            "resolutions": [{"x": 600, "y": 600, "unit": 4}, {"x": 1200, "y": 600, "unit": 4}],
            "media_sizes": ["na_letter_8.5x11in", "iso_a4_210x297mm"],
            "default_media_size": "na_letter_8.5x11in",
            "media_trays": [
                {"source": "tray-1", "type": "stationery", "width_in": 8.5, "height_in": 11.0}
            ],
            "finishings": ["staple-top-left"],
        }
    )
    info = build_page_info(printer, [_FakeCartridge("cyan", 41), _FakeCartridge("black", 72)])

    assert info.address == "10.50.1.37:443"
    assert info.location == "LCACTC · Room 118 · Graphic Arts"
    assert info.resolutions == ["600 dpi", "1200x600 dpi"]
    assert info.media_sizes == ["Letter", "A4"]
    assert info.media_trays == ['Tray 1 · 8.5x11" · Stationery']
    assert info.finishings == ["Staple top left"]
    # Black first regardless of the order the rows came back in — it's the
    # order the device's own panel lists them.
    assert info.toner == [("black", 72), ("cyan", 41)]


def test_page_info_survives_a_printer_that_was_never_discovered():
    """A printer added minutes ago has no capabilities at all. It must still
    get a page — printing is the point, the data is a bonus."""
    from app.printers.test_print import _build_test_page, build_page_info

    info = build_page_info(_FakePrinter(capabilities=None, serial_number=None), [])
    assert info.media_sizes == []
    assert info.toner == []
    assert _build_test_page(info, "someone@example.org", "America/Chicago").startswith(b"%PDF")


def test_long_lists_say_how_many_were_left_off():
    """A sheet listing 8 of 60 media sizes without saying so reads as a
    complete list and misleads whoever is holding it."""
    from app.printers.test_print import _join

    assert _join([f"size{n}" for n in range(12)]) == (
        "size0, size1, size2, size3, size4, size5, size6, size7  (+4 more)"
    )
    assert _join([]) == "—"


def test_media_labels_fall_back_to_the_names_own_size_token():
    from app.printers.test_print import media_label

    assert media_label("na_letter_8.5x11in") == "Letter"
    assert media_label("iso_a4_210x297mm") == "A4"
    # Unknown but well-formed PWG name: the middle token is the common name.
    assert media_label("om_photo-l_89x127mm") == "Photo L"
    assert media_label(None) == "—"


def test_rendered_page_is_a_single_letter_sized_pdf():
    from app.printers.test_print import _build_test_page, build_page_info

    printer = _FakePrinter(capabilities={"color_supported": True})
    info = build_page_info(printer, [_FakeCartridge("black", 5)])
    pdf = _build_test_page(info, "manderson@brookfieldr3.org", "America/Chicago")
    assert pdf.startswith(b"%PDF")
    assert pdf.count(b"/Type /Page\n") <= 1
