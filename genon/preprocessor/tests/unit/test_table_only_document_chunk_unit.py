"""본문 텍스트 없이 표만 있는 docling 문서의 청킹(#599).

청크로 나갈 내용이 하나도 없을 때만 "." 한 글자 text item 을 넣어 임베딩할 텍스트를 만든다.
셀이 있는 표는 그 자체로 청크가 되므로 "." 를 붙이지 않는다(적재용 2.2.5 에서 표만 있는 csv·xlsx 에
"." 청크가 생겼다). 셀이 0개인 빈 표만 있으면 "." 로 청크 1개를 만들어 0건 실패를 막는다.
단, 청크 검증(chunking.validation, 출고 action: drop)이 켜져 있으면 그 "." 청크는 내용 없음으로 빠진다.
"""

import asyncio
import shutil
from pathlib import Path

import pytest
import yaml
from docling_core.types.doc import DoclingDocument, TableCell, TableData

_RESOURCE = Path(__file__).resolve().parents[2] / "resource"
_CONFIG_NAME = "chunking_processor_config.yaml"


def _table_only_document(cells: list[tuple[int, int, str]]) -> dict:
    doc = DoclingDocument(name="table_only")
    rows = max((r for r, _, _ in cells), default=-1) + 1
    cols = max((c for _, c, _ in cells), default=-1) + 1
    doc.add_table(data=TableData(num_rows=rows, num_cols=cols, table_cells=[
        TableCell(text=text, start_row_offset_idx=r, end_row_offset_idx=r + 1,
                  start_col_offset_idx=c, end_col_offset_idx=c + 1, column_header=(r == 0))
        for r, c, text in cells]))
    return doc.export_to_dict()


def _chunk_texts(document: dict, tmp_path: Path, *, validation: bool = True) -> list[str]:
    """출고 청커 설정으로 청킹한다. validation=False 면 chunking.validation 을 끈 사본을 쓴다."""
    mod = pytest.importorskip("facade.chunking_processor")
    shutil.copytree(_RESOURCE, tmp_path, dirs_exist_ok=True)
    cfg = yaml.safe_load((_RESOURCE / _CONFIG_NAME).read_text(encoding="utf-8"))
    if not validation:
        cfg.setdefault("chunking", {}).setdefault("validation", {})["enable"] = False
    config = tmp_path / _CONFIG_NAME
    config.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    try:
        processor = mod.DocumentProcessor(config_path=str(config))
    except Exception as e:  # noqa: BLE001 - 모델/네트워크 등 환경 의존
        pytest.skip(f"DocumentProcessor init unavailable: {e}")
    from genon.preprocessor.processing.core.cli import mock_request

    vectors = asyncio.run(processor(mock_request(), "", document=document))
    return [(v.model_dump() if hasattr(v, "model_dump") else v)["text"] for v in vectors]


@pytest.mark.unit
def test_table_with_cells_gets_no_dot(tmp_path):
    """셀이 있는 표만 있는 문서 — 표 청크만 나오고 "." 가 따로 붙지 않는다(예전엔 표 청크 끝 줄이 "." 였다)."""
    texts = _chunk_texts(_table_only_document([(0, 0, "구분"), (0, 1, "금액"), (1, 0, "연회비"), (1, 1, "18,000원")]),
                         tmp_path)
    assert texts and all("18,000원" in t for t in texts)
    assert all(t.strip().splitlines()[-1].strip() != "." for t in texts)


@pytest.mark.unit
def test_document_without_content_gets_dot_chunk(tmp_path):
    """청크로 나갈 내용이 없는 문서(셀이 0개인 빈 표만) — "." 한 글자 청크를 만들어 0건으로 끝나지 않는다.
    출고 설정의 청크 검증(action: drop)은 이 청크를 내용 없음으로 빼므로 검증을 끄고 본다."""
    texts = _chunk_texts(_table_only_document([]), tmp_path, validation=False)
    assert [t.strip().splitlines()[-1].strip() for t in texts] == ["."]
