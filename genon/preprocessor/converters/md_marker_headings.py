"""Markdown 에서 마커로만 표현된 계층을 `#` heading 으로 승격한다.

## 왜 필요한가

`◈ 기본내용` / `▣ 이용방법` 처럼 heading 태그 없이 도형 마커로만 소제목을 표현한 문서가
있다(모니모 고객센터 계열). HTML 경로는 `html_flatten.promote_marker_headings` 가 이미
이를 처리하지만, 같은 원문이 md 로 오면 그 줄이 평범한 단락이라 청킹 경계가 생기지 않고
문서 하나가 통째로 한 섹션이 된다 — 같은 내용이 포맷에 따라 다르게 쪼개진다.

## HTML 경로와 공유하는 것 / 다른 것

판정 규칙(마커 집합·길이 상한·서술형 종결 강등·장식선 강등·제목 최소 길이)과 레벨 배정은
`html_flatten` 의 것을 **그대로 호출**한다. 규칙을 복사하면 한쪽만 고쳐져 두 경로가 갈린다.

다른 것은 "한 줄"을 정의하는 방법뿐이다. HTML 은 블록 요소 하나가 한 줄이고, md 는 개행이
한 줄이다. 그래서 DOM 조건(블록 자식·표 안·`<br>`) 대신 md 문맥 조건을 본다 — 코드펜스 안,
이미 heading 인 줄, 목록 항목, 표 행, 인용문은 승격하지 않는다.

## 게이트

HTML 과 같은 두 게이트를 쓴다. 이미 하위 heading 으로 계층을 표현한 문서는 대상이 아니고
(`_MARKER_MAX_EXISTING_SUBHEADINGS`), 후보가 너무 적으면 마커를 장식으로 쓴 문서와 구분할
수 없다(`_MARKER_HEADING_MIN`). 둘 다 opt-in 이라 설정을 켠 doc_type 에서만 돌아간다.
"""
from __future__ import annotations

import logging
import re

from .html_flatten import (
    _MARKER_CHARS,
    _MARKER_HEADING_MIN,
    _MARKER_MAX_EXISTING_SUBHEADINGS,
    _marker_levels,
    marker_heading_match,
)

_log = logging.getLogger(__name__)

# 코드펜스(``` 또는 ~~~). 펜스 안은 저자가 원문 그대로 보이려는 영역이라 건드리지 않는다.
_FENCE_RE = re.compile(r"^\s*(```|~~~)")
# 이미 heading 인 줄. 승격 대상이 아니고 게이트 계산의 분모가 된다.
_ATX_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s")
# 목록 항목·표 행·인용문. 마커가 이 구조 안에 있으면 구조가 깨지므로 승격하지 않는다
# (HTML 경로가 `li` 를 _PROMOTABLE_TAGS 에서 뺀 것과 같은 이유다).
_STRUCTURE_LINE_RE = re.compile(r"^\s{0,3}([-*+>]\s|\d+[.)]\s|\|)")


def _promotable_lines(lines: list[str]) -> list[tuple[int, str, str]]:
    """승격 가능한 `(줄 번호, 마커, 제목)` 목록을 문서 순서로 반환한다."""
    candidates: list[tuple[int, str, str]] = []
    in_fence = False
    for index, line in enumerate(lines):
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        stripped = line.strip()
        if not stripped:
            continue
        if _ATX_HEADING_RE.match(line) or _STRUCTURE_LINE_RE.match(line):
            continue
        matched = marker_heading_match(stripped)
        if matched is None:
            continue
        candidates.append((index, matched[0], matched[1]))
    return candidates


def _existing_subheadings(lines: list[str]) -> int:
    """이미 있는 하위 heading(`##` 이상) 수. HTML 의 `<h2>`~`<h6>` 세기와 같은 역할."""
    count = 0
    in_fence = False
    for line in lines:
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = _ATX_HEADING_RE.match(line)
        if match and len(match.group(1)) >= 2:
            count += 1
    return count


def promote_markdown_marker_headings(text: str) -> tuple[str, int]:
    """마커 소제목을 `#` heading 으로 승격한 본문과 승격 건수를 돌려준다.

    승격할 것이 없거나 게이트에 걸리면 원문을 그대로 돌려준다(건수 0).
    마커 문자는 제거하지 않는다 — 저자가 쓴 라벨이고 청커 breadcrumb 에 그대로 실린다
    (HTML 경로와 같은 결정).
    """
    if not text or not any(ch in text for ch in _MARKER_CHARS):
        return text, 0

    lines = text.splitlines()
    if _existing_subheadings(lines) > _MARKER_MAX_EXISTING_SUBHEADINGS:
        return text, 0

    candidates = _promotable_lines(lines)
    if len(candidates) < _MARKER_HEADING_MIN:
        return text, 0

    levels = _marker_levels([marker for _i, marker, _t in candidates])
    for index, marker, _title in candidates:
        lines[index] = f"{'#' * levels[marker]} {lines[index].strip()}"

    _log.info(
        "[md_marker_headings] %s (승격 %d건)",
        {marker: levels[marker] for _i, marker, _t in candidates},
        len(candidates),
    )
    suffix = "\n" if text.endswith("\n") else ""
    return "\n".join(lines) + suffix, len(candidates)
