"""docling 이 파싱 가능한 flat HTML 을 만드는 전처리 모듈.

## 왜 필요한가

크롤 산출물(예: monimo 카드 `merged.html`)은 본문이 `<iframe srcdoc="...">` **속성값
안에** HTML-escape 되어 담겨 있다. docling 의 HTML 백엔드는 속성을 읽지 않으므로
4MB 문서에서 641자·표 0개만 추출된다(실측). srcdoc 을 펼쳐 heading 기반 단일 문서로
재조립하면 같은 문서가 59,140자·표 43개로 살아난다.

## 숨김 요소를 함부로 지우지 말 것 (중요)

이 모듈의 조상인 크롤러측 `flatten_merged_html.py` 는 `hidden` 속성 + 인라인
`display:none`/`visibility:hidden`/`opacity:0` + `aria-hidden` 을 **모두** 제거했다.
크롤러 용도로는 맞지만 파싱 용도로는 실제 내용을 지운다:

  - `<span class="option-label" aria-hidden="true">` → "대중교통·택시·전기차 충전요금
    10% 결제일할인", "주유 10,000원 결제일할인" 등 **혜택 텍스트**. `aria-hidden` 은
    접근성 속성일 뿐 화면에는 보이는 내용이고, 이건 custom_field_card.yaml 의
    `benefit_text` 가 노리는 바로 그 데이터다.
  - `<div class="accordion-collapse" style="display:none">` → 부가서비스 변경 약관 본문.

13페이지 monimo 카드로 분리 측정한 결과(docling BODY 텍스트):

  | 규칙                                          | 텍스트   | 차이   |
  |-----------------------------------------------|----------|--------|
  | aria-hidden·display:none 까지 제거(구 규칙)   | 59,132자 | 기준   |
  | hidden 속성만 제거(이 모듈)                   | 60,857자 | +1,725 |
  | 콘텐츠영역 선택도 안 함(body 전체)            | 63,381자 | +4,249 |

그래서 이 모듈은 요소를 지울 때 **`hidden` 속성만** 본다(docling 의 `convert()` 와 동일한 범위).
마지막 +4,249자는 대부분 footer 상용구(사업자번호·주소·수상내역·전화번호)라, 콘텐츠영역
선택(main/.modal-container/#contents)은 유지한다.

## 숨김 '표시'는 떼어내야 한다 (docling 백엔드가 억제한다)

내용을 보존하려면 지우지 않는 것만으로는 부족하다. docling 백엔드는 `_walk` 에서
`_is_suppressed_tag` → `_is_invisible_tag` 를 타고 **`aria-hidden` / 인라인
`display:none`·`visibility:hidden|collapse`·`opacity:0` 컨테이너의 내용을 통째로 억제**한다
(`docling/backend/html_backend.py`). 실측 — `<div style="display:none"><table>` 과
`<div aria-hidden="true"><table>` 은 인식되는 표가 0개다(보이는 `<table>` 은 1개).

즉 상류에서 "안 지운다"고 끝나지 않는다. `_clean` 은 **숨김 표시만 떼고 내용은 남긴다** —
그래야 위 측정에서 지키려던 혜택 텍스트·접힌 약관과 그 안의 표가 docling BODY 에 들어온다.
(이 모듈이 추가된 시점 `4792e0ff` 보다 백엔드 업그레이드 `93557b6a` 가 앞서서, 위 측정 당시의
"docling 이 문맥에 따라 알아서 판정한다"는 전제가 더는 성립하지 않는다.)

`<caption>` 도 docling 이 버리므로("라이트할부 기간동안 추가 결제 안내입니다." 같은 표 설명),
표 앞의 문단으로 옮겨 살린다.

의존성: beautifulsoup4 (docling 이 자체 HTML 백엔드에서 쓰므로 전이 확보) + 표준 라이브러리.
"""
from __future__ import annotations

import html
import re

from bs4 import BeautifulSoup
from bs4.element import Tag

# 콘텐츠 영역 우선순위 셀렉터. 첫 매치를 본문으로 쓰고, 없으면 body 전체로 폴백한다.
#  - main             : 카드 상세 full-page 스냅샷. gnb/footer 제외.
#  - .modal-container : 연회비/혜택 상세 등 모달 캡처(body 루트에 위치).
#  - #contents        : 위 둘의 예비 셀렉터.
_CONTENT_SELECTORS = ("main", ".modal-container", "#contents")

# 제거할 태그(docling 이 무시하거나 노이즈인 것).
_STRIP_TAGS = (
    "script", "style", "noscript", "iframe", "svg", "template", "canvas", "base",
)

# 떼어낼 숨김 속성. `hidden` 은 여기가 아니라 요소째로 제거한다(docling 과 동일 범위).
_UNHIDE_ATTRS = ("aria-hidden",)

# 인라인 style 에서 떼어낼 숨김 선언. 선언 하나 단위로 매칭해 나머지 선언은 살린다.
# `!important` 는 실 마크업에 흔하므로 함께 받는다. `opacity:0.5` 처럼 0 이 아닌 값은 남긴다.
_HIDDEN_DECL_RE = re.compile(
    r"^\s*(?:display\s*:\s*none"
    r"|visibility\s*:\s*(?:hidden|collapse)"
    r"|opacity\s*:\s*0(?:\.0+)?)"
    r"(?:\s*!\s*important)?\s*$",
    re.I,
)

# Docling 은 <li> 의 본문을 읽을 때 하위 <ul>/<ol> 텍스트를 먼저 제외한 뒤,
# <li> 의 *직접 자식* 목록만 별도로 순회한다. 따라서 웹 UI 에 흔한
#   <li><div class="content"><ul>...</ul></div></li>
# 구조는 안쪽 목록 전체가 누락된다. 아래 태그들은 문서 의미보다 레이아웃을 위한
# 투명 컨테이너로 보고, 목록과 가장 가까운 <li> 사이에 있을 때만 unwrap 한다.
_TRANSPARENT_LIST_WRAPPERS = frozenset(
    {"div", "section", "article", "aside", "details", "span"}
)

# 접힌 본문으로 판정할 class 힌트. 임의의 display:none 을 모두 펼치면 메뉴·로딩 UI·
# 반응형 중복 DOM까지 본문에 섞이므로, 내용 컨테이너로 알려진 이름에만 적용한다.
_COLLAPSIBLE_CONTENT_CLASS_HINTS = (
    "ui_accord_content",
    "accordion-content",
    "accordion-collapse",
    "desc_wrap",
)

# ── flatten 사전 검사 ────────────────────────────────────────────────────────────
# 본문이 속성 안에 escape 되어 있는지는 '구조적 사실'이라 임계값 없이 원문 스캔으로
# 판정된다. bs4 파싱도 필요 없어 4MB 문서가 약 3ms 다(실측). 정상 HTML 9종에서
# 오탐 0건이므로 auto 모드가 기존 .html 동작을 건드리지 않는다.
_SRCDOC_RE = re.compile(r"<iframe[^>]*\ssrcdoc\s*=", re.I)
_ESCAPED_BLOCK_RE = re.compile(r"&lt;\s*(?:div|p|table|section|h[1-6])\b", re.I)

# 이중 인코딩으로 볼 최소 매치 수. 정상 문서가 코드 예시로 `&lt;div&gt;` 를 몇 개
# 보여주는 것과, 본문 전체가 escape 된 것을 구분한다.
_ESCAPED_BLOCK_MIN = 10


def precheck_html(raw: str) -> list[str]:
    """flatten 이 고칠 수 있는 구조적 결함의 사유 목록. 비어 있으면 그대로 파싱 가능.

    주의: 여기서 잡는 건 'flatten 으로 복구 가능한' 결함뿐이다. SPA 가 본문을
    `<script type="application/json">` 하이드레이션 페이로드에만 담은 경우는 잡히지
    않지만, 그건 flatten 으로도 복구되지 않는다(호출측에서 경고만 남긴다).
    """
    reasons: list[str] = []
    if _SRCDOC_RE.search(raw):
        reasons.append("iframe_srcdoc")
    if len(_ESCAPED_BLOCK_RE.findall(raw)) >= _ESCAPED_BLOCK_MIN:
        reasons.append("escaped_html")

    # srcdoc/escape 검사는 수 ms 정규식 경로를 유지한다. DOM 검사는 관련 태그/클래스가
    # 원문에 있을 때만 수행해 일반 HTML 의 auto 경로 비용을 최소화한다.
    lower = raw.lower()
    has_list_candidate = (
        "<li" in lower
        and ("<ul" in lower or "<ol" in lower)
        and any(f"<{name}" in lower for name in _TRANSPARENT_LIST_WRAPPERS)
    )
    has_collapsible_candidate = (
        any(hint in lower for hint in _COLLAPSIBLE_CONTENT_CLASS_HINTS)
        and any(marker in lower for marker in ("display", "visibility", "opacity", "aria-hidden", " hidden"))
    )
    if has_list_candidate or has_collapsible_candidate:
        soup = BeautifulSoup(raw, "html.parser")
        if has_list_candidate and _has_wrapped_nested_list(soup):
            reasons.append("wrapped_nested_list")
        if has_collapsible_candidate and _has_hidden_collapsible_content(soup):
            reasons.append("collapsed_content")
    return reasons


# looks_thin 판정 하한. 이보다 작은 문서는 텍스트가 적은 게 정상일 수 있어 보지 않는다.
THIN_MIN_RAW_SIZE = 100_000


def looks_thin(raw_size: int, text_len: int, min_ratio: float = 0.001) -> bool:
    """원문은 큰데 추출 텍스트가 거의 없는지(= precheck 로 못 잡은 미지의 결함) 판정.

    재파싱 트리거가 아니라 경고 로그용이다. flatten 으로 고칠 수 없는 경우까지
    재파싱하면 정상 문서에 부수효과만 남기 때문.

    기준: THIN_MIN_RAW_SIZE 이상 문서에서 추출 텍스트가 원문 크기의 0.1% 미만.
    (실측 — 깨진 merged.html 은 0.02%, 정상 문서는 7.8~67%)
    """
    if raw_size < THIN_MIN_RAW_SIZE:
        return False
    return text_len < raw_size * min_ratio


def _fully_unescape(text: str) -> str:
    """중첩/이중 HTML escape 를 완전히 해제한다.

    bs4 가 속성값을 이미 한 겹 unescape 하지만, merged 빌더가 이중 인코딩한 경우
    (`&amp;lt;` → `&lt;`) 가 남을 수 있어 더 이상 바뀌지 않을 때까지 반복한다.
    """
    for _ in range(3):
        if "&lt;" not in text and "&gt;" not in text and "&amp;" not in text:
            break
        new = html.unescape(text)
        if new == text:
            break
        text = new
    return text


def _strip_hidden_markers(el: Tag) -> None:
    """요소에서 숨김 '표시'만 떼어낸다(내용·다른 스타일은 그대로).

    docling 백엔드가 이 표시를 보고 내용을 통째로 억제하므로, 보존하려면 표시를
    제거해야 한다 — 모듈 docstring "숨김 '표시'는 떼어내야 한다" 참고.
    """
    for attr in _UNHIDE_ATTRS:
        el.attrs.pop(attr, None)

    style = el.get("style")
    if not isinstance(style, str) or not style:
        return
    decls = [decl for decl in style.split(";") if decl.strip()]
    kept = [decl for decl in decls if not _HIDDEN_DECL_RE.match(decl)]
    if len(kept) == len(decls):
        return  # 숨김 선언이 없었다 — 원본 style 문자열을 건드리지 않는다
    if kept:
        el["style"] = ";".join(kept)
    else:
        del el["style"]


def _is_collapsible_content(el: Tag) -> bool:
    """실제 문서 본문을 담는 아코디언/접힘 컨테이너인지 보수적으로 판정한다."""
    classes = el.get("class") or []
    if isinstance(classes, str):
        classes = classes.split()
    normalized = {str(cls).strip("\\\"'").lower() for cls in classes}
    return any(
        hint in cls
        for cls in normalized
        for hint in _COLLAPSIBLE_CONTENT_CLASS_HINTS
    )


def _has_hidden_marker(el: Tag) -> bool:
    if el.has_attr("hidden"):
        return True
    aria_hidden = el.get("aria-hidden")
    if isinstance(aria_hidden, str) and aria_hidden.strip().lower() in {"true", "1", "yes"}:
        return True
    style = el.get("style")
    if not isinstance(style, str):
        return False
    return any(_HIDDEN_DECL_RE.match(decl) for decl in style.split(";") if decl.strip())


def _has_hidden_collapsible_content(node: Tag) -> bool:
    """숨겨진 아코디언 본문이 실제 텍스트/구조를 담고 있는지 확인한다."""
    for el in node.find_all(True):
        if not _is_collapsible_content(el) or not _has_hidden_marker(el):
            continue
        if el.get_text(" ", strip=True) or el.find(("table", "ul", "ol", "img")) is not None:
            return True
    return False


def _transparent_wrapper_chain(sublist: Tag, owner: Tag) -> list[Tag] | None:
    """sublist 와 소유 <li> 사이가 투명 컨테이너뿐이면 안쪽부터 그 체인을 반환한다."""
    chain: list[Tag] = []
    parent = sublist.parent
    while parent is not owner:
        if not isinstance(parent, Tag) or parent.name not in _TRANSPARENT_LIST_WRAPPERS:
            return None
        chain.append(parent)
        parent = parent.parent
    return chain


def _has_wrapped_nested_list(node: Tag) -> bool:
    """Docling 이 놓치는 `<li> ... wrapper ... <ul|ol>` 구조가 있는지 판정한다."""
    for sublist in node.find_all(("ul", "ol")):
        owner = sublist.find_parent("li")
        if owner is None or sublist.parent is owner:
            continue
        if _transparent_wrapper_chain(sublist, owner) is not None:
            return True
    return False


def _normalize_wrapped_nested_lists(node: Tag) -> int:
    """감싸진 중첩 목록을 가장 가까운 `<li>`의 직접 자식으로 만든다.

    목록 자체를 이동하면 wrapper 안의 앞/뒤 문장 순서가 바뀔 수 있다. 대신 사이의
    레이아웃 컨테이너를 안쪽부터 unwrap 해 모든 자식의 DOM 순서를 그대로 보존한다.
    의미 있는 태그가 하나라도 사이에 있으면 건드리지 않는다.
    """
    normalized = 0
    for sublist in list(node.find_all(("ul", "ol"))):
        owner = sublist.find_parent("li")
        if owner is None or sublist.parent is owner:
            continue
        chain = _transparent_wrapper_chain(sublist, owner)
        if chain is None:
            continue
        for wrapper in chain:
            # 앞 목록 처리로 이미 풀린 공유 wrapper 는 부모가 없을 수 있다.
            if wrapper.parent is not None:
                wrapper.unwrap()
        if sublist.parent is owner:
            normalized += 1
    return normalized


def _lift_table_captions(node: Tag) -> None:
    """`<caption>` 을 표 앞의 `<p>` 로 옮긴다 — docling 은 caption 을 버린다."""
    for caption in node.find_all("caption"):
        table = caption.find_parent("table")
        text = caption.get_text(" ", strip=True)
        caption.decompose()
        if table is None or not text:
            continue
        para = Tag(name="p")
        para.string = text
        table.insert_before(para)


def _clean(node: Tag) -> None:
    """콘텐츠 노드에서 노이즈를 제거한다.

    요소를 지우는 기준은 `hidden` 속성뿐이다 — 모듈 docstring 의 측정 근거 참고.
    `aria-hidden`/`display:none` 은 실제 본문(혜택 텍스트, 접힌 약관)을 담고 있어
    지우지 않고, 대신 그 **표시만** 떼어내 docling 이 억제하지 않게 한다.
    """
    for tag in node.find_all(_STRIP_TAGS):
        try:
            tag.decompose()
        except (AttributeError, ValueError):
            pass  # 부모가 먼저 제거되어 이미 사라진 경우
    # 루트 자신도 대상이다 — find_all(True) 은 자신을 포함하지 않는다.
    for el in [node, *node.find_all(True)]:
        # find_all 은 정적 리스트라, 조상을 먼저 제거하면 그 자손은 이미 분리되어
        # (attrs=None) 있을 수 있다 → 건너뛴다.
        if el.attrs is None:
            continue
        if el is not node and el.has_attr("hidden"):
            # 접힌 약관/FAQ 본문은 hidden 이어도 적재해야 한다. 일반 hidden 요소는 기존
            # 정책대로 제거해 메뉴·템플릿·반응형 중복 콘텐츠 유입을 막는다.
            if _is_collapsible_content(el):
                el.attrs.pop("hidden", None)
            else:
                el.decompose()
                continue
        _strip_hidden_markers(el)
    _normalize_wrapped_nested_lists(node)
    _lift_table_captions(node)
    # facebook 추적 픽셀 등 1x1 트래커 제거
    for img in node.find_all("img"):
        src = (img.get("src") or "").lower()
        if "facebook.com/tr" in src or "/tr?" in src:
            img.decompose()


def extract_content(doc_html: str) -> Tag:
    """단일 페이지 HTML 에서 docling 에 넣을 '정리된 콘텐츠 노드'를 뽑는다.

    body(없으면 전체)에서 콘텐츠 영역 셀렉터로 본문을 고르고(폴백: body),
    노이즈를 제거한 뒤 그 노드를 반환한다.
    """
    soup = BeautifulSoup(doc_html, "html.parser")
    root = soup.body or soup
    node: Tag | None = None
    for sel in _CONTENT_SELECTORS:
        node = root.select_one(sel)
        if node is not None:
            break
    if node is None:
        node = root
    _clean(node)
    return node


def build_docling_document(title: str, sections: list[tuple[str, Tag]]) -> str:
    """정리된 섹션들을 heading 기반의 단일 클린 문서로 합친다.

    docling HTML 백엔드는 첫 non-table heading 부터 본문 레이어(BODY)를 켜므로
    (`docling/backend/html_backend.py` convert() 의 furniture 규칙), 각 페이지를
    <h2> 로 시작시켜 본문이 확실히 BODY 로 분류되게 한다.
    """
    parts = [
        '<!doctype html><html lang="ko"><head><meta charset="utf-8">',
        f"<title>{html.escape(title)}</title></head><body>",
        f"<h1>{html.escape(title)}</h1>",
    ]
    for label, node in sections:
        parts.append(f"<section><h2>{html.escape(label)}</h2>")
        parts.append(str(node))
        parts.append("</section>")
    parts.append("</body></html>")
    return "".join(parts)


# merged.html 섹션 제목(<h2 class="page-title">N. label <span class="page-file">…</span>)
# 에서 앞의 "N. " 순번을 떼기 위한 패턴.
_SECTION_NUM_RE = re.compile(r"^\s*\d+\.\s*")


def _section_label(section: Tag, idx: int) -> str:
    """merged.html 의 <section class="page-section"> 에서 사람이 읽는 라벨을 복원한다."""
    h2 = section.select_one("h2.page-title")
    if h2 is None:
        return f"섹션 {idx}"
    file_span = h2.select_one(".page-file")
    if file_span is not None:
        file_span.extract()  # "파일명" 부가정보 제거
    label = _SECTION_NUM_RE.sub("", h2.get_text(strip=True))
    return label or f"섹션 {idx}"


def iter_srcdoc_sections(raw: str) -> list[tuple[str, str]]:
    """merged.html 에서 (라벨, 펼친 페이지 HTML) 목록을 뽑는다.

    표준 형태(section.page-section > iframe[srcdoc])를 우선 시도하고, 없으면
    iframe 전체를 훑는다. 정리는 하지 않는다 — 호출측이 extract_content 로 한다.
    """
    soup = BeautifulSoup(raw, "html.parser")
    out: list[tuple[str, str]] = []

    page_sections = soup.select("section.page-section")
    if page_sections:
        for idx, section in enumerate(page_sections, 1):
            iframe = section.find("iframe")
            srcdoc = iframe.get("srcdoc") if iframe else None
            if not srcdoc:
                continue
            out.append((_section_label(section, idx), _fully_unescape(srcdoc)))
        return out

    for idx, iframe in enumerate(soup.find_all("iframe"), 1):
        srcdoc = iframe.get("srcdoc")
        if not srcdoc:
            continue
        out.append((f"섹션 {idx}", _fully_unescape(srcdoc)))
    return out


def document_title(raw: str, fallback: str = "") -> str:
    """<title> 텍스트(없으면 fallback)를 돌려준다."""
    soup = BeautifulSoup(raw, "html.parser")
    tag = soup.find("title")
    text = tag.get_text(strip=True) if tag else ""
    return text or fallback


def flatten_html(raw: str, title: str = "", reasons: list[str] | None = None) -> str:
    """srcdoc 형태의 HTML 을 펼쳐 docling 이 읽을 수 있는 단일 클린 문서로 만든다.

    srcdoc 섹션이 없으면(= 단일 페이지 HTML) 그 페이지 자체를 정리해 한 섹션짜리
    문서로 만든다. 그래서 `flatten: always` 모드에서도 안전하게 호출할 수 있다.

    `reasons` 는 호출측이 이미 계산해 둔 precheck_html 결과다. 넘기지 않으면 내부에서
    계산한다 — 대용량 문서를 정규식으로 한 번 더 전수 스캔하지 않기 위한 선택 인자.
    """
    doc_title = title or document_title(raw, "document")
    pages = iter_srcdoc_sections(raw)
    if pages:
        # srcdoc 섹션은 iter_srcdoc_sections 가 이미 unescape 했다(중복 적용 금지).
        sections = [(label, extract_content(page)) for label, page in pages]
    else:
        # iframe 없이 본문 전체가 escape 된 경우. 여기서 풀지 않으면 bs4 가
        # `&lt;table&gt;` 을 텍스트 노드로 읽어 표/헤딩 구조가 통째로 사라진다.
        if reasons is None:
            reasons = precheck_html(raw)
        source = _fully_unescape(raw) if "escaped_html" in reasons else raw
        sections = [(doc_title, extract_content(source))]
    return build_docling_document(doc_title, sections)
