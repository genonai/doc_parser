"""표 블록 검출·표기형태 변환 단위테스트(#360).

행/텍스트 청킹 경로는 `TableItem` 이 없어 청크 텍스트만 보고 표를 다뤄야 한다. 여기서
고정하는 계약은 세 가지다.

1. 표를 표로 본다(그리고 표가 아닌 것을 표로 보지 않는다).
2. 표기형태를 바꿔도 셀 값이 사라지거나 늘지 않는다.
3. 바꿀 수 없으면 원문 표기를 유지한다 — 내용이 사라지는 일은 없다.
"""

import re
from pathlib import Path

import pytest

from genon.preprocessor.processing.chunking import table_blocks as tb

pytestmark = pytest.mark.unit

HTML_TABLE = (
    '<table><caption>연회비</caption><tbody>'
    '<tr><th>구분</th><th>국내전용</th><th>해외겸용</th></tr>'
    '<tr><th>총 연회비</th><td>18,000</td><td>20,000</td></tr>'
    '</tbody></table>'
)
MD_TABLE = "| 구분 | 국내전용 |\n| - | - |\n| 총 연회비 | 18,000 |"


def test_html_block_boundaries_are_the_table_only():
    text = f"앞 본문\n{HTML_TABLE}\n뒤 본문"
    blocks = tb.find_blocks(text)
    assert [b.kind for b in blocks] == ["html"]
    assert blocks[0].text == HTML_TABLE
    assert text[blocks[0].start:blocks[0].end] == HTML_TABLE


def test_markdown_block_needs_a_delimiter_row():
    """구분선 없는 파이프 줄은 표가 아니다. 평문의 `|` 를 표로 삼으면 본문이 잘린다."""
    assert tb.find_blocks("메뉴는 카드 | 대출 | 보험 입니다") == []
    assert [b.kind for b in tb.find_blocks(f"앞\n{MD_TABLE}\n뒤")] == ["markdown"]


def test_code_fence_pipes_are_not_tables():
    fenced = "```\n| a | b |\n| - | - |\n| 1 | 2 |\n```"
    assert tb.find_blocks(fenced) == []


def test_pipes_inside_html_table_are_not_a_second_block():
    """auto 는 표마다 표기형태가 갈린다 — html 표 안의 파이프를 markdown 표로 세면 안 된다."""
    html = ('<table><tbody><tr><td>| - |</td><td>| 1 |</td></tr>'
            '<tr><td>| - |</td><td>| 2 |</td></tr></tbody></table>')
    assert [b.kind for b in tb.find_blocks(html)] == ["html"]


def test_mixed_notations_in_one_text_are_both_found():
    text = f"{HTML_TABLE}\n\n{MD_TABLE}"
    assert [b.kind for b in tb.find_blocks(text)] == ["html", "markdown"]


def test_nested_table_reports_outer_boundary_only():
    nested = ('<table><tbody><tr><td>'
              '<table><tbody><tr><td>안쪽</td></tr></tbody></table>'
              '</td></tr></tbody></table>')
    blocks = tb.find_blocks(nested)
    assert len(blocks) == 1
    assert blocks[0].text == nested


def test_unclosed_table_is_not_treated_as_a_table():
    """닫는 태그가 없는 조각을 표로 보면 뒤 본문을 통째로 삼킨다."""
    assert tb.find_blocks("<table><tr><td>값</td></tr>\n뒤 본문") == []


def test_html_to_markdown_keeps_every_cell_value():
    out = tb.renotate(f"앞\n{HTML_TABLE}\n뒤", "markdown")
    assert "<table" not in out
    for value in ("구분", "국내전용", "해외겸용", "총 연회비", "18,000", "20,000"):
        assert value in out
    # markdown 표로 읽히려면 구분선이 있어야 한다.
    assert any(re.fullmatch(r"\|(\s*-+\s*\|)+", line.strip()) for line in out.splitlines())
    assert out.startswith("앞\n") and out.endswith("\n뒤")


def test_markdown_to_html_keeps_every_cell_value():
    out = tb.renotate(f"앞\n{MD_TABLE}\n뒤", "html")
    assert "<table" in out and "</table>" in out
    for value in ("구분", "국내전용", "총 연회비", "18,000"):
        assert value in out
    assert "| - |" not in out


def test_compact_tables_controls_the_delimiter_width():
    def delimiter(text: str) -> str:
        return next(line.strip() for line in text.splitlines()
                    if re.fullmatch(r"\|(\s*-+\s*\|)+", line.strip()))

    assert delimiter(tb.renotate(HTML_TABLE, "markdown", compact_tables=True)) \
        == "| - | - | - |"
    assert delimiter(tb.renotate(HTML_TABLE, "markdown", compact_tables=False)) \
        == "| --- | --- | --- |"


def test_same_notation_is_left_untouched():
    assert tb.renotate(HTML_TABLE, "html") == HTML_TABLE
    assert tb.renotate(MD_TABLE, "markdown") == MD_TABLE


def test_text_without_table_is_returned_as_is():
    assert tb.renotate("표가 없는 본문", "markdown") == "표가 없는 본문"
    assert tb.renotate("", "html") == ""


def test_colspan_value_is_repeated_across_covered_columns():
    """markdown 에는 병합이 없다. 값을 버리는 대신 피복 위치마다 복제한다."""
    html = ('<table><tbody><tr><th colspan="2">적립 한도</th></tr>'
            '<tr><td>국내</td><td>1만</td></tr></tbody></table>')
    out = tb.renotate(html, "markdown")
    header = out.splitlines()[0]
    assert header.count("적립 한도") == 2


def test_rowspan_value_is_repeated_across_covered_rows():
    html = ('<table><tbody>'
            '<tr><td rowspan="2">공통</td><td>A</td></tr>'
            '<tr><td>B</td></tr></tbody></table>')
    out = tb.renotate(html, "markdown")
    assert out.count("공통") == 2
    assert "A" in out and "B" in out


def test_cell_pipe_is_escaped_when_rendering_markdown():
    """셀 값의 `|` 를 그대로 두면 컬럼 수가 어긋나 표가 깨진다."""
    html = '<table><tbody><tr><td>가 | 나</td><td>값</td></tr></tbody></table>'
    out = tb.renotate(html, "markdown")
    first = out.splitlines()[0]
    assert first.count("|") == 3 + 1        # 셀 경계 3개 + 이스케이프한 파이프 1개
    assert "\\|" in first


def test_split_at_tables_isolates_the_table():
    pieces = tb.split_at_tables(f"앞 본문\n{HTML_TABLE}\n뒤 본문")
    assert pieces == ["앞 본문", HTML_TABLE, "뒤 본문"]


def test_split_at_tables_returns_one_piece_without_table():
    assert tb.split_at_tables("표 없는 본문") == ["표 없는 본문"]


def test_split_at_tables_loses_no_cell_value():
    text = f"안내\n{MD_TABLE}\n주석\n{HTML_TABLE}"
    joined = "\n".join(tb.split_at_tables(text))
    for value in ("안내", "주석", "총 연회비", "18,000", "해외겸용", "20,000"):
        assert value in joined


def test_has_table_matches_block_detection():
    assert tb.has_table(HTML_TABLE) is True
    assert tb.has_table(MD_TABLE) is True
    assert tb.has_table("표 없는 본문") is False


def test_expand_elements_reattaches_chunk_prefix_to_every_piece():
    prefix = "[상품 문서] 연회비\n상품코드: AAP1344"
    element = {
        "category": "custom_fields_row",
        "content": f"{prefix}\n안내 문구\n{HTML_TABLE}",
        "chunk_prefix": prefix,
        "metadata": {"PRODUCT_C": "AAP1344"},
        "splittable": True,
    }
    pieces = tb.expand_elements([element])
    assert len(pieces) == 2
    assert all(piece["content"].startswith(prefix) for piece in pieces)
    # metadata 는 조각마다 그대로 복제된다(같은 레코드의 조각임을 적재 측이 식별한다).
    assert all(piece["metadata"] == {"PRODUCT_C": "AAP1344"} for piece in pieces)
    assert HTML_TABLE in pieces[1]["content"]
    assert "안내 문구" in pieces[0]["content"]


def test_expand_elements_leaves_table_free_rows_alone():
    element = {"category": "tabular_row", "content": "질문\n답변", "metadata": {}}
    assert tb.expand_elements([element]) == [element]


def test_table_search_description_stays_with_its_table():
    """`[표 검색 설명]` 은 그 표의 것이다. 떼어 놓으면 표 청크가 설명을, 설명 청크가 표를 잃는다."""
    body = ("연회비 안내 문단입니다.\n"
            "[표 검색 설명]\n연회비 비교 표로, 국내전용과 해외겸용 금액을 제시합니다.\n"
            f"{HTML_TABLE}")
    pieces = tb.split_at_tables(body)
    assert len(pieces) == 2
    assert "[표 검색 설명]" not in pieces[0]
    assert pieces[1].startswith("[표 검색 설명]")
    assert HTML_TABLE in pieces[1]


def test_isolated_pieces_carry_the_section_heading():
    """표만 담긴 조각도 어느 섹션의 표인지 알아야 한다(크기 분할과 같은 규칙)."""
    element = {"category": "custom_fields_row", "metadata": {},
               "content": f"## 일별 시세\n표 앞 설명 문장.\n{MD_TABLE}"}
    pieces = tb.expand_elements([element])
    assert len(pieces) == 2
    assert pieces[0]["content"].startswith("## 일별 시세")
    assert pieces[1]["content"].startswith("## 일별 시세 (이어서)")


# ── 표 바로 위 섹션 제목은 표와 같은 조각에 남는다 ──────────────────────────
# 고객센터(화재) 원천의 `<p><b>ㅁ 소지품(보상가능)</b></p>` 처럼, 표를 설명하는 유일한
# 문장이 표와 갈라진 청크로 나오던 결함을 고정한다.

def test_bold_section_title_above_table_stays_with_the_table():
    """줄 전체가 굵은 글씨인 제목은 표 조각의 선두로 옮긴다."""
    body = f"휴대품\n\n**소지품(보상가능)**\n{MD_TABLE}"
    pieces = tb.split_at_tables(body)
    assert len(pieces) == 2
    assert pieces[0] == "휴대품"
    assert pieces[1].startswith("**소지품(보상가능)**")
    assert MD_TABLE in pieces[1]


def test_section_title_only_body_leaves_no_orphan_piece():
    """제목이 표 앞 본문의 전부면 제목만 담긴 조각 자체가 생기지 않는다."""
    pieces = tb.split_at_tables(f"**소지품(보상가능)**\n{HTML_TABLE}")
    assert len(pieces) == 1
    assert pieces[0].startswith("**소지품(보상가능)**")
    assert HTML_TABLE in pieces[0]


def test_markdown_heading_above_table_stays_with_the_table():
    pieces = tb.split_at_tables(f"앞 문단입니다.\n## 일별 시세\n{MD_TABLE}")
    assert pieces[0] == "앞 문단입니다."
    assert pieces[1].startswith("## 일별 시세")


def test_prose_line_above_table_is_not_taken_as_a_title():
    """굵은 글씨가 섞인 본문 문장은 제목이 아니다 — 표 조각으로 끌어오지 않는다."""
    body = f"**국내전용** 과 **해외겸용** 의 연회비는 아래와 같습니다.\n{MD_TABLE}"
    pieces = tb.split_at_tables(body)
    assert len(pieces) == 2
    assert pieces[0].startswith("**국내전용**")
    assert pieces[1] == MD_TABLE


def test_long_bold_line_above_table_is_not_taken_as_a_title():
    """제목이라기엔 긴 굵은 글씨 문단은 그대로 본문 조각에 둔다."""
    body = f"**{'가' * 90}**\n{MD_TABLE}"
    pieces = tb.split_at_tables(body)
    assert len(pieces) == 2
    assert pieces[1] == MD_TABLE


def test_section_title_precedes_the_table_search_description():
    """제목·설명이 함께 있으면 문서 순서대로 제목 → 설명 → 표."""
    body = ("연회비 안내 문단입니다.\n"
            "**연회비 비교**\n"
            "[표 검색 설명]\n연회비 비교 표입니다.\n"
            f"{HTML_TABLE}")
    pieces = tb.split_at_tables(body)
    assert len(pieces) == 2
    assert pieces[0] == "연회비 안내 문단입니다."
    assert pieces[1].startswith("**연회비 비교**\n[표 검색 설명]")
    assert HTML_TABLE in pieces[1]


def test_expand_elements_keeps_the_title_with_the_table_row():
    """행 경로(custom_fields)에서도 같은 규칙이 성립한다 — chunk_prefix 는 조각마다 재부착."""
    prefix = "카테고리: 자동차_담보\n제목: [보상콜] 휴대품과 소지품 보상 여부"
    element = {
        "category": "custom_fields_row",
        "content": f"{prefix}\n휴대품\n\n**소지품(보상가능)**\n{MD_TABLE}",
        "chunk_prefix": prefix,
        "metadata": {},
    }
    pieces = tb.expand_elements([element])
    assert len(pieces) == 2
    assert pieces[0]["content"] == f"{prefix}\n휴대품"
    assert pieces[1]["content"].startswith(f"{prefix}\n**소지품(보상가능)**")
    assert MD_TABLE in pieces[1]["content"]


def test_cs_ssf_sample_keeps_each_section_title_with_its_table():
    """원천 샘플(고객센터 화재)에서 `ㅁ …` 제목 두 개가 각자의 표 조각에 실린다.

    캡쳐로 보고된 결함 그대로의 문서다 — 탭 상자 아래 `<p><b>ㅁ 소지품(보상가능) 과
    휴대품(보상불가)</b></p>` 와 표, Q&A 문단, 그리고 `<p><b>ㅁ 피해물이 상품인 경우
    보상 기준</b></p>` 와 두 번째 표. 원천 → 평문화 → 표 분리까지 실제 경로로 확인한다.
    """
    from genon.preprocessor.converters.delimited_text import parse_spec, read_records
    from genon.preprocessor.processing.enrichment.json_records import html_to_text

    sample = (Path(__file__).resolve().parents[2]
              / "sample_files" / "monimo" / "monimo_cs_ssf_table_title_sample.dtms")
    spec = parse_spec({"separator": "|@|",
                       "columns": ["대분류", "중분류", "소분류", "제목", "내용"]})
    records = read_records(str(sample), spec)
    assert len(records) == 1

    text = html_to_text(records[0]["내용"], table_format="markdown")
    pieces = tb.split_at_tables(text)
    table_pieces = [p for p in pieces if tb.has_table(p)]
    assert len(table_pieces) == 2
    assert table_pieces[0].startswith("**ㅁ 소지품")
    assert "소지품(보상가능)" in table_pieces[0].replace(" ", "")
    assert table_pieces[1].startswith("**ㅁ 피해물이 상품인 경우 보상 기준**")
    # 제목만 담긴 조각이 남지 않는다 — 탭 이름 줄과 Q&A 문단만 표 밖에 있다.
    assert all("ㅁ 소지품" not in p and "ㅁ 피해물이" not in p
               for p in pieces if p not in table_pieces)
