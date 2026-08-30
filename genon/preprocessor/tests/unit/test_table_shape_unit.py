"""표 구조 분석·포맷 선택·span 정규화의 순수 단위 테스트."""

from dataclasses import dataclass

import pytest

from genon.preprocessor.facade.chunking.table_shape import (
    analyze_grid,
    flatten_header_rows,
    normalize_row_spans,
    resolve_table_format,
    serialize_rows,
)


@dataclass
class Cell:
    text: str
    start_col_offset_idx: int = 0
    col_span: int = 1
    row_span: int = 1
    column_header: bool = False
    row_header: bool = False
    row_section: bool = False


def _simple_grid():
    """헤더 1행 + 데이터 2행, 병합 없음."""
    return [
        [Cell("연도", 0, column_header=True), Cell("금리", 1, column_header=True)],
        [Cell("2024", 0), Cell("3.0", 1)],
        [Cell("2025", 0), Cell("3.2", 1)],
    ]


def _hierarchical_grid():
    """colspan 2단 헤더 + rowspan 데이터. docling grid 처럼 병합 값이 복제된 상태."""
    return [
        [Cell("구분", 0, column_header=True),
         Cell("금리", 1, col_span=2, column_header=True),
         Cell("금리", 1, col_span=2, column_header=True)],
        [Cell("구분", 0, column_header=True),
         Cell("기본", 1, column_header=True),
         Cell("추가", 2, column_header=True)],
        [Cell("1년", 0, row_span=2), Cell("3.0", 1), Cell("0.2", 2)],
        [Cell("1년", 0, row_span=2), Cell("3.2", 1), Cell("0.3", 2)],
    ]


# ─── analyze_grid ─────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_simple_grid_is_not_complex():
    shape = analyze_grid(_simple_grid(), 2, is_html_origin=True)
    assert (shape.header_row_count, shape.is_complex) == (1, False)
    assert not shape.has_row_span and not shape.has_col_span


@pytest.mark.unit
def test_hierarchical_grid_is_complex():
    shape = analyze_grid(_hierarchical_grid(), 3, is_html_origin=True)
    assert shape.header_row_count == 2
    assert shape.is_complex
    assert shape.has_col_span and shape.has_row_span and shape.has_data_row_span


@pytest.mark.unit
def test_non_html_origin_takes_one_more_header_row():
    """PDF 등은 플래그 행 다음의 컬럼명 추정 행까지 헤더로 본다."""
    assert analyze_grid(_simple_grid(), 2, is_html_origin=False).header_row_count == 2
    assert analyze_grid(_simple_grid(), 2, is_html_origin=True).header_row_count == 1


@pytest.mark.unit
def test_header_row_count_never_exceeds_grid():
    grid = [[Cell("A", 0, column_header=True)]]
    assert analyze_grid(grid, 1, is_html_origin=False).header_row_count == 1


@pytest.mark.unit
@pytest.mark.parametrize("grid", [None, [], [[]]])
def test_unreadable_grid_returns_none(grid):
    assert analyze_grid(grid, 0, is_html_origin=True) is None


@pytest.mark.unit
def test_header_only_rowspan_is_not_data_row_span():
    """헤더에만 걸린 rowspan 은 분할 정규화 대상이 아니다."""
    grid = [
        [Cell("구분", 0, row_span=2, column_header=True), Cell("금리", 1, column_header=True)],
        [Cell("구분", 0, row_span=2, column_header=True), Cell("기본", 1, column_header=True)],
        [Cell("1년", 0), Cell("3.0", 1)],
    ]
    shape = analyze_grid(grid, 2, is_html_origin=True)
    assert shape.has_row_span and not shape.has_data_row_span


# ─── resolve_table_format ─────────────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.parametrize("configured,expected", [
    ("html", "html"), ("markdown", "markdown"),
    ("HTML", "html"), (" markdown ", "markdown"),
])
def test_explicit_format_ignores_shape(configured, expected):
    shape = analyze_grid(_hierarchical_grid(), 3, is_html_origin=True)
    assert resolve_table_format(configured, shape) == expected


@pytest.mark.unit
def test_auto_picks_markdown_for_simple_and_html_for_complex():
    simple = analyze_grid(_simple_grid(), 2, is_html_origin=True)
    complex_ = analyze_grid(_hierarchical_grid(), 3, is_html_origin=True)
    assert resolve_table_format("auto", simple) == "markdown"
    assert resolve_table_format("auto", complex_) == "html"


@pytest.mark.unit
@pytest.mark.parametrize("configured", ["auto", "html", "", None, "otsl"])
def test_unknown_shape_or_value_falls_back_to_html(configured):
    """구조를 모르면 정보를 잃지 않는 html 로 간다."""
    assert resolve_table_format(configured, None) == "html"


# ─── normalize_row_spans ──────────────────────────────────────────────────────

@pytest.mark.unit
def test_normalize_clears_row_span_without_touching_original():
    grid = _hierarchical_grid()
    original_spans = [[cell.row_span for cell in row] for row in grid]

    normalized = normalize_row_spans(grid)

    assert len(normalized) == len(grid)
    assert all(len(new) == len(old) for new, old in zip(normalized, grid))
    assert all(getattr(cell, "row_span", 1) == 1 for row in normalized for cell in row)
    # 원본은 그대로여야 한다 — 파서 출력과 표 이미지가 같은 TableItem 을 다시 읽는다.
    assert [[cell.row_span for cell in row] for row in grid] == original_spans


@pytest.mark.unit
def test_normalize_keeps_cell_values_in_every_covered_row():
    normalized = normalize_row_spans(_hierarchical_grid())
    assert normalized[2][0].text == normalized[3][0].text == "1년"


# ─── flatten_header_rows ──────────────────────────────────────────────────────

@pytest.mark.unit
def test_flatten_joins_levels_and_dedups_colspan_repeat():
    labels = flatten_header_rows(_hierarchical_grid(), 2, 3)
    # 상위 헤더가 colspan 으로 복제되어도 한 번만, 같은 값이 겹치면 접힌다.
    assert labels == ["구분", "금리 > 기본", "금리 > 추가"]


@pytest.mark.unit
def test_flatten_single_header_row_is_plain_label():
    assert flatten_header_rows(_simple_grid(), 1, 2) == ["연도", "금리"]


@pytest.mark.unit
def test_flatten_skips_empty_levels():
    grid = [
        [Cell("", 0, column_header=True), Cell("금리", 1, column_header=True)],
        [Cell("연도", 0, column_header=True), Cell("기본", 1, column_header=True)],
    ]
    assert flatten_header_rows(grid, 2, 2) == ["연도", "금리 > 기본"]


# ─── serialize_rows ───────────────────────────────────────────────────────────

@pytest.mark.unit
def test_serialize_rows_labels_each_value_with_header_path():
    grid = _hierarchical_grid()
    lines = serialize_rows(grid[2:], flatten_header_rows(grid, 2, 3), 3)
    assert lines == [
        "구분=1년 | 금리 > 기본=3.0 | 금리 > 추가=0.2",
        "구분=1년 | 금리 > 기본=3.2 | 금리 > 추가=0.3",
    ]


@pytest.mark.unit
def test_serialize_rows_drops_empty_cells_and_rows():
    grid = [[Cell("", 0), Cell("3.0", 1)], [Cell("", 0), Cell("", 1)]]
    assert serialize_rows(grid, ["연도", "금리"], 2) == ["금리=3.0"]
