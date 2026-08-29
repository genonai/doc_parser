"""청크 선두 `HEADER:` 라인의 섹션 경로 조립.

intelligent/convert/chunking 세 facade 가 동일하게 복제해 두었던 로직의 단일 사본이다.
구분자와 리프 상한은 facade 쪽 상수로 남겨 두었으므로(사이트별 조정 대상) 인자로 받는다.

크기 산정(분할 예산 / 병합 재검증)과 실제 부착(compose_vectors)이 반드시 같은 문자열을
봐야 청크가 chunk_size 를 넘지 않는다. 예전에는 이 조립이 네 곳에 흩어져 있어 분할 예산과
병합이 헤더 몫을 빼먹고 청크가 한도를 초과했다 — 그래서 한 곳으로 모았다.
"""

from __future__ import annotations

import os
import unicodedata
from pathlib import Path
from typing import Any, Optional


def normalize_filename_title(value: Any) -> str:
    """파일명과 TITLE 을 비교하기 위한 유니코드/대소문자 정규화."""
    if not isinstance(value, str):
        return ""
    return unicodedata.normalize("NFKC", value).strip().casefold()


def filename_title_candidates(document: Any) -> set[str]:
    """문서 이름에서 HEADER 에 넣지 않을 파일명 TITLE 후보를 만든다.

    backend 에 따라 TITLE 이 `sample.pdf` 또는 `sample` 로 들어올 수 있어 원본명과
    확장자를 제거한 이름을 모두 비교한다. 그 밖의 실제 TITLE 은 헤더 경로에 유지한다.
    """
    raw_names = [getattr(document, "name", None)]
    origin = getattr(document, "origin", None)
    raw_names.append(getattr(origin, "filename", None))

    candidates: set[str] = set()
    for raw_name in raw_names:
        if not isinstance(raw_name, str) or not raw_name.strip():
            continue
        basename = os.path.basename(raw_name.replace("\\", "/"))
        for candidate in (basename, Path(basename).stem):
            normalized = normalize_filename_title(candidate)
            if normalized:
                candidates.add(normalized)
    return candidates


def union_paths(first, second) -> Optional[list]:
    """헤더 경로 목록 두 개를 순서 보존 dedup 으로 합친다(청크 병합 시 사용)."""
    merged: list = []
    for path in list(first or []) + list(second or []):
        if path and path not in merged:
            merged.append(path)
    return merged or None


def collapse_paths(paths, sep: str) -> list:
    """경로 목록을 정규화: 중복 제거 + 다른 경로의 진부분 접두인 경로 버리기.

    `A` 와 `A > B` 가 함께 오면 `A > B` 만 남긴다(같은 위치를 두 번 말하는 셈).
    헤더 경로 추출이 이미 한 번 하지만, 청크 병합(union_paths)이 접두 쌍을 다시
    만들 수 있어 렌더 직전에도 적용한다.
    """
    seen: list = []
    for p in (paths or []):
        if p and p not in seen:
            seen.append(p)
    prefixes = [tuple(p.split(sep)) for p in seen]
    return [p for p, tp in zip(seen, prefixes)
            if not any(tq != tp and tq[:len(tp)] == tp for tq in prefixes)]


def render_header_paths(headings, sep: str, path_sep: str, max_leaves: int) -> str:
    """경로 목록을 한 줄로 렌더. 공통 조상은 factor 하고 리프 수는 상한을 둔다."""
    paths = collapse_paths([h for h in (headings or []) if h], sep)
    if not paths:
        return ""
    if len(paths) == 1:
        return paths[0]

    split = [p.split(sep) for p in paths]
    shortest = min(len(s) for s in split)
    # 공통 조상(모든 경로가 공유하는 선행 레벨). 마지막 레벨은 리프로 남겨야 하므로 제외한다.
    common: list = []
    for level in zip(*split):
        if len(set(level)) != 1 or len(common) >= shortest - 1:
            break
        common.append(level[0])

    leaves = [sep.join(s[len(common):]) for s in split]
    shown, rest = leaves[:max_leaves], len(leaves) - max_leaves
    body = path_sep.join(shown) + (f" … 외 {rest}개" if rest > 0 else "")
    if not common:
        return body
    return sep.join(common) + sep + "(" + body + ")"


def build_header_line(headings, include_header: bool, sep: str, path_sep: str, max_leaves: int) -> str:
    """청크 선두에 실제로 붙을 `HEADER: <경로들>\n` 문자열.

    headings 의 원소 하나가 하나의 완전한 경로(`부모 > 자식`)다. 경로가 여러 개면
    공통 조상을 한 번만 쓰고 리프만 나열한다 — 부모를 경로마다 반복하지 않는다.

        1개        : `상품 안내 > 우대금리 조건`
        여러 개    : `상품 안내 > (우대금리 조건 | 가입 제한 | 수수료 안내)`
        상한 초과  : `제1장 총칙 > (제1조 | 제2조 … 외 68개)`
    """
    if not include_header or not headings:
        return ""
    return "HEADER: " + render_header_paths(headings, sep, path_sep, max_leaves) + "\n"
