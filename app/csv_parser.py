"""Parse and validate a hospital CSV upload.

Validation is all-or-nothing: if any row is invalid we reject the whole file
and create nothing. A batch can only be activated when every row was created,
so accepting the valid rows of a bad file would just leave an inactive,
half-populated batch behind on the directory API.
"""

import csv
import io
from dataclasses import dataclass
from typing import BinaryIO, List, Optional, Tuple

REQUIRED_COLUMNS = ("name", "address")
OPTIONAL_COLUMNS = ("phone",)
KNOWN_COLUMNS = REQUIRED_COLUMNS + OPTIONAL_COLUMNS


@dataclass(frozen=True)
class HospitalRow:
    row: int  # 1-based data row number (header not counted)
    name: str
    address: str
    phone: Optional[str]

    def identity(self) -> Tuple[str, str, Optional[str]]:
        """The fields that identify this row on the directory API.

        The directory API has no idempotency key, so after an ambiguous
        failure we find out whether a create landed by matching on these.
        """
        return (self.name, self.address, self.phone)


@dataclass(frozen=True)
class RowError:
    row: Optional[int]  # None for file-level problems (header, size, encoding)
    message: str


class CSVValidationError(Exception):
    def __init__(self, errors: List[RowError]):
        super().__init__(f"{len(errors)} validation error(s)")
        self.errors = errors


def parse_hospital_csv(stream: BinaryIO, max_rows: int) -> List[HospitalRow]:
    """Return validated rows, or raise CSVValidationError listing every problem.

    The stream is read lazily, a buffer at a time: parsing stops at the first
    row past `max_rows`, so the rest of an oversized file is never read or
    decoded. Used by both the validate and the bulk-create endpoints.
    """
    # utf-8-sig strips the byte-order mark Excel adds to exported CSVs.
    # newline="" hands line endings to the csv module, as its docs require.
    text = io.TextIOWrapper(stream, encoding="utf-8-sig", newline="")
    try:
        # strict=True turns quoting mistakes (e.g. an unclosed quote) into
        # errors instead of silently merging the rest of the file into one field.
        reader = csv.reader(text, strict=True)
        header = next(reader, None)
        if header is None:
            raise CSVValidationError([RowError(None, "File is empty")])
        columns = _validate_header(header)
        rows, errors = _parse_rows(reader, columns, max_rows)
    except UnicodeDecodeError:
        raise CSVValidationError([RowError(None, "File must be UTF-8 encoded text")])
    except csv.Error as exc:
        raise CSVValidationError([RowError(None, f"Malformed CSV: {exc}")])
    finally:
        text.detach()  # don't let the wrapper close the caller's stream

    if errors:
        raise CSVValidationError(errors)
    if not rows:
        raise CSVValidationError([RowError(None, "File contains no hospital rows")])
    return rows


def _validate_header(header: List[str]) -> List[str]:
    columns = [c.strip().lower() for c in header]
    errors = []
    unknown = [c for c in columns if c not in KNOWN_COLUMNS]
    if unknown:
        errors.append(RowError(None, f"Unknown column(s): {', '.join(unknown)}"))
    missing = [c for c in REQUIRED_COLUMNS if c not in columns]
    if missing:
        errors.append(RowError(None, f"Missing required column(s): {', '.join(missing)}"))
    duplicated = sorted({c for c in columns if columns.count(c) > 1})
    if duplicated:
        errors.append(RowError(None, f"Duplicate column(s): {', '.join(duplicated)}"))
    if errors:
        raise CSVValidationError(errors)
    return columns


def _parse_rows(reader, columns: List[str], max_rows: int):
    rows: List[HospitalRow] = []
    errors: List[RowError] = []
    seen = {}  # identity -> first row number, to reject duplicate rows

    row_number = 0
    for fields in reader:
        if not any(f.strip() for f in fields):
            continue  # tolerate blank lines, e.g. a trailing newline
        row_number += 1
        if row_number > max_rows:
            # Stop instead of parsing the rest: the file is rejected regardless.
            return rows, [RowError(None, f"Too many rows: maximum is {max_rows} hospitals")]

        # Allow a row to leave off trailing optional columns ("A,1 St" under
        # a name,address,phone header) since phone is optional.
        if len(fields) < len(columns) and all(
            c in OPTIONAL_COLUMNS for c in columns[len(fields):]
        ):
            fields = fields + [""] * (len(columns) - len(fields))

        if len(fields) != len(columns):
            errors.append(RowError(
                row_number, f"Expected {len(columns)} fields, found {len(fields)}"
            ))
            continue

        values = {col: val.strip() for col, val in zip(columns, fields)}
        row_errors = [
            RowError(row_number, f"'{col}' is required")
            for col in REQUIRED_COLUMNS
            if not values[col]
        ]
        if row_errors:
            errors.extend(row_errors)
            continue

        hospital = HospitalRow(
            row=row_number,
            name=values["name"],
            address=values["address"],
            phone=values.get("phone") or None,
        )
        first = seen.setdefault(hospital.identity(), row_number)
        if first != row_number:
            errors.append(RowError(row_number, f"Duplicate of row {first}"))
            continue
        rows.append(hospital)

    return rows, errors
