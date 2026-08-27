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
