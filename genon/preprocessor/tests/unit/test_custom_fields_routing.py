"""doc_type 기반 custom_fields 라우팅과 tabular_mapping 단위 테스트."""
from __future__ import annotations

import asyncio
import json
import logging
import textwrap
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
from genon.preprocessor.facade.enrichment.field_transforms import (
    detect_payload_kind,
    render_field_text,
)
from genon.preprocessor.facade.enrichment.tabular_custom_fields import (
    compile_text_from,
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
        "defaults": {"needs_realtime_yn": "N", "question_variant_text": None},
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
    assert element["content"] == "대표질문: 가입은 어떻게 하나요?\n답변: 앱에서 가입할 수 있습니다."
    assert element["metadata"] == {
        "question_variant_text": None,
        "question": "가입은 어떻게 하나요?",
        "answer_text": "앱에서 가입할 수 있습니다.",
        "category_code": "가입",
        "needs_realtime_yn": "N",
        "doc_type": "faq",
    }


@pytest.mark.unit
def test_tabular_mapping_splits_long_row_and_repeats_prefix(tmp_path):
    """긴 Excel 행은 chunk_size 로 나뉘고 모든 조각에 질문이 유지된다."""
    config_path = _write_mapping(tmp_path / "faq.yaml")
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config.update({"split": True, "chunk_prefix_fields": ["question"]})
    config_path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")

    mapper = TabularCustomFieldsMapper(
        doc_type="faq",
        extractor="tabular_mapping",
        config_file=config_path.name,
        resource_path=str(tmp_path),
    )
    question = "가입은 어떻게 하나요?"
    data = {"data": [{
        "sheet_name": "FAQ",
        "data_rows": [{"대표질문": question, "답변": "앱에서 가입하세요. " * 40}],
    }]}
    document = mapper.to_parse_format(data, "faq")
    element = document["elements"][0]
    assert element["splittable"] is True
    # 접두에도 원천 컬럼 라벨이 붙는다(본문과 같은 규칙 — 5265f487).
    prefix = f"대표질문: {question}"
    assert element["chunk_prefix"] == prefix

    vectors = asyncio.run(ChunkProcessor()(
        request=None,
        file_path="/data/faq.xlsx",
        document=document,
        chunk_size=80,
        chunk_overlap=0,
    ))
    assert len(vectors) > 1
    assert all(vector.text.startswith(prefix) for vector in vectors)
    assert all(len(vector.text) <= 80 for vector in vectors)
    assert all(vector.question == question for vector in vectors)


@pytest.mark.unit
def test_tabular_mapping_short_row_stays_one_chunk_even_with_split(tmp_path):
    """split: true 를 켜도 chunk_size 미만 행은 여전히 "행 1개 = 청크 1개" 다."""
    config_path = _write_mapping(tmp_path / "faq.yaml")
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config.update({"split": True, "chunk_prefix_fields": ["question"]})
    config_path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")

    mapper = TabularCustomFieldsMapper(
        doc_type="faq",
        extractor="tabular_mapping",
        config_file=config_path.name,
        resource_path=str(tmp_path),
    )
    data = {"data": [{
        "sheet_name": "FAQ",
        "data_rows": [{"대표질문": "가입은 어떻게 하나요?", "답변": "앱에서 가입하세요."}],
    }]}
    vectors = asyncio.run(ChunkProcessor()(
        request=None,
        file_path="/data/faq.xlsx",
        document=mapper.to_parse_format(data, "faq"),
        chunk_size=80,
        chunk_overlap=0,
    ))
    assert len(vectors) == 1
    assert vectors[0].text == "대표질문: 가입은 어떻게 하나요?\n답변: 앱에서 가입하세요."


@pytest.mark.unit
def test_tabular_mapping_split_false_ignores_chunk_prefix_fields(tmp_path):
    """split 이 아니면 접두 설정은 무시된다 — 본문은 text_fields 선언 순서 그대로다.

    접두는 본문 맨 앞으로 끌어올려지므로, 분할하지 않는 설정에서까지 허용하면
    text_fields 중간 필드를 지정했을 때 청크 본문 순서가 조용히 바뀐다.
    """
    config_path = _write_mapping(tmp_path / "faq.yaml")
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config.update({"split": False, "chunk_prefix_fields": ["answer_text"]})
    config_path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")

    mapper = TabularCustomFieldsMapper(
        doc_type="faq",
        extractor="tabular_mapping",
        config_file=config_path.name,
        resource_path=str(tmp_path),
    )
    assert mapper.chunk_prefix_fields == []
    data = {"data": [{
        "sheet_name": "FAQ",
        "data_rows": [{"대표질문": "가입은 어떻게 하나요?", "답변": "앱에서 가입하세요."}],
    }]}
    element = mapper.to_parse_format(data, "faq")["elements"][0]
    assert "splittable" not in element
    assert "chunk_prefix" not in element
    assert element["content"] == "대표질문: 가입은 어떻게 하나요?\n답변: 앱에서 가입하세요."


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
    assert partial["elements"][0]["content"] == "대표질문: Q2\n답변: A2"
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
    "custom_field_stock_insight.yaml": ["GROUP_C", "JONG_CODE", "JONG_NM", "ANALYSIS_DATE",
                                        "SEARCHABLE_YN"],
    "custom_field_link.yaml":          ["GROUP_C", "TITLE", "SEARCHABLE_YN"],
}

# 날짜 메타데이터는 원천의 YYMMDD·YYYYMMDD·구분자 표기를 모두 YYYYMMDD 정수로 고정한다.
# 운영과 개발 설정을 함께 검사해 한쪽만 누락되는 배포 차이를 막는다.
_DATE_INT_FLEX_FIELDS = {
    "custom_field_faq.yaml": ["SRC_LAST_MOD_DT"],
    "custom_field_menu.yaml": ["SRC_LAST_MOD_DT"],
    "custom_field_term.yaml": ["SRC_LAST_MOD_DT"],
    "custom_field_link.yaml": ["CAMPAIGN_FROM", "CAMPAIGN_TO"],
    "custom_field_monimo_event.yaml": ["EVENT_FROM", "EVENT_TO"],
    "custom_field_monimo_news.yaml": ["NEWS_TO"],
    "custom_field_stock_insight.yaml": ["NEWS_DATE", "ANALYSIS_DATE"],
}

# TB_* 쪽에 컬럼 기본값이 있어 config 가 값을 주지 않아도 적재가 되는 NOT NULL 컬럼.
# SEARCHABLE_YN 은 전 TB 가 'N' 을 기본값으로 갖는다("TB_EVENT 기본값과 같은 'N' 으로 두고,
# 적재 측에서 게시 승인 시 올리는 것을 전제로 한다" — 각 yaml 주석 참고). 그래서 출고 설정이
# 노출 게이트를 잠정 보류(주석)해 둔 상태도 적재 실패가 아니다.
# 단, `defaults: {X: null}` 로 명시 선언하면 기본값을 덮어 null 이 들어가므로 아래 두 번째
# 검사는 그대로 적용한다(예전 `nulls:` 목록이 이 형태로 통합됐다).
_DB_DEFAULTED_COLUMNS = {"SEARCHABLE_YN"}


@pytest.mark.unit
@pytest.mark.parametrize("resource_dir", _RESOURCE_DIRS)
@pytest.mark.parametrize("config_name,fields", sorted(_DATE_INT_FLEX_FIELDS.items()))
def test_shipped_date_fields_use_flexible_integer_transform(resource_dir, config_name, fields):
    """날짜 대상 필드는 압축·2자리 연도 표기까지 정규화해야 한다."""
    base = Path(__file__).resolve().parents[2] / resource_dir
    cfg = yaml.safe_load((base / config_name).read_text(encoding="utf-8"))

    transforms = cfg.get("transforms") or {}
    assert {field: transforms.get(field) for field in fields} == {
        field: "date_int_flex" for field in fields
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
    mapped |= {t for t, _, _ in compile_text_from(cfg)}

    missing = [c for c in _REQUIRED_BY_DOC_TYPE[config_name]
               if c not in mapped and c not in _DB_DEFAULTED_COLUMNS]
    assert not missing, f"{config_name}: NOT NULL 컬럼에 값 확보 경로가 없습니다: {missing}"

    # 값이 null 인 defaults 로만 선언된 NOT NULL 컬럼은 무조건 적재 실패다.
    declared_null = {k for k, v in (cfg.get("defaults") or {}).items() if v is None}
    only_null = [c for c in _REQUIRED_BY_DOC_TYPE[config_name]
                 if c in declared_null and c not in set(cfg.get("constants") or {})]
    assert not only_null, f"{config_name}: NOT NULL 인데 defaults 로 null 선언됨: {only_null}"


@pytest.mark.unit
@pytest.mark.parametrize("resource_dir", _RESOURCE_DIRS)
@pytest.mark.parametrize("config_name", sorted(_REQUIRED_BY_DOC_TYPE))
def test_shipped_monimo_configs_use_db_column_names(resource_dir, config_name):
    """출력 필드명은 DB 컬럼명(대문자)으로 통일한다 — 적재 매핑이 1:1 이 되도록."""
    base = Path(__file__).resolve().parents[2] / resource_dir
    cfg = yaml.safe_load((base / config_name).read_text(encoding="utf-8"))

    targets = set(cfg.get("column_map") or {}) | set(cfg.get("key_map") or {})
    targets |= set(cfg.get("constants") or {}) | set(cfg.get("defaults") or {})
    targets |= {f for spec in (cfg.get("llm_fields") or []) for f in spec["output_fields"]}
    bad = sorted(t for t in targets if t != t.upper())
    assert not bad, f"{config_name}: DB 컬럼명(대문자)이 아닌 목표필드: {bad}"

    # 요약본문을 CONTENT_HASH 로 잘못 쓰던 회귀를 막는다(그건 RAW(32) 원문 검증 해시다).
    llm_outputs = {f for spec in (cfg.get("llm_fields") or []) for f in spec["output_fields"]}
    assert "CONTENT_HASH" not in llm_outputs


# ── row_merge: 여러 행에 쪼개져 오는 값을 한 레코드로 접기 ───────────────────
# 모니모 AI차트뷰(stock_insight) 원천은 한 종목의 세부내용 JSON 하나를 게시물 라인번호
# 1..N 으로 **문자 단위 절단**해 행마다 뿌린다. 구분자를 끼우면 JSON 이 복원되지 않는다.

def _write_row_merge_cfg(tmp_path: Path, **overrides) -> Path:
    config = {
        "column_map": {
            "REGT_NO": ["regt_no"],
            "NTC_OBJLINE_NO": ["ntc_objline"],
            "JONG_CODE": ["jong_code"],
            "JONG_NM": ["jong_name"],
            "DETAIL_DESC": ["detail_desc"],
        },
        "row_merge": {
            "group_by": ["REGT_NO", "JONG_CODE"],
            "order_by": "NTC_OBJLINE_NO",
            "concat": ["DETAIL_DESC"],
        },
        "text_from": {"DETAIL_TEXT": "DETAIL_DESC"},
        "text_fields": ["JONG_NM", "DETAIL_TEXT"],
    }
    config.update(overrides)
    path = tmp_path / "stock.yaml"
    path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    return path


def _row_merge_rows(order=(1, 2, 3)):
    """한 JSON 을 키 이름 한가운데에서 3조각으로 자른 원천 재현."""
    pieces = {
        1: '{"a": "\uac12", "long_k',
        2: 'ey": {"b": "<strong>\ubcfc\ub4dc</strong>"}, "c": "x<BR>y',
        3: '"}',
    }
    return [
        {
            "regt_no": "4556", "jong_code": "TSLA", "jong_name": "\ud14c\uc2ac\ub77c",
            "ntc_objline": line, "detail_desc": pieces[line],
        }
        for line in order
    ]


def _build(mapper, rows, sheet="Sheet"):
    return mapper.build_fields({"data": [{"sheet_name": sheet, "data_rows": rows}]}, "stock_insight")


def _mapper(config_path: Path, tmp_path: Path):
    return TabularCustomFieldsMapper(
        doc_type="stock_insight", extractor="tabular_mapping",
        config_file=config_path.name, resource_path=str(tmp_path),
    )


@pytest.mark.unit
def test_row_merge_joins_pieces_without_separator(tmp_path):
    """3행 → 1레코드. 구분자 없이 이어붙여야 JSON 이 복원된다."""
    mapper = _mapper(_write_row_merge_cfg(tmp_path), tmp_path)
    fields = _build(mapper, _row_merge_rows())

    assert len(fields) == 1
    assert json.loads(fields[0]["DETAIL_DESC"]) == {
        "a": "값", "long_key": {"b": "<strong>볼드</strong>"}, "c": "x<BR>y",
    }


@pytest.mark.unit
def test_row_merge_sorts_by_order_by(tmp_path):
    """원천이 순서를 뒤섞어 보내도 order_by 로 정렬해 복원한다."""
    mapper = _mapper(_write_row_merge_cfg(tmp_path), tmp_path)
    shuffled = _build(mapper, _row_merge_rows(order=(3, 1, 2)))
    ordered = _build(mapper, _row_merge_rows())

    assert shuffled[0]["DETAIL_DESC"] == ordered[0]["DETAIL_DESC"]


@pytest.mark.unit
def test_row_merge_keeps_first_row_values_for_other_fields(tmp_path):
    """concat 이외 필드는 정렬 후 첫 행 값을 쓴다."""
    mapper = _mapper(_write_row_merge_cfg(tmp_path), tmp_path)
    fields = _build(mapper, _row_merge_rows(order=(3, 1, 2)))

    assert fields[0]["NTC_OBJLINE_NO"] == 1
    assert fields[0]["JONG_NM"] == "테슬라"


@pytest.mark.unit
def test_row_merge_splits_on_group_boundary(tmp_path):
    """등록번호가 바뀌면 다른 레코드다 — 두 종목이 한 덩어리로 뭉개지지 않는다."""
    mapper = _mapper(_write_row_merge_cfg(tmp_path), tmp_path)
    rows = _row_merge_rows()
    other = [
        {**row, "regt_no": "4558", "jong_code": "NVDA", "jong_name": "엔비디아"}
        for row in _row_merge_rows()
    ]
    fields = _build(mapper, rows + other)

    assert [f["JONG_NM"] for f in fields] == ["테슬라", "엔비디아"]
    assert all(json.loads(f["DETAIL_DESC"]) for f in fields)


@pytest.mark.unit
def test_row_merge_absent_keeps_one_record_per_row(tmp_path):
    """row_merge 미선언이면 종전대로 행 1개 = 레코드 1개다(회귀 가드)."""
    config = yaml.safe_load(_write_row_merge_cfg(tmp_path).read_text(encoding="utf-8"))
    config.pop("row_merge")
    config.pop("text_from")
    config["text_fields"] = ["JONG_NM", "DETAIL_DESC"]
    path = tmp_path / "stock.yaml"
    path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")

    fields = _build(_mapper(path, tmp_path), _row_merge_rows())
    assert len(fields) == 3
    assert [f["NTC_OBJLINE_NO"] for f in fields] == [1, 2, 3]


@pytest.mark.unit
@pytest.mark.parametrize("bad", [
    {"group_by": ["NOPE"], "order_by": "NTC_OBJLINE_NO", "concat": ["DETAIL_DESC"]},
    {"group_by": ["REGT_NO"], "order_by": "NOPE", "concat": ["DETAIL_DESC"]},
    {"group_by": ["REGT_NO"], "order_by": "NTC_OBJLINE_NO", "concat": ["NOPE"]},
])
def test_row_merge_rejects_unknown_field_at_startup(tmp_path, bad):
    """오타를 조용히 무시하면 병합이 안 된 채로 요청이 성공해 버린다 — 기동 시에 잡는다."""
    path = _write_row_merge_cfg(tmp_path, row_merge=bad)
    with pytest.raises(ValueError, match="NOPE"):
        _mapper(path, tmp_path)


@pytest.mark.unit
@pytest.mark.parametrize("bad,match", [
    ({"order_by": "NTC_OBJLINE_NO", "concat": ["DETAIL_DESC"]}, "group_by"),
    ({"group_by": ["REGT_NO"], "order_by": "NTC_OBJLINE_NO"}, "concat"),
    ({"group_by": "REGT_NO", "concat": ["DETAIL_DESC"]}, "목록"),
])
def test_row_merge_rejects_bad_shape_at_startup(tmp_path, bad, match):
    with pytest.raises(ValueError, match=match):
        _mapper(_write_row_merge_cfg(tmp_path, row_merge=bad), tmp_path)


# ── json_text_fields: JSON 필드 → 평문 파생 필드 ─────────────────────────────

@pytest.mark.unit
def test_text_from_keeps_source_and_derives_markdown(tmp_path):
    """원본은 그대로 남고 파생 필드만 마크다운이 된다(TB 는 원천 원본을 보관한다)."""
    mapper = _mapper(_write_row_merge_cfg(tmp_path), tmp_path)
    fields = _build(mapper, _row_merge_rows())[0]

    assert fields["DETAIL_DESC"].startswith('{"a"')
    # 짧은 스칼라는 앞으로 끌어올린 불릿, 나머지는 `## 제목` 섹션.
    assert fields["DETAIL_TEXT"] == "- a: 값\n\n## long key\n- b: 볼드\n\n## c\nx\ny"


@pytest.mark.unit
def test_text_from_block_drops_column_label(tmp_path):
    """여러 줄 블록에는 `detail_desc:` 라벨을 붙이지 않는다 — 헤딩이 이미 문맥을 담는다."""
    mapper = _mapper(_write_row_merge_cfg(tmp_path), tmp_path)
    element = mapper.to_parse_format(
        {"data": [{"sheet_name": "Sheet", "data_rows": _row_merge_rows()}]}, "stock_insight"
    )["elements"][0]

    assert element["content"].startswith("jong_name: 테슬라\n- a: 값")
    assert "DETAIL_TEXT" not in element["content"]
    assert "detail_desc:" not in element["content"]


@pytest.mark.unit
def test_text_from_warns_and_falls_back_on_broken_json(caplog):
    """병합이 어긋나 JSON 이 안 맞으면 조용히 넘기지 않는다 — 경고 + 원문 평문화."""
    with caplog.at_level(logging.WARNING):
        text = render_field_text('{"a": "\uac12<BR>\ub05d')

    assert text == "{\"a\": \"값\n끝"
    assert "row_merge" in caplog.text


@pytest.mark.unit
def test_text_from_plain_text_does_not_warn(caplog):
    """브레이스로 시작하지 않는 평문은 그냥 평문이다 — 경고를 남기지 않는다."""
    with caplog.at_level(logging.WARNING):
        text = render_field_text("현재 주가는 20일선 아래에서 형성되며 하락 추세입니다.")

    assert text == "현재 주가는 20일선 아래에서 형성되며 하락 추세입니다."
    assert "row_merge" not in caplog.text


@pytest.mark.unit
def test_text_from_routes_structural_html_to_renderer():
    """표가 섞인 HTML 은 넘겨준 렌더러(docling)로 보낸다 — 표 행/열이 뭉개지지 않게."""
    seen = []

    def renderer(value):
        seen.append(value)
        return "| 일자 | 종가 |\n| --- | --- |"

    text = render_field_text("<p>본문</p><table><tr><td>1</td></tr></table>", html_renderer=renderer)
    assert seen and text.startswith("| 일자")


@pytest.mark.unit
def test_text_from_inline_html_skips_renderer():
    """인라인 태그뿐이면 렌더러를 부르지 않는다 — 행마다 docling 문서를 세우지 않게."""
    called = []
    text = render_field_text("가나<BR>다라<strong>강조</strong>",
                             html_renderer=lambda v: called.append(v) or "")
    assert not called
    assert text == "가나\n다라강조"


@pytest.mark.unit
@pytest.mark.parametrize("value,kind", [
    ('{"a": 1}', "json"),
    ("[1, 2]", "json"),
    ('{"a": ', "broken_json"),
    ("<p>본문</p>", "html"),
    ("<table><tr><td>1</td></tr></table>", "html"),
    ("가나<BR>다라", "html_inline"),
    ("그냥 평문", "text"),
    ("", "empty"),
    (None, "empty"),
])
def test_detect_payload_kind(value, kind):
    """detail_desc 에 JSON·HTML·평문이 섞여 오므로 판별이 계약이다."""
    assert detect_payload_kind(value) == kind


@pytest.mark.unit
@pytest.mark.parametrize("value,expected", [
    (None, None),
    ("", None),
    ('{"a": null, "b": ""}', None),                       # 빈 리프만 있으면 본문도 비운다
    ('{"a": {"b": {"c": "\uae4a\uc74c"}}}', "## a\n\n### b\n- c: 깊음"),  # 헤딩은 두 단계까지
    ('{"a": [1, 2]}', "- a: 1, 2"),                       # 짧은 스칼라 배열은 한 줄로
    ('{"a": "  x   y  "}', "- a: x y"),                   # 줄 안 연속 공백만 접는다
    ('{"totalCount": 3}', "- total Count: 3"),            # camelCase 를 단어로 나눈다
    ('{"RSI": "8월 13일 47.72"}', "- RSI: 8월 13일 47.72"),  # 약어는 대문자 그대로
])
def test_render_field_text_shapes(value, expected):
    assert render_field_text(value) == expected


# ── 목표필드명 검증 (예약 필드 / property 이름 규칙) ─────────────────────────
# 행 metadata 는 그대로 벡터 property 로 승격되므로, 목표필드명이 청커 모델의 예약 필드와 겹치면
# 값이 조용히 덮어써지거나(text 등) 타입 검증에 걸려 **요청 전체가 실패**한다
# (title/created_date/appendix 는 값이 None 이기만 해도 ValidationError).
# 일반 컬럼 경로는 xlsx_processor._stable_key 로 이미 회피하는데 custom_fields 는 그 경로를 안 탄다.

def _write_mapper_cfg(tmp_path, body: str):
    path = tmp_path / "custom_field_probe.yaml"
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


@pytest.mark.unit
@pytest.mark.parametrize("target", ["title", "created_date", "appendix", "text", "n_char", "reg_date"])
def test_reserved_target_field_name_rejected_at_startup(tmp_path, target):
    """예약 필드명을 목표필드로 쓰면 기동 시 막는다(런타임 크래시 예방)."""
    _write_mapper_cfg(tmp_path, f"""
        column_map:
          {target}: [원천컬럼]
        text_fields: [{target}]
    """)
    with pytest.raises(ValueError, match="예약 필드"):
        TabularCustomFieldsMapper(
            config_file="custom_field_probe.yaml", resource_path=str(tmp_path),
            doc_type="x", extractor="tabular_mapping",
        )


@pytest.mark.unit
@pytest.mark.parametrize("target", ["제목", "MY FIELD", "1ST", "a-b"])
def test_invalid_property_name_rejected_at_startup(tmp_path, target):
    """한글·공백·기호·숫자시작 목표필드명은 적재 시 실패하므로 기동 시 막는다."""
    _write_mapper_cfg(tmp_path, f"""
        column_map:
          "{target}": [원천컬럼]
        text_fields: ["{target}"]
    """)
    with pytest.raises(ValueError, match="이름 규칙"):
        TabularCustomFieldsMapper(
            config_file="custom_field_probe.yaml", resource_path=str(tmp_path),
            doc_type="x", extractor="tabular_mapping",
        )


@pytest.mark.unit
def test_reserved_name_checked_in_constants_and_llm_fields(tmp_path):
    """column_map 뿐 아니라 constants·defaults·llm_fields 출력도 검사 대상이다."""
    _write_mapper_cfg(tmp_path, """
        column_map:
          TITLE: [제목]
        text_fields: [TITLE]
        llm_fields:
          - output_fields: [created_date]
            input_fields: [TITLE]
            url: "http://example/v1/chat/completions"
    """)
    with pytest.raises(ValueError, match="예약 필드"):
        TabularCustomFieldsMapper(
            config_file="custom_field_probe.yaml", resource_path=str(tmp_path),
            doc_type="x", extractor="tabular_mapping",
        )


@pytest.mark.unit
def test_db_column_style_target_names_pass(tmp_path):
    """출고 관례(대문자 DB 컬럼명)는 그대로 통과해야 한다 — 오탐 방지."""
    _write_mapper_cfg(tmp_path, """
        column_map:
          TITLE: [제목]
          GROUP_C: [회사명]
          SRC_LAST_MOD_DT: [최종수정일]
        text_fields: [TITLE]
    """)
    mapper = TabularCustomFieldsMapper(
        config_file="custom_field_probe.yaml", resource_path=str(tmp_path),
        doc_type="x", extractor="tabular_mapping",
    )
    assert set(mapper.config["column_map"]) == {"TITLE", "GROUP_C", "SRC_LAST_MOD_DT"}


@pytest.mark.unit
def test_row_metadata_validation_failure_is_wrapped_with_stage():
    """청커의 예약필드 충돌은 raw ValidationError 가 아니라 stage 를 가진 예외로 올라간다.

    raw 로 두면 pydantic ValidationError 가 ValueError 하위라 업로드 파일 문제(INPUT_ERROR)로
    오분류되고 stage 도 없어 원인 추적이 어렵다.
    """
    cp = pytest.importorskip("facade.chunking_processor")
    proc = object.__new__(cp.DocumentProcessor)
    elements = [{
        "category": "custom_fields_row", "content": "본문", "page": 1,
        "metadata": {"title": None, "GROUP_C": "SLF", "doc_type": "notice"},
    }]
    with pytest.raises(cp.GenosServiceException) as exc:
        cp.DocumentProcessor._chunk_custom_fields_rows(proc, elements)
    assert exc.value.stage == "custom_fields"
    assert "title" in exc.value.error_msg


# ── 설정 오기입을 기동 시에 잡는다 ───────────────────────────────────────────
# YAML 에서 타입을 틀리면 코드가 조용히 엉뚱하게 해석한다(문자열을 글자 단위로 쪼개는 등).
# 그 결과는 "청크 0건" 또는 "매 요청 실패"로만 드러나므로 기동 시에 막는다.

@pytest.mark.unit
@pytest.mark.parametrize("key", ["required", "text_fields", "chunk_prefix_fields"])
def test_scalar_instead_of_list_rejected_at_startup(tmp_path, key):
    """`- ` 를 빠뜨려 스칼라가 되면 글자 단위로 쪼개져 전 행이 걸러진다 — 기동 시 거부."""
    _write_mapper_cfg(tmp_path, f"""
        column_map:
          TITLE: [제목]
        text_fields: [TITLE]
        {key}: TITLE
    """)
    with pytest.raises(ValueError, match="목록이어야"):
        TabularCustomFieldsMapper(
            config_file="custom_field_probe.yaml", resource_path=str(tmp_path),
            doc_type="x", extractor="tabular_mapping",
        )


@pytest.mark.unit
def test_unknown_chunk_prefix_field_rejected_at_startup(tmp_path):
    _write_mapper_cfg(tmp_path, """
        column_map:
          TITLE: [제목]
        text_fields: [TITLE]
        chunk_prefix_fields: [MISSING_TITLE]
    """)
    with pytest.raises(ValueError, match="chunk_prefix_fields.*만드는 설정이 없습니다"):
        TabularCustomFieldsMapper(
            config_file="custom_field_probe.yaml", resource_path=str(tmp_path),
            doc_type="x", extractor="tabular_mapping",
        )


@pytest.mark.unit
@pytest.mark.parametrize("key", ["constants", "defaults", "value_map", "transforms"])
def test_list_instead_of_mapping_rejected_at_startup(tmp_path, key):
    """맵이어야 하는 키를 리스트로 쓰면 dict() 강제 변환이 요청마다 터진다 — 기동 시 거부."""
    _write_mapper_cfg(tmp_path, f"""
        column_map:
          TITLE: [제목]
        text_fields: [TITLE]
        {key}:
          - X
    """)
    with pytest.raises(ValueError, match="object 여야"):
        TabularCustomFieldsMapper(
            config_file="custom_field_probe.yaml", resource_path=str(tmp_path),
            doc_type="x", extractor="tabular_mapping",
        )


@pytest.mark.unit
def test_required_on_llm_generated_field_rejected_at_startup(tmp_path):
    """필수값 검사는 LLM 호출보다 먼저 돈다 — LLM 생성 필드를 required 로 걸면 전 행이 사라진다."""
    _write_mapper_cfg(tmp_path, """
        column_map:
          TITLE: [제목]
        required: [SUMMARY_TEXT]
        text_fields: [TITLE]
        llm_fields:
          - output_fields: [SUMMARY_TEXT]
            input_fields: [TITLE]
            url: "http://example/v1/chat/completions"
    """)
    with pytest.raises(ValueError, match="llm_fields 가 만드는 필드"):
        TabularCustomFieldsMapper(
            config_file="custom_field_probe.yaml", resource_path=str(tmp_path),
            doc_type="x", extractor="tabular_mapping",
        )


@pytest.mark.unit
def test_unproducible_text_field_warns_but_loads(tmp_path, caplog):
    """text_fields 가 만들 수 없는 필드를 가리키면 경고한다(실패는 아니다).

    출고 custom_field_monimo_event.yaml 이 현재 이 상태라 hard error 로 두면 기동이 막힌다.
    """
    _write_mapper_cfg(tmp_path, """
        column_map:
          TITLE: [제목]
        text_fields: [TITLE, SUMMARY_TEXT]
    """)
    with caplog.at_level("WARNING"):
        TabularCustomFieldsMapper(
            config_file="custom_field_probe.yaml", resource_path=str(tmp_path),
            doc_type="x", extractor="tabular_mapping",
        )
    assert "SUMMARY_TEXT" in caplog.text


@pytest.mark.unit
def test_shipped_configs_pass_startup_validation():
    """출고 매핑 설정은 새 검증을 모두 통과해야 한다(오탐 방지)."""
    import yaml as _yaml
    base = Path(__file__).resolve().parents[2] / "resource"
    raw = _yaml.safe_load((base / "parser_processor_config.yaml").read_text(encoding="utf-8"))
    built = 0
    for item in raw.get("enrichment") or []:
        for name, opts in (item or {}).items():
            if name != "custom_fields" or not isinstance(opts, dict) or not opts.get("config_file"):
                continue
            extractor = opts.get("extractor") or "llm"
            if extractor.startswith("tabular"):
                cls = TabularCustomFieldsMapper
            elif extractor == "json_semantic":
                cls = pytest.importorskip(
                    "genon.preprocessor.facade.enrichment.json_semantic"
                ).SemanticJsonMapper
            elif extractor.startswith("json"):
                cls = pytest.importorskip(
                    "genon.preprocessor.facade.enrichment.json_records"
                ).JsonRecordsMapper
            else:
                continue
            cls(config_file=opts["config_file"], resource_path=str(base),
                doc_type=opts.get("doc_type"), extractor=extractor)
            built += 1
    assert built >= 10, f"검증 대상 매핑 설정이 너무 적다({built}개) — 등록을 확인하라"


@pytest.mark.unit
def test_registration_block_accepts_html_preprocess_block():
    """등록 블록 최상위의 `html:` 은 enricher 가 아니라 parser 가 소비하는 키다.

    `_NON_ENRICHER_KEYS` 에서 빠져 있으면 생성자로 흘러들어 TypeError 로 기동이 죽는다.
    `build_html_marker_heading_doc_types` 가 이 위치의 블록을 읽도록 만들어져 있는데도
    그 코드에 도달하지 못했다(json/markdown 은 처음부터 제외돼 있었다).
    """
    from genon.preprocessor.facade.enrichment.markdown_front_matter import (
        build_html_marker_heading_doc_types,
    )

    configs = [{
        "doc_type": "cs_hpp",
        "extractor": "llm",
        "url": "http://example",
        "model": "model",
        "html": {"marker_headings": True},
    }]
    enrichers = build_document_custom_fields_enrichers(configs)
    assert len(enrichers) == 1
    assert "cs_hpp" in build_html_marker_heading_doc_types(configs)


@pytest.mark.unit
def test_registration_block_still_rejects_unknown_keys():
    """html 을 통과시키느라 모르는 키까지 삼키면 안 된다(오타 fail-fast 유지)."""
    with pytest.raises(TypeError):
        build_document_custom_fields_enrichers(
            [{"extractor": "llm", "url": "u", "model": "m", "htmlx": {}}]
        )


# ── extractor 별 지원키 선언 (config_schema) ────────────────────────────────

@pytest.mark.unit
def test_unknown_key_is_rejected_with_suggestion(tmp_path):
    """모르는 최상위 키는 지금까지 완전 무증상이었다 — `column_maps` 한 글자로 매핑이 사라졌다."""
    from genon.preprocessor.facade.enrichment.tabular_custom_fields import (
        TabularCustomFieldsMapper,
    )

    cfg = tmp_path / "custom_field_x.yaml"
    cfg.write_text("column_maps:\n  Q: [질문]\ntext_fields: [Q]\n", encoding="utf-8")
    with pytest.raises(ValueError) as exc:
        TabularCustomFieldsMapper(
            config_file=cfg.name, resource_path=str(tmp_path),
            doc_type="t", extractor="tabular_mapping",
        )
    message = str(exc.value)
    assert "column_maps" in message
    assert "column_map" in message  # 가장 가까운 이름을 제안한다


@pytest.mark.unit
def test_key_of_another_extractor_is_rejected(tmp_path):
    """다른 extractor 의 키는 읽히지 않으므로 설정이 무효다 — 조용히 무시하지 않는다.

    특히 json_semantic 은 text_fields/chunk_prefix_fields 를 읽지 않는데, 예전에는
    이름이 맞으면 무시하고 틀리면 기동을 실패시켜 신호가 정반대로 나갔다.
    """
    from genon.preprocessor.facade.enrichment.json_semantic import SemanticJsonMapper

    cfg = tmp_path / "custom_field_s.yaml"
    cfg.write_text(
        "shared_fields:\n  PRODUCT_NM: [prodNm]\n"
        "sections:\n  ksp: {name: 혜택, include: true}\n"
        "text_fields: [PRODUCT_NM]\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError) as exc:
        SemanticJsonMapper(
            config_file=cfg.name, resource_path=str(tmp_path),
            doc_type="t", extractor="json_semantic",
        )
    assert "text_fields" in str(exc.value)


@pytest.mark.unit
def test_llm_config_rejects_output_field_typo(tmp_path):
    """템플릿이 오래 경고만 하던 오타다 — 이제 기동 시에 잡는다."""
    from genon.preprocessor.facade.enrichment.custom_fields_enricher import (
        CustomFieldsEnricher,
    )

    cfg = tmp_path / "custom_field_l.yaml"
    cfg.write_text(
        'url: "u"\nmodel: m\noutput_field:\n  - TITLE\nuser_prompt: |\n  {{raw_text}}\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError) as exc:
        CustomFieldsEnricher(config_file=cfg.name, resource_path=str(tmp_path))
    assert "output_fields" in str(exc.value)


@pytest.mark.unit
@pytest.mark.parametrize("resource_dir", ["resource", "resource_dev"])
def test_shipped_configs_match_declared_keys(resource_dir):
    """출고 설정이 지원키 선언과 어긋나지 않는지 지킨다.

    코드에 새 키를 넣고 config_schema 갱신을 잊으면 그 키를 쓴 설정이 기동에 실패한다 —
    이 테스트가 그 누락을 배포 전에 잡는다.
    """
    from pathlib import Path

    import yaml

    from genon.preprocessor.facade.enrichment import config_schema as cs

    root = Path(__file__).resolve().parents[2] / resource_dir
    registered = {}
    for name in ("parser_processor_config.yaml", "parser_processor_config_simple.yaml",
                 "intelligent_processor_config.yaml", "convert_processor_config.yaml"):
        path = root / name
        if not path.exists():
            continue
        for item in (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("enrichment") or []:
            block = (item or {}).get("custom_fields")
            if block and block.get("config_file"):
                registered.setdefault(block["config_file"], block.get("extractor") or "llm")

    checked = 0
    for path in sorted(root.glob("custom_field_*.yaml")):
        extractor = registered.get(path.name)
        if extractor is None:
            continue  # 어느 프로세서에도 등록되지 않은 설정은 대상이 아니다
        cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        cs.validate_known_keys(cfg, label=f"{resource_dir}/{path.name}", extractor=extractor)
        checked += 1
    assert checked >= 15, f"검사된 출고 설정이 너무 적다: {checked}건"


# ── 시트/표 컨텍스트를 column_map 에서 참조 (A3) ────────────────────────────

@pytest.mark.unit
def test_column_map_can_reference_sheet_name(tmp_path):
    """관계사별로 시트를 나눠 보내면서 구분값을 셀에는 안 넣는 원천이 있다.

    시트명·표 제목은 그동안 로그·에러 메시지에만 쓰여 목표필드로 만들 방법이 없었다.
    """
    from genon.preprocessor.facade.enrichment.tabular_custom_fields import (
        TabularCustomFieldsMapper,
    )

    cfg = tmp_path / "custom_field_ctx.yaml"
    cfg.write_text(
        "column_map:\n  COMPANY: [sheet_name]\n  QUESTION: [질문]\n"
        "required: [COMPANY, QUESTION]\n"
        "text_fields: [QUESTION]\n",
        encoding="utf-8",
    )
    mapper = TabularCustomFieldsMapper(
        config_file=cfg.name, resource_path=str(tmp_path),
        doc_type="cs", extractor="tabular_mapping",
    )
    rows = mapper.build_fields({"data": [
        {"sheet_name": "삼성생명", "data_rows": [{"질문": "가입 방법은?"}]},
        {"sheet_name": "삼성화재", "data_rows": [{"질문": "청구 방법은?"}]},
    ]}, "cs")

    assert [r["COMPANY"] for r in rows] == ["삼성생명", "삼성화재"]
    assert rows[0]["QUESTION"] == "가입 방법은?"


@pytest.mark.unit
def test_real_column_wins_over_sheet_context(tmp_path):
    """컨텍스트가 진짜 데이터를 가리면 안 된다 — 같은 이름의 실제 컬럼이 우선한다."""
    from genon.preprocessor.facade.enrichment.tabular_custom_fields import (
        TabularCustomFieldsMapper,
    )

    cfg = tmp_path / "custom_field_ctx2.yaml"
    cfg.write_text(
        "column_map:\n  SRC: [sheet_name]\n  QUESTION: [질문]\ntext_fields: [QUESTION]\n",
        encoding="utf-8",
    )
    mapper = TabularCustomFieldsMapper(
        config_file=cfg.name, resource_path=str(tmp_path),
        doc_type="cs", extractor="tabular_mapping",
    )
    rows = mapper.build_fields({"data": [
        {"sheet_name": "시트A", "data_rows": [{"sheet_name": "행에_있는_값", "질문": "Q1"}]},
    ]}, "cs")
    assert rows[0]["SRC"] == "행에_있는_값"


# ── 죽은 키·중복 키 정리 (B1·B2) ────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.parametrize("key", ["nulls", "json_text_fields"])
def test_removed_keys_are_rejected(tmp_path, key):
    """제거한 키는 조용히 무시되지 않고 기동 시에 드러나야 한다.

    nulls 는 `defaults: {X: null}` 과 결과가 완전히 같아 개념 하나를 줄였고,
    json_text_fields 는 순효과가 경고 한 줄뿐이라 text_from 으로 접었다.
    """
    from genon.preprocessor.facade.enrichment.tabular_custom_fields import (
        TabularCustomFieldsMapper,
    )

    cfg = tmp_path / "custom_field_x.yaml"
    body = "  - BIZ_ID\n" if key == "nulls" else "  D: SRC\n"
    cfg.write_text(
        f"column_map:\n  Q: [질문]\n  SRC: [원문]\ntext_fields: [Q]\n{key}:\n{body}",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match=key):
        TabularCustomFieldsMapper(
            config_file=cfg.name, resource_path=str(tmp_path),
            doc_type="t", extractor="tabular_mapping",
        )


@pytest.mark.unit
def test_defaults_null_declares_field_without_mapping(tmp_path):
    """`defaults: {X: null}` 이 예전 `nulls: [X]` 와 같은 결과를 낸다."""
    from genon.preprocessor.facade.enrichment.tabular_custom_fields import (
        TabularCustomFieldsMapper,
    )

    cfg = tmp_path / "custom_field_d.yaml"
    cfg.write_text(
        'column_map:\n  Q: [질문]\ndefaults:\n  STATUS: "PUBLISHED"\n  BIZ_ID: null\n'
        "text_fields: [Q]\n",
        encoding="utf-8",
    )
    mapper = TabularCustomFieldsMapper(
        config_file=cfg.name, resource_path=str(tmp_path),
        doc_type="t", extractor="tabular_mapping",
    )
    row = mapper.build_fields(
        {"data": [{"sheet_name": "S", "data_rows": [{"질문": "Q1"}]}]}, "t"
    )[0]
    assert "BIZ_ID" in row and row["BIZ_ID"] is None
    assert row["STATUS"] == "PUBLISHED"


@pytest.mark.unit
@pytest.mark.parametrize("alias", ["document_llm", "tabular", "column_mapping", "json_records"])
def test_extractor_aliases_are_gone(alias):
    """extractor 이름은 종류마다 하나씩이다 — 별칭은 "무엇을 써야 하나"만 늘렸다."""
    with pytest.raises(ValueError, match="지원하지 않는 custom_fields extractor"):
        build_document_custom_fields_enrichers([{"extractor": alias, "url": "u", "model": "m"}])


@pytest.mark.unit
def test_thinking_falls_back_to_config_file(tmp_path):
    """문서유형 yaml 의 thinking 값이 반영돼야 한다(생성자 기본값이 먼저 잡히던 버그)."""
    from genon.preprocessor.facade.enrichment.custom_fields_enricher import (
        CustomFieldsEnricher,
    )

    cfg = tmp_path / "custom_field_t.yaml"
    cfg.write_text(
        "url: u\nmodel: m\nthinking: auto\nthinking_dialect: hcx\n"
        "output_fields: [T]\nuser_prompt: |\n  {{raw_text}}\n",
        encoding="utf-8",
    )
    kwargs = {"config_file": cfg.name, "resource_path": str(tmp_path)}
    assert (CustomFieldsEnricher(**kwargs)._thinking,
            CustomFieldsEnricher(**kwargs)._thinking_dialect) == ("auto", "hcx")
    # 등록 블록이 명시하면 그쪽이 이긴다(다른 키와 같은 우선순위).
    explicit = CustomFieldsEnricher(**kwargs, thinking="off", thinking_dialect="standard")
    assert (explicit._thinking, explicit._thinking_dialect) == ("off", "standard")


# ── 등록 블록 ↔ config_file 병합 규칙 (B3) ──────────────────────────────────
# 규칙은 하나다 — 등록 블록이 config_file 을 이기고, 미지정이면 config_file 을 쓴다.

def _llm_cfg(tmp_path, extra=""):
    path = tmp_path / "custom_field_b3.yaml"
    path.write_text(
        "url: cfg-url\nmodel: cfg-model\n"
        "max_tokens: 4000\ntemperature: 0.7\ntimeout: 300\n"
        "constants:\n  FROM_CFG: cfg\n  BOTH: cfg\n"
        "system_prompt: |\n  cfg-system\n"
        "user_prompt: |\n  cfg-user {{raw_text}}\n"
        "output_fields: [A]\n" + extra,
        encoding="utf-8",
    )
    return {"config_file": path.name, "resource_path": str(tmp_path)}


@pytest.mark.unit
def test_registration_block_wins_even_with_default_looking_values(tmp_path):
    """기본값과 같은 값을 등록 블록에 **명시**해도 이겨야 한다.

    예전에는 sentinel 비교(`max_tokens != 1000`)라 "미지정"과 구분되지 않아 config_file
    값이 이겼다. `temperature: 0.0` 이 가장 자주 밟혔다.
    """
    from genon.preprocessor.facade.enrichment.custom_fields_enricher import (
        CustomFieldsEnricher,
    )

    kwargs = _llm_cfg(tmp_path)
    unset = CustomFieldsEnricher(**kwargs)
    assert (unset._max_tokens, unset._temperature, unset._timeout) == (4000, 0.7, 300)

    explicit = CustomFieldsEnricher(**kwargs, max_tokens=1000, temperature=0.0, timeout=60)
    assert (explicit._max_tokens, explicit._temperature, explicit._timeout) == (1000, 0.0, 60)


@pytest.mark.unit
def test_constants_merge_per_key(tmp_path):
    """전체 치환이면 등록 블록에 상수 하나만 덧붙여도 config_file 상수가 통째로 사라진다."""
    from genon.preprocessor.facade.enrichment.custom_fields_enricher import (
        CustomFieldsEnricher,
    )

    enricher = CustomFieldsEnricher(
        **_llm_cfg(tmp_path), constants={"BOTH": "item", "FROM_ITEM": "item"}
    )
    assert enricher._constants == {"FROM_CFG": "cfg", "BOTH": "item", "FROM_ITEM": "item"}


@pytest.mark.unit
def test_registration_inline_prompt_beats_config_file_prompt_file(tmp_path):
    """프롬프트만 방향이 반대였다 — config_file 의 `*_prompt_file` 이 등록 블록을 이겼다."""
    from genon.preprocessor.facade.enrichment.custom_fields_enricher import (
        CustomFieldsEnricher,
    )

    (tmp_path / "sys.md").write_text("cfg-system-from-file", encoding="utf-8")
    kwargs = _llm_cfg(tmp_path, extra="system_prompt_file: sys.md\n")

    assert CustomFieldsEnricher(**kwargs)._system_prompt == "cfg-system-from-file"
    named = CustomFieldsEnricher(**kwargs, system_prompt="item-system")
    assert named._system_prompt == "item-system"


# ── 값 적용 순서 (B4) ───────────────────────────────────────────────────────

@pytest.mark.unit
def test_constants_beat_defaults_even_when_empty(tmp_path):
    """`constants: {X: ""}` 를 defaults 가 되살리면 "constants 가 이긴다"는 계약이 깨진다.

    세 extractor 가 같은 순서(defaults → constants)를 쓰는지 고정한다.
    """
    from genon.preprocessor.facade.enrichment.json_records import JsonRecordsMapper
    from genon.preprocessor.facade.enrichment.tabular_custom_fields import (
        TabularCustomFieldsMapper,
    )

    tab = tmp_path / "custom_field_t.yaml"
    tab.write_text(
        'column_map:\n  Q: [질문]\n  X: [엑스]\n'
        'constants:\n  X: ""\ndefaults:\n  X: "채움"\ntext_fields: [Q]\n',
        encoding="utf-8",
    )
    row = TabularCustomFieldsMapper(
        config_file=tab.name, resource_path=str(tmp_path),
        doc_type="t", extractor="tabular_mapping",
    ).build_fields({"data": [{"sheet_name": "S", "data_rows": [{"질문": "Q1", "엑스": "원천"}]}]}, "t")[0]
    assert row["X"] == ""

    jsn = tmp_path / "custom_field_j.yaml"
    jsn.write_text(
        'key_map:\n  T: [title]\n  X: [x]\n'
        'constants:\n  X: ""\ndefaults:\n  X: "채움"\ntext_fields: [T]\n',
        encoding="utf-8",
    )
    record = JsonRecordsMapper(
        config_file=jsn.name, resource_path=str(tmp_path),
        doc_type="t", extractor="json_mapping",
    ).build_fields([{"title": "T1", "x": "원천"}], "t")[0]
    assert record["X"] == ""
