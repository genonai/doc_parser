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

from genon.preprocessor.processing.enrichment.custom_fields_enricher import CustomFieldsEnricher
from genon.preprocessor.processing.enrichment.table_description import TableDescriptionExtractor
from genon.preprocessor.processing.enrichment.table_text_description import (
    TableTextDescriptionEnricher,
    apply_table_description_stage,
    build_text_table_targets,
    find_table_blocks,
    text_table_skip_reason,
)
from genon.preprocessor.processing.enrichment.table_text_context import (
    collect_table_text_targets,
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


# 표 설명 런타임 실패는 문서를 막지 않는다(부가 기능). 설정 충돌은 그대로 facade 정책을 탄다.

@pytest.mark.unit
def test_runtime_failure_does_not_reach_the_facade_error_policy():
    """LLM 실패는 strict facade 에서도 문서를 죽이지 않는다 — handle_error 로 가지 않는다."""
    doc = _document_with_table()
    enricher = _standalone()
    runner = enricher._get_runner()
    runner._call_llm = AsyncMock(side_effect=RuntimeError("게이트웨이 오류"))
    kwargs: dict = {}

    result = asyncio.run(apply_table_description_stage(
        doc,
        custom_fields_enrichers=[],
        standalone=enricher,
        run_image_stage=lambda document, **_: document,
        # strict facade 를 흉내낸다 — 여기로 오면 문서 전체가 실패한다.
        handle_error=lambda exc, stage: pytest.fail(f"런타임 실패가 에러 정책을 탔다: {stage}"),
        kwargs=kwargs,
    ))

    assert result is doc, "표 설명 없이도 문서는 그대로 흘러야 한다"
    assert _retrieval(doc) == {}, "실패했으므로 설명은 붙지 않는다"


@pytest.mark.unit
def test_image_stage_runtime_failure_is_also_skipped():
    """이미지 표 설명 경로의 런타임 실패도 같은 정책을 탄다."""
    doc = _document_with_table()
    enricher = _standalone(enabled=False)

    def _boom(document, **_):
        raise RuntimeError("이미지 표 설명 실패")

    result = asyncio.run(apply_table_description_stage(
        doc,
        custom_fields_enrichers=[],
        standalone=enricher,
        run_image_stage=_boom,
        handle_error=lambda exc, stage: pytest.fail(f"런타임 실패가 에러 정책을 탔다: {stage}"),
        kwargs={},
    ))

    assert result is doc


@pytest.mark.unit
def test_config_conflict_still_reaches_the_facade_error_policy():
    """설정 오기입(텍스트·이미지 동시 활성)은 조용히 넘기지 않는다."""
    doc = _document_with_table()
    enricher = _standalone(conflict_policy="error")
    seen: list = []

    asyncio.run(apply_table_description_stage(
        doc,
        custom_fields_enrichers=[],
        standalone=enricher,
        run_image_stage=lambda document, **_: document,
        handle_error=lambda exc, stage: seen.append(stage),
        kwargs={"table_desc": True},
    ))

    assert seen == ["table_description"], "설정 충돌은 facade 의 에러 정책으로 올라가야 한다"


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


# ── LLM 전송 제외(#530) ──────────────────────────────────────────────────────
# 표는 모니모 실물 샘플에서 옮겼다. 배너는 monimo_cs_hpp_rich_table_sample.html, 나머지는
# monimo_product_ssf_bold_title_sample.md·monimo_product_ssf_sample.md 의 표다.

BANNER_TEXT = (
    "해당 목차는 [AI 에이전트용] 이므로, 실제 상담 시에는 세부 지침이 포함된 "
    "[상담직원용] 컨텐츠를 활용하시기 바랍니다."
)
BANNER_RECORD = f'<table class="__se_tbl"><tbody><tr><td colspan="4">{BANNER_TEXT}</td></tr></tbody></table>'

REPAIR_TABLE = """| 구분 | 내용 |
| --- | --- |
| 원상태 복구 | 피해물이 사고직전의 원상태로 복구가 가능한 품목은 수리비용을 보상 |
| 수리비 초과 | 수리비가 사고직전 가액을 초과하는 경우는 사고직전 가액 보상 |
"""
SELF_DESC_RECORD = "**ㅁ 피해물이 상품인 경우 보상 기준**\n\n" + REPAIR_TABLE
NO_TITLE_RECORD = "전쟁·테러 지역 여행은 인수가 제한됩니다.\n\n" + REPAIR_TABLE
AGE_RECORD = """## 원문 3쪽 - 보험가입자격요건

| 구분 | 내용 |
| --- | --- |
| 가입나이 | 만 0세 ~ 79세 |
| 보험기간 | 여행 일정과 동일 (최대 90일) |
| 납입기간 | 일시납 |
"""
AMOUNT_RECORD = """**ㅁ 소지품(보상가능) 과 휴대품(보상불가)**

| 구분 | 내용 |
| --- | --- |
| 소지품 | 휴대품을 제외한 물품으로 장착되어 있지 않고 휴대할 수 있는 물품 |
| 보상 | 피보험자의 가족을 제외한 탑승자와 통행인의 훼손된 소지품 1인당 200만원 한도 보상 |
| 면책 | 탑승자와 통행인의 분실 또는 도난으로 인한 소지품 손해는 보상불가 |
"""


@pytest.mark.unit
@pytest.mark.parametrize("record, skip, expected", [
    (BANNER_RECORD, {}, "layout"),                                   # 청커가 평문으로 내는 배너
    (BANNER_RECORD, {"layout": False}, None),
    (SELF_DESC_RECORD, {}, None),                                    # 자기서술형은 기본 off
    (SELF_DESC_RECORD, {"self_describing": True}, "self_describing"),
    (NO_TITLE_RECORD, {"self_describing": True}, None),              # 표 위 제목 줄 없음
    ("**[AI 에이전트용]**\n\n" + REPAIR_TABLE, {"self_describing": True}, None),  # 굵은 태그는 제목 아님
    (AGE_RECORD, {"self_describing": True}, None),                   # 키-값 조건표
    (AMOUNT_RECORD, {"self_describing": True}, None),                # 금액 값
])
def test_record_table_skip_reason(record, skip, expected):
    options = _standalone(skip=skip).options
    target = build_text_table_targets(record, options)[0]
    assert text_table_skip_reason(target, options) == expected


@pytest.mark.unit
def test_record_skipped_table_is_not_sent_and_next_table_keeps_its_description():
    """뺀 표가 앞에 있어도 설명은 원래 표 앞에 붙는다(삽입 짝이 밀리지 않음).

    모델이 보내지 않은 표의 ID(table_0001)로도 답하면 그 답은 버린다.
    """
    enricher = _standalone()
    runner = enricher._get_runner()
    runner._call_llm = AsyncMock(return_value=_record_response("table_0001", "table_0002"))

    out = asyncio.run(enricher.describe_texts([BANNER_RECORD + "\n\n" + HTML_RECORD]))[0]

    _raw, _doc, suffix = runner._call_llm.await_args.args
    assert ('id="table_0001"' in suffix, 'id="table_0002"' in suffix) == (False, True)
    assert out.index("[표 검색 설명]\ntable_0002 설명") < out.index("<table><tbody><tr><th>구분")
    assert out.count("[표 검색 설명]") == 1 and out.index("[표 검색 설명]") > out.index(BANNER_TEXT)


@pytest.mark.unit
def test_document_layout_table_is_not_collected():
    doc = _document_with_table()
    banner = TableCell(
        text=BANNER_TEXT, start_row_offset_idx=0, end_row_offset_idx=1,
        start_col_offset_idx=0, end_col_offset_idx=1,
    )
    doc.add_table(data=TableData(num_rows=1, num_cols=1, table_cells=[banner]))

    targets = collect_table_text_targets(doc, _standalone().options)

    assert [t.table_item.data.num_cols for t in targets] == [3]


# 실물 발췌: 검증 문서군 web15(sample_06.html) 암수술특약 표. 지급금액 셀 안에 표가 들어 있다.
_NESTED_TABLE_HTML = """<table border="1" style="width: 100%; border-collapse: collapse;">
<thead>
<tr>
<th style="width: 15%; background-color: #4A7EBB; color: white;">구분</th>
<th style="width: 50%; background-color: #4A7EBB; color: white;">지급사유</th>
<th style="width: 35%; background-color: #4A7EBB; color: white;">지급금액</th>
</tr>
</thead>
<tbody>
<tr>
<td rowspan="2" style="background-color: #B4D7F7;">암수술<br/>급여금</td>
<td rowspan="2" style="background-color: #B4D7F7;">
                      특약보험기간 중 피보험자가 보장개시일(암의 경우 암보장개시일) 이후에 「암, 대장점막내암 또는 비침습방광암」, 기타피부암, 갑상선암, 제자리암 또는 경계성종양으로 진단확정
                      되고, 특약보험기간 중 그 「암, 대장점막내암 또는 비침습방광암」, 기타피부암, 갑상선암, 제자리암 또는 경계성종양의 직접적인 치료를 목적으로 수술을 받았을 때
                  </td>
<td style="background-color: #E8E8E8;"><b>【수술 1회당】</b></td>
</tr>
<tr>
<td style="background-color: #B4D7F7;">
<table border="0" style="width: 100%; border-collapse: collapse;">
<tr>
<td style="border-right: 1px solid #999;">암,<br/>대장점막내암<br/>또는<br/>비침습방광암</td>
<td>100만원<br/>(다만, 계약일부터 1년 미만에 지급사유가 발생한 경우 상기 지급금액의 50%를 지급함)</td>
</tr>
<tr>
<td style="border-right: 1px solid #999;">기타피부암,<br/>갑상선암,<br/>제자리암,<br/>경계성종양</td>
<td>20만원<br/>(다만, 계약일부터 1년 미만에 지급사유가 발생한 경우 상기 지급금액의 50%를 지급함)</td>
</tr>
</table>
</td>
</tr>
</tbody>
</table>"""


@pytest.mark.unit
def test_document_table_nested_in_another_table_is_not_collected(tmp_path):
    """다른 표의 셀 안에 든 표는 LLM 에 보내지 않는다(#567).

    청커는 중첩 표를 바깥 표 셀 텍스트로만 싣고 그 표의 설명은 청크에 싣지 않는다.
    """
    pytest.importorskip("docling.document_converter", exc_type=ImportError)
    from docling.document_converter import DocumentConverter

    path = tmp_path / "nested.html"
    path.write_text(f"<!doctype html><html><body>{_NESTED_TABLE_HTML}</body></html>", encoding="utf-8")
    doc = DocumentConverter().convert(path).document

    targets = collect_table_text_targets(doc, _standalone().options)

    assert len(doc.tables) == 2
    assert [t.table_item.self_ref for t in targets] == [doc.tables[0].self_ref]


@pytest.mark.unit
def test_child_table_outside_rich_cells_is_still_collected():
    """parent 만 표이고 rich cell 이 가리키지 않는 표는 청커가 자체 블록으로 싣으므로 보낸다(#567).

    생성 데이터: 실물 HTML 에서는 이런 구조가 나오지 않는다(외부 Docling JSON·훅이 만들 수 있다).
    표 값은 아래 보존치료 표(실물 발췌)를 따랐다.
    """
    doc = DoclingDocument(name="child")
    _add_table(doc, [["구분", "지급금액"], ["인레이, 온레이", "15만원"], ["복합레진", "5만원"]])
    _add_table(doc, [["구분", "지급금액"], ["인레이, 온레이", "5만원"], ["복합레진", "15만원"]],
               parent=doc.tables[0])

    targets = collect_table_text_targets(doc, _standalone().options)

    assert [t.table_item.self_ref for t in targets] == [t.self_ref for t in doc.tables]


def _add_table(doc: DoclingDocument, rows: list[list[str]], parent=None) -> None:
    cells = [
        TableCell(
            text=value,
            start_row_offset_idx=r, end_row_offset_idx=r + 1,
            start_col_offset_idx=c, end_col_offset_idx=c + 1,
            column_header=r == 0,
        )
        for r, row in enumerate(rows) for c, value in enumerate(row)
    ]
    doc.add_table(data=TableData(num_rows=len(rows), num_cols=len(rows[0]), table_cells=cells), parent=parent)


@pytest.mark.unit
@pytest.mark.parametrize("skip, expected", [
    ({}, ["최초로 보존치료를 받은 경우 ◇ 계약일로부터 1년초과 구분 지급금액 인레이, 온레이 15만원 복합레진 5만원",
          "15만원", "5만원"]),
    ({"contained_duplicate": True}, [
        "최초로 보존치료를 받은 경우 ◇ 계약일로부터 1년초과 구분 지급금액 인레이, 온레이 15만원 복합레진 5만원",
        "5만원",
    ]),
])
def test_document_table_contained_in_previous_table_is_not_collected(skip, expected):
    """앞 표의 병합 셀에 통째로 전사된 하위 표는 빼고, 금액만 뒤바뀐 표는 보낸다."""
    doc = DoclingDocument(name="slf")
    doc.add_heading(text="원문 6쪽 - 보존치료", level=2)
    _add_table(doc, [
        ["보험금", "지급사유"],
        ["보존치료 보험금", expected[0]],
    ])
    _add_table(doc, [["구분", "지급금액"], ["인레이, 온레이", "15만원"], ["복합레진", "5만원"]])
    _add_table(doc, [["구분", "지급금액"], ["인레이, 온레이", "5만원"], ["복합레진", "15만원"]])

    targets = collect_table_text_targets(doc, _standalone(skip=skip).options)

    assert [t.table_item.data.grid[1][1].text for t in targets] == expected


@pytest.mark.unit
def test_zero_search_terms_limit_keeps_no_terms():
    """search_terms_limit: 0 이면 모델이 검색어를 돌려줘도 하나도 남기지 않는다(문서·레코드 경로)."""
    cfg = {"rag": {"search_terms_limit": 0, "include_search_terms": True}}
    doc = _document_with_table()
    enricher = _standalone(**cfg)
    _stub_llm(enricher)
    asyncio.run(enricher.enrich(doc))

    record = _standalone(**cfg)
    record._get_runner()._call_llm = AsyncMock(return_value=_record_response("table_0001"))
    out = asyncio.run(record.describe_texts([HTML_RECORD]))[0]

    assert (_retrieval(doc).get("search_terms"), "검색어:" in out) == ([], False)
