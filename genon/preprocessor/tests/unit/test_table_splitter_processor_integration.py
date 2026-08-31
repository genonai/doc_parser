"""공통 표 분할기를 사용하는 processor별 통합 계약 테스트."""

import pytest


def _large_table_doc(rows=12, payload_size=90):
    core = pytest.importorskip("docling_core.types.doc", exc_type=ImportError)
    doc = core.DoclingDocument(
        name="large_table",
        origin=core.DocumentOrigin(
            mimetype="text/html", binary_hash=1, filename="large_table.html"
        ),
    )
    cells = []
    values = [["번호", "내용"]]
    values.extend([
        [
            f"ROW-{n:02d}",
            f"ROW-{n:02d}-START " + ("가" * payload_size) + f" ROW-{n:02d}-END",
        ]
        for n in range(1, rows + 1)
    ])
    for r, row in enumerate(values):
        for c, value in enumerate(row):
            cells.append(core.TableCell(
                text=value,
                start_row_offset_idx=r,
                end_row_offset_idx=r + 1,
                start_col_offset_idx=c,
                end_col_offset_idx=c + 1,
                column_header=(r == 0),
            ))
    doc.add_table(data=core.TableData(
        num_rows=len(values), num_cols=2, table_cells=cells
    ))
    return doc


def _assert_rows_stay_together(texts):
    assert len(texts) > 1
    for n in range(1, 13):
        marker = f"ROW-{n:02d}"
        starts = [i for i, text in enumerate(texts) if f"{marker}-START" in text]
        ends = [i for i, text in enumerate(texts) if f"{marker}-END" in text]
        assert starts == ends and len(starts) == 1


def _attach_rag_description(table):
    core = pytest.importorskip("docling_core.types.doc", exc_type=ImportError)
    table.annotations.append(core.DescriptionAnnotation(
        text="2026년 센터별 장애 대응 실적",
        provenance="facade_table_text_description",
    ))
    table.annotations.append(core.MiscAnnotation(content={
        "provenance": "facade_table_text_description",
        "table_retrieval": {
            "retrieval_context": "2026년 센터별 장애 건수와 평균 복구시간",
            "key_facts": ["서울 센터의 복구시간을 월별로 비교"],
            "search_terms": ["서울 장애 복구시간"],
            "include_search_terms": False,
            "repeat_context_on_split": True,
        },
    }))


@pytest.mark.unit
@pytest.mark.parametrize(
    "module_name",
    [
        "genon.preprocessor.facade.chunking_processor",
        "genon.preprocessor.facade.intelligent_processor",
        "genon.preprocessor.facade.convert_processor",
    ],
)
@pytest.mark.parametrize("chunk_mode", ["split_only", "resize_all"])
def test_genos_smart_chunkers_keep_html_table_rows(module_name, chunk_mode):
    module = pytest.importorskip(module_name, exc_type=ImportError)
    chunker = module.GenosSmartChunker(
        max_tokens=420, chunk_mode=chunk_mode, tokenizer_type="char"
    )
    texts = [
        chunk.text for chunk in chunker.chunk(
            dl_doc=_large_table_doc(), export_to_html=1, table_format="html"
        )
        if "<table>" in chunk.text
    ]

    _assert_rows_stay_together(texts)
    for text in texts:
        assert text.count("<table>") == text.count("</table>") == 1
        assert text.count("<tr>") == text.count("</tr>")
        assert "<th>번호</th><th>내용</th>" in text
        assert len(text) <= 420


@pytest.mark.unit
@pytest.mark.parametrize(
    "module_name",
    [
        "genon.preprocessor.facade.chunking_processor",
        "genon.preprocessor.facade.intelligent_processor",
        "genon.preprocessor.facade.convert_processor",
    ],
)
def test_chunking_processor_repeats_table_description_within_chunk_size(module_name):
    module = pytest.importorskip(module_name, exc_type=ImportError)
    doc = _large_table_doc(rows=12, payload_size=70)
    _attach_rag_description(doc.tables[0])
    chunker = module.GenosSmartChunker(
        max_tokens=420, chunk_mode="split_only", tokenizer_type="char"
    )
    texts = [
        chunk.text for chunk in chunker.chunk(dl_doc=doc, export_to_html=1)
        if "<table>" in chunk.text
    ]

    _assert_rows_stay_together(texts)
    assert all("[표 검색 설명]\n2026년 센터별 장애 건수와 평균 복구시간" in text for text in texts)
    assert all(len(text) <= 420 for text in texts)
    assert all(text.count("[표 검색 설명]") == 1 for text in texts)


@pytest.mark.unit
@pytest.mark.parametrize(
    "module_name",
    [
        "genon.preprocessor.facade.chunking_processor",
        "genon.preprocessor.facade.intelligent_processor",
        "genon.preprocessor.facade.convert_processor",
    ],
)
def test_image_table_summary_is_not_rendered_as_retrieval_prefix(module_name):
    """이미지 기반 표 요약은 텍스트 표 설명 경로로 새어 들어가지 않는다."""
    module = pytest.importorskip(module_name, exc_type=ImportError)
    core = pytest.importorskip("docling_core.types.doc", exc_type=ImportError)
    doc = _large_table_doc(rows=12, payload_size=70)
    doc.tables[0].annotations.append(core.DescriptionAnnotation(
        text="이미지 VLM 이 만든 표 요약", provenance="facade_table_description",
    ))
    chunker = module.GenosSmartChunker(
        max_tokens=420, chunk_mode="split_only", tokenizer_type="char"
    )
    texts = [chunk.text for chunk in chunker.chunk(dl_doc=doc, export_to_html=1)]

    assert all("[표 검색 설명]" not in text for text in texts)


@pytest.mark.unit
@pytest.mark.parametrize("module_name", [
    "genon.preprocessor.facade.intelligent_processor",
    "genon.preprocessor.facade.convert_processor",
])
def test_intelligent_and_convert_repeat_markdown_table_header(module_name):
    module = pytest.importorskip(module_name, exc_type=ImportError)
    chunker = module.GenosSmartChunker(
        max_tokens=420, chunk_mode="split_only", tokenizer_type="char"
    )
    texts = [
        chunk.text for chunk in chunker.chunk(
            dl_doc=_large_table_doc(), table_format="markdown", compact_tables=True
        )
        if "| 번호 | 내용 |" in chunk.text
    ]
    _assert_rows_stay_together(texts)
    assert all("| 번호 | 내용 |\n| --- | --- |" in text for text in texts)


@pytest.mark.unit
def test_attachment_hybrid_and_recursive_repeat_markdown_table_header():
    attachment = pytest.importorskip(
        "genon.preprocessor.facade.attachment_processor", exc_type=ImportError
    )
    doc = _large_table_doc()
    hybrid = attachment.HybridChunker(
        max_tokens=420, merge_peers=False, tokenizer_type="char"
    )
    hybrid_texts = [chunk.text for chunk in hybrid.chunk(dl_doc=doc)]
    recursive_texts = [
        chunk["text"] for chunk in attachment._split_with_recursive_chunker(
            doc, chunk_size=420, chunk_overlap=30, compact_tables=True
        )
    ]

    for texts in (hybrid_texts, recursive_texts):
        table_texts = [text for text in texts if "| 번호 | 내용 |" in text]
        _assert_rows_stay_together(table_texts)
        assert all("| 번호 | 내용 |\n| --- | --- |" in text for text in table_texts)


def _rowspan_table_doc(rows=12, payload_size=70):
    """첫 열이 세로 병합(rowspan)된 표 — 구조상 행 단위로 나눌 수 없다."""
    core = pytest.importorskip("docling_core.types.doc", exc_type=ImportError)
    doc = core.DoclingDocument(
        name="rowspan_table",
        origin=core.DocumentOrigin(
            mimetype="text/html", binary_hash=1, filename="rowspan_table.html"
        ),
    )
    cells = [
        core.TableCell(
            text=value, start_row_offset_idx=0, end_row_offset_idx=1,
            start_col_offset_idx=c, end_col_offset_idx=c + 1, column_header=True,
        )
        for c, value in enumerate(["구분", "내용"])
    ]
    for r in range(1, rows + 1):
        cells.append(core.TableCell(
            text="공통 구분", start_row_offset_idx=1, end_row_offset_idx=rows + 1,
            start_col_offset_idx=0, end_col_offset_idx=1, row_span=rows,
        ))
        cells.append(core.TableCell(
            text=f"ROW-{r:02d}-START " + ("가" * payload_size) + f" ROW-{r:02d}-END",
            start_row_offset_idx=r, end_row_offset_idx=r + 1,
            start_col_offset_idx=1, end_col_offset_idx=2,
        ))
    doc.add_table(data=core.TableData(
        num_rows=rows + 1, num_cols=2, table_cells=cells
    ))
    return doc


_CHUNKER_MODULES = [
    "genon.preprocessor.facade.chunking_processor",
    "genon.preprocessor.facade.intelligent_processor",
    "genon.preprocessor.facade.convert_processor",
]


@pytest.mark.unit
@pytest.mark.parametrize("module_name", _CHUNKER_MODULES)
def test_rowspan_table_splits_with_description_once_per_piece(module_name):
    """rowspan 표도 행 경계에서 나뉘고, 각 조각이 설명을 정확히 한 번씩 싣는다.

    예전에는 rowspan 이 보이면 분할을 포기해 예산을 크게 넘는 청크가 그대로 나갔다.
    이제는 병합 값을 각 행에 복제해 나누므로, 조각마다 자족적인 표가 된다.
    """
    module = pytest.importorskip(module_name, exc_type=ImportError)
    doc = _rowspan_table_doc()
    _attach_rag_description(doc.tables[0])
    chunker = module.GenosSmartChunker(
        max_tokens=420, chunk_mode="split_only", tokenizer_type="char"
    )
    texts = [chunk.text for chunk in chunker.chunk(dl_doc=doc, export_to_html=1)
             if "[표 검색 설명]" in chunk.text]

    assert len(texts) > 1
    assert all(text.count("[표 검색 설명]") == 1 for text in texts)
    _assert_rows_stay_together(texts)


@pytest.mark.unit
@pytest.mark.parametrize("module_name", _CHUNKER_MODULES)
def test_chunk_size_zero_keeps_single_table_chunk_with_full_description(module_name):
    """chunk_size=0 이면 분할하지 않고 key_facts 까지 포함한 전체 설명을 1회 싣는다."""
    module = pytest.importorskip(module_name, exc_type=ImportError)
    doc = _large_table_doc(rows=12, payload_size=70)
    _attach_rag_description(doc.tables[0])
    chunker = module.GenosSmartChunker(
        max_tokens=0, chunk_mode="split_only", tokenizer_type="char"
    )
    texts = [chunk.text for chunk in chunker.chunk(dl_doc=doc, export_to_html=1)
             if "[표 검색 설명]" in chunk.text]

    assert len(texts) == 1
    assert "핵심 사실: 서울 센터의 복구시간을 월별로 비교" in texts[0]


@pytest.mark.unit
@pytest.mark.parametrize("module_name", _CHUNKER_MODULES)
@pytest.mark.parametrize("chunk_mode", ["split_only", "resize_all", "table_as_chunk"])
def test_description_survives_every_chunk_mode(module_name, chunk_mode):
    module = pytest.importorskip(module_name, exc_type=ImportError)
    doc = _large_table_doc(rows=12, payload_size=70)
    _attach_rag_description(doc.tables[0])
    chunker = module.GenosSmartChunker(
        max_tokens=420, chunk_mode=chunk_mode, tokenizer_type="char"
    )
    texts = [chunk.text for chunk in chunker.chunk(dl_doc=doc, export_to_html=1)
             if "<table>" in chunk.text or "|" in chunk.text]

    assert texts
    assert all("[표 검색 설명]" in text for text in texts)
    assert all(text.count("[표 검색 설명]") == 1 for text in texts)


def _roundtrip(doc):
    """/parser 가 낸 docling JSON 을 /chunker 가 다시 읽는 경로를 재현한다.

    이 검증 시점에 docling_core 가 deprecated `annotations` 를 `meta` 로 이관한다.
    """
    core = pytest.importorskip("docling_core.types.doc", exc_type=ImportError)
    return core.DoclingDocument.model_validate(doc.model_dump(mode="json"))


@pytest.mark.unit
@pytest.mark.parametrize(
    "module_name",
    [
        "genon.preprocessor.facade.chunking_processor",
        "genon.preprocessor.facade.intelligent_processor",
        "genon.preprocessor.facade.convert_processor",
    ],
)
def test_enricher_meta_does_not_leak_into_chunk_text_after_json_roundtrip(module_name):
    """표 설명이 meta 로 이관돼도 청크 본문에 docling meta 블록이 실리지 않는다."""
    module = pytest.importorskip(module_name, exc_type=ImportError)
    # 분할되지 않는 작은 표라야 docling 직렬화기를 그대로 타고, meta 누출이 드러난다.
    doc = _large_table_doc(rows=2, payload_size=10)
    _attach_rag_description(doc.tables[0])
    doc = _roundtrip(doc)
    assert doc.tables[0].meta is not None  # 이관이 실제로 일어났는지 먼저 고정

    chunker = module.GenosSmartChunker(
        max_tokens=4000, chunk_mode="split_only", tokenizer_type="char"
    )
    texts = [chunk.text for chunk in chunker.chunk(dl_doc=doc, export_to_html=1)]

    assert texts
    for text in texts:
        assert "docling-meta" not in text
        assert "docling_legacy" not in text
        assert "table_retrieval" not in text
        assert "retrieval_context" not in text
    # 설명 자체는 청커가 붙이는 접두 한 번만 남는다.
    table_texts = [text for text in texts if "<table>" in text]
    assert table_texts
    assert all(text.count("[표 검색 설명]") == 1 for text in table_texts)
