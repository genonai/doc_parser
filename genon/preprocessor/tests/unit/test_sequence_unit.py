"""항목 순번(sequence) — 원천에 번호가 없는 레코드에 목표필드로 번호를 매긴다.

번호를 매기는 **시점**이 이 기능의 핵심이다. 값 파이프라인 안에서 매기면 filter/required 로
걸러진 레코드가 번호를 소비해 적재된 값에 구멍이 생긴다. 그래서 확정된 레코드 목록에
후처리로 걸고, 엑셀 시트 경계도 넘는다(문서 1건 = 한 벌의 번호).
"""
import textwrap

import pytest

from genon.preprocessor.processing.enrichment import config_v2
from genon.preprocessor.processing.enrichment.json_records import JsonRecordsMapper
from genon.preprocessor.processing.enrichment.tabular_custom_fields import (
    TabularCustomFieldsMapper,
)

pytestmark = pytest.mark.unit


def _mapper(tmp_path, body, kind="tabular"):
    path = tmp_path / "custom_field_x.yaml"
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    cls, extractor = (
        (TabularCustomFieldsMapper, "tabular_mapping") if kind == "tabular"
        else (JsonRecordsMapper, "json_mapping")
    )
    return cls(
        config_file=path.name, resource_path=str(tmp_path),
        doc_type="t", extractor=extractor,
    )


def _sheets(*sheets):
    return {"data": [
        {"sheet_name": f"S{i}", "data_rows": rows} for i, rows in enumerate(sheets, start=1)
    ]}


_CFG = """
    schema: v2
    source: {kind: rows}
    fields:
      TITLE:  {alias: [제목]}
      DEL_YN: {alias: [삭제여부]}
      ROW_NO: {seq: {prefix: "FAQ-", width: 4}}
    filter:
      - {field: DEL_YN, not_in: [Y]}
    body:
      fields: [TITLE]
"""


def test_prefix_and_width_make_a_padded_id(tmp_path):
    rows = _mapper(tmp_path, _CFG).build_fields(
        _sheets([{"제목": "가", "삭제여부": "N"}, {"제목": "나", "삭제여부": "N"}]), "t"
    )
    assert [r["ROW_NO"] for r in rows] == ["FAQ-0001", "FAQ-0002"]


def test_numbering_continues_across_sheets(tmp_path):
    """시트별로 리셋하면 한 문서 안에 같은 번호가 둘 생긴다."""
    rows = _mapper(tmp_path, _CFG).build_fields(
        _sheets([{"제목": "가", "삭제여부": "N"}], [{"제목": "나", "삭제여부": "N"}]), "t"
    )
    assert [r["ROW_NO"] for r in rows] == ["FAQ-0001", "FAQ-0002"]


def test_filtered_records_do_not_consume_numbers(tmp_path):
    """값 파이프라인 안에서 매기면 걸러진 행이 번호를 먹어 0002 가 빈다."""
    rows = _mapper(tmp_path, _CFG).build_fields(
        _sheets([
            {"제목": "가", "삭제여부": "N"},
            {"제목": "삭제됨", "삭제여부": "Y"},
            {"제목": "나", "삭제여부": "N"},
        ]), "t"
    )
    assert [(r["TITLE"], r["ROW_NO"]) for r in rows] == [("가", "FAQ-0001"), ("나", "FAQ-0002")]


def test_required_skipped_records_do_not_consume_numbers(tmp_path):
    rows = _mapper(tmp_path, """
        schema: v2
        source: {kind: rows}
        fields:
          TITLE:  {alias: [제목]}
          ROW_NO: {seq: {width: 3}}
        require: {fields: [TITLE]}
        body:
          fields: [TITLE]
    """).build_fields(
        _sheets([{"제목": "가"}, {"제목": ""}, {"제목": "나"}]), "t"
    )
    assert [r["ROW_NO"] for r in rows] == ["001", "002"]


def test_bare_sequence_is_an_int(tmp_path):
    """접두도 자리수도 없으면 적재 컬럼이 숫자인 경우가 많다 — 문자열 "1" 을 내지 않는다."""
    rows = _mapper(tmp_path, """
        schema: v2
        source: {kind: rows}
        fields:
          TITLE:  {alias: [제목]}
          ROW_NO: {seq: {}}
        body:
          fields: [TITLE]
    """).build_fields(_sheets([{"제목": "가"}, {"제목": "나"}]), "t")
    assert [r["ROW_NO"] for r in rows] == [1, 2]


def test_start_shifts_the_first_number(tmp_path):
    rows = _mapper(tmp_path, """
        schema: v2
        source: {kind: rows}
        fields:
          TITLE:  {alias: [제목]}
          ROW_NO: {seq: {start: 100}}
        body:
          fields: [TITLE]
    """).build_fields(_sheets([{"제목": "가"}, {"제목": "나"}]), "t")
    assert [r["ROW_NO"] for r in rows] == [100, 101]


def test_json_mapping_follows_the_same_rules(tmp_path):
    mapper = _mapper(tmp_path, """
        schema: v2
        source: {kind: records, records_at: items}
        fields:
          TITLE:  {alias: [title]}
          DEL_YN: {alias: [delYn]}
          ROW_NO: {seq: {prefix: "N-", width: 2}}
        filter:
          - {field: DEL_YN, not_in: [Y]}
        body:
          fields: [TITLE]
    """, kind="json")
    rows = mapper.build_fields(
        {"items": [
            {"title": "가", "delYn": "N"},
            {"title": "삭제됨", "delYn": "Y"},
            {"title": "나", "delYn": "N"},
        ]}, "t",
    )
    assert [r["ROW_NO"] for r in rows] == ["N-01", "N-02"]


def test_sequence_field_can_be_used_as_a_target_field(tmp_path):
    """목표필드 목록에 등록하지 않으면 body.repeat/filter 참조가 기동 시 실패한다."""
    mapper = _mapper(tmp_path, """
        schema: v2
        source: {kind: rows}
        fields:
          TITLE:  {alias: [제목]}
          ROW_NO: {seq: {prefix: "FAQ-"}}
        body:
          fields: [ROW_NO, TITLE]
          repeat: [ROW_NO]
          split: true
          labels:
            ROW_NO: 번호
    """)
    rows = mapper.build_fields(_sheets([{"제목": "가"}]), "t")
    element = mapper.to_parse_format_from_fields(rows, "t")["elements"][0]
    assert element["content"].startswith("번호: FAQ-1")


def test_name_clash_with_another_block_fails_at_startup(tmp_path):
    """순번이 파이프라인 맨 뒤에서 덮으므로 원천 값이 말없이 사라진다."""
    with pytest.raises(ValueError, match="이미 만드는 필드"):
        _mapper(tmp_path, """
            schema: v2
            source: {kind: rows}
            fields:
              ROW_NO: {alias: [번호], seq: {}}
        """)


def test_unknown_option_fails_at_startup(tmp_path):
    with pytest.raises(ValueError, match="모르는 옵션"):
        _mapper(tmp_path, """
            schema: v2
            source: {kind: rows}
            fields:
              TITLE:  {alias: [제목]}
              ROW_NO: {seq: {padding: 4}}
        """)


def test_non_integer_width_fails_at_startup(tmp_path):
    """문자열을 조용히 받으면 zfill 이 요청마다 터진다(기동은 성공한 채로)."""
    with pytest.raises(ValueError, match="정수여야 합니다"):
        _mapper(tmp_path, """
            schema: v2
            source: {kind: rows}
            fields:
              TITLE:  {alias: [제목]}
              ROW_NO: {seq: {width: "4"}}
        """)


def test_v2_seq_normalizes():
    v2 = {
        "schema": "v2",
        "source": {"kind": "rows"},
        "fields": {
            "TITLE": {"alias": ["제목"]},
            "ROW_NO": {"seq": {"prefix": "FAQ-", "width": 4}},
        },
    }
    v1, extractor = config_v2.normalize(v2, label="t")
    assert extractor == "tabular_mapping"
    assert v1["sequence"] == {"ROW_NO": {"prefix": "FAQ-", "width": 4}}


def test_seq_on_a_document_kind_fails_at_startup():
    """문서형·섹션형에는 "항목 N번째"가 없다 — 조용히 무시되지 않게 지원 키 표가 막는다."""
    from genon.preprocessor.processing.enrichment.config_schema import validate_known_keys

    v1, extractor = config_v2.normalize(
        {"schema": "v2", "source": {"kind": "document"},
         "fields": {"ROW_NO": {"seq": {}}}},
        label="t",
    )
    with pytest.raises(ValueError, match="sequence"):
        validate_known_keys(v1, label="t", extractor=extractor)
