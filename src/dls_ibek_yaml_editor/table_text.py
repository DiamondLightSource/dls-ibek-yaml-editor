"""Pure text operations shared by table actions and their tests."""

import csv
import io
import json
import re

CELLS_MIME = "application/x-dls-ibek-cells+json"


def encode_tsv(rows: list[list[str]]) -> str:
    """Spreadsheet text: quote fields containing tabs, newlines or quotes."""
    stream = io.StringIO(newline="")
    csv.writer(stream, delimiter="\t", lineterminator="\n").writerows(rows)
    return stream.getvalue()


def decode_cells(payload: bytes, text: str) -> list[list[str]]:
    """Prefer lossless internal data, otherwise read quoted spreadsheet TSV.

    A final newline terminates a row; an additional newline is an empty row.
    Backslashes are always literal. Empty clipboard text clears one cell.
    """
    if payload:
        try:
            rows = json.loads(payload)
            if (
                isinstance(rows, list)
                and rows
                and all(
                    isinstance(row, list)
                    and row
                    and all(isinstance(v, str) for v in row)
                    for row in rows
                )
            ):
                return rows
        except (ValueError, UnicodeError):
            pass
    rows = list(csv.reader(io.StringIO(text, newline=""), delimiter="\t"))
    return [row or [""] for row in rows] or [[""]]


def increment_text(text: str, number: int) -> str:
    """Increment the last number in ``text`` by ``number``, keeping its width."""
    match = re.search(r"[^\d]*(\d+)[^\d]*$", text)
    if not match:
        return text
    digits = match.group(1)
    width = len(digits)
    return (
        text[: match.start(1)]
        + f"{int(digits) + number:0{width}d}"
        + text[match.end(1) :]
    )
