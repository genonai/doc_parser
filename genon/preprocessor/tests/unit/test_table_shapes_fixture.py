"""실제 HTML fixture 를 docling 으로 파싱해 표 분할 불변식을 검사한다.

합성 grid 단위 테스트가 놓치는 것 — docling 이 rowspan/colspan 을 grid 에 어떻게
펼쳐 놓는지, 헤더 플래그가 실제로 몇 행에 붙는지 — 를 여기서 잡는다.
"""

from pathlib import Path

import pytest

from genon.preprocessor.facade.chunking.table_shape import (
    analyze_grid, resolve_table_format)
from genon.preprocessor.facade.chunking.table_splitter import split_table_rows
from table_invariants import assert_table_invariants, looks_like_markdown_table

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


# ─── 청커 배선 (auto 포맷 · 캡션 반복 · 분할 기록) ─────────────────────────────

@pytest.fixture(scope="module")
def chunker_module():
    return pytest.importorskip(
        "genon.preprocessor.facade.chunking_processor", exc_type=ImportError)


def _chunk(chunker_module, document, **kwargs):
    chunker = chunker_module.GenosSmartChunker(
        max_tokens=LIMIT, chunk_mode="split_only", tokenizer_type="char")
    return chunker, list(chunker.chunk(dl_doc=document, **kwargs))


@pytest.mark.unit
def test_auto_emits_markdown_for_simple_table_and_html_for_complex(
        chunker_module, documents):
    _, chunks = _chunk(chunker_module, documents["mixed_doc"], table_format="auto")
    texts = [c.text for c in chunks]
    # 단순 격자 표는 markdown 파이프 표로, 병합/계층 표는 html 로 나간다.
    assert any(looks_like_markdown_table(t) for t in texts)
    assert any("<table>" in t for t in texts)


@pytest.mark.unit
@pytest.mark.parametrize("table_format", ["html", "markdown", "auto"])
def test_no_chunk_exceeds_budget_and_no_cell_value_is_lost(
        chunker_module, documents, table_format):
    document = documents["mixed_doc"]
    _, chunks = _chunk(chunker_module, document, table_format=table_format)
    joined = "\n".join(c.text for c in chunks)

    assert all(len(c.text) <= LIMIT for c in chunks)
    missing = [
        text for table in document.tables for row in table.data.grid for cell in row
        if (text := str(getattr(cell, "text", "") or "").strip()) and text not in joined
    ]
    assert not missing, f"사라진 셀 값: {missing[:5]}"


def _captioned_table_doc(caption: str, rows: int = 20, payload: int = 90):
    """캡션이 달린 큰 표 하나짜리 문서.

    HTML fixture 를 쓰지 않는 이유는 docling HTML 백엔드가 <caption> 을 버리기 때문이다
    (caption_text 도 texts 도 비어 있다). 캡션은 PDF 등 다른 백엔드에서 채워진다.
    """
    core = pytest.importorskip("docling_core.types.doc", exc_type=ImportError)
    document = core.DoclingDocument(name="captioned")
    cells = []
    values = [["항목", "내용"]]
    values.extend([[f"ITEM-{n:02d}",
                    f"ROW-{n:02d}-START " + ("가" * payload) + f" ROW-{n:02d}-END"]
                   for n in range(1, rows + 1)])
    for r, row in enumerate(values):
        for c, value in enumerate(row):
            cells.append(core.TableCell(
                text=value, start_row_offset_idx=r, end_row_offset_idx=r + 1,
                start_col_offset_idx=c, end_col_offset_idx=c + 1, column_header=(r == 0)))
    document.add_table(
        data=core.TableData(num_rows=len(values), num_cols=2, table_cells=cells),
        caption=document.add_text(label=core.DocItemLabel.CAPTION, text=caption),
    )
    return document


@pytest.mark.unit
def test_caption_is_repeated_in_every_split_piece(chunker_module):
    """단일 청크 경로는 serializer 가 캡션을 싣지만 분할 경로는 grid 에서 다시 만든다.

    캡션은 그 표가 무엇에 대한 것인지 말하는 유일한 문장인 경우가 많아, 조각마다
    없으면 그 조각만 검색됐을 때 근거로 쓸 수 없다.
    """
    caption = "월별 이용 내역 요약표"
    document = _captioned_table_doc(caption)
    assert document.tables[0].caption_text(document).strip() == caption

    _, chunks = _chunk(chunker_module, document, table_format="html")
    pieces = [c.text for c in chunks if "<table>" in c.text]
    assert len(pieces) > 1, "표가 분할되어야 이 검사가 의미 있다"
    assert all(caption in piece for piece in pieces)


@pytest.mark.unit
def test_chunker_records_split_totals_for_vector_metadata(chunker_module, documents):
    chunker, _ = _chunk(chunker_module, documents["mixed_doc"], table_format="auto")
    totals = getattr(chunker, "_table_split_totals", {})
    assert set(totals) == {t.self_ref for t in documents["mixed_doc"].tables}
    assert all(count > 1 for count in totals.values())


@pytest.mark.unit
def test_row_serialization_applies_only_to_complex_tables(chunker_module, documents):
    document = documents["mixed_doc"]
    _, plain = _chunk(chunker_module, document, table_format="auto")
    _, serial = _chunk(chunker_module, document, table_format="auto",
                       table_row_serialization=True)

    assert all("[표 행 요약]" not in c.text for c in plain)
    assert any("[표 행 요약]" in c.text for c in serial)
    # 단순 격자 표(markdown 으로 나가는 쪽)에는 붙지 않는다.
    for chunk in serial:
        if looks_like_markdown_table(chunk.text):
            assert "[표 행 요약]" not in chunk.text


@pytest.mark.unit
def test_auto_format_is_stable_whether_or_not_the_table_is_split(chunker_module, documents):
    """같은 표가 분할 여부에 따라 다른 형식으로 나가면 한 인덱스 안에서 표현이 어긋난다."""
    document = documents["simple_grid"]  # auto 가 markdown 을 고르는 단순 격자 표

    def formats(max_tokens):
        chunker = chunker_module.GenosSmartChunker(
            max_tokens=max_tokens, chunk_mode="split_only", tokenizer_type="char")
        texts = [c.text for c in chunker.chunk(dl_doc=document, table_format="auto")]
        return {"markdown" if looks_like_markdown_table(t) else "html"
                for t in texts if looks_like_markdown_table(t) or "<table>" in t}

    assert formats(1_000_000) == {"markdown"}   # 분할 없음(표 하나가 통째로)
    assert formats(LIMIT) == {"markdown"}       # 분할됨
