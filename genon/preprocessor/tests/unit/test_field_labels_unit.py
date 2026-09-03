"""청크 본문의 항목명(field_labels) 단위 테스트.

메타 값이 청크 본문에 실릴 때 항목명을 함께 낼지가 extractor 마다 달랐다(tabular 는 붙이고
json 은 안 붙임). 그 기준을 "사람이 붙인 이름이 있는가" 하나로 모은 것이 `field_labels` 다.
여기서는 네 경로(tabular / json_mapping / json_semantic / 문서형 접두)가 같은 규칙을 쓰는지,
그리고 이름이 없을 때 시스템 이름이 새지 않는지를 고정한다.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml

from genon.preprocessor.facade.chunking import doc_prefix as dpx
from genon.preprocessor.facade.common import config_parse as cp
from genon.preprocessor.facade.enrichment.json_records import JsonRecordsMapper
from genon.preprocessor.facade.enrichment.tabular_custom_fields import (
    TabularCustomFieldsMapper,
    build_chunk_text,
)

RESOURCE_DIR = Path(__file__).resolve().parents[2] / "resource"


# ── build_chunk_text: 항목명 결정 규칙 ───────────────────────────────────────

@pytest.mark.unit
def test_field_labels_beats_column_map_alias():
    """사람이 붙인 이름이 원천 헤더(column_map 별칭 첫 값)보다 우선한다."""
    fields = {"QUESTION": "가입 방법", "ANSWER": "앱에서 가입"}
    content, _ = build_chunk_text(
        fields, ["QUESTION", "ANSWER"], [],
        column_map={"QUESTION": ["depth4", "질문"], "ANSWER": ["description"]},
        field_labels={"QUESTION": "질문", "ANSWER": "답변"},
    )
    assert content == "질문: 가입 방법\n답변: 앱에서 가입"


@pytest.mark.unit
def test_column_map_alias_stays_the_fallback_label():
    """field_labels 가 없으면 종전대로 별칭 첫 값이 항목명이다(tabular 기존 동작 보존)."""
    content, _ = build_chunk_text(
        {"QUESTION": "가입 방법"}, ["QUESTION"], [],
        column_map={"QUESTION": ["대표질문", "질문"]},
    )
    assert content == "대표질문: 가입 방법"


@pytest.mark.unit
def test_field_labels_names_a_field_that_column_map_does_not_have():
    """LLM 생성 필드는 column_map 에 없다 — 이름을 안 주면 값만 나가야 한다.

    엑셀 헤더로 폴백하는 근거는 그 헤더가 사람이 읽는 말이라는 것뿐이다. column_map 에
    없는 필드에는 그런 헤더가 없으므로 목표필드명으로 폴백하면 `SUMMARY_TEXT: ` 같은
    적재 DB 컬럼명이 청크마다 임베딩에 실린다.
    """
    fields = {"TITLE": "보험금 청구", "SUMMARY_TEXT": "청구서와 진단서를 준비한다."}
    unlabeled, _ = build_chunk_text(
        fields, ["TITLE", "SUMMARY_TEXT"], [], column_map={"TITLE": ["제목"]},
    )
    assert "SUMMARY_TEXT" not in unlabeled
    assert unlabeled == "제목: 보험금 청구\n청구서와 진단서를 준비한다."

    named, _ = build_chunk_text(
        fields, ["TITLE", "SUMMARY_TEXT"], [],
        column_map={"TITLE": ["제목"]},
        field_labels={"SUMMARY_TEXT": "안내요약"},
    )
    assert named == "제목: 보험금 청구\n안내요약: 청구서와 진단서를 준비한다."


@pytest.mark.unit
def test_json_path_without_labels_stays_value_only():
    """column_map 이 없는 경로(json_mapping)는 이름을 안 주면 값만 낸다.

    key_map 별칭(`depth4`)으로 폴백하면 시스템 key 가 임베딩에 실린다.
    """
    content, _ = build_chunk_text({"QUESTION": "가입 방법"}, ["QUESTION"], [])
    assert content == "가입 방법"


@pytest.mark.unit
def test_multiline_block_keeps_no_label():
    """여러 줄 블록은 자기 제목을 이미 갖고 있어 항목명을 붙이지 않는다."""
    content, _ = build_chunk_text(
        {"DETAIL_TEXT": "## 혜택\n- 5% 적립"}, ["DETAIL_TEXT"], [],
        field_labels={"DETAIL_TEXT": "상세내용"},
    )
    assert content == "## 혜택\n- 5% 적립"


@pytest.mark.unit
def test_chunk_prefix_carries_the_label_too():
    """분할 조각마다 반복되는 접두도 같은 규칙을 쓴다(재부착 계약: content 는 prefix 로 시작)."""
    content, prefix = build_chunk_text(
        {"TITLE": "7월 이벤트", "DETAIL_TEXT": "본문"}, ["TITLE", "DETAIL_TEXT"], ["TITLE"],
        field_labels={"TITLE": "제목"},
    )
    assert prefix == "제목: 7월 이벤트"
    assert content.startswith(prefix)


# ── 설정 해석 ────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_parse_field_labels_drops_empty_names():
    assert cp.parse_field_labels({"A": "질문", "B": "", "C": None, "": "x"}) == {"A": "질문"}
    assert cp.parse_field_labels(["A"]) == {}
    assert cp.parse_field_labels(None) == {}


@pytest.mark.unit
def test_resolve_field_labels_prefers_kwargs_over_document_metadata():
    metadata = {cp.FIELD_LABELS_KEY: {"PRODUCT_NM": "상품명"}}
    assert cp.resolve_field_labels({}, metadata) == {"PRODUCT_NM": "상품명"}
    assert cp.resolve_field_labels(
        {cp.FIELD_LABELS_KEY: {"PRODUCT_NM": "카드명"}}, metadata
    ) == {"PRODUCT_NM": "카드명"}


@pytest.mark.unit
def test_unknown_field_label_warns_but_does_not_fail(tmp_path, caplog):
    """이름을 잘못 적으면 라벨만 조용히 사라진다 — 기동 시 경고로 드러낸다."""
    cfg = """
    key_map:
      TITLE: [title]
    field_labels:
      TITLE: 제목
      TITEL: 제목
    text_fields: [TITLE]
    """
    path = tmp_path / "custom_field_json.yaml"
    path.write_text(textwrap.dedent(cfg), encoding="utf-8")
    with caplog.at_level("WARNING"):
        JsonRecordsMapper(
            config_file=path.name, resource_path=str(tmp_path),
            doc_type="x", extractor="json_mapping",
        )
    assert "TITEL" in caplog.text


# ── 문서형 접두(llm extractor) ───────────────────────────────────────────────

@pytest.mark.unit
def test_doc_prefix_labels_only_named_fields():
    meta = {"PRODUCT_NM": "삼성 iD ON 카드", "BIZ_ID": "P000210"}
    labels = {"PRODUCT_NM": "상품명"}
    assert dpx.build_prefix_text(meta, ["PRODUCT_NM", "BIZ_ID"], labels) == (
        "상품명: 삼성 iD ON 카드\nP000210\n"
    )
    # 이름이 없으면 종전 그대로 값만.
    assert dpx.build_prefix_text(meta, ["PRODUCT_NM"]) == "삼성 iD ON 카드\n"


@pytest.mark.unit
def test_doc_prefix_dedupes_by_value_not_by_rendered_line():
    """항목명만 다른 같은 값은 한 줄만 남긴다 — 접두는 모든 청크에 반복되기 때문이다."""
    meta = {"TITLE": "카드", "PRODUCT_NM": "카드"}
    assert dpx.build_prefix_text(
        meta, ["TITLE", "PRODUCT_NM"], {"TITLE": "제목", "PRODUCT_NM": "상품명"}
    ) == "제목: 카드\n"


@pytest.mark.unit
def test_doc_prefix_reads_labels_from_document_metadata():
    """custom_fields yaml → 문서 metadata → 청커 경로가 이어져 있는지."""
    metadata = {
        "PRODUCT_NM": "삼성 iD ON 카드",
        cp.CHUNK_PREFIX_FIELDS_KEY: ["PRODUCT_NM"],
        cp.FIELD_LABELS_KEY: {"PRODUCT_NM": "상품명"},
    }
    repeated, first_only = dpx.resolve_prefix_texts({}, metadata)
    assert repeated == "상품명: 삼성 iD ON 카드\n"
    assert first_only == ""
    assert dpx.reserved_prefix_text({}, metadata) == repeated


# ── 실제 출고 설정 ───────────────────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.parametrize("name", [
    "custom_field_faq.yaml", "custom_field_faq_json.yaml", "custom_field_cs_sss.yaml",
    "custom_field_cs_slf.yaml", "custom_field_cs_ssf.yaml", "custom_field_link.yaml",
    "custom_field_monimo_news.yaml", "custom_field_monimo_event.yaml",
])
def test_shipped_configs_name_every_body_field(name):
    """본문 필드는 모두 항목명을 갖는다 — 하나라도 빠지면 그 필드만 값으로 나가 불규칙해진다."""
    cfg = yaml.safe_load((RESOURCE_DIR / name).read_text(encoding="utf-8"))
    labels = cfg.get("field_labels") or {}
    missing = [f for f in (cfg.get("text_fields") or []) if f not in labels]
    assert not missing, f"{name}: {missing} 에 항목명이 없습니다."


@pytest.mark.unit
def test_faq_xlsx_and_faq_json_agree_on_labels():
    """같은 doc_type 이 원천 포맷에 따라 다른 본문 모양을 내지 않는다(이 작업의 출발점)."""
    tabular = yaml.safe_load(
        (RESOURCE_DIR / "custom_field_faq.yaml").read_text(encoding="utf-8"))
    json_cfg = yaml.safe_load(
        (RESOURCE_DIR / "custom_field_faq_json.yaml").read_text(encoding="utf-8"))
    assert tabular["field_labels"] == json_cfg["field_labels"]
    assert tabular["text_fields"] == json_cfg["text_fields"]


@pytest.mark.unit
def test_shipped_faq_configs_produce_the_same_chunk_text():
    """설정만이 아니라 두 매퍼의 실제 산출 본문이 같은지 확인한다."""
    record = {"id": "1", "corp_code": "IFP", "depth3": "가입",
              "depth4": "가입은 어떻게 하나요?", "description": "앱에서 가입할 수 있습니다."}
    json_mapper = JsonRecordsMapper(
        config_file="custom_field_faq_json.yaml", resource_path=str(RESOURCE_DIR),
        doc_type="faq", extractor="json_mapping",
    )
    # 출고 설정의 records 키(faqMenuList)로 감싼다 — 원천 JSON 과 같은 모양.
    json_rows = json_mapper.build_fields({"faqMenuList": [record]}, "faq")
    json_text = json_mapper.to_parse_format(json_rows, "faq")["elements"][0]["content"]

    tabular_mapper = TabularCustomFieldsMapper(
        doc_type="faq", extractor="tabular_mapping",
        config_file="custom_field_faq.yaml", resource_path=str(RESOURCE_DIR),
    )
    tabular_out = tabular_mapper.to_parse_format(
        {"data": [{"sheet_name": "FAQ", "data_rows": [record]}]}, "faq")
    tabular_text = tabular_out["elements"][0]["content"]

    assert json_text == tabular_text
    assert json_text == "질문: 가입은 어떻게 하나요?\n답변: 앱에서 가입할 수 있습니다."
