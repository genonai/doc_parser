"""v2 스키마 — 정규화·역변환·병행 검증 단위 테스트.

v2 는 새 파이프라인이 아니라 **내부(v1) 형태로 번역하는 앞단**이다. 그래서 여기서 고정할
것은 "번역이 정확한가" 하나이고, 동작 동일성은 같은 매퍼를 타는 구조가 보장한다.
"""
import textwrap

import pytest
import yaml

from genon.preprocessor.facade.enrichment import config_v2 as cv2
from genon.preprocessor.facade.enrichment.tabular_custom_fields import (
    TabularCustomFieldsMapper,
)

pytestmark = pytest.mark.unit

_V1 = {
    "column_map": {"QUESTION": ["질문"], "ANSWER": ["답변"]},
    "value_map": {"SEARCHABLE_YN": {"Y": ["노출"]}},
    "defaults": {"SEARCHABLE_YN": "N"},
    "constants": {"GROUP_C": "HPP"},
    "transforms": {"MOD_DT": "date_int_flex"},
    "html_text_fields": {"DETAIL_TEXT": "DETAIL_HTML"},
    "required": ["QUESTION"],
    "text_fields": ["QUESTION", "ANSWER"],
    "field_labels": {"QUESTION": "질문"},
    "split": True,
    "chunk_prefix_fields": ["QUESTION"],
}


def test_round_trip_preserves_every_key():
    """v1 → v2 → v1 왕복이 원본과 같아야 v2 가 그 설정을 온전히 표현한다는 뜻이다."""
    as_v2 = cv2.to_v2(_V1, "tabular_mapping")
    back, extractor = cv2.normalize(as_v2)
    assert extractor == "tabular_mapping"
    assert back == _V1


def test_field_rules_collapse_into_one_spec():
    """한 필드의 규칙이 6개 블록에 흩어지던 것이 한 dict 로 모인다(v2 의 존재 이유)."""
    as_v2 = cv2.to_v2(_V1, "tabular_mapping")
    assert as_v2["fields"]["SEARCHABLE_YN"] == {"values": {"Y": ["노출"]}, "default": "N"}
    assert as_v2["fields"]["DETAIL_TEXT"] == {"from": "DETAIL_HTML", "as": "html"}
    assert set(as_v2) <= cv2.TOP_LEVEL_KEYS


def test_to_v2_refuses_to_drop_unknown_keys():
    """옮기지 못한 키를 조용히 버리면 왕복 검증이 통과해 버린다 — 반드시 알려야 한다."""
    with pytest.raises(cv2.ConfigV2Error, match="옮기지 못한"):
        cv2.to_v2({**_V1, "someday_key": 1}, "tabular_mapping")


def test_v2_config_produces_same_fields_as_v1(tmp_path):
    """같은 입력에 같은 결과 — v2 는 같은 매퍼 코드를 탄다."""
    (tmp_path / "custom_field_v1.yaml").write_text(
        yaml.safe_dump(_V1, allow_unicode=True), encoding="utf-8"
    )
    (tmp_path / "custom_field_v2.yaml").write_text(
        yaml.safe_dump(cv2.to_v2(_V1, "tabular_mapping"), allow_unicode=True), encoding="utf-8"
    )
    payload = {"data": [{"sheet_name": "S", "data_rows": [
        {"질문": "가입 방법은?", "답변": "앱에서"}]}]}

    def rows(name):
        mapper = TabularCustomFieldsMapper(
            config_file=name, resource_path=str(tmp_path),
            doc_type="faq", extractor="tabular_mapping",
        )
        return mapper.to_parse_format_from_fields(mapper.build_fields(payload, "faq"), "faq")

    assert rows("custom_field_v1.yaml") == rows("custom_field_v2.yaml")


def test_field_spec_must_be_a_dict(tmp_path):
    """`Q:` 처럼 값을 빠뜨리면 null 로 파싱된다 — v1 에서는 조용히 통과했다."""
    cfg = tmp_path / "custom_field_bad.yaml"
    cfg.write_text("schema: v2\nsource: {kind: rows}\nfields:\n  Q:\n", encoding="utf-8")
    with pytest.raises(ValueError, match="object 여야"):
        TabularCustomFieldsMapper(
            config_file=cfg.name, resource_path=str(tmp_path),
            doc_type="x", extractor="tabular_mapping",
        )


@pytest.mark.parametrize(
    "body, expect",
    [
        ("source:\n  kind: bogus\n", "source.kind"),
        ("source:\n  kind: rows\n  records_at: x\n", "records_at"),
        ("source:\n  kind: rows\nfields:\n  Q: {alias: [질문], typo: 1}\n", "typo"),
        ("source:\n  kind: rows\nfilter:\n  - {field: X, in: [Y]}\n", "filter"),
        ("source:\n  kind: document\nfields:\n  Q: {alias: [질문]}\n", "alias"),
    ],
)
def test_v2_rejects_malformed_config(body, expect):
    """v2 는 모르는 키·잘못된 자리를 조용히 무시하지 않는다."""
    cfg = yaml.safe_load(textwrap.dedent("schema: v2\n" + body))
    with pytest.raises(cv2.ConfigV2Error, match=expect):
        cv2.normalize(cfg)


def test_preprocess_blocks_survive_round_trip():
    """markdown/html 전처리는 parser 가 소비한다 — v2 는 source.pre 에 담고 그대로 되돌린다."""
    v1 = {"url": "u", "model": "m", "output_fields": ["A"],
          "markdown": {"text_fence": True}, "html": {"marker_headings": True}}
    as_v2 = cv2.to_v2(v1, "llm")
    assert as_v2["source"]["pre"] == {"markdown": {"text_fence": True},
                                      "html": {"marker_headings": True}}
    back, _ = cv2.normalize(as_v2)
    assert back == v1
