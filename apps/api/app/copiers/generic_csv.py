"""Generic CSV import connector — the fallback every vendor gets before
(or instead of) a real API/native connector exists, per the spec's "don't
fake support, CSV import is a legitimate fallback" principle.

Only parses raw rows into NormalizedUsageRow — it does NOT resolve
staff_email against StaffCopierIdentity itself (that needs a DB session,
which this connector interface deliberately doesn't take; see
app/copiers/connector.py). Identity resolution happens in the import
flow (app/routers/copier_imports.py's shared parser), which does have a
db session, using external_identity_used from each row this produces.
"""

import csv
import io
from datetime import UTC, datetime

from app.copiers.connector import CopierConnector, ImportResult, NormalizedUsageRow
from app.models.copier_import import CopierImportTemplate
from app.models.mfp_device import MfpDevice

_BOOL_TRUE_VALUES = {"1", "true", "yes", "y", "duplex", "two-sided"}


class CsvParseError(Exception):
    """A row (or the file as a whole) couldn't be parsed — carries a row
    number so app/routers/copier_imports.py can report it per-row rather
    than failing the whole batch on one bad row."""

    def __init__(self, message: str, row_number: int | None = None):
        super().__init__(message)
        self.row_number = row_number


def _parse_int(value: str | None) -> int | None:
    if value is None or value.strip() == "":
        return None
    try:
        return int(float(value.strip()))
    except ValueError:
        return None


def _parse_bool(value: str | None) -> bool | None:
    if value is None or value.strip() == "":
        return None
    return value.strip().lower() in _BOOL_TRUE_VALUES


def _parse_datetime(value: str | None) -> datetime | None:
    if value is None or value.strip() == "":
        return None
    text = value.strip()
    # Vendor exports use wildly different formats — try ISO first (the one
    # format we can actually promise), then a handful of common
    # spreadsheet/export shapes before giving up. Naive datetimes are
    # assumed UTC rather than the server's local time, since the source
    # timezone is never actually knowable from a bare CSV cell.
    try:
        return datetime.fromisoformat(text).replace(tzinfo=UTC)
    except ValueError:
        pass
    formats = (
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y",
    )
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


def normalize_csv_row(
    row: dict[str, str], mapping: dict[str, str], row_number: int
) -> NormalizedUsageRow:
    """mapping is {target_field: source_column_name}. Missing/blank source
    columns just leave the corresponding NormalizedUsageRow field None,
    except identity_value, which is required — a row with no identity at
    all can't be attributed to anyone or resolved later, so it's a hard
    parse error rather than a silently-unmapped row."""

    def get(field_name: str) -> str | None:
        source_column = mapping.get(field_name)
        if not source_column:
            return None
        return row.get(source_column)

    identity_value = get("identity_value")
    if not identity_value or not identity_value.strip():
        raise CsvParseError("Missing identity value", row_number=row_number)

    occurred_at = _parse_datetime(get("occurred_at"))
    period_start = _parse_datetime(get("period_start"))
    period_end = _parse_datetime(get("period_end"))
    if occurred_at is None and (period_start is None or period_end is None):
        raise CsvParseError(
            "Row has neither a single timestamp nor a full period_start/period_end pair",
            row_number=row_number,
        )

    return NormalizedUsageRow(
        external_identity_used=identity_value.strip(),
        activity_type=(get("activity_type") or "copy").strip() or "copy",
        page_count=_parse_int(get("page_count")),
        sheet_count=_parse_int(get("sheet_count")),
        color_page_count=_parse_int(get("color_page_count")),
        monochrome_page_count=_parse_int(get("monochrome_page_count")),
        duplex=_parse_bool(get("duplex")),
        paper_size=get("paper_size"),
        occurred_at=occurred_at,
        period_start=period_start,
        period_end=period_end,
        authentication_method=get("authentication_method"),
        raw_payload=dict(row),
    )


def parse_csv_rows(
    raw_bytes: bytes, delimiter: str = ","
) -> tuple[list[str], list[dict[str, str]]]:
    """Decodes + parses raw CSV bytes into (header_columns, rows-as-dicts)
    — the shared entry point for upload (header detection), preview, and
    commit, so all three see identical parsing. Column mapping is applied
    separately, per row, by normalize_csv_row — this just gets from bytes
    to plain dict rows."""
    text = raw_bytes.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    rows = list(reader)
    header = reader.fieldnames or []
    return list(header), rows


class GenericCsvConnector(CopierConnector):
    connector_type = "generic_csv"
    display_name = "Generic CSV Import"

    async def import_accounting_file(
        self, device: MfpDevice, raw_bytes: bytes, template: CopierImportTemplate
    ) -> ImportResult:
        """Rows that fail to parse (bad/missing identity or timestamp) are
        silently skipped here — this connector method is used for a quick
        programmatic import path only. app/routers/copier_imports.py's
        actual upload/preview/commit flow calls parse_csv_rows +
        normalize_csv_row directly instead, so it can report each row's
        error individually (CsvParseError.row_number) rather than dropping
        rows without telling anyone."""
        _, raw_rows = parse_csv_rows(raw_bytes, template.delimiter)
        normalized: list[NormalizedUsageRow] = []
        for row_number, row in enumerate(raw_rows, start=1):
            try:
                normalized.append(normalize_csv_row(row, template.column_mapping, row_number))
            except CsvParseError:
                continue
        return ImportResult(rows=normalized)
