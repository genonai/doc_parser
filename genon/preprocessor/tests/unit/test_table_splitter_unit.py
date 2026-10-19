"""processor 공통 HTML/Markdown 표 행 분할기의 순수 단위 테스트."""

import re
from dataclasses import dataclass
from pathlib import Path

import pytest

from genon.preprocessor.processing.chunking.table_splitter import (
    ROW_CONTINUED_LABEL,
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


# ─── colspan 병합 셀 (#539, monimo_product_slf_span_table_sample.md) ───────────

def _spanned(text, start, span, **flags):
    """docling grid 처럼 병합 셀 하나를 피복 컬럼마다 같은 객체로 채운다."""
    cell = Cell(text, start, col_span=span, **flags)
    return [cell] * span


_CLAUSE = "㈜ 1. 보험료 납입기간 중 주계약의 보험료 납입 면제 사유가 발생하였을 경우 " + "가" * 200


@pytest.mark.unit
def test_tbody_colspan_clause_is_written_once_in_table_and_row_summary():
    """11쪽 표: tbody 전체 폭 조항(colspan=3)이 표에 3회, 행 요약에 3회 실려 조각이 한도를 넘었다."""
    grid = [
        [Cell("특약명", 0, column_header=True), Cell("지급사유", 1, column_header=True),
         Cell("지급금액", 2, column_header=True)],
        _spanned(_CLAUSE, 0, 3),
    ]
    for n in range(1, 4):
        grid.append([Cell("3대안과질환 수술보장특약", 0, row_span=3),
                     Cell(f"ROW-{n:02d}-START □ 수술보험금 " + "나" * 80, 1), Cell("50만원", 2)])
    grid.append(_spanned(_CLAUSE, 0, 3))
    result = split_table_rows(
        grid=grid, num_cols=3, single_text="X" * 3000, limit=700, count_text=len,
        table_format="markdown", header_row_count=1, row_serialization=True,
    )

    assert result.did_split and not result.oversized_piece_indexes
    clause_pieces = [piece for piece in result.pieces if _CLAUSE in piece]
    assert len(clause_pieces) == 2
    for piece in clause_pieces:
        body, _, summary = piece.partition(ROW_LINES_LABEL)
        assert (body.count(_CLAUSE), summary.count(_CLAUSE)) == (1, 1)
        assert f"| {_CLAUSE} |  |  |" in body
    # rowspan 값은 조각 맥락을 위해 행마다 그대로 복제된다.
    assert "\n".join(result.pieces).count("특약명=3대안과질환 수술보장특약") == 3


@pytest.mark.unit
@pytest.mark.parametrize("header, data_cols", [
    # 13쪽 표: thead 의 유일한 셀이 전체 폭(colspan=4) 조항이다.
    (_spanned(_CLAUSE, 0, 4, column_header=True), 4),
    # 6쪽 표: 두 번째 헤더 셀이 나머지 두 컬럼을 덮는 긴 설명(colspan=2)이다.
    ([Cell("보존치료 보험금", 0, column_header=True), *_spanned(_CLAUSE, 1, 2, column_header=True)], 3),
], ids=["full-width", "partial-width"])
def test_merged_header_without_sub_header_is_not_repeated_as_column_label(header, data_cols):
    """하위 헤더 없는 긴 병합 헤더가 모든 컬럼 라벨이 되어 표 헤더와 행 요약에 컬럼 수만큼 실렸다."""
    grid = [header]
    for n in range(1, 5):
        grid.append([Cell(f"ROW-{n:02d}-START", 0),
                     *(Cell(f"값{n}-{c}", c) for c in range(1, data_cols))])
    result = split_table_rows(
        grid=grid, num_cols=data_cols, single_text="X" * 3000, limit=500, count_text=len,
        table_format="markdown", header_row_count=1, row_serialization=True,
    )

    assert result.did_split
    for piece in result.pieces:
        body, _, summary = piece.partition(ROW_LINES_LABEL)
        # 표 헤더에 한 번 남고 행 요약 라벨로는 쓰이지 않는다.
        assert (body.count(_CLAUSE), summary.count(_CLAUSE)) == (1, 0)


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
def test_blank_data_rows_are_dropped_before_split():
    """OCR 레이아웃 표(상품설명서 116쪽 캡쳐): 빈 thead 아래 빈 행이 데이터 행 사이와 표 끝에 있다.

    빈 행이 분할 단위로 남으면 표 끝의 빈 행이 홀로 조각이 되어 `설명 + 빈 헤더 + 빈 행` 만 담긴
    청크가 나오고, 행 문장(빈 행을 건너뛴다)이 다른 조각의 행에 붙는다.
    """
    blank = lambda: [Cell("", c) for c in range(4)]
    grid = [[Cell("", c, column_header=True) for c in range(4)],
            [Cell("보장명", 0), Cell("", 1), Cell("지급사유", 2), Cell("지급금액", 3)], blank()]
    for n in range(1, 9):
        grid.append([Cell("", 0), Cell(f"ROW-{n:02d}-START", 1),
                     Cell("가" * 40, 2), Cell(f"가입금액의 {n}%", 3)])
        grid.append(blank())
    grid.append(blank())
    result = split_table_rows(
        grid=grid, num_cols=4, single_text="X" * 3000, limit=400, count_text=len,
        table_format="markdown", header_row_count=1,
        prefix="[표 검색 설명]\n특정치료비 보장 내역이다.\n", row_serialization=True,
    )

    assert result.did_split
    for piece in result.pieces:
        body, _, summary = piece.partition(ROW_LINES_LABEL)
        rows = body.strip().splitlines()[4:]      # 설명 라벨·설명·헤더·구분선 다음이 데이터 행
        assert rows and all(row.replace("|", "").strip() for row in rows)
        for n in range(1, 9):
            marker = f"ROW-{n:02d}-START"
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
    from genon.preprocessor.processing.chunking.table_html import render_table

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


# ─── 긴 행 조각의 한도 초과 해소 (#548, monimo_product_slf_span_table_sample.md 13쪽) ─────

_SAMPLE_PAGE13 = (Path(__file__).resolve().parents[2] / "sample_files" / "monimo"
                  / "monimo_product_slf_span_table_sample.md").read_text(encoding="utf-8")
_SAMPLE_PAGE13 = _SAMPLE_PAGE13[_SAMPLE_PAGE13.index("<!-- source_page: 13 -->"):]


def _page13_cell(pattern):
    """샘플 13쪽 표의 셀 원문. ``<br>`` 은 docling 셀 텍스트처럼 줄바꿈이 된다."""
    return re.search(pattern, _SAMPLE_PAGE13, re.S).group(1).replace("<br>", "\n")


_P13_THEAD = _page13_cell(r'<th colspan="4">(.*?)</th>')
_P13_TBODY = _page13_cell(r'<td colspan="4">(.*?)</td>')
_P13_REASON = _page13_cell(r'<td>(□ 소액질병진단보험금.*?)</td>')
_P13_CONTEXT = "[표 검색 설명]\n소액질병보장특약 VII D 의 소액질병진단보험금 지급 내역을 비교한다.\n"


def _page13_grid():
    """13쪽 표: thead 전체 폭 조항, 상품 행, tbody 전체 폭 조항 한 행, 상품 행."""
    product = [Cell("소액질병보장특약VII", 0), Cell(_P13_REASON, 1), Cell("150만원", 2), Cell("300만원", 3)]
    return [
        _spanned(_P13_THEAD, 0, 4, column_header=True),
        [Cell("특약명", 0), Cell("지급사유", 1), *_spanned("지급금액", 2, 2)],
        product,
        _spanned(_P13_TBODY, 0, 4),
        list(product),
    ]


def _joined_cells(pieces):
    """markdown 조각의 데이터 행 셀을 모은다. 이어지는 조각의 첫 행은 앞 행에 셀별로 잇는다."""
    rows: list[list[str]] = []
    for piece in pieces:
        body = piece.partition(ROW_LINES_LABEL)[0]
        lines = [line for line in body.split("\n") if line.startswith("| ")][2:]
        cells = [line[2:-2].split(" | ") for line in lines]
        if ROW_CONTINUED_LABEL in piece and rows and cells:
            rows[-1] = [a + b for a, b in zip(rows[-1], cells.pop(0))]
        rows.extend(cells)
    return rows


def _squash(text):
    return re.sub(r"\s+", "", text)


@pytest.mark.unit
@pytest.mark.parametrize("limit, stage", [
    (1500, "summary"),       # 라벨 없는 행 요약 줄만 빼면 든다
    (1400, "description"),   # 반복 검색 설명까지 빼야 든다
    (1200, "cells"),         # 긴 셀을 나눠 행을 이어 써야 든다
])
def test_long_row_piece_is_reduced_in_order_until_it_fits(limit, stage):
    """13쪽 tbody 조항 한 행 조각이 행 요약 중복과 반복 설명을 실은 채 한도를 넘었다."""
    grid = _page13_grid()
    kwargs = dict(grid=grid, num_cols=4, single_text="X" * 9000, limit=limit, count_text=len,
                  table_format="markdown", header_row_count=1, prefix=_P13_CONTEXT,
                  row_serialization=True, extra_formats=("html",))
    before = split_table_rows(**kwargs)
    result = split_table_rows(**kwargs, compact_prefix="", split_long_cells=True)

    assert not result.oversized_piece_indexes
    assert all(len(piece) <= limit for piece in result.pieces)
    # 한도 안에 들던 조각은 그대로다.
    assert result.pieces[0] == before.pieces[0] and result.pieces[-1] == before.pieces[-1]
    clause = [piece for piece, bucket in zip(result.pieces, result.buckets) if bucket == (2,)]
    assert all(ROW_LINES_LABEL not in piece for piece in clause)
    assert all(piece.startswith(_P13_CONTEXT) for piece in clause) == (stage == "summary")
    assert (len(clause) > 1) == (stage == "cells")
    assert _squash(_joined_cells(result.pieces)[2][0]) == _squash(_P13_TBODY)
    # 추가 형식도 같은 조각 계획으로 렌더한다.
    assert len(result.format_pieces["html"]) == len(result.pieces)


@pytest.mark.unit
@pytest.mark.parametrize("row, limit", [
    # 경계(공백·줄바꿈)가 없는 긴 문자열은 문자 위치에서 자른다.
    ([*_spanned(_squash(_P13_TBODY), 0, 4)], 300),
    # 한 행에 긴 셀이 둘이고 첫 셀은 rowspan 복제 셀이다.
    ([Cell("소액질병보장특약VII", 0, row_span=2), Cell(_P13_THEAD, 1), Cell(_P13_TBODY, 2),
      Cell("150만원", 3)], 400),
], ids=["no-boundary", "two-long-cells-with-rowspan"])
def test_long_cells_are_split_into_continued_rows_that_rebuild_the_cells(row, limit):
    grid = [[Cell(name, c, column_header=True) for c, name in enumerate(("특약명", "지급사유", "지급금액", "비고"))],
            row, [Cell("소액질병보장특약VII", 0, row_span=2, start_row_offset_idx=1), Cell("-", 1),
                  Cell("50만원", 2), Cell("100만원", 3)]]
    result = split_table_rows(
        grid=grid, num_cols=4, single_text="X" * 9000, limit=limit, count_text=len,
        table_format="markdown", header_row_count=1, split_long_cells=True,
    )

    assert not result.oversized_piece_indexes
    assert all(len(piece) <= limit for piece in result.pieces)
    continued = [ROW_CONTINUED_LABEL in piece for piece in result.pieces]
    assert continued[0] is False and any(continued)
    rebuilt = _joined_cells(result.pieces)[0]
    expected = ["" if cell is not None and cell.start_col_offset_idx != column else cell.text
                for column, cell in enumerate(row)]
    assert [_squash(text) for text in rebuilt] == [_squash(text) for text in expected]


@pytest.mark.unit
def test_row_is_kept_whole_when_header_alone_exceeds_limit():
    """긴 헤더 조항은 자르지 않는다. 헤더만으로 한도를 넘으면 셀을 나눠도 진전이 없다."""
    grid = _page13_grid()
    result = split_table_rows(
        grid=grid, num_cols=4, single_text="X" * 9000, limit=len(_P13_THEAD), count_text=len,
        table_format="markdown", header_row_count=1, compact_prefix="", split_long_cells=True,
    )

    assert result.oversized_piece_indexes == tuple(range(len(result.pieces)))
    assert all(ROW_CONTINUED_LABEL not in piece for piece in result.pieces)
    assert sum(_squash(_P13_TBODY) in _squash(piece) for piece in result.pieces) == 1

