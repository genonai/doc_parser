"""processor 공통 HTML/Markdown 표 행 분할기의 순수 단위 테스트."""

from dataclasses import dataclass

import pytest

from genon.preprocessor.facade.chunking.table_splitter import (
    leading_header_row_count,
    split_entries_preserving_tables,
    split_table_rows,
)


@dataclass
class Cell:
    text: str
    start_col_offset_idx: int
    col_span: int = 1
    row_span: int = 1
    column_header: bool = False
    row_header: bool = False
    row_section: bool = False


def _grid(rows=10, payload_size=90):
    grid = [[
        Cell("번호", 0, column_header=True),
        Cell("내용", 1, column_header=True),
    ]]
    for n in range(1, rows + 1):
        grid.append([
            Cell(f"ROW-{n:02d}", 0),
            Cell(f"ROW-{n:02d}-START " + ("가" * payload_size) + f" ROW-{n:02d}-END", 1),
        ])
    return grid


@pytest.mark.unit
@pytest.mark.parametrize("table_format", ["html", "markdown"])
def test_large_table_is_split_by_complete_rows_with_repeated_header(table_format):
    grid = _grid()
    result = split_table_rows(
        grid=grid,
        num_cols=2,
        single_text="X" * 3000,
        limit=420,
        count_text=len,
        table_format=table_format,
        header_row_count=leading_header_row_count(grid),
    )

    assert result.did_split
    assert len(result.pieces) > 1
    assert not result.oversized_piece_indexes
    for piece in result.pieces:
        assert len(piece) <= 420
        if table_format == "html":
            assert piece.count("<table>") == piece.count("</table>") == 1
            assert piece.count("<tr>") == piece.count("</tr>")
            assert "<th>번호</th><th>내용</th>" in piece
        else:
            assert piece.startswith("| 번호 | 내용 |\n| --- | --- |")

    for n in range(1, 11):
        marker = f"ROW-{n:02d}"
        containing = [i for i, piece in enumerate(result.pieces) if f"{marker}-START" in piece]
        ending = [i for i, piece in enumerate(result.pieces) if f"{marker}-END" in piece]
        assert containing == ending and len(containing) == 1


@pytest.mark.unit
def test_html_escapes_cells_and_preserves_colspan():
    grid = [
        [Cell("A&B", 0, col_span=2, column_header=True), Cell("A&B", 0, column_header=True)],
        [Cell("<value>", 0), Cell("ok", 1)],
        [Cell("tail", 0), Cell("done", 1)],
    ]
    result = split_table_rows(
        grid=grid, num_cols=2, single_text="X" * 500, limit=130,
        count_text=len, table_format="html", header_row_count=1,
    )
    assert all('<th colspan="2">A&amp;B</th>' in piece for piece in result.pieces)
    assert any("&lt;value&gt;" in piece for piece in result.pieces)


@pytest.mark.unit
def test_rowspan_falls_back_without_breaking_original_table():
    grid = _grid(rows=3)
    grid[1][0].row_span = 2
    original = "<table>original-rowspan-table</table>"
    result = split_table_rows(
        grid=grid, num_cols=2, single_text=original, limit=10,
        count_text=len, table_format="html", header_row_count=1,
    )
    assert result.pieces == [original]
    assert not result.did_split
    assert result.reason == "rowspan"


@pytest.mark.unit
def test_single_oversized_row_is_reported_but_kept_complete():
    grid = _grid(rows=1, payload_size=500)
    result = split_table_rows(
        grid=grid, num_cols=2, single_text="X" * 1000, limit=100,
        count_text=len, table_format="html", header_row_count=1,
    )
    assert result.oversized_piece_indexes == (0,)
    assert result.pieces[0].count("<tr>") == result.pieces[0].count("</tr>") == 2
    assert "ROW-01-START" in result.pieces[0] and "ROW-01-END" in result.pieces[0]


@pytest.mark.unit
@pytest.mark.parametrize("table_format", ["html", "markdown"])
def test_rag_context_prefix_is_repeated_and_counted_in_every_piece(table_format):
    grid = _grid(rows=8, payload_size=70)
    prefix = "[표 검색 설명]\n2026년 센터별 장애 건수와 평균 복구시간\n"
    result = split_table_rows(
        grid=grid,
        num_cols=2,
        single_text=prefix + ("X" * 2000),
        limit=360,
        count_text=len,
        table_format=table_format,
        header_row_count=1,
        prefix=prefix,
    )

    assert result.did_split
    assert all(piece.startswith(prefix) for piece in result.pieces)
    assert all(len(piece) <= 360 for piece in result.pieces)


@pytest.mark.unit
def test_entry_splitter_routes_only_oversized_table_to_table_callback():
    normal_a = ("normal-a",)
    table = ("table",)
    normal_b = ("normal-b",)
    table_calls = []

    def render(entries):
        return "\n".join(entry[0] * (20 if entry[0] == "table" else 1) for entry in entries)

    parts = split_entries_preserving_tables(
        item_groups=[[normal_a], [table], [normal_b]],
        budget=30,
        is_table_entry=lambda entry: entry[0] == "table",
        render_entries=render,
        count_text=len,
        split_plain_text=lambda text, budget: [text],
        split_table_entry=lambda entry, budget: table_calls.append((entry, budget)) or ["T1", "T2"],
    )

    assert table_calls == [(table, 30)]
    assert parts == [
        ("normal-a", [normal_a]),
        ("T1", [table]),
        ("T2", [table]),
        ("normal-b", [normal_b]),
    ]
