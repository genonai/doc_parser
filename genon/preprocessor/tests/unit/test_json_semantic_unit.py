"""json_semantic — 의미 단위 섹션 매핑(SemanticJsonMapper) 단위 테스트.

`json_records`(레코드 배열)와 달리 이 모듈은 **대상 하나를 깊게 설명하는 JSON**을 성격별
섹션으로 쪼갠다. 여기서는 그 정규화 규칙(HTML→Markdown, 제목 승격, 부모-자식 중복 제거,
공통 정보 상속, 식별자뿐인 섹션 접기, 원문 key 이름이 본문에 새어나가지 않는 것)을 규칙별로
검증한다. LLM 은 호출하지 않는다(llm_fields_scope="document" 는 parser_processor 쪽에서
검증한다).
"""
import json
import logging
import textwrap
from pathlib import Path

import pytest

from genon.preprocessor.facade.enrichment.json_semantic import (
    SemanticJsonMapper,
    build_semantic_json_mappers,
)

pytestmark = pytest.mark.unit


BASE_CONFIG = """
shared_fields:
  BIZ_ID:     [wcmsId]
  PRODUCT_C:  [code]
  PRODUCT_NM: [cardTitle]
sections:
  bubble:   { name: 혜택 상세,     include: true  }
  ksp:      { name: 주요 혜택 요약, include: true  }
  htmlList: { name: 상품 문서,     include: true  }
  mpo:      { name: 추천 상품,     include: false }
ignore_keys:
  - "*Img*"
constants:
  GROUP_C: "HPP"
"""


def write_mapper(tmp_path, config_text=BASE_CONFIG, doc_type="product_hpp"):
    """설정 yaml 을 임시 파일로 쓰고 매퍼를 만든다(json_records 테스트와 같은 패턴)."""
    path = tmp_path / "custom_field_semantic.yaml"
    path.write_text(textwrap.dedent(config_text), encoding="utf-8")
    return SemanticJsonMapper(
        config_file=path.name,
        resource_path=str(tmp_path),
        doc_type=doc_type,
        extractor="json_semantic",
    )


def _texts(mapper, fields_list):
    return [mapper.build_text(fields) for fields in fields_list]


def _by_title(fields_list, title):
    return next(f for f in fields_list if f["_title"] == title)


# ── HTML 값 → Markdown(표/목록 유지) ─────────────────────────────────────────

def test_html_value_keeps_table_and_becomes_own_section(tmp_path):
    payload = {
        "wcmsId": "W1", "code": "C1", "cardTitle": "테스트카드",
        "htmlList": {
            "feeUrl": (
                "<h3>연회비</h3><table><tr><th>구분</th><th>금액</th></tr>"
                "<tr><td>국내전용</td><td>10,000원</td></tr></table>"
            ),
        },
    }
    mapper = write_mapper(tmp_path)
    fields_list = mapper.build_fields(payload, "product_hpp", table_format="markdown")
    section = _by_title(fields_list, "연회비")
    text = mapper.build_text(section)

    assert "| 구분 | 금액 |" in text
    assert "10,000원" in text
    # 규칙 4 — 값 자체가 들고 있던 제목(<h3>연회비</h3>)이 섹션 제목으로 승격되고 본문에서 빠진다.
    assert text.count("연회비") == 1  # 헤더 한 번뿐, 본문에 중복 없음


# ── 인라인 <br> 평문화 ────────────────────────────────────────────────────────

def test_inline_br_is_flattened_regardless_of_length(tmp_path):
    """길이<=120·detect_format 판정과 무관하게 태그가 있으면 평문화한다(cardSlogan 재현)."""
    payload = {
        "wcmsId": "W1", "code": "C1", "cardTitle": "테스트카드",
        "cardSlogan": "가족과 함께할 때도 필요한 실속<br>새마을금고 혜택까지",
    }
    mapper = write_mapper(tmp_path)
    fields_list = mapper.build_fields(payload, "product_hpp")
    text = mapper.build_text(fields_list[0])

    assert "<br>" not in text
    assert "가족과 함께할 때도 필요한 실속 새마을금고 혜택까지" in text


# ── 문자열 배열 → 목록 섹션 ───────────────────────────────────────────────────

def test_string_array_becomes_bullet_list_section(tmp_path):
    payload = {
        "wcmsId": "W1", "code": "C1", "cardTitle": "테스트카드",
        "benefit": ["현금카드 기능", "포인트 적립", "무이자할부"],
    }
    mapper = write_mapper(tmp_path)
    fields_list = mapper.build_fields(payload, "product_hpp")
    section = _by_title(fields_list, "benefit")
    text = mapper.build_text(section)

    assert "- 현금카드 기능" in text
    assert "- 포인트 적립" in text
    assert "- 무이자할부" in text
    # 파이썬 list repr(`['a', 'b']`) 이 새어 나가지 않는다.
    assert "['" not in text and "']" not in text


# ── 객체 배열 → 원소별 섹션(제목은 형제 name/title) ──────────────────────────

def test_object_array_becomes_one_section_per_item_titled_by_sibling(tmp_path):
    payload = {
        "wcmsId": "W1", "code": "C1", "cardTitle": "테스트카드",
        "bubble": [
            {"title": "포인트 적립 혜택", "serviceUrl": "<h4>포인트 적립 혜택</h4><p>적립 안내.</p>"},
            {"title": "주유 할인 혜택", "serviceUrl": "<h4>주유 할인 혜택</h4><p>할인 안내.</p>"},
        ],
    }
    mapper = write_mapper(tmp_path)
    fields_list = mapper.build_fields(payload, "product_hpp")
    titles = {f["_title"] for f in fields_list if f.get("SECTION_NM") == "혜택 상세"}

    assert titles == {"포인트 적립 혜택", "주유 할인 혜택"}
    first = _by_title(fields_list, "포인트 적립 혜택")
    assert "적립 안내." in mapper.build_text(first)


# ── 값 자체 제목 승격 ─────────────────────────────────────────────────────────

def test_value_own_heading_is_promoted_to_section_title(tmp_path):
    payload = {
        "wcmsId": "W1", "code": "C1", "cardTitle": "테스트카드",
        "htmlList": {"noticeUrl": "<h3>이용 유의사항</h3><p>유의사항 본문입니다.</p>"},
    }
    mapper = write_mapper(tmp_path)
    fields_list = mapper.build_fields(payload, "product_hpp")
    section = _by_title(fields_list, "이용 유의사항")

    assert "유의사항 본문입니다." in section["_body"]
    # h3 로 쓰인 원문 제목 텍스트는 본문에서 제거된다(제목으로만 승격).
    assert "이용 유의사항" not in section["_body"]


# ── 부모와 같은 제목 중복 제거 ────────────────────────────────────────────────

def test_child_heading_matching_array_label_is_not_repeated_in_body(tmp_path):
    payload = {
        "wcmsId": "W1", "code": "C1", "cardTitle": "테스트카드",
        "bubble": [{"title": "특가 혜택", "serviceUrl": "<h4>특가 혜택</h4><p>본문 내용입니다.</p>"}],
    }
    mapper = write_mapper(tmp_path)
    fields_list = mapper.build_fields(payload, "product_hpp")
    section = _by_title(fields_list, "특가 혜택")
    text = mapper.build_text(section)

    assert "본문 내용입니다." in text
    # "특가 혜택"은 헤더( [혜택 상세] 특가 혜택 )에 한 번만 나오고 본문에서 중복되지 않는다.
    assert text.count("특가 혜택") == 1


# ── 공통 정보 상속 ────────────────────────────────────────────────────────────

def test_shared_fields_are_inherited_by_nested_sections(tmp_path):
    payload = {
        "wcmsId": "W1", "code": "C1", "cardTitle": "새마을금고 삼성카드 7",
        "htmlList": {"noticeUrl": "<h3>이용 유의사항</h3><p>본문.</p>"},
    }
    mapper = write_mapper(tmp_path)
    fields_list = mapper.build_fields(payload, "product_hpp")
    section = _by_title(fields_list, "이용 유의사항")

    assert section["PRODUCT_NM"] == "새마을금고 삼성카드 7"
    assert "상품명: 새마을금고 삼성카드 7" in mapper.build_text(section)


# ── ignore_keys glob ─────────────────────────────────────────────────────────

def test_ignore_keys_glob_matches_multiple_keys(tmp_path):
    payload = {
        "wcmsId": "W1", "code": "C1", "cardTitle": "테스트카드",
        "imgInfo": {
            "pcImg1": "/wcms/image/pc.png",
            "moImg1": "/wcms/image/mo.png",
        },
        "benefit": ["혜택 문구"],
    }
    mapper = write_mapper(tmp_path)
    fields_list = mapper.build_fields(payload, "product_hpp")
    full_text = "\n".join(_texts(mapper, fields_list))

    assert "pc.png" not in full_text
    assert "mo.png" not in full_text
    assert "pcImg1" not in full_text
    assert "moImg1" not in full_text


# ── include:false 서브트리 전체 제외 ─────────────────────────────────────────

def test_include_false_excludes_whole_subtree(tmp_path):
    payload = {
        "wcmsId": "W1", "code": "C1", "cardTitle": "테스트카드",
        "mpo": [{"code": "X1", "name": "다른 카드", "ksp": [{"title": "다른 카드만의 혜택 문구"}]}],
    }
    mapper = write_mapper(tmp_path)
    fields_list = mapper.build_fields(payload, "product_hpp")
    full_text = "\n".join(_texts(mapper, fields_list))

    assert "다른 카드만의 혜택 문구" not in full_text
    assert "추천 상품" not in full_text


# ── SOURCE_JSON_PATH 형식 ─────────────────────────────────────────────────────

def test_source_json_path_formats(tmp_path):
    payload = {
        "wcmsId": "W1", "code": "C1", "cardTitle": "테스트카드",
        "extraNote": "루트에 남는 값입니다 여러 단어",
        "htmlList": {"feeUrl": "<h3>연회비</h3><p>10,000원.</p>"},
        "bubble": [
            {"title": "첫 혜택", "serviceUrl": "<h4>첫 혜택</h4><p>첫 안내.</p>"},
            {"title": "둘째 혜택", "serviceUrl": "<h4>둘째 혜택</h4><p>둘째 안내.</p>"},
        ],
    }
    mapper = write_mapper(tmp_path)
    fields_list = mapper.build_fields(payload, "product_hpp")

    root_paths = {f["SOURCE_JSON_PATH"] for f in fields_list if f["_title"] == "개요"}
    assert "$" in root_paths
    assert _by_title(fields_list, "연회비")["SOURCE_JSON_PATH"] == "$.htmlList.feeUrl"
    # JSONPath 규약대로 0-base — 첫 원소가 [0], "둘째 혜택"(두 번째 원소)은 [1].
    assert _by_title(fields_list, "첫 혜택")["SOURCE_JSON_PATH"] == "$.bubble[0]"
    assert _by_title(fields_list, "둘째 혜택")["SOURCE_JSON_PATH"] == "$.bubble[1]"


# ── 식별자뿐인 섹션 접기 + 경고 ────────────────────────────────────────────────

def test_identifier_only_sections_collapse_with_warning(tmp_path, caplog):
    payload = {
        "wcmsId": "W1", "code": "C1", "cardTitle": "테스트카드",
        "ksp": [
            {"code": "P000210", "title": "혜택 A"},
            {"code": "P000211", "title": "혜택 B"},
        ],
    }
    mapper = write_mapper(tmp_path)
    with caplog.at_level("WARNING"):
        fields_list = mapper.build_fields(payload, "product_hpp")

    # title 은 forced_title(라벨)로 소비되고 남는 건 identifier(code) 뿐이라 둘 다 접힌다.
    assert not any(f["_title"] in ("혜택 A", "혜택 B") for f in fields_list)
    assert "식별자/빈 본문뿐인 섹션 2건을 접었습니다" in caplog.text


# ── 규칙 회귀: 본문에 원문 JSON 키 이름이 나오지 않는다 ───────────────────────

LEAKY_KEY_PAYLOAD = {
    "wcmsId": "1202631",
    "code": "AAP1344",
    "cardTitle": "새마을금고 삼성카드 7",
    "cardSlogan": "가족과 함께할 때도 필요한 실속<br>새마을금고 혜택까지",
    "mpoNum": "여신금융협회 심의필 제 2025-C1h-14606호 (2025.09.30)",
    "imgInfo": {"pcImg1": "/img/pc.png", "moImg1": "/img/mo.png"},
    "is3d": "N",
    "pCApplyNoYn": "N",
    "bubble": [
        {
            "title": "0.5%~3% 빅포인트 적립",
            "tabName": "포인트 적립",
            "serviceName": "0.5%~3% 빅포인트 적립",
            "serviceCode": "P006890",
            "serviceUrl": "<h4>0.5%~3% 빅포인트 적립</h4><p>가맹점에서 적립됩니다.</p>",
        },
    ],
}

# 원문에만 있고 사람 라벨이 없는 key 이름들 — 본문에 하나라도 보이면 회귀.
_LEAKY_KEYS = [
    "cardSlogan", "mpoNum", "imgInfo", "pcImg1", "moImg1", "is3d", "pCApplyNoYn",
    "tabName", "serviceName", "serviceCode", "wcmsId",
]


def test_original_json_key_names_never_leak_into_body(tmp_path):
    mapper = write_mapper(tmp_path)
    fields_list = mapper.build_fields(LEAKY_KEY_PAYLOAD, "product_hpp")
    full_text = "\n".join(_texts(mapper, fields_list))

    for key in _LEAKY_KEYS:
        assert key not in full_text, f"원문 key '{key}' 가 본문에 노출됨"

    # 값 자체는 살아 있어야 한다(키만 빠지고 내용은 남는다).
    assert "가족과 함께할 때도 필요한 실속 새마을금고 혜택까지" in full_text
    assert "여신금융협회 심의필" in full_text
    assert "포인트 적립" in full_text
    # serviceCode/serviceName(중복)/identifier 값 자체도 검색에 쓸모없어 빠진다.
    assert "P006890" not in full_text


def test_shared_field_without_label_is_metadata_only():
    """규칙 10 — BIZ_ID 는 라벨이 없어 본문에서 빠지고 metadata 에만 남는다."""
    from genon.preprocessor.facade.enrichment.json_semantic import _SHARED_FIELD_LABELS

    assert "BIZ_ID" not in _SHARED_FIELD_LABELS


def test_biz_id_value_absent_from_body_but_present_in_metadata(tmp_path):
    mapper = write_mapper(tmp_path)
    fields_list = mapper.build_fields(LEAKY_KEY_PAYLOAD, "product_hpp")
    result = mapper.to_parse_format(fields_list, "product_hpp")

    for element in result["elements"]:
        assert "1202631" not in element["content"]
        assert element["metadata"]["BIZ_ID"] == "1202631"


def test_no_python_list_repr_in_body(tmp_path):
    payload = {
        "wcmsId": "W1", "code": "C1", "cardTitle": "테스트카드",
        "benefit": ["첫째 혜택", "둘째 혜택", "셋째 혜택"],
    }
    mapper = write_mapper(tmp_path)
    fields_list = mapper.build_fields(payload, "product_hpp")
    full_text = "\n".join(_texts(mapper, fields_list))

    assert "['" not in full_text
    assert "']" not in full_text


# ── SECTION_NM 은 항상 값이 있다(설정 없는 key 는 key 이름 그대로) ────────────

def test_section_nm_falls_back_to_key_name_when_unconfigured(tmp_path):
    payload = {
        "wcmsId": "W1", "code": "C1", "cardTitle": "테스트카드",
        "specialNotice": "<h4>특이사항 안내</h4><p>본문.</p>",
    }
    mapper = write_mapper(tmp_path)
    fields_list = mapper.build_fields(payload, "product_hpp")

    assert all(f.get("SECTION_NM") not in (None, "") for f in fields_list)
    section = _by_title(fields_list, "특이사항 안내")
    assert section["SECTION_NM"] == "specialNotice"


def test_root_overview_section_nm_is_default_title(tmp_path):
    payload = {"wcmsId": "W1", "code": "C1", "cardTitle": "테스트카드", "note": "루트 leftover 값"}
    mapper = write_mapper(tmp_path)
    fields_list = mapper.build_fields(payload, "product_hpp")
    root = _by_title(fields_list, "개요")

    assert root["SECTION_NM"] == "개요"


# ── 깊이/노드 상한 초과 시 경고 ────────────────────────────────────────────────

def test_depth_limit_exceeded_warns(tmp_path, caplog):
    node = {"leaf": "바닥 값"}
    for _ in range(20):
        node = {"child": node}
    payload = {"wcmsId": "W1", "code": "C1", "cardTitle": "테스트카드", "chain": node}

    mapper = write_mapper(tmp_path)
    with caplog.at_level("WARNING"):
        mapper.build_fields(payload, "product_hpp")

    assert "상한을 초과" in caplog.text


def test_node_count_limit_exceeded_warns(tmp_path, caplog):
    payload = {
        "wcmsId": "W1", "code": "C1", "cardTitle": "테스트카드",
        "items": [{"note": f"항목 {i} 입니다"} for i in range(5005)],
    }
    mapper = write_mapper(tmp_path)
    with caplog.at_level("WARNING"):
        mapper.build_fields(payload, "product_hpp")

    assert "상한을 초과" in caplog.text


# ── shared_fields/sections 누락 시 기동 실패 ──────────────────────────────────

def test_shared_fields_is_required(tmp_path):
    config = "sections:\n  bubble: { name: 혜택, include: true }\n"
    with pytest.raises(ValueError, match="shared_fields"):
        write_mapper(tmp_path, config)


def test_sections_is_required(tmp_path):
    config = "shared_fields:\n  PRODUCT_NM: [cardTitle]\n"
    with pytest.raises(ValueError, match="sections"):
        write_mapper(tmp_path, config)


# ── 빌더(설정 라우팅) ────────────────────────────────────────────────────────

def test_builder_selects_only_json_semantic_configs(tmp_path):
    path = tmp_path / "custom_field_semantic.yaml"
    path.write_text(textwrap.dedent(BASE_CONFIG), encoding="utf-8")
    configs = [
        {"extractor": "json_mapping", "config_file": "other.yaml"},
        {
            "extractor": "json_semantic",
            "config_file": path.name,
            "resource_path": str(tmp_path),
            "doc_type": "product_hpp",
        },
    ]
    mappers = build_semantic_json_mappers(configs)
    assert len(mappers) == 1
    assert mappers[0].doc_types == ("product_hpp",)


# ── document_input_fields(LLM 문서 스코프 입력) ───────────────────────────────

def test_document_input_fields_merges_all_sections_body(tmp_path):
    payload = {
        "wcmsId": "W1", "code": "C1", "cardTitle": "테스트카드",
        "benefit": ["첫째 혜택"],
        "htmlList": {"noticeUrl": "<h3>이용 유의사항</h3><p>유의사항 본문.</p>"},
    }
    mapper = write_mapper(tmp_path)
    fields_list = mapper.build_fields(payload, "product_hpp")
    merged = mapper.document_input_fields(fields_list)

    assert merged["PRODUCT_NM"] == "테스트카드"
    assert "첫째 혜택" in merged["PRODUCT_INFO"]
    assert "유의사항 본문" in merged["PRODUCT_INFO"]


# ── 리뷰 지적 1 회귀 — 하위 객체의 동명 키(code/name)가 루트 identity 를 덮지 않는다 ────

def test_child_object_code_does_not_override_root_identity(tmp_path):
    """`ksp[].code`(BENEFIT1) 가 루트의 상품코드(CARD1)를 덮어쓰던 결함의 회귀 방지.

    identity 는 문서 루트에서 한 번만 확정되고 `_walk` 는 그 값을 불변으로 상속만 한다
    (json_semantic.py 모듈 docstring "키 지정 방식" 참고).
    """
    payload = {
        "wcmsId": "W1", "code": "CARD1", "cardTitle": "원본 카드",
        "ksp": [
            {"code": "BENEFIT1", "title": "적립 혜택", "description": "포인트 적립 안내 문구입니다"},
        ],
    }
    mapper = write_mapper(tmp_path)
    fields_list = mapper.build_fields(payload, "product_hpp")
    section = _by_title(fields_list, "적립 혜택")

    assert section["PRODUCT_C"] == "CARD1"
    assert section["PRODUCT_NM"] == "원본 카드"


def test_excluded_nested_product_cannot_fill_missing_root_identity(tmp_path):
    """루트 상품코드가 없을 때 제외 대상 mpo[].code로 required 검사를 통과하면 안 된다."""
    payload = {
        "wcmsId": "W1",
        "cardTitle": "원본 카드",
        "bubble": [{"title": "루트 혜택", "description": "정상 혜택 본문입니다"}],
        "mpo": [{"code": "OTHER-CARD", "name": "다른 카드"}],
    }
    config = BASE_CONFIG + "required_shared_fields: [PRODUCT_C, PRODUCT_NM]\n"
    mapper = write_mapper(tmp_path, config)

    with pytest.raises(ValueError, match="PRODUCT_C"):
        mapper.build_fields(payload, "product_hpp")


# ── required_shared_fields / missing_policy ──────────────────────────────────

REQUIRED_CONFIG = BASE_CONFIG + "required_shared_fields: [PRODUCT_NM]\n"
REQUIRED_CONFIG_SKIP = REQUIRED_CONFIG + "missing_policy: skip\n"


def test_required_shared_fields_missing_raises_by_default(tmp_path):
    payload = {"wcmsId": "W1", "code": "C1"}  # cardTitle(PRODUCT_NM) 없음
    mapper = write_mapper(tmp_path, REQUIRED_CONFIG)
    with pytest.raises(ValueError, match="필수 공통 필드"):
        mapper.build_fields(payload, "product_hpp")


def test_required_shared_fields_missing_with_skip_policy_returns_empty(tmp_path, caplog):
    payload = {"wcmsId": "W1", "code": "C1"}
    mapper = write_mapper(tmp_path, REQUIRED_CONFIG_SKIP)
    with caplog.at_level("WARNING"):
        fields_list = mapper.build_fields(payload, "product_hpp")

    assert fields_list == []
    assert "필수 공통 필드" in caplog.text


# ── extractor 집합 분리(리뷰 지적 3) ──────────────────────────────────────────

def test_build_json_records_mappers_ignores_json_semantic_configs(tmp_path):
    """공개 빌더 build_json_records_mappers 에 json_semantic 설정을 넣어도 무시된다."""
    from genon.preprocessor.facade.enrichment.json_records import build_json_records_mappers

    path = tmp_path / "custom_field_semantic.yaml"
    path.write_text(textwrap.dedent(BASE_CONFIG), encoding="utf-8")
    configs = [{
        "extractor": "json_semantic",
        "config_file": path.name,
        "resource_path": str(tmp_path),
        "doc_type": "product_hpp",
    }]

    assert build_json_records_mappers(configs) == []


def test_build_semantic_json_mappers_ignores_json_mapping_configs(tmp_path):
    """build_semantic_json_mappers 에 json_mapping 설정을 넣어도 무시된다."""
    configs = [{
        "extractor": "json_mapping",
        "config_file": "other.yaml",
        "resource_path": str(tmp_path),
        "doc_type": "product_hpp",
    }]

    assert build_semantic_json_mappers(configs) == []


def test_semantic_mapper_rejects_json_mapping_extractor(tmp_path):
    path = tmp_path / "custom_field_semantic.yaml"
    path.write_text(textwrap.dedent(BASE_CONFIG), encoding="utf-8")
    with pytest.raises(ValueError, match="extractor"):
        SemanticJsonMapper(
            config_file=path.name, resource_path=str(tmp_path),
            doc_type="product_hpp", extractor="json_mapping",
        )


def test_json_records_mapper_rejects_json_semantic_extractor(tmp_path):
    from genon.preprocessor.facade.enrichment.json_records import JsonRecordsMapper

    path = tmp_path / "custom_field_records.yaml"
    path.write_text("key_map:\n  TITLE: [title]\ntext_fields: [TITLE]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="extractor"):
        JsonRecordsMapper(
            config_file=path.name, resource_path=str(tmp_path),
            doc_type="x", extractor="json_semantic",
        )


# ── SALE_STATUS / PRODUCT_ATTRS 복원(리뷰 지적 4) ─────────────────────────────

ATTRS_CONFIG = """
shared_fields:
  PRODUCT_NM:    [cardTitle]
  PRODUCT_ATTRS: [benefit]
sections:
  benefit: { name: 주요 혜택, include: true }
"""

SALE_STATUS_CONFIG = """
shared_fields:
  PRODUCT_NM:  [cardTitle]
  SALE_STATUS: [saleStatus]
sections:
  htmlList: { name: 상품 문서, include: true }
"""

SALE_STATUS_DEFAULT_CONFIG = SALE_STATUS_CONFIG + """
defaults:
  SALE_STATUS: ON_SALE
required_shared_fields: [SALE_STATUS]
"""

SALE_STATUS_CONSTANT_CONFIG = SALE_STATUS_DEFAULT_CONFIG + """
constants:
  SALE_STATUS: FIXED
"""


def test_product_attrs_scalar_array_kept_in_body_and_metadata(tmp_path):
    """PRODUCT_ATTRS(스칼라 배열)는 metadata 에 리스트로 실리고, 본문의 "주요 혜택" 목록
    섹션도 억제되지 않고 그대로 남는다(규칙 보강 — 배열은 정체성이 아니라 콘텐츠)."""
    payload = {"cardTitle": "테스트카드", "benefit": ["첫째 혜택", "둘째 혜택"]}
    mapper = write_mapper(tmp_path, ATTRS_CONFIG)
    fields_list = mapper.build_fields(payload, "product_hpp")
    result = mapper.to_parse_format(fields_list, "product_hpp")

    section = next(e for e in result["elements"] if e["metadata"]["SECTION_NM"] == "주요 혜택")
    assert section["metadata"]["PRODUCT_ATTRS"] == ["첫째 혜택", "둘째 혜택"]
    assert "- 첫째 혜택" in section["content"]
    assert "- 둘째 혜택" in section["content"]


def test_sale_status_absent_from_source_is_none_in_metadata(tmp_path):
    payload = {"cardTitle": "테스트카드", "htmlList": {"noticeUrl": "<h3>유의사항</h3><p>본문.</p>"}}
    mapper = write_mapper(tmp_path, SALE_STATUS_CONFIG)
    fields_list = mapper.build_fields(payload, "product_hpp")
    result = mapper.to_parse_format(fields_list, "product_hpp")

    assert result["elements"]
    for element in result["elements"]:
        assert element["metadata"]["SALE_STATUS"] is None


@pytest.mark.parametrize(
    ("source_value", "expected"),
    [(None, "ON_SALE"), ("", "ON_SALE"), ("STOPPED", "STOPPED")],
)
def test_semantic_defaults_only_fill_missing_or_empty_source(
    tmp_path, source_value, expected,
):
    payload = {
        "cardTitle": "테스트카드",
        "htmlList": {"noticeUrl": "<h3>유의사항</h3><p>본문.</p>"},
    }
    if source_value is not None:
        payload["saleStatus"] = source_value
    mapper = write_mapper(tmp_path, SALE_STATUS_DEFAULT_CONFIG)
    result = mapper.to_parse_format(mapper.build_fields(payload, "product_hpp"), "product_hpp")

    assert result["elements"]
    assert {element["metadata"]["SALE_STATUS"] for element in result["elements"]} == {expected}


def test_semantic_constants_override_source_and_defaults(tmp_path):
    payload = {
        "cardTitle": "테스트카드",
        "saleStatus": "STOPPED",
        "htmlList": {"noticeUrl": "<h3>유의사항</h3><p>본문.</p>"},
    }
    mapper = write_mapper(tmp_path, SALE_STATUS_CONSTANT_CONFIG)
    result = mapper.to_parse_format(mapper.build_fields(payload, "product_hpp"), "product_hpp")

    assert {element["metadata"]["SALE_STATUS"] for element in result["elements"]} == {"FIXED"}


def test_product_attrs_and_sale_status_absent_from_chunk_prefix(tmp_path):
    """청크 접두(본문 첫 줄들)에는 상품명·상품코드만 실리고, 라벨이 없는 PRODUCT_ATTRS/
    SALE_STATUS 는 metadata 에만 남는다(규칙 10)."""
    config = """
shared_fields:
  PRODUCT_NM:    [cardTitle]
  PRODUCT_ATTRS: [benefit]
  SALE_STATUS:   [saleStatus]
sections:
  htmlList: { name: 상품 문서, include: true }
"""
    payload = {
        "cardTitle": "테스트카드", "saleStatus": "판매중",
        "benefit": ["첫째 혜택"],
        "htmlList": {"noticeUrl": "<h3>유의사항</h3><p>본문.</p>"},
    }
    mapper = write_mapper(tmp_path, config)
    fields_list = mapper.build_fields(payload, "product_hpp")
    section = _by_title(fields_list, "유의사항")
    prefix = mapper._chunk_prefix(section)

    assert "첫째 혜택" not in prefix
    assert "판매중" not in prefix
    assert "테스트카드" in prefix


# ── input_fields 에 JSON key 이름(섹션) 지정 ─────────────────────────────────
#
# 실데이터(sample_files/monimo/monimo_product_hpp_wcms_sample.json)로 검증한다 — 연회비 표는
# 루트가 아니라 `htmlList.feeUrl` 안에 HTML 로 들어 있어서, shared_fields(루트 스칼라 전용)로는
# 애초에 잡히지 않는다. "총연회비를 LLM 으로 뽑고 싶다"가 막혔던 실제 사례가 이 구조다.

REAL_SAMPLE = (
    Path(__file__).resolve().parents[2]
    / "sample_files" / "monimo" / "monimo_product_hpp_wcms_sample.json"
)


def real_sample_mapper(tmp_path):
    payload = json.loads(REAL_SAMPLE.read_text(encoding="utf-8"))
    return write_mapper(tmp_path), payload


def test_input_fields_accepts_json_key_and_narrows_to_that_section(tmp_path):
    """`feeUrl` 한 이름으로 연회비 섹션 본문만 뽑아 온다 — 문서 전체보다 짧고, 값은 살아 있다."""
    mapper, payload = real_sample_mapper(tmp_path)
    fields_list = mapper.build_fields(payload, "product_hpp")
    merged = mapper.document_input_fields(fields_list, ["feeUrl"])

    assert "총 연회비" in merged["feeUrl"]
    assert "20,000" in merged["feeUrl"] and "18,000" in merged["feeUrl"]
    # 좁혀 넣는 것이 목적이므로 문서 전체보다 확실히 작아야 한다.
    assert len(merged["feeUrl"]) < len(merged["PRODUCT_INFO"]) / 2
    # 다른 섹션(혜택·유의사항)은 섞이지 않는다.
    assert "빅포인트" not in merged["feeUrl"]


def test_input_fields_json_key_carries_chunk_plaintext_not_raw_html(tmp_path):
    """LLM 에 들어가는 것은 원문 HTML 이 아니라 청크에 실제로 실리는 평문이다."""
    mapper, payload = real_sample_mapper(tmp_path)
    fields_list = mapper.build_fields(payload, "product_hpp")
    text = mapper.document_input_fields(fields_list, ["feeUrl"])["feeUrl"]

    assert 'class="hide"' not in text and "<span" not in text
    # 섹션 접두(제목 + 상품명/상품코드)가 함께 들어가 어느 카드 이야기인지 알 수 있다.
    assert "[상품 문서] 연회비" in text
    assert "상품명: 새마을금고 삼성카드 7" in text


def test_input_fields_container_key_covers_all_children(tmp_path):
    """컨테이너 이름(`htmlList`)을 적으면 그 아래 섹션이 모두 들어간다."""
    mapper, payload = real_sample_mapper(tmp_path)
    fields_list = mapper.build_fields(payload, "product_hpp")
    merged = mapper.document_input_fields(fields_list, ["htmlList", "feeUrl"])

    assert "총 연회비" in merged["htmlList"]
    assert "이용 유의사항" in merged["htmlList"]
    assert len(merged["htmlList"]) > len(merged["feeUrl"])


def test_input_fields_unknown_name_warns_with_available_names(tmp_path, caplog):
    """못 찾은 이름은 조용히 빠지지 않는다 — 빈 raw_text 로 LLM 이 호출되던 실패 모드의 회귀 방지.

    캡처된 실제 설정이 쓴 `FEE`(shared_fields 로 `feeUrl` 을 잡으려던 시도)가 이 경우다.
    """
    mapper, payload = real_sample_mapper(tmp_path)
    fields_list = mapper.build_fields(payload, "product_hpp")
    with caplog.at_level(logging.WARNING):
        merged = mapper.document_input_fields(fields_list, ["FEE"])

    assert "FEE" not in merged
    message = "\n".join(r.message for r in caplog.records)
    assert "FEE" in message and "feeUrl" in message and "PRODUCT_INFO" in message


def test_input_fields_shared_field_name_still_wins(tmp_path):
    """기존 동작 보존 — 공통 필드명은 예전처럼 그 값 그대로다(섹션 검색으로 넘어가지 않는다)."""
    mapper, payload = real_sample_mapper(tmp_path)
    fields_list = mapper.build_fields(payload, "product_hpp")
    merged = mapper.document_input_fields(fields_list, ["PRODUCT_NM", "PRODUCT_INFO"])

    assert merged["PRODUCT_NM"] == "새마을금고 삼성카드 7"


def test_input_fields_array_index_segments_match_by_key_name(tmp_path):
    """`$.bubble[0]` 같은 배열 원소 섹션도 `bubble` 한 이름으로 잡힌다(경로 문법 불필요)."""
    mapper, payload = real_sample_mapper(tmp_path)
    fields_list = mapper.build_fields(payload, "product_hpp")
    merged = mapper.document_input_fields(fields_list, ["bubble"])

    assert "빅포인트" in merged["bubble"]
    assert "총 연회비" not in merged["bubble"]
