"""body_fields — 지정한 메타 필드를 청크 본문과 같은 값으로 채우는 계약.

문서 단위로 뽑힌 값(cs_hpp 의 CONTENT 처럼 LLM 재작성본)은 모든 청크에 같은 문자열이
붙어 청크 단위 검색이 깨진다. 이 설정은 그 필드를 청크 본문에 맞춘다.

설정 자리는 문서형 custom_fields yaml 이고, 파서가 문서 metadata 로 실어 청커로 넘긴다.
"""

import pytest

from genon.preprocessor.facade.common import config_parse as cp


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
            / "facade" / "chunking_processor.py"
        ).read_text(encoding="utf-8")
        reserved_block = source.split("reserved_keys = {", 1)[1].split("consumed_keys", 1)[0]
        assert "cp.BODY_FIELDS_KEY" in reserved_block
        assert "row_meta.pop(cp.BODY_FIELDS_KEY, None)" in source


@pytest.mark.unit
def test_cs_hpp_yaml_declares_content_as_body_field():
    """운영·개발 yaml 양쪽에서 CONTENT 가 본문과 같은 값을 받도록 선언돼 있다."""
    import pathlib

    import yaml as _yaml

    base = pathlib.Path(__file__).resolve().parents[2]
    for resource_dir in ("resource", "resource_dev"):
        cfg = _yaml.safe_load(
            (base / resource_dir / "custom_field_cs_hpp.yaml").read_text(encoding="utf-8")
        )
        assert cp.parse_field_name_list(cfg.get("body_fields")) == ["CONTENT"], resource_dir
