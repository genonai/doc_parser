"""청크 선두 `HEADER:` 라인의 섹션 경로 조립.

intelligent/convert/chunking 세 facade 가 동일하게 복제해 두었던 로직의 단일 사본이다.
구분자와 리프 상한은 facade 쪽 상수로 남겨 두었으므로(사이트별 조정 대상) 인자로 받는다.

크기 산정(분할 예산 / 병합 재검증)과 실제 부착(compose_vectors)이 반드시 같은 문자열을
봐야 청크가 chunk_size 를 넘지 않는다. 예전에는 이 조립이 네 곳에 흩어져 있어 분할 예산과
병합이 헤더 몫을 빼먹고 청크가 한도를 초과했다 — 그래서 한 곳으로 모았다.
"""

from __future__ import annotations

import os
import re
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


# markdown 헤딩 줄. 레코드 본문(custom_fields text_from 렌더링)에서 섹션 제목을 찾는다.
_MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)


def carry_over_section_headings(pieces: list) -> list:
    """분할된 뒷 조각에 직전 섹션 제목을 `## <제목> (이어서)` 로 다시 붙인다.

    이게 없으면 두 번째 조각이 "8월 17일 2544만으로 감소" 처럼 **무엇에 대한 값인지 알 수 없는
    상태**로 검색에 노출된다. 검색으로 그 조각만 뽑히면 LLM 이 근거로 쓸 수 없다.
    (Contextual Retrieval 과 같은 발상을 LLM 없이 결정론적으로 적용한 것이다.)

    이미 헤딩으로 시작하는 조각은 그대로 둔다 — 섹션 경계에서 깔끔히 잘린 경우다.
    헤딩이 하나도 없는 평문 입력에서는 아무것도 하지 않는다.

    크기 기준 분할(`_expand_splittable_rows`)과 표 기준 분리(`table_blocks.expand_elements`)가
    같은 함수를 쓴다 — 조각을 만드는 이유가 달라도 조각이 문맥을 잃는 문제는 같다.
    """
    out: list = []
    current = None
    for idx, piece in enumerate(pieces):
        text = str(piece).strip("\n")
        if idx and current and not text.lstrip().startswith("#"):
            text = f"{current} (이어서)\n{text}"
        found = _MD_HEADING_RE.findall(str(piece))
        if found:
            marks, title = found[-1]
            current = f"{marks} {title.strip()}"
        out.append(text)
    return out
