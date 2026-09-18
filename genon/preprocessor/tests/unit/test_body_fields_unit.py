"""body_fields — 지정한 메타 필드를 청크 본문과 같은 값으로 채우는 계약.

문서 단위로 뽑힌 값(LLM 재작성본 등)은 모든 청크에 같은 문자열이 붙어 청크 단위 검색이
깨진다. 이 설정은 그 필드를 청크 본문에 맞춘다.

설정 자리는 문서형 custom_fields yaml 이고, 파서가 문서 metadata 로 실어 청커로 넘긴다.
"""

import pytest

from genon.preprocessor.processing.common import config_parse as cp


@pytest.mark.unit
class TestBodyFieldsSetting:
    def test_reads_from_document_metadata(self):
        assert cp.resolve_body_fields({}, {"body_fields": ["CONTENT"]}) == ["CONTENT"]

    def test_kwargs_override_document_metadata(self):
        assert cp.resolve_body_fields(
            {"body_fields": ["SUMMARY_TEXT"]}, {"body_fields": ["CONTENT"]}
        ) == ["SUMMARY_TEXT"]

    def test_unset_means_no_field(self):
        assert cp.resolve_body_fields({}, {}) == []
        assert cp.resolve_body_fields({}, None) == []
        assert cp.resolve_body_fields({}, {"body_fields": []}) == []

    def test_comma_string_and_blank_entries(self):
        assert cp.parse_field_name_list("CONTENT, SUMMARY_TEXT") == ["CONTENT", "SUMMARY_TEXT"]
        assert cp.parse_field_name_list(["CONTENT", "", "  "]) == ["CONTENT"]
        assert cp.parse_field_name_list(123) == []

    def test_control_key_is_not_emitted_as_a_chunk_field(self):
        """제어값이라 청크 필드로 새 나가면 안 된다 — 청커 예약 키에 들어 있어야 한다."""
        source = (
            __import__("pathlib").Path(__file__).resolve().parents[2]
            / "processing" / "core" / "chunker.py"
        ).read_text(encoding="utf-8")
        reserved_block = source.split("reserved_keys = {", 1)[1].split("consumed_keys", 1)[0]
        assert "cp.BODY_FIELDS_KEY" in reserved_block
        assert "record_meta.pop(cp.BODY_FIELDS_KEY, None)" in source


@pytest.mark.unit
def test_cs_hpp_yaml_takes_content_from_the_source():
    """cs_hpp 는 body_fields 를 쓰지 않는다 — CONTENT 를 원천 `CONT` 에서 직접 받는다.

    예전 설정은 문서 단위 LLM 이 CONTENT 를 재작성했고, 문서당 한 값이 모든 청크에 같은
    문자열로 붙는 문제를 body_fields 로 막았다. 사이트 설정이 JSON 레코드 매핑으로 바뀌면서
    (`kind: records`) CONTENT 가 레코드별 원천 필드가 됐고 그 우회가 필요 없어졌다.
    선언이 되살아나면 원천 값이 청크 본문으로 덮이므로 양쪽 yaml 에서 함께 고정한다.
    """
    from shipped_config import load_shipped_named

    for resource_dir in ("resource", "resource_dev"):
        # 출고는 v2 표기다. raw 로 읽으면 body_fields 가 None 이 되어 검사가 조용히 무력해진다.
        cfg = load_shipped_named("custom_field_cs_hpp.yaml", resource_dir)
        assert cp.parse_field_name_list(cfg.get("body_fields")) == [], resource_dir
        assert cfg["key_map"]["CONTENT"][0] == "CONT", resource_dir
        assert "CONTENT" in cp.parse_field_name_list(cfg.get("text_fields")), resource_dir
