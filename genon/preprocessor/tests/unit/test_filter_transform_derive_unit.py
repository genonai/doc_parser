"""C2·C3 — 값 기반 필터, 인자 받는 변환기, 필드 결합.

지금까지 코드 수정 없이는 불가능하던 요건들이다. 새 요건이 올 때마다
field_transforms.py 에 함수를 추가하던 통로를 설정으로 옮긴 것이 이 세 기능의 목적이다.
"""
import json
import textwrap

import pytest

from genon.preprocessor.processing.enrichment.json_records import JsonRecordsMapper
from genon.preprocessor.processing.enrichment.json_semantic import SemanticJsonMapper
from genon.preprocessor.processing.enrichment.tabular_custom_fields import (
    TabularCustomFieldsMapper,
)

pytestmark = pytest.mark.unit


def _rows(tmp_path, body, payload, kind="tabular"):
    path = tmp_path / "custom_field_x.yaml"
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    if kind == "tabular":
        mapper = TabularCustomFieldsMapper(
            config_file=path.name, resource_path=str(tmp_path),
            doc_type="t", extractor="tabular_mapping",
        )
        data = {"data": [{"sheet_name": "S", "data_rows": payload}]}
    elif kind == "sections":
        mapper = SemanticJsonMapper(
            config_file=path.name, resource_path=str(tmp_path),
            doc_type="t", extractor="json_semantic",
        )
        data = payload
    else:
        mapper = JsonRecordsMapper(
            config_file=path.name, resource_path=str(tmp_path),
            doc_type="t", extractor="json_mapping",
        )
        data = payload
    return mapper.build_fields(data, "t")


_FILTER_CFG = """
    schema: v2
    source: {kind: rows}
    fields:
      TITLE:  {alias: [제목]}
      DEL_YN: {alias: [삭제여부]}
      STATUS: {alias: [상태]}
    filter:
      - {field: DEL_YN, not_in: [Y]}
      - {field: STATUS, in: [ACTIVE, PENDING]}
    body:
      fields: [TITLE]
"""


def test_filter_selects_records_by_value(tmp_path):
    """`required` 는 빈 값만 걸렀다 — "삭제여부 Y 인 행은 빼라"를 표현할 수 없었다."""
    rows = _rows(tmp_path, _FILTER_CFG, [
        {"제목": "살아있음", "삭제여부": "N", "상태": "ACTIVE"},
        {"제목": "삭제됨", "삭제여부": "Y", "상태": "ACTIVE"},
        {"제목": "상태밖", "삭제여부": "N", "상태": "CLOSED"},
        {"제목": "대기", "삭제여부": "n", "상태": "pending"},   # 대소문자 무시
    ])
    assert [r["TITLE"] for r in rows] == ["살아있음", "대기"]
    assert rows[0]["DEL_YN"] == "N"  # 옛 value_map 우회와 달리 원값이 파괴되지 않는다


def test_filter_is_not_fail_open(tmp_path):
    """열거하지 않은 값이 통과하던 옛 우회(value_map 빈 표준값)의 반대다."""
    assert _rows(tmp_path, _FILTER_CFG,
                 [{"제목": "신규", "삭제여부": "N", "상태": "NEW_UNKNOWN"}]) == []


def test_transform_takes_arguments_and_chains(tmp_path):
    """`"18,000원"` → 18000 처럼 두 단계가 필요한 요건이 흔하다."""
    rows = _rows(tmp_path, """
        schema: v2
        source: {kind: rows}
        fields:
          TITLE:   {alias: [제목]}
          FEE_AMT:
            alias: [연회비]
            transform:
              - {name: regex_sub, pattern: "[^0-9]", repl: ""}
              - {name: to_int}
          CODE:
            alias: [설명]
            transform: {name: regex_extract, pattern: "([A-Z]+-[A-Z]+-[0-9]+)"}
          SHORT:
            alias: [본문]
            transform: {name: truncate, length: 6, suffix: "…"}
        body:
          fields: [TITLE]
    """, [{"제목": "카드", "연회비": "18,000원",
           "설명": "문서 CS-HPP-0231 참고", "본문": "가나다라마바사아자차"}])
    assert rows[0]["FEE_AMT"] == 18000
    assert rows[0]["CODE"] == "CS-HPP-0231"
    assert rows[0]["SHORT"] == "가나다라마…"


def test_plain_transform_name_still_works(tmp_path):
    """기존 표기를 깨지 않는다 — 출고 설정 7개가 이 형태다."""
    rows = _rows(tmp_path, """
        schema: v2
        source: {kind: rows}
        fields:
          DT: {alias: [일자], transform: date_int_flex}
          T:  {alias: [제목]}
        body:
          fields: [T]
    """, [{"일자": "26.07.01", "제목": "x"}])
    assert rows[0]["DT"] == 20260701


def test_derive_combines_fields(tmp_path):
    """두 필드를 metadata 필드로 합치는 방법이 없었다(text_fields 는 청크 본문이다)."""
    rows = _rows(tmp_path, """
        schema: v2
        source: {kind: rows}
        fields:
          BRAND:      {alias: [브랜드]}
          PRODUCT_NM: {alias: [상품명]}
          MISSING:    {alias: [없음]}
          DISPLAY_NM: {template: "{{BRAND}} {{PRODUCT_NM}}"}
          WITH_HOLE:  {template: "{{BRAND}} {{MISSING}}"}
        body:
          fields: [DISPLAY_NM]
    """, [{"브랜드": "삼성카드", "상품명": "taptap O"}])
    assert rows[0]["DISPLAY_NM"] == "삼성카드 taptap O"
    assert rows[0]["WITH_HOLE"] == "삼성카드"   # 빈 자리는 지우고 양끝을 다듬는다


def test_derive_field_is_usable_downstream(tmp_path):
    """derive 산출이 "생성 가능 필드"로 등록돼야 한다.

    아니면 text_fields 에 쓸 때 오탐 경고가 나고, chunk_prefix_fields/filter 에 쓰면
    기동이 실패한다.
    """
    rows = _rows(tmp_path, """
        schema: v2
        source: {kind: rows}
        fields:
          A: {alias: [에이]}
          B: {alias: [비]}
          D: {template: "{{A}}-{{B}}"}
        filter:
          - {field: D, not_in: [x-y]}
        body:
          fields: [D]
          split: true
          repeat: [D]
    """, [{"에이": "a", "비": "b"}, {"에이": "x", "비": "y"}])
    assert [r["D"] for r in rows] == ["a-b"]


def test_pack_bundles_fields_into_one_json_column(tmp_path):
    """`pack` — 값 여럿을 적재 컬럼 하나에 JSON 으로 담는다.

    `derive` 로는 만들 수 없다. 문자열 치환이라 값에 따옴표가 섞이면 깨진 JSON 이 조용히
    만들어진다 — 그래서 직렬화까지 이 기능이 맡는다.
    """
    rows = _rows(tmp_path, """
        schema: v2
        source: {kind: rows}
        fields:
          BENEFIT: {alias: [혜택]}
          LIMIT:   {alias: [한도]}
          BRAND:   {alias: [브랜드]}
          MISSING: {alias: [없음]}
          DISPLAY_NM: {template: "{{BRAND}} 카드"}
          DETAIL_JSON: {pack: [BENEFIT, LIMIT, MISSING, DISPLAY_NM]}
        body:
          fields: [BENEFIT]
    """, [{"혜택": '연 18,000원 "무이자"', "한도": "500만원", "브랜드": "삼성"}])

    assert json.loads(rows[0]["DETAIL_JSON"]) == {
        "BENEFIT": '연 18,000원 "무이자"',
        "LIMIT": "500만원",
        "MISSING": None,        # 값이 없어도 키는 남는다(적재쪽 키 집합이 흔들리지 않는다)
        "DISPLAY_NM": "삼성 카드",   # derive 산출도 담을 수 있다(pack 이 파이프라인 마지막)
    }
    assert rows[0]["BENEFIT"] == '연 18,000원 "무이자"'  # 묶은 원천은 그대로 남는다


def test_pack_includes_sequence_output(tmp_path):
    """순번은 값 파이프라인 뒤에 매겨진다 — 묶기가 그보다 앞서면 JSON 안이 늘 null 이다."""
    rows = _rows(tmp_path, """
        schema: v2
        source: {kind: rows}
        fields:
          A: {alias: [에이]}
          ROW_NO: {seq: {prefix: "R-", width: 3}}
          J: {pack: [A, ROW_NO]}
        body:
          fields: [A]
    """, [{"에이": "a"}, {"에이": "b"}])

    assert [json.loads(r["J"])["ROW_NO"] for r in rows] == ["R-001", "R-002"]


def test_sections_require_still_accepts_a_pack_field(tmp_path):
    """sections 는 묶기가 필수 검사 **앞**이다 — rows/records 와 달리 require 로 고를 수 있다.

    compile_pack 의 선별 금지가 kind 를 가리지 않으면 여기서 동작하는 설정이 기동 실패가 된다.
    """
    sections = _rows(tmp_path, """
        schema: v2
        source: {kind: sections}
        fields:
          TITLE: {alias: [title]}
          J: {pack: [TITLE]}
        require:
          fields: [J]
    """, {"title": "T", "body": {"a": "본문내용입니다"}}, kind="sections")

    assert json.loads(sections[0]["J"]) == {"TITLE": "T"}


def test_pack_result_is_always_a_string(tmp_path):
    """dict 로 두면 경로마다 모양이 갈린다 — 행 경로는 metadata 를 그대로 청크에 싣는다."""
    rows = _rows(tmp_path, """
        schema: v2
        source: {kind: rows}
        fields:
          A: {alias: [에이]}
          J: {pack: [A]}
        body:
          fields: [A]
    """, [{"에이": "a"}])

    assert isinstance(rows[0]["J"], str)


def test_to_json_keeps_a_single_field_loadable_in_a_json_column(tmp_path):
    """`to_json` — 필드 **하나**의 모양을 JSON 으로 보장한다.

    `pack` 은 묶을 원천 필드가 여럿일 때의 기능이라, 출처가 하나뿐인 필드(LLM 추출 결과가
    곧 그 컬럼인 경우)에는 쓸 수 없다. 오라클 JSON 컬럼은 스칼라도 문법상 받지만 그 순간
    `JSON_VALUE(col, '$.키')` 가 NULL 이 되므로, 스칼라가 오면 감싸서 객체로 만든다.
    """
    rows = _rows(tmp_path, """
        schema: v2
        source: {kind: rows}
        fields:
          TITLE: {alias: [제목]}
          FEE:   {alias: [연회비], transform: [to_json]}
          EMPTY: {alias: [없음], transform: [to_json]}
        body:
          fields: [TITLE]
    """, [{"제목": "연회비 안내", "연회비": "국내전용 18,000원"}])

    assert json.loads(rows[0]["FEE"]) == {"value": "국내전용 18,000원"}
    assert rows[0]["EMPTY"] is None      # 빈 값은 DB NULL 로 둔다("" 는 유효한 JSON 이 아니다)


def test_to_json_wrap_key_and_drop(tmp_path):
    """스칼라 처리는 두 가지다 — 키를 씌워 살리거나(wrap), 적재하지 않거나(drop)."""
    rows = _rows(tmp_path, """
        schema: v2
        source: {kind: rows}
        fields:
          TITLE: {alias: [제목]}
          A: {alias: [출시일], transform: [{name: to_json, key: release_date}]}
          B: {alias: [연회비], transform: [{name: to_json, on_scalar: drop}]}
        body:
          fields: [TITLE]
    """, [{"제목": "안내", "출시일": "20260710", "연회비": "국내전용 18,000원"}])

    assert json.loads(rows[0]["A"]) == {"release_date": "20260710"}
    assert rows[0]["B"] is None


def test_to_json_normalizes_json_text_and_refuses_broken_fragments(tmp_path):
    """JSON 텍스트는 파싱해 재직렬화하고, 조각은 감싸지 않고 null 로 둔다.

    조각을 `{"value": "{\"a\":"}` 로 감싸면 쓰레기가 유효 JSON 으로 위장되고, 원천이 한
    JSON 을 여러 행에 잘라 보내는 스키마의 신호(broken_json)를 지우게 된다.
    """
    rows = _rows(tmp_path, """
        schema: v2
        source: {kind: rows}
        fields:
          TITLE:  {alias: [제목]}
          OK:     {alias: [정상], transform: [to_json]}
          BROKEN: {alias: [조각], transform: [to_json]}
        body:
          fields: [TITLE]
    """, [{"제목": "안내", "정상": '{"a":   1}', "조각": '{"a":'}])

    assert rows[0]["OK"] == '{"a": 1}'   # 공백·이스케이프 표기가 통일된다
    assert rows[0]["BROKEN"] is None


def test_to_json_is_available_in_every_custom_fields_form(tmp_path):
    """rows·records·sections — 값 파이프라인을 공유하므로 같은 표기가 그대로 통한다.

    document(llm·python)형은 같은 파이프라인을 다른 파일에서 도므로
    test_python_extractor_unit.py 가 맡는다.
    """
    rows = _rows(tmp_path, """
        schema: v2
        source: {kind: rows}
        fields:
          TITLE: {alias: [제목]}
          ATTRS: {alias: [속성], transform: [to_json]}
        body:
          fields: [TITLE]
    """, [{"제목": "안내", "속성": "혜택"}])
    assert json.loads(rows[0]["ATTRS"]) == {"value": "혜택"}

    # 원천이 JSON 을 텍스트로 실어 보내는 경우(적재 컬럼을 그대로 내려주는 API).
    records = _rows(tmp_path, """
        schema: v2
        source: {kind: records}
        fields:
          TITLE: {alias: [title]}
          ATTRS: {alias: [attrs], transform: [to_json]}
        body:
          fields: [TITLE]
    """, [{"title": "제목", "attrs": '{"annual_fee":  18000}'}], kind="json")
    assert json.loads(records[0]["ATTRS"]) == {"annual_fee": 18000}

    # sections 는 `PRODUCT_ATTRS` 가 배열 그대로 나가던 실제 경로다(모니모 product_hpp).
    sections = _rows(tmp_path, """
        schema: v2
        source: {kind: sections}
        fields:
          PRODUCT_C: {alias: [productCode]}
          PRODUCT_ATTRS: {alias: [benefit], transform: [to_json]}
        require:
          fields: [PRODUCT_C]
    """, {"productCode": "AAP1344", "benefit": ["현금카드 기능", "빅포인트 적립"]},
        kind="sections")
    assert sections, "섹션이 만들어져야 한다"
    for section in sections:
        assert section["PRODUCT_ATTRS"] == '["현금카드 기능", "빅포인트 적립"]'


def test_to_json_works_in_v2_notation(tmp_path):
    """출고 표기는 v2 다 — `fields.<이름>.transform` 으로도 같은 결과·같은 가드여야 한다."""
    body = """
        schema: v2
        source: {kind: rows}
        fields:
          TITLE: {alias: [제목]}
          PRODUCT_ATTRS:
            alias: [속성]
            transform: [{name: to_json, key: fee_text}]
        body:
          fields: [TITLE]
    """
    rows = _rows(tmp_path, body, [{"제목": "안내", "속성": "국내전용 18,000원"}])
    assert json.loads(rows[0]["PRODUCT_ATTRS"]) == {"fee_text": "국내전용 18,000원"}

    with pytest.raises(ValueError, match="쓸 수 없습니다"):
        _rows(tmp_path, body.replace("fields: [TITLE]", "fields: [TITLE, PRODUCT_ATTRS]"),
              [{"제목": "안내", "속성": "x"}])


def test_raw_brings_a_source_object_in_as_a_field_value(tmp_path):
    """`raw` — 원천 JSON 의 객체를 필드 값으로 받는다.

    기본 규칙은 "객체는 값이 아니라 구조" 다. 그래야 `eventList` 같은 레코드 배열이 필드
    값으로 잘못 잡히지 않는다. 그 규칙 때문에 객체를 통째로 적재 DB 의 JSON 컬럼에 담을
    수단이 없었다 — `raw` 는 그 판정을 **필드 이름으로 명시한 자리에서만** 끈다.
    """
    payload = [{"title": "T", "attrs": {"annual_fee": 18000, "benefits": ["5% 할인"]}}]

    # raw 없이는 비어 있다(기본 규칙이 그대로 산다).
    plain = _rows(tmp_path, """
        schema: v2
        source: {kind: records}
        fields:
          TITLE: {alias: [title]}
          ATTRS: {alias: [attrs]}
        body:
          fields: [TITLE]
    """, payload, kind="json")
    assert plain[0]["ATTRS"] is None

    # raw + to_json 이 적재 컬럼에 넣을 수 있는 모양을 만든다.
    raw = _rows(tmp_path, """
        schema: v2
        source: {kind: records}
        fields:
          TITLE: {alias: [title]}
          ATTRS: {alias: [attrs], raw: true, transform: [to_json]}
        body:
          fields: [TITLE]
    """, payload, kind="json")
    assert json.loads(raw[0]["ATTRS"]) == {"annual_fee": 18000, "benefits": ["5% 할인"]}


def test_raw_in_sections_stays_root_only(tmp_path):
    """sections 의 루트 전용 계약은 raw 에서도 그대로다.

    깊이까지 함께 풀면 `mpo[].code` 같은 관련 상품 값이 상품 identity 로 승격되던 문제가
    그 필드에서 되살아난다.
    """
    body = """
        schema: v2
        source: {kind: sections}
        fields:
          PRODUCT_C: {alias: [productCode]}
          PRODUCT_ATTRS: {alias: [attrs], raw: true, transform: [to_json]}
        require:
          fields: [PRODUCT_C]
    """
    at_root = _rows(tmp_path, body, {
        "productCode": "AAP1344", "attrs": {"annual_fee": 18000}, "desc": "본문"},
        kind="sections")
    assert json.loads(at_root[0]["PRODUCT_ATTRS"]) == {"annual_fee": 18000}

    nested = _rows(tmp_path, body, {
        "productCode": "AAP1344", "mpo": [{"attrs": {"annual_fee": 9}}], "desc": "본문"},
        kind="sections")
    assert nested[0]["PRODUCT_ATTRS"] is None


def test_raw_in_v2_notation(tmp_path):
    """출고 표기는 v2 다 — `fields.<이름>.raw`."""
    rows = _rows(tmp_path, """
        schema: v2
        source: {kind: records}
        fields:
          TITLE: {alias: [title]}
          ATTRS:
            alias: [attrs]
            raw: true
            transform: [{name: to_json}]
        body: {fields: [TITLE]}
    """, [{"title": "T", "attrs": {"a": 1}}], kind="json")
    assert json.loads(rows[0]["ATTRS"]) == {"a": 1}


def test_raw_on_a_field_without_alias_fails_at_startup(tmp_path):
    """값을 원천에서 찾지 않는 필드에 raw 를 걸면 아무 일도 일어나지 않는다 — 오설정이다."""
    with pytest.raises(ValueError, match="원천에서 값을 찾는 필드가 아닙니다"):
        _rows(tmp_path, """
            schema: v2
            source: {kind: records}
            fields:
              TITLE: {alias: [title]}
              NOPE: {const: x, raw: true}
            body:
              fields: [TITLE]
        """, [{"title": "T"}], kind="json")


def test_raw_must_be_a_boolean(tmp_path):
    with pytest.raises(ValueError, match="true 또는 false"):
        _rows(tmp_path, """
            schema: v2
            source: {kind: records}
            fields:
              TITLE: {alias: [title]}
              ATTRS: {alias: [attrs], raw: "네"}
            body:
              fields: [TITLE]
        """, [{"title": "T"}], kind="json")


def test_pack_json_path_gets_the_same_feature(tmp_path):
    rows = _rows(tmp_path, """
        schema: v2
        source: {kind: records}
        fields:
          TITLE: {alias: [title]}
          FEE_AMT: {alias: [fee], transform: {name: to_int}}
          DETAIL_JSON: {pack: [TITLE, FEE_AMT]}
        body:
          fields: [TITLE]
    """, [{"title": "연회비 안내", "fee": "18,000원"}], kind="json")

    # 변환 뒤의 값을 담는다(정수는 JSON 숫자로 나간다)
    assert json.loads(rows[0]["DETAIL_JSON"]) == {"TITLE": "연회비 안내", "FEE_AMT": 18000}


def test_json_path_gets_the_same_features(tmp_path):
    rows = _rows(tmp_path, """
        schema: v2
        source: {kind: records}
        fields:
          TITLE: {alias: [title]}
          FEE_AMT: {alias: [fee], transform: {name: to_int}}
          DEL_YN: {alias: [delYn]}
          LABEL: {template: "{{TITLE}} ({{FEE_AMT}})"}
        filter:
          - {field: DEL_YN, not_in: [Y]}
        body:
          fields: [LABEL]
    """, [{"title": "연회비 안내", "fee": "18,000원", "delYn": "N"},
          {"title": "삭제", "fee": "0", "delYn": "Y"}], kind="json")
    assert len(rows) == 1
    assert rows[0]["LABEL"] == "연회비 안내 (18000)"


@pytest.mark.parametrize(
    "body, expect",
    [
        ("schema: v2\nsource: {kind: rows}\n"
         "fields: {T: {alias: [제목], transform: {name: 없는변환기}}}\n"
         "body: {fields: [T]}\n", "등록되지 않은"),
        ("schema: v2\nsource: {kind: rows}\n"
         "fields: {T: {alias: [제목], transform: {name: regex_sub}}}\n"
         "body: {fields: [T]}\n", "인자가 필요"),
        ('schema: v2\nsource: {kind: rows}\n'
         'fields: {T: {alias: [제목], transform: {name: regex_sub, pattern: "(["}}}\n'
         'body: {fields: [T]}\n', "정규식"),
        ("schema: v2\nsource: {kind: rows}\n"
         "fields: {T: {alias: [제목], transform: {name: date_int, pattern: x}}}\n"
         "body: {fields: [T]}\n", "인자를 받지 않"),
        ('schema: v2\nsource: {kind: rows}\n'
         'fields: {T: {alias: [제목]}, D: {template: "{{NOPE}}"}}\n'
         'body: {fields: [T]}\n', "만드는 설정이 없"),
        ("schema: v2\nsource: {kind: rows}\n"
         "fields: {T: {alias: [제목]}}\n"
         "filter:\n  - {field: NOPE, in: [Y]}\n"
         "body: {fields: [T]}\n", "만드는 설정이 없"),
        ("schema: v2\nsource: {kind: rows}\n"
         "fields: {T: {alias: [제목]}}\n"
         "filter:\n  - {field: T}\n"
         "body: {fields: [T]}\n", "정확히 하나"),
        ("schema: v2\nsource: {kind: rows}\n"
         "fields: {T: {alias: [제목]}}\n"
         "filter:\n  - {field: T, in: []}\n"
         "body: {fields: [T]}\n", "비어 있지 않은"),
        ("schema: v2\nsource: {kind: rows}\n"
         "fields: {T: {alias: [제목]}, J: {pack: [NOPE]}}\n"
         "body: {fields: [T]}\n", "만드는 설정이 없"),
        ("schema: v2\nsource: {kind: rows}\n"
         "fields: {T: {alias: [제목]}, J: {pack: T}}\n"
         "body: {fields: [T]}\n", "목록이어야"),
        ("schema: v2\nsource: {kind: rows}\n"
         "fields: {T: {alias: [제목]}, J: {pack: []}}\n"
         "body: {fields: [T]}\n", "묶을 필드가 없"),
        # pack 산출을 다시 묶으면 JSON 안에 JSON 문자열이 중첩되고 적용 순서가 yaml 키
        # 순서에 조용히 의존한다.
        ("schema: v2\nsource: {kind: rows}\n"
         "fields: {T: {alias: [제목]}, J: {pack: [T]}, K: {pack: [J]}}\n"
         "body: {fields: [T]}\n", "만드는 설정이 없"),
        # 본문 관련 키는 pack 필드를 받지 않는다(문서형은 조용히 버리고 행 경로는 싣는다).
        ("schema: v2\nsource: {kind: rows}\n"
         "fields: {T: {alias: [제목]}, J: {pack: [T]}}\n"
         "body: {fields: [T], split: true, repeat: [J]}\n", "쓸 수 없습니다"),
        # 선별(require/filter)은 묶기보다 먼저 돈다 — pack 산출로 고르면 늘 빈 값이다.
        ("schema: v2\nsource: {kind: rows}\n"
         "fields: {T: {alias: [제목]}, J: {pack: [T]}}\n"
         "require: {fields: [J]}\nbody: {fields: [T]}\n", "쓸 수 없습니다"),
        ("schema: v2\nsource: {kind: rows}\n"
         "fields: {T: {alias: [제목]}, J: {pack: [T]}}\n"
         "filter: [{field: J, in: [x]}]\nbody: {fields: [T]}\n", "쓸 수 없습니다"),
        # to_json 뒤에 다른 단계가 오면 산출이 더 이상 JSON 이 아니다(잘리거나 평문이 된다).
        ("schema: v2\nsource: {kind: rows}\n"
         "fields: {T: {alias: [제목], transform: [to_json, {name: truncate, length: 5}]}}\n"
         "body: {fields: [T]}\n", "맨 뒤"),
        # 변환은 필드를 제자리에서 덮으므로, 본문에도 쓰이는 필드에 걸면 본문에 JSON 이 실린다.
        ("schema: v2\nsource: {kind: rows}\n"
         "fields: {T: {alias: [제목], transform: [to_json]}}\n"
         "body: {fields: [T]}\n", "쓸 수 없습니다"),
        # pack 이 다시 묶으면 JSON 안에 JSON 문자열이 중첩된다.
        ("schema: v2\nsource: {kind: rows}\n"
         "fields: {T: {alias: [제목]}, U: {const: x, transform: [to_json]}, "
         "J: {pack: [U]}}\n"
         "body: {fields: [T]}\n", "중첩"),
        ('schema: v2\nsource: {kind: rows}\n'
         'fields: {T: {alias: [제목], transform: [{name: to_json, on_scalar: 널}]}}\n'
         'body: {fields: [T]}\n', "wrap"),
        # 파일명과 원천 key 를 섞으면 어느 쪽이 우선인지 모호하다.
        ("schema: v2\nsource: {kind: rows}\n"
         "fields: {T: {alias: [제목]}, B: {alias: [$file, ID]}}\n"
         "body: {fields: [T]}\n", "함께 쓸 수 없습니다"),
        # 파일명 원천은 document/html/records 만 채운다. 다른 kind 에서 조용히 비지 않게 막는다.
        ("schema: v2\nsource: {kind: rows}\n"
         "fields: {T: {alias: [제목]}, B: {alias: [$file]}}\n"
         "body: {fields: [T]}\n", "file_fields"),
    ],
)
def test_misconfiguration_is_caught_at_startup(tmp_path, body, expect):
    """요청 때 터지면 어느 설정이 문제인지 로그만 보고는 알 수 없다."""
    with pytest.raises(ValueError, match=expect):
        _rows(tmp_path, body, [{"제목": "x"}])


@pytest.mark.parametrize(
    "filename, expected",
    [
        ("hpp_rag_adcc_237219_init.json", "hpp_rag_adcc_237219"),
        ("hpp_rag_adcc_237219_delta_001.json", "hpp_rag_adcc_237219"),
        # 규칙에 맞지 않는 파일명은 파싱을 막지 않고 비운다(경고는 file_source 가 남긴다).
        ("export.json", None),
    ],
)
def test_file_alias_takes_value_from_filename(tmp_path, filename, expected):
    """`alias: [$file]` — 원천 본문에 없는 식별자를 파일명에서 가져온다.

    파일명은 원천값과 같은 층이라 transform 이 그대로 걸리고, 레코드 안의 ID 는 보지 않는다.
    """
    path = tmp_path / "custom_field_x.yaml"
    path.write_text(textwrap.dedent("""
        schema: v2
        source: {kind: records}
        fields:
          T: {alias: [제목]}
          BIZ_ID:
            alias: [$file]
            transform: {name: regex_extract, pattern: '^(.+?_\\d+)(?=[_.])'}
            default: null
        body: {fields: [T]}
    """), encoding="utf-8")
    mapper = JsonRecordsMapper(
        config_file=path.name, resource_path=str(tmp_path),
        doc_type="t", extractor="json_mapping",
    )
    rows = mapper.build_fields(
        [{"제목": "a", "ID": "rec-1"}, {"제목": "b"}], "t", source_filename=filename)
    assert [r["BIZ_ID"] for r in rows] == [expected, expected]


def test_hash_applies_to_template_result(tmp_path):
    """결합 키를 해시로 코드화한다(원천에 ID 가 없는 cs_ssf).

    변환은 결합보다 먼저 돌기 때문에, template 결과에 걸지 않으면 결합이 해시를 덮는다.
    빈 칸도 구분자 자리를 남겨 `A||B` 와 `A|B|` 가 다른 코드가 된다.
    """
    import hashlib

    rows = _rows(tmp_path, """
        schema: v2
        source: {kind: rows}
        fields:
          L1: {alias: [대분류]}
          L2: {alias: [중분류], default: null}
          TITLE: {alias: [제목]}
          BIZ_ID:
            template: "{{L1}}|{{L2}}|{{TITLE}}"
            transform: {name: hash, prefix: SSF_CS_, length: 16}
        body:
          fields: [TITLE]
    """, [{"대분류": "자동차", "중분류": "", "제목": "담보"},
          {"대분류": "자동차", "중분류": "담보", "제목": ""}])
    expect = [
        "SSF_CS_" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
        for key in ("자동차||담보", "자동차|담보|")
    ]
    assert [r["BIZ_ID"] for r in rows] == expect
