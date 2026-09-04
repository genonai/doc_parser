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
  bubble:   혜택 상세
  ksp:      주요 혜택 요약
  htmlList: 상품 문서
ignore_keys:
  - mpo
  - "*Img*"
constants:
  GROUP_C: "HPP"
text_fields: [PRODUCT_C, PRODUCT_NM]
field_labels:
  PRODUCT_C:  상품코드
  PRODUCT_NM: 상품명
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


# ── ignore_keys 정확 이름으로 서브트리 전체 제외 ─────────────────────────────

_OTHER_TARGET_PAYLOAD = {
    "wcmsId": "W1", "code": "C1", "cardTitle": "테스트카드",
    "mpo": [{"code": "X1", "name": "다른 카드", "ksp": [{"title": "다른 카드만의 혜택 문구"}]}],
}


def test_ignore_keys_exact_name_excludes_whole_subtree(tmp_path):
    """와일드카드 없는 정확 이름도 fnmatch 로 걸린다 — "이 대상이 아닌 내용" 제외 경로다."""
    mapper = write_mapper(tmp_path)
    full_text = "\n".join(_texts(mapper, mapper.build_fields(_OTHER_TARGET_PAYLOAD, "product_hpp")))

    assert "다른 카드만의 혜택 문구" not in full_text
    assert "다른 카드" not in full_text


def test_legacy_include_false_still_excludes_the_same_subtree(tmp_path):
    """옛 표기 `include: false` 는 `ignore_keys` 항목과 같은 결과여야 한다(하위호환).

    제외 수단을 `ignore_keys` 하나로 합쳤으므로, 옛 설정이 조용히 무효가 되면
    다른 대상의 내용이 이 대상의 청크로 새어 나간다.
    """
    legacy = """
shared_fields:
  PRODUCT_NM: [cardTitle]
sections:
  mpo: { name: 추천 상품, include: false }
"""
    mapper = write_mapper(tmp_path, legacy)
    full_text = "\n".join(_texts(mapper, mapper.build_fields(_OTHER_TARGET_PAYLOAD, "product_hpp")))

    assert "mpo" in mapper.ignore_keys
    assert "mpo" not in mapper.sections_cfg
    assert "다른 카드만의 혜택 문구" not in full_text
    assert "추천 상품" not in full_text


def test_legacy_object_form_keeps_the_display_name(tmp_path):
    """`{name: X, include: true}` 는 문자열 `X` 와 같게 해석된다."""
    legacy = """
shared_fields:
  PRODUCT_NM: [cardTitle]
sections:
  htmlList: { name: 상품 문서, include: true }
"""
    mapper = write_mapper(tmp_path, legacy)

    assert mapper.sections_cfg == {"htmlList": "상품 문서"}


def test_sections_is_optional(tmp_path):
    """이름을 안 붙이고 제외만 하는 설정도 정상이다 — 예전에는 기동이 실패했다."""
    config = """
shared_fields:
  PRODUCT_NM: [cardTitle]
ignore_keys:
  - mpo
"""
    payload = {**_OTHER_TARGET_PAYLOAD,
               "htmlList": {"noticeUrl": "<h3>이용 유의사항</h3><p>유의사항 본문.</p>"}}
    mapper = write_mapper(tmp_path, config)
    fields_list = mapper.build_fields(payload, "product_hpp")
    full_text = "\n".join(_texts(mapper, fields_list))

    assert mapper.sections_cfg == {}
    assert "유의사항 본문." in full_text
    # 이름을 안 붙였으므로 SECTION_NM 은 자동(컨테이너 key 이름)이 된다.
    assert _by_title(fields_list, "이용 유의사항")["SECTION_NM"] == "htmlList"
    assert "다른 카드만의 혜택 문구" not in full_text


def test_section_name_without_a_label_falls_back_to_the_key(tmp_path):
    """`sections` 에 값을 빠뜨려도(`key:` 만) key 이름을 표시 이름으로 쓴다."""
    config = """
shared_fields:
  PRODUCT_NM: [cardTitle]
sections:
  htmlList:
"""
    mapper = write_mapper(tmp_path, config)

    assert mapper.sections_cfg == {"htmlList": "htmlList"}


@pytest.mark.parametrize("value", ["[상품 문서]", "123", "true"])
def test_sections_rejects_a_non_string_display_name(tmp_path, value):
    """표시 이름 자리에 문자열이 아닌 값을 적으면 기동 시 잡는다.

    `include: true/false` 를 적던 습관 때문에 `htmlList: true` 같은 오기입이 나기 쉽고,
    통과시키면 이름이 `"True"` 인 섹션이 조용히 생긴다.
    """
    config = f"""
shared_fields:
  PRODUCT_NM: [cardTitle]
sections:
  htmlList: {value}
"""
    with pytest.raises(ValueError, match="sections.htmlList"):
        write_mapper(tmp_path, config)


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


def test_shared_field_outside_body_fields_is_metadata_only(tmp_path):
    """규칙 10 — `body.fields` 에 없는 BIZ_ID 는 본문에서 빠지고 metadata 에만 남는다."""
    mapper = write_mapper(tmp_path)

    assert mapper._in_chunk_body("PRODUCT_NM")
    assert not mapper._in_chunk_body("BIZ_ID")


def test_body_fields_is_the_only_switch_even_with_a_label(tmp_path):
    """라벨이 있어도 `body.fields` 에 없으면 본문에 나가지 않는다.

    예전에는 라벨이 표시 이름과 포함 스위치를 겸해서, 이름을 붙이는 순간 그 값이
    metadata 전용에서 임베딩 대상으로 조용히 올라왔다.
    """
    config = """
shared_fields:
  PRODUCT_NM: [cardTitle]
  BIZ_ID:     [wcmsId]
sections:
  htmlList: 상품 문서
text_fields: [PRODUCT_NM]
field_labels:
  PRODUCT_NM: 상품명
  BIZ_ID:     사업자
"""
    mapper = write_mapper(tmp_path, config)
    payload = {"wcmsId": "W1", "cardTitle": "테스트카드",
               "htmlList": {"noticeUrl": "<h3>유의사항</h3><p>본문.</p>"}}
    prefix = mapper._chunk_prefix(_by_title(mapper.build_fields(payload, "product_hpp"), "유의사항"))

    assert "상품명: 테스트카드" in prefix
    assert "사업자" not in prefix and "W1" not in prefix


def test_empty_body_fields_drops_every_shared_field_from_prefix(tmp_path):
    """`body.fields: []` 는 "접두에 공통 필드를 싣지 않는다" 는 명시적 선언이다."""
    config = """
shared_fields:
  PRODUCT_NM: [cardTitle]
sections:
  htmlList: 상품 문서
text_fields: []
field_labels:
  PRODUCT_NM: 상품명
"""
    mapper = write_mapper(tmp_path, config)
    payload = {"cardTitle": "테스트카드",
               "htmlList": {"noticeUrl": "<h3>유의사항</h3><p>본문.</p>"}}
    fields = _by_title(mapper.build_fields(payload, "product_hpp"), "유의사항")

    assert "테스트카드" not in mapper._chunk_prefix(fields)
    assert fields["PRODUCT_NM"] == "테스트카드"  # metadata 에는 남는다


def test_without_body_fields_labels_still_decide_inclusion(tmp_path):
    """`body.fields` 선언이 없는 옛 설정은 종전대로 라벨이 포함 여부를 정한다(하위호환)."""
    config = """
shared_fields:
  PRODUCT_NM: [cardTitle]
  BIZ_ID:     [wcmsId]
sections:
  htmlList: 상품 문서
field_labels:
  PRODUCT_NM: 상품명
"""
    mapper = write_mapper(tmp_path, config)
    payload = {"wcmsId": "W1", "cardTitle": "테스트카드",
               "htmlList": {"noticeUrl": "<h3>유의사항</h3><p>본문.</p>"}}
    prefix = mapper._chunk_prefix(_by_title(mapper.build_fields(payload, "product_hpp"), "유의사항"))

    assert "상품명: 테스트카드" in prefix
    assert "W1" not in prefix


def test_no_field_is_in_the_body_by_default(tmp_path):
    """매퍼가 특정 사이트의 필드명을 기본 라벨로 들고 있지 않다.

    예전에는 `PRODUCT_NM`/`PRODUCT_C` 가 상수로 박혀 있어, 설정에 아무 것도 안 적어도
    그 이름을 쓰는 사이트에서는 접두 2줄이 생겼다.
    """
    config = """
shared_fields:
  PRODUCT_NM: [cardTitle]
  PRODUCT_C:  [code]
sections:
  htmlList: 상품 문서
"""
    mapper = write_mapper(tmp_path, config)
    payload = {"code": "C1", "cardTitle": "테스트카드",
               "htmlList": {"noticeUrl": "<h3>유의사항</h3><p>본문.</p>"}}
    prefix = mapper._chunk_prefix(_by_title(mapper.build_fields(payload, "product_hpp"), "유의사항"))

    assert mapper.field_labels == {}
    assert "테스트카드" not in prefix and "C1" not in prefix


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


# ── shared_fields 누락 시 기동 실패 ──────────────────────────────────────────

def test_shared_fields_is_required(tmp_path):
    config = "sections:\n  bubble: 혜택\n"
    with pytest.raises(ValueError, match="shared_fields"):
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
  benefit: 주요 혜택
"""

SALE_STATUS_CONFIG = """
shared_fields:
  PRODUCT_NM:  [cardTitle]
  SALE_STATUS: [saleStatus]
sections:
  htmlList: 상품 문서
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
    """청크 접두(본문 첫 줄들)에는 `body.fields` 에 적은 상품명만 실리고, 적지 않은
    PRODUCT_ATTRS/SALE_STATUS 는 metadata 에만 남는다(규칙 10)."""
    config = """
shared_fields:
  PRODUCT_NM:    [cardTitle]
  PRODUCT_ATTRS: [benefit]
  SALE_STATUS:   [saleStatus]
sections:
  htmlList: 상품 문서
text_fields: [PRODUCT_NM]
field_labels:
  PRODUCT_NM: 상품명
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


# ── 첫 청크에만 1회 싣는 필드(first_chunk_fields) ───────────────────────────

@pytest.mark.unit
def test_first_chunk_fields_appear_once(tmp_path):
    """문서 1건에 하나뿐인 값(연회비 등)을 섹션마다 반복하지 않는다.

    shared_fields 로 넣으면 모든 청크 접두에 붙어 chunk_size 를 깎고 같은 문장이 섹션 수만큼
    임베딩에 들어간다. 값 자체는 모든 청크 metadata 에 실리므로 필터 검색은 전 청크에서 된다.
    """
    import textwrap

    cfg = tmp_path / "custom_field_s.yaml"
    cfg.write_text(textwrap.dedent("""
        shared_fields:
          PRODUCT_NM: [prodNm]
        sections:
          ksp: 주요 혜택
        first_chunk_fields: [ANNUAL_FEE]
        field_labels:
          ANNUAL_FEE: 연회비
    """), encoding="utf-8")
    mapper = SemanticJsonMapper(
        config_file=cfg.name, resource_path=str(tmp_path),
        doc_type="p", extractor="json_semantic",
    )
    rows = mapper.build_fields(
        {"prodNm": "카드", "ksp": {"a": "혜택 하나"}, "etc": {"b": "다른 절"}}, "p"
    )
    fee = "국내전용 18,000원"
    for row in rows:
        row["ANNUAL_FEE"] = fee

    elements = mapper.to_parse_format(rows, "p")["elements"]
    assert len(elements) >= 2, "여러 섹션이 나와야 의미 있는 검사다"
    hits = [i for i, e in enumerate(elements) if fee in (e.get("content") or "")]
    assert hits == [0], f"첫 청크에만 1회여야 한다: {hits}"
    assert all((e.get("metadata") or {}).get("ANNUAL_FEE") == fee for e in elements)
    assert "연회비: " + fee in elements[0]["content"]


@pytest.mark.unit
def test_first_chunk_field_keeps_prefix_contract(tmp_path):
    """청커는 `content.startswith(chunk_prefix)` 로 접두를 재부착한다 — 앞에 끼우면 깨진다."""
    import textwrap

    cfg = tmp_path / "custom_field_s.yaml"
    cfg.write_text(textwrap.dedent("""
        shared_fields:
          PRODUCT_NM: [prodNm]
        sections:
          ksp: 주요 혜택
        first_chunk_fields: [ANNUAL_FEE]
    """), encoding="utf-8")
    mapper = SemanticJsonMapper(
        config_file=cfg.name, resource_path=str(tmp_path),
        doc_type="p", extractor="json_semantic",
    )
    rows = mapper.build_fields({"prodNm": "카드", "ksp": {"a": "혜택"}}, "p")
    for row in rows:
        row["ANNUAL_FEE"] = "18,000원"
    first = mapper.to_parse_format(rows, "p")["elements"][0]
    assert first["content"].startswith(first.get("chunk_prefix") or "")


@pytest.mark.unit
def test_first_chunk_field_declared_as_shared_is_not_repeated_in_prefix(tmp_path):
    """`first_chunk_fields` 에 적은 필드는 원천에서 오는 공통 필드여도 접두에 반복하지 않는다.

    접두는 모든 청크에 붙으므로, 라벨이 있는 shared_fields 를 그대로 내보내면 "첫 청크에만
    1회" 라는 약속이 두 가지로 깨졌다 — 값이 섹션 수만큼 반복되고, 첫 청크에서는 접두 줄과
    1회 줄이 겹쳐 같은 문장이 두 번 나왔다.
    """
    config = """
shared_fields:
  PRODUCT_NM: [prodNm]
  ANNUAL_FEE: [fee]
sections:
  ksp: 주요 혜택
first_chunk_fields: [ANNUAL_FEE]
field_labels:
  ANNUAL_FEE: 연회비
"""
    mapper = write_mapper(tmp_path, config)
    payload = {"prodNm": "카드", "fee": "국내전용 18,000원",
               "ksp": {"a": "혜택 하나"}, "etc": {"b": "다른 절"}}
    elements = mapper.to_parse_format(mapper.build_fields(payload, "product_hpp"), "product_hpp")["elements"]

    assert len(elements) >= 2, "여러 섹션이 나와야 의미 있는 검사다"
    marker = "연회비: 국내전용 18,000원"
    assert elements[0]["content"].count(marker) == 1
    assert not any(marker in e["content"] for e in elements[1:])
    # 값 자체는 전 청크 metadata 에 남는다(필터 검색용).
    assert all(e["metadata"]["ANNUAL_FEE"] == "국내전용 18,000원" for e in elements)
    # 접두 재부착 계약도 유지된다.
    assert all(e["content"].startswith(e["chunk_prefix"]) for e in elements)


@pytest.mark.unit
def test_const_only_field_can_be_put_in_body_with_a_label(tmp_path):
    """`const`/`default` 로만 만드는 공통 필드도 라벨을 붙이면 본문에 실려야 한다.

    예전에는 접두가 `shared_fields`(별칭으로 원천에서 찾는 필드)만 훑어서, 원천 key 가 없는
    `GROUP_C: {const: HPP}` 같은 필드는 라벨을 붙여도 metadata 에만 남았다.
    """
    config = """
shared_fields:
  PRODUCT_NM: [prodNm]
sections:
  ksp: 주요 혜택
constants:
  GROUP_C: HPP
defaults:
  SALE_STATUS: ON_SALE
field_labels:
  GROUP_C: 그룹코드
"""
    mapper = write_mapper(tmp_path, config)
    fields_list = mapper.build_fields({"prodNm": "카드", "ksp": {"a": "혜택 하나"}}, "product_hpp")
    prefix = mapper._chunk_prefix(fields_list[0])

    assert "그룹코드: HPP" in prefix
    # 라벨이 없는 defaults 필드는 종전대로 metadata 에만 남는다(규칙 10).
    assert "ON_SALE" not in prefix
    assert fields_list[0]["SALE_STATUS"] == "ON_SALE"


# ── 공통 필드 값 파이프라인(values / transform / template) ────────────────────
#
# 실제 원천 파일로 검증한다 — 값의 형태(코드값·단위 붙은 금액·브랜드 분리)를 흉내낸
# sample_files/monimo/monimo_product_hpp_fields_sample.json 을 쓴다. 생성 스크립트는
# examples/parse_chunk/make_product_hpp_fields_sample.py 다.

FIELDS_SAMPLE = (
    Path(__file__).resolve().parents[2]
    / "sample_files" / "monimo" / "monimo_product_hpp_fields_sample.json"
)

PIPELINE_CONFIG = """
shared_fields:
  PRODUCT_C:   [productCode]
  PRODUCT_NM:  [cardTitle]
  BRAND_NM:    [brandName]
  SALE_STATUS: [saleStatus]
  ANNUAL_FEE:  [feeAmount]
sections:
  htmlList: 상품 문서
ignore_keys:
  - mpo
  - "*Img*"
  - fontColor
value_map:
  SALE_STATUS:
    ON_SALE:  ["1", "Y", 판매중]
    OFF_SALE: ["0", "N", 판매중지]
transforms:
  ANNUAL_FEE:
    - {name: regex_sub, pattern: "[^0-9]", repl: ""}
    - {name: to_int}
derive:
  DISPLAY_NM: "{{BRAND_NM}} {{PRODUCT_NM}}"
"""


def pipeline_fields(tmp_path, config_text=PIPELINE_CONFIG):
    """실 샘플 파일을 새 파이프라인 설정으로 태워 공통 필드를 돌려준다."""
    mapper = write_mapper(tmp_path, config_text)
    payload = json.loads(FIELDS_SAMPLE.read_text(encoding="utf-8"))
    return mapper, mapper.build_fields(payload, "product_hpp")[0]


def test_value_map_folds_a_code_value(tmp_path):
    """`values` — 원천의 코드값(`"1"`)이 적재 표준값으로 접힌다."""
    _mapper, fields = pipeline_fields(tmp_path)

    assert fields["SALE_STATUS"] == "ON_SALE"


def test_transform_chain_runs_in_declared_order(tmp_path):
    """`transform` — 단위를 떼고 정수로. 체인은 적은 순서대로 돈다."""
    _mapper, fields = pipeline_fields(tmp_path)

    assert fields["ANNUAL_FEE"] == 18000


def test_template_joins_other_fields(tmp_path):
    """`template` — 브랜드와 상품명을 합쳐 표시용 이름을 만든다."""
    _mapper, fields = pipeline_fields(tmp_path)

    assert fields["DISPLAY_NM"] == "다올신협 다올신협 체크카드"


def test_pipeline_order_matches_rows_and_records(tmp_path):
    """적용 순서 `원천값 -> default -> const -> values -> transform -> template` 고정.

    다른 kind 와 순서가 어긋나는 것이 이 기능에서 가장 나기 쉬운 결함이다. 순서가
    드러나도록 한 필드에 네 단계를 겹쳐 태운다.
      · SALE_STATUS  : 원천에 값이 있으므로 default 가 무시되고 원천값이 values 로 접힌다
      · GRADE        : 원천에 없어 default 가 채워지고 그 값이 values 로 접힌다
      · GROUP_C      : const 가 원천값을 덮고, 덮은 값이 values 로 접힌다
      · DISPLAY_GRADE: template 이 values/transform 를 지난 값을 읽는다(파이프라인 마지막)
    """
    config = """
shared_fields:
  PRODUCT_NM:  [cardTitle]
  SALE_STATUS: [saleStatus]
  GRADE:       [gradeCode]
  GROUP_C:     [brandName]
defaults:
  SALE_STATUS: "0"
  GRADE: "2"
constants:
  GROUP_C: "1"
sections:
  htmlList: 상품 문서
value_map:
  SALE_STATUS: {ON_SALE: ["1"], OFF_SALE: ["0"]}
  GRADE:       {GOLD: ["1"], SILVER: ["2"]}
  GROUP_C:     {HPP: ["1"]}
derive:
  DISPLAY_GRADE: "{{GROUP_C}}/{{GRADE}}"
"""
    _mapper, fields = pipeline_fields(tmp_path, config)

    # 원천값이 default 를 이기고, 그 뒤 values 가 돈다.
    assert fields["SALE_STATUS"] == "ON_SALE"
    # 원천에 없으면 default("2") 가 채워지고, 채워진 값도 values 를 지난다.
    assert fields["GRADE"] == "SILVER"
    # const 가 원천값(brandName="다올신협")을 덮고, 덮은 값이 values 를 지난다.
    assert fields["GROUP_C"] == "HPP"
    # template 은 파이프라인 **뒤**라 접힌 값을 읽는다(원본 코드값이 아니다).
    assert fields["DISPLAY_GRADE"] == "HPP/SILVER"


def test_pipeline_runs_before_required_check(tmp_path):
    """`require` 는 파이프라인 뒤에 본다 — const/template 으로 채운 필드가 통과해야 한다."""
    config = """
shared_fields:
  PRODUCT_NM: [cardTitle]
  BRAND_NM:   [brandName]
sections:
  htmlList: 상품 문서
derive:
  DISPLAY_NM: "{{BRAND_NM}} {{PRODUCT_NM}}"
required_shared_fields: [DISPLAY_NM]
"""
    _mapper, fields = pipeline_fields(tmp_path, config)

    assert fields["DISPLAY_NM"] == "다올신협 다올신협 체크카드"


def test_derive_target_can_be_carried_in_the_chunk_body(tmp_path):
    """`template` 로만 만드는 필드도 `body.fields` 에 적으면 접두에 실린다.

    접두는 `shared_fields`+`defaults`+`constants`+`derive` 를 훑는다 — derive 를 빼면
    파생 필드는 body.fields 에 적어도 metadata 에만 남았다.
    """
    config = PIPELINE_CONFIG + """
text_fields: [DISPLAY_NM]
field_labels:
  DISPLAY_NM: 상품명
"""
    mapper, fields = pipeline_fields(tmp_path, config)
    prefix = mapper._chunk_prefix(fields)

    assert "상품명: 다올신협 다올신협 체크카드" in prefix


def test_bad_transform_name_fails_at_startup(tmp_path):
    """잘못된 변환기 이름은 요청 때가 아니라 기동 시에 잡는다(rows/records 와 동일)."""
    config = """
shared_fields:
  PRODUCT_NM: [cardTitle]
sections:
  htmlList: 상품 문서
transforms:
  PRODUCT_NM:
    - {name: no_such_transform}
"""
    with pytest.raises(ValueError, match="no_such_transform"):
        write_mapper(tmp_path, config)


def test_derive_referencing_an_unknown_field_fails_at_startup(tmp_path):
    """`template` 이 아무도 만들지 않는 필드를 참조하면 기동 시에 잡는다."""
    config = """
shared_fields:
  PRODUCT_NM: [cardTitle]
sections:
  htmlList: 상품 문서
derive:
  DISPLAY_NM: "{{NO_SUCH_FIELD}} {{PRODUCT_NM}}"
"""
    with pytest.raises(ValueError, match="NO_SUCH_FIELD"):
        write_mapper(tmp_path, config)
