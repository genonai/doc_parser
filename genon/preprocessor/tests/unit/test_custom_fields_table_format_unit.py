"""custom_fields(html_text_fields) 경로의 표 형식 선택 테스트.

파서가 docling 을 거치지 않고 HTML 필드를 평문으로 바꾸는 경로다. 청커가 쓰는 판정과
같은 함수를 써야 같은 표가 파생 필드와 청크에서 다른 모양으로 나가지 않는다.
"""

import pytest

from genon.preprocessor.facade.enrichment.json_records import (
    html_to_text,
    normalize_table_format,
)

SIMPLE = ("<table><thead><tr><th>항목</th><th>금액</th></tr></thead>"
          "<tbody><tr><td>가입비</td><td>1000</td></tr></tbody></table>")
COMPLEX = ('<table><thead><tr><th>구분</th><th colspan="2">금리</th></tr>'
           "<tr><th>연도</th><th>기본</th><th>추가</th></tr></thead>"
           "<tbody><tr><td>2026</td><td>3.0</td><td>0.2</td></tr></tbody></table>")


def _has_markdown_table(text: str) -> bool:
    return any(
        line.strip().startswith("|")
        and set(line.replace("|", "").replace(" ", "")) <= {"-", ":"}
        for line in text.splitlines()
    )


@pytest.mark.unit
@pytest.mark.parametrize("value", ["html", "markdown", "auto", "AUTO", " Auto "])
def test_auto_is_an_accepted_table_format(value):
    assert normalize_table_format(value) == value.strip().lower()


@pytest.mark.unit
def test_unknown_format_falls_back_to_html():
    assert normalize_table_format("otsl") == "html"


@pytest.mark.unit
@pytest.mark.parametrize("body,expect_html", [(SIMPLE, False), (COMPLEX, True)])
def test_auto_picks_per_table_by_structure(body, expect_html):
    """병합 셀 표는 html, 단순 격자 표는 markdown."""
    out = html_to_text(f"<h2>안내</h2>{body}", table_format="auto")
    assert ("<table>" in out) is expect_html
    assert _has_markdown_table(out) is not expect_html


@pytest.mark.unit
def test_auto_mixes_formats_within_one_document():
    """문서 단위로 형식을 고정하면 둘 중 한쪽이 항상 손해다."""
    out = html_to_text(f"<h2>안내</h2>{SIMPLE}{COMPLEX}", table_format="auto")
    assert out.count("<table>") == 1
    assert _has_markdown_table(out)


@pytest.mark.unit
@pytest.mark.parametrize("fmt,expect_html,expect_md", [
    ("html", True, False),
    ("markdown", False, True),
])
def test_explicit_formats_still_apply_to_every_table(fmt, expect_html, expect_md):
    out = html_to_text(f"<h2>안내</h2>{SIMPLE}{COMPLEX}", table_format=fmt)
    assert ("<table>" in out) is expect_html
    assert _has_markdown_table(out) is expect_md


@pytest.mark.unit
def test_table_caption_survives_every_format():
    body = f'<table><caption>월별 내역</caption>{SIMPLE[len("<table>"):]}'
    for fmt in ("html", "markdown", "auto"):
        assert "월별 내역" in html_to_text(f"<h2>안내</h2>{body}", table_format=fmt)
