"""`table_text_description` 이 자체 LLM 연결로 독립 실행되는 경로의 계약 테스트.

표 설명은 원래 문서형 custom_fields LLM 호출에 얹혀 갔다. 그래서 custom_fields 가 LLM 을
쓰지 않는 문서유형(tabular/json 매핑 등)이나 매칭 설정이 아예 없는 문서에서는 표 설명이
만들어지지 않았다. `table_text_description` 블록에 url/model 을 두면 custom_fields 의 LLM
사용 여부와 무관하게 독립 실행되며, 그 요청에서는 융합 경로가 같은 표를 다시 설명하지 않는다.
"""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from docling_core.types.doc import (
    DocItemLabel,
    DoclingDocument,
    TableCell,
    TableData,
)

from genon.preprocessor.facade.enrichment.custom_fields_enricher import CustomFieldsEnricher
from genon.preprocessor.facade.enrichment.table_description import TableDescriptionExtractor
from genon.preprocessor.facade.enrichment.table_text_description import (
    TableTextDescriptionEnricher,
    apply_table_description_stage,
    build_text_table_targets,
    find_table_blocks,
)

TABLE_PROMPT = "표별 RAG 설명을 _table_descriptions JSON 배열로 반환하라."
CONTEXT = "본인카드의 해외겸용·국내전용 연회비 비교"

LLM_RESPONSE = json.dumps({
    "_table_descriptions": [{
        "table_id": "table_0001",
        "retrieval_context": CONTEXT,
        "key_facts": ["국내전용 총 연회비는 18,000원"],
        "search_terms": ["연회비"],
    }]
}, ensure_ascii=False)


def _document_with_table() -> DoclingDocument:
    doc = DoclingDocument(name="fee")
    doc.add_heading(text="본인 연회비", level=1)
    doc.add_text(label=DocItemLabel.TEXT, text="카드 연회비 정보를 확인할 수 있는 표다.")
    rows = [["구분", "해외겸용", "국내전용"], ["총 연회비", "20,000 원", "18,000 원"]]
    cells = [
        TableCell(
            text=value,
            start_row_offset_idx=r, end_row_offset_idx=r + 1,
            start_col_offset_idx=c, end_col_offset_idx=c + 1,
            column_header=r == 0,
        )
        for r, row in enumerate(rows) for c, value in enumerate(row)
    ]
    doc.add_table(data=TableData(num_rows=2, num_cols=3, table_cells=cells))
    return doc


def _standalone(**overrides) -> TableTextDescriptionEnricher:
    cfg = {
        "enabled": True,
        "url": "http://llm.invalid/v1/chat/completions",
        "model": "table-model",
        "prompt_template": TABLE_PROMPT,
        "before_items": 2,
        "after_items": 1,
    }
    cfg.update(overrides)
    return TableTextDescriptionEnricher(cfg)


def _stub_llm(enricher: TableTextDescriptionEnricher) -> AsyncMock:
    """독립 실행기가 내부에 세우는 표 전용 runner 의 LLM 호출만 대체한다."""
    runner = enricher._get_runner()
    call = AsyncMock(return_value=LLM_RESPONSE)
    runner._call_llm = call
    return call


def _custom_fields_enricher(**overrides) -> CustomFieldsEnricher:
    kwargs = dict(
        url="http://llm.invalid/v1/chat/completions",
        model="custom-fields-model",
        output_fields=["document_kind"],
        table_text_description={"enabled": True, "prompt_template": TABLE_PROMPT},
    )
    kwargs.update(overrides)
    return CustomFieldsEnricher(**kwargs)


def _retrieval(document: DoclingDocument) -> dict:
    return TableDescriptionExtractor.extract_retrieval(document.tables[0]) or {}


@pytest.mark.unit
def test_standalone_annotates_tables_with_a_single_call():
    doc = _document_with_table()
    enricher = _standalone()
    call = _stub_llm(enricher)

    asyncio.run(enricher.enrich(doc))

    assert call.await_count == 1
    # 표만 싣는 호출이므로 custom_fields 본문(raw_text)은 비어 있고 표 프롬프트만 붙는다.
    raw_text, _document, suffix = call.await_args.args
    assert raw_text == ""
    assert TABLE_PROMPT in suffix
    assert _retrieval(doc).get("retrieval_context") == CONTEXT


@pytest.mark.unit
def test_standalone_wins_over_custom_fields_and_blocks_fusion():
    """custom_fields 가 LLM 을 쓰든 말든 자체 연결이 있으면 독립 실행이 가져간다."""
    doc = _document_with_table()
    enricher = _standalone()
    call = _stub_llm(enricher)
    custom_fields = _custom_fields_enricher()
    kwargs: dict = {}
    image_calls: list = []

    assert custom_fields.wants_table_descriptions(**kwargs)  # 원래는 융합이 가져갔다

    asyncio.run(apply_table_description_stage(
        doc,
        custom_fields_enrichers=[custom_fields],
        standalone=enricher,
        run_image_stage=lambda document, **_: image_calls.append(document) or document,
        handle_error=lambda exc, stage: pytest.fail(f"{stage}: {exc}"),
        kwargs=kwargs,
    ))

    assert call.await_count == 1
    assert image_calls == []
    assert _retrieval(doc).get("retrieval_context") == CONTEXT
    # 뒤이어 도는 custom_fields 스테이지는 같은 표를 다시 설명하지 않는다.
    assert kwargs["_table_text_desc_owned"] is True
    assert not custom_fields.wants_table_descriptions(**kwargs)


@pytest.mark.unit
def test_without_own_connection_the_fused_path_still_owns_it():
    doc = _document_with_table()
    enricher = _standalone(url="", model="")
    custom_fields = _custom_fields_enricher()
    kwargs: dict = {}
    image_calls: list = []

    asyncio.run(apply_table_description_stage(
        doc,
        custom_fields_enrichers=[custom_fields],
        standalone=enricher,
        run_image_stage=lambda document, **_: image_calls.append(document) or document,
        handle_error=lambda exc, stage: pytest.fail(f"{stage}: {exc}"),
        kwargs=kwargs,
    ))

    # 융합 경로가 가져갔으므로 이 스테이지는 아무 호출도 하지 않는다(실제 호출은 custom_fields 때).
    assert image_calls == []
    assert "_table_text_desc_owned" not in kwargs
    assert custom_fields.wants_table_descriptions(**kwargs)


@pytest.mark.unit
def test_falls_back_to_image_stage_when_no_text_path_applies():
    doc = _document_with_table()
    enricher = _standalone(enabled=False)
    kwargs: dict = {}
    image_calls: list = []

    asyncio.run(apply_table_description_stage(
        doc,
        custom_fields_enrichers=[],
        standalone=enricher,
        run_image_stage=lambda document, **_: image_calls.append(document) or document,
        handle_error=lambda exc, stage: pytest.fail(f"{stage}: {exc}"),
        kwargs=kwargs,
    ))

    assert image_calls == [doc]


@pytest.mark.unit
def test_prefer_image_yields_to_the_image_stage():
    doc = _document_with_table()
    enricher = _standalone(conflict_policy="prefer_image")
    kwargs: dict = {}
    image_calls: list = []

    asyncio.run(apply_table_description_stage(
        doc,
        custom_fields_enrichers=[],
        standalone=enricher,
        run_image_stage=lambda document, **_: image_calls.append(document) or document,
        handle_error=lambda exc, stage: pytest.fail(f"{stage}: {exc}"),
        kwargs=kwargs,
    ))

    assert image_calls == [doc]
    assert "_table_text_desc_owned" not in kwargs


# ── 레코드 경로(docling 문서 없음) ────────────────────────────────────────────
# json/tabular 매핑은 표를 레코드 본문 문자열 안의 마크업으로만 갖는다. custom_fields 의
# extractor 종류와 무관하게 같은 표 설명이 붙어야 한다는 요구를 여기서 고정한다.

HTML_RECORD = """[상품 문서] 연회비
#### 본인 연회비

본인카드의 연회비 정보를 확인할 수 있는 표입니다.

<table><tbody><tr><th>구분</th><th>국내전용</th></tr><tr><th>총 연회비</th><td>18,000 원</td></tr></tbody></table>

#### 가족 연회비

가족카드는 없음
"""

MD_RECORD = """[상품 문서] 포인트

| 구분 | 적립률 |
| - | - |
| 기본 | 0.5% |

적립은 익월에 반영된다.
"""


def _record_response(*table_ids: str) -> str:
    return json.dumps({"_table_descriptions": [
        {
            "table_id": table_id,
            "retrieval_context": f"{table_id} 설명",
            "key_facts": [f"{table_id} 사실"],
            "search_terms": ["연회비"],
        }
        for table_id in table_ids
    ]}, ensure_ascii=False)


@pytest.mark.unit
def test_find_table_blocks_detects_html_and_markdown_tables():
    assert [fmt for _s, _e, fmt in find_table_blocks(HTML_RECORD)] == ["html"]
    assert [fmt for _s, _e, fmt in find_table_blocks(MD_RECORD)] == ["markdown"]
    # 파이프가 한 줄뿐이면 표로 보지 않는다(레이아웃 잔재).
    assert find_table_blocks("| 그냥 한 줄 |") == []


@pytest.mark.unit
def test_text_targets_carry_surrounding_context_and_heading():
    enricher = _standalone(before_items=2, after_items=1)
    target = build_text_table_targets(HTML_RECORD, enricher.options)[0]

    assert target.input_format == "html"
    assert target.section_header == "본인 연회비"
    assert "연회비 정보를 확인할 수 있는 표입니다." in target.before_context
    assert "가족 연회비" in target.after_context
    assert "18,000 원" in target.table_text


@pytest.mark.unit
def test_record_texts_get_the_same_retrieval_block_as_docling_chunks():
    enricher = _standalone()
    runner = enricher._get_runner()
    runner._call_llm = AsyncMock(return_value=_record_response("table_0001", "table_0002"))

    out = asyncio.run(enricher.describe_texts([HTML_RECORD, MD_RECORD]))

    # 레코드가 여러 건이어도 표를 한 번에 묶어 호출한다.
    assert runner._call_llm.await_count == 1
    for index, text in enumerate(out):
        table_id = f"table_{index + 1:04d}"
        assert f"[표 검색 설명]\n{table_id} 설명" in text
        assert f"핵심 사실: {table_id} 사실" in text
    # 설명은 표 바로 앞에 들어가고 본문은 그대로 남는다.
    assert out[0].index("[표 검색 설명]") < out[0].index("<table>")
    assert "가족카드는 없음" in out[0]
    assert "적립은 익월에 반영된다." in out[1]
    # include_search_terms 기본값(false)이면 검색어는 본문에 노출되지 않는다.
    assert "검색어:" not in out[0]


@pytest.mark.unit
def test_record_texts_without_tables_are_untouched_and_cost_nothing():
    enricher = _standalone()
    runner = enricher._get_runner()
    runner._call_llm = AsyncMock(return_value=_record_response("table_0001"))

    plain = ["표가 없는 레코드 본문이다.", ""]
    assert asyncio.run(enricher.describe_texts(plain)) == plain
    assert runner._call_llm.await_count == 0


@pytest.mark.unit
def test_record_texts_survive_llm_failure():
    enricher = _standalone()
    enricher._get_runner()._call_llm = AsyncMock(side_effect=RuntimeError("boom"))

    assert asyncio.run(enricher.describe_texts([HTML_RECORD])) == [HTML_RECORD]


@pytest.mark.unit
def test_parser_record_branch_describes_element_tables():
    """레코드 경로 조기 반환 직전에 표 설명이 붙는지(parser 배선) 고정한다."""
    parser_module = pytest.importorskip(
        "genon.preprocessor.facade.parser_processor", exc_type=ImportError
    )
    enricher = _standalone()
    enricher._get_runner()._call_llm = AsyncMock(return_value=_record_response("table_0001"))

    processor = object.__new__(parser_module.DocumentProcessor)
    processor._intel = SimpleNamespace(table_text_description_enricher=enricher)
    result = {"elements": [
        {"category": "custom_fields_row", "content": HTML_RECORD},
        {"category": "custom_fields_row", "content": "표 없는 레코드"},
    ]}

    described = asyncio.run(processor._describe_record_tables(result, doc_type="product_hpp"))

    assert "[표 검색 설명]" in described["elements"][0]["content"]
    assert described["elements"][1]["content"] == "표 없는 레코드"


@pytest.mark.unit
def test_parser_record_branch_is_a_noop_without_own_connection():
    parser_module = pytest.importorskip(
        "genon.preprocessor.facade.parser_processor", exc_type=ImportError
    )
    processor = object.__new__(parser_module.DocumentProcessor)
    processor._intel = SimpleNamespace(
        table_text_description_enricher=_standalone(url="", model="")
    )
    result = {"elements": [{"content": HTML_RECORD}]}

    assert asyncio.run(processor._describe_record_tables(result))["elements"][0][
        "content"
    ] == HTML_RECORD
