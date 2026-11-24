import io

import pytest

from app.csv_parser import CSVValidationError, HospitalRow, parse_hospital_csv


def parse(text: str, max_rows: int = 20):
    return parse_hospital_csv(io.BytesIO(text.encode("utf-8")), max_rows=max_rows)


def errors_of(text: str, max_rows: int = 20):
    with pytest.raises(CSVValidationError) as exc_info:
        parse(text, max_rows)
    return [(e.row, e.message) for e in exc_info.value.errors]


def test_parses_valid_rows_and_optional_phone():
    rows = parse(
        "name,address,phone\n"
        "General Hospital,1 Main St,555-1234\n"
        "City Clinic,2 Side St,\n"
    )
    assert rows == [
        HospitalRow(1, "General Hospital", "1 Main St", "555-1234"),
        HospitalRow(2, "City Clinic", "2 Side St", None),
    ]


def test_phone_column_may_be_omitted_entirely():
    rows = parse("name,address\nA,1 St\n")
    assert rows == [HospitalRow(1, "A", "1 St", None)]


def test_header_is_case_and_whitespace_insensitive_and_column_order_free():
    rows = parse(" Address , NAME ,Phone\n1 St,A,555\n")
    assert rows == [HospitalRow(1, "A", "1 St", "555")]


def test_values_are_trimmed_and_quoted_commas_kept():
    rows = parse('name,address\n  A  ,"1 Main St, Suite 2"\n')
    assert rows[0].name == "A"
    assert rows[0].address == "1 Main St, Suite 2"


def test_utf8_bom_is_ignored():
    rows = parse_hospital_csv(io.BytesIO(b"\xef\xbb\xbfname,address\nA,1 St\n"), max_rows=20)
    assert rows[0].name == "A"


def test_blank_lines_are_skipped_and_do_not_count_as_rows():
    rows = parse("name,address\n\nA,1 St\n\n,\nB,2 St\n")
    assert [(r.row, r.name) for r in rows] == [(1, "A"), (2, "B")]


def test_missing_required_values_are_reported_per_row():
    assert errors_of("name,address\n,1 St\nB,\nC,3 St\n") == [
        (1, "'name' is required"),
        (2, "'address' is required"),
    ]


def test_missing_required_column():
    assert errors_of("name,phone\nA,555\n") == [
        (None, "Missing required column(s): address"),
    ]


def test_unknown_and_duplicate_columns():
    errs = errors_of("name,address,adress,name\nA,1,2,3\n")
    assert (None, "Unknown column(s): adress") in errs
    assert (None, "Duplicate column(s): name") in errs


def test_wrong_field_count():
    assert errors_of("name,address\nA,1 St,extra\nB\n") == [
        (1, "Expected 2 fields, found 3"),
        (2, "Expected 2 fields, found 1"),
    ]


def test_trailing_optional_phone_may_be_left_off():
    rows = parse("name,address,phone\nA,1 St\n")
    assert rows == [HospitalRow(1, "A", "1 St", None)]


def test_short_row_missing_required_column_rejected():
    assert errors_of("name,phone,address\nA,555\n") == [
        (1, "Expected 3 fields, found 2"),
    ]


def test_duplicate_rows_rejected():
    # Duplicates would make it impossible to tell which create succeeded
    # when reconciling after a timeout.
    assert errors_of("name,address,phone\nA,1 St,5\nB,2 St,\nA, 1 St ,5\n") == [
        (3, "Duplicate of row 1"),
    ]


def test_same_name_different_address_is_not_a_duplicate():
    rows = parse("name,address\nA,1 St\nA,2 St\n")
    assert len(rows) == 2


def test_max_rows_is_enforced():
    body = "".join(f"H{i},{i} St\n" for i in range(21))
    assert errors_of("name,address\n" + body) == [
        (None, "Too many rows: maximum is 20 hospitals"),
    ]


def test_exactly_max_rows_is_accepted():
    body = "".join(f"H{i},{i} St\n" for i in range(20))
    assert len(parse("name,address\n" + body)) == 20


@pytest.mark.parametrize("text, message", [
    ("", "File is empty"),
    ("name,address\n", "File contains no hospital rows"),
    ("name,address\n\n\n", "File contains no hospital rows"),
])
def test_empty_inputs(text, message):
    assert errors_of(text) == [(None, message)]


def test_non_utf8_rejected():
    with pytest.raises(CSVValidationError) as exc_info:
        parse_hospital_csv(io.BytesIO("name,address\nCafé,1 St\n".encode("latin-1")), max_rows=20)
    assert exc_info.value.errors[0].message == "File must be UTF-8 encoded text"


def test_unclosed_quote_rejected():
    errs = errors_of('name,address\n"A,1 St\nB,2 St\n')
    assert errs[0][0] is None
    assert errs[0][1].startswith("Malformed CSV")


class CountingStream(io.RawIOBase):
    """An endless CSV that records how many bytes have been read from it."""

    def __init__(self):
        self.bytes_read = 0
        self._buffer = b"name,address\n"
        self._next_row = 0

    def readable(self):
        return True

    def readinto(self, b):
        while len(self._buffer) < len(b):
            self._buffer += f"H{self._next_row},{self._next_row} St\n".encode()
            self._next_row += 1
        n = len(b)
        b[:n], self._buffer = self._buffer[:n], self._buffer[n:]
        self.bytes_read += n
        return n


def test_row_limit_is_enforced_while_reading_not_after():
    stream = CountingStream()
    with pytest.raises(CSVValidationError) as exc_info:
        parse_hospital_csv(io.BufferedReader(stream), max_rows=20)
    assert exc_info.value.errors[0].message == "Too many rows: maximum is 20 hospitals"
    # Only a buffer or two was read from a stream that never ends.
    assert stream.bytes_read <= 64 * 1024


def test_caller_stream_is_left_open():
    stream = io.BytesIO(b"name,address\nA,1 St\n")
    parse_hospital_csv(stream, max_rows=20)
    assert not stream.closed
