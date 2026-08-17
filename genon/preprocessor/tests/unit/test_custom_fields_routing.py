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


# ── value_map / transforms / llm_fields (모니모 15종 지원) ────────────────────

@pytest.mark.unit
def test_value_map_folds_aliases_and_keeps_unmapped_value(tmp_path, caplog):
    """GROUP_C 처럼 원천 표기가 흔들리는 값을 표준 코드로 접는다.

    매핑표에 없는 값은 조용히 null 로 만들지 않고 원값 유지 + 경고 — 그래야 표준화 누락이
    적재 직전이 아니라 파싱 로그에서 드러난다.
    """
    from genon.preprocessor.facade.enrichment.tabular_custom_fields import (
        apply_value_map,
        compile_value_map,
    )

    compiled = compile_value_map({"GROUP_C": {"SLF": ["삼성생명", "생명"], "HPP": ["삼성카드"]}})
    fields = {"GROUP_C": " 삼성생명 "}
    apply_value_map(fields, compiled)
    assert fields["GROUP_C"] == "SLF"

    # 표준값 자신도 별칭이라 이미 코드로 오는 원천(corp_code=HPP)은 그대로 통과한다.
    fields = {"GROUP_C": "HPP"}
    apply_value_map(fields, compiled)
    assert fields["GROUP_C"] == "HPP"

    with caplog.at_level("WARNING"):
        fields = {"GROUP_C": "삼성전자"}
        apply_value_map(fields, compiled)
    assert fields["GROUP_C"] == "삼성전자"
    assert "value_map 미등록 값" in caplog.text


@pytest.mark.unit
def test_value_map_rejects_alias_claimed_by_two_canonicals():
    from genon.preprocessor.facade.enrichment.tabular_custom_fields import compile_value_map

    with pytest.raises(ValueError, match="양쪽에 있습니다"):
        compile_value_map({"GROUP_C": {"SLF": ["생명"], "SSF": ["생명"]}})


@pytest.mark.unit
def test_tabular_mapping_applies_value_map_and_transforms(tmp_path):
    config = {
        "column_map": {"GROUP_C": ["회사명"], "TERM": ["용어"], "TERM_NORM": ["용어"]},
        "value_map": {"GROUP_C": {"SLF": ["삼성생명", "생명"]}},
        "transforms": {"TERM_NORM": "text_norm"},
        "required": ["GROUP_C", "TERM"],
        "text_fields": ["TERM"],
    }
    path = tmp_path / "term.yaml"
    path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")

    mapper = TabularCustomFieldsMapper(
        doc_type="term", extractor="tabular_mapping",
        config_file=path.name, resource_path=str(tmp_path),
    )
    data = {"data": [{"sheet_name": "용어", "data_rows": [
        {"회사명": "생명", "용어": " 해지  환급금 "},
    ]}]}
    metadata = mapper.to_parse_format(data, "term")["elements"][0]["metadata"]
    assert metadata["GROUP_C"] == "SLF"
    # 같은 컬럼을 두 목표필드로 매핑한 뒤 한쪽만 정규화한다(별도 파생 기능 없이 TERM_NORM 확보).
    assert metadata["TERM"] == "해지  환급금"
    assert metadata["TERM_NORM"] == "해지 환급금"


@pytest.mark.unit
def test_tabular_build_fields_and_to_parse_format_round_trip(tmp_path):
    """3단 분리(build_fields → LLM → to_parse_format)가 1단 호출과 같은 결과를 낸다."""
    config_path = _write_mapping(tmp_path / "faq.yaml")
    mapper = TabularCustomFieldsMapper(
        doc_type="faq", extractor="tabular_mapping",
        config_file=config_path.name, resource_path=str(tmp_path),
    )
    data = {"data": [{"sheet_name": "FAQ", "data_rows": [
        {"대표질문": "Q1", "답변": "A1"},
        {"대표질문": "Q2", "답변": "A2"},
    ]}]}

    one_shot = mapper.to_parse_format(data, "faq")
    fields_list = mapper.build_fields(data, "faq")
    assert len(fields_list) == 2
    split = mapper.to_parse_format_from_fields(fields_list, "faq")
    assert split == one_shot

    # llm_fields 의 skip_record 로 일부 행이 빠져도 남은 행의 page/본문이 어긋나지 않는다.
    partial = mapper.to_parse_format_from_fields(mapper.build_fields(data, "faq")[1:], "faq")
    assert len(partial["elements"]) == 1
    assert partial["elements"][0]["content"] == "Q2\nA2"
    assert partial["elements"][0]["metadata"]["question"] == "Q2"
    # 내부 전용 예약 키는 metadata 로 새어 나가지 않는다.
    assert not [k for k in partial["elements"][0]["metadata"] if k.startswith("__cf_")]


@pytest.mark.unit
def test_tabular_mapping_compiles_llm_fields(tmp_path):
    """tabular 도 json_mapping 과 같은 llm_fields 스펙을 받는다(요약본문 생성용)."""
    config = {
        "column_map": {"TITLE": ["제목"]},
        "required": ["TITLE"],
        "text_fields": ["TITLE", "SUMMARY_TEXT"],
        "llm_fields": [{
            "output_fields": ["SUMMARY_TEXT"],
            "input_fields": ["TITLE"],
            "url": "http://example/v1/chat/completions",
            "model": "model",
        }],
    }
    path = tmp_path / "cs.yaml"
    path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")

    mapper = TabularCustomFieldsMapper(
        doc_type="cs_slf", extractor="tabular_mapping",
        config_file=path.name, resource_path=str(tmp_path),
    )
    assert len(mapper.llm_field_specs) == 1
    spec = mapper.llm_field_specs[0]
    assert spec.output_fields == ["SUMMARY_TEXT"]
    assert spec.build_input_text({"TITLE": "보험금 청구"}) == "보험금 청구"
    # parser 가 enricher 를 만들 때 쓰는 기준 경로가 매퍼에 남아 있어야 한다.
    assert mapper.resource_path == str(tmp_path)


@pytest.mark.unit
def test_tabular_mapping_rejects_unknown_transform(tmp_path):
    config = {
        "column_map": {"TITLE": ["제목"]},
        "transforms": {"TITLE": "does_not_exist"},
        "text_fields": ["TITLE"],
    }
    path = tmp_path / "bad.yaml"
    path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    with pytest.raises(ValueError, match="등록되지 않은 transforms 변환기"):
        TabularCustomFieldsMapper(
            doc_type="x", extractor="tabular_mapping",
            config_file=path.name, resource_path=str(tmp_path),
        )


@pytest.mark.unit
def test_llm_extractor_constants_survive_and_win(tmp_path):
    """extractor=llm 도 constants 로 고정값을 채운다(관계사 전용 파일의 GROUP_C)."""
    enricher = CustomFieldsEnricher(
        url="http://example/v1/chat/completions",
        model="model",
        output_fields=["PRODUCT_NM", "SUMMARY_TEXT"],
        constants={"GROUP_C": "SLF"},
        user_prompt="{{raw_text}}",
    )
    # LLM 이 상수를 다른 값으로 내놔도 선언한 값이 이긴다.
    normalized = enricher._normalize_output_fields(
        {"PRODUCT_NM": "삼성 s교통상해보험", "GROUP_C": "HPP", "무관한키": 1}
    )
    assert normalized == {
        "PRODUCT_NM": "삼성 s교통상해보험",
        "SUMMARY_TEXT": None,
        "GROUP_C": "SLF",
    }


# ── 출고 config 전수 검증 ────────────────────────────────────────────────────

_RESOURCE_DIRS = ["resource", "resource_dev"]

# 모니모 적재 스키마(TB_*)의 NOT NULL 컬럼. doc_type 별로 "이 필드는 반드시 값이 확보되는
# 경로가 있어야 한다"는 뜻이다 — column_map/key_map 매핑, constants 고정, defaults 기본값
# 중 하나로는 채워져야 하고, nulls 에만 선언돼 있으면 적재 시 터진다.
_REQUIRED_BY_DOC_TYPE = {
    "custom_field_menu.yaml":          ["GROUP_C", "MENU_NM", "SEARCHABLE_YN"],
    "custom_field_term.yaml":          ["GROUP_C", "TERM", "TERM_NORM", "DEFINITION", "STATUS"],
    "custom_field_faq.yaml":           ["GROUP_C", "QUESTION", "ANSWER", "STATUS"],
    "custom_field_faq_json.yaml":      ["GROUP_C", "QUESTION", "ANSWER", "STATUS"],
    "custom_field_monimo_event.yaml":  ["GROUP_C", "TITLE", "SEARCHABLE_YN"],
    "custom_field_monimo_news.yaml":   ["GROUP_C", "TITLE", "SEARCHABLE_YN"],
    "custom_field_cs_slf.yaml":        ["GROUP_C", "TITLE", "SEARCHABLE_YN"],
    "custom_field_cs_ssf.yaml":        ["GROUP_C", "TITLE", "SEARCHABLE_YN"],
    "custom_field_cs_sss.yaml":        ["GROUP_C", "TITLE", "SEARCHABLE_YN"],
    "custom_field_stock_insight.yaml": ["GROUP_C", "JONG_CODE", "ANALYSIS_DATE", "SEARCHABLE_YN"],
    "custom_field_link.yaml":          ["GROUP_C", "TITLE", "SEARCHABLE_YN"],
}


@pytest.mark.unit
@pytest.mark.parametrize("resource_dir", _RESOURCE_DIRS)
@pytest.mark.parametrize("config_name", sorted(_REQUIRED_BY_DOC_TYPE))
def test_shipped_monimo_configs_cover_not_null_columns(resource_dir, config_name):
    base = Path(__file__).resolve().parents[2] / resource_dir
    cfg = yaml.safe_load((base / config_name).read_text(encoding="utf-8"))

    mapped = set(cfg.get("column_map") or {}) | set(cfg.get("key_map") or {})
    mapped |= set(cfg.get("constants") or {})
    mapped |= set(cfg.get("defaults") or {})
    mapped |= {f for spec in (cfg.get("llm_fields") or []) for f in spec["output_fields"]}
    mapped |= set(cfg.get("html_text_fields") or {})

    missing = [c for c in _REQUIRED_BY_DOC_TYPE[config_name] if c not in mapped]
    assert not missing, f"{config_name}: NOT NULL 컬럼에 값 확보 경로가 없습니다: {missing}"

    # nulls 에만 있는 NOT NULL 컬럼은 무조건 적재 실패다.
    only_null = [c for c in _REQUIRED_BY_DOC_TYPE[config_name] if c in set(cfg.get("nulls") or [])
                 and c not in (set(cfg.get("constants") or {}) | set(cfg.get("defaults") or {}))]
    assert not only_null, f"{config_name}: NOT NULL 인데 nulls 로 선언됨: {only_null}"


@pytest.mark.unit
@pytest.mark.parametrize("resource_dir", _RESOURCE_DIRS)
@pytest.mark.parametrize("config_name", sorted(_REQUIRED_BY_DOC_TYPE))
def test_shipped_monimo_configs_use_db_column_names(resource_dir, config_name):
    """출력 필드명은 DB 컬럼명(대문자)으로 통일한다 — 적재 매핑이 1:1 이 되도록."""
    base = Path(__file__).resolve().parents[2] / resource_dir
    cfg = yaml.safe_load((base / config_name).read_text(encoding="utf-8"))

    targets = set(cfg.get("column_map") or {}) | set(cfg.get("key_map") or {})
    targets |= set(cfg.get("constants") or {}) | set(cfg.get("nulls") or [])
    targets |= {f for spec in (cfg.get("llm_fields") or []) for f in spec["output_fields"]}
    bad = sorted(t for t in targets if t != t.upper())
    assert not bad, f"{config_name}: DB 컬럼명(대문자)이 아닌 목표필드: {bad}"

    # 요약본문을 CONTENT_HASH 로 잘못 쓰던 회귀를 막는다(그건 RAW(32) 원문 검증 해시다).
    llm_outputs = {f for spec in (cfg.get("llm_fields") or []) for f in spec["output_fields"]}
    assert "CONTENT_HASH" not in llm_outputs
