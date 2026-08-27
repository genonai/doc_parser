"""html_flatten 전처리 단위 테스트.

핵심은 두 가지다.

1) precheck 가 '정상 HTML' 을 건드리지 않는다 — auto 모드가 기존 .html 동작을 바꾸지
   않는다는 보장.
2) 정리(extract_content)가 aria-hidden / display:none 안의 **실제 본문을 지우지
   않는다** — 이 모듈의 조상(크롤러측 flatten_merged_html.py)은 이것들을 모두 지웠고,
   그래서 monimo 카드의 혜택 텍스트("대중교통·택시·전기차 충전요금 10% 결제일할인" 등,
   custom_field_card.yaml 의 benefit_text 대상)가 소실됐다. 회귀 방지용 고정 테스트다.
"""
from io import BytesIO
from pathlib import Path

import pytest

from genon.preprocessor.converters.html_flatten import (
    build_docling_document,
    document_title,
    extract_content,
    flatten_html,
    iter_srcdoc_sections,
    looks_thin,
    precheck_html,
)

pytestmark = pytest.mark.unit

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "html"
_WRAPPED_ACCORDION = _FIXTURES / "accordion_wrapped_nested_list.html"


# ── precheck ────────────────────────────────────────────────────────────────

def test_precheck_detects_iframe_srcdoc():
    raw = '<html><body><iframe class="page" srcdoc="&lt;p&gt;hi&lt;/p&gt;"></iframe></body></html>'
    assert "iframe_srcdoc" in precheck_html(raw)


def test_precheck_detects_escaped_html_blocks():
    # 본문 전체가 escape 된 경우(이중 인코딩). 임계값(10) 이상이어야 잡힌다.
    raw = "<html><body>" + "&lt;div&gt;x&lt;/div&gt;" * 12 + "</body></html>"
    assert "escaped_html" in precheck_html(raw)


def test_precheck_ignores_few_escaped_samples():
    """코드 예시로 &lt;div&gt; 를 몇 개 보여주는 정상 문서는 잡지 않는다."""
    raw = "<html><body><p>예시: &lt;div&gt; 태그</p><p>&lt;p&gt; 도 있다</p></body></html>"
    assert precheck_html(raw) == []


def test_precheck_clean_document_has_no_reasons():
    raw = "<html><body><h1>제목</h1><p>본문</p><table><tr><td>1</td></tr></table></body></html>"
    assert precheck_html(raw) == []


def test_precheck_detects_wrapped_nested_list_and_collapsed_content():
    raw = _WRAPPED_ACCORDION.read_text(encoding="utf-8")
    reasons = precheck_html(raw)
    assert "wrapped_nested_list" in reasons
    assert "collapsed_content" in reasons


def test_precheck_does_not_trigger_for_direct_nested_list_or_generic_hidden_ui():
    raw = (
        '<html><body><div style="display:none">로딩 중</div>'
        "<ul><li>항목<ul><li>정상 중첩</li></ul></li></ul></body></html>"
    )
    assert precheck_html(raw) == []


def test_precheck_no_false_positive_on_sample_html_files(sample_dir):
    """sample_files/*.html 는 정상 문서다 — auto 모드에서 flatten 되면 안 된다."""
    html_files = sorted(sample_dir.glob("*.html"))
    assert html_files, "sample_files 에 .html 픽스처가 없습니다"
    for path in html_files:
        raw = path.read_text(encoding="utf-8", errors="replace")
        assert precheck_html(raw) == [], f"{path.name} 이 오탐되었습니다"


# ── 숨김 요소 보존 (회귀 방지) ──────────────────────────────────────────────

def test_extract_content_keeps_aria_hidden_text():
    """aria-hidden 은 접근성 속성일 뿐 화면에 보이는 내용이다 — 지우면 혜택 텍스트가 사라진다."""
    raw = (
        "<html><body><main>"
        '<span class="option-label" aria-hidden="true">대중교통 10% 결제일할인</span>'
        "</main></body></html>"
    )
    assert "대중교통 10% 결제일할인" in extract_content(raw).get_text()


def test_extract_content_keeps_display_none_text():
    """접힌 아코디언(약관 본문)은 display:none 이지만 문서의 실질 내용이다."""
    raw = (
        "<html><body><main>"
        '<div class="accordion-collapse" style="display: none;">'
        "<p>카드사가 부가서비스를 변경하는 경우</p></div>"
        "</main></body></html>"
    )
    assert "부가서비스를 변경하는 경우" in extract_content(raw).get_text()


def test_extract_content_drops_hidden_attribute():
    """docling 과 같은 범위: hidden 속성만 제거한다."""
    raw = "<html><body><main><p hidden>숨김</p><p>보임</p></main></body></html>"
    text = extract_content(raw).get_text()
    assert "숨김" not in text
    assert "보임" in text


def test_extract_content_unhides_hidden_accordion_body():
    raw = (
        '<html><body><main><div class="ui_accord_content" hidden>'
        "<p>접힌 FAQ 본문</p></div></main></body></html>"
    )
    assert "collapsed_content" in precheck_html(raw)
    node = extract_content(raw)
    assert not node.find("div").has_attr("hidden")
    assert "접힌 FAQ 본문" in node.get_text()


def test_extract_content_strips_hidden_markers_not_content():
    """숨김 '표시'는 떼어낸다 — 남겨 두면 docling 백엔드가 내용을 통째로 억제한다."""
    raw = (
        "<html><body><main>"
        '<span aria-hidden="true">혜택</span>'
        '<div style="display:none"><p>약관</p></div>'
        '<div style="visibility:hidden">숨김2</div>'
        '<div style="opacity:0">숨김3</div>'
        "</main></body></html>"
    )
    html = str(extract_content(raw))
    assert "aria-hidden" not in html
    assert "display:none" not in html.replace(" ", "")
    assert "visibility:hidden" not in html.replace(" ", "")
    assert "opacity:0" not in html.replace(" ", "")
    for text in ("혜택", "약관", "숨김2", "숨김3"):
        assert text in html


def test_extract_content_keeps_other_style_declarations():
    """숨김 선언만 골라 떼고 나머지 style 은 건드리지 않는다."""
    raw = '<html><body><main><div style="color:red;display:none;margin:4px">본문</div></main></body></html>'
    html = str(extract_content(raw))
    assert "color:red" in html
    assert "margin:4px" in html
    assert "display" not in html


def test_extract_content_strips_hidden_marker_with_important():
    """`!important` 가 붙은 숨김 선언도 떼어낸다(실 마크업에 흔하다)."""
    raw = '<html><body><main><div style="display:none !important">본문</div></main></body></html>'
    html = str(extract_content(raw))
    assert "display" not in html
    assert "본문" in html


def test_extract_content_keeps_nonzero_opacity():
    """opacity 는 0 일 때만 숨김이다 — 0.5 는 남긴다."""
    raw = '<html><body><main><div style="opacity:0.5">본문</div></main></body></html>'
    assert "opacity:0.5" in str(extract_content(raw))


def test_extract_content_strips_hidden_marker_on_root_node():
    """콘텐츠 루트 자신이 숨김 표시를 달고 있어도 떼어낸다(find_all 은 자신을 포함하지 않는다)."""
    raw = '<html><body><main aria-hidden="true"><p>본문</p></main></body></html>'
    node = extract_content(raw)
    assert not node.has_attr("aria-hidden")
    assert "본문" in node.get_text()


def test_extract_content_normalizes_wrapped_nested_lists_without_text_loss():
    raw = _WRAPPED_ACCORDION.read_text(encoding="utf-8")
    node = extract_content(raw)
    html = str(node)

    assert "display:none" not in html.replace(" ", "")
    for sublist in node.find_all(("ul", "ol")):
        owner = sublist.find_parent("li")
        if owner is not None:
            assert sublist.parent is owner

    for expected in (
        "전자티켓 수령을 위해 등록한 이메일 확인 필요",
        "대상카드로 결제하지 않을 시 예약은 취소될 수 있음",
        "연체이자율은 회원별·이용상품별 정상 이자율에 3%p를 더해 적용",
        "신용카드 발급이 부적정한 경우 카드 발급이 제한될 수 있음",
    ):
        assert expected in node.get_text(" ", strip=True)


def test_extract_content_keeps_meaningful_wrapper_between_li_and_list():
    raw = "<html><body><ul><li>항목<blockquote><ul><li>인용 목록</li></ul></blockquote></li></ul></body></html>"
    node = extract_content(raw)
    sublist = node.find("blockquote").find("ul")
    assert sublist.parent.name == "blockquote"


def test_extract_content_lifts_table_caption_before_table():
    """docling 은 <caption> 을 버리므로 표 앞 문단으로 옮겨 살린다."""
    raw = (
        "<html><body><main><table><caption>표 설명입니다</caption>"
        "<tr><td>a</td></tr></table></main></body></html>"
    )
    node = extract_content(raw)
    html = str(node)
    assert "<caption>" not in html
    assert html.index("표 설명입니다") < html.index("<table>")


def test_extract_content_drops_empty_caption():
    raw = "<html><body><main><table><caption>  </caption><tr><td>a</td></tr></table></main></body></html>"
    html = str(extract_content(raw))
    assert "<caption>" not in html
    assert "<p>" not in html


def test_extract_content_drops_script_and_style():
    raw = (
        "<html><body><main><script>var a=1;</script>"
        "<style>p{color:red}</style><p>본문</p></main></body></html>"
    )
    text = extract_content(raw).get_text()
    assert "var a=1" not in text
    assert "color:red" not in text
    assert "본문" in text


# ── 콘텐츠 영역 선택 ────────────────────────────────────────────────────────

@pytest.mark.parametrize("wrapper", ["main", 'div class="modal-container"', 'div id="contents"'])
def test_extract_content_selects_content_area(wrapper):
    """gnb/footer 노이즈를 빼고 콘텐츠 영역만 고른다."""
    raw = (
        "<html><body><nav>메뉴노이즈</nav>"
        f"<{wrapper}><p>본문내용</p></{wrapper.split()[0]}>"
        "<footer>사업자번호 202-81-45602</footer></body></html>"
    )
    text = extract_content(raw).get_text()
    assert "본문내용" in text
    assert "메뉴노이즈" not in text
    assert "202-81-45602" not in text


def test_extract_content_falls_back_to_body():
    raw = "<html><body><p>셀렉터에 안 걸리는 문서</p></body></html>"
    assert "셀렉터에 안 걸리는 문서" in extract_content(raw).get_text()


# ── srcdoc 펼치기 / 조립 ────────────────────────────────────────────────────

def test_iter_srcdoc_sections_reads_page_sections():
    raw = (
        "<html><body>"
        '<section class="page-section"><h2 class="page-title">1. 메인 '
        '<span class="page-file">main.html</span></h2>'
        '<iframe srcdoc="&lt;main&gt;&lt;p&gt;내용A&lt;/p&gt;&lt;/main&gt;"></iframe></section>'
        '<section class="page-section"><h2 class="page-title">2. 연회비</h2>'
        '<iframe srcdoc="&lt;main&gt;&lt;p&gt;내용B&lt;/p&gt;&lt;/main&gt;"></iframe></section>'
        "</body></html>"
    )
    sections = iter_srcdoc_sections(raw)
    assert [label for label, _ in sections] == ["메인", "연회비"]
    assert "내용A" in sections[0][1]
    assert "내용B" in sections[1][1]


def test_iter_srcdoc_sections_bare_iframes():
    raw = '<html><body><iframe srcdoc="&lt;p&gt;X&lt;/p&gt;"></iframe></body></html>'
    sections = iter_srcdoc_sections(raw)
    assert len(sections) == 1
    assert sections[0][0] == "섹션 1"


def test_flatten_html_recovers_srcdoc_content():
    raw = (
        '<html><head><title>크롤 결과</title></head><body>'
        '<section class="page-section"><h2 class="page-title">1. 혜택</h2>'
        '<iframe srcdoc="&lt;main&gt;&lt;p&gt;대중교통 10%&lt;/p&gt;'
        '&lt;table&gt;&lt;tr&gt;&lt;td&gt;셀&lt;/td&gt;&lt;/tr&gt;&lt;/table&gt;&lt;/main&gt;">'
        "</iframe></section></body></html>"
    )
    out = flatten_html(raw)
    assert "<h1>크롤 결과</h1>" in out
    assert "<h2>혜택</h2>" in out
    assert "대중교통 10%" in out
    assert "<table>" in out
    assert "srcdoc" not in out  # 속성이 아니라 본문으로 펼쳐졌다


def test_flatten_html_single_page_without_srcdoc():
    """srcdoc 이 없는 단일 페이지도 always 모드에서 안전하게 처리된다."""
    raw = "<html><head><title>T</title></head><body><main><p>본문</p></main></body></html>"
    out = flatten_html(raw)
    assert "본문" in out
    assert "<h1>T</h1>" in out


def test_problem_fixture_recovers_missing_text_in_real_docling_parse():
    """캡처와 같은 li > div > ul 및 display:none 조합의 실제 Docling 회귀 테스트."""
    from docling.datamodel.base_models import DocumentStream
    from docling.document_converter import DocumentConverter

    raw = _WRAPPED_ACCORDION.read_text(encoding="utf-8")
    converter = DocumentConverter()

    def parse(html: str, name: str) -> str:
        stream = DocumentStream(name=name, stream=BytesIO(html.encode("utf-8")))
        return converter.convert(stream, raises_on_error=True).document.export_to_text()

    raw_text = parse(raw, "accordion_raw.html")
    parsed_text = parse(flatten_html(raw, reasons=precheck_html(raw)), "accordion_flat.html")

    # 전처리 전에는 Docling 의 목록 직접 자식 제약과 숨김 판정으로 상세가 누락된다.
    assert "전자티켓 수령을 위해 등록한 이메일 확인 필요" not in raw_text
    assert "연체이자율은 회원별·이용상품별 정상 이자율에 3%p를 더해 적용" not in raw_text

    # 전처리 후에는 접힌/펼친 두 아코디언의 중첩 목록이 모두 살아난다.
    for expected in (
        "전자티켓 수령을 위해 등록한 이메일 확인 필요",
        "대상카드로 결제하지 않을 시 예약은 취소될 수 있음",
        "혜택은 아이디당 1회 제공",
        "연체이자율은 회원별·이용상품별 정상 이자율에 3%p를 더해 적용",
        "신용카드 발급이 부적정한 경우 카드 발급이 제한될 수 있음",
        "결제 기간 원리금을 연체할 경우 모든 원리금을 변제할 의무가 발생할 수 있음",
    ):
        assert expected in parsed_text


def test_flatten_html_decodes_whole_document_escaped_html():
    """iframe 없이 본문 전체가 escape 된 문서도 표 구조가 복원된다.

    srcdoc 경로만 unescape 하던 시절엔 bs4 가 `&lt;table&gt;` 을 텍스트 노드로 읽어
    표/헤딩이 0개인 문서가 docling 에 넘어갔다. 회귀 방지용.
    """
    body = (
        "&lt;table&gt;&lt;tr&gt;&lt;td&gt;셀값&lt;/td&gt;&lt;/tr&gt;&lt;/table&gt;"
        + "&lt;p&gt;문단&lt;/p&gt;" * 12  # precheck 임계값(10) 초과
    )
    raw = f"<html><head><title>T</title></head><body>{body}</body></html>"
    assert precheck_html(raw) == ["escaped_html"]  # iframe 은 없다

    out = flatten_html(raw)
    assert "<table>" in out  # 텍스트가 아니라 실제 구조로 복원
    assert "셀값" in out
    assert "&lt;table&gt;" not in out


def test_flatten_html_does_not_decode_clean_document():
    """escaped_html 이 아니면 엔티티를 건드리지 않는다(오탐 시 본문 손상 방지)."""
    raw = (
        "<html><head><title>T</title></head><body><main>"
        "<p>예시: &lt;div&gt; 태그</p></main></body></html>"
    )
    assert precheck_html(raw) == []

    out = flatten_html(raw)
    assert "&lt;div&gt;" in out  # 코드 예시는 그대로 텍스트로 남는다


def test_flatten_html_honors_caller_supplied_reasons():
    """호출측이 넘긴 precheck 결과를 그대로 쓴다(재스캔하지 않는다)."""
    raw = "<html><body>" + "&lt;p&gt;문단&lt;/p&gt;" * 12 + "</body></html>"
    # escaped_html 이 빠진 reasons 를 주면 디코딩하지 않는다.
    assert "&lt;p&gt;" in flatten_html(raw, "T", reasons=[])
    assert "<p>문단</p>" in flatten_html(raw, "T", reasons=["escaped_html"])


def test_build_docling_document_escapes_labels():
    from bs4 import BeautifulSoup

    node = BeautifulSoup("<p>x</p>", "html.parser")
    out = build_docling_document("A & B", [("<라벨>", node)])
    assert "A &amp; B" in out
    assert "&lt;라벨&gt;" in out


def test_document_title_fallback():
    assert document_title("<html><body></body></html>", "fb") == "fb"
    assert document_title("<html><head><title> T </title></head></html>") == "T"


# ── looks_thin ──────────────────────────────────────────────────────────────

def test_looks_thin_only_for_large_documents():
    assert looks_thin(4_000_000, 641) is True      # 실측 merged.html
    assert looks_thin(4_000_000, 60_000) is False  # 실측 flatten 후
    assert looks_thin(3_000, 5) is False           # 작은 문서는 판정 대상 아님
