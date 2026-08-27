"""레이아웃 보존용 ```text 펜스를 마크다운 단락으로 복원한다.

## 왜 필요한가

모니모 상품요약서 md 는 PDF 레이아웃을 살리려고 본문 대부분을 ```text 펜스로 감싼다.
docling 의 Markdown 백엔드는 `FencedCode` 를 `doc.add_code()` **한 번**으로 처리하므로
(`docling/backend/md_backend.py`) 펜스 본문 전체가 CodeItem 하나가 된다. 실측 4654자 펜스가
아이템 1개였고, 그 결과 chunk_size 를 1000 으로 낮춰도 청크 경계가 아이템 경계와 맞지 않아
청크 품질이 무너진다(청커의 semchunk 내부 분할이 크기만 맞추고 문장 중간을 자른다).

## 왜 "펜스만 제거" 로는 안 되는가

실측한 함정 두 개다.

1. 펜스 본문은 4칸 이상 들여쓰여 있다. 펜스 마커만 지우면 marko 가 그것을
   `marko.block.CodeBlock`(indented code block)으로 파싱해 **같은 add_code() 분기**를 타고
   또 CodeItem 하나가 된다. 그래서 들여쓰기 제거가 함께 필요하다.
2. 본문에는 `|    |    |    |    상품의 특이사항` 같은 레이아웃 파이프 줄이 있다.
   md_backend 는 `"|" in snippet_text` 한 줄로 표 모드에 들어가고, `_close_table` 이
   불리기 전까지 뒤따르는 모든 줄을 표 버퍼로 삼킨다. 실측: 본문 1586자가 셀이 전부 빈
   11x3 표로 바뀌고 텍스트는 전량 소실됐다. 그래서 파이프 제거가 함께 필요하다.

## 무엇을 하지 않는가

하드랩 경계의 공백을 추측해 지우지 않는다. 원문에는 단어 중간 랩("…알릴의무사항 항" /
"목(이하…")과 어절 경계 랩("…유병력자" / "또는 고연령자…")이 섞여 있어 공백을 지워야
하는지 안전하게 판정할 수 없다. 다만 Markdown backend 구현이나 HTML 우회 여부와
무관하게 논리 단위 하나를 item 하나로 만들기 위해 물리 줄들을 공백 하나로 접는다.
그 결과 "항 목" 처럼 공백 하나가 끼는 기존 동작은 남는다.

## 설정 위치

`custom_fields` 항목 하위 `markdown.text_fence` 블록이다. 형제 모듈
`markdown_front_matter` 와 같은 자리이며 doc_type 별로 켠다(진짜 코드 펜스를 쓰는 문서를
건드리지 않기 위해 전역 적용하지 않는다).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

_log = logging.getLogger(__name__)

# 변환 대상 정보문자열(펜스 뒤 언어 표기). "" = 언어 표기 없는 펜스.
DEFAULT_LANGS: tuple[str, ...] = ("", "text")

# 본문이 산문인지 판정하는 한글 비율 하한. 글자(한글+영문)만 분모로 세므로
# 숫자·기호가 많은 금액 표도 한글 헤더만 있으면 통과한다.
DEFAULT_MIN_HANGUL_RATIO = 0.3

# 펜스 여닫이 줄. 마크다운 규격대로 들여쓰기 3칸까지 허용하고 ``` 와 ~~~ 를 모두 본다.
_FENCE_RE = re.compile(r"^[ \t]{0,3}(?P<ticks>`{3,}|~{3,})[ \t]*(?P<lang>[^`]*?)[ \t]*$")

# 논리 단위(= 새 단락)의 시작. Q/A 문답, 불릿, ※ 주석, 번호 목록.
_UNIT_START_RE = re.compile(r"(?:[QA][.)]?(?=\s)|[-*•·▪◦○□■](?=\s)|※|\d+[.)](?=\s))")

# 불릿은 PDF 레이아웃을 md 로 옮기는 과정에서 물리 줄마다 반복되기도
# 한다. 다른 구조 마커(Q/A, ※, 번호)와 달리 불릿만 문장 완결성을 보조
# 신호로 삼아 새 단위인지 하드랩 연속 행인지 판별한다.
_BULLET_START_RE = re.compile(r"(?P<marker>[-*•·▪◦○□■])\s+(?P<text>.*)")
_BULLET_UNIT_RE = re.compile(r"(?:[A][.)]?\s+)?[-*•·▪◦○□■]\s+")

# 닫는 따옴표/괄호 뒤의 종결부호도 문장 끝으로 본다. 콜론은 뒤의
# 불릿이 설명 목록으로 시작하는 구조가 많아 구조적 종결에 포함한다.
_TERMINAL_RE = re.compile(
    r"[.!?\u3002\uff01\uff1f:\uff1a](?:[\"'\u201d\u2019)\]}>》」』]*)$"
)

# 구분선/장식만 남은 줄. 안 지우면 `---` 가 thematic break 나 setext 헤딩이 된다.
_RULE_RE = re.compile(r"[\s\-=_~·ㆍ*]+")

_WS_RUN_RE = re.compile(r"[ \t 　]+")

_HANGUL_RE = re.compile(r"[가-힣ㄱ-ㅎㅏ-ㅣ]")
_LATIN_RE = re.compile(r"[A-Za-z]")

# 줄머리에서 마크다운 블록 구조로 오인되는 문자. 백슬래시로 이스케이프한다.
_ESCAPE_PREFIX = ("#", ">")


@dataclass(frozen=True)
class MarkdownTextFenceSpec:
    """`markdown.text_fence` 블록 하나를 컴파일한 선언.

    ```yaml
    markdown:
      text_fence:
        enable: true
        langs: ["", "text"]      # 변환 대상 정보문자열(진짜 코드 펜스는 손대지 않는다)
        min_hangul_ratio: 0.3    # 본문 한글 비율 하한 — 코드 블록 오변환 방지
    ```
    """

    doc_types: tuple[str, ...] = ()
    langs: tuple[str, ...] = DEFAULT_LANGS
    min_hangul_ratio: float = DEFAULT_MIN_HANGUL_RATIO

    @classmethod
    def from_config(cls, cfg: Any, doc_types: tuple[str, ...] = ()) -> MarkdownTextFenceSpec:
        """`text_fence` 블록 값을 스펙으로 만든다. `true` 는 기본값 사용을 뜻한다."""
        if cfg is True:
            cfg = {}
        if not isinstance(cfg, dict):
            raise ValueError("custom_fields.markdown.text_fence는 object 또는 true여야 합니다.")

        raw_langs = cfg.get("langs")
        if raw_langs is None:
            langs = DEFAULT_LANGS
        else:
            if isinstance(raw_langs, str):
                raw_langs = [raw_langs]
            if not isinstance(raw_langs, (list, tuple)):
                raise ValueError("text_fence.langs는 문자열 목록이어야 합니다.")
            # 빈 문자열은 "언어 표기 없는 펜스" 를 뜻하므로 지우지 않는다.
            langs = tuple(str(v).strip().lower() for v in raw_langs)
            if not langs:
                raise ValueError("text_fence.langs가 비어 있습니다.")

        raw_ratio = cfg.get("min_hangul_ratio")
        try:
            ratio = DEFAULT_MIN_HANGUL_RATIO if raw_ratio is None else float(raw_ratio)
        except (TypeError, ValueError) as exc:
            raise ValueError("text_fence.min_hangul_ratio는 숫자여야 합니다.") from exc
        if not 0.0 <= ratio <= 1.0:
            raise ValueError("text_fence.min_hangul_ratio는 0 이상 1 이하여야 합니다.")

        return cls(doc_types=doc_types, langs=langs, min_hangul_ratio=ratio)

    def apply(self, text: str) -> tuple[str, int]:
        return transform(text, langs=self.langs, min_hangul_ratio=self.min_hangul_ratio)


def _looks_like_prose(body: str, min_hangul_ratio: float) -> bool:
    """본문이 산문인지(= 변환 대상인지) 판정한다.

    분모를 글자(한글+영문)로만 잡는다. 문자 전체로 세면 금액·비율이 많은 안내문이
    숫자 때문에 탈락하고, 반대로 파이썬 코드는 영문 식별자가 대부분이라 확실히 걸러진다.
    글자가 아예 없는 블록(숫자·기호뿐)은 코드일 가능성이 없으므로 변환한다.
    """
    hangul = len(_HANGUL_RE.findall(body))
    latin = len(_LATIN_RE.findall(body))
    if hangul + latin == 0:
        return True
    return hangul / (hangul + latin) >= min_hangul_ratio


def _clean_line(raw: str) -> str:
    """레이아웃 파이프·들여쓰기·공백 런을 걷어낸 한 줄."""
    line = raw.replace("|", " ")
    return _WS_RUN_RE.sub(" ", line).strip()


def _escape_md_prefix(line: str) -> str:
    return "\\" + line if line.startswith(_ESCAPE_PREFIX) else line


def _leading_indent(raw: str) -> int:
    """탭을 4칸으로 펼 뒤의 시각적 들여쓰기 깊이."""
    expanded = raw.expandtabs(4)
    return len(expanded) - len(expanded.lstrip(" "))


def _estimate_wrap_width(body_lines: list[str]) -> int:
    """펜스 본문의 대략적인 하드랩 폭(길이 75 백분위)."""
    widths = sorted(
        len(cleaned)
        for raw in body_lines
        if (cleaned := _clean_line(raw)) and not _RULE_RE.fullmatch(cleaned)
    )
    if not widths:
        return 0
    return widths[min(len(widths) - 1, (len(widths) * 3) // 4)]


def _has_unclosed_delimiter(text: str) -> bool:
    """괄호가 닫히지 않은 행은 다음 행과 연결된 것으로 본다."""
    pairs = (("(", ")"), ("[", "]"), ("{", "}"), ("「", "」"), ("『", "』"))
    return any(
        text.count(opening) > text.count(closing) for opening, closing in pairs
    )


def _is_wrapped_bullet_continuation(
    *,
    raw: str,
    previous_raw: str,
    previous_text: str,
    current_unit_first_text: str,
    current_unit_text: str,
    wrap_width: int,
) -> bool:
    """반복 불릿이 새 목록이 아니라 이전 물리 줄의 연속인지 판정."""
    # 긴 제목/질문 뒤에 처음 나오는 불릿은 조건없이 새 단위다.
    # 현재 단위가 이미 불릿(또는 `A - ...` 형태)로 시작했을 때만
    # 뒤이은 불릿을 하드랩 후보로 본다.
    if not _BULLET_UNIT_RE.match(current_unit_first_text):
        return False
    if _TERMINAL_RE.search(previous_text.rstrip()):
        return False
    if _has_unclosed_delimiter(current_unit_text):
        return True

    # 긴 행이 종결부호 없이 끝나고 불릿이 반복되면 고정폭 레이아웃의
    # 하드랩일 가능성이 높다. 더 깊은 들여쓰기는 연속 행 신호이므로
    # 필요 길이를 낮춘다. 짧은 무종결 불릿("가입 대상")은 병합하지 않는다.
    deeper_indent = _leading_indent(raw) > _leading_indent(previous_raw)
    ratio = 0.65 if deeper_indent else 0.80
    threshold = max(20, int(wrap_width * ratio))
    return len(previous_text) >= threshold


def _restore_paragraphs(body_lines: list[str]) -> str:
    """펜스 본문을 논리 단위별 단락으로 복원한다.

    단위 내부의 물리 줄은 공백 하나로 접고 단위 사이는 빈 줄로 끊는다.
    이렇게 해야 Markdown backend 의 줄바꿈 처리 차이와 무관하게 단위마다
    별개 TextItem/ListItem 하나가 된다.
    """
    units: list[list[tuple[str, str]]] = []
    cur: list[tuple[str, str]] = []
    wrap_width = _estimate_wrap_width(body_lines)
    for raw in body_lines:
        cleaned = _clean_line(raw)
        if not cleaned or _RULE_RE.fullmatch(cleaned):
            continue

        starts_unit = _UNIT_START_RE.match(cleaned) is not None
        bullet = _BULLET_START_RE.match(cleaned)
        wrapped_bullet = False
        if starts_unit and bullet and cur:
            wrapped_bullet = _is_wrapped_bullet_continuation(
                raw=raw,
                previous_raw=cur[-1][0],
                previous_text=cur[-1][1],
                current_unit_first_text=cur[0][1],
                current_unit_text=" ".join(line for _, line in cur),
                wrap_width=wrap_width,
            )

        if starts_unit and cur and not wrapped_bullet:
            units.append(cur)
            cur = []
        elif wrapped_bullet and bullet:
            # 레이아웃 변환기가 물리 줄마다 붙인 가짜 불릿은 본문에서 제거한다.
            cleaned = bullet.group("text")

        cur.append((raw, _escape_md_prefix(cleaned)))
    if cur:
        units.append(cur)
    return "\n\n".join(" ".join(line for _, line in unit) for unit in units)


def transform(
    text: str,
    *,
    langs: tuple[str, ...] = DEFAULT_LANGS,
    min_hangul_ratio: float = DEFAULT_MIN_HANGUL_RATIO,
) -> tuple[str, int]:
    """레이아웃 보존용 펜스 블록을 마크다운 단락으로 복원한다.

    Returns:
        (변환된 텍스트, 변환한 펜스 블록 수). 변환할 것이 없으면 원문을 그대로 돌려준다
        (줄바꿈 문자까지 손대지 않기 위해 조기 반환한다).
    """
    if not text or ("```" not in text and "~~~" not in text):
        return text, 0

    lines = text.splitlines()
    out: list[str] = []
    converted = 0
    i = 0
    while i < len(lines):
        opening = _FENCE_RE.match(lines[i])
        if not opening:
            out.append(lines[i])
            i += 1
            continue

        ticks = opening.group("ticks")
        lang = opening.group("lang").strip().lower()

        # 닫는 펜스 찾기 — 같은 문자로 시작하고 길이가 같거나 길며 정보문자열이 없는 줄.
        body: list[str] = []
        close_idx = None
        j = i + 1
        while j < len(lines):
            closing = _FENCE_RE.match(lines[j])
            if (
                closing
                and closing.group("ticks")[0] == ticks[0]
                and len(closing.group("ticks")) >= len(ticks)
                and not closing.group("lang")
            ):
                close_idx = j
                break
            body.append(lines[j])
            j += 1

        # 대상이 아니면(미닫힘/다른 언어/코드로 보임) 블록 전체를 원문 그대로 통과시킨다.
        if (
            close_idx is None
            or lang not in langs
            or not _looks_like_prose("\n".join(body), min_hangul_ratio)
        ):
            end = len(lines) - 1 if close_idx is None else close_idx
            out.extend(lines[i:end + 1])
            i = end + 1
            continue

        restored = _restore_paragraphs(body)
        if restored:
            # 앞뒤를 빈 줄로 감싸야 직전/직후 단락과 붙어 한 단락이 되지 않는다.
            # 이미 빈 줄이면 더 넣지 않는다(빈 줄 3개는 마크다운 의미가 같고 진단만 어렵다).
            if out and out[-1].strip():
                out.append("")
            out.extend([restored, ""])
            converted += 1
        i = close_idx + 1

    if not converted:
        return text, 0

    trailing = "\n" if text.endswith("\n") else ""
    return "\n".join(out) + trailing, converted
