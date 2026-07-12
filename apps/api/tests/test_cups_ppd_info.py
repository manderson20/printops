import subprocess

from app.printers.cups_ppd_info import get_cups_queue_default_page_size


class FakeCompletedProcess:
    def __init__(self, returncode: int, stdout: str):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = ""


def test_extracts_starred_page_size(monkeypatch):
    def fake_run(argv, **kwargs):
        assert argv == ["lpoptions", "-p", "printops-abc123", "-l"]
        return FakeCompletedProcess(
            0,
            "PageSize/Media Size: A4 *Letter Legal\nColorModel/Color Model: *RGB Gray\n",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert get_cups_queue_default_page_size("abc123") == "Letter"


def test_no_pagesize_line_returns_none(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run", lambda argv, **kwargs: FakeCompletedProcess(0, "ColorModel: *RGB Gray\n")
    )
    assert get_cups_queue_default_page_size("abc123") is None


def test_no_starred_choice_returns_none(monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda argv, **kwargs: FakeCompletedProcess(0, "PageSize: A4 Letter Legal\n"),
    )
    assert get_cups_queue_default_page_size("abc123") is None


def test_nonzero_exit_returns_none(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run", lambda argv, **kwargs: FakeCompletedProcess(1, "")
    )
    assert get_cups_queue_default_page_size("abc123") is None


def test_command_not_found_returns_none(monkeypatch):
    def fake_run(argv, **kwargs):
        raise FileNotFoundError()

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert get_cups_queue_default_page_size("abc123") is None


def test_timeout_returns_none(monkeypatch):
    def fake_run(argv, **kwargs):
        raise subprocess.TimeoutExpired(cmd="lpoptions", timeout=10)

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert get_cups_queue_default_page_size("abc123") is None
