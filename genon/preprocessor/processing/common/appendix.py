"""규정 문서의 별지/별표/장부 부속서류 매칭 (appendix feature, 2025-09-30).

intelligent / chunking 두 facade 가 같은 구현을 들고 있던 것을 한 벌로 모았다.
탐지 패턴은 사이트마다 손댈 수 있도록 모듈 상수로 빼 두었다.
"""

from __future__ import annotations

import re

# 부속서류 표기 종류. 사이트 문서 관례가 다르면 여기만 고친다.
APPENDIX_KINDS = ("별지", "별표", "장부")

# "별지 제 I -1 호 서식" 같은 표기를 통째로 잡는다. 닫는 구분자나 호/서식에서 끊는다.
_COMPLEX_RE = re.compile(
    r"(" + "|".join(APPENDIX_KINDS) + r")(?:제)?([^<>()\[\]]+?)(?=(?:호|서식)|[<>\)\]]|$)"
)
# "(별표)", "[별지]" 처럼 종류만 단독으로 나오는 표기.
_STANDALONE_RE = re.compile(r"[\(\[]+(" + "|".join(APPENDIX_KINDS) + r")[\)\]]+")


def check_appendix_keywords(content: str, appendix_list: list) -> str:
    """본문에서 찾은 부속서류 표기를 첨부 파일명 목록과 대조해 매칭된 파일명을 돌려준다.

    공백은 양쪽 모두에서 제거하고 비교한다(원문 표기가 "별지 제 1 호" 처럼 벌어져 있다).
    매칭이 없으면 빈 문자열.
    """
    if not content or not appendix_list:
        return ""

    matched_appendices = []
    found_patterns = []

    # 1. 본문에서 부속서류 표기를 먼저 수집
    content = re.sub(r"\s+", "", content)
    for pattern_type, number in _COMPLEX_RE.findall(content):
        found_patterns.extend([
            f"{pattern_type} {number}",
            f"{pattern_type} 제{number}호",
            f"{pattern_type}{number}",
            f"{pattern_type}제{number}호",
        ])
    for pattern_type in set(_STANDALONE_RE.findall(content)):
        found_patterns.append(pattern_type)

    # 2. 수집한 표기가 첨부 파일명에 들어 있으면 매칭으로 본다
    for appendix in appendix_list:
        if not appendix or not isinstance(appendix, str):
            continue
        appendix_clean_no_space = re.sub(r"\s+", "", appendix.replace(".pdf", "").lower().strip())
        for pattern in found_patterns:
            if re.sub(r"\s+", "", pattern).lower() in appendix_clean_no_space:
                matched_appendices.append(appendix)
                break  # 중복 방지

    return ", ".join(matched_appendices) if matched_appendices else ""
