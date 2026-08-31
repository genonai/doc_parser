"""상품 카드(product_hpp) 연회비 표의 `<caption>` 이 표 설명으로 실리는지 고정한다.

fixture 는 실제 보고된 카드 WCMS 레코드(새마을금고 삼성카드 7)의 `htmlList.feeUrl` 을 옮긴
것이다. 원문 표는 `<caption>` 에 표 설명을 두고, 셀 값을 `<span class="ls0">20,000</span>원`
처럼 인라인 태그로 쪼개 놓았다. 이 두 가지가 각각 결함을 하나씩 드러냈다.

1. 표 설명 누락 — `html_flatten` 이 `<caption>` 을 표 앞의 `<p>` 로 옮겨 버려서
   `TableItem.captions` 가 비었다. 그러면 표 설명이 어느 표의 것인지 잃는다.
   백엔드가 `<caption>` 을 캡션 아이템으로 만들도록 고쳐졌으므로 전처리는 손대지 않는다.
2. 셀 값 중복 — 인라인 태그로 쪼개진 셀은 RichTableCell 이 되고 그 내용이 문서에 별도
   TextItem 으로도 들어간다. 표를 `export_to_html` 로 내보내는 serializer 가 그 하위 트리를
   visited 로 찍지 않아 금액 6개가 표 뒤에 평문으로 한 번 더 붙었다.
"""
import json
from pathlib import Path

import pytest

from genon.preprocessor.facade.enrichment.json_records import html_to_text

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "product_hpp_fee_table.json"
CAPTION = "본인카드의 해외겸용, 국내전용 별 연회비 정보를 확인할 수 있는 표입니다."
AMOUNTS = ("20,000", "18,000", "15,000", "13,000")


def _fee_html() -> str:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["htmlList"]["feeUrl"]


def _document():
    pytest.importorskip("docling.document_converter", exc_type=ImportError)
    import io

    from docling.datamodel.base_models import DocumentStream
    from genon.preprocessor.converters.html_flatten import (
        build_docling_document,
        extract_content,
    )
    from genon.preprocessor.facade.enrichment.json_records import _get_html_converter

    doc_html = build_docling_document("", [("", extract_content(_fee_html()))])
    return _get_html_converter().convert(
        DocumentStream(name="fee.html", stream=io.BytesIO(doc_html.encode("utf-8")))
    ).document


@pytest.mark.unit
def test_caption_becomes_table_caption_not_a_stray_paragraph():
    doc = _document()
    assert len(doc.tables) == 1

    captions = [ref.resolve(doc).text for ref in doc.tables[0].captions]
    assert captions == [CAPTION]

    # 표 앞 문단으로 옮겨지지 않는다 — 캡션 텍스트를 가진 TextItem 은 표의 캡션 아이템
    # 하나뿐이어야 한다(캡션 아이템 자체도 doc.texts 에 담긴다).
    caption_refs = {ref.cref for ref in doc.tables[0].captions}
    carriers = [item for item in doc.texts if CAPTION in (item.text or "")]
    assert [item.self_ref for item in carriers] == list(caption_refs)


@pytest.mark.unit
@pytest.mark.parametrize("table_format", ["html", "markdown", "auto"])
def test_fee_table_renders_caption_once_and_does_not_duplicate_cells(table_format):
    pytest.importorskip("docling.document_converter", exc_type=ImportError)
    text = html_to_text(_fee_html(), table_format=table_format)

    # 표 설명은 형식과 무관하게 정확히 한 번 실린다(html 은 <caption>, markdown 은 표 앞 문단).
    assert text.count(CAPTION) == 1

    # 인라인 span 으로 쪼개진 셀 값이 표 밖에서 되풀이되지 않는다.
    for amount in AMOUNTS:
        assert text.count(amount) == 1

    # 표 구조와 주변 본문은 그대로 남는다.
    assert "구분" in text and "해외겸용 (AMEX)" in text and "국내전용" in text
    assert "가족카드는 없음" in text
    assert "보너스포인트로 연회비를 결제할 수 있습니다." in text

    # 표시용 마크업은 산출물로 새지 않는다.
    for token in ("style=", "colgroup", "<col", "<span", "txt_dot", "ls0"):
        assert token not in text
