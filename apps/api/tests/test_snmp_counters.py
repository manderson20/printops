from unittest.mock import patch

import pytest

from app.core.crypto import encrypt
from app.models.printer import Printer
from app.models.snmp import SnmpDefaultsSettings
from app.printers.snmp_counters import (
    SnmpConfig,
    SnmpProbeError,
    _canon_breakdown,
    _compute_level_percent,
    _extract_counter_value,
    _extract_string_value,
    _konica_minolta_breakdown,
    _poll_counters_sync,
    detect_vendor_profile,
    get_sys_descr_vendor_profile,
    get_toner_supplies,
    resolve_snmp_config,
)

# Real snmpwalk -On output captured live against this district's Canon
# MF642C/643C/644C (community "public", -v1). Used as fixture data so the
# label/value zipping logic is tested against the actual shape a real
# device returns, not an assumed one.
CANON_LABELS_OUTPUT = """\
.1.3.6.1.4.1.1602.1.11.2.1.1.2.1 = STRING: "Total 1"
.1.3.6.1.4.1.1602.1.11.2.1.1.2.17 = STRING: "Copy (Total 1)"
.1.3.6.1.4.1.1602.1.11.2.1.1.2.25 = STRING: "Print (Total 1)"
"""

CANON_VALUES_OUTPUT = """\
.1.3.6.1.4.1.1602.1.11.2.1.1.3.1 = Counter32: 9026
.1.3.6.1.4.1.1602.1.11.2.1.1.3.17 = Counter32: 354
.1.3.6.1.4.1.1602.1.11.2.1.1.3.25 = Counter32: 8672
"""

CANON_SYS_DESCR_OUTPUT = '.1.3.6.1.2.1.1.1.0 = STRING: "Canon MF642C/643C/644C /P"\n'

STANDARD_TOTAL_OUTPUT = ".1.3.6.1.2.1.43.10.2.1.4.1.1 = Counter32: 9026\n"


def _fake_snmp_config(**overrides) -> SnmpConfig:
    defaults = dict(community="public", version="v2c", port=161, vendor_profile="generic")
    defaults.update(overrides)
    return SnmpConfig(**defaults)


class TestDetectVendorProfile:
    def test_manufacturer_field_match(self):
        printer = Printer(name="t", ip_address="1.2.3.4", manufacturer="Lexmark", model="MX711")
        assert detect_vendor_profile(printer) == "lexmark"

    def test_model_field_match(self):
        printer = Printer(name="t", ip_address="1.2.3.4", manufacturer="", model="TASKalfa 3253ci")
        assert detect_vendor_profile(printer) == "kyocera"

    def test_konica_minolta_via_bizhub(self):
        printer = Printer(
            name="t", ip_address="1.2.3.4", manufacturer=None, model="KONICA MINOLTA bizhub 750i"
        )
        assert detect_vendor_profile(printer) == "konica_minolta"

    def test_hp_via_laserjet(self):
        printer = Printer(
            name="t", ip_address="1.2.3.4", manufacturer="", model="HP LaserJet M402n"
        )
        assert detect_vendor_profile(printer) == "hp"

    def test_unrecognized_falls_back_to_generic(self):
        printer = Printer(
            name="t", ip_address="1.2.3.4", manufacturer="", model="Some Weird Printer 3000"
        )
        assert detect_vendor_profile(printer) == "generic"

    def test_real_canon_db_fields_are_unreliable(self):
        """Confirmed against this district's real Canon unit: IPP discovery
        left manufacturer blank and model as the raw Canon model code
        ("CNMF642C/643C/644C") with no "canon" substring anywhere — this
        heuristic alone gets it wrong. get_sys_descr_vendor_profile (using
        a live SNMP sysDescr fetch instead) is what actually recognizes
        this device correctly; see TestGetSysDescrVendorProfile below."""
        printer = Printer(
            name="IT Color Copier",
            ip_address="10.20.1.25",
            manufacturer="",
            model="CNMF642C/643C/644C",
            capabilities={"make_model": "CNMF642C/643C/644C"},
        )
        assert detect_vendor_profile(printer) == "generic"


class TestExtractCounterValue:
    def test_counter32(self):
        assert _extract_counter_value("Counter32: 9026") == 9026

    def test_integer(self):
        assert _extract_counter_value("INTEGER: 42") == 42

    def test_no_such_instance(self):
        assert _extract_counter_value("No Such Instance currently exists at this OID") is None

    def test_malformed(self):
        assert _extract_counter_value("garbage") is None


class TestExtractStringValue:
    def test_quoted_string(self):
        assert _extract_string_value('STRING: "Copy (Total 1)"') == "Copy (Total 1)"

    def test_malformed(self):
        assert _extract_string_value("garbage") is None


class TestGetSysDescrVendorProfile:
    def test_recognizes_real_canon_sys_descr(self):
        config = _fake_snmp_config(version="v1")
        with patch("app.printers.snmp_counters._run_snmp", return_value=CANON_SYS_DESCR_OUTPUT):
            assert get_sys_descr_vendor_profile("10.20.1.25", config) == "canon"

    def test_returns_none_not_generic_on_probe_failure(self):
        """Unreachable is not the same as unrecognized — the caller falls
        back to the DB-field heuristic only when this returns None."""
        config = _fake_snmp_config()
        with patch("app.printers.snmp_counters._run_snmp", side_effect=SnmpProbeError("timed out")):
            assert get_sys_descr_vendor_profile("10.0.0.1", config) is None

    def test_returns_none_for_unrecognized_sys_descr(self):
        config = _fake_snmp_config()
        with patch(
            "app.printers.snmp_counters._run_snmp",
            return_value='.1.3.6.1.2.1.1.1.0 = STRING: "Some Weird Device"\n',
        ):
            assert get_sys_descr_vendor_profile("10.0.0.1", config) is None


class TestCanonBreakdown:
    def test_matches_real_captured_output(self):
        """Regression check mirroring the manual live cross-check: Copy +
        Print sums exactly to Total for the real device this was verified
        against."""
        config = _fake_snmp_config(vendor_profile="canon", version="v1")

        def fake_run_snmp(argv):
            oid = argv[-1]
            if oid.endswith(".2"):
                return CANON_LABELS_OUTPUT
            return CANON_VALUES_OUTPUT

        with patch("app.printers.snmp_counters._run_snmp", side_effect=fake_run_snmp):
            result = _canon_breakdown("10.20.1.25", config)

        assert result.copy == 354
        assert result.print == 8672
        assert result.confidence == "verified"
        assert result.copy + result.print == 9026


class TestKonicaMinoltaBreakdown:
    def test_matches_real_captured_output(self):
        config = _fake_snmp_config(vendor_profile="konica_minolta")
        responses = iter(["INTEGER: 757288", "INTEGER: 533574", "INTEGER: 223714"])
        with patch(
            "app.printers.snmp_counters._run_snmp", side_effect=lambda argv: next(responses)
        ):
            result = _konica_minolta_breakdown("10.30.1.210", config)

        assert result.copy == 533574
        assert result.print == 223714
        assert result.confidence == "best_effort"
        assert result.copy + result.print == 757288

    def test_logs_warning_but_does_not_raise_when_split_disagrees(self, caplog):
        config = _fake_snmp_config(vendor_profile="konica_minolta")
        responses = iter(["INTEGER: 100", "INTEGER: 40", "INTEGER: 40"])  # doesn't sum to 100
        with patch(
            "app.printers.snmp_counters._run_snmp", side_effect=lambda argv: next(responses)
        ):
            result = _konica_minolta_breakdown("10.30.1.210", config)

        assert result.confidence == "best_effort"  # doesn't downgrade or raise
        assert "didn't sum to the total" in caplog.text


class TestComputeLevelPercent:
    def test_normal_reading(self):
        assert _compute_level_percent(45, 100) == 45

    def test_rounds_to_nearest_percent(self):
        assert _compute_level_percent(1, 3) == 33

    def test_clamps_above_max_capacity(self):
        # Confirmed live: some firmware reports a raw level slightly above
        # its own stated max capacity.
        assert _compute_level_percent(105, 100) == 100

    def test_unknown_level_returns_none(self):
        # -2 is this MIB's convention for "some supply remains, but the
        # printer can't quantify it."
        assert _compute_level_percent(-2, 100) is None

    def test_missing_max_capacity_returns_none(self):
        assert _compute_level_percent(45, None) is None

    def test_zero_max_capacity_returns_none(self):
        assert _compute_level_percent(45, 0) is None

    def test_missing_level_returns_none(self):
        assert _compute_level_percent(None, 100) is None


SUPPLIES_TYPE_OUTPUT = (
    ".1.3.6.1.2.1.43.11.1.1.5.1 = INTEGER: 3\n"
    ".1.3.6.1.2.1.43.11.1.1.5.2 = INTEGER: 3\n"
    ".1.3.6.1.2.1.43.11.1.1.5.3 = INTEGER: 9\n"  # not a cartridge type — excluded
)
SUPPLIES_DESCRIPTION_OUTPUT = (
    '.1.3.6.1.2.1.43.11.1.1.6.1 = STRING: "Black Toner Cartridge"\n'
    '.1.3.6.1.2.1.43.11.1.1.6.2 = STRING: "Cyan Toner Cartridge"\n'
    '.1.3.6.1.2.1.43.11.1.1.6.3 = STRING: "Waste Toner Box"\n'
)
SUPPLIES_LEVEL_OUTPUT = (
    ".1.3.6.1.2.1.43.11.1.1.9.1 = INTEGER: 45\n"
    ".1.3.6.1.2.1.43.11.1.1.9.2 = INTEGER: -2\n"  # unknown
)
SUPPLIES_MAX_CAPACITY_OUTPUT = (
    ".1.3.6.1.2.1.43.11.1.1.8.1 = INTEGER: 100\n" ".1.3.6.1.2.1.43.11.1.1.8.2 = INTEGER: 100\n"
)


class TestGetTonerSupplies:
    def test_parses_type_description_and_level(self):
        config = _fake_snmp_config()

        def fake_run_snmp(argv):
            oid = argv[-1]
            return {
                "1.3.6.1.2.1.43.11.1.1.5": SUPPLIES_TYPE_OUTPUT,
                "1.3.6.1.2.1.43.11.1.1.6": SUPPLIES_DESCRIPTION_OUTPUT,
                "1.3.6.1.2.1.43.11.1.1.9": SUPPLIES_LEVEL_OUTPUT,
                "1.3.6.1.2.1.43.11.1.1.8": SUPPLIES_MAX_CAPACITY_OUTPUT,
            }[oid]

        with patch("app.printers.snmp_counters._run_snmp", side_effect=fake_run_snmp):
            supplies = get_toner_supplies("10.30.1.210", config)

        # Row 3 (Waste Toner Box, type 9) isn't a cartridge type — excluded
        # even though it has no level/max-capacity data of its own.
        assert len(supplies) == 2

        black, cyan = supplies
        assert black.description == "Black Toner Cartridge"
        assert black.color == "black"
        assert black.level_percent == 45

        assert cyan.description == "Cyan Toner Cartridge"
        assert cyan.color == "cyan"
        assert cyan.level_percent is None  # -2 == unknown

    def test_missing_level_columns_still_returns_supplies(self):
        """A device that doesn't report prtMarkerSuppliesLevel/MaxCapacity
        at all (empty walk, not an error) still yields cartridge rows —
        just with level_percent=None, same best-effort convention as
        color/high_capacity."""
        config = _fake_snmp_config()

        def fake_run_snmp(argv):
            oid = argv[-1]
            if oid == "1.3.6.1.2.1.43.11.1.1.5":
                return ".1.3.6.1.2.1.43.11.1.1.5.1 = INTEGER: 3\n"
            if oid == "1.3.6.1.2.1.43.11.1.1.6":
                return '.1.3.6.1.2.1.43.11.1.1.6.1 = STRING: "Black Toner Cartridge"\n'
            raise SnmpProbeError("No response from device.")

        with patch("app.printers.snmp_counters._run_snmp", side_effect=fake_run_snmp):
            supplies = get_toner_supplies("10.30.1.210", config)

        assert len(supplies) == 1
        assert supplies[0].level_percent is None


class TestGracefulDegradation:
    def test_missing_snmp_binary_raises_clear_error(self):
        config = _fake_snmp_config()
        with patch("app.printers.snmp_counters.subprocess.run", side_effect=FileNotFoundError()):
            with pytest.raises(SnmpProbeError, match="available"):
                from app.printers.snmp_counters import snmp_get

                snmp_get("10.0.0.1", "1.2.3", config)

    def test_unrecognized_vendor_still_gets_total_only(self):
        """SNMP has no distinguishable "wrong community" error from
        "unreachable device" — both surface as a silent timeout at the
        protocol level (a protocol limitation, not a gap here). This test
        instead documents the other graceful-degradation path: an
        unrecognized vendor still gets the universal total, just no
        breakdown."""
        printer = Printer(
            name="t",
            ip_address="10.30.2.201",
            manufacturer="",
            model="Some Weird Printer 3000",
            snmp_enabled=True,
        )
        defaults = SnmpDefaultsSettings(
            community_encrypted=encrypt("public"), version="v2c", port=161
        )

        with (
            patch("app.printers.snmp_counters.get_standard_total", return_value=10291),
            patch("app.printers.snmp_counters.get_sys_descr_vendor_profile", return_value=None),
        ):
            succeeded = _poll_counters_sync(printer, defaults)

        assert succeeded is True
        assert printer.page_count_total == 10291
        assert printer.page_count_copy is None
        assert printer.page_count_print is None
        assert printer.page_count_confidence == "unsupported"
        assert printer.page_count_error is None

    def test_failed_standard_probe_leaves_prior_counts_in_place(self):
        printer = Printer(
            name="t",
            ip_address="10.0.0.1",
            manufacturer="",
            model="x",
            snmp_enabled=True,
            page_count_total=999,
            page_count_copy=111,
        )
        defaults = SnmpDefaultsSettings(
            community_encrypted=encrypt("public"), version="v2c", port=161
        )

        with patch(
            "app.printers.snmp_counters.get_standard_total",
            side_effect=SnmpProbeError("No response from device."),
        ):
            succeeded = _poll_counters_sync(printer, defaults)

        assert succeeded is False
        assert printer.page_count_total == 999  # untouched
        assert printer.page_count_copy == 111  # untouched
        assert printer.page_count_error == "No response from device."

    def test_snmp_disabled_is_a_no_op(self):
        import asyncio

        printer = Printer(
            name="t", ip_address="10.0.0.1", snmp_enabled=False, page_count_total=None
        )
        defaults = SnmpDefaultsSettings(
            community_encrypted=encrypt("public"), version="v2c", port=161
        )

        with patch("app.printers.snmp_counters._poll_counters_sync") as mock_poll:
            from app.printers.snmp_counters import refresh_printer_counters

            succeeded = asyncio.run(refresh_printer_counters(printer, defaults))
        mock_poll.assert_not_called()
        assert succeeded is False

    def test_refresh_printer_counters_returns_true_on_success(self):
        import asyncio

        printer = Printer(name="t", ip_address="10.0.0.1", snmp_enabled=True)
        defaults = SnmpDefaultsSettings(
            community_encrypted=encrypt("public"), version="v2c", port=161
        )

        with patch("app.printers.snmp_counters._poll_counters_sync", return_value=True):
            from app.printers.snmp_counters import refresh_printer_counters

            succeeded = asyncio.run(refresh_printer_counters(printer, defaults))
        assert succeeded is True


class TestResolveSnmpConfig:
    def test_printer_override_wins_over_global_default(self):
        printer = Printer(
            name="t",
            ip_address="10.0.0.1",
            snmp_port=1610,
            snmp_version="v1",
            snmp_community_encrypted=encrypt("private-community"),
            snmp_vendor_profile="hp",
        )
        defaults = SnmpDefaultsSettings(
            community_encrypted=encrypt("public"), version="v2c", port=161
        )

        config = resolve_snmp_config(printer, defaults)
        assert config.port == 1610
        assert config.version == "v1"
        assert config.community == "private-community"
        assert config.vendor_profile == "hp"

    def test_falls_back_to_global_default_when_unset(self):
        printer = Printer(name="t", ip_address="10.0.0.1", manufacturer="", model="unknown")
        defaults = SnmpDefaultsSettings(
            community_encrypted=encrypt("public"), version="v2c", port=161
        )

        config = resolve_snmp_config(printer, defaults)
        assert config.port == 161
        assert config.version == "v2c"
        assert config.community == "public"
        assert config.vendor_profile == "generic"


class TestRecordReading:
    def test_builds_reading_from_current_printer_state(self):
        import uuid
        from datetime import UTC, datetime

        from app.printers.snmp_counters import record_reading

        printer_id = uuid.uuid4()
        checked_at = datetime.now(UTC)
        printer = Printer(
            id=printer_id,
            name="t",
            ip_address="10.0.0.1",
            page_count_total=9026,
            page_count_copy=354,
            page_count_print=8672,
            page_count_confidence="verified",
            page_count_checked_at=checked_at,
        )

        reading = record_reading(printer)

        assert reading.printer_id == printer_id
        assert reading.recorded_at == checked_at
        assert reading.page_count_total == 9026
        assert reading.page_count_copy == 354
        assert reading.page_count_print == 8672
        assert reading.page_count_confidence == "verified"
