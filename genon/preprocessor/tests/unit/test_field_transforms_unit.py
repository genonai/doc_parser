"""facade/enrichment/field_transforms.py 에 대한 unit test.

순수 stdlib 모듈이라 docling/fastapi 없이 로컬에서도 실제 실행된다.
"""

from unittest.mock import MagicMock

import pytest

from facade.enrichment.field_transforms import (
    DEFAULT_METADATA_FIELD_TRANSFORMS,
    JSON_VALUE_SENTINEL,
    apply_field_transforms,
    extract_metadata_from_document,
    normalize_metadata_value,
    parse_created_date,
    serialize_metadata_value_for_output,
    store_metadata_in_document,
)


def _make_document(pairs):
    """key/value 쌍 리스트로 docling KeyValueItem 구조를 흉내내는 stub 문서 생성."""
    cells = []
    for key, value in pairs:
        cells.append(MagicMock(label=MagicMock(value="key"), text=key))
        cells.append(MagicMock(label=MagicMock(value="value"), text=value))
    kv_item = MagicMock(graph=MagicMock(cells=cells))
    return MagicMock(key_value_items=[kv_item])


# ── parse_created_date ───────────────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.parametrize("text,expected", [
    ("2025-03-14", 20250314),
    ("2025.03.14", 20250314),
    ("2025/3/4", 20250304),
    ("2024년 5월 7일", 20240507),
    ("2024-05", 20240501),       # 일자 누락 → 01
    ("2024년 5월", 20240501),
    ("2023", 20230101),          # 연도만 → 0101
    ("보고자료 2025-03-14 작성", 20250314),  # 문장 내부 포함
    ("", 0),
    ("None", 0),
    ("날짜없음", 0),
    ("2025-13-40", 20250101),    # 월/일이 잘못되면 연도 fallback → YYYY0101
])
def test_parse_created_date(text, expected):
    assert parse_created_date(text) == expected


# ── apply_field_transforms ───────────────────────────────────────────────────

@pytest.mark.unit
def test_apply_field_transforms_backward_compat_and_rename():
    """기본 transform 은 created_date 동작을 보존하고, source 키 변경 시에도 동작."""
    # 1) 하위 호환: created_date 문자열 → YYYYMMDD int
    typed, consumed = apply_field_transforms(
        DEFAULT_METADATA_FIELD_TRANSFORMS,
        {"created_date": "2025-03-14", "authors": [{"name": "a"}]}, document=None)
    assert typed == {"created_date": 20250314}
    assert consumed == {"created_date", "작성일"}

    # 2) 작성일 후보 fallthrough
    typed, _ = apply_field_transforms(
        DEFAULT_METADATA_FIELD_TRANSFORMS, {"작성일": "2024년 5월"}, document=None)
    assert typed == {"created_date": 20240501}

    # 3) 정수 입력은 손실 없이 보존
    typed, _ = apply_field_transforms(
        DEFAULT_METADATA_FIELD_TRANSFORMS, {"created_date": 20251231}, document=None)
    assert typed == {"created_date": 20251231}

    # 4) 키 이름 변경: 설정의 source 만 바꿔도 created_date 가 채워짐
    typed, consumed = apply_field_transforms(
        [{"source": ["doc_date"], "target": "created_date", "type": "date_int"}],
        {"doc_date": "2023.07.01", "department": "IT"}, document=None)
    assert typed == {"created_date": 20230701}
    assert "department" not in consumed  # 변환 비대상 필드는 passthrough 로 유지


@pytest.mark.unit
def test_apply_field_transforms_doc_text_scan_fallback():
    """추출값이 비어있고 fallback=doc_text_scan 이면 본문 휴리스틱으로 보강."""
    document = MagicMock()
    document.export_to_text.return_value = "보고자료 2024-01-15 기준 작성"

    typed, _ = apply_field_transforms(
        DEFAULT_METADATA_FIELD_TRANSFORMS, {}, document=document)
    assert typed == {"created_date": 20240115}


@pytest.mark.unit
def test_apply_field_transforms_no_transform_type_passthrough():
    """type 미지정이면 원본 값을 그대로 target 에 매핑한다."""
    typed, consumed = apply_field_transforms(
        [{"source": "title_raw", "target": "title"}],
        {"title_raw": "보고서"}, document=None)
    assert typed == {"title": "보고서"}
    assert consumed == {"title_raw", "title"}


@pytest.mark.unit
def test_apply_field_transforms_empty_config():
    """빈 설정이면 변환 없음(빈 dict/set 반환)."""
    typed, consumed = apply_field_transforms([], {"created_date": "2025-01-01"}, document=None)
    assert typed == {}
    assert consumed == set()


# ── 메타데이터 추출/직렬화 유틸 ─────────────────────────────────────────────────

@pytest.mark.unit
def test_normalize_metadata_value():
    assert normalize_metadata_value('[{"name": "홍길동"}]') == [{"name": "홍길동"}]
    assert normalize_metadata_value('{"a": 1}') == {"a": 1}
    assert normalize_metadata_value("  2025-03-14  ") == "2025-03-14"
    assert normalize_metadata_value("") == ""
    assert normalize_metadata_value("[broken") == "[broken"   # 파싱 실패 → 원본 문자열
    assert normalize_metadata_value(123) == 123               # 비문자열은 그대로


@pytest.mark.unit
def test_serialize_metadata_value_for_output():
    assert serialize_metadata_value_for_output({"a": 1}) == '{"a": 1}'
    assert serialize_metadata_value_for_output([1, 2]) == "[1, 2]"
    assert serialize_metadata_value_for_output("text") == "text"
    assert serialize_metadata_value_for_output(20250314) == 20250314


@pytest.mark.unit
def test_extract_metadata_from_document():
    """KeyValueItem 의 key/value 쌍을 일반적으로 추출하고 값은 normalize 한다."""
    doc = _make_document([
        ("created_date", "2025-03-14"),
        ("authors", '[{"name": "홍길동"}]'),
        ("department", "채권시장팀"),
    ])
    md = extract_metadata_from_document(doc)
    assert md == {
        "created_date": "2025-03-14",
        "authors": [{"name": "홍길동"}],
        "department": "채권시장팀",
    }


@pytest.mark.unit
def test_extract_metadata_preserves_typed_json_sentinel():
    doc = _make_document([
        ("source_pages", JSON_VALUE_SENTINEL + "9"),
        ("searchable", JSON_VALUE_SENTINEL + "true"),
        ("tags", JSON_VALUE_SENTINEL + '["보험", "교통"]'),
        # 표식 없는 숫자 문자열은 하위 호환상 문자열로 유지한다.
        ("legacy_code", "00123"),
    ])
    assert extract_metadata_from_document(doc) == {
        "source_pages": 9,
        "searchable": True,
        "tags": ["보험", "교통"],
        "legacy_code": "00123",
    }


@pytest.mark.unit
def test_extract_metadata_from_document_empty():
    assert extract_metadata_from_document(MagicMock(key_value_items=[])) == {}


def _stored_pairs(metadata, **kwargs):
    """store_metadata_in_document 이 실제로 기록한 (key, value_text) 쌍을 뽑아낸다."""
    pytest.importorskip("docling_core")
    document = MagicMock()
    store_metadata_in_document(document, metadata, **kwargs)
    if not document.add_key_values.call_args_list:
        return []
    cells = document.add_key_values.call_args.kwargs["graph"].cells
    return [(cells[i].text, cells[i + 1].text) for i in range(0, len(cells), 2)]


@pytest.mark.unit
def test_store_metadata_types_only_typed_keys():
    """typed_keys 에 든 키만 JSON 표식으로 저장되고, 나머지는 종전 규칙을 유지한다.

    #360: 표식을 전 필드에 적용하면 md front matter 와 무관한 기존 doc_type 의 청크 property
    타입까지 바뀐다(card 의 annual_fee_amount 가 '18000' → 18000). 이미 text 로 만들어진
    벡터 컬렉션에 int 가 들어가면 적재가 깨지므로 opt-in 이어야 한다.
    """
    stored = dict(_stored_pairs(
        {
            "source_pages": 9,          # front matter 유래 → 타입 보존
            "annual_fee_amount": 18000,  # 기존 LLM 필드 → 문자열 유지
            "PRODUCT_ATTRS": {"a": 1},   # 기존 규칙: 표식 없는 JSON
            "issuer_name": "삼성카드",
        },
        typed_keys={"source_pages"},
    ))
    assert stored["source_pages"] == JSON_VALUE_SENTINEL + "9"
    assert stored["annual_fee_amount"] == "18000"
    assert stored["PRODUCT_ATTRS"] == '{"a": 1}'
    assert stored["issuer_name"] == "삼성카드"

    # 읽기측 왕복: 표식이 붙은 값만 원래 타입으로 복원된다.
    doc = _make_document(list(stored.items()))
    assert extract_metadata_from_document(doc) == {
        "source_pages": 9,
        "annual_fee_amount": "18000",
        "PRODUCT_ATTRS": {"a": 1},
        "issuer_name": "삼성카드",
    }


@pytest.mark.unit
def test_store_metadata_typed_keys_default_is_off():
    """typed_keys 미지정(기본)이면 표식이 전혀 붙지 않는다 — 기존 호출부 하위 호환."""
    stored = dict(_stored_pairs({"n": 9, "flag": True}))
    assert stored == {"n": "9", "flag": "True"}


@pytest.mark.unit
def test_extract_then_apply_pipeline():
    """convert/intelligent compose_vectors 와 동일한 흐름: 추출 → 변환 → passthrough 검증."""
    doc = _make_document([("created_date", "2025-03-14"), ("authors", '[{"name": "홍길동"}]')])
    merged = extract_metadata_from_document(doc)
    typed, consumed = apply_field_transforms(DEFAULT_METADATA_FIELD_TRANSFORMS, merged, doc)
    assert typed == {"created_date": 20250314}
    # created_date 는 consumed 라 passthrough 제외, authors 는 passthrough 로 직렬화되어 유지
    passthrough = {k: serialize_metadata_value_for_output(v) for k, v in merged.items() if k not in consumed}
    assert passthrough == {"authors": '[{"name": "홍길동"}]'}
