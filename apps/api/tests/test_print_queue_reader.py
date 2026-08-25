"""Reading the live queue out of cupsd.

Ordering is the part worth pinning down: the page's whole claim is "you are
3rd of 5", and cupsd's order is priority-then-age, not the order Get-Jobs
happens to hand back. Ownership matching matters just as much — CUPS records
whatever name the client sent, and a match that misses turns a person's own
job into "another staff member", with no button on it.
"""

import plistlib
from unittest.mock import patch

from app.printers.print_queue import (
    NORMAL_PRIORITY,
    YIELDED_PRIORITY,
    QueuedJob,
    queue_order,
    queued_jobs,
)


def make(cups_job_id, *, priority=NORMAL_PRIORITY, state=3, owner="a@b.org"):
    return QueuedJob(
        cups_job_id=cups_job_id,
        printer_id="8142ccdb-195b-4acf-acfd-56bc52162b72",
        owner=owner,
        document_name="doc.pdf",
        size_bytes=None,
        priority=priority,
        state=state,
        created_at=None,
    )


def _plist(*jobs: dict) -> str:
    return plistlib.dumps(
        {
            "Tests": [
                {
                    "StatusCode": "successful-ok",
                    # The first group back is the operation's own attributes,
                    # not a job — the real thing always includes it.
                    "ResponseAttributes": [
                        {"attributes-charset": "utf-8"},
                        *jobs,
                    ],
                }
            ]
        }
    ).decode()


def _run(stdout: str, returncode: int = 0):
    class Result:
        pass

    result = Result()
    result.stdout = stdout
    result.returncode = returncode
    return patch("app.printers.print_queue.subprocess.run", return_value=result)


def test_orders_the_way_cupsd_will_print():
    printing = make(9, state=5)
    yielded = make(2, priority=YIELDED_PRIORITY)
    older = make(4)
    newer = make(7)
    assert sorted([yielded, newer, printing, older], key=queue_order) == [
        printing,  # whatever is on its way to the device is the head of the line
        older,  # same priority, oldest first
        newer,
        yielded,  # dropped priority puts it behind everything at the default
    ]


def test_parses_a_job_out_of_the_plist():
    output = _plist(
        {
            "job-id": 5814,
            "job-name": "Advisory.pdf",
            "job-originating-user-name": "hfiala@brookfieldr3.org",
            "job-state": 3,
            "job-priority": 50,
            "job-k-octets": 847,
            "time-at-creation": 1787613031,
            "job-printer-uri": (
                "ipp://localhost:631/printers/printops-8142ccdb-195b-4acf-acfd-56bc52162b72"
            ),
        }
    )
    with _run(output):
        [job] = queued_jobs()
    assert job.cups_job_id == 5814
    assert job.printer_id == "8142ccdb-195b-4acf-acfd-56bc52162b72"
    assert job.owner == "hfiala@brookfieldr3.org"
    # job-k-octets is kilobytes; a size shown to a person should be bytes.
    assert job.size_bytes == 847 * 1024
    assert job.created_at is not None and job.created_at.year == 2026
    assert job.is_waiting is True


def test_completed_and_cancelled_jobs_are_not_in_the_line():
    output = _plist(
        {"job-id": 1, "job-state": 9, "job-printer-uri": "ipp://localhost/printers/printops-x"},
        {"job-id": 2, "job-state": 7, "job-printer-uri": "ipp://localhost/printers/printops-x"},
        {"job-id": 3, "job-state": 3, "job-printer-uri": "ipp://localhost/printers/printops-x"},
    )
    with _run(output):
        assert [job.cups_job_id for job in queued_jobs()] == [3]


def test_a_queue_that_is_not_printops_has_no_printer_id():
    output = _plist(
        {"job-id": 1, "job-state": 3, "job-printer-uri": "ipp://localhost/printers/HP_LaserJet"}
    )
    with _run(output):
        [job] = queued_jobs()
    assert job.printer_id is None


def test_unreadable_output_is_not_an_empty_queue():
    # None means "couldn't ask", which the router turns into a 503. Returning
    # [] here would tell someone with a job waiting that nothing is queued.
    with _run("this is not a plist"):
        assert queued_jobs() is None


def test_an_ipp_error_is_not_an_empty_queue():
    output = plistlib.dumps({"Tests": [{"StatusCode": "server-error-busy"}]}).decode()
    with _run(output):
        assert queued_jobs() is None


def test_ownership_is_case_insensitive_and_never_matches_nothing():
    job = make(1, owner="HFiala@Brookfieldr3.org")
    assert job.belongs_to("hfiala@brookfieldr3.org") is True
    assert job.belongs_to(None, "hfiala@brookfieldr3.org") is True
    assert job.belongs_to("someone@else.org") is False
    # A job cupsd gave us no owner for belongs to nobody — emphatically not to
    # whoever happens to be looking at the page.
    assert make(1, owner=None).belongs_to(None) is False
    assert make(1, owner=None).belongs_to("hfiala@brookfieldr3.org") is False
