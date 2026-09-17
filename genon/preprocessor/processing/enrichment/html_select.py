"""HTML 원문에서 선택자로 목표필드 값을 뽑는다(extractor: html_select).

## 왜 필요한가

카드 WCMS 계열 원천은 뽑아야 할 값이 **class 와 속성으로 이미 정확히 지목**되어 있다.
관심소식 요건표가 그 예다 — 제목은 `.new-banner-headline`, 카테고리는 wrap div 의
`newsletter-title` 속성, 상세는 `.newslertter-article-content`(원천 마크업의 오타를
포함한 이름 그대로)다.

그런데 문서 단위 추출기(`llm`/`python`)가 받는 입력은 `document.export_to_text()` —
docling 이 파싱을 마친 **평문**이라 class 도 속성도 남지 않는다. 실측하면 이렇다.

  | 신호                              | 파싱 후 |
  |-----------------------------------|---------|
  | class 이름                        | 사라짐  |
  | 사용자 속성(`newsletter-title`)   | 사라짐  |
  | HTML 주석                         | 사라짐  |
  | 텍스트, `img` 의 `alt`, `.sr-only`| 남음    |

그래서 `llm` 으로 같은 요건을 풀려면 프롬프트가 class 가 아니라 "몇 번째 줄" 같은
순서에 기대야 한다. 마크업이 바뀌면 조용히 틀리고, 상세 본문처럼 긴 값은 모델이 통째로
베껴 쓰느라 문서마다 수천 자를 왕복한다. 근거가 마크업에 있는 값은 마크업에서 뽑는 것이
맞고, 이 모듈이 그 자리다.

## 동작

원문 HTML 은 파서가 `_enrichment_context` 에 실어 준다(`stash_source_html`). 선택자는
설정에서 컴파일해 두고(`compile_selectors`) 문서마다 한 번 훑는다. LLM 호출이 없으므로
연결 설정도, 환각 방지 프롬프트도, 응답 파싱도 필요 없다.

값이 만들어진 뒤의 파이프라인(`default` → `const` → `values` → `transform` → `derive`
→ `pack` → `body`)은 다른 extractor 와 **같은 것을 그대로 쓴다.** 이 모듈은 값만 만든다.

## 뽑은 값의 모양

`transform: html_text` 와 **같은 렌더러**로 평문화한다(`render_field_text(kind="html")`).
표·목록 구조가 남고, 짧은 필드는 그냥 한 줄로 나온다. 설정에 모양 스위치를 따로 두지
않은 이유다 — 이미 있는 변환기와 결과가 같으면 개념을 하나 더 만들 이유가 없다.

`attr` 을 주면 그 속성값을 문자열 그대로 쓴다(평문화하지 않는다).

## 한계

선택자는 원천 마크업에 결합된다. WCMS 템플릿이 class 를 바꾸면 값이 None 이 된다.
그 자체는 순서 의존 프롬프트도 같지만, 이쪽은 무엇이 안 잡혔는지 로그로 드러난다.

HTML 주석 안의 요소는 잡지 않는다. 주석은 원천이 "꺼 둔 것"이라는 뜻이고, 그것을 되살리는
판단은 파싱기가 아니라 원천이 해야 한다(관심소식의 `.newsletter-source` 가 실제로 주석
처리되어 들어온다 - 값은 None 이 되고 경고가 남는다).
"""

from __future__ import annotations

import logging
from typing import Any

_log = logging.getLogger(__name__)

# `_enrichment_context` 안에서 원문 HTML 이 앉는 자리. 파서가 담고 이 모듈이 읽는다.
# `metadata` 와 달리 응답으로 새어 나가지 않는다(_docling_response 는 metadata 만 읽는다).
SOURCE_HTML_KEY = "source_html"


def compile_selectors(cfg: dict, *, label: str) -> dict[str, dict]:
    """`select_map` 을 검증해 컴파일한다. 잘못된 선택자는 기동 시 실패한다.

    문법 오류를 요청 시점까지 끌고 가면 "값이 안 나온다"로만 보이고, 선택자가 틀렸는지
    원천이 바뀌었는지 구분할 수 없다.
    """
    raw = cfg.get("select_map") or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{label}: select_map 은 '목표필드: 선택자' 형태여야 합니다.")

    import soupsieve

    compiled: dict[str, dict] = {}
    for target, spec in raw.items():
        where = f"{label}: fields.{target}"
        if not isinstance(spec, dict):
            raise ValueError(f"{where}: 선택자 스펙이 object 여야 합니다.")
        css = str(spec.get("css") or "").strip()
        if not css:
            raise ValueError(f"{where}: select 값이 비어 있습니다.")
        try:
            soupsieve.compile(css)
        except Exception as exc:  # noqa: BLE001 - soupsieve 예외 종류가 버전마다 다르다
            raise ValueError(f"{where}: 선택자를 해석할 수 없습니다({css!r}): {exc}") from exc
        attr = spec.get("attr")
        attr = str(attr).strip() if attr not in (None, "") else None
        compiled[str(target)] = {"css": css, "attr": attr}
    return compiled


def extract_fields(html: str, selectors: dict[str, dict]) -> dict[str, Any]:
    """HTML 한 벌에서 선언한 목표필드를 모두 만든다.

    선언한 필드는 **못 찾아도 키를 남긴다**(값 None). 키가 사라지면 그 필드는 청크에서
    조용히 빠지고, 나중에 "왜 없지"를 적재 데이터에서 발견하게 된다.
    """
    if not selectors:
        return {}

    from bs4 import BeautifulSoup

    from .field_transforms import render_field_text

    soup = BeautifulSoup(html or "", "html.parser")
    values: dict[str, Any] = {}
    missed: list[str] = []
    for target, spec in selectors.items():
        element = soup.select_one(spec["css"])
        if element is None:
            values[target] = None
            missed.append(f"{target}({spec['css']})")
            continue
        if spec["attr"]:
            raw = element.get(spec["attr"])
            values[target] = str(raw).strip() or None if raw is not None else None
        else:
            # `transform: html_text` 와 같은 렌더러 — 표·목록 구조가 남는다.
            values[target] = render_field_text(element.decode_contents(), kind="html")
    if missed:
        _log.warning(
            f"[html_select] 선택자에 걸리지 않아 값이 비었습니다: {', '.join(missed)} "
            f"— 원천 마크업이 바뀌었거나 해당 요소가 주석 처리된 문서일 수 있습니다."
        )
    return values


def stash_source_html(context: Any, html: str) -> None:
    """파서가 원문 HTML 을 enrichment 쪽에 넘긴다.

    `context` 는 파서와 enrichment 가 공유하는 `_enrichment_context` dict 다. dict 가
    아니거나 내용이 비면 아무것도 하지 않는다 — 이 통로는 있으면 쓰고 없으면 마는
    보조 입력이라, 없다고 파싱을 실패시키지 않는다.
    """
    if isinstance(context, dict) and html:
        context[SOURCE_HTML_KEY] = html


def source_html_from(kwargs: dict) -> str:
    """enricher 쪽에서 원문 HTML 을 꺼낸다. 없으면 빈 문자열."""
    context = (kwargs or {}).get("_enrichment_context")
    if not isinstance(context, dict):
        return ""
    return str(context.get(SOURCE_HTML_KEY) or "")
