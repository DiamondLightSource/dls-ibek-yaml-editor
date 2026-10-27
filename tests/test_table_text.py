import json

import pytest

from dls_ibek_yaml_editor.table_text import decode_cells, encode_tsv, increment_text


@pytest.mark.parametrize(
    "rows",
    [
        [[r"C:\new\test", "line\nnext", "tab\there", 'a "quote"']],
        [["a", ""], ["", ""], [""]],
        [["α", "β"], ["odd width"]],
    ],
)
def test_internal_and_spreadsheet_round_trip(rows: list[list[str]]):
    assert decode_cells(json.dumps(rows).encode(), "ignored") == rows
    assert decode_cells(b"", encode_tsv(rows)) == rows


def test_external_text_does_not_decode_backslashes():
    assert decode_cells(b"", r"C:\new\test") == [[r"C:\new\test"]]
    assert decode_cells(b"", "one\n\n") == [["one"], [""]]
    assert decode_cells(b"invalid", "1\t2\r\n3\t4\r\n") == [["1", "2"], ["3", "4"]]


def test_increment_padding_and_suffix():
    assert increment_text("MOT.APTR03X", 2) == "MOT.APTR05X"
    assert increment_text("nothing", 2) == "nothing"
