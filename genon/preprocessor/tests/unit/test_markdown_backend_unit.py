from __future__ import annotations

from pathlib import Path
from typing import cast
import pytest


MD_SAMPLES = sorted(
    (Path(__file__).resolve().parents[2] / "sample_files").glob("*.md")
)


@pytest.mark.unit
@pytest.mark.parametrize("sample_path", MD_SAMPLES, ids=lambda p: p.name)
def test_markdown_backend_convert_on_all_md_samples(sample_path: Path):
    from docling.datamodel.document import InputDocument
    from docling.datamodel.base_models import InputFormat
    from docling.backend.md_backend import MarkdownDocumentBackend

    if not sample_path.exists():
        pytest.skip(f"sample not found: {sample_path}")

    in_doc = InputDocument(
        path_or_stream=sample_path,
        format=InputFormat.MD,
        backend=MarkdownDocumentBackend,
        filename=sample_path.name,
    )

    assert in_doc.valid is True
    assert in_doc._backend.is_valid() is True

    backend = cast(MarkdownDocumentBackend, in_doc._backend)
    doc = backend.convert()
    assert doc is not None

    # 텍스트가 최소 1개 이상 존재하는지 확인
    assert hasattr(doc, "texts")
    assert isinstance(doc.texts, list)
    assert len(doc.texts) >= 1




@pytest.mark.unit
def test_markdown_table_has_no_duplicate_cells(tmp_path: Path):
    """마크다운 표의 셀은 table_cells 에 한 번씩만 들어간다(#366, 업스트림 docling #3781).

    백엔드가 TableData 생성자에 셀을 넘긴 뒤 같은 셀을 한 번 더 append 해서, 셀 수가
    실제의 두 배가 되고 같은 격자 위치가 두 번씩 나왔다.
    """
    from docling.datamodel.document import InputDocument
    from docling.datamodel.base_models import InputFormat
    from docling.backend.md_backend import MarkdownDocumentBackend

    md = tmp_path / "table.md"
    md.write_text(
        "| Region | Q1 | Q2 |\n| --- | --- | --- |\n| North | 10 | 20 |\n| South | 30 | 40 |\n",
        encoding="utf-8",
    )
    in_doc = InputDocument(
        path_or_stream=md, format=InputFormat.MD, backend=MarkdownDocumentBackend, filename=md.name,
    )
    doc = cast(MarkdownDocumentBackend, in_doc._backend).convert()

    data = doc.tables[0].data
    positions = [(c.start_row_offset_idx, c.start_col_offset_idx) for c in data.table_cells]
    assert len(data.table_cells) == data.num_rows * data.num_cols
    assert len(positions) == len(set(positions))
