"""표 표기형태별 청크 필드(#360)의 프로세서 계약 — 실제 청킹 경로로 확인한다.

- 기본(off)은 필드가 아예 생기지 않아야 한다(운영 동작 무변화).
- on 이면 표가 있든 없든 두 필드가 실리고, 표기형태만 바뀐다.
- 변형에도 가드레일 마스킹과 텍스트 정제가 primary 와 똑같이 적용돼야 한다.
"""
import asyncio
import dataclasses
import re

import pytest

from docling_core.types import DoclingDocument
from docling_core.types.doc import DocItemLabel, TableCell, TableData


def _doc_with_table() -> DoclingDocument:
    doc = DoclingDocument(name="monimo_table_sample")
    heading = doc.add_heading(text="연회비 안내", level=1)
    doc.add_text(label=DocItemLabel.TEXT, text="카드별 연회비는 아래 표와 같다.", parent=heading)
    cells = [
        TableCell(text="카드", start_row_offset_idx=0, end_row_offset_idx=1,
                  start_col_offset_idx=0, end_col_offset_idx=1, column_header=True),
        TableCell(text="연회비", start_row_offset_idx=0, end_row_offset_idx=1,
                  start_col_offset_idx=1, end_col_offset_idx=2, column_header=True),
        TableCell(text="국내전용", start_row_offset_idx=1, end_row_offset_idx=2,
                  start_col_offset_idx=0, end_col_offset_idx=1),
        TableCell(text="18,000원", start_row_offset_idx=1, end_row_offset_idx=2,
                  start_col_offset_idx=1, end_col_offset_idx=2),
        TableCell(text="해외겸용", start_row_offset_idx=2, end_row_offset_idx=3,
                  start_col_offset_idx=0, end_col_offset_idx=1),
        TableCell(text="20,000원", start_row_offset_idx=2, end_row_offset_idx=3,
                  start_col_offset_idx=1, end_col_offset_idx=2),
    ]
    doc.add_table(data=TableData(num_rows=3, num_cols=2, table_cells=cells), parent=heading)
    doc.add_text(label=DocItemLabel.TEXT, text="연회비는 최초 발급 시 청구된다.", parent=heading)
    return doc


def _chunk(payload_extra: dict | None = None, masking: bool = False, **kwargs) -> list:
    module = pytest.importorskip("facade.chunking_processor")
    chunker = module.DocumentProcessor()
    if masking:
        # 마스킹 스위치는 yaml(guardrail.masking_enabled)에서 온다. 그 값만 켜서 확인한다.
        chunker._gr_cfg = dataclasses.replace(chunker._gr_cfg, masking_enabled=True)
    payload = {"document": _doc_with_table().model_dump(mode="json"), **(payload_extra or {})}
    vectors = asyncio.run(chunker(
        request=None, file_path="/data/monimo_table_sample.pdf",
        document=payload, **kwargs))
    return [v.model_dump() for v in vectors]


def _content_tokens(text: str) -> list:
    """표기형태를 지운 내용 토큰. 태그·파이프·markdown 구분선을 걷어낸다."""
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"^\s*\|[\s\-:|]+\|\s*$", " ", text, flags=re.M)
    return [t for t in re.split(r"\s+", text.replace("|", " ")) if t]


VARIANT_FIELDS = ("text_table_html", "text_table_md")


@pytest.mark.unit
def test_variant_fields_are_absent_by_default():
    """설정 기본값은 off 다. 필드가 null 로라도 생기면 기존 스키마가 바뀐다."""
    for vector in _chunk(table_text_formats=()):
        for field in VARIANT_FIELDS:
            assert field not in vector


@pytest.mark.unit
def test_variant_fields_carry_the_whole_chunk_in_both_notations():
    vectors = _chunk(table_format="html", table_text_formats=("html", "markdown"))
    table_chunks = [v for v in vectors if v["has_table"]]
    assert table_chunks, "표 청크가 없으면 이 테스트가 아무것도 확인하지 않는다"

    for vector in vectors:
        html, markdown = vector["text_table_html"], vector["text_table_md"]
        # 표가 없는 청크도 채운다 — 소비 측이 필드 부재를 분기하지 않게 한다.
        assert html and markdown
        if not vector["has_table"]:
            assert html == vector["text"] == markdown
            continue
        # primary 가 html 이므로 html 변형은 본문과 같고, markdown 변형만 표기가 바뀐다.
        assert html == vector["text"]
        assert "<table>" in html and "<table>" not in markdown
        assert "| 카드 | 연회비 |" in markdown
        for value in ("국내전용", "18,000원", "해외겸용", "20,000원"):
            assert value in html and value in markdown
        # 표기형태만 다르고 담은 내용은 같다(표 밖 문장 포함).
        assert _content_tokens(markdown) == _content_tokens(vector["text"])


@pytest.mark.unit
def test_chunk_header_line_is_included_in_variants():
    vectors = _chunk(include_chunk_header=1, table_text_formats=("markdown",))
    headers = [v for v in vectors if v["text"].startswith("HEADER:")]
    assert headers
    for vector in headers:
        assert vector["text_table_md"].startswith("HEADER:")


@pytest.mark.unit
def test_primary_text_is_unchanged_when_variants_are_on():
    off = _chunk(table_format="html", table_text_formats=())
    on = _chunk(table_format="html", table_text_formats=("html", "markdown"))

    assert len(off) == len(on)
    assert [v["text"] for v in off] == [v["text"] for v in on]


@pytest.mark.unit
def test_masking_applies_to_variants_too():
    """마스킹한 값이 변형 필드로 평문 유출되면 그게 사고다."""
    sensitive = [{"category": "민감", "quote_origin": "20,000원",
                  "quote_masked": "**,***원"}]
    vectors = _chunk(payload_extra={"sensitive_infos": sensitive}, masking=True,
                     table_format="html", table_text_formats=("html", "markdown"))
    table_chunks = [v for v in vectors if v["has_table"]]
    assert table_chunks

    masked = [v for v in table_chunks if "**,***원" in v["text"]]
    assert masked, "표 청크에 마스킹이 적용되지 않으면 이 테스트가 아무것도 확인하지 않는다"
    for vector in masked:
        assert "20,000원" not in vector["text"]
        for field in VARIANT_FIELDS:
            assert "20,000원" not in vector[field], field
            assert "**,***원" in vector[field], field


@pytest.mark.unit
def test_text_cleanup_applies_to_variants_too():
    """정제를 primary 에만 걸면 같은 청크의 두 표현이 서로 다른 규칙으로 나간다."""
    vectors = _chunk(text_cleanup="safe", table_format="html",
                     table_text_formats=("html", "markdown"))
    assert vectors
    for vector in vectors:
        for field in VARIANT_FIELDS:
            value = vector[field]
            assert value == value.strip(), field                 # 양끝 공백 제거
            assert "\n\n\n" not in value, field                  # 3연속 개행 축약
            assert not any(line != line.rstrip() for line in value.splitlines()), field


# ---------------------------------------------------------------------------
# 행 기반 custom_fields 경로 — docling document 가 없는 경로도 같은 계약을 갖는다.
# 상품설명 카드(product_hpp, JSON 원천)가 이 경로를 탄다.
# ---------------------------------------------------------------------------

ROW_TABLE_HTML = (
    '<table><tbody>'
    '<tr><th>구분</th><th>연회비</th></tr>'
    '<tr><td>국내전용</td><td>18,000원</td></tr>'
    '<tr><td>해외겸용</td><td>20,000원</td></tr>'
    '</tbody></table>'
)
ROW_PREFIX = "[상품 문서] 연회비\n상품코드: AAP1344"


def _row_elements() -> dict:
    """json_semantic 이 만드는 형태의 parse-format payload."""
    return {"elements": [
        {"category": "custom_fields_row", "id": 0, "page": 1, "splittable": True,
         "chunk_prefix": ROW_PREFIX,
         "content": f"{ROW_PREFIX}\n연회비는 아래 표와 같다.\n{ROW_TABLE_HTML}",
         "metadata": {"PRODUCT_C": "AAP1344", "doc_type": "product_hpp"}},
        {"category": "custom_fields_row", "id": 1, "page": 2, "splittable": True,
         "chunk_prefix": ROW_PREFIX, "content": f"{ROW_PREFIX}\n표가 없는 섹션 본문.",
         "metadata": {"PRODUCT_C": "AAP1344", "doc_type": "product_hpp"}},
    ]}


def _chunk_rows(payload_extra: dict | None = None, masking: bool = False, **kwargs) -> list:
    module = pytest.importorskip("facade.chunking_processor")
    chunker = module.DocumentProcessor()
    if masking:
        chunker._gr_cfg = dataclasses.replace(chunker._gr_cfg, masking_enabled=True)
    payload = {**_row_elements(), **(payload_extra or {})}
    vectors = asyncio.run(chunker(
        request=None, file_path="/data/monimo_product_hpp_sample.json",
        document=payload, **kwargs))
    return [v.model_dump() for v in vectors]


@pytest.mark.unit
def test_row_path_variant_fields_are_absent_by_default():
    for vector in _chunk_rows(table_text_formats=()):
        for field in VARIANT_FIELDS:
            assert field not in vector


@pytest.mark.unit
def test_row_path_carries_both_notations_on_every_chunk():
    """docling 경로와 같은 계약: 표가 없는 청크도 두 필드를 갖는다."""
    vectors = _chunk_rows(table_text_formats=("html", "markdown"))
    assert vectors
    table_chunks = [v for v in vectors if v["has_table"]]
    assert table_chunks, "표 청크가 없으면 이 테스트가 아무것도 확인하지 않는다"

    for vector in vectors:
        html, markdown = vector["text_table_html"], vector["text_table_md"]
        assert html and markdown
        if not vector["has_table"]:
            assert html == vector["text"] == markdown
            continue
        assert html == vector["text"]                      # 원문 표기가 html 이다
        assert "<table" in html and "<table" not in markdown
        assert "| 구분 | 연회비 |" in markdown
        for value in ("국내전용", "18,000원", "해외겸용", "20,000원"):
            assert value in html and value in markdown
        assert _content_tokens(markdown) == _content_tokens(vector["text"])


@pytest.mark.unit
def test_row_path_isolates_the_table_into_its_own_chunk():
    """표 독립 청크(table_as_chunk)도 경로에 따라 갈리지 않는다."""
    vectors = _chunk_rows(table_text_formats=("markdown",))
    table_chunks = [v for v in vectors if v["has_table"]]
    assert len(table_chunks) == 1
    body = table_chunks[0]["text"]
    # 표 조각에도 접두가 붙는다 — 어느 카드의 표인지 사라지면 검색 근거로 못 쓴다.
    assert body.startswith(ROW_PREFIX)
    # 표 앞 본문 문장은 다른 청크로 갈라져 있어야 한다.
    assert "연회비는 아래 표와 같다." not in body
    assert any("연회비는 아래 표와 같다." in v["text"] for v in vectors if not v["has_table"])


@pytest.mark.unit
def test_row_path_table_isolation_can_be_turned_off():
    off = _chunk_rows(table_as_chunk=False)
    on = _chunk_rows(table_as_chunk=True)
    assert len(off) < len(on)
    assert any("연회비는 아래 표와 같다." in v["text"] and v["has_table"] for v in off)


@pytest.mark.unit
def test_row_path_keeps_row_metadata_on_every_piece():
    """조각으로 나뉘어도 레코드 metadata 는 그대로 실린다(적재 측 식별 키)."""
    for vector in _chunk_rows(table_text_formats=("markdown",)):
        assert vector["PRODUCT_C"] == "AAP1344"
        assert vector["doc_type"] == "product_hpp"


@pytest.mark.unit
def test_row_path_masking_applies_to_variants_too():
    sensitive = [{"category": "민감", "quote_origin": "20,000원",
                  "quote_masked": "**,***원"}]
    vectors = _chunk_rows(payload_extra={"sensitive_infos": sensitive}, masking=True,
                          table_text_formats=("html", "markdown"))
    masked = [v for v in vectors if "**,***원" in v["text"]]
    assert masked, "마스킹이 적용되지 않으면 이 테스트가 아무것도 확인하지 않는다"
    for vector in masked:
        for field in VARIANT_FIELDS:
            assert "20,000원" not in vector[field], field
            assert "**,***원" in vector[field], field


@pytest.mark.unit
def test_row_path_without_table_is_unchanged():
    """표가 없는 doc_type(faq 등)의 청크 수와 본문은 그대로여야 한다."""
    module = pytest.importorskip("facade.chunking_processor")
    payload = {"elements": [
        {"category": "faq_row", "id": 0, "page": 1, "content": "질문\n답변",
         "metadata": {"doc_type": "faq"}},
        {"category": "faq_row", "id": 1, "page": 2, "content": "질문2\n답변2",
         "metadata": {"doc_type": "faq"}},
    ]}
    vectors = asyncio.run(module.DocumentProcessor()(
        request=None, file_path="/data/faq.json", document=payload))
    assert [v.text for v in vectors] == ["질문\n답변", "질문2\n답변2"]
