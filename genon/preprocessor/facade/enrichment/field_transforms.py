"""field_transforms.py — 추출 메타데이터를 typed 벡터 필드로 변환하는 재사용 로직.

intelligent_processor 의 created_date 전용 코드를 일반화한 모듈이다.
설정(field_transforms)에 따라 "어떤 출력 키 → 어떤 벡터 필드 → 어떤 변환" 을 매핑한다.

순수 함수 모음이라 docling/fastapi 등 무거운 의존성이 없다(enrichment_config.py 와 동일 성격).
docling 타입은 타입 힌트 용도로만 참조하므로 TYPE_CHECKING 으로 import 한다.

신규 변환기/보조추출은 함수 작성 후 VALUE_TRANSFORMS / FALLBACK_STRATEGIES 에 등록만 하면 된다.
"""
from __future__ import annotations

import html
import json
import logging
import re
import unicodedata
from datetime import datetime
from typing import TYPE_CHECKING, Any, Callable, Optional

if TYPE_CHECKING:
    from docling_core.types import DoclingDocument

_log = logging.getLogger(__name__)


# null 값을 KeyValueItem 으로 왕복하기 위한 sentinel. 빈 값 셀은 extract 에서 skip 되어
# key/value 페어링을 깨므로, None 을 비지 않는 이 토큰으로 저장하고 읽을 때 다시 None 으로 복원한다.
NULL_SENTINEL = "\x00__CF_NULL__\x00"
# 문자열 "9"와 정수 9를 구분하면서 parse→chunk API 경계를 왕복하기 위한 표식.
# 표식 없는 기존 KeyValueItem은 종전 normalize_metadata_value 규칙으로 계속 읽는다.
JSON_VALUE_SENTINEL = "\x00__CF_JSON__\x00"


# 추출 메타데이터 → typed 벡터 필드 변환의 기본값.
# yaml metadata.field_transforms 가 비어있을 때 적용되어 기존 created_date 동작을 보존한다.
# 각 항목: source(후보 키, str 또는 list) → target(벡터 필드) → type(값 변환기) / fallback(보조 추출).
DEFAULT_METADATA_FIELD_TRANSFORMS = [
    {
        "source": ["created_date", "작성일"],
        "target": "created_date",
        "type": "date_int",
        "fallback": "doc_text_scan",
    },
]


# ── 값 변환기 ───────────────────────────────────────────────────────────────

def parse_created_date(date_text: str) -> int:
    """작성일 텍스트를 파싱하여 YYYYMMDD 형식의 정수로 변환.

    Args:
        date_text: 작성일 텍스트 (YYYY-MM 또는 YYYY-MM-DD 형식)

    Returns:
        YYYYMMDD 형식의 정수, 파싱 실패시 0
    """
    if not date_text or not isinstance(date_text, str) or date_text == "None":
        return 0

    # 공백 제거 및 정리
    date_text = date_text.strip()

    # 1) YYYY-MM-DD / YYYY.MM.DD / YYYY/MM/DD / YYYY년 MM월 DD일 (문장 내부 포함) 우선
    for pattern in (
        r'(\d{4})\s*[-./]\s*(\d{1,2})\s*[-./]\s*(\d{1,2})',
        r'(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일?',
    ):
        match_full = re.search(pattern, date_text)
        if match_full:
            year, month, day = match_full.groups()
            try:
                datetime(int(year), int(month), int(day))
                return int(f"{year}{month.zfill(2)}{day.zfill(2)}")
            except ValueError:
                continue

    # 2) YYYY-MM / YYYY.MM / YYYY/MM / YYYY년 MM월 (문장 내부 포함) → 일자는 01
    for pattern in (
        r'(\d{4})\s*[-./]\s*(\d{1,2})',
        r'(\d{4})\s*년\s*(\d{1,2})\s*월',
    ):
        match_month = re.search(pattern, date_text)
        if match_month:
            year, month = match_month.groups()
            try:
                datetime(int(year), int(month), 1)
                return int(f"{year}{month.zfill(2)}01")
            except ValueError:
                continue

    # 3) YYYY 형식 → 월일은 0101
    match_year = re.search(r'(\d{4})', date_text)
    if match_year:
        year = match_year.group(1)
        try:
            datetime(int(year), 1, 1)
            return int(f"{year}0101")
        except ValueError:
            pass

    return 0


def transform_date_int(value: Any) -> int:
    """date_int 변환기: 날짜 텍스트/정수를 YYYYMMDD 정수로 변환."""
    if isinstance(value, (int, float)):
        candidate_int = int(value)
        return candidate_int if candidate_int > 0 else 0
    if value in (None, ""):
        return 0
    return parse_created_date(str(value))


# 2자리 연도 표기("26.07.01")를 4자리로 펼칠 때의 세기 분기점. 50 미만은 20xx, 이상은 19xx.
_SHORT_YEAR_PIVOT = 50
# 구분자 없는 압축 표기. 모니모 원천이 실제로 이 형태로 준다:
#   관심소식/이벤트 "260701"(YYMMDD) · 링크 "20260713"(YYYYMMDD)
# parse_created_date 는 `\d{4}` 를 연도로만 읽어 "260731"→2607년, "20260713"→2026-01-01 로
# 조용히 뭉갠다. 종료일이 그렇게 들어가면 기간 게이트가 영원히 열리므로 여기서 먼저 잡는다.
_COMPACT_DATE8_RE = re.compile(r"^\s*(\d{4})(\d{2})(\d{2})\s*$")
_COMPACT_DATE6_RE = re.compile(r"^\s*(\d{2})(\d{2})(\d{2})\s*$")
_SHORT_DATE_RE = re.compile(r"^\s*(\d{2})\s*[-./]\s*(\d{1,2})\s*[-./]\s*(\d{1,2})\s*$")


def transform_date_int_flex(value: Any) -> int:
    """date_int_flex 변환기: 2자리 연도("26.07.01")까지 받는 YYYYMMDD 정수 변환.

    `date_int`(parse_created_date)는 `\\d{4}` 연도만 인식해 "26.07.01" 을 2601 년으로도,
    날짜로도 읽지 못한다. 여기서 아래 세 형태를 먼저 정규화한 뒤 나머지는 `date_int` 에
    위임하므로 기존 created_date 동작에는 영향이 없다.
      - "26.07.01" / "26-07-01"  (2자리 연도 + 구분자)
      - "20260713"               (구분자 없는 8자리 YYYYMMDD)
      - "260701"                 (구분자 없는 6자리 YYMMDD)
    월/일이 실제 날짜가 아니면(예: "202699") 정규화하지 않고 기존 경로로 넘긴다.
    """
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        # 엑셀/JSON 이 숫자로 준 압축 날짜(20260713)도 문자열과 같게 다룬다.
        as_int = int(value)
        if 10_000_000 <= as_int <= 99_999_999 or 100_000 <= as_int <= 999_999:
            value = str(as_int)

    if isinstance(value, str):
        match = _SHORT_DATE_RE.match(value)
        if match:
            year, month, day = match.groups()
            century = 2000 if int(year) < _SHORT_YEAR_PIVOT else 1900
            value = f"{century + int(year)}-{month}-{day}"
        else:
            for pattern, two_digit_year in ((_COMPACT_DATE8_RE, False), (_COMPACT_DATE6_RE, True)):
                compact = pattern.match(value)
                if not compact:
                    continue
                year, month, day = compact.groups()
                if two_digit_year:
                    century = 2000 if int(year) < _SHORT_YEAR_PIVOT else 1900
                    year = str(century + int(year))
                try:
                    datetime(int(year), int(month), int(day))
                except ValueError:
                    break          # 날짜가 아니면 압축 표기로 보지 않는다
                value = f"{year}-{month}-{day}"
                break
    return transform_date_int(value)


def transform_text_norm(value: Any) -> Optional[str]:
    """text_norm 변환기: 표기 흔들림을 흡수한 대조용 정규화 문자열.

    모니모 TB_TERM.TERM_NORM("띄어쓰기와 대소문자를 고른 형태입니다. 같은 용어가 두 번
    등록되는 걸 막습니다") 용도. 유일키 `CLCM_C + TERM_NORM` 의 재료라 규칙이 곧 중복 판정
    기준이 된다.

    적용 규칙: NFKC 정규화 → 양끝 공백 제거 → 연속 공백 1칸으로 축약 → casefold.
    공백을 **제거하지 않고 축약만** 한다 — 완전 제거는 "선 지급"/"선지급" 같은 서로 다른
    용어까지 합쳐버릴 수 있어, 원천 담당과 규칙이 확정되기 전까지는 보수적으로 둔다.
    """
    if value in (None, ""):
        return None
    text = unicodedata.normalize("NFKC", str(value))
    text = text.replace("﻿", "").strip()
    text = text.replace("<BR>", " ").replace("<br>", " ").replace("<br/>", " ").replace("<BR/>", " ").strip()
    text = re.sub(r"\s+", " ", text)
    return text.casefold() or None


# ── 원천 필드 → 사람이 읽는 평문 (text_from 파생 필드용) ─────────────────────
# 원천이 같은 컬럼에 JSON·HTML·평문을 섞어 보낸다(모니모 AI차트뷰 detail_desc). 정해진
# 스키마가 없으므로 **종류를 자동 판별해 하나의 마크다운으로 수렴**시킨다. 설정은 없다.
#
# VALUE_TRANSFORMS 에 등록하지 않는 이유: 등록 변환기는 값을 제자리에서 덮어쓰는데,
# 여기서는 원본(TB 는 "원천이 보낸 세부 내용을 그대로 보관")을 남긴 채 평문 사본만 따로
# 만들어야 한다. 그 배선은 tabular/json 매퍼의 text_from 이 맡는다.

_BR_RE = re.compile(r"<\s*br\s*/?\s*>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
# 인라인 태그만 있으면 경량 경로로 충분하다. 표·목록·문단 같은 **구조**가 섞여 있으면
# docling 백엔드(json_records.html_to_text)에 태워야 행/열 대응이 살아남는다.
_STRUCTURAL_HTML_RE = re.compile(
    r"<\s*(table|thead|tbody|tr|td|th|ul|ol|li|dl|h[1-6]|p|div|section|article)\b",
    re.IGNORECASE,
)
_ANY_TAG_RE = re.compile(r"<\s*[a-zA-Z][a-zA-Z0-9]*\b[^>]*>")

# 값이 이보다 짧은 스칼라면 헤딩을 만들지 않고 한 줄 불릿으로 붙인다.
# 안 그러면 평평한 20필드 JSON 이 헤딩 20개가 되어 본문보다 제목이 많아진다.
_SHORT_SCALAR = 60
# 헤딩은 두 단계까지만(## / ###). 그보다 깊으면 `- 경로: 값` 불릿으로 접는다 —
# 깊은 트리에서 제목이 계단처럼 쌓여 본문을 밀어내는 것을 막는다.
_HEADING_MAX_DEPTH = 2


def strip_inline_html(value: Any) -> str:
    """인라인 HTML 조각 → 평문. `<BR>` 은 개행으로, 나머지 태그는 제거한다."""
    if value in (None, ""):
        return ""
    text = _BR_RE.sub("\n", str(value))
    text = _TAG_RE.sub("", text)
    text = html.unescape(text)
    text = unicodedata.normalize("NFKC", text).replace("﻿", "")
    # 줄 안의 연속 공백만 접는다. 개행은 <BR> 이 만든 문단 구분이라 살린다.
    text = "\n".join(re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines())
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def detect_payload_kind(value: Any) -> str:
    """값의 종류를 판별한다: json / html / html_inline / text / broken_json / empty.

    `broken_json` 은 `{`·`[` 로 시작하는데 파싱이 안 되는 경우다. 원천이 한 JSON 을
    문자 단위로 잘라 여러 행에 나눠 보내는 스키마가 있어(row_merge), 이 상태는 조용히
    넘기면 안 되는 신호다. 브레이스로 시작하지 않는 평문은 그냥 text 다.
    """
    if value in (None, ""):
        return "empty"
    if isinstance(value, (dict, list)):
        return "json"
    text = str(value).strip()
    if not text:
        return "empty"
    if text[0] in "{[":
        try:
            json.loads(text)
        except (ValueError, TypeError):
            return "broken_json"
        return "json"
    if _STRUCTURAL_HTML_RE.search(text):
        return "html"
    if _ANY_TAG_RE.search(text) or _BR_RE.search(text):
        return "html_inline"
    return "text"


def humanize_key(key: Any) -> str:
    """JSON 키를 제목으로 쓸 수 있게 다듬는다 — 사전 없이, 스키마와 무관하게.

    `_`/`-` 를 공백으로, camelCase 를 단어로 나눈다. **대문자화는 하지 않는다** —
    `RSI`·`MACD` 같은 약어가 망가진다. 한글 키는 그대로 나온다.
    """
    text = re.sub(r"[_\-]+", " ", str(key).strip())
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _leaf_text(value: Any, html_renderer: Optional[Callable[[str], str]]) -> str:
    """리프 값 하나 → 평문. 리프 안에 HTML 이 섞여 있으면 같은 판별을 다시 건다."""
    if value is None or value == "":
        return ""
    if isinstance(value, bool):
        return "예" if value else "아니오"
    if not isinstance(value, str):
        return str(value)
    if detect_payload_kind(value) == "html" and html_renderer is not None:
        return str(html_renderer(value) or "").strip()
    return strip_inline_html(value)


def _is_short(text: str) -> bool:
    return len(text) <= _SHORT_SCALAR and "\n" not in text


def _render_node(node: Any, key: Any, depth: int, path: str, lines: list[str],
                 html_renderer: Optional[Callable[[str], str]]) -> None:
    """`node` 하나를 마크다운 줄로 펼친다. `key=None` 이면 루트(제목 없음)."""
    label = humanize_key(key) if key is not None else ""
    full_path = f"{path}.{label}" if path and label else (label or path)
    heading_ok = label and depth < _HEADING_MAX_DEPTH

    def heading() -> None:
        if heading_ok:
            lines.append(f"{'#' * (depth + 2)} {label}")

    # 헤딩을 냈으면 그 제목이 문맥을 담으므로 하위 불릿 경로를 거기서 다시 시작한다
    # (`- deep.a.b.c:` 대신 `### a` 아래 `- b.c:`).
    child_path = "" if heading_ok else full_path

    if isinstance(node, dict):
        heading()
        # 루트는 깊이를 소비하지 않는다 — 최상위 키가 `##` 이 되도록.
        child_depth = depth if key is None else depth + 1
        if key is None:
            # 한 줄 불릿으로 끝나는 항목을 앞으로 끌어올린다. 그러지 않으면 앞 섹션 뒤에
            # 붙어 그 섹션의 하위 항목처럼 보인다(`## candle analysis` 다음의 `- nat c:`).
            bullets: list[str] = []
            sections: list[str] = []
            for child_key, child in node.items():
                rendered: list[str] = []
                _render_node(child, child_key, child_depth, child_path, rendered, html_renderer)
                (sections if any(l.startswith("#") for l in rendered) else bullets).extend(rendered)
            lines.extend(bullets)
            lines.extend(sections)
            return
        for child_key, child in node.items():
            _render_node(child, child_key, child_depth, child_path, lines, html_renderer)
        return

    if isinstance(node, (list, tuple)):
        items = [item for item in node if item not in (None, "")]
        if not items:
            return
        if all(not isinstance(item, (dict, list, tuple)) for item in items):
            rendered = [text for text in (_leaf_text(i, html_renderer) for i in items) if text]
            if not rendered:
                return
            inline = ", ".join(rendered)
            if label and _is_short(inline):
                lines.append(f"- {label}: {inline}")
                return
            heading()
            lines.extend(f"- {text}" for text in rendered)
            return
        for idx, item in enumerate(items, start=1):
            _render_node(item, f"{key} {idx}" if key is not None else str(idx),
                         depth, child_path, lines, html_renderer)
        return

    text = _leaf_text(node, html_renderer)
    if not text:
        return
    if not heading_ok:
        lines.append(f"- {full_path}: {text}" if full_path else text)
        return
    # 짧은 스칼라는 헤딩을 만들지 않고 한 줄로 — 제목이 본문보다 많아지지 않게.
    if _is_short(text):
        lines.append(f"- {label}: {text}")
        return
    heading()
    lines.append(text)


def json_to_markdown(parsed: Any, html_renderer: Optional[Callable[[str], str]] = None) -> str:
    """파싱된 JSON → 마크다운. 최상위 키가 `##`, 한 단계 아래가 `###` 이 된다.

    헤딩을 만드는 이유는 읽기 좋으라고가 아니라 **청킹 때문**이다. 청커가 `## ` 를 우선
    분리자로 써서 섹션 경계에서 자르고, 분할된 뒷 조각에 직전 섹션 제목을 다시 붙인다
    (`_expand_splittable_rows`). 헤딩이 없으면 "8월 17일 2544만" 같은 조각이 무엇에
    대한 값인지 알 수 없는 상태로 검색에 노출된다.
    """
    lines: list[str] = []
    _render_node(parsed, None, 0, "", lines, html_renderer)
    out: list[str] = []
    for line in lines:
        # 헤딩 앞에는 빈 줄을 둔다(청커의 문단 분리자와 마크다운 렌더링 양쪽에 필요).
        if line.startswith("#") and out and out[-1] != "":
            out.append("")
        out.append(line)
    return "\n".join(out).strip()


def render_field_text(
    value: Any,
    *,
    kind: Optional[str] = None,
    html_renderer: Optional[Callable[[str], str]] = None,
) -> Optional[str]:
    """원천 값 하나 → 청크 본문에 실을 평문. 종류는 자동 판별한다.

    `kind` 로 "json"/"html"/"text" 를 주면 판별을 건너뛰고 그 경로로 강제한다
    (출고 설정의 html_text_fields 별칭이 `kind="html"` 로 쓴다).
    `html_renderer` 는 구조 HTML 을 처리할 함수다(json_records.html_to_text). 주지 않으면
    경량 태그 제거로 폴백한다 — 표가 한 줄씩 뭉개지므로 표가 오는 경로에서는 반드시 넘긴다.
    """
    if kind == "html":
        # 원본을 그대로 넘긴다 — html_to_text 는 리스트(collect_key_map 결과)까지 처리하므로
        # 여기서 문자열로 눌러 버리면 기존 html_text_fields 동작이 깨진다.
        if html_renderer is not None:
            return str(html_renderer(value) or "").strip() or None
        return strip_inline_html(value) or None

    detected = detect_payload_kind(value)
    if detected == "empty":
        return None

    if kind == "text":
        return strip_inline_html(value) or None
    if kind == "json" and detected == "text":
        # 강제했는데 JSON 이 아니면 파싱 실패로 취급해 신호를 남긴다.
        detected = "broken_json"

    if detected == "broken_json":
        raw = str(value).strip()
        _log.warning(
            f"[text_from] JSON 파싱 실패 — 원문을 평문화만 합니다"
            f"(len={len(raw)}, head={raw[:80]!r}). 여러 행으로 쪼개진 값이라면 "
            f"row_merge 의 group_by/order_by 를 확인하세요."
        )
        return strip_inline_html(raw) or None

    if detected == "json":
        parsed = value if isinstance(value, (dict, list)) else json.loads(str(value).strip())
        if not isinstance(parsed, (dict, list)):
            return _leaf_text(parsed, html_renderer) or None
        return json_to_markdown(parsed, html_renderer) or None

    if detected == "html" and html_renderer is not None:
        return str(html_renderer(value) or "").strip() or None

    return strip_inline_html(value) or None


# ── 보조 추출(fallback) ──────────────────────────────────────────────────────

def extract_created_date_from_document_text(document: "DoclingDocument") -> int:
    """metadata 추출이 비어있을 때 문서 본문에서 작성/기준일을 휴리스틱으로 추출."""
    try:
        raw_text = document.export_to_text() or ""
    except Exception:
        raw_text = ""
    if not raw_text:
        return 0

    prioritized_patterns = (
        r'(?:기준일|작성일|최초\s*작성일|보고자료)[^0-9]{0,20}(\d{4}\s*[./-]\s*\d{1,2}\s*[./-]\s*\d{1,2})',
        r'(?:기준일|작성일|최초\s*작성일|보고자료)[^0-9]{0,20}(\d{4}\s*년\s*\d{1,2}\s*월\s*\d{1,2}\s*일?)',
        r'(?:기준일|작성일|최초\s*작성일|보고자료)[^0-9]{0,20}(\d{4}\s*[./-]\s*\d{1,2})',
        r'(?:기준일|작성일|최초\s*작성일|보고자료)[^0-9]{0,20}(\d{4}\s*년\s*\d{1,2}\s*월)',
    )
    for pattern in prioritized_patterns:
        m = re.search(pattern, raw_text)
        if not m:
            continue
        parsed = parse_created_date(m.group(1))
        if parsed:
            return parsed

    # 키워드 기반으로 찾지 못하면 문서 최초 날짜를 fallback 으로 사용.
    fallback_match = re.search(
        r'(\d{4}\s*[./-]\s*\d{1,2}\s*[./-]\s*\d{1,2}|\d{4}\s*년\s*\d{1,2}\s*월\s*\d{1,2}\s*일?)',
        raw_text,
    )
    if fallback_match:
        parsed = parse_created_date(fallback_match.group(1))
        if parsed:
            return parsed
    return 0


# ── 레지스트리 ───────────────────────────────────────────────────────────────
# 신규 변환기/보조추출은 함수 작성 후 아래 dict 에 등록만 하면 설정에서 바로 사용 가능.
VALUE_TRANSFORMS: dict[str, Callable[[Any], Any]] = {
    "date_int": transform_date_int,
    "date_int_flex": transform_date_int_flex,
    "text_norm": transform_text_norm,
}
FALLBACK_STRATEGIES: dict[str, Callable[["DoclingDocument"], Any]] = {
    "doc_text_scan": extract_created_date_from_document_text,
}


# ── 적용 ─────────────────────────────────────────────────────────────────────

def apply_field_transforms(
    field_transforms: list,
    merged_metadata: dict[str, Any],
    document: "DoclingDocument",
) -> tuple[dict[str, Any], set[str]]:
    """field_transforms 설정에 따라 추출 메타데이터를 typed 벡터 필드로 변환한다.

    Args:
        field_transforms: 변환 spec 의 list (각 spec 은 source/target/type/fallback dict).
        merged_metadata: 문서/컨텍스트 병합 메타데이터.
        document: 본문 휴리스틱(fallback)에 사용할 docling 문서.

    Returns:
        (typed_values, consumed_keys)
        - typed_values: {target_field: 변환값} — global_metadata 로 주입
        - consumed_keys: passthrough 에서 제외할 source/target 키 집합
    """
    typed_values: dict[str, Any] = {}
    consumed_keys: set[str] = set()

    for spec in field_transforms or []:
        if not isinstance(spec, dict):
            continue
        sources = spec.get("source")
        if isinstance(sources, str):
            sources = [sources]
        elif not isinstance(sources, list):
            sources = []
        target = spec.get("target") or (sources[0] if sources else None)
        if not target:
            continue
        consumed_keys.update(sources)
        consumed_keys.add(target)

        # 후보 키를 순서대로 탐색해 첫 non-empty 값 선택
        raw_value = None
        for key in sources:
            candidate = merged_metadata.get(key)
            if candidate not in (None, ""):
                raw_value = candidate
                break

        transform_name = spec.get("type")
        transform_fn = VALUE_TRANSFORMS.get(transform_name) if transform_name else None
        value = transform_fn(raw_value) if transform_fn is not None else raw_value

        # 값이 비어있고 fallback 이 지정된 경우 문서 본문 휴리스틱 적용
        if (value in (None, "") or value == 0):
            fallback_name = spec.get("fallback")
            fallback_fn = FALLBACK_STRATEGIES.get(fallback_name) if fallback_name else None
            if fallback_fn is not None:
                value = fallback_fn(document)

        typed_values[target] = value

    return typed_values, consumed_keys


# ── 문서 메타데이터 추출/직렬화 유틸 ─────────────────────────────────────────────
# compose_vectors 에서 docling 문서의 KeyValueItem 메타데이터를 읽고 벡터 출력용으로
# 직렬화하는 공통 로직. intelligent_processor / convert_processor 가 공유한다.

def normalize_metadata_value(value: Any) -> Any:
    """문자열 값이 JSON 배열/객체 형태면 파싱, 아니면 strip 한 문자열을 반환."""
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if not stripped:
        return ""
    if stripped[0] in "[{" and stripped[-1] in "]}":
        try:
            return json.loads(stripped)
        except Exception:
            return stripped
    return stripped


def extract_metadata_from_document(document: "DoclingDocument") -> dict[str, Any]:
    """docling 문서의 KeyValueItem 들에서 key→value 메타데이터를 일반적으로 추출."""
    metadata: dict[str, Any] = {}
    for kv_item in getattr(document, "key_value_items", []) or []:
        graph = getattr(kv_item, "graph", None)
        cells = getattr(graph, "cells", None) or []
        pending_key: Optional[str] = None
        for cell in cells:
            text = str(getattr(cell, "text", "") or "").strip()
            if not text:
                continue
            label_obj = getattr(cell, "label", None)
            label = str(getattr(label_obj, "value", label_obj) or "").strip().lower()
            if label == "key":
                pending_key = text
                continue
            if label == "value" and pending_key:
                metadata[pending_key] = (
                    None if text == NULL_SENTINEL
                    else _deserialize_stored_metadata_value(text)
                )
                pending_key = None
                continue
            # fallback: label 정보가 없거나 예외적인 순서인 경우 순차 pair 처리
            if pending_key is None:
                pending_key = text
            else:
                metadata[pending_key] = (
                    None if text == NULL_SENTINEL
                    else _deserialize_stored_metadata_value(text)
                )
                pending_key = None
    return metadata


def _deserialize_stored_metadata_value(text: str) -> Any:
    """새 typed 표식과 기존 무표식 metadata를 모두 읽는다."""
    if text.startswith(JSON_VALUE_SENTINEL):
        candidate = text[len(JSON_VALUE_SENTINEL):]
        try:
            return json.loads(candidate)
        except Exception:
            _log.warning("typed metadata JSON 복원 실패 — 문자열로 유지")
            return candidate
    return normalize_metadata_value(text)


def serialize_metadata_value_for_output(value: Any) -> Any:
    """벡터 결과 포맷 일관성을 위해 중첩 metadata는 JSON 문자열로 직렬화."""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return value


def store_metadata_in_document(
    document: "DoclingDocument",
    metadata: dict,
    preserve_nulls: bool = False,
    typed_keys: "set[str] | None" = None,
) -> None:
    """추출한 key→value 메타데이터를 문서의 KeyValueItem 으로 저장한다.

    extract_metadata_from_document 의 쓰기측 대칭 함수. 문서에 실리면 청커의
    passthrough(chunking_processor.compose_vectors)가 각 청크에 자동 부착한다
    — created_date(MetadataEnricher 가 이미 add_key_values 로 저장)와 동일 경로.
    parse↔chunk 가 별도 API 여도 DoclingDocument 에 실려 경계를 넘는다.

    None 값 필드 처리:
    - preserve_nulls=False(기본): 저장하지 않는다. 값 셀 text 가 비면 extract 측이 셀을
      건너뛰어(extract_metadata_from_document 의 빈 텍스트 skip) key/value 페어링이 깨지므로,
      null 은 애초에 emit 하지 않는다.
    - preserve_nulls=True(custom_fields): 선언된 output_fields 를 값이 null 이어도 결과에
      모두 남기기 위해, None 을 비지 않는 NULL_SENTINEL 로 저장한다(읽을 때 다시 None 으로 복원).

    typed_keys: 값의 **타입까지** chunk API 경계 너머로 보존할 키 집합(예: md front matter 의
        `source_pages: 9`). 여기 든 키만 JSON_VALUE_SENTINEL 로 직렬화되어 읽을 때 int/bool/
        list 원형으로 복원된다.
        **의도적으로 opt-in 이다** — 전 필드에 적용하면 문서 단위 custom_fields 를 쓰는 모든
        doc_type 의 청크 property 타입이 함께 바뀐다(실측: card 의 annual_fee_amount 가
        '18000' → 18000). 이미 text 로 생성된 벡터 컬렉션 property 에 int 가 들어가면 적재가
        깨지므로, 기존 doc_type 은 종전 직렬화 규칙(dict/list → json.dumps, 그 외 → str)을
        그대로 유지한다.
    """
    typed_keys = typed_keys or set()
    try:
        from docling_core.types.doc.document import GraphData, GraphCell, GraphCellLabel
    except ImportError:
        _log.warning("GraphData/GraphCell import 실패 — 문서 메타데이터 저장 생략")
        return

    graph_cells = []
    cell_id = 0
    for key, value in (metadata or {}).items():
        if value is None:
            if not preserve_nulls:
                continue  # null 필드는 저장하지 않음(빈 value 셀 skip → 페어링 붕괴 방지)
            value_str = NULL_SENTINEL  # 페어링을 깨지 않는 sentinel 로 왕복
        elif isinstance(value, str):
            value_str = value
        elif key in typed_keys:
            # 숫자/bool/배열/객체의 타입을 별도 chunk API 까지 보존한다. JSON 직렬화가
            # 불가능한 사용자 객체는 기존 동작처럼 문자열로 안전하게 내린다.
            try:
                value_str = JSON_VALUE_SENTINEL + json.dumps(value, ensure_ascii=False)
            except (TypeError, ValueError):
                value_str = str(value)
        else:
            # 기존 규칙(하위 호환): dict/list 는 표식 없는 JSON, 그 외는 문자열.
            value_str = (
                json.dumps(value, ensure_ascii=False)
                if isinstance(value, (dict, list))
                else str(value)
            )
        graph_cells.append(GraphCell(label=GraphCellLabel.KEY, cell_id=cell_id, text=str(key), orig=str(key)))
        cell_id += 1
        graph_cells.append(GraphCell(label=GraphCellLabel.VALUE, cell_id=cell_id, text=value_str, orig=value_str))
        cell_id += 1

    if not graph_cells:
        return  # 저장할 값이 없으면 빈 KeyValueItem 을 만들지 않음

    graph_data = GraphData(cells=graph_cells, links=[])
    document.add_key_values(graph=graph_data, prov=None, parent=None)
