"""processor 공통 HTML/Markdown 표 행 분할기의 순수 단위 테스트."""

from dataclasses import dataclass

import pytest

from genon.preprocessor.facade.chunking.table_splitter import (
    ROW_LINES_LABEL,
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
    # docling grid 는 병합 셀을 피복 위치마다 복제하고, 각 사본이 시작 행을 가리킨다.
    start_row_offset_idx: int = 0
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
    # `<` 와 `&` 만 이스케이프한다. `>` 는 HTML5 텍스트 노드에서 이스케이프할 의무가 없고,
    # 바꿔 두면 청크 텍스트에 `&gt;` 노이즈만 남는다(실측 — `일반결제 &gt; 삼성페이`).
    assert any("&lt;value>" in piece for piece in result.pieces)
    assert all("&gt;" not in piece for piece in result.pieces)


@pytest.mark.unit
def test_rowspan_is_normalized_instead_of_giving_up_on_split():
    """rowspan 이 있어도 조각을 낸다.

    예전에는 여기서 원문을 그대로 돌려줘, 예산을 크게 넘는 청크가 벡터로 나갔다.
    """
    grid = _grid(rows=3)
    grid[1][0].row_span = 2
    result = split_table_rows(
        grid=grid, num_cols=2, single_text="X" * 2000,
        limit=300, count_text=len, table_format="html", header_row_count=1,
    )
    assert result.did_split and result.normalized_spans
    assert result.reason is None
    assert all(piece.count("<table>") == 1 for piece in result.pieces)


@pytest.mark.unit
def test_row_label_table_is_split_not_treated_as_all_header():
    """행 라벨 `<th>` 표도 분할된다.

    헤더 행 수를 `any(is_header_cell)` 로 세던 시절에는 모든 행이 헤더로 집계돼
    `header_row_count >= len(grid)` 가 되고, 분할을 포기해(`no-data-rows`) 예산을
    넘는 표가 통째로 나갔다.
    """
    grid = _grid(rows=6)
    for row in grid[1:]:
        row[0].row_header = True
    result = split_table_rows(
        grid=grid, num_cols=2, single_text="X" * 3000, limit=420,
        count_text=len, table_format="html",
        header_row_count=leading_header_row_count(grid),
    )
    assert result.did_split and result.reason is None
    assert all("<th>번호</th><th>내용</th>" in piece for piece in result.pieces)
    # 행 라벨은 데이터 행으로 남아야 한다 - 헤더로 반복되면 값이 조각마다 중복된다.
    assert sum(piece.count("ROW-01-START") for piece in result.pieces) == 1


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


# ─── 다중 헤더행 markdown (결함 회귀 고정) ─────────────────────────────────────

def _two_header_grid(rows=6, payload_size=90):
    """헤더가 두 행인 표. colspan 상위 헤더 + 하위 헤더 구성."""
    grid = [
        [Cell("구분", 0, column_header=True),
         Cell("금리", 1, col_span=2, column_header=True),
         Cell("금리", 1, col_span=2, column_header=True)],
        [Cell("연도", 0, column_header=True),
         Cell("기본", 1, column_header=True),
         Cell("추가", 2, column_header=True)],
    ]
    for n in range(1, rows + 1):
        grid.append([
            Cell(f"ROW-{n:02d}-START " + ("가" * payload_size) + f" ROW-{n:02d}-END", 0),
            Cell(f"{n}.0", 1),
            Cell(f"0.{n}", 2),
        ])
    return grid


@pytest.mark.unit
def test_multi_header_markdown_piece_has_exactly_one_header_row_then_separator():
    """헤더가 여러 행이어도 markdown 표로 파싱되는 조각이 나와야 한다.

    예전에는 헤더 행을 전부 늘어놓고 뒤에 구분선을 붙여, 구분선이 2번째 줄이 아니라
    표로 인식되지 않는 텍스트가 나왔다.
    """
    grid = _two_header_grid()
    result = split_table_rows(
        grid=grid, num_cols=3, single_text="X" * 3000, limit=400,
        count_text=len, table_format="markdown", header_row_count=2,
    )

    assert result.did_split
    for piece in result.pieces:
        lines = [line for line in piece.splitlines() if line.strip()]
        assert set(lines[1].replace("|", "").replace(" ", "")) <= {"-", ":"}
        # 계층은 사라지지 않고 한 라벨로 합쳐진다.
        assert "금리 > 기본" in lines[0] and "금리 > 추가" in lines[0]
        assert len({line.count("|") for line in lines}) == 1


# ─── rowspan 분할 (결함 회귀 고정) ─────────────────────────────────────────────

def _rowspan_grid(groups=3, per_group=4, payload_size=90):
    """분류 열이 rowspan 으로 묶인 표. docling grid 처럼 값이 각 행에 복제된 상태."""
    grid = [[Cell("분류", 0, column_header=True), Cell("내용", 1, column_header=True)]]
    n = 0
    for g in range(groups):
        for _ in range(per_group):
            n += 1
            grid.append([
                Cell(f"CAT-{g}", 0, row_span=per_group),
                Cell(f"ROW-{n:02d}-START " + ("가" * payload_size) + f" ROW-{n:02d}-END", 1),
            ])
    return grid


@pytest.mark.unit
@pytest.mark.parametrize("table_format", ["html", "markdown"])
def test_rowspan_table_is_split_after_normalizing_spans(table_format):
    """rowspan 이 있어도 분할한다. 예전에는 포기해서 초과 청크가 그대로 나갔다."""
    grid = _rowspan_grid()
    result = split_table_rows(
        grid=grid, num_cols=2, single_text="X" * 5000, limit=500,
        count_text=len, table_format=table_format, header_row_count=1,
    )

    assert result.did_split and result.normalized_spans
    assert result.reason is None
    assert not result.oversized_piece_indexes
    assert all(len(piece) <= 500 for piece in result.pieces)
    # 병합 값은 걸친 모든 조각에 복제되어 남는다.
    for group in range(3):
        assert any(f"CAT-{group}" in piece for piece in result.pieces)


@pytest.mark.unit
def test_normalized_spans_is_false_when_no_rowspan():
    grid = _grid(rows=6)
    result = split_table_rows(
        grid=grid, num_cols=2, single_text="X" * 3000, limit=400,
        count_text=len, table_format="html", header_row_count=1,
    )
    assert result.did_split and not result.normalized_spans


# ─── 행 직렬화 ────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_row_serialization_appends_only_that_piece_rows_within_budget():
    grid = _two_header_grid(rows=6, payload_size=40)
    result = split_table_rows(
        grid=grid, num_cols=3, single_text="X" * 3000, limit=600,
        count_text=len, table_format="html", header_row_count=2,
        row_serialization=True,
    )

    assert result.did_split
    assert all(len(piece) <= 600 for piece in result.pieces)
    for piece in result.pieces:
        assert "[표 행 요약]" in piece
        assert "금리 > 기본=" in piece
        # 조각에 없는 행의 문장이 새어 들어오지 않는다.
        for n in range(1, 7):
            marker = f"ROW-{n:02d}-START"
            body, _, summary = piece.partition("[표 행 요약]")
            assert (marker in summary) == (marker in body)


@pytest.mark.unit
def test_row_serialization_off_by_default():
    grid = _grid(rows=6)
    result = split_table_rows(
        grid=grid, num_cols=2, single_text="X" * 3000, limit=400,
        count_text=len, table_format="html", header_row_count=1,
    )
    assert all("[표 행 요약]" not in piece for piece in result.pieces)


@pytest.mark.unit
def test_render_table_emits_rowspan_once_instead_of_repeating_the_value():
    """docling grid 는 병합 셀을 피복 위치마다 복제해 둔다.

    전체 표를 렌더할 때 그 복제를 그대로 내면 같은 값이 행마다 반복된다. 시작 행에서만
    ``rowspan`` 과 함께 내고 이어지는 행은 건너뛴다.
    """
    from genon.preprocessor.facade.chunking.table_html import render_table

    merged = Cell("연회비", 0, row_span=2, start_row_offset_idx=1)
    grid = [
        [Cell("구분", 0, column_header=True), Cell("값", 1, column_header=True)],
        [merged, Cell("20,000원", 1, start_row_offset_idx=1)],
        [merged, Cell("18,000원", 1, start_row_offset_idx=2)],
    ]
    html = render_table(grid, 2)

    assert html.count("연회비") == 1
    assert 'rowspan="2"' in html
    assert "20,000원" in html and "18,000원" in html


# ─── 표기형태별 조각(#360 text_table_html / text_table_md) ─────────────────────


@pytest.mark.unit
@pytest.mark.parametrize("table_format", ["html", "markdown"])
def test_extra_formats_share_row_boundaries_with_primary(table_format):
    """형식별로 따로 분할하면 텍스트 길이가 달라 조각 경계가 어긋난다.

    행 묶음은 primary 로 한 번만 계산되어야 조각이 1:1 로 대응한다.
    """
    grid = _grid()
    result = split_table_rows(
        grid=grid, num_cols=2, single_text="X" * 3000, limit=420, count_text=len,
        table_format=table_format, header_row_count=leading_header_row_count(grid),
        extra_formats=("html", "markdown"),
    )

    assert result.did_split
    assert len(result.buckets) == len(result.pieces)
    for fmt in ("html", "markdown"):
        assert len(result.format_pieces[fmt]) == len(result.pieces)
    # 같은 조각은 형식이 달라도 같은 행을 담는다.
    for index in range(len(result.pieces)):
        for row_index in result.buckets[index]:
            marker = f"ROW-{row_index + 1:02d}-END"
            assert marker in result.format_pieces["html"][index]
            assert marker in result.format_pieces["markdown"][index]
    # 요청한 형식이 primary 와 같으면 primary 조각을 그대로 준다.
    assert result.format_pieces[table_format] == result.pieces


@pytest.mark.unit
def test_primary_pieces_are_unchanged_when_extra_formats_requested():
    """추가 형식 요청이 primary 산출물을 건드리면 이 기능이 회귀를 만든다."""
    grid = _grid()
    common = dict(
        grid=grid, num_cols=2, single_text="X" * 3000, limit=420, count_text=len,
        table_format="html", header_row_count=leading_header_row_count(grid),
    )
    base = split_table_rows(**common)
    with_extra = split_table_rows(**common, extra_formats=("markdown",))

    assert with_extra.pieces == base.pieces
    assert with_extra.oversized_piece_indexes == base.oversized_piece_indexes
    assert with_extra.did_split == base.did_split


@pytest.mark.unit
def test_extra_formats_repeat_prefix_and_keep_suffix_on_last_piece():
    grid = _grid()
    result = split_table_rows(
        grid=grid, num_cols=2, single_text="X" * 3000, limit=460, count_text=len,
        table_format="html", header_row_count=leading_header_row_count(grid),
        prefix="캡션 문장\n", suffix="\n---\n[표 설명]\n요약",
        extra_formats=("markdown",),
    )

    pieces = result.format_pieces["markdown"]
    assert len(pieces) > 1
    assert all(piece.startswith("캡션 문장\n") for piece in pieces)
    assert pieces[-1].endswith("요약")
    assert not any(piece.endswith("요약") for piece in pieces[:-1])
    # 접미 위치가 primary 와 같아야 조각 대응이 유지된다.
    assert result.pieces[-1].endswith("요약")


@pytest.mark.unit
def test_extra_format_row_lines_match_primary():
    """행 문장은 표기형태와 무관하다. 형식마다 다시 만들면 값이 갈릴 수 있다."""
    grid = _grid(rows=6)
    result = split_table_rows(
        grid=grid, num_cols=2, single_text="X" * 3000, limit=520, count_text=len,
        table_format="html", header_row_count=leading_header_row_count(grid),
        row_serialization=True, extra_formats=("markdown",),
    )

    for primary, variant in zip(result.pieces, result.format_pieces["markdown"]):
        label = ROW_LINES_LABEL + "\n"
        assert label in primary and label in variant
        assert primary.split(label, 1)[1] == variant.split(label, 1)[1]


@pytest.mark.unit
def test_markdown_variant_is_omitted_when_header_row_is_missing():
    """markdown 표는 헤더 행이 있어야 만들 수 있다. 못 만들면 호출부가 primary 로 폴백한다."""
    grid = _grid()
    result = split_table_rows(
        grid=grid, num_cols=2, single_text="X" * 3000, limit=420, count_text=len,
        table_format="html", header_row_count=0, extra_formats=("markdown",),
    )

    assert result.did_split
    assert "markdown" not in result.format_pieces
