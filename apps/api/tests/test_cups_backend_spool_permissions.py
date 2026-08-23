"""infra/cups/backends/printops — what a held job's document is readable by.

A held job's file sits in /var/spool/printops-held until someone releases it
at the printer, owned by root with group `lp` so the API process (a member of
that group, scripts/ensure_held_spool_group.sh) can read it back. It is
somebody's document, so the bits it is written with are worth pinning: not
world-anything, and no more group access than the release path actually uses.
"""

import importlib.util
import stat
from importlib.machinery import SourceFileLoader
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[3] / "infra" / "cups" / "backends" / "printops"


@pytest.fixture
def backend_module():
    loader = SourceFileLoader("printops_cups_backend_spool", str(SCRIPT_PATH))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def _spool(backend_module, monkeypatch, tmp_path, source: Path | None):
    monkeypatch.setattr(backend_module, "HELD_SPOOL_DIR", str(tmp_path / "held"))
    return Path(backend_module.spool_held_file("job-uuid", str(source) if source else None))


def test_a_spooled_document_is_not_group_writable(backend_module, monkeypatch, tmp_path):
    """Group `lp` reads this file to release the job and unlinks it afterwards;
    unlinking answers to the directory's bits, not the file's. Nothing writes
    to it after it is spooled, so group write is access it never needs."""
    source = tmp_path / "doc.pdf"
    source.write_bytes(b"%PDF-1.4 held")

    dest = _spool(backend_module, monkeypatch, tmp_path, source)

    mode = stat.S_IMODE(dest.stat().st_mode)
    assert mode == 0o640, oct(mode)
    assert dest.read_bytes() == b"%PDF-1.4 held"


def test_a_spooled_document_is_never_world_readable(backend_module, monkeypatch, tmp_path):
    source = tmp_path / "doc.pdf"
    source.write_bytes(b"%PDF-1.4 held")

    dest = _spool(backend_module, monkeypatch, tmp_path, source)

    mode = stat.S_IMODE(dest.stat().st_mode)
    assert not mode & stat.S_IRWXO, oct(mode)


def test_a_document_arriving_on_stdin_gets_the_same_bits(backend_module, monkeypatch, tmp_path):
    """CUPS hands the document over on stdin when it passes no filename — the
    same custody, so the same permissions."""

    source = tmp_path / "in.pdf"
    source.write_bytes(b"%PDF-1.4 stdin")
    with source.open("rb") as handle:
        monkeypatch.setattr(backend_module.sys, "stdin", SimpleNamespace(buffer=handle))
        dest = _spool(backend_module, monkeypatch, tmp_path, None)

    assert stat.S_IMODE(dest.stat().st_mode) == 0o640
    assert dest.read_bytes() == b"%PDF-1.4 stdin"


def test_the_spool_directory_keeps_its_group_and_setgid(backend_module, monkeypatch, tmp_path):
    """The directory does need group write — that is what lets the API remove
    a released job's file — and setgid is what keeps `lp` on new files."""
    source = tmp_path / "doc.pdf"
    source.write_bytes(b"x")

    dest = _spool(backend_module, monkeypatch, tmp_path, source)

    mode = stat.S_IMODE(dest.parent.stat().st_mode)
    assert mode == 0o2770, oct(mode)
    assert not mode & stat.S_IRWXO, oct(mode)
