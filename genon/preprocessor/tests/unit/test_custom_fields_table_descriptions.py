"""custom_fields 통합 텍스트 표 설명의 호출·RAG annotation 계약 테스트."""

import asyncio
import json
import re
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from docling_core.types.doc import (
    DocItemLabel,
    DoclingDocument,
    TableCell,
    TableData,
)

from genon.preprocessor.facade.enrichment.custom_fields_enricher import CustomFieldsEnricher
from genon.preprocessor.facade.enrichment.field_transforms import extract_metadata_from_document
from genon.preprocessor.facade.enrichment.table_description import TableDescriptionExtractor


SAMPLE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "table_text_description"
TEST_TABLE_PROMPT = "표별 RAG 설명을 _table_descriptions JSON 배열로 반환하라."


def _add_table(doc: DoclingDocument, rows: list[list[str]]):
    cells = []
    for row_index, row in enumerate(rows):
        for column_index, value in enumerate(row):
            cells.append(TableCell(
                text=value,
                start_row_offset_idx=row_index,
                end_row_offset_idx=row_index + 1,
                start_col_offset_idx=column_index,
                end_col_offset_idx=column_index + 1,
                column_header=row_index == 0,
            ))
    return doc.add_table(data=TableData(
        num_rows=len(rows), num_cols=len(rows[0]), table_cells=cells
    ))


def _document_with_two_tables() -> DoclingDocument:
    doc = DoclingDocument(name="table_context")
    doc.add_heading(text="기업 요금제", level=1)
    doc.add_text(label=DocItemLabel.TEXT, text="서울 기업 고객의 12개월 상품 비교다.")
    _add_table(doc, [["상품", "월 요금"], ["스타터", "190,000원"]])
    doc.add_text(label=DocItemLabel.TEXT, text="비즈니스 이상은 감사 로그를 제공한다.")
    doc.add_heading(text="장애 대응", level=1)
    doc.add_text(label=DocItemLabel.TEXT, text="2026년 운영센터 복구 실적이다.")
    _add_table(doc, [["센터", "복구시간"], ["서울", "38분"]])
    doc.add_text(label=DocItemLabel.TEXT, text="접수부터 정상화까지의 평균이다.")
    return doc


@pytest.mark.unit
def test_multiple_tables_share_one_custom_fields_llm_call_and_are_annotated():
    doc = _document_with_two_tables()
    enricher = CustomFieldsEnricher(
        url="http://llm.invalid/v1/chat/completions",
        model="test-model",
        output_fields=["document_kind"],
        table_text_description={
            "enabled": True, "before_items": 2, "after_items": 1,
            "prompt_template": TEST_TABLE_PROMPT,
        },
    )
    response = {
        "document_kind": "상품 안내",
        "_table_descriptions": [
            {
                "table_id": "table_0001",
                "retrieval_context": "서울 기업 고객용 12개월 요금제의 상품별 월 요금 비교",
                "key_facts": ["스타터 월 요금은 190,000원"],
                "search_terms": ["서울 기업 요금제", "스타터 월 요금"],
            },
            {
                "table_id": "table_0002",
                "retrieval_context": "2026년 운영센터별 장애 평균 복구시간 실적",
                "key_facts": ["서울 센터 평균 복구시간은 38분"],
                "search_terms": ["서울 장애 복구시간"],
            },
        ],
    }
    enricher._call_llm = AsyncMock(return_value=json.dumps(response, ensure_ascii=False))

    asyncio.run(enricher.enrich(doc))

    assert enricher._call_llm.await_count == 1
    suffix = enricher._call_llm.await_args.args[2]
    assert 'id="table_0001"' in suffix and 'id="table_0002"' in suffix
    assert "서울 기업 고객의 12개월 상품 비교" in suffix
    assert "접수부터 정상화까지의 평균" in suffix
    assert TableDescriptionExtractor.retrieval_text(doc.tables[0]).startswith("서울 기업 고객용")
    assert TableDescriptionExtractor.retrieval_text(doc.tables[1], split_piece=True).endswith("복구시간 실적")

    metadata = extract_metadata_from_document(doc)
    assert metadata["document_kind"] == "상품 안내"
    assert "_table_descriptions" not in metadata


@pytest.mark.unit
def test_text_table_description_runtime_flag_can_enable_config_default():
    enricher = CustomFieldsEnricher(
        url="http://llm.invalid", model="test-model",
        table_text_description={"enabled": False, "prompt_template": TEST_TABLE_PROMPT},
    )
    assert not enricher.wants_table_descriptions()
    assert enricher.wants_table_descriptions(table_text_desc=1)
    assert not enricher.wants_table_descriptions(table_text_desc=0)


@pytest.mark.unit
def test_table_prompt_template_file_is_loaded_from_resource_yaml_directory():
    resource_dir = Path(__file__).resolve().parents[2] / "resource"
    enricher = CustomFieldsEnricher(
        url="http://llm.invalid",
        model="test-model",
        resource_path=str(resource_dir),
        table_text_description={
            "enabled": True,
            "prompt_template_file": "prompt_table_text_description_rag.md",
        },
    )
    assert "_table_descriptions" in enricher._table_description_options.prompt_template
    assert "RAG 검색용 설명" in enricher._table_description_options.prompt_template


@pytest.mark.unit
@pytest.mark.parametrize(
    ("filename", "input_format", "expected_input_format", "export_to_html"),
    [
        ("table_description_rag_sample.md", "auto", "markdown", 0),
        ("table_description_rag_sample.html", "auto", "html", 1),
    ],
)
def test_actual_md_html_samples_parse_describe_once_and_chunk(
    filename, input_format, expected_input_format, export_to_html
):
    from docling.document_converter import DocumentConverter
    from genon.preprocessor.facade.chunking_processor import GenosSmartChunker

    doc = DocumentConverter().convert(SAMPLE_DIR / filename).document
    assert len(doc.tables) == 2
    assert doc.tables[1].data.num_rows == 37

    enricher = CustomFieldsEnricher(
        url="http://llm.invalid/v1/chat/completions",
        model="test-model",
        output_fields=["document_kind"],
        table_text_description={
            "enabled": True, "input_format": input_format,
            "prompt_template": TEST_TABLE_PROMPT,
        },
    )
    response = {
        "document_kind": "기업 운영 안내",
        "_table_descriptions": [
            {
                "table_id": "table_0001",
                "retrieval_context": "서울 기업 고객의 12개월 기본 요금제 비교",
                "key_facts": ["스타터 월 요금은 190,000원"],
                "search_terms": ["기업 요금제"],
            },
            {
                "table_id": "table_0002",
                "retrieval_context": "2026년 서울·부산 센터의 월별 장애와 평균 복구시간",
                "key_facts": ["1월 서울 평균 복구시간은 38분"],
                "search_terms": ["서울 부산 복구시간"],
            },
        ],
    }
    enricher._call_llm = AsyncMock(return_value=json.dumps(response, ensure_ascii=False))
    asyncio.run(enricher.enrich(doc))
    assert enricher._call_llm.await_count == 1
    assert f"[표 본문 format={expected_input_format}]" in enricher._call_llm.await_args.args[2]

    chunker = GenosSmartChunker(
        max_tokens=1024, chunk_mode="split_only", tokenizer_type="char"
    )
    table_chunks = [
        chunk.text
        for chunk in chunker.chunk(dl_doc=doc, export_to_html=export_to_html)
        if "[표 검색 설명]" in chunk.text
    ]
    assert len(table_chunks) >= 3  # 작은 표 1개 + 큰 표가 2개 이상으로 분할
    assert all(len(text) <= 1024 for text in table_chunks)
    assert all(text.count("[표 검색 설명]") == 1 for text in table_chunks)
    large_pieces = [text for text in table_chunks if "평균 복구시간" in text]
    assert len(large_pieces) >= 2
    assert all("2026년 서울·부산 센터" in text for text in large_pieces)


@pytest.mark.unit
def test_doc_type_yaml_overrides_processor_common_table_text_description(tmp_path):
    """문서유형 YAML 값이 프로세서 공통값을 덮고, rag 하위는 키 단위로 병합된다."""
    (tmp_path / "cf.yaml").write_text(
        "url: http://llm.invalid\n"
        "model: test-model\n"
        "output_fields: [document_kind]\n"
        "table_text_description:\n"
        "  enabled: true\n"
        "  prompt_template: 문서유형 프롬프트\n"
        "  after_items: 6\n"
        "  rag:\n"
        "    key_fact_limit: 5\n",
        encoding="utf-8",
    )
    enricher = CustomFieldsEnricher(
        config_file="cf.yaml",
        resource_path=str(tmp_path),
        table_text_description={
            "enabled": False,
            "before_items": 4,
            "rag": {"key_fact_limit": 1, "search_terms_limit": 7},
        },
    )
    options = enricher._table_description_options

    assert options.enabled is True          # 문서유형 값이 공통값을 이긴다
    assert options.after_items == 6
    assert options.before_items == 4        # 문서유형에 없는 키는 공통값이 살아남는다
    assert options.key_fact_limit == 5
    assert options.search_terms_limit == 7  # rag 하위도 키 단위 병합


@pytest.mark.unit
def test_runtime_flag_is_ignored_when_prompt_is_not_configured():
    """프롬프트가 없으면 전역 런타임 플래그로도 켜지지 않는다(custom_fields 보호)."""
    enricher = CustomFieldsEnricher(
        url="http://llm.invalid", model="test-model",
        table_text_description={"enabled": True},
    )
    assert not enricher.wants_table_descriptions(table_text_desc=1)


@pytest.mark.unit
def test_custom_fields_survive_table_batch_failure():
    """표 설명 추가 호출이 실패해도 이미 추출한 custom fields 는 유지된다."""
    doc = _document_with_two_tables()
    enricher = CustomFieldsEnricher(
        url="http://llm.invalid", model="test-model", output_fields=["document_kind"],
        table_text_description={
            "enabled": True, "prompt_template": TEST_TABLE_PROMPT,
            "max_context_tokens": 10, "completion_reserved_tokens": 0,
        },
    )
    calls = {"n": 0}

    async def fake_call(raw_text, document=None, user_suffix=""):
        calls["n"] += 1
        if calls["n"] == 1:
            return json.dumps({"document_kind": "상품 안내"}, ensure_ascii=False)
        raise RuntimeError("표 배치 호출 실패")

    enricher._call_llm = fake_call
    asyncio.run(enricher.enrich(doc))

    assert extract_metadata_from_document(doc)["document_kind"] == "상품 안내"
    assert TableDescriptionExtractor.retrieval_text(doc.tables[0]) == ""


@pytest.mark.unit
def test_oversized_single_table_is_skipped_instead_of_being_sent():
    """표 하나가 예산을 넘으면 그 표만 건너뛰고 나머지 표는 배치로 처리한다."""
    doc = _document_with_two_tables()
    _add_table(doc, [["항목", "값"], ["설명" * 400, "1"]])
    enricher = CustomFieldsEnricher(
        url="http://llm.invalid", model="test-model", output_fields=["document_kind"],
        table_text_description={
            "enabled": True, "prompt_template": TEST_TABLE_PROMPT,
            "max_context_tokens": 1200, "completion_reserved_tokens": 0,
        },
    )
    sent_ids: list[list[str]] = []

    async def fake_call(raw_text, document=None, user_suffix=""):
        ids = re.findall(r'id="(table_\d+)"', user_suffix or "")
        sent_ids.append(ids)
        return json.dumps({
            "document_kind": "상품 안내",
            "_table_descriptions": [
                {"table_id": table_id, "retrieval_context": f"{table_id} 설명",
                 "key_facts": [], "search_terms": []}
                for table_id in ids
            ],
        }, ensure_ascii=False)

    enricher._call_llm = fake_call
    asyncio.run(enricher.enrich(doc))

    described = [
        bool(TableDescriptionExtractor.retrieval_text(table)) for table in doc.tables
    ]
    assert described == [True, True, False]
    assert "table_0003" not in [table_id for ids in sent_ids for table_id in ids]


@pytest.mark.unit
def test_table_description_survives_docling_json_round_trip():
    """parser 가 내보낸 docling JSON 을 chunker 가 다시 읽어도 표 설명이 남아야 한다."""
    doc = _document_with_two_tables()
    enricher = CustomFieldsEnricher(
        url="http://llm.invalid", model="test-model", output_fields=["document_kind"],
        table_text_description={"enabled": True, "prompt_template": TEST_TABLE_PROMPT},
    )
    enricher._call_llm = AsyncMock(return_value=json.dumps({
        "document_kind": "상품 안내",
        "_table_descriptions": [
            {"table_id": "table_0001", "retrieval_context": "요금제 비교",
             "key_facts": ["스타터 월 요금은 190,000원"], "search_terms": []},
            {"table_id": "table_0002", "retrieval_context": "복구시간 실적",
             "key_facts": [], "search_terms": []},
        ],
    }, ensure_ascii=False))
    asyncio.run(enricher.enrich(doc))

    restored = DoclingDocument.model_validate(doc.export_to_dict())

    assert TableDescriptionExtractor.retrieval_text(restored.tables[0]).startswith("요금제 비교")
    assert "핵심 사실: 스타터 월 요금은 190,000원" in TableDescriptionExtractor.retrieval_text(
        restored.tables[0]
    )
    assert TableDescriptionExtractor.retrieval_text(
        restored.tables[1], split_piece=True
    ) == "복구시간 실적"
