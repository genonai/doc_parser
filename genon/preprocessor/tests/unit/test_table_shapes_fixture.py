"""실제 HTML fixture 를 docling 으로 파싱해 표 분할 불변식을 검사한다.

합성 grid 단위 테스트가 놓치는 것 — docling 이 rowspan/colspan 을 grid 에 어떻게
펼쳐 놓는지, 헤더 플래그가 실제로 몇 행에 붙는지 — 를 여기서 잡는다.
"""

from pathlib import Path

import pytest

from genon.preprocessor.facade.chunking.table_shape import (
    analyze_grid, resolve_table_format)
from genon.preprocessor.facade.chunking.table_splitter import split_table_rows
from table_invariants import assert_table_invariants

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "table_shapes"
LIMIT = 1200

# fixture 이름 -> (표 개수, 표별 is_complex 기대값)
EXPECTED_COMPLEXITY = {
    "simple_grid": [False],
    "merged_header": [True],
    "rowspan_groups": [True],
    "mixed_doc": [False, True, True],
}


@pytest.fixture(scope="module")
def documents():
    """fixture 4종을 한 번만 변환해 모든 테스트가 공유한다."""
    pytest.importorskip("docling.document_converter", exc_type=ImportError)
    from docling.document_converter import DocumentConverter

    converter = DocumentConverter()
    return {
        name: converter.convert(FIXTURES / f"{name}.html").document
        for name in EXPECTED_COMPLEXITY
    }


def _tables(document):
    for table in document.tables:
        grid = table.data.grid
        shape = analyze_grid(grid, table.data.num_cols, is_html_origin=True)
        yield table, grid, shape


@pytest.mark.unit
@pytest.mark.parametrize("name", sorted(EXPECTED_COMPLEXITY))
def test_shape_complexity_matches_document_structure(documents, name):
    shapes = [shape for _, _, shape in _tables(documents[name])]
    assert [s.is_complex for s in shapes] == EXPECTED_COMPLEXITY[name]


@pytest.mark.unit
@pytest.mark.parametrize("name", sorted(EXPECTED_COMPLEXITY))
def test_auto_format_follows_complexity(documents, name):
    for _, _, shape in _tables(documents[name]):
        expected = "html" if shape.is_complex else "markdown"
        assert resolve_table_format("auto", shape) == expected


@pytest.mark.unit
@pytest.mark.parametrize("name", sorted(EXPECTED_COMPLEXITY))
@pytest.mark.parametrize("table_format", ["html", "markdown"])
def test_every_table_splits_into_self_contained_pieces(documents, name, table_format):
    document = documents[name]
    for table, grid, shape in _tables(document):
        single = (table.export_to_html(document) if table_format == "html"
                  else table.export_to_markdown(document))
        result = split_table_rows(
            grid=grid, num_cols=shape.num_cols, single_text=single, limit=LIMIT,
            count_text=len, table_format=table_format,
            header_row_count=shape.header_row_count,
        )

        assert result.did_split, f"{name} 의 표가 분할되지 않았다: reason={result.reason}"
        assert not result.oversized_piece_indexes
        assert result.normalized_spans == shape.has_data_row_span
        assert_table_invariants(
            grid, result.pieces, num_cols=shape.num_cols,
            header_row_count=shape.header_row_count, table_format=table_format)


@pytest.mark.unit
def test_row_serialization_exposes_header_path_for_merged_header_table(documents):
    document = documents["merged_header"]
    table, grid, shape = next(_tables(document))
    result = split_table_rows(
        grid=grid, num_cols=shape.num_cols,
        single_text=table.export_to_html(document), limit=LIMIT,
        count_text=len, table_format="html",
        header_row_count=shape.header_row_count, row_serialization=True,
    )
    assert result.did_split
    assert all("금리 > 기본=" in piece for piece in result.pieces)
