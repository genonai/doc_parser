"""HTML 표의 <caption> 이 docling 문서에 남는지 검증한다.

이 처리가 없으면 캡션 텍스트가 표에도 본문에도 남지 않고 통째로 사라져,
청크만 보고는 그 표가 무엇에 대한 것인지 알 수 없다.
"""

import pytest


def _convert(tmp_path, body: str):
    pytest.importorskip("docling.document_converter", exc_type=ImportError)
    from docling.document_converter import DocumentConverter

    path = tmp_path / "table.html"
    path.write_text(f"<!doctype html><html><body>{body}</body></html>", encoding="utf-8")
    return DocumentConverter().convert(path).document


@pytest.mark.unit
def test_table_caption_survives_conversion(tmp_path):
    doc = _convert(tmp_path, """
        <table>
          <caption>2026년 월별 이용 내역</caption>
          <thead><tr><th>월</th><th>금액</th></tr></thead>
          <tbody><tr><td>1월</td><td>1000</td></tr></tbody>
        </table>""")

    assert doc.tables[0].caption_text(doc).strip() == "2026년 월별 이용 내역"
    assert "2026년 월별 이용 내역" in doc.export_to_markdown()


@pytest.mark.unit
def test_caption_text_is_not_mistaken_for_a_table_cell(tmp_path):
    doc = _convert(tmp_path, """
        <table>
          <caption>안내</caption>
          <tbody><tr><td>값</td></tr></tbody>
        </table>""")

    cells = [c.text for row in doc.tables[0].data.grid for c in row]
    assert cells == ["값"]


@pytest.mark.unit
@pytest.mark.parametrize("body", [
    "<table><tbody><tr><td>a</td></tr></tbody></table>",              # 캡션 없음
    "<table><caption>   </caption><tbody><tr><td>a</td></tr></tbody></table>",  # 공백뿐
])
def test_missing_or_blank_caption_leaves_table_uncaptioned(tmp_path, body):
    doc = _convert(tmp_path, body)
    assert doc.tables[0].caption_text(doc) == ""


@pytest.mark.unit
def test_inline_markup_inside_caption_is_flattened(tmp_path):
    doc = _convert(tmp_path, """
        <table><caption>월별 <b>이용</b> 내역</caption>
        <tbody><tr><td>a</td></tr></tbody></table>""")
    assert doc.tables[0].caption_text(doc).strip() == "월별 이용 내역"


@pytest.mark.unit
def test_nested_table_keeps_its_own_caption(tmp_path):
    """직계 자식만 봐야 바깥 표가 안쪽 표의 캡션을 가져가지 않는다."""
    doc = _convert(tmp_path, """
        <table><caption>바깥표</caption><tbody><tr><td>
          <table><caption>안쪽표</caption><tbody><tr><td>x</td></tr></tbody></table>
        </td></tr></tbody></table>""")

    captions = {t.caption_text(doc).strip() for t in doc.tables}
    assert captions == {"바깥표", "안쪽표"}
