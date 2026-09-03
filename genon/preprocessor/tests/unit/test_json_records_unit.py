"""json_records — JSON 레코드 배열 → 목표필드/청크 본문 매핑 단위 테스트.

LLM 은 호출하지 않는다(llm_fields 는 선언 파싱만 확인). 실제 LLM 경로는
examples/parse_chunk 스크립트로 확인한다.
"""
import textwrap

import pytest

from genon.preprocessor.facade.enrichment.field_transforms import transform_date_int_flex
from genon.preprocessor.facade.enrichment.json_records import (
    JsonRecordsMapper,
    build_json_records_mappers,
    collect_records,
    find_field,
    find_fields,
    html_to_text,
)

pytestmark = pytest.mark.unit


SAMPLE_PAYLOAD = {
    "resultCode": "0000",
    "eventList": [
        {
            "ID": "CM26061182",
            "회사명": "모니모",
            "제목": "무신사 블랙 프라이데이 혜택 받기",
            "이벤트 시작일": "26.07.01",
            "이벤트 종료일": "26.07.31",
            "wcmsHtml": {"htmlText": "<div><p>이벤트 상세 내용</p></div>"},
        },
        {
            "ID": "CM26061183",
            "회사명": "모니모",
            "제목": "",
            "이벤트 시작일": "26.08.01",
            "이벤트 종료일": "26.08.31",
            "wcmsHtml": {"htmlText": "<div>제목 없음</div>"},
        },
    ],
}


BASE_CONFIG = """
records: eventList
key_map:
  EVENT_ID:    [ID]
  TITLE:       [제목, title]
  EVENT_FROM:  [이벤트 시작일]
  EVENT_TO:    [이벤트 종료일]
  DETAIL_HTML: [htmlText]
required: [TITLE]
defaults:
  KEYWORD: null
transforms:
  EVENT_FROM: date_int_flex
  EVENT_TO:   date_int_flex
html_text_fields:
  DETAIL_TEXT: DETAIL_HTML
text_fields: [TITLE, DETAIL_TEXT]
split: true
chunk_prefix_fields: [TITLE]
"""


def write_mapper(tmp_path, config_text=BASE_CONFIG, doc_type="monimo_event"):
    """설정 yaml 을 임시 파일로 쓰고 매퍼를 만든다."""
    path = tmp_path / "custom_field_json.yaml"
    path.write_text(textwrap.dedent(config_text), encoding="utf-8")
    return JsonRecordsMapper(
        config_file=path.name,
        resource_path=str(tmp_path),
        doc_type=doc_type,
        extractor="json_mapping",
    )


# ── 레코드 수집 ─────────────────────────────────────────────────────────────

def test_collect_records_finds_key_at_depth():
    payload = {"data": {"body": {"eventList": [{"a": 1}, {"b": 2}]}}}
    assert collect_records(payload, "eventList") == [{"a": 1}, {"b": 2}]


def test_collect_records_ignores_non_dict_items():
    payload = {"eventList": [{"a": 1}, "문자열", 3]}
    assert collect_records(payload, "eventList") == [{"a": 1}]


def test_collect_records_without_key_uses_payload():
    assert collect_records({"a": 1}, None) == [{"a": 1}]
    assert collect_records([{"a": 1}, {"b": 2}], None) == [{"a": 1}, {"b": 2}]


def test_collect_records_returns_none_when_key_missing():
    assert collect_records({"other": []}, "eventList") is None


def test_collect_records_accepts_single_dict_value():
    assert collect_records({"event": {"a": 1}}, "event") == [{"a": 1}]


# ── 필드 별칭 해석 ───────────────────────────────────────────────────────────

def test_find_field_matches_nested_key_without_path_syntax():
    record = {"wcmsHtml": {"htmlText": "<p>본문</p>"}}
    assert find_field(record, ["htmlText"]) == "<p>본문</p>"


def test_find_field_prefers_shallower_depth_over_alias_order():
    """깊이가 별칭 순서보다 우선한다 — 최상위 제목이 중첩 title 보다 먼저 잡힌다."""
    record = {"제목": "얕음", "nested": {"title": "깊음"}}
    assert find_field(record, ["title", "제목"]) == "얕음"


def test_find_field_normalizes_spacing_and_case():
    record = {"Event Start_Date": "26.07.01"}
    assert find_field(record, ["eventstartdate"]) == "26.07.01"


def test_find_field_skips_containers_and_strips_whitespace():
    record = {"TITLE": {"nested": "dict 는 값이 아님"}, "inner": {"TITLE": "  실제 값  "}}
    assert find_field(record, ["TITLE"]) == "실제 값"


def test_find_field_returns_none_when_absent():
    assert find_field({"a": 1}, ["없는키"]) is None


def test_find_fields_collects_repeated_nested_values_in_order():
    record = {
        "bubble": [{"serviceUrl": "첫째"}, {"serviceUrl": "둘째"}],
        "nested": {"items": [{"service_url": "둘째"}, {"serviceUrl": "셋째"}]},
    }
    assert find_fields(record, ["serviceUrl"]) == ["첫째", "둘째", "셋째"]


# ── 값 변환 ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("value,expected", [
    ("26.07.01", 20260701),
    ("26-07-31", 20260731),
    ("2026-07-01", 20260701),
    ("2026.7.1", 20260701),
    ("99.01.01", 19990101),
    ("", 0),
    (None, 0),
    ("날짜아님", 0),
])
def test_date_int_flex(value, expected):
    assert transform_date_int_flex(value) == expected


def test_unknown_transform_is_rejected_at_startup(tmp_path):
    config = BASE_CONFIG.replace("EVENT_FROM: date_int_flex", "EVENT_FROM: 없는변환기")
    with pytest.raises(ValueError, match="등록되지 않은 transforms"):
        write_mapper(tmp_path, config)


# ── HTML → 평문 ──────────────────────────────────────────────────────────────

def test_html_to_text_keeps_aria_hidden_and_collapsed_text():
    """혜택 텍스트(aria-hidden)와 접힌 약관(display:none)은 지우지 않는다."""
    html = (
        '<div><span aria-hidden="true">가전 구독료 10% 할인</span>'
        '<div style="display:none"><p>중도 해지 시 회수</p></div></div>'
    )
    text = html_to_text(html)
    assert "가전 구독료 10% 할인" in text
    assert "중도 해지 시 회수" in text


def test_html_to_text_drops_scripts():
    """markdown 은 블록 사이에 빈 줄을 넣는다 — 내용 줄만 비교한다."""
    text = html_to_text("<div><p>본문</p><script>track()</script>\n\n<p>다음</p></div>")
    assert "track()" not in text
    assert [line for line in text.splitlines() if line.strip()] == ["본문", "다음"]


def test_html_to_text_handles_empty_input():
    assert html_to_text(None) == ""
    assert html_to_text("   ") == ""


def _pipe_rows(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith("|")]


def _cells(row: str) -> list[str]:
    return [cell.strip() for cell in row.strip("|").split("|")]


def _has_table(text: str, table_format: str) -> bool:
    """table_format 에 맞는 표가 출력에 있는지."""
    return bool("<table" in text) if table_format == "html" else bool(_pipe_rows(text))


def test_html_to_text_renders_table_as_markdown_pipe_table():
    """표는 docling 이 파이프 표로 만든다 — 예전 get_text 평문화는 행/열을 뭉갰다."""
    html = (
        "<div><table><thead><tr><th>구독료</th><th>할인</th></tr></thead>"
        "<tbody><tr><td>5만원 이상</td><td>5,000원</td></tr>"
        "<tr><td>10만원 이상</td><td>10,000원</td></tr></tbody></table></div>"
    )
    rows = _pipe_rows(html_to_text(html, table_format="markdown"))
    # 헤더 + 구분줄 + 본문 2행
    assert len(rows) == 4
    assert _cells(rows[0]) == ["구독료", "할인"]
    assert _cells(rows[2]) == ["5만원 이상", "5,000원"]
    assert _cells(rows[3]) == ["10만원 이상", "10,000원"]


def test_html_to_text_keeps_empty_cell_so_columns_stay_aligned():
    """빈 셀이 소실되면 5열 표가 4줄이 되어 위치로도 복원할 수 없다(원 버그)."""
    html = (
        '<div class="overflow_wrap_scroll"><div class="inner"><table>'
        "<thead><tr><th>전월 이용금액</th><th>1회차</th><th>2회차</th><th>3회차</th><th>합계</th></tr></thead>"
        '<tbody><tr><td>150만원 이상</td><td>20,000원</td><td>20,000원</td><td> </td>'
        "<td>720,000원</td></tr></tbody></table></div></div>"
    )
    rows = _pipe_rows(html_to_text(html, table_format="markdown"))
    assert all(len(row.strip("|").split("|")) == 5 for row in rows)
    assert _cells(rows[-1]) == ["150만원 이상", "20,000원", "20,000원", "", "720,000원"]


@pytest.mark.parametrize("table_format", ["html", "markdown"])
def test_html_to_text_keeps_table_inside_hidden_container(table_format):
    """docling 백엔드는 숨김 컨테이너의 내용을 억제한다 — 상류에서 표시를 떼어 살린다."""
    html = (
        '<div><div style="display:none"><table><tr><th>K</th><th>V</th></tr>'
        "<tr><td>a</td><td>1</td></tr></table></div>"
        '<div aria-hidden="true"><p>혜택 문구</p></div></div>'
    )
    text = html_to_text(html, table_format=table_format)
    assert _has_table(text, table_format)
    assert "혜택 문구" in text


@pytest.mark.parametrize("table_format", ["html", "markdown"])
def test_html_to_text_keeps_table_before_a_later_heading(table_format):
    """docling 은 첫 heading 앞을 furniture 로 본다 — 합성 heading 래퍼가 없으면 표가 사라진다."""
    html = (
        "<div><p>선두 문단</p><table><tr><th>A</th></tr><tr><td>1</td></tr></table>"
        "<h2>중간 제목</h2><p>제목 뒤 문단</p></div>"
    )
    text = html_to_text(html, table_format=table_format)
    assert "선두 문단" in text
    assert _has_table(text, table_format)


# ── output.table_format 반영 ─────────────────────────────────────────────────

_TABLE_HTML = (
    "<div><p>안내</p><table><thead><tr><th>구분</th><th>금액</th></tr></thead>"
    "<tbody><tr><td>snake_case 항목</td><td>13,000원</td></tr>"
    "<tr><td>빈칸행</td><td> </td></tr></tbody></table></div>"
)


def test_html_to_text_table_format_html_emits_html_table():
    """config output.table_format=html → 표만 <table> 로, 본문은 markdown 그대로."""
    text = html_to_text(_TABLE_HTML, table_format="html")
    assert "<table" in text and "</table>" in text
    assert not _pipe_rows(text)
    assert "안내" in text                       # 표 밖 본문은 markdown 유지
    assert "snake_case 항목" in text and "13,000원" in text


def test_html_to_text_table_format_markdown_emits_pipe_table():
    text = html_to_text(_TABLE_HTML, table_format="markdown")
    assert "<table" not in text
    assert len(_pipe_rows(text)) == 4


def test_html_to_text_defaults_to_config_default_table_format():
    """인자를 생략하면 parser config 의 기본값(html)과 같게 동작한다."""
    assert html_to_text(_TABLE_HTML) == html_to_text(_TABLE_HTML, table_format="html")


def test_html_to_text_invalid_table_format_falls_back_to_html(caplog):
    with caplog.at_level("WARNING"):
        text = html_to_text(_TABLE_HTML, table_format="csv")
    assert "<table" in text
    assert "지원하지 않는 table_format" in caplog.text


def test_html_to_text_compact_tables_only_affects_markdown():
    """compact_tables 는 markdown 표의 컬럼 정렬 패딩만 없앤다. html 에는 무관하다."""
    compact = html_to_text(_TABLE_HTML, table_format="markdown", compact_tables=True)
    padded = html_to_text(_TABLE_HTML, table_format="markdown", compact_tables=False)
    assert compact != padded
    # 구분줄로 판별한다 — compact 는 "-" 하나, 패딩본은 컬럼 폭만큼 늘어난다.
    assert _cells(_pipe_rows(compact)[1]) == ["-", "-"]
    assert all(len(cell) > 1 for cell in _cells(_pipe_rows(padded)[1]))
    # 셀 값 자체는 두 모드가 같다
    assert _cells(_pipe_rows(compact)[0]) == _cells(_pipe_rows(padded)[0])
    assert _cells(_pipe_rows(compact)[-1]) == _cells(_pipe_rows(padded)[-1])

    html_compact = html_to_text(_TABLE_HTML, table_format="html", compact_tables=True)
    html_padded = html_to_text(_TABLE_HTML, table_format="html", compact_tables=False)
    assert html_compact == html_padded


def test_build_fields_applies_table_format_to_html_derived_fields(tmp_path):
    """매퍼가 table_format 을 html_text_fields 파생 필드까지 전달한다."""
    payload = {
        "eventList": [
            {
                "ID": "T1", "회사명": "모니모", "제목": "표 테스트",
                "이벤트 시작일": "26.07.01", "이벤트 종료일": "26.07.31",
                "wcmsHtml": {"htmlText": _TABLE_HTML},
            }
        ]
    }
    mapper = write_mapper(tmp_path)
    as_html = mapper.build_fields(payload, "monimo_event", table_format="html")[0]["DETAIL_TEXT"]
    as_md = mapper.build_fields(payload, "monimo_event", table_format="markdown")[0]["DETAIL_TEXT"]
    assert "<table" in as_html and not _pipe_rows(as_html)
    assert "<table" not in as_md and _pipe_rows(as_md)


def test_html_to_text_keeps_table_caption():
    html = "<div><table><caption>표 설명입니다</caption><tr><th>A</th></tr><tr><td>1</td></tr></table></div>"
    assert "표 설명입니다" in html_to_text(html)


def test_html_to_text_has_no_synthetic_heading_prefix():
    """래퍼가 넣은 빈 heading 2개는 출력에 남지 않는다."""
    text = html_to_text("<div><p>본문</p></div>")
    assert text == "본문"


@pytest.mark.parametrize(
    "html",
    ["<div></div>", "<div>   </div>", "<div><script>x</script></div>"],
)
def test_html_to_text_returns_empty_for_contentless_html(html):
    """본문이 없으면 빈 문자열이어야 한다 — 합성 heading 이 새어 나가면 빈 값 판정이 깨진다."""
    assert html_to_text(html) == ""


def test_html_to_text_does_not_escape_entities_or_underscores():
    """LLM 입력용 평문이라 markdown 이스케이프는 노이즈다."""
    text = html_to_text("<div><p>A &amp; B &lt;태그&gt; snake_case</p></div>")
    assert "&amp;" not in text and "&lt;" not in text
    assert "snake_case" in text


@pytest.mark.parametrize("table_format", ["html", "markdown", "auto"])
def test_html_to_text_drops_hyperlink_url_keeps_label(table_format):
    """`<a href>` 는 라벨만 남는다 - URL 은 검색에 기여하지 않고 청크 예산만 먹는다.

    실측(상품카드): 라벨이 도메인 자체일 때 `[www.samsungfire.com](http://www.samsungfire.com/)`
    처럼 같은 문자열이 두 번 실렸다.
    """
    html = (
        '<div><p>애니카랜드 <a href="http://www.samsungfire.com/">www.samsungfire.com</a>'
        ' , 1588-5114</p></div>'
    )
    text = html_to_text(html, table_format=table_format)
    assert "](http" not in text
    assert "http://www.samsungfire.com/" not in text
    assert "애니카랜드 www.samsungfire.com , 1588-5114" in text


def test_html_to_text_drops_image_placeholder_but_keeps_alt():
    text = html_to_text('<div><img src="a.png" alt="대체문구"></div>')
    assert "<!-- image -->" not in text
    assert "대체문구" in text


# ── 레코드 매핑 ─────────────────────────────────────────────────────────────

def test_build_fields_maps_targets_and_skips_missing_required(tmp_path, caplog):
    mapper = write_mapper(tmp_path)
    fields_list = mapper.build_fields(SAMPLE_PAYLOAD, "monimo_event")

    assert len(fields_list) == 1  # 제목이 빈 두 번째 레코드는 skip
    fields = fields_list[0]
    assert fields["TITLE"] == "무신사 블랙 프라이데이 혜택 받기"
    assert fields["EVENT_ID"] == "CM26061182"
    assert fields["EVENT_FROM"] == 20260701
    assert fields["EVENT_TO"] == 20260731
    assert fields["DETAIL_HTML"] == "<div><p>이벤트 상세 내용</p></div>"
    assert fields["DETAIL_TEXT"] == "이벤트 상세 내용"
    assert fields["KEYWORD"] is None  # nulls 로 스키마 고정
    assert fields["doc_type"] == "monimo_event"


def test_build_fields_logs_skipped_count(tmp_path, caplog):
    mapper = write_mapper(tmp_path)
    with caplog.at_level("WARNING"):
        mapper.build_fields(SAMPLE_PAYLOAD, "monimo_event")
    assert "skipped 1/2 records" in caplog.text


def test_defaults_and_constants_apply(tmp_path):
    config = BASE_CONFIG + textwrap.dedent("""
        defaults:
          KEYWORD: 기본키워드
        constants:
          SOURCE: monimo
    """)
    mapper = write_mapper(tmp_path, config)
    fields = mapper.build_fields(SAMPLE_PAYLOAD, "monimo_event")[0]
    assert fields["KEYWORD"] == "기본키워드"
    assert fields["SOURCE"] == "monimo"


def test_missing_records_key_raises(tmp_path):
    mapper = write_mapper(tmp_path)
    with pytest.raises(ValueError, match="찾지 못했습니다"):
        mapper.build_fields({"other": []}, "monimo_event")


def test_missing_records_key_skip_policy(tmp_path):
    mapper = write_mapper(tmp_path, BASE_CONFIG + "missing_policy: skip\n")
    assert mapper.build_fields({"other": []}, "monimo_event") == []


# ── parse-format 출력 ────────────────────────────────────────────────────────

def test_to_parse_format_emits_row_elements(tmp_path):
    mapper = write_mapper(tmp_path)
    fields_list = mapper.build_fields(SAMPLE_PAYLOAD, "monimo_event")
    result = mapper.to_parse_format(fields_list, "monimo_event")

    assert result["usage"] == {"pages": 1}
    assert result["metadata"] == {"doc_type": "monimo_event"}
    element = result["elements"][0]
    # 청커의 행 기반 경로가 소비하는 계약 — category 를 새로 만들지 않는다.
    assert element["category"] == "custom_fields_row"
    assert element["page"] == 1
    assert element["splittable"] is True
    assert element["chunk_prefix"] == "무신사 블랙 프라이데이 혜택 받기"
    # text_fields 선언 순서대로 개행 결합
    assert element["content"] == "무신사 블랙 프라이데이 혜택 받기\n이벤트 상세 내용"
    assert element["metadata"]["EVENT_TO"] == 20260731


def test_split_false_omits_splittable_flag(tmp_path):
    mapper = write_mapper(tmp_path, BASE_CONFIG.replace("split: true", "split: false"))
    fields_list = mapper.build_fields(SAMPLE_PAYLOAD, "monimo_event")
    element = mapper.to_parse_format(fields_list, "monimo_event")["elements"][0]
    assert "splittable" not in element
    assert "chunk_prefix" not in element
    # 접두는 본문 맨 앞으로 이동하므로 분할하지 않는 설정에서는 아예 무효화한다.
    assert mapper.chunk_prefix_fields == []


def test_text_fields_skip_empty_values(tmp_path):
    mapper = write_mapper(tmp_path)
    assert mapper.build_text({"TITLE": "제목만", "DETAIL_TEXT": ""}) == "제목만"


def test_records_without_text_are_excluded(tmp_path, caplog):
    """본문이 빈 레코드는 element 로 내보내지 않는다(빈 text 벡터 적재 방지)."""
    config = BASE_CONFIG.replace("text_fields: [TITLE, DETAIL_TEXT]", "text_fields: [CONTENT_HASH]")
    config = config.replace("chunk_prefix_fields: [TITLE]\n", "")
    mapper = write_mapper(tmp_path, config)
    fields_list = [
        {"TITLE": "요약 성공", "CONTENT_HASH": "요약본문"},
        {"TITLE": "요약 실패", "CONTENT_HASH": None},   # LLM 실패 → on_error=null
    ]
    with caplog.at_level("WARNING"):
        result = mapper.to_parse_format(fields_list, "monimo_event")

    assert len(result["elements"]) == 1
    assert result["elements"][0]["metadata"]["TITLE"] == "요약 성공"
    # id/page 는 제외 후 기준으로 0/1 부터 다시 매겨진다
    assert result["elements"][0]["id"] == 0
    assert result["elements"][0]["page"] == 1
    assert "본문이 빈 레코드 1/2건" in caplog.text


# ── doc_type 매칭 / 설정 검증 ────────────────────────────────────────────────

def test_matches_only_configured_doc_type(tmp_path):
    mapper = write_mapper(tmp_path)
    assert mapper.matches("monimo_event") is True
    assert mapper.matches("card") is False


def test_wildcard_when_doc_type_unset(tmp_path):
    mapper = write_mapper(tmp_path, doc_type=None)
    assert mapper.matches("아무거나") is True


def test_key_map_is_required(tmp_path):
    with pytest.raises(ValueError, match="key_map"):
        write_mapper(tmp_path, "records: eventList\ntext_fields: [TITLE]\n")


def test_text_fields_is_required(tmp_path):
    with pytest.raises(ValueError, match="text_fields"):
        write_mapper(tmp_path, "records: eventList\nkey_map:\n  TITLE: [제목]\n")


# ── llm_fields 선언 ─────────────────────────────────────────────────────────

INLINE_LLM_CONFIG = textwrap.dedent("""
    llm_fields:
      - output_fields: [CONTENT_HASH]
        input_fields: [TITLE, DETAIL_TEXT]
        concurrency: 2
        url: "http://llm.invalid/v1/chat/completions"
        model: model
        max_tokens: 500
        system_prompt: "너는 요약 전문가다."
        user_prompt: |
          <event>
          {{raw_text}}
          </event>
""")


def test_llm_field_spec_parsed(tmp_path):
    spec = write_mapper(tmp_path, BASE_CONFIG + INLINE_LLM_CONFIG).llm_field_specs[0]
    assert spec.output_fields == ["CONTENT_HASH"]
    assert spec.concurrency == 2
    assert spec.on_error == "null"
    # 입력이 2개 이상이면 필드명을 붙여 모델이 값의 의미를 알 수 있게 한다.
    assert spec.build_input_text({"TITLE": "제목", "DETAIL_TEXT": "본문"}) == (
        "TITLE: 제목\n\nDETAIL_TEXT: 본문"
    )


def test_llm_field_enricher_kwargs_passthrough(tmp_path):
    """스펙 전용 키만 빠지고 나머지는 그대로 enricher 생성자로 넘어간다."""
    spec = write_mapper(tmp_path, BASE_CONFIG + INLINE_LLM_CONFIG).llm_field_specs[0]

    assert set(spec.enricher_kwargs) == {
        "output_fields", "url", "model", "max_tokens", "system_prompt", "user_prompt",
    }
    # output_fields 는 양쪽이 다 쓰므로 남아야 한다(enricher 응답 정규화용).
    assert spec.enricher_kwargs["output_fields"] == ["CONTENT_HASH"]
    assert "{{raw_text}}" in spec.enricher_kwargs["user_prompt"]


def test_llm_field_inline_config_builds_real_enricher(tmp_path):
    """인라인 설정만으로 실제 CustomFieldsEnricher 가 만들어진다(LLM 호출은 하지 않음)."""
    from genon.preprocessor.facade.enrichment.custom_fields_enricher import CustomFieldsEnricher

    spec = write_mapper(tmp_path, BASE_CONFIG + INLINE_LLM_CONFIG).llm_field_specs[0]
    enricher = CustomFieldsEnricher(resource_path=str(tmp_path), **spec.enricher_kwargs)

    assert enricher.is_configured is True
    assert enricher._system_prompt == "너는 요약 전문가다."
    assert "{{raw_text}}" in enricher._user_prompt
    assert enricher._max_tokens == 500


def test_llm_field_config_file_still_supported(tmp_path):
    """프롬프트를 파일로 뺀 기존 방식(경로 A 스타일)도 그대로 동작한다."""
    config = BASE_CONFIG + textwrap.dedent("""
        llm_fields:
          - config_file: custom_field_summary.yaml
            output_fields: [CONTENT_HASH]
            input_fields: [DETAIL_TEXT]
    """)
    spec = write_mapper(tmp_path, config).llm_field_specs[0]
    assert spec.enricher_kwargs["config_file"] == "custom_field_summary.yaml"
    assert spec.label == "custom_field_summary.yaml"
    # 입력이 하나면 필드명 라벨 없이 값만 넣는다.
    assert spec.build_input_text({"DETAIL_TEXT": "본문"}) == "본문"


def test_llm_field_requires_config_file_or_url(tmp_path):
    """연결 정보가 아예 없는 설정은 기동 시에 잡는다."""
    config = BASE_CONFIG + textwrap.dedent("""
        llm_fields:
          - output_fields: [CONTENT_HASH]
            input_fields: [TITLE]
    """)
    with pytest.raises(ValueError, match="config_file 또는 url"):
        write_mapper(tmp_path, config)


# ── 빌더(설정 라우팅) ────────────────────────────────────────────────────────

def test_builder_selects_only_json_mapping_configs(tmp_path):
    path = tmp_path / "custom_field_json.yaml"
    path.write_text(textwrap.dedent(BASE_CONFIG), encoding="utf-8")
    configs = [
        {"extractor": "llm", "config_file": "custom_field_card.yaml"},
        {"extractor": "tabular_mapping", "config_file": "custom_field_faq.yaml"},
        {
            "extractor": "json_mapping",
            "config_file": path.name,
            "resource_path": str(tmp_path),
            "doc_type": "monimo_event",
        },
    ]
    mappers = build_json_records_mappers(configs)
    assert len(mappers) == 1
    assert mappers[0].doc_types == ("monimo_event",)


# ── 출고 설정 파일 자체 검증 ─────────────────────────────────────────────────

@pytest.mark.parametrize("resource_dir", ["resource", "resource_dev"])
def test_shipped_monimo_event_config_loads(resource_dir):
    """출고 yaml 이 실제로 매퍼로 컴파일되는지(오타/키 누락 방지)."""
    from pathlib import Path

    base = Path(__file__).resolve().parents[2] / resource_dir
    mapper = JsonRecordsMapper(
        config_file="custom_field_monimo_event.yaml",
        resource_path=str(base),
        doc_type="monimo_event",
        extractor="json_mapping",
    )
    assert mapper.records_key == "eventList"
    # LLM 요약 설정이 비활성화된 현재 출고 설정은 평문화한 상세 원문을 검색 본문으로 쓴다.
    # TB_* 의 CONTENT_HASH 는 RAW(32) 원문 검증 해시이므로 임베딩 입력이 아니다.
    assert mapper.text_fields == ["TITLE", "DETAIL_TEXT"]
    assert mapper.chunk_prefix_fields == ["TITLE"]
    assert "CONTENT_HASH" not in mapper.key_map

    # llm_fields 는 모델서빙이 배정되기 전까지 주석으로 내려둘 수 있다(현재 resource/ 가 그 상태).
    # 그때는 SUMMARY_TEXT 가 비고 text_fields 의 TITLE 만 본문에 남는다 — yaml 주석이 그 상황을
    # 전제로 제목을 앞에 두고 있고, 로더도 경고만 남기고 통과한다
    # (test_custom_fields_routing.test_unproducible_text_field_warns_but_loads).
    # 선언되어 있다면 자족해야 한다 — LLM 연결·프롬프트가 이 파일 안에 인라인되어 있고
    # 참조하는 외부 파일이 없다(파생 yaml/프롬프트 md 를 다시 만들면 여기서 깨진다).
    for spec in mapper.llm_field_specs:
        assert spec.output_fields == ["SUMMARY_TEXT"]
        assert "config_file" not in spec.enricher_kwargs
        for key in ("url", "model", "system_prompt", "user_prompt"):
            assert spec.enricher_kwargs.get(key), f"{key} 가 인라인되어 있어야 합니다"
        assert "{{raw_text}}" in spec.enricher_kwargs["user_prompt"]
        # 프롬프트가 내놓는 JSON 키와 output_fields 가 어긋나면 값이 조용히 빈다.
        assert "SUMMARY_TEXT" in spec.enricher_kwargs["system_prompt"]
        assert "CONTENT_HASH" not in spec.enricher_kwargs["system_prompt"]


@pytest.mark.parametrize("resource_dir", ["resource", "resource_dev"])
def test_shipped_monimo_event_config_maps_real_payload_schema(resource_dir):
    """출고 yaml 이 실 payload(영문 camelCase 키) 스키마를 매핑하는지.

    원천 화면에서 확인한 키(cmpId / evtTodayMainCopy / evtHeaderTopTitle / evtPtrmStrtDt …)는
    협의용 한글 키와 표기가 다르고 회사명 필드가 없다. 별칭이 빠지면 TITLE 이 null 이 되어
    전 레코드가 조용히 skip 되므로(그러면 청크 0건) 여기서 고정한다.
    샘플 파일을 그대로 읽어 config 와 픽스처가 따로 흘러가지 않게 한다.
    """
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    payload = json.loads(
        (root / "sample_files/monimo/monimo_event_real_sample.json").read_text(encoding="utf-8")
    )
    mapper = JsonRecordsMapper(
        config_file="custom_field_monimo_event.yaml",
        resource_path=str(root / resource_dir),
        doc_type="monimo_event",
        extractor="json_mapping",
    )

    fields_list = mapper.build_fields(payload, "monimo_event")

    # 3번째 레코드는 어느 설정에서든 제목 계열 키가 없어 required(TITLE) 로 skip 된다.
    # 2번째는 evtTodayMainCopy 가 없어 evtHeaderTopTitle 별칭이 있어야 잡힌다 — 그 별칭을
    # 선언하지 않은 설정에서는 함께 skip 되는 것이 설정대로의 동작이므로 기대값을 설정에서 뽑는다.
    has_header_fallback = bool(
        set(mapper.key_map["TITLE"]) & {"evtHeaderTopTitle", "evtHeaderTitle"}
    )
    assert len(fields_list) == (2 if has_header_fallback else 1)

    first = fields_list[0]
    assert first["BIZ_ID"] == "M261106191"                 # cmpId
    assert first["TITLE"] == "하이마트 구독을 가볍게 매월 최대2만원 까지 혜택"   # evtTodayMainCopy 우선
    assert first["EVENT_FROM"] == 20260710                 # evtPtrmStrtDt (8자리 압축)
    assert first["EVENT_TO"] == 20261111                   # evtPtrmEndDt
    # 실 payload 에 회사명이 없어도 defaults→value_map 으로 표준 코드가 채워진다.
    assert first["GROUP_C"] == "IFP"
    # 노출 게이트를 constants/defaults 로 고정한 설정만 값이 실린다. 잠정 보류(주석)한 설정은
    # 필드를 아예 내보내지 않고 TB 기본값('N')에 맡긴다 — 둘 다 적재 가능한 상태다.
    if "SEARCHABLE_YN" in mapper.constants or "SEARCHABLE_YN" in mapper.defaults:
        assert first["SEARCHABLE_YN"] == "N"
    else:
        assert "SEARCHABLE_YN" not in first
    # 상세 HTML 은 원문 그대로, 평문 파생에는 aria-hidden/접힌 약관 텍스트가 남아야 한다.
    assert first["DETAIL_HTML"].startswith("<div class=\"box940 mt0\">")
    assert "가전 구독료 10% 결제일할인" in first["DETAIL_TEXT"]
    assert "중도 해지 시 회수됩니다" in first["DETAIL_TEXT"]
    assert "trackEvent" not in first["DETAIL_TEXT"]

    # evtTodayMainCopy 가 없으면 evtHeaderTopTitle 로 내려가고, 시작일이 없으면 0 이다.
    if has_header_fallback:
        second = fields_list[1]
        assert second["TITLE"] == "여름 휴가 주유 캐시백"
        assert second["EVENT_FROM"] == 0
        assert second["EVENT_TO"] == 20260831


@pytest.mark.unit
@pytest.mark.parametrize("raw,expected", [
    # 모니모 원천이 실제로 쓰는 압축 표기. parse_created_date 는 `\\d{4}` 를 연도로만 읽어
    # "260731"→26070101, "20260713"→20260101 로 뭉갰다. 종료일이 그렇게 들어가면
    # 기간 게이트가 영원히 열리므로 회귀를 막는다.
    ("260701", 20260701),        # 관심소식/이벤트 YYMMDD
    ("260731", 20260731),
    (260701, 20260701),          # 엑셀이 숫자로 준 경우
    ("20260713", 20260713),      # 링크 YYYYMMDD
    (20260713, 20260713),
    ("26.07.01", 20260701),      # 기존 동작 유지
    ("2026-07-13", 20260713),
    ("202699", 20260101),        # 날짜가 아니면 압축 표기로 보지 않고 기존 경로(연도만)
    ("", 0),
    (None, 0),
])
def test_date_int_flex_handles_compact_forms(raw, expected):
    assert transform_date_int_flex(raw) == expected


# ── 설정 형 오류 진단(검증 순서) ────────────────────────────────────────────

def test_shape_error_reports_key_name_before_consumption(tmp_path):
    """`transforms` 를 맵이 아닌 값으로 쓰면 키 이름과 파일명이 담긴 ValueError 가 나야 한다.

    검증을 키 소비 뒤로 미루면 `'list' object has no attribute 'items'` 라는
    AttributeError 가 먼저 나서 어느 파일 어느 키가 틀렸는지 알 수 없었다(tabular 와 순서 불일치).
    """
    with pytest.raises(ValueError) as exc:
        write_mapper(tmp_path, """
            key_map:
              TITLE: [title]
            text_fields: [TITLE]
            transforms:
              - date_int_flex
        """)
    message = str(exc.value)
    assert "transforms" in message
    assert "custom_field_json.yaml" in message


# ── row_merge (tabular 와 공유) ─────────────────────────────────────────────

def test_row_merge_folds_split_records_before_value_pipeline(tmp_path):
    """원천이 값 하나를 여러 레코드에 쪼개 보내는 스키마를 한 건으로 접는다.

    병합은 값 파이프라인 **전에** 돌아야 한다 — 조각마다 text_from 이 먼저 돌면 잘린 JSON
    조각을 각각 평문화하게 되어 복원이 불가능하다.
    """
    whole = '{"종목명": "삼성전자", "투자의견": "매수"}'
    mapper = write_mapper(tmp_path, """
        key_map:
          REGT_NO:     [regtNo]
          LINE_NO:     [lineNo]
          DETAIL_JSON: [detailDesc]
        row_merge:
          group_by:  [REGT_NO]
          order_by:  LINE_NO
          concat:    [DETAIL_JSON]
          separator: ""
        text_from:
          DETAIL_TEXT: DETAIL_JSON
        text_fields: [DETAIL_TEXT]
    """, doc_type="stock")
    rows = mapper.build_fields([
        {"regtNo": "R1", "lineNo": 1, "detailDesc": whole[:12]},
        {"regtNo": "R1", "lineNo": 2, "detailDesc": whole[12:]},
        {"regtNo": "R2", "lineNo": 1, "detailDesc": '{"종목명": "SK하이닉스"}'},
    ], "stock")

    assert len(rows) == 2
    assert rows[0]["DETAIL_JSON"] == whole
    assert "삼성전자" in str(rows[0]["DETAIL_TEXT"])
    assert "SK하이닉스" in str(rows[1]["DETAIL_JSON"])


def test_row_merge_only_folds_consecutive_runs(tmp_path):
    """떨어진 동일 키는 합치지 않는다 — 등록번호 재사용 시 다른 건이 뭉개지는 것을 막는다."""
    mapper = write_mapper(tmp_path, """
        key_map:
          REGT_NO: [regtNo]
          BODY:    [body]
        row_merge:
          group_by: [REGT_NO]
          concat:   [BODY]
        text_fields: [BODY]
    """, doc_type="stock")
    rows = mapper.build_fields([
        {"regtNo": "R1", "body": "a"},
        {"regtNo": "R2", "body": "b"},
        {"regtNo": "R1", "body": "c"},
    ], "stock")
    assert [r["BODY"] for r in rows] == ["a", "b", "c"]
