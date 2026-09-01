"""표가 청크 텍스트에 정제된 HTML 로만 실리는지 고정한다.

실측 캡처(상품설명 카드·고객센터 카드)에서 청크 `text` 안에 원시 HTML 이 그대로 실려 있었다.
원인은 파싱 잔재가 아니라 재직렬화였다 — `TableItem.export_to_html()` 이 쓰는 docling_core
HTML serializer 는 화면 렌더링이 목적이라 rich cell 서브트리를 통째로 내보낸다.

fixture 두 개가 그 원문 구조를 그대로 담고 있고, 여기서 증상 네 가지를 한꺼번에 본다.

1. 표시용 태그 누출 — `<p>`, `<ul><li>`, `<span class='inline-group'>`, `<a href>`
2. markdown 경로 노이즈 — `<!-- image -->` 자리표시자와 `&gt;` 엔티티
3. 셀 값 중복 — rich cell 내용과 캡션이 표 뒤에 평문으로 한 번 더 붙는다
4. 구조 보존 — 격자 태그와 colspan/rowspan 은 남고 셀 값도 잃지 않는다
"""

import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
CS_HPP = FIXTURES / "cs_hpp_rich_cell_tables.html"
PRODUCT_HPP = FIXTURES / "product_hpp_fee_table.json"

# 청크 텍스트에 남으면 안 되는 것들. 표 격자 태그(table/caption/tbody/tr/th/td)는 제외한다.
MARKUP_LEAKS = (
    "<p>", "<ul", "<li", "<span", "<a href", "<div", "<img",
    "inline-group", "<!-- image -->", "&gt;", "&quot;", "&#x27;",
)

CHUNKER_MODULES = [
    "genon.preprocessor.facade.chunking_processor",
    "genon.preprocessor.facade.intelligent_processor",
    "genon.preprocessor.facade.convert_processor",
]


def _convert(path: Path):
    pytest.importorskip("docling.document_converter", exc_type=ImportError)
    from docling.document_converter import DocumentConverter

    return DocumentConverter().convert(path).document


def _product_html(tmp_path: Path, key: str) -> Path:
    """wcms 레코드의 htmlList 조각을 그대로 html 파일로 푼다."""
    payload = json.loads(PRODUCT_HPP.read_text(encoding="utf-8"))
    target = tmp_path / f"product_hpp_{key}.html"
    target.write_text(payload["htmlList"][key], encoding="utf-8")
    return target


def _chunk_texts(module_name: str, path: Path, table_format: str) -> list[str]:
    module = pytest.importorskip(module_name, exc_type=ImportError)
    chunker = module.GenosSmartChunker(
        max_tokens=4000, chunk_mode="split_only", tokenizer_type="char"
    )
    doc = _convert(path)
    return [chunk.text for chunk in chunker.chunk(dl_doc=doc, table_format=table_format)]


@pytest.mark.unit
@pytest.mark.parametrize("module_name", CHUNKER_MODULES)
@pytest.mark.parametrize("table_format", ["html", "auto"])
def test_cs_hpp_rich_cell_tables_carry_no_markup(module_name, table_format):
    texts = _chunk_texts(module_name, CS_HPP, table_format)
    body = "\n".join(texts)

    assert texts
    for token in MARKUP_LEAKS:
        assert token not in body

    # 표 구조와 내용은 남는다. 정형 표는 auto 에서 markdown 으로 나가므로 `<table>` 을
    # 기대할 수 있는 것은 html 강제일 때뿐이다.
    if table_format == "html":
        assert "<table>" in body
    assert "비밀번호 6자리를 입력" in body
    # `colspan="4"` 안내 배너는 레이아웃용 표라 표기 없이 평문 한 번으로만 실린다
    # (예전에는 이 표가 열 수만큼 값이 복제된 채 표로 실렸다).
    assert 'colspan="4"' not in body
    assert body.count("해당 목차는 [AI 에이전트용] 이므로") == 1
    if table_format == "html":
        # 정제 HTML 은 셀 안 여러 문단을 공백 한 칸으로 잇는다.
        assert "본인인증 진행 2. 비밀번호 6자리를 입력" in body
    # `>` 는 엔티티가 아니라 그대로 남는다.
    assert "일반결제 > 삼성페이" in body


@pytest.mark.unit
@pytest.mark.parametrize("module_name", CHUNKER_MODULES)
@pytest.mark.parametrize("table_format", ["html", "auto"])
def test_product_hpp_rich_cell_tables_carry_no_markup(module_name, tmp_path, table_format):
    texts = _chunk_texts(module_name, _product_html(tmp_path, "addServiceUrl"), table_format)
    body = "\n".join(texts)

    assert texts
    for token in MARKUP_LEAKS:
        assert token not in body

    # 셀 안 리스트 항목은 한 줄로 이어진다. 이 표들은 행 라벨이 `<th scope="row">` 일 뿐
    # 병합 셀이 없어 auto 에서는 markdown 으로 나가고, 그 경로는 항목 앞에 `- ` 를 남긴다.
    joiner = " " if table_format == "html" else " - "
    assert f"주중(월~금요일) : 0.5%{joiner}주말(토~일요일) : 1%" in body
    # 링크는 어느 경로에서도 표시 문구만 남는다(URL 은 검색에 기여하지 않는다).
    assert "홈페이지 예매 할인 가능(제휴 할인 카드 선택) 예약하기" in body
    assert "everland.com" not in body


@pytest.mark.unit
@pytest.mark.parametrize("table_format", ["html", "auto"])
def test_rich_cell_values_are_not_repeated_outside_the_table(tmp_path, table_format):
    body = "\n".join(_chunk_texts(
        CHUNKER_MODULES[0], _product_html(tmp_path, "addServiceUrl"), table_format))

    # 표 안에 한 번. 예전에는 rich cell 서브트리가 표 뒤에 평문으로 또 붙었다.
    assert body.count("주중(월~금요일) : 0.5%") == 1
    assert body.count("대중교통 : 버스(시외·고속버스 제외), 지하철") == 1
    # 캡션도 표 안에 한 번만 실린다.
    assert body.count("적립기준에 따른 할인 정보를 확인하실 수 있는 표 입니다.") == 1


@pytest.mark.unit
def test_fee_table_amounts_appear_once_in_chunks(tmp_path):
    body = "\n".join(_chunk_texts(
        CHUNKER_MODULES[0], _product_html(tmp_path, "feeUrl"), "html"))

    for amount in ("20,000", "18,000", "15,000", "13,000"):
        assert body.count(amount) == 1
    # <br> 로 나뉜 헤더는 공백 한 칸으로 합쳐진다.
    assert "<th>해외겸용 (AMEX)</th>" in body


@pytest.mark.unit
def test_image_only_row_does_not_leave_an_empty_markdown_row(tmp_path):
    """이미지만 든 셀은 평문이 비어 `|  |  |` 한 줄로 남았다. 값 없는 행은 버린다."""
    body = "\n".join(_chunk_texts(
        CHUNKER_MODULES[0], _product_html(tmp_path, "addServiceUrl"), "auto"))

    assert "| 온라인 결제 |" in body  # 표 자체는 남는다
    assert not any(
        line.replace("|", "").strip() == ""
        for line in body.split("\n")
        if line.startswith("|")
    )
