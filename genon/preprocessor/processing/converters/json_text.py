"""JSON 안에 담긴 본문 텍스트(markdown/html)를 꺼내 docling 입력 HTML 로 재조립한다.

## 왜 필요한가

monimo 카드처럼 본문이 JSON 의 특정 key 값(markdown 또는 html 문자열)으로 오는 입력이
있다. 파서의 기존 `.json` 경로는 캐치올(TextLoader)로 흘러 원문을 `<pre>` 로 감싸
PDF 렌더 후 재파싱하므로 표·heading 구조가 전부 소실된다. 이 모듈은 텍스트만 꺼내
단일 클린 HTML 로 합쳐서, 파싱 본체는 기존 docling 경로(`_parse_docling`)를 그대로
재사용하게 한다.

## 키 지정 방식

경로 문법(JSONPath 등) 대신 **키 이름만** 나열한다. JSON 을 재귀 순회해 같은 이름의
키를 임의 깊이에서 문서 순서대로 수집하므로, monimo 의 `pages[*].html` 같은 배열 구조도
별도 문법 없이 처리된다.

## 포맷 판별

값 내용으로 자동 판별한다(config 의 `format` 으로 강제 가능). 오판 시 손실이 비대칭이라
**html 쪽으로 편향**시킨다 — html 을 markdown 으로 오판하면 markdown 변환기가 태그를
망가뜨리지만, markdown 을 html 로 오판해도 docling 의 HTML 백엔드가 평문으로 읽어
텍스트는 남는다.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from bs4 import BeautifulSoup

from .html_flatten import (
    build_docling_document,
    extract_content,
    flatten_html,
    precheck_html,
)

_log = logging.getLogger(__name__)

# 형제 키 중 섹션 라벨로 쓸 후보(우선순위 순).
_LABEL_KEYS = ("name", "title", "label", "section", "heading", "subject")

# 블록 레벨 태그. 인라인 태그(em/strong/code 등)는 markdown 본문에도 흔히 섞이므로
# 판별에 쓰지 않는다.
_BLOCK_TAG_RE = re.compile(
    r"<\s*(?:div|p|table|tr|td|th|thead|tbody|section|article|main|header|footer|nav"
    r"|ul|ol|li|dl|dt|dd|h[1-6]|pre|blockquote|figure|form|iframe)\b[^>]*>",
    re.I,
)

# markdown 구조 마커(줄머리 기준).
_MD_MARKER_RE = re.compile(
    r"^(?:\s{0,3}#{1,6}\s|\s{0,3}[-*+]\s|\s{0,3}\d+\.\s|\s{0,3}>\s|\s*```|\s*\|)",
    re.M,
)

# 블록 태그가 이 수 이상이면 markdown 마커가 있어도 html 로 본다.
_HTML_TAG_STRONG = 3

VALID_FORMATS = ("html", "markdown", "auto")
VALID_MISSING_POLICIES = ("skip", "error")


class JsonTextSpec:
    """custom_fields 항목의 `json:` 블록.

    ```yaml
    json:
      text_fields: [html, summary_md]   # 키 이름만 — 임의 깊이에서 매칭
      format: auto                      # auto(기본) | html | markdown
      missing_policy: skip              # skip(기본) | error
    ```
    """

    def __init__(self, cfg: dict, doc_types: tuple[str, ...] = ()):
        raw_fields = cfg.get("text_fields") or cfg.get("text_keys") or []
        if isinstance(raw_fields, str):
            raw_fields = [raw_fields]
        self.text_fields: list[str] = [str(k).strip() for k in raw_fields if str(k).strip()]
        if not self.text_fields:
            raise ValueError("json.text_fields 가 비어 있습니다(텍스트가 담긴 key 이름 목록).")

        fmt = str(cfg.get("format") or "auto").strip().lower()
        if fmt not in VALID_FORMATS:
            _log.warning(f"[json_text] Invalid json.format '{fmt}', fallback to 'auto'")
            fmt = "auto"
        self.format: str = fmt

        policy = str(cfg.get("missing_policy") or "skip").strip().lower()
        if policy not in VALID_MISSING_POLICIES:
            _log.warning(
                f"[json_text] Invalid json.missing_policy '{policy}', fallback to 'skip'"
            )
            policy = "skip"
        self.missing_policy: str = policy
        self.doc_types = doc_types

    def __repr__(self) -> str:  # pragma: no cover - 디버깅 편의용
        return (
            f"JsonTextSpec(text_fields={self.text_fields}, format={self.format!r}, "
            f"missing_policy={self.missing_policy!r}, doc_types={self.doc_types})"
        )


def _label_from_siblings(container: dict, key: str, seq: int) -> str:
    """같은 dict 안의 name/title 류 형제 키로 섹션 라벨을 만든다."""
    for cand in _LABEL_KEYS:
        value = container.get(cand)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return f"{key}#{seq}"


def collect_text_fields(payload: Any, keys: list[str]) -> list[tuple[str, str]]:
    """payload 를 재귀 순회해 이름이 `keys` 에 있는 키의 문자열 값을 수집한다.

    dict/list 임의 깊이를 지원하고 문서 순서를 유지한다. 문자열이 아닌 값(dict/list/
    숫자)은 본문 텍스트가 아니므로 건너뛰고 그 아래를 계속 탐색한다.

    반환: [(라벨, 값)] — 라벨은 형제 name/title, 없으면 "<key>#<순번>".
    """
    wanted = set(keys)
    found: list[tuple[str, str]] = []
    counter: dict[str, int] = {}

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in wanted and isinstance(value, str) and value.strip():
                    counter[key] = counter.get(key, 0) + 1
                    found.append((_label_from_siblings(node, key, counter[key]), value))
                    continue  # 값은 본문 텍스트 — 안을 더 파고들지 않는다
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    return found


def detect_format(value: str) -> str:
    """'html' | 'markdown'. 판단이 애매하면 html 쪽으로 기운다(모듈 docstring 참고)."""
    tag_hits = len(_BLOCK_TAG_RE.findall(value))
    if tag_hits >= _HTML_TAG_STRONG:
        return "html"
    if _MD_MARKER_RE.search(value):
        return "markdown"
    if tag_hits:
        return "html"
    return "markdown"


def _markdown_to_html(value: str) -> str:
    """markdown → html. 표/코드펜스를 살리려면 확장이 필요하다."""
    try:
        import markdown  # genon/preprocessor/pyproject.toml 에 선언됨
    except ImportError:  # pragma: no cover - 의존성 누락 환경 방어
        _log.warning("[json_text] markdown 패키지 없음 — 평문 <pre> 로 대체합니다.")
        soup = BeautifulSoup("<pre></pre>", "html.parser")
        soup.pre.string = value
        return str(soup)
    return markdown.markdown(
        value, extensions=["tables", "fenced_code", "sane_lists", "nl2br"]
    )


def _section_node(value: str, fmt: str, label: str):
    """항목 하나를 정리된 콘텐츠 노드로 만든다."""
    if fmt == "markdown":
        return extract_content(_markdown_to_html(value))

    # html: 항목 안에 srcdoc/이중인코딩이 있으면 먼저 펼친다.
    reasons = precheck_html(value)
    if reasons:
        _log.info(f"[json_text] '{label}' flatten 적용 — 사유: {', '.join(reasons)}")
        value = flatten_html(value, label, reasons)
    return extract_content(value)


def build_merged_html(
    items: list[tuple[str, str]], title: str, forced_format: str = "auto"
) -> str:
    """(라벨, 값) 목록을 heading 기반 단일 HTML 문서로 병합한다.

    항목별 `<h2>` 섹션이 되어 docling 이 1회 파싱으로 전체를 읽는다.
    """
    sections = []
    for label, value in items:
        fmt = forced_format if forced_format in ("html", "markdown") else detect_format(value)
        _log.debug(f"[json_text] section '{label}' format={fmt} len={len(value):,}")
        sections.append((label, _section_node(value, fmt, label)))
    return build_docling_document(title, sections)


def json_payload_to_html(payload: Any, spec: JsonTextSpec, title: str) -> str:
    """JSON payload → docling 입력 HTML. 항목이 하나도 없으면 정책에 따라 처리."""
    items = collect_text_fields(payload, spec.text_fields)
    if not items:
        msg = (
            f"json.text_fields {spec.text_fields} 에 해당하는 텍스트를 JSON 에서 "
            f"찾지 못했습니다."
        )
        if spec.missing_policy == "error":
            raise ValueError(msg)
        _log.warning(f"[json_text] {msg} — 빈 문서로 진행합니다.")
    else:
        _log.info(
            f"[json_text] 텍스트 항목 {len(items)}개 수집 "
            f"(총 {sum(len(v) for _, v in items):,}자)"
        )
    return build_merged_html(items, title, spec.format)
