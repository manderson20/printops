"""The per-printer Avahi service file is what `airprint_enabled` actually does.

Untested until now, and it got away with that because nothing depended on it:
cupsd was advertising every shared queue itself, so a printer showed up in an
Add Printer picker whether or not this script had written anything. 0.75.2 turns
cupsd's blanket advertisement off (#110), which makes these files the only thing
publishing a queue — the flag finally means something, and so does a bug here.

The failure mode is quiet in both directions. A file that doesn't get written
means a printer nobody can find; a file that doesn't get removed means a printer
an admin believes they hid.
"""

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
GENERATOR = REPO / "infra" / "cups" / "generate_avahi_service.py"

PRINTER_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


@pytest.fixture
def avahi(monkeypatch, tmp_path):
    """The generator loaded as a module, with its writes redirected and its two
    API calls stubbed. It is a standalone script run by root from
    sync_cups_queue.sh, not an importable package, so it is loaded by path."""
    spec = importlib.util.spec_from_file_location("generate_avahi_service", GENERATOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "SERVICES_DIR", str(tmp_path))
    monkeypatch.setattr(module, "load_backend_token", lambda: "stub-token")
    return module


def _install(module, printer, *, advertise_ipps=False):
    def api_get(_token, path):
        if path.endswith("/server-settings"):
            return {"advertise_ipps": advertise_ipps}
        return printer

    module.api_get = api_get


def _printer(**overrides):
    base = {
        "name": "ES Library Printer",
        "airprint_enabled": True,
        "capabilities": {
            "color_supported": False,
            "duplex_supported": True,
            "document_formats": ["application/pdf", "image/urf"],
        },
    }
    base.update(overrides)
    return base


def _service_file(module):
    return Path(module.SERVICES_DIR) / f"printops-{PRINTER_ID}.service"


def test_an_enabled_printer_gets_a_service_file(avahi, monkeypatch):
    _install(avahi, _printer())
    monkeypatch.setattr(avahi.sys, "argv", ["generate_avahi_service.py", PRINTER_ID])

    assert avahi.main() == 0

    xml = _service_file(avahi).read_text()
    assert "<type>_ipp._tcp</type>" in xml
    assert f"<txt-record>rp=printers/printops-{PRINTER_ID}</txt-record>" in xml
    assert "<txt-record>Color=F</txt-record>" in xml
    assert "<txt-record>Duplex=T</txt-record>" in xml


def test_disabling_a_printer_removes_the_file_it_had(avahi, monkeypatch):
    """The half that matters for an admin who is hiding something. A stale file
    left behind keeps advertising a queue that the Printers page now reports as
    hidden — which is #110 again, one printer at a time."""
    monkeypatch.setattr(avahi.sys, "argv", ["generate_avahi_service.py", PRINTER_ID])

    _install(avahi, _printer(airprint_enabled=True))
    assert avahi.main() == 0
    assert _service_file(avahi).exists()

    _install(avahi, _printer(airprint_enabled=False))
    assert avahi.main() == 0
    assert not _service_file(avahi).exists()


def test_a_printer_that_was_never_enabled_writes_nothing(avahi, monkeypatch):
    _install(avahi, _printer(airprint_enabled=False))
    monkeypatch.setattr(avahi.sys, "argv", ["generate_avahi_service.py", PRINTER_ID])

    assert avahi.main() == 0
    assert not _service_file(avahi).exists()
    assert list(Path(avahi.SERVICES_DIR).iterdir()) == []


def test_ipps_is_additive_and_never_replaces_the_plaintext_advertisement(avahi, monkeypatch):
    """Settings > Server > advertise_ipps. An AirPrint client that only knows
    _ipp._tcp must keep finding the printer, so the encrypted block is a second
    service inside the same group, not a substitution."""
    _install(avahi, _printer(), advertise_ipps=True)
    monkeypatch.setattr(avahi.sys, "argv", ["generate_avahi_service.py", PRINTER_ID])

    assert avahi.main() == 0
    xml = _service_file(avahi).read_text()
    assert xml.count("<type>_ipp._tcp</type>") == 1
    assert xml.count("<type>_ipps._tcp</type>") == 1


def test_a_printer_name_cannot_break_out_of_the_xml(avahi, monkeypatch):
    """Printer names are typed by admins and land in both an element and a TXT
    record. A stray `&` or `<` in one would make avahi-daemon reject the whole
    file, silently taking that printer off the network."""
    _install(avahi, _printer(name="Art & Design <Main>"))
    monkeypatch.setattr(avahi.sys, "argv", ["generate_avahi_service.py", PRINTER_ID])

    assert avahi.main() == 0
    xml = _service_file(avahi).read_text()
    assert "Art &amp; Design &lt;Main&gt;" in xml
    assert "Art & Design <Main>" not in xml

    from xml.etree import ElementTree

    ElementTree.fromstring(xml)  # raises if the document is not well-formed


def test_capabilities_missing_entirely_still_produces_a_usable_file(avahi, monkeypatch):
    """Two printers on this estate have never been successfully probed. They
    still need to be reachable, so the advertisement falls back to the default
    formats rather than publishing an empty pdl list."""
    _install(avahi, _printer(capabilities=None))
    monkeypatch.setattr(avahi.sys, "argv", ["generate_avahi_service.py", PRINTER_ID])

    assert avahi.main() == 0
    xml = _service_file(avahi).read_text()
    assert "pdl=application/pdf" in xml
    assert "<txt-record>Color=F</txt-record>" in xml


def test_the_bulk_regenerate_script_calls_a_route_that_exists():
    """scripts/regenerate_avahi_services.sh runs once, during an upgrade, as
    root, in the window where cupsd's advertisement is about to be switched
    off. A wrong URL there is not a 404 in a log — it is every printer on the
    estate missing from Add Printer pickers.

    It shipped with one: `/internal/printers` instead of `/internal/printers/ids`,
    caught only by running it against the live API. Shell scripts naming REST
    paths are invisible to every other check in this repo, so the path is
    matched against the routes the app actually serves.
    """
    import re

    from app.main import app

    script = (REPO / "scripts" / "regenerate_avahi_services.sh").read_text()
    called = set(re.findall(r"\$API_BASE(/api/v1/[A-Za-z0-9/_-]+)", script))
    assert called, "the script no longer calls the API — update this test"

    # Via the OpenAPI schema rather than app.routes: this FastAPI version keeps
    # included routers as lazy _IncludedRouter wrappers with no .path, so
    # walking app.routes finds 23 objects and no paths at all.
    served = set(app.openapi()["paths"])
    for path in called:
        assert path in served, f"{path} is not a route this app serves"
