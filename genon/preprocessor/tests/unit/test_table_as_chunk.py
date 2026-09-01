"""표 독자 청크(table_as_chunk) 경계 규칙 계약.

표는 앞뒤 본문과 섞이지 않고 자기 청크를 갖는다. 예전 구현은 이 격리를 토큰 단계
이전의 early return 으로 처리해서 표 사이 본문이 chunk_size 를 통째로 무시했다.
지금은 섹션 경계 규칙으로 처리하므로 본문 분할이 살아 있어야 한다 — 아래 두 번째
테스트가 그 회귀의 재발 감시다.
"""

import pytest

_CHUNKER_MODULES = [
    "genon.preprocessor.facade.chunking_processor",
    "genon.preprocessor.facade.intelligent_processor",
    "genon.preprocessor.facade.convert_processor",
]

BODY_MARKER = "BODY-SENTENCE"
TABLE_MARKER = "CELL-VALUE"


def _text_table_text_doc(body_repeat=1):
    """제목 → 본문 → 표 → 본문 순서의 문서. 본문 길이는 호출부가 정한다."""
    core = pytest.importorskip("docling_core.types.doc", exc_type=ImportError)
    doc = core.DoclingDocument(
        name="text_table_text",
        origin=core.DocumentOrigin(
            mimetype="text/html", binary_hash=1, filename="text_table_text.html"
        ),
    )
    doc.add_heading(text="표가 들어 있는 절")
    sentence = f"{BODY_MARKER} 앞 문단입니다. " + ("가나다라마바사아자차 " * 10)
    for _ in range(body_repeat):
        doc.add_text(label=core.DocItemLabel.TEXT, text=sentence)

    values = [["항목", "값"], [f"{TABLE_MARKER}-A", "1000"], [f"{TABLE_MARKER}-B", "2000"]]
    cells = []
    for r, row in enumerate(values):
        for c, value in enumerate(row):
            cells.append(core.TableCell(
                text=value,
                start_row_offset_idx=r, end_row_offset_idx=r + 1,
                start_col_offset_idx=c, end_col_offset_idx=c + 1,
                column_header=(r == 0),
            ))
    doc.add_table(data=core.TableData(
        num_rows=len(values), num_cols=2, table_cells=cells))

    for _ in range(body_repeat):
        doc.add_text(
            label=core.DocItemLabel.TEXT,
            text=f"{BODY_MARKER} 뒤 문단입니다. " + ("마바사아자차카타파하 " * 10),
        )
    return doc


@pytest.mark.unit
@pytest.mark.parametrize("module_name", _CHUNKER_MODULES)
def test_table_gets_its_own_chunk(module_name):
    """표 청크에 앞뒤 본문 산문이 섞이지 않는다."""
    module = pytest.importorskip(module_name, exc_type=ImportError)
    doc = _text_table_text_doc()
    chunker = module.GenosSmartChunker(
        max_tokens=4000, chunk_mode="split_only", tokenizer_type="char")
    texts = [chunk.text for chunk in chunker.chunk(dl_doc=doc, export_to_html=1)]

    table_texts = [t for t in texts if TABLE_MARKER in t]
    assert len(table_texts) == 1, "표가 정확히 한 청크에 담겨야 한다"
    assert BODY_MARKER not in table_texts[0], "표 청크에 본문 산문이 섞였다"
    # 본문은 사라지지 않는다.
    assert any(BODY_MARKER in t for t in texts)


@pytest.mark.unit
@pytest.mark.parametrize("module_name", _CHUNKER_MODULES)
def test_body_between_tables_still_respects_chunk_size(module_name):
    """표 격리가 본문의 chunk_size 분할을 무력화하지 않는다(early-return 회귀 감시)."""
    module = pytest.importorskip(module_name, exc_type=ImportError)
    doc = _text_table_text_doc(body_repeat=12)
    max_tokens = 500
    chunker = module.GenosSmartChunker(
        max_tokens=max_tokens, chunk_mode="split_only", tokenizer_type="char",
        include_chunk_header=False)
    texts = [chunk.text for chunk in chunker.chunk(dl_doc=doc, export_to_html=1)]

    body_texts = [t for t in texts if BODY_MARKER in t]
    assert len(body_texts) > 1, "긴 본문이 전혀 분할되지 않았다"
    oversized = [len(t) for t in body_texts if len(t) > max_tokens]
    assert not oversized, f"chunk_size({max_tokens}) 초과 본문 청크: {oversized}"


@pytest.mark.unit
@pytest.mark.parametrize("module_name", _CHUNKER_MODULES)
def test_table_as_chunk_off_restores_merging(module_name):
    """스위치를 내리면 예전처럼 표가 본문과 한 청크로 묶인다."""
    module = pytest.importorskip(module_name, exc_type=ImportError)
    doc = _text_table_text_doc()
    chunker = module.GenosSmartChunker(
        max_tokens=4000, chunk_mode="split_only", tokenizer_type="char",
        table_as_chunk=False)
    texts = [chunk.text for chunk in chunker.chunk(dl_doc=doc, export_to_html=1)]

    table_texts = [t for t in texts if TABLE_MARKER in t]
    assert len(table_texts) == 1
    assert BODY_MARKER in table_texts[0], "off 인데도 표가 본문과 분리됐다"


@pytest.mark.unit
@pytest.mark.parametrize("module_name", _CHUNKER_MODULES)
def test_kwargs_override_beats_field(module_name):
    """요청 kwargs 의 table_as_chunk 가 청커 필드(=yaml)보다 우선한다."""
    module = pytest.importorskip(module_name, exc_type=ImportError)
    doc = _text_table_text_doc()
    chunker = module.GenosSmartChunker(
        max_tokens=4000, chunk_mode="split_only", tokenizer_type="char",
        table_as_chunk=True)
    texts = [chunk.text for chunk in
             chunker.chunk(dl_doc=doc, export_to_html=1, table_as_chunk=False)]

    table_texts = [t for t in texts if TABLE_MARKER in t]
    assert len(table_texts) == 1
    assert BODY_MARKER in table_texts[0]


@pytest.mark.unit
@pytest.mark.parametrize("module_name", _CHUNKER_MODULES)
def test_resize_all_does_not_merge_table_groups(module_name):
    """resize_all 의 greedy 병합도 표 경계를 넘지 않는다."""
    module = pytest.importorskip(module_name, exc_type=ImportError)
    doc = _text_table_text_doc()
    # 문서 전체가 한 청크에 들어가고도 남는 예산이라, 격리가 없으면 반드시 병합된다.
    chunker = module.GenosSmartChunker(
        max_tokens=100000, chunk_mode="resize_all", tokenizer_type="char")
    texts = [chunk.text for chunk in chunker.chunk(dl_doc=doc, export_to_html=1)]

    table_texts = [t for t in texts if TABLE_MARKER in t]
    assert len(table_texts) == 1
    assert BODY_MARKER not in table_texts[0], "resize_all 이 표를 본문과 병합했다"


def _sheet_doc():
    """xlsx 유래 docling 문서(그룹명 'sheet: X') 재현. 시트마다 표 1개."""
    core = pytest.importorskip("docling_core.types.doc", exc_type=ImportError)
    doc = core.DoclingDocument(
        name="sheets",
        origin=core.DocumentOrigin(
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            binary_hash=2, filename="sheets.xlsx"),
    )
    for s in ("1분기", "2분기"):
        group = doc.add_group(name=f"sheet: {s}")
        cells = []
        values = [["항목", "값"], [f"{TABLE_MARKER}-{s}", "10"]]
        for r, row in enumerate(values):
            for c, value in enumerate(row):
                cells.append(core.TableCell(
                    text=value,
                    start_row_offset_idx=r, end_row_offset_idx=r + 1,
                    start_col_offset_idx=c, end_col_offset_idx=c + 1,
                    column_header=(r == 0),
                ))
        doc.add_table(
            data=core.TableData(num_rows=len(values), num_cols=2, table_cells=cells),
            parent=group,
        )
    return doc


@pytest.mark.unit
@pytest.mark.parametrize("module_name", _CHUNKER_MODULES)
def test_sheet_tables_stay_one_chunk_each(module_name):
    """xlsx 시트 표는 시트마다 별도 청크로 남는다.

    예전에는 AUTO_TABLE_AS_CHUNK_FOR_SHEETS 라는 별도 플래그가 이것을 보장했다.
    그 플래그를 걷어낸 뒤에도 table_as_chunk 기본값이 같은 결과를 내야 한다.
    """
    module = pytest.importorskip(module_name, exc_type=ImportError)
    doc = _sheet_doc()
    chunker = module.GenosSmartChunker(
        max_tokens=100000, chunk_mode="resize_all", tokenizer_type="char")
    texts = [chunk.text for chunk in chunker.chunk(dl_doc=doc, export_to_html=1)]

    for sheet in ("1분기", "2분기"):
        hits = [t for t in texts if f"{TABLE_MARKER}-{sheet}" in t]
        assert len(hits) == 1, f"{sheet} 표가 한 청크에 있지 않다"
    # 서로 다른 시트의 표가 한 청크로 합쳐지지 않는다.
    assert not [t for t in texts
                if f"{TABLE_MARKER}-1분기" in t and f"{TABLE_MARKER}-2분기" in t]
