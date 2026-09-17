"""표 설명을 문서유형 YAML 에서 doc_type 별로 켜고 끄고 튜닝하는 계약.

예전에는 프로세서 공통 블록에 url/model 이 채워지면(권장 구성) 독립 실행기가 무조건
표 설명을 가져가, 문서유형 YAML 의 값이 죽었다. 레코드 매핑 extractor 는 그 블록을
가질 수조차 없었다(스키마 거부). 외부 LLM 은 부르지 않는다.
"""

import asyncio
import os

import pytest
import yaml

from genon.preprocessor.processing.enrichment import config_schema as cs
from genon.preprocessor.processing.enrichment import config_v2 as cv2
from genon.preprocessor.processing.enrichment.enrichment_config import (
    build_table_text_description_overrides,
)
from genon.preprocessor.processing.enrichment.table_text_context import (
    TableTextDescriptionOptions,
    merge_table_text_description,
)
from genon.preprocessor.processing.enrichment.table_text_description import (
    TableTextDescriptionEnricher,
    apply_table_description_stage,
)

COMMON = {
    "enable": True, "url": "http://llm.invalid/v1/chat/completions", "model": "m",
    "prompt_template_file": "p.md", "max_tokens": 16000,
}


def _write(tmp_path, name, body):
    (tmp_path / name).write_text(yaml.safe_dump(body, allow_unicode=True), encoding="utf-8")
    return name


@pytest.fixture
def configured(tmp_path):
    (tmp_path / "p.md").write_text("표 설명 프롬프트", encoding="utf-8")
    _write(tmp_path, "off_llm.yaml", {
        "schema": "v2", "source": {"kind": "document"},
        "llm": [{"endpoint": {"url": "http://x", "model": "m"}, "out": ["k"]}],
        "table_text_description": {"enable": False}})
    _write(tmp_path, "off_records.yaml", {
        "schema": "v2", "source": {"kind": "records"}, "fields": {"A": {"alias": ["a"]}},
        "table_text_description": {"enable": False}})
    _write(tmp_path, "tuned_rows.yaml", {
        "schema": "v2", "source": {"kind": "rows"}, "fields": {"A": {"alias": ["a"]}},
        "table_text_description": {"enable": True, "max_tokens": 32000,
                                   "rag": {"key_fact_limit": 5}}})
    cfgs = [
        {"doc_type": "prod", "extractor": "llm",
         "config_file": "off_llm.yaml", "resource_path": str(tmp_path)},
        {"doc_type": "cs_ssf", "extractor": "json_mapping",
         "config_file": "off_records.yaml", "resource_path": str(tmp_path)},
        {"doc_type": "menu", "extractor": "tabular_mapping",
         "config_file": "tuned_rows.yaml", "resource_path": str(tmp_path)},
        {"doc_type": "faq", "extractor": "json_mapping"},
    ]
    overrides = build_table_text_description_overrides(cfgs, tmp_path)
    return TableTextDescriptionEnricher(
        {**COMMON, "resource_path": str(tmp_path)}, overrides
    )


@pytest.mark.unit
@pytest.mark.parametrize("extractor", ["llm", "json_mapping", "tabular_mapping", "json_semantic"])
def test_every_extractor_accepts_the_override_block(extractor):
    """레코드 매핑 문서유형도 이 블록을 가질 수 있다(예전에는 기동 실패)."""
    cs.validate_known_keys(
        {"table_text_description": {"enable": False}}, label="x.yaml", extractor=extractor
    )


@pytest.mark.unit
def test_v2_carries_the_block_for_record_kinds():
    normalized, extractor = cv2.normalize({
        "schema": "v2", "source": {"kind": "records"}, "fields": {"A": {"alias": ["a"]}},
        "table_text_description": {"enable": False},
    }, label="x")
    assert extractor == "json_mapping"
    assert normalized["table_text_description"] == {"enable": False}


@pytest.mark.unit
@pytest.mark.parametrize("common_key", ["enable", "enabled"])
@pytest.mark.parametrize("local_key", ["enable", "enabled"])
def test_enable_and_enabled_spellings_override_each_other(common_key, local_key):
    """철자가 달라도 문서유형 값이 공통값을 이긴다(예전에는 조용히 무시됐다)."""
    merged = merge_table_text_description(
        {common_key: True, "prompt_template": "p"}, {local_key: False}
    )
    assert TableTextDescriptionOptions.from_config(merged).enabled is False


@pytest.mark.unit
def test_doc_type_yaml_switches_the_standalone_runner(configured):
    assert configured.wants(doc_type="prod") is False      # llm 문서유형이 껐다
    assert configured.wants(doc_type="cs_ssf") is False    # 레코드 문서유형이 껐다
    assert configured.wants(doc_type="menu") is True       # 켠 문서유형
    assert configured.wants(doc_type="faq") is True        # 오버라이드 없음 -> 공통값
    assert configured.wants() is True                      # doc_type 없음 -> 공통값


@pytest.mark.unit
def test_runtime_flag_still_beats_the_doc_type_setting(configured):
    """런타임 플래그는 요청자가 명시한 뜻이라 설정보다 우선한다."""
    assert configured.wants(doc_type="cs_ssf", table_text_desc=1) is True
    assert configured.wants(doc_type="menu", table_text_desc=0) is False


@pytest.mark.unit
def test_doc_type_can_tune_not_just_switch(configured):
    common = configured.options_for("")
    menu = configured.options_for("menu")
    assert configured.config_for("menu")["max_tokens"] == 32000
    assert configured.config_for("")["max_tokens"] == 16000
    assert menu.key_fact_limit == 5 and common.key_fact_limit == 3
    # 문서유형이 말하지 않은 키는 공통값이 살아남는다.
    assert menu.retrieval_context_max_chars == common.retrieval_context_max_chars


@pytest.mark.unit
@pytest.mark.parametrize("doc_type,expected", [
    ("prod", "image"), ("cs_ssf", "image"), ("menu", "standalone"),
])
def test_stage_routing_follows_the_doc_type(configured, doc_type, expected):
    """자체 연결이 있어도 문서유형이 껐으면 독립 실행기가 가져가지 않는다."""
    taken = []

    async def _enrich(document, **kwargs):
        taken.append("standalone")
        return document

    configured.enrich = _enrich

    def _image(document, **kwargs):
        taken.append("image")
        return document

    asyncio.run(apply_table_description_stage(
        object(), custom_fields_enrichers=[], standalone=configured,
        run_image_stage=_image, handle_error=lambda exc, stage: taken.append("error"),
        kwargs={"doc_type": doc_type},
    ))
    assert taken == [expected]
