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

`<caption>`("라이트할부 기간동안 추가 결제 안내입니다." 같은 표 설명)은 손대지 않고 그대로
넘긴다. 예전에는 docling 이 caption 을 버려서 표 앞의 `<p>` 로 옮겨 살렸는데, 그 우회책은
캡션을 본문 문단으로 만들어 `TableItem.captions` 를 비워 놓는다 — 표 설명이 어느 표의 것인지
잃어버리므로 청커·enrichment 가 표 설명으로 쓸 수 없다. 백엔드가 `<caption>` 을 캡션 아이템으로
만들도록 고쳐졌으니(`html_backend._emit_table_caption`) 전처리에서 미리 뜯어내면 안 된다.

## 마커만으로 계층을 표현한 문서

모니모 고객센터 카드 HTML(doc_type=cs_hpp)은 `<h1>` 하나만 갖고 화면상 섹션 계층을
◈/▣/■ 같은 도형 마커 문자로만 표현한다(예: `◈ 시행일자`/`◈ 대상카드`/`◈ 기본내용`
같은 상위 구획, `▣ 네이버페이 간편결제 이용방법` 같은 중간 구획, `<td>` 안의
`■ 카드 등록방법 및 화면` 같은 하위 구획). 마크업은 전부 `<p>` 또는 `<td><p>` 다.
docling HTML 백엔드는 `SECTION_HEADER` 를 `_handle_heading`(h1~h6 전용,
`docling/backend/html_backend.py:2075-2136`) 한 곳에서만 만들고 h1~h6 외
heading 승격 휴리스틱이 전무해서, 이런 문서는 `SectionHeaderItem` 이 0개가 된다.
실측 — 운영 산출 청크 6개 전부 접두어가 `HEADER: [AI 에이전트용]`로 동일하고
(n_char 683~1326), 청크 경계는 ◈/▣ 마커와 무관하게 chunk_size 로만 절단된다.

`docling/backend/genos_hwp_backend.py`(상수 `:77-118`, 판정 `:211-269`)에 이미
마커 기반 heading 승격이 있다. `_SENTENCE_FINAL`/`_HEADING_MAX_LEN` 은 그 값과
정규식을 그대로 복사했다 — 다만 그 모듈은 import 하지 않는다. 임포트 시점에
`os.environ["PATH"]`/`LD_LIBRARY_PATH` 를 변조하고 SDK 부재 경고를 찍기 때문이다
(`genos_hwp_backend.py:60-70`).

hwp 의 "약한 마커 AND 강조(bold/큰폰트)" 규칙(`genos_hwp_backend.py:264-266`)은
이식하지 않는다. 캡쳐 실측에서 `- 2015년 6월 25일` 은 `-` 마커 + 100% bold +
20자 + 명사 종결이라 강등 조건 어디에도 안 걸려 오승격된다. 게다가 이 문서
계열은 인라인 `font-size` 가 실측 27건 중 26건이 `revert` 라 baseline 계산이
불가능하고, `<b>`/`<strong>`/`font-weight` 는 0건이다
(`sample_files/monimo/.INC_235488_02_20260626103138.html`). 그래서 강조 신호는
쓰지 않고, 대신 좁힌 도형 마커 화이트리스트 + 문서단위 구조 게이트로 대체한다
(아래 `_MARKER_*` 상수와 `promote_marker_headings`).

추가 실측(2026-09-01 캡쳐, `sample_files/monimo/monimo_cs_hpp_marker_nospace_sample.html`
와 `..._marker_real_dom_sample.html` 로 전사)은 같은 결론을 다른 각도에서 확인해 준다.
이 문서들은 위와 달리 `font-weight:700` 을 대량으로 쓰는데, 소제목(`◆개요`)과 본문
(`-2025년 11월 22일(토)`)이 **둘 다** bold 다. 강조는 여기서도 신호가 아니다.
같은 캡쳐가 마커 뒤 공백을 필수로 두면 안 된다는 것도 보여준다 — 소제목이 `◆개요`,
`▣[홈페이지]서비스 신청 및 해지 방법` 처럼 마커와 붙어 있다(`_MARKER_HEADING_RE` 주석 참고).

이 승격은 srcdoc 이 없는 단일 페이지 HTML 에만 걸린다는 한계가 있다. srcdoc 로
합쳐진 merged 문서는 본문이 속성값 안에 escape 되어 있어 원문 스캔(precheck)이
마커를 보지 못하고, 애초에 페이지 라벨 h2 를 이미 갖고 있어 기존 하위 heading
개수 게이트에서 대상 밖으로 걸러진다.

의존성: beautifulsoup4 (docling 이 자체 HTML 백엔드에서 쓰므로 전이 확보) + 표준 라이브러리.
"""
from __future__ import annotations

import html
import logging
import re

from bs4 import BeautifulSoup
from bs4.element import NavigableString, Tag

_log = logging.getLogger(__name__)

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

# ── 마커 기반 heading 승격 ──────────────────────────────────────────────────
# 모듈 docstring "마커만으로 계층을 표현한 문서" 절 참고. hwp 선례
# (docling/backend/genos_hwp_backend.py, import 는 하지 않는다)에서 값을 그대로
# 가져온 상수는 각각 표기했다.

# 소제목으로 승격할 도형 마커 화이트리스트. hwp `_HEADING_WEAK_MARKER`
# (genos_hwp_backend.py:105-109) 집합에서 글머리표·주석 마커(- · • * ∙ ◦ ※ →
# ①-⑳, 가., (1))를 뺐다. 캡쳐 실측 — 소제목은 예외 없이 ◈/▣/■로 시작하고
# 본문은 예외 없이 -/※/→로 시작한다(오탐·미탐 0). bold 는 양쪽에 섞여 있어
# 판별 신호로 쓸 수 없다. ●/○/▶는 이 문서엔 없지만 한국 기업문서 소제목으로
# 흔해 포함했고, 본문 포인터로 이 문자를 쓰는 문서의 오탐은 아래 문서단위
# 게이트(_MARKER_MAX_EXISTING_SUBHEADINGS, _MARKER_HEADING_MIN)와 강등 규칙
# (_SENTENCE_FINAL, _HEADING_MAX_LEN)이 막는다.
_MARKER_CHARS = "◈◆◇▣■□❏●○▶▷"

_SUBHEADING_TAG_RE = re.compile(r"<h[2-6][\s>/]", re.I)

# 이미 하위 heading 으로 계층을 표현한 문서는 승격 대상이 아니다. 실측 —
# 문제 문서(모니모 cs_hpp)는 h1 1개 + h2 0~1개뿐이고, 기존 픽스처는
# monimo_cs_hpp_sample.html h2×4 / monimo_cs_hpp_chunksize_sample.html h2×6
# 이라 이 게이트 하나로 갈린다. 표 안 h태그까지 세는 보수적 상한이다(원문
# 전체 개수가 이 상한을 통과했다면, 그 부분집합인 표 밖 개수도 상한 이하다).
_MARKER_MAX_EXISTING_SUBHEADINGS = 1

# 1~2개면 마커를 장식으로 쓴 문서와 구분할 수 없고, 헤더 1개는 섹션 경계를
# 하나도 만들지 못해 승격해도 이득이 없다. 실측 — 캡쳐 문서의 표 밖 후보는
# ◈ 4개(시행일자·대상카드·기본내용·예상Q&A) + ▣ 3개(간편결제 이용방법·서비스
# 등록방법 및 화면·참고사항) = 7개다.
_MARKER_HEADING_MIN = 3

# 마커 뒤 공백은 선택이다. 초기 규칙은 공백을 필수로 뒀지만 실측이 이를 뒤집었다 —
# 캡쳐(genon/_tmp_docs/20260901_캡쳐/03_고객_카드_문서_05, INC_19570012)의 소제목은
# `◆개요` / `▣[홈페이지]서비스 신청 및 해지 방법` 처럼 마커와 제목이 붙어 있다.
# 게다가 공백 필수 규칙은 저자가 아니라 DOM 우연에 반응한다. `get_text(" ")` 가 태그
# 경계에 공백을 넣으므로, 같은 `▣제목` 표기라도 마커와 제목이 다른 span 이면 통과하고
# 한 span 안에 있으면 탈락한다(픽스처 marker_real_dom 의 `▣ 처리가능 업무` 대 픽스처
# marker_nospace 의 `◆개요`). 공백 필수가 막던 `■■■` 구분선 장식은 아래
# `_MARKER_DECORATION_RE` 가 대신 막고, `◈결제` 류 본문 강조는 후보 조건이 "블록 자식
# 없는 p/div 한 줄 전체"라 문장 중간에서는 애초에 매칭되지 않는다. 한 줄 전체가 `◈결제`
# 면 소제목으로 보는 편이 맞다.
_MARKER_HEADING_RE = re.compile(rf"^([{_MARKER_CHARS}])\s*(\S.*)$", re.S)

# 제목이 다시 마커 문자로 시작하면 제목이 아니라 마커 반복 장식선이다(`■■■`, `◈◈`).
_MARKER_DECORATION_RE = re.compile(rf"^[{_MARKER_CHARS}]")

_MARKER_TITLE_MIN_LEN = 2

# hwp 선례(genos_hwp_backend.py:112)와 동일한 값·정규식. 한국어 서술형 본문은
# 종결형 '다./함/음/됨/임' 또는 의문형 '까?'로 끝나는 경우가 많고, 제목은
# 보통 명사로 끝난다.
_SENTENCE_FINAL = re.compile(r"(?:다|함|음|됨|임)[.)\]」』】〉]?\s*$|까[?]\s*$")

# hwp 선례(genos_hwp_backend.py:114)와 동일한 값. 마커가 있어도 이 길이를
# 넘으면 제목이 아니라 본문으로 본다.
_HEADING_MAX_LEN = 80

_PROMOTABLE_TAGS = frozenset({"p", "div"})  # li 는 승격하면 목록 구조가 깨지므로 제외
_BLOCK_CHILD_TAGS = ("p", "div", "table", "ul", "ol", "li", "br",
                     "h1", "h2", "h3", "h4", "h5", "h6", "section", "article")

# build_docling_document(:354-371 부근)가 h1(문서 title) + h2(섹션 라벨)를
# 이미 주입하므로, 마커 heading 은 h3 부터 써야 주입된 레벨과 충돌하지 않는다.
_MARKER_HEADING_BASE_LEVEL = 3
_MARKER_HEADING_MAX_LEVEL = 6


def precheck_html(raw: str, *, detect_marker_headings: bool = False) -> list[str]:
    """flatten 이 고칠 수 있는 구조적 결함의 사유 목록. 비어 있으면 그대로 파싱 가능.

    주의: 여기서 잡는 건 'flatten 으로 복구 가능한' 결함뿐이다. SPA 가 본문을
    `<script type="application/json">` 하이드레이션 페이로드에만 담은 경우는 잡히지
    않지만, 그건 flatten 으로도 복구되지 않는다(호출측에서 경고만 남긴다).

    `detect_marker_headings` 는 opt-in 이고 기본값은 False 다. 이게 핵심이다 —
    기존 호출부와 테스트(tests/unit/test_html_flatten_unit.py) 계약이 그대로
    유지되고, facade/enrichment/json_records.py:309-318 의 extract_content
    경로와 다른 doc_type 의 auto 모드 동작이 이 인자로 전혀 바뀌지 않는다.
    모니모 cs_hpp 처럼 마커 승격을 원하는 호출측만 명시적으로 켠다.

    한계: 이 함수는 원문 DOM(전처리 전)을 본다. `iframe srcdoc` 로 합쳐진 merged
    문서는 마커 후보가 속성값 안에 escape 되어 있어 여기서 보이지 않으므로
    "marker_headings" 사유가 뜨지 않는다. 그 문서들은 이미 페이지 라벨 h2 를
    갖고 있어 애초에 _MARKER_MAX_EXISTING_SUBHEADINGS 게이트에서 탈락하므로
    동작은 일관된다 — 즉 승격은 실질적으로 단일 페이지 HTML 에만 걸린다.
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
    # 문서 전체가 이미 하위 heading 으로 계층을 표현했다면(_MARKER_MAX_EXISTING_
    # SUBHEADINGS 초과) 마커 승격 대상이 아니므로 bs4 파싱까지 가지 않는다.
    has_marker_candidate = (
        detect_marker_headings
        and any(ch in raw for ch in _MARKER_CHARS)
        and len(_SUBHEADING_TAG_RE.findall(raw)) <= _MARKER_MAX_EXISTING_SUBHEADINGS
    )
    if has_list_candidate or has_collapsible_candidate or has_marker_candidate:
        soup = BeautifulSoup(raw, "html.parser")
        if has_list_candidate and _has_wrapped_nested_list(soup):
            reasons.append("wrapped_nested_list")
        if has_collapsible_candidate and _has_hidden_collapsible_content(soup):
            reasons.append("collapsed_content")
        if has_marker_candidate and len(_marker_heading_candidates(soup)) >= _MARKER_HEADING_MIN:
            # promote_marker_headings 와 같은 스캐너(_marker_heading_candidates)를
            # 공유한다 — "사유는 떴는데 승격은 0건"인 드리프트를 원천 차단한다.
            reasons.append("marker_headings")
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


def _text_after_first_break(el: Tag) -> str:
    """요소 안 첫 <br> 뒤에 남는 텍스트.

    `get_text("\n")` 은 <br> 이 아니라 모든 텍스트 노드 경계에 구분자를 넣으므로
    `▣ <span>이용방법</span><br>` 을 두 줄로 잘못 센다. 문서 순서대로 훑어 <br> 이후의
    텍스트만 모은다.
    """
    seen_break = False
    tail: list[str] = []
    for node in el.descendants:
        if getattr(node, "name", None) == "br":
            seen_break = True
        elif seen_break and isinstance(node, NavigableString):
            tail.append(str(node))
    return "".join(tail).strip()


def marker_heading_match(text: str) -> tuple[str, str] | None:
    """한 줄 텍스트가 "마커 + 짧은 제목" 규칙을 만족하면 `(마커, 제목)`, 아니면 None.

    HTML 과 Markdown 이 이 판정을 공유한다. 마커 집합·길이 상한·종결형/장식 강등 규칙은
    문서 포맷이 아니라 한국 기업문서 관례에서 나온 것이라 두 경로가 같아야 한다 —
    한쪽에만 규칙을 복사하면 같은 원문이 포맷에 따라 다르게 쪼개진다.
    DOM 에만 있는 조건(블록 자식·표 안·`<br>`)은 호출측이 따로 본다.
    """
    match = _MARKER_HEADING_RE.match(text)
    if not match:
        return None
    if len(text) > _HEADING_MAX_LEN:
        return None
    if _SENTENCE_FINAL.search(text):
        return None  # 서술형 본문 문장 — 제목이 아니다
    marker, title = match.group(1), match.group(2)
    if _MARKER_DECORATION_RE.match(title):
        return None  # `■■■` 처럼 마커를 반복한 구분선 장식 — 제목이 아니다
    if len(title.strip()) < _MARKER_TITLE_MIN_LEN:
        return None
    return marker, title


def _marker_heading_candidates(node: Tag) -> list[tuple[Tag, str]]:
    """"마커 + 짧은 제목" 한 줄짜리 블록을 문서 순서로 찾는다.

    모듈 docstring "마커만으로 계층을 표현한 문서" 절 참고. 반환은
    (요소, 마커 문자) 쌍의 목록이며, 아래 조건을 모두 만족해야 후보가 된다.
    """
    candidates: list[tuple[Tag, str]] = []
    for el in node.find_all(True):
        if el.name not in _PROMOTABLE_TAGS:
            continue  # li 는 목록 구조가 깨지므로 제외
        if el.find_parent("table") is not None:
            # 표 안 heading 은 두 가지 이유로 제외한다.
            # (a) docling 의 clean_headers(html_backend.py:534-548)가
            #     find_parent("table") heading 을 furniture 규칙에서 빼버린다.
            # (b) 실측 — <td> 안 heading 은 SectionHeaderItem 을 만들되 자기
            #     TableItem 뒤에 배치되어(document.py:5999-6031 의 자식 순회가
            #     PictureItem 만 가드) 표가 직전 섹션에 귀속되고 셀 텍스트가
            #     표 마크다운과 중복된다. 청커가 _is_section_header 로 섹션을
            #     끊어 표가 셀 중간에서 쪼개져 "완전한 표 조각" 불변식
            #     (커밋 a6e4241c, tests/unit/test_doc_type_chunk_size.py:186-217)
            #     이 깨진다.
            continue
        if any(t.name != "br" for t in el.find_all(_BLOCK_CHILD_TAGS)):
            # 인라인 <b>/<span>/<a>/<em> 자식은 허용한다 — 스마트에디터가
            # 제목 문단을 이런 인라인 태그로 감싸는 경우가 흔하다.
            continue
        if el.find("br") is not None and _text_after_first_break(el):
            # <br> 은 후행 한 개까지만 허용한다. 스마트에디터가 제목 문단 끝에 습관적으로
            # 붙이는데(픽스처 marker_real_dom 의 `▣ 이용방법<br>`), 이걸 블록 자식으로
            # 취급하면 소제목 하나가 통째로 후보에서 빠진다. 반대로 <br> 이 텍스트를 두 줄
            # 이상으로 쪼갰다면 한 줄짜리 제목이 아니라 여러 줄 본문이므로 승격하지 않는다.
            continue
        matched = marker_heading_match(el.get_text(" ", strip=True))
        if matched is None:
            continue
        candidates.append((el, matched[0]))
    return candidates


def _marker_levels(markers: list[str]) -> dict[str, int]:
    """마커 문자별 heading 레벨을 문서 내 첫 등장 순서로 배정한다.

    개요 문서는 하위 항목보다 상위 항목을 반드시 먼저 소개하므로, 첫 등장
    순서가 곧 깊이 순서라고 본다. 마커 문자 → 레벨을 하드코딩한 목록은
    마커 사이의 깊이 서열(예: ◈ 가 ▣ 보다 위인지)에 측정 근거가 없어 채택하지
    않았다.
    """
    levels: dict[str, int] = {}
    next_level = _MARKER_HEADING_BASE_LEVEL
    for marker in markers:
        if marker in levels:
            continue
        levels[marker] = min(next_level, _MARKER_HEADING_MAX_LEVEL)
        next_level += 1
    return levels


def promote_marker_headings(node: Tag) -> int:
    """마커로 시작하는 단락을 heading 태그로 제자리 승격한다.

    자식·속성·문서 순서는 그대로 두고 태그 이름만 `h{level}` 로 바꾼다(rename).
    승격 대상은 `_marker_heading_candidates`, 레벨 배정은 `_marker_levels` 를
    그대로 쓴다 — precheck_html 의 사유 판정과 같은 스캐너를 공유해 "사유는
    떴는데 승격은 0건"인 드리프트를 막는다.

    마커 문자는 텍스트에서 제거하지 않는다 — 저자가 쓴 라벨이고, 청커
    breadcrumb 이 쓰는 값이 item.orig(facade/chunking_processor.py:583)라
    "HEADER: [AI 에이전트용] > ◈ 기본내용 > ▣ 네이버페이 서비스 등록방법 및
    화면"처럼 원문 그대로 읽힌다.

    반환값은 승격 건수다(0 이면 이 노드엔 대상이 없었다는 뜻).
    """
    candidates = _marker_heading_candidates(node)
    if not candidates:
        return 0
    levels = _marker_levels([marker for _el, marker in candidates])
    for el, marker in candidates:
        el.name = f"h{levels[marker]}"
    # 배정 결과는 여기서 남긴다 — 레벨이 문서 내 등장 순서로 정해지므로, 산출물만
    # 보고는 왜 그 레벨이 됐는지 알 수 없다. 호출측에서 따로 스캔해 찍으면 노드마다
    # 독립 배정되는 실제 결과와 어긋날 수 있어(섹션이 여러 개인 경우) 승격과 같은
    # 자리에서 찍는다.
    mapping = ", ".join(f"{marker}→h{level}" for marker, level in levels.items())
    _log.info("[html_flatten] marker_headings: %s (승격 %d건)", mapping, len(candidates))
    return len(candidates)


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

    단일 페이지 경로는 섹션 라벨이 문서 제목과 같은 값이라(`flatten_html` 의 else
    분기), 그 <h2> 가 바로 위 <h1> 과 같은 문자열을 한 번 더 넣는다. 실측 — 청커
    breadcrumb 이 `제목 > 제목 | 원문제목 > ◈ 시행일자` 처럼 같은 이름을 두 번
    거치는 첫 청크를 만든다. 라벨이 제목과 같은 한 섹션짜리 문서에서는 <h2> 를
    생략한다. 이미 주입한 <h1> 이 첫 non-table heading 이므로 BODY 전환 보장은
    그대로다. `label` 이 비어 있지 않을 것을 조건에 넣어, 라벨과 제목을 모두 빈
    문자열로 넘기는 `facade/enrichment/json_records.py` 경로는 건드리지 않는다.
    """
    parts = [
        '<!doctype html><html lang="ko"><head><meta charset="utf-8">',
        f"<title>{html.escape(title)}</title></head><body>",
        f"<h1>{html.escape(title)}</h1>",
    ]
    skip_label = (
        len(sections) == 1
        and bool(sections[0][0].strip())
        and sections[0][0].strip() == title.strip()
    )
    for label, node in sections:
        parts.append("<section>" if skip_label else f"<section><h2>{html.escape(label)}</h2>")
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


def flatten_html(
    raw: str,
    title: str = "",
    reasons: list[str] | None = None,
    marker_headings: bool = False,
) -> str:
    """srcdoc 형태의 HTML 을 펼쳐 docling 이 읽을 수 있는 단일 클린 문서로 만든다.

    srcdoc 섹션이 없으면(= 단일 페이지 HTML) 그 페이지 자체를 정리해 한 섹션짜리
    문서로 만든다. 그래서 `flatten: always` 모드에서도 안전하게 호출할 수 있다.

    `reasons` 는 호출측이 이미 계산해 둔 precheck_html 결과다. 넘기지 않으면 내부에서
    계산한다 — 대용량 문서를 정규식으로 한 번 더 전수 스캔하지 않기 위한 선택 인자.
    이 계산은 함수 선두에서 한다(예전엔 else 분기에서만 계산해 srcdoc 섹션이 있는
    `always` 모드 호출에서 marker_headings 게이트가 항상 죽어 있었다). `_prepare_html`
    이 항상 `reasons` 를 미리 계산해 넘기므로, 실사용 경로에서 재스캔 비용이
    늘어나는 건 아니다 — 지연 계산이라는 기존 설계 의도는 그대로 유지된다.

    `marker_headings=True` 면 srcdoc 을 펼친 각 섹션 노드에 대해
    `promote_marker_headings` 를 적용한다. `reasons` 에 "marker_headings" 가
    없으면(대상 문서가 아니거나 호출측이 넘긴 reasons 에 빠져 있으면) 아무 것도
    하지 않는다 — 판정은 precheck_html 쪽 게이트가 전담한다.
    """
    doc_title = title or document_title(raw, "document")
    if reasons is None:
        reasons = precheck_html(raw, detect_marker_headings=marker_headings)
    pages = iter_srcdoc_sections(raw)
    if pages:
        # srcdoc 섹션은 iter_srcdoc_sections 가 이미 unescape 했다(중복 적용 금지).
        sections = [(label, extract_content(page)) for label, page in pages]
    else:
        # iframe 없이 본문 전체가 escape 된 경우. 여기서 풀지 않으면 bs4 가
        # `&lt;table&gt;` 을 텍스트 노드로 읽어 표/헤딩 구조가 통째로 사라진다.
        source = _fully_unescape(raw) if "escaped_html" in reasons else raw
        sections = [(doc_title, extract_content(source))]
    if "marker_headings" in reasons:
        for _label, node in sections:
            promote_marker_headings(node)  # 배정 로그는 그 함수가 남긴다
    return build_docling_document(doc_title, sections)
