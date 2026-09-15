"""chunk_prefix_fields / first_chunk_fields — 메타 필드를 청크 본문 앞에 얹는 계약.

body_fields 의 반대 방향이다. 문서 단위로 뽑힌 식별값(PRODUCT_NM, CS_CATEGORY)은 metadata
컬럼에만 있으면 임베딩 검색에 안 걸리므로 청크 본문에 실어야 한다.

둘의 차이는 반복 여부뿐이다 — chunk_prefix_fields 는 모든 청크, first_chunk_fields 는
문서의 첫 청크에만 1회.
"""

import pathlib

import pytest
import yaml as _yaml

from genon.preprocessor.facade.chunking import doc_prefix as dpx
from genon.preprocessor.facade.common import config_parse as cp

_BASE = pathlib.Path(__file__).resolve().parents[2]


@pytest.mark.unit
class TestFieldListResolution:
    def test_reads_from_document_metadata(self):
        meta = {"chunk_prefix_fields": ["PRODUCT_NM"], "first_chunk_fields": ["CS_CATEGORY"]}
        assert cp.resolve_chunk_prefix_fields({}, meta) == ["PRODUCT_NM"]
        assert cp.resolve_first_chunk_fields({}, meta) == ["CS_CATEGORY"]

    def test_kwargs_override_document_metadata(self):
        assert cp.resolve_chunk_prefix_fields(
            {"chunk_prefix_fields": "TITLE"}, {"chunk_prefix_fields": ["PRODUCT_NM"]}
        ) == ["TITLE"]

    def test_unset_means_no_field(self):
        assert cp.resolve_chunk_prefix_fields({}, None) == []
        assert cp.resolve_first_chunk_fields({}, {}) == []

    def test_repeated_field_is_dropped_from_first_chunk_list(self):
        """매 청크에 이미 들어가는 필드가 첫 청크에서 두 줄로 겹치지 않는다."""
        meta = {"chunk_prefix_fields": ["TITLE"], "first_chunk_fields": ["TITLE", "CS_CATEGORY"]}
        assert cp.resolve_first_chunk_fields({}, meta) == ["CS_CATEGORY"]


@pytest.mark.unit
class TestPrefixText:
    def test_renders_value_only_without_label(self):
        """필드명은 DB 컬럼명이라 라벨로 노출하면 임베딩에 잡음이 된다."""
        assert dpx.build_prefix_text({"PRODUCT_NM": "삼성 iD ON 카드"}, ["PRODUCT_NM"]) == (
            "삼성 iD ON 카드\n"
        )

    def test_skips_missing_and_unrenderable_values(self):
        meta = {"A": "값", "B": None, "C": "", "D": {"nested": 1}}
        assert dpx.build_prefix_text(meta, ["A", "B", "C", "D", "MISSING"]) == "값\n"

    def test_joins_scalar_list(self):
        assert dpx.build_prefix_text({"K": ["결제취소", "환불"]}, ["K"]) == "결제취소, 환불\n"

    def test_deduplicates_identical_values(self):
        assert dpx.build_prefix_text({"A": "카드", "B": "카드"}, ["A", "B"]) == "카드\n"

    def test_reserved_text_covers_both_lists(self):
        """청커 예산은 첫 청크 몫까지 더한 보수적 상한이라야 어떤 청크도 한도를 안 넘는다."""
        meta = {
            "chunk_prefix_fields": ["PRODUCT_NM"],
            "first_chunk_fields": ["CS_CATEGORY"],
            "PRODUCT_NM": "카드",
            "CS_CATEGORY": "결제/환불",
        }
        assert dpx.reserved_prefix_text({}, meta) == "카드\n결제/환불\n"

    def test_context_metadata_overrides_document_metadata(self):
        merged = dpx.merge_context_metadata(
            {"A": "doc"}, {"_enrichment_context": {"metadata": {"A": "ctx"}}})
        assert merged["A"] == "ctx"


@pytest.mark.unit
class TestChunkerAndProcessorContract:
    def test_chunker_reserves_prefix_in_size_calculation(self):
        """접두 몫을 헤더 라인과 같은 자리에서 예약해야 산출 청크가 chunk_size 를 지킨다."""
        source = (_BASE / "facade" / "chunking" / "smart_chunker.py").read_text(encoding="utf-8")
        assert "chunk_prefix_text: str = \"\"" in source
        assert "return self.chunk_prefix_text + hp.build_header_line(" in source

    def test_control_keys_are_not_emitted_as_chunk_fields(self):
        source = (_BASE / "facade" / "core" / "chunker.py").read_text(encoding="utf-8")
        reserved_block = source.split("reserved_keys = {", 1)[1].split("consumed_keys", 1)[0]
        assert "cp.CHUNK_PREFIX_FIELDS_KEY" in reserved_block
        assert "cp.FIRST_CHUNK_FIELDS_KEY" in reserved_block

    def test_first_chunk_prefix_is_attached_exactly_once(self):
        """문서당 1회 계약.

        기준은 "첫 번째 청크" 가 아니라 "살아남은 첫 번째 청크" 다 — on_chunk 이 첫 청크를
        버렸을 때 그 접두가 문서에서 통째로 사라지면 안 된다.
        """
        source = (_BASE / "facade" / "core" / "chunker.py").read_text(encoding="utf-8")
        assert 'notes["first_prefix_text"] if notes["first_prefix_pending"] else ""' in source
        # 플래그는 청크를 실제로 유지했을 때만 내려간다. 버린 청크는 dropped 를 세고 끝이라
        # 두 갈래가 _call_on_chunk 한 곳에서 갈린다 — 반복문이 어디에 있든 계약이 지켜진다.
        branch = source.split('notes["dropped"] += 1', 1)[1].split("return text, drop", 1)[0]
        assert branch.lstrip().startswith("else:")
        assert 'notes["first_prefix_pending"] = False' in branch


@pytest.mark.unit
@pytest.mark.parametrize(
    "yaml_name,key,expected",
    [
        # TITLE 은 사이트 운영 설정(263f53ea)이 첫 청크 접두에 함께 넣었다.
        ("custom_field_cs_hpp.yaml", "first_chunk_fields", ["CS_CATEGORY", "TITLE"]),
        ("custom_field_product_hpp.yaml", "chunk_prefix_fields", ["PRODUCT_NM"]),
    ],
)
def test_operational_yaml_declares_prefix_fields(yaml_name, key, expected):
    """운영·개발 yaml 양쪽이 같은 선언을 갖는다."""
    from shipped_config import load_shipped_named

    for resource_dir in ("resource", "resource_dev"):
        # 출고는 v2 표기다(`body.repeat` / `body.once`). raw 로 읽으면 둘 다 None 이 된다.
        cfg = load_shipped_named(yaml_name, resource_dir)
        assert cp.parse_field_name_list(cfg.get(key)) == expected, (resource_dir, yaml_name)


@pytest.mark.unit
def test_enricher_ships_rules_into_document_metadata():
    """규칙은 값이 아니므로 enricher 가 이름만 문서 metadata 로 실어 청커에 넘긴다."""
    source = (_BASE / "facade" / "enrichment" / "custom_fields_enricher.py").read_text(
        encoding="utf-8")
    assert "cp.CHUNK_PREFIX_FIELDS_KEY: list(self._chunk_prefix_fields)" in source
    assert "cp.FIRST_CHUNK_FIELDS_KEY: list(self._first_chunk_fields)" in source
