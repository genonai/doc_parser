"""doc_type 기반 custom_fields 라우팅과 tabular_mapping 단위 테스트."""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

from genon.preprocessor.facade.enrichment import custom_fields_enricher as cfe
from genon.preprocessor.facade.enrichment.custom_fields_enricher import (
    CustomFieldsEnricher,
    build_document_custom_fields_enrichers,
    matches_doc_type,
)
from genon.preprocessor.facade.enrichment.enrichment_config import EnrichmentConfig
from genon.preprocessor.facade.enrichment.tabular_custom_fields import (
    TabularCustomFieldsMapper,
    build_tabular_custom_fields_mappers,
)
from genon.preprocessor.facade.chunking_processor import DocumentProcessor as ChunkProcessor


@pytest.mark.unit
def test_enrichment_config_preserves_custom_fields_router_options(tmp_path):
    raw = [
        {"custom_fields": {
            "enable": True,
            "doc_type": "card",
            "extractor": "llm",
            "url": "http://example",
            "model": "model",
        }},
        {"custom_fields": {
            "enable": True,
            "doc_type": "faq",
            "extractor": "tabular_mapping",
            "config_file": "faq.yaml",
        }},
    ]
    config = EnrichmentConfig.from_raw(raw, tmp_path)
    assert [item["doc_type"] for item in config.custom_fields_cfgs] == ["card", "faq"]
    assert [item["extractor"] for item in config.custom_fields_cfgs] == [
        "llm", "tabular_mapping",
    ]
    assert all(item["resource_path"] == str(tmp_path) for item in config.custom_fields_cfgs)


@pytest.mark.unit
def test_doc_type_matching_is_normalized_and_missing_config_is_wildcard():
    assert matches_doc_type("card", " CARD ")
    assert matches_doc_type(["card", "faq"], "FAQ")
    assert not matches_doc_type("card", "faq")
    assert matches_doc_type(None, None)
    assert matches_doc_type(None, "anything")


@pytest.mark.unit
def test_document_factory_excludes_tabular_mapping(tmp_path):
    faq_config = tmp_path / "faq.yaml"
    faq_config.write_text("column_map: {}\n", encoding="utf-8")
    configs = [
        {
            "doc_type": "card",
            "extractor": "llm",
            "url": "http://example",
            "model": "model",
        },
        {
            "doc_type": "faq",
            "extractor": "tabular_mapping",
            "config_file": faq_config.name,
            "resource_path": str(tmp_path),
        },
    ]
    enrichers = build_document_custom_fields_enrichers(configs)
    mappers = build_tabular_custom_fields_mappers(configs)
    assert len(enrichers) == 1
    assert enrichers[0]._doc_types == ("card",)
    assert len(mappers) == 1
    assert mappers[0].doc_types == ("faq",)


@pytest.mark.unit
def test_unknown_custom_fields_extractor_fails_fast():
    with pytest.raises(ValueError, match="지원하지 않는 custom_fields extractor"):
        build_document_custom_fields_enrichers([{"extractor": "typo"}])


@pytest.mark.unit
def test_llm_custom_fields_runs_only_for_matching_doc_type(monkeypatch):
    enricher = CustomFieldsEnricher(
        doc_type="card",
        extractor="llm",
        url="http://example",
        model="model",
        output_fields=["product_name"],
    )
    doc = MagicMock()
    enricher._extract_raw_text = MagicMock(return_value="raw")
    enricher._call_llm = AsyncMock(return_value='{"product_name":"Card A"}')
    stored = []
    monkeypatch.setattr(cfe, "store_metadata_in_document", lambda document, metadata, **kwargs: stored.append(metadata))

    # faq 요청에서는 card LLM을 호출하지 않는다.
    assert asyncio.run(enricher.enrich(doc, doc_type="faq")) is doc
    enricher._call_llm.assert_not_awaited()

    ctx = {}
    assert asyncio.run(enricher.enrich(doc, doc_type="CARD", _enrichment_context=ctx)) is doc
    enricher._call_llm.assert_awaited_once()
    assert stored == [{"product_name": "Card A"}]
    assert ctx["metadata"] == {"product_name": "Card A"}


def _write_mapping(path: Path) -> Path:
    config = {
        "column_map": {
            "question": ["대표질문", "질문", "depth4"],
            "answer_text": ["답변", "description"],
            "category_code": ["분류", "depth3"],
            "needs_realtime_yn": ["실시간보완필요"],
        },
        "required": ["question", "answer_text", "needs_realtime_yn"],
        "defaults": {"needs_realtime_yn": "N"},
        "nulls": ["question_variant_text"],
        "text_fields": ["question", "answer_text"],
    }
    path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    return path


@pytest.mark.unit
def test_tabular_mapping_accepts_renamed_and_normalized_headers(tmp_path):
    config_path = _write_mapping(tmp_path / "faq.yaml")
    mapper = TabularCustomFieldsMapper(
        doc_type="faq",
        extractor="tabular_mapping",
        config_file=config_path.name,
        resource_path=str(tmp_path),
    )
    data = {
        "data": [{
            "sheet_name": "FAQ",
            "data_rows": [{
                " 대표 질문 ": "가입은 어떻게 하나요?",
                "\ufeff답변": "앱에서 가입할 수 있습니다.",
                "분 류": "가입",
            }],
        }]
    }
    result = mapper.to_parse_format(data, " FAQ ")
    assert result["metadata"] == {"doc_type": "faq"}
    assert len(result["elements"]) == 1
    element = result["elements"][0]
    assert element["category"] == "custom_fields_row"
    assert element["content"] == "가입은 어떻게 하나요?\n앱에서 가입할 수 있습니다."
    assert element["metadata"] == {
        "question_variant_text": None,
        "question": "가입은 어떻게 하나요?",
        "answer_text": "앱에서 가입할 수 있습니다.",
        "category_code": "가입",
        "needs_realtime_yn": "N",
        "doc_type": "faq",
    }


@pytest.mark.unit
def test_tabular_mapping_rejects_missing_required_column(tmp_path):
    config_path = _write_mapping(tmp_path / "faq.yaml")
    mapper = TabularCustomFieldsMapper(
        doc_type="faq",
        config_file=config_path.name,
        resource_path=str(tmp_path),
    )
    data = {"data": [{"sheet_name": "FAQ", "data_rows": [{"대표질문": "Q"}]}]}
    with pytest.raises(ValueError, match="필수 Excel 컬럼 매핑 실패"):
        mapper.to_parse_format(data, "faq")


@pytest.mark.unit
@pytest.mark.parametrize("category", ["tabular_row", "custom_fields_row", "faq_row"])
def test_chunker_accepts_generic_and_legacy_row_categories(category):
    processor = object.__new__(ChunkProcessor)
    vectors = processor._chunk_parse_format([{
        "category": category,
        "content": "질문\n답변",
        "page": 1,
        "metadata": {"question": "질문", "answer_text": "답변", "doc_type": "faq"},
    }])
    assert len(vectors) == 1
    vector = vectors[0].model_dump()
    assert vector["question"] == "질문"
    assert vector["answer_text"] == "답변"
    assert vector["doc_type"] == "faq"
