"""D1·D2 — 한 파일에 스키마가 다른 묶음이 여럿 올 때.

JSON 에 `faqList` + `noticeList` 가 함께 오거나, 엑셀 한 파일에 스키마가 다른 표가 둘
있는 원천이 있다. 매퍼 하나에 매핑 하나뿐이라 그중 하나만 다룰 수 있었고 나머지는
통째로 버려졌다. `tables:` 같은 하위 스키마를 새로 만들지 않고 **매퍼를 여러 개 등록**
하는 것으로 푼다 — 이미 있는 doc_type 등록 구조를 그대로 쓰므로 설정 개념이 늘지 않는다.
"""
import textwrap

import pytest

from genon.preprocessor.facade.enrichment.json_records import JsonRecordsMapper
from genon.preprocessor.facade.enrichment.tabular_custom_fields import (
    TabularCustomFieldsMapper,
    merge_parse_formats,
)

pytestmark = pytest.mark.unit


def _tabular(tmp_path, name, body):
    (tmp_path / name).write_text(textwrap.dedent(body), encoding="utf-8")
    return TabularCustomFieldsMapper(
        config_file=name, resource_path=str(tmp_path),
        doc_type="mixed", extractor="tabular_mapping",
    )


_TWO_SHEETS = {"data": [
    {"sheet_name": "FAQ", "data_rows": [{"질문": "가입은?", "답변": "앱에서"}]},
    {"sheet_name": "용어집", "data_rows": [{"용어": "연회비", "정의": "카드 연간 회비"}]},
]}


def test_mapper_skips_tables_it_cannot_handle(tmp_path):
    """매퍼가 여럿일 때 못 맡는 표는 건너뛴다 — 다른 매퍼가 맡는다."""
    faq = _tabular(tmp_path, "custom_field_faq.yaml", """
        column_map: {QUESTION: [질문], ANSWER: [답변]}
        required: [QUESTION]
        constants: {KIND: FAQ}
        text_fields: [QUESTION]
    """)
    rows = faq.build_fields(_TWO_SHEETS, "mixed", skip_unmapped=True)
    assert [r["KIND"] for r in rows] == ["FAQ"]
    assert rows[0]["QUESTION"] == "가입은?"


def test_single_mapper_still_fails_on_missing_required_column(tmp_path):
    """매퍼가 하나면 종전대로 하드 에러다 — 원천 스키마가 바뀐 신호를 삼키면 안 된다."""
    faq = _tabular(tmp_path, "custom_field_faq.yaml", """
        column_map: {QUESTION: [질문]}
        required: [QUESTION]
        text_fields: [QUESTION]
    """)
    with pytest.raises(ValueError, match="필수 Excel 컬럼"):
        faq.build_fields(_TWO_SHEETS, "mixed")


def test_two_mappers_cover_two_schemas(tmp_path):
    """각 매퍼가 자기 표만 맡고 결과가 합쳐진다."""
    faq = _tabular(tmp_path, "custom_field_faq.yaml", """
        column_map: {QUESTION: [질문], ANSWER: [답변]}
        required: [QUESTION]
        constants: {KIND: FAQ}
        text_fields: [QUESTION, ANSWER]
    """)
    term = _tabular(tmp_path, "custom_field_term.yaml", """
        column_map: {TERM: [용어], DEFINITION: [정의]}
        required: [TERM]
        constants: {KIND: TERM}
        text_fields: [TERM, DEFINITION]
    """)
    merged = merge_parse_formats([
        m.to_parse_format_from_fields(
            m.build_fields(_TWO_SHEETS, "mixed", skip_unmapped=True), "mixed"
        )
        for m in (faq, term)
    ])
    kinds = [(e.get("metadata") or {}).get("KIND") for e in merged["elements"]]
    assert kinds == ["FAQ", "TERM"]


def test_json_mappers_split_by_records_key(tmp_path):
    """배열마다 다른 매퍼가 붙는다."""
    def build(name, body):
        (tmp_path / name).write_text(textwrap.dedent(body), encoding="utf-8")
        return JsonRecordsMapper(
            config_file=name, resource_path=str(tmp_path),
            doc_type="mixed", extractor="json_mapping",
        )

    payload = {
        "faqList": [{"question": "가입은?", "answer": "앱에서"}],
        "noticeList": [{"title": "점검 안내"}],
    }
    faq = build("custom_field_faq.yaml", """
        records: faqList
        key_map: {QUESTION: [question], ANSWER: [answer]}
        constants: {KIND: FAQ}
        text_fields: [QUESTION]
    """)
    notice = build("custom_field_notice.yaml", """
        records: noticeList
        key_map: {TITLE: [title]}
        constants: {KIND: NOTICE}
        text_fields: [TITLE]
    """)
    merged = merge_parse_formats([
        m.to_parse_format(m.build_fields(payload, "mixed"), "mixed") for m in (faq, notice)
    ])
    kinds = [(e.get("metadata") or {}).get("KIND") for e in merged["elements"]]
    assert kinds == ["FAQ", "NOTICE"]


def test_merge_keeps_single_result_untouched():
    """매퍼가 하나면 합치는 단계가 결과를 건드리지 않아야 한다(회귀 방지)."""
    one = {"elements": [{"category": "custom_fields_row"}], "usage": {"pages": 3},
           "metadata": {"doc_type": "x"}}
    assert merge_parse_formats([one]) is one
    assert merge_parse_formats([]) == {"elements": [], "usage": {"pages": 0}}


def test_merge_concatenates_and_takes_max_page():
    merged = merge_parse_formats([
        {"elements": [{"i": 1}], "usage": {"pages": 2}, "metadata": {"doc_type": "x"}},
        {"elements": [{"i": 2}], "usage": {"pages": 5}, "metadata": {"doc_type": "x"}},
    ])
    assert [e["i"] for e in merged["elements"]] == [1, 2]
    assert merged["usage"]["pages"] == 5
    assert merged["metadata"] == {"doc_type": "x"}


def test_json_mapper_list_tolerates_semantic_mappers():
    """`_json_records_mappers` 에는 json_semantic 매퍼도 섞여 들어온다.

    빌더 둘(build_json_records_mappers / build_semantic_json_mappers)의 결과를 한 리스트에
    담기 때문이다. 그쪽은 `records_key` 가 없어, 속성을 그냥 읽으면 product_hpp 처리가
    통째로 죽는다(실제로 그렇게 깨뜨렸다가 doc_type 검증에서 잡혔다).
    """
    from genon.preprocessor.facade.enrichment.json_semantic import SemanticJsonMapper

    semantic = object.__new__(SemanticJsonMapper)   # __init__ 우회 — 속성 부재 상황 재현
    assert getattr(semantic, "records_key", None) is None


def test_ambiguous_json_mappers_are_rejected(tmp_path, monkeypatch):
    """records 키가 겹치거나 없으면 어느 매퍼가 무엇을 맡는지 알 수 없다 — 거부한다."""
    from facade import parser_processor as pp

    class _Mapper:
        def __init__(self, key):
            self.records_key = key

        def matches(self, _doc_type):
            return True

    processor = object.__new__(pp.DocumentProcessor)

    processor._json_records_mappers = [_Mapper("faqList"), _Mapper("noticeList")]
    assert len(processor._json_records_mappers_for("mixed")) == 2

    for mappers in (
        [_Mapper("faqList"), _Mapper("faqList")],   # 같은 배열을 둘이 맡는다
        [_Mapper("faqList"), _Mapper(None)],        # 하나는 무엇을 맡는지 모른다
    ):
        processor._json_records_mappers = mappers
        with pytest.raises(Exception, match="records 키가 겹칩니다"):
            processor._json_records_mappers_for("mixed")
