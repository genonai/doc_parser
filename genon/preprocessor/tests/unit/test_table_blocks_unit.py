"""표 블록 검출·표기형태 변환 단위테스트(#360).

행/텍스트 청킹 경로는 `TableItem` 이 없어 청크 텍스트만 보고 표를 다뤄야 한다. 여기서
고정하는 계약은 세 가지다.

1. 표를 표로 본다(그리고 표가 아닌 것을 표로 보지 않는다).
2. 표기형태를 바꿔도 셀 값이 사라지거나 늘지 않는다.
3. 바꿀 수 없으면 원문 표기를 유지한다 — 내용이 사라지는 일은 없다.
"""

import re

import pytest

from genon.preprocessor.facade.chunking import table_blocks as tb

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
