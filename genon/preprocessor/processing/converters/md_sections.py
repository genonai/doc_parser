"""Markdown 헤딩을 기준으로 청크 본문에 넣을 섹션을 고른다.

## 왜 필요한가

모니모 상품요약서 md 는 변환 과정에서 원문 앞에 `## 문서 개요`·`## 핵심 키워드`·
`## 내용 색인` 같은 안내 섹션이 붙는다. 이 섹션은 원문에 없는 생성 문장이고 일부는
잘린 문장이라 검색 노이즈가 된다. 원문은 `## 원문 전사` 표식 뒤에 오지만 표식이 없는
문서도 있다. 그래서 두 가지를 함께 쓴다.

  - `after_heading` : 일치하는 첫 헤딩 앞의 내용을 지운다. 그 헤딩 줄도 지운다.
                      상위 레벨의 활성 조상 헤딩 줄(상품명 H1)만 남긴다.
  - `exclude`       : 일치하는 헤딩부터 **다음 헤딩 직전까지**(레벨 무관) 지운다.

`after_heading` 이 일치하지 않아도 `exclude` 는 적용한다. 둘 다 해당이 없으면 원문 그대로다.

## 왜 exclude 범위가 "다음 헤딩 직전까지"인가

마크다운 섹션 의미대로 하위 헤딩까지 지우면 `## 내용 색인` 아래 `### 원문 1쪽` 처럼
원문이 하위 레벨로 붙은 문서에서 원문이 전부 사라진다. 안내 섹션은 목록뿐이므로 자기
본문만 지워도 충분하고, 원문은 레벨과 상관없이 보존된다.

## 패턴 문법

  - `#` 뒤에 공백을 두면 그 레벨만 일치한다(`## 문서 개요`). `#` 가 없으면 모든 레벨이다.
  - 비교는 공백을 모두 지우고 대소문자를 무시한다(`목차` == `목 차`). 헤딩 쪽은 제목
    전체를 감싼 강조(`**`, `__`, 백틱)도 벗긴다. 패턴은 평문으로 적는다.
  - glob(`*`, `?`, `[...]`)을 쓴다. 와일드카드가 없으면 전체 일치다. 제목 글자 `[`·`?`·`*`·`#`
    는 `[[]`·`[?]`·`[*]`·`[#]` 로 적는다.

## 헤딩으로 보지 않는 줄

`#` 로 시작하는 ATX 헤딩만 대상이다(들여쓰기 0~3칸). 코드 펜스(``` ~~~), 여러 줄 HTML
주석, `<pre>`·`<script>`·`<style>`·`<textarea>` 블록, 문서 맨 앞 YAML front matter 안의 줄은
헤딩으로 보지 않고 그대로 보존한다. Setext 헤딩(밑줄형)과 HTML `<h2>` 는 대상이 아니다.

## 본문이 남지 않으면

지운 결과에 헤딩·주석·front matter 말고 남은 본문이 없으면 `ValueError` 다. 원문으로
조용히 되돌리면 제외 설정이 무시된 사실이 가려진다.
"""
from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from typing import Any
from genon.preprocessor.processing.common.reason_codes import CodedValueError

_ATX_RE = re.compile(r"^ {0,3}(?P<hashes>#{1,6})(?:[ \t]+(?P<title>.*?))?(?:[ \t]+#+)?[ \t]*$")
_FENCE_RE = re.compile(r"^ {0,3}(?P<ticks>`{3,}|~{3,})(?P<info>.*)$")
_PATTERN_LEVEL_RE = re.compile(r"^(?P<hashes>#{1,6})[ \t]+(?P<title>\S.*)$")
_RAW_HTML_OPEN_RE = re.compile(r"^ {0,3}<(?P<tag>pre|script|style|textarea)(?:[\s>]|$)", re.IGNORECASE)
_SINGLE_COMMENT_RE = re.compile(r"^\s*<!--.*-->\s*$")
_WRAPPERS = ("**", "__", "`")
_WS_RE = re.compile(r"\s+")
_ALLOWED_KEYS = frozenset({"after_heading", "exclude"})


def _norm(text: str) -> str:
    return _WS_RE.sub("", text).casefold()


def _strip_wrappers(title: str) -> str:
    title = title.strip()
    changed = True
    while changed:
        changed = False
        for w in _WRAPPERS:
            if len(title) > 2 * len(w) and title.startswith(w) and title.endswith(w):
                title = title[len(w):-len(w)].strip()
                changed = True
    return title


@dataclass(frozen=True)
class HeadingPattern:
    level: int | None
    glob: str
    source: str

    @classmethod
    def parse(cls, raw: Any, label: str) -> HeadingPattern:
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError(f"{label} 는 비어 있지 않은 문자열이어야 합니다: {raw!r}")
        text = raw.strip()
        level = None
        if text.startswith("#"):
            m = _PATTERN_LEVEL_RE.match(text)
            if not m:
                raise ValueError(
                    f"{label} '{raw}': '#' 레벨 표기는 1~6개이고 뒤에 공백이 있어야 합니다"
                    f"(예: '## 문서 개요'). 제목의 '#' 글자는 '[#]' 로 적습니다."
                )
            level, text = len(m.group("hashes")), m.group("title")
        glob = _norm(text)
        if not glob.strip("*?"):
            raise ValueError(f"{label} '{raw}': 와일드카드만으로 된 패턴은 쓸 수 없습니다.")
        return cls(level=level, glob=glob, source=raw)

    def matches(self, level: int, title_norm: str) -> bool:
        if self.level is not None and self.level != level:
            return False
        return fnmatch.fnmatchcase(title_norm, self.glob)


@dataclass(frozen=True)
class _Heading:
    index: int
    level: int
    title: str   # 정규화한 제목


def _front_matter_end(lines: list[str]) -> int:
    """문서 맨 앞 YAML front matter 의 닫는 줄 번호. 없으면 -1."""
    if not lines or lines[0].rstrip("\r\n") != "---":
        return -1
    for j in range(1, len(lines)):
        if lines[j].rstrip("\r\n") in ("---", "..."):
            return j
    return -1


def _skip_block(lines: list[str], i: int, is_end) -> int:
    """i 줄에서 시작한 보호 블록의 마지막 줄 번호. 닫히지 않으면 문서 끝까지 보호한다."""
    j = i + 1
    while j < len(lines) and not is_end(lines[j].rstrip("\r\n")):
        j += 1
    return min(j, len(lines) - 1)


def _scan(lines: list[str], markup: set[int] | None = None) -> list[_Heading]:
    """ATX 헤딩 목록. 펜스·HTML 주석·원시 HTML·front matter 안의 줄은 건너뛴다.

    ``markup`` 을 넘기면 본문이 아닌 표기 줄(펜스 여닫이, 여러 줄 HTML 주석)의 번호를 채운다.
    """
    markup = set() if markup is None else markup
    headings: list[_Heading] = []
    n = len(lines)
    i = _front_matter_end(lines) + 1
    while i < n:
        line = lines[i].rstrip("\r\n")
        fence = _FENCE_RE.match(line)
        if fence and not (fence.group("ticks")[0] == "`" and "`" in fence.group("info")):
            ticks = fence.group("ticks")

            def _closes(text: str, ticks: str = ticks) -> bool:
                close = _FENCE_RE.match(text)
                return bool(close and close.group("ticks")[0] == ticks[0]
                            and len(close.group("ticks")) >= len(ticks)
                            and not close.group("info").strip())

            end = _skip_block(lines, i, _closes)
            markup.add(i)
            if end > i and _closes(lines[end].rstrip("\r\n")):
                markup.add(end)
            i = end + 1
            continue
        if "<!--" in line and "-->" not in line.split("<!--", 1)[1]:
            end = _skip_block(lines, i, lambda text: "-->" in text)
            markup.update(range(i, end + 1))
            i = end + 1
            continue
        raw_html = _RAW_HTML_OPEN_RE.match(line)
        if raw_html:
            closing = f"</{raw_html.group('tag').lower()}>"
            if closing not in line.lower():
                i = _skip_block(lines, i, lambda text: closing in text.lower())
            i += 1
            continue
        atx = _ATX_RE.match(line)
        if atx:
            title = _norm(_strip_wrappers(atx.group("title") or ""))
            headings.append(_Heading(i, len(atx.group("hashes")), title))
        i += 1
    return headings


def _has_body(lines: list[str]) -> bool:
    """헤딩·주석·펜스 여닫이·front matter·빈 줄 말고 남은 줄이 있는가."""
    markup: set[int] = set()
    skip = markup | {h.index for h in _scan(lines, markup)}
    fm_end = _front_matter_end(lines)
    return any(
        k > fm_end and k not in skip and line.strip() and not _SINGLE_COMMENT_RE.match(line)
        for k, line in enumerate(lines)
    )


@dataclass(frozen=True)
class MarkdownSectionsSpec:
    """`markdown.sections` 블록 하나를 컴파일한 선언.

    ```yaml
    markdown:
      sections:
        after_heading: '## 원문 전사'
        exclude: ['## 문서 개요', '## 핵심 키워드', '## 내용 색인']
    ```
    """

    doc_types: tuple[str, ...] = ()
    after_heading: HeadingPattern | None = None
    exclude: tuple[HeadingPattern, ...] = ()

    @classmethod
    def from_config(cls, cfg: Any, doc_types: tuple[str, ...] = ()) -> MarkdownSectionsSpec:
        if not isinstance(cfg, dict):
            raise ValueError("custom_fields.markdown.sections는 object 여야 합니다.")
        unknown = sorted(str(k) for k in cfg if k not in _ALLOWED_KEYS)
        if unknown:
            raise ValueError(
                f"markdown.sections 에 쓸 수 없는 키가 있습니다: {unknown}. "
                f"쓸 수 있는 키: {sorted(_ALLOWED_KEYS)}"
            )
        after = cfg.get("after_heading")
        after_pat = None if after is None else HeadingPattern.parse(after, "markdown.sections.after_heading")
        raw_exclude = cfg.get("exclude")
        if raw_exclude is None:
            raw_exclude = []
        if not isinstance(raw_exclude, list):
            raise ValueError("markdown.sections.exclude 는 문자열 목록이어야 합니다(각 항목 앞에 '- ').")
        exclude = tuple(HeadingPattern.parse(v, "markdown.sections.exclude") for v in raw_exclude)
        return cls(doc_types=doc_types, after_heading=after_pat, exclude=exclude)

    def apply(self, text: str) -> tuple[str, int]:
        """섹션을 고른 텍스트와 지운 헤딩 수를 돌려준다. 바뀐 것이 없으면 (원문, 0)."""
        return transform(text, self.after_heading, self.exclude)


def _cut_before(lines: list[str], pattern: HeadingPattern) -> tuple[list[str], bool]:
    headings = _scan(lines)
    target = next((h for h in headings if pattern.matches(h.level, h.title)), None)
    if target is None:
        return lines, False
    stack: list[_Heading] = []
    for h in headings:
        if h.index >= target.index:
            break
        while stack and stack[-1].level >= h.level:
            stack.pop()
        stack.append(h)
    # 문서 맨 앞 front matter 는 보존한다(일부 키만 본문에서 뺀 설정이면 남은 YAML 이 여기 있다).
    kept = lines[:_front_matter_end(lines) + 1]
    kept += [lines[h.index] for h in stack if h.level < target.level]
    if kept:
        kept.append("\n")
    return kept + lines[target.index + 1:], True


def _drop_sections(lines: list[str], patterns: tuple[HeadingPattern, ...]) -> tuple[list[str], int]:
    headings = _scan(lines)
    drop: set[int] = set()
    removed = 0
    for pos, h in enumerate(headings):
        if not any(p.matches(h.level, h.title) for p in patterns):
            continue
        end = headings[pos + 1].index if pos + 1 < len(headings) else len(lines)
        # 다음 헤딩 바로 앞의 한 줄 주석(source_page 등)은 다음 섹션의 것이라 남긴다.
        keep_from = end
        while keep_from - 1 > h.index and (
            not lines[keep_from - 1].strip() or _SINGLE_COMMENT_RE.match(lines[keep_from - 1])
        ):
            keep_from -= 1
        drop.update(range(h.index, keep_from))
        removed += 1
    return [line for k, line in enumerate(lines) if k not in drop], removed


def transform(
    text: str,
    after_heading: HeadingPattern | None = None,
    exclude: tuple[HeadingPattern, ...] = (),
) -> tuple[str, int]:
    if not text or "#" not in text:
        return text, 0
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith(("\n", "\r")):
        lines[-1] += "\n"
        trailing_added = True
    else:
        trailing_added = False
    removed = 0
    if after_heading is not None:
        lines, cut = _cut_before(lines, after_heading)
        removed += int(cut)
    if exclude:
        lines, dropped = _drop_sections(lines, exclude)
        removed += dropped
    if not removed:
        return text, 0
    if not _has_body(lines):
        raise CodedValueError(
            "markdown.sections 적용 후 남은 본문이 없습니다. after_heading/exclude 설정이 "
            "문서 구조와 맞는지 확인하세요.",
            "PARSE_D012",
        )
    out = "".join(lines)
    if trailing_added and out.endswith("\n"):
        out = out[:-1]
    return out, removed
