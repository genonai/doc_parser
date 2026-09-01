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
    promote_marker_headings,
)

pytestmark = pytest.mark.unit

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "html"
_WRAPPED_ACCORDION = _FIXTURES / "accordion_wrapped_nested_list.html"

_MONIMO_SAMPLES = Path(__file__).resolve().parents[2] / "sample_files" / "monimo"
_MARKER_FIXTURE = _MONIMO_SAMPLES / "monimo_cs_hpp_marker_sections_sample.html"
_HEADING_TAGS = ["h1", "h2", "h3", "h4", "h5", "h6"]


def _require_html(path: Path) -> str:
    """픽스처가 없으면 skip 하고, 있으면 원문 HTML 을 읽어 돌려준다."""
    if not path.exists():
        pytest.skip(f"검증용 샘플 없음: {path}")
    return path.read_text(encoding="utf-8")


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


def test_extract_content_keeps_table_caption_in_place():
    """`<caption>` 은 백엔드가 표의 캡션 아이템으로 만든다 — 전처리가 미리 뜯어내면 안 된다.

    예전에는 docling 이 caption 을 버려서 표 앞의 `<p>` 로 옮겼다. 그 우회책은 표 설명을
    본문 문단으로 만들어 `TableItem.captions` 를 비워 놓으므로 지금은 하지 않는다.
    """
    raw = (
        "<html><body><main><table><caption>표 설명입니다</caption>"
        "<tr><td>a</td></tr></table></main></body></html>"
    )
    html = str(extract_content(raw))
    assert "<caption>표 설명입니다</caption>" in html
    assert html.index("<table>") < html.index("표 설명입니다")


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


# ── 마커 기반 heading 승격(cs_hpp) ──────────────────────────────────────────

def test_precheck_detects_marker_headings():
    raw = _require_html(_MARKER_FIXTURE)
    assert "marker_headings" in precheck_html(raw, detect_marker_headings=True)


def test_precheck_marker_headings_requires_opt_in():
    """opt-in 기본값이 False 다 — 이게 핵심이다.

    marker_headings 를 명시적으로 켜지 않은 호출(다른 doc_type 의 auto 모드 포함)에는
    이 사유가 전혀 뜨지 않는다, 즉 기존 호출부 동작이 이 인자로 바뀌지 않는다는 보장이다.
    """
    raw = _require_html(_MARKER_FIXTURE)
    assert "marker_headings" not in precheck_html(raw)


def test_precheck_ignores_markers_when_document_has_subheadings():
    """표 밖 마커 후보가 3개 이상이어도 원문에 하위 heading(h2)이 이미 2개면

    게이트 A(_MARKER_MAX_EXISTING_SUBHEADINGS)에서 탈락한다 — 이미 h 태그로 계층을
    표현한 문서는 승격 대상이 아니다.
    """
    raw = (
        "<html><body><h2>섹션1</h2><p>◈ 항목1</p><p>▣ 항목2</p><p>■ 항목3</p>"
        "<h2>섹션2</h2></body></html>"
    )
    assert "marker_headings" not in precheck_html(raw, detect_marker_headings=True)


def test_precheck_ignores_few_marker_blocks():
    """마커 후보가 2개뿐이면 임계값(_MARKER_HEADING_MIN=3) 미만이라 게이트 B 에서 탈락한다."""
    raw = "<html><body><p>◈ 항목1</p><p>▣ 항목2</p></body></html>"
    assert "marker_headings" not in precheck_html(raw, detect_marker_headings=True)


def test_precheck_marker_headings_absent_for_existing_cs_hpp_fixtures():
    """회귀 가드 — 기존 cs_hpp 픽스처 3종은 opt-in 이어도 승격 사유가 뜨지 않는다.

    monimo_cs_hpp_sample.html / monimo_cs_hpp_chunksize_sample.html 은 h2 를 이미
    갖고 있어 게이트 A(_MARKER_MAX_EXISTING_SUBHEADINGS)에서 탈락하고,
    monimo_cs_hpp_large_table_sample.html 은 h2 가 0개지만 도형 마커 자체가 없어
    게이트 B(마커 후보 부족)에서 탈락한다 — 탈락 사유가 서로 다른 근접 케이스다.
    """
    for name in (
        "monimo_cs_hpp_sample.html",
        "monimo_cs_hpp_chunksize_sample.html",
        "monimo_cs_hpp_large_table_sample.html",
    ):
        raw = _require_html(_MONIMO_SAMPLES / name)
        assert precheck_html(raw, detect_marker_headings=True) == [], name


def test_promote_marker_headings_assigns_levels_by_first_appearance():
    """마커 문자별 레벨은 문서 내 첫 등장 순서로 배정된다 — ◈ 재등장도 h3 을 유지한다."""
    raw = "<html><body><p>◈ 첫제목</p><p>▣ 중제목</p><p>◈ 둘째제목</p></body></html>"
    node = extract_content(raw)
    assert promote_marker_headings(node) == 3
    headings = [(tag.name, tag.get_text(strip=True)) for tag in node.find_all(True)]
    assert headings == [("h3", "◈ 첫제목"), ("h4", "▣ 중제목"), ("h3", "◈ 둘째제목")]


def test_promote_marker_headings_keeps_bold_bullet_as_body():
    """`- 2015년 6월 25일` 은 `-` 마커 + 100% bold + 20자 + 명사 종결이라 길이·종결형·

    강조 조건 어디에도 걸리지 않는다(hwp 선례의 "약한 마커 AND 강조" 규칙은 미이식).
    마커 화이트리스트(_MARKER_CHARS)에 `-` 가 없다는 사실만이 오승격을 막는다.
    """
    raw = "<html><body><p><b>- 2015년 6월 25일</b></p></body></html>"
    node = extract_content(raw)
    assert promote_marker_headings(node) == 0
    assert node.find("p").name == "p"


def test_promote_marker_headings_skips_annotation_markers():
    """`※`/`→`/`-`/`·` 로 시작하는 줄은 마커 화이트리스트 밖이라 본문(p)으로 남는다."""
    raw = '<html><body><p>※ 주석1</p><p>→ 화살표</p><p>- 대시</p><p>· 가운뎃점</p></body></html>'
    node = extract_content(raw)
    assert promote_marker_headings(node) == 0
    assert [tag.name for tag in node.find_all(True)] == ["p", "p", "p", "p"]


def test_promote_marker_headings_skips_table_cells():
    """표 안 heading 은 표를 셀 중간에서 쪼개므로(모듈 docstring 참고) 표 밖 후보만 승격한다."""
    raw = _require_html(_MARKER_FIXTURE)
    node = extract_content(raw)
    tables_before = len(node.find_all("table"))
    tds_before = len(node.find_all("td"))
    promote_marker_headings(node)
    assert len(node.find_all("table")) == tables_before
    assert len(node.find_all("td")) == tds_before
    for table in node.find_all("table"):
        assert not table.find_all(["h1", "h2", "h3", "h4", "h5", "h6"])


def test_promote_marker_headings_demotes_long_and_sentence_final():
    """80자 초과 줄과 서술형 종결(`...다.`)은 마커가 있어도 제목이 아니라 본문으로 본다."""
    long_line = "◈ " + "가" * 90
    raw = f"<html><body><p>{long_line}</p><p>◈ 서비스가 오픈됩니다.</p></body></html>"
    node = extract_content(raw)
    assert promote_marker_headings(node) == 0
    assert [tag.name for tag in node.find_all(True)] == ["p", "p"]


def test_promote_marker_headings_requires_title_but_not_space():
    """`■`(제목 없음)과 `■■■`(마커 반복 장식선)은 본문으로 남고, `◈결제`는 승격된다.

    마커 뒤 공백은 더 이상 필수가 아니다(_MARKER_HEADING_RE). 실측 근거는
    monimo_cs_hpp_marker_nospace_sample.html 로, 실제 소제목이 `◆개요` 처럼 마커와
    붙어 있다. 공백 필수 규칙은 저자 의도가 아니라 DOM 우연에 반응했다 —
    `get_text(" ")` 가 태그 경계에 공백을 넣으므로 같은 표기라도 마커와 제목이 다른
    span 이면 통과하고 한 span 안이면 탈락했다.

    공백 필수가 막던 장식선은 `_MARKER_DECORATION_RE`(제목이 다시 마커로 시작하면 탈락)가
    대신 막는다. 한 줄 전체가 `◈결제` 인 문단은 소제목으로 보는 편이 맞다 — 문장 중간의
    `◈결제` 는 후보 조건("블록 자식 없는 p/div 한 줄 전체")에서 애초에 걸리지 않는다.
    """
    raw = "<html><body><p>■</p><p>■■■</p><p>◈결제</p></body></html>"
    node = extract_content(raw)
    assert promote_marker_headings(node) == 1
    assert [tag.name for tag in node.find_all(True)] == ["p", "p", "h3"]


def test_marker_headings_promote_nospace_bold_sections():
    """캡쳐 05(INC_19570012) 전사본 — 마커와 제목이 한 bold span 안에 붙어 있는 형태.

    `◆개요` / `▣[홈페이지]서비스 신청 및 해지 방법` 처럼 마커 뒤 공백이 없고 줄 전체가
    bold 다. 이 픽스처가 고정하는 사실 두 가지:
    (a) 공백 없는 마커도 소제목이다 — 6건 전부 승격되어야 한다.
    (b) bold 는 판별 신호가 못 된다 — `-`로 시작하는 본문 줄도 똑같이 bold 인데,
        마커 화이트리스트(_MARKER_CHARS) 밖이라는 사실만이 오승격을 막는다.
    """
    raw = _require_html(_MONIMO_SAMPLES / "monimo_cs_hpp_marker_nospace_sample.html")
    assert "marker_headings" in precheck_html(raw, detect_marker_headings=True)

    node = extract_content(raw)
    assert promote_marker_headings(node) == 6

    # 문서 <h1> 은 원래 있던 제목이므로 승격분(h3/h4)만 본다.
    promoted = [(t.name, t.get_text(" ", strip=True))
                for t in node.find_all(_HEADING_TAGS) if t.name != "h1"]
    assert [name for name, _ in promoted] == ["h3", "h3", "h3", "h3", "h3", "h4"]
    assert promoted[0][1] == "◆개요"
    assert promoted[-1][1] == "▣[홈페이지]서비스 신청 및 해지 방법"

    # bold 본문 줄은 p 로 남는다. 마커가 없다는 것 외에 제목과 구분되는 신호가 없다.
    bodies = [t.get_text(" ", strip=True) for t in node.find_all("p")]
    assert any(b.startswith("-2025년 11월 22일") for b in bodies)
    assert any(b.startswith("고객 동의하에") for b in bodies)


def test_marker_headings_promote_real_dom_noise_sections():
    """캡쳐 03·04(INC_4710521/INC_4902781) 전사본 — 실 CMS 노이즈가 섞인 형태.

    같은 문서가 `▣ 이용조건`(공백 있음)과 `▣처리가능 업무`(공백 없음)를 함께 쓰고, 제목
    문단 끝에 스마트에디터가 붙인 후행 `<br>` 이 달려 있다. ▣ 하나만 쓰는 문서라 승격된
    헤더는 전부 h3 이며, 표 구조와 표 안 굵은 셀(`■ 카드 등록방법 및 화면`)은 건드리지
    않는다.
    """
    raw = _require_html(_MONIMO_SAMPLES / "monimo_cs_hpp_marker_real_dom_sample.html")
    assert "marker_headings" in precheck_html(raw, detect_marker_headings=True)

    node = extract_content(raw)
    tables_before, tds_before = len(node.find_all("table")), len(node.find_all("td"))
    assert promote_marker_headings(node) == 6

    promoted = [(t.name, t.get_text(" ", strip=True)) for t in node.find_all(_HEADING_TAGS)]
    assert {name for name, _ in promoted} == {"h1", "h3"}
    titles = [text for name, text in promoted if name == "h3"]
    assert "▣ 이용방법" in titles       # 후행 <br> 이 붙은 문단
    assert "▣ 처리가능 업무" in titles   # 마커 뒤 공백 없는 문단

    assert len(node.find_all("table")) == tables_before
    assert len(node.find_all("td")) == tds_before
    for table in node.find_all("table"):
        assert not table.find_all(_HEADING_TAGS)


def test_promote_marker_headings_skips_paragraph_split_by_inner_break():
    """`<br>` 이 텍스트를 두 줄로 쪼갠 문단은 한 줄짜리 제목이 아니므로 승격하지 않는다.

    후행 `<br>` 만 허용한다 — 그 경계가 없으면 여러 줄 본문 블록까지 헤더가 된다.
    """
    raw = ("<html><body><p>▣ 제목<br>본문이 이어짐</p>"
           "<p>▣ 항목2</p><p>▣ 항목3</p></body></html>")
    node = extract_content(raw)
    assert promote_marker_headings(node) == 2
    assert node.find("p").name == "p"


def test_flatten_html_promotes_marker_sections_only_when_reason_present():
    """flatten_html 은 reasons 에 marker_headings 가 있을 때만 승격을 적용한다."""
    raw = (
        "<html><head><title>T</title></head><body>"
        "<p>◈ 항목1</p><p>▣ 항목2</p><p>■ 항목3</p></body></html>"
    )
    assert "<h3>" in flatten_html(raw, reasons=["marker_headings"])
    assert "<h3>" not in flatten_html(raw, reasons=[])


def test_build_docling_document_skips_duplicate_single_section_label():
    """단일 섹션 라벨이 제목과 같으면 <h2> 중복을 생략하고, 다르면 <h2>라벨</h2> 을 넣는다.

    라벨·제목이 둘 다 빈 문자열이면(facade/enrichment/json_records.py:309-318 이
    build_docling_document("", [("", node)]) 로 호출하는 경로) <h2></h2> 를 그대로
    유지한다 — "라벨이 비어있지 않을 것"을 skip 조건에 넣어 이 경로를 건드리지 않는다.
    """
    node = extract_content("<html><body><p>x</p></body></html>")
    assert "<h2>" not in build_docling_document("T", [("T", node)])
    assert "<h2>혜택</h2>" in build_docling_document("T", [("혜택", node)])
    assert "<h2></h2>" in build_docling_document("", [("", node)])


def test_marker_headings_produce_section_headers_in_real_docling_parse():
    """실제 Docling 왕복 — 원문은 SectionHeaderItem 0개, 승격 후엔 8개(레벨 2/3)가 잡히고

    표 2개는 양쪽 다 파괴되지 않는다. 문서 최상위 <h1> 은 TitleItem 으로 분류돼
    SectionHeaderItem 집계에 들지 않는다(이 픽스처는 <title>과 본문 <h1> 이 서로 달라
    TitleItem 이 2개다).
    """
    from docling.datamodel.base_models import DocumentStream
    from docling.document_converter import DocumentConverter
    from docling_core.types.doc import SectionHeaderItem

    raw = _require_html(_MARKER_FIXTURE)
    converter = DocumentConverter()

    def parse(html: str, name: str):
        stream = DocumentStream(name=name, stream=BytesIO(html.encode("utf-8")))
        return converter.convert(stream, raises_on_error=True).document

    raw_doc = parse(raw, "marker_raw.html")
    flat = flatten_html(
        raw, reasons=precheck_html(raw, detect_marker_headings=True), marker_headings=True
    )
    flat_doc = parse(flat, "marker_flat.html")

    def section_headers(doc):
        return [item for item, _ in doc.iterate_items() if isinstance(item, SectionHeaderItem)]

    raw_headers = section_headers(raw_doc)
    flat_headers = section_headers(flat_doc)

    assert len(raw_headers) == 0
    assert len(flat_headers) == 8
    assert len(raw_doc.tables) == len(flat_doc.tables) == 2
    assert {h.level for h in flat_headers} == {2, 3}


# ── looks_thin ──────────────────────────────────────────────────────────────

def test_looks_thin_only_for_large_documents():
    assert looks_thin(4_000_000, 641) is True      # 실측 merged.html
    assert looks_thin(4_000_000, 60_000) is False  # 실측 flatten 후
    assert looks_thin(3_000, 5) is False           # 작은 문서는 판정 대상 아님
