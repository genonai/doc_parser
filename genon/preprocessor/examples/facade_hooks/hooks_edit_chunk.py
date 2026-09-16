"""edit_chunk 로 청크를 손보거나 버리는 예시. 이 클래스 몸통을 chunking_processor.py 에 붙인다.

본문 수정과 청크 버리기는 `edit_output` 가 아니라 여기서 한다. 통계(n_char 등)와 순번이
확정되기 **전**이라 코어가 다시 맞춰 주므로 `refresh_stats` 를 부를 필요가 없다.

전 문서 공통 정제는 설정(`chunking.text_cleanup`)이 먼저다 — 그쪽은 청킹 **입력**에 걸려
청크 경계까지 깨끗해진다. 이 훅은 설정이 doc_type 을 가릴 수 없을 때 쓴다.
"""
from __future__ import annotations

import re

from genon.preprocessor.processing.core import toolbox as tb

# 장식 마커. 구조를 나타내던 역할은 마커 승격이 이미 끝냈으므로 지워도 계층이 남는다.
GLYPHS = re.compile(r"[■◈※☎▶●◆▲☞]\s*")
# 파싱이 해독하지 못하고 넘긴 HTML 엔티티.
ENTITIES = {"&gt;": ">", "&lt;": "<", "&amp;": "&", "&nbsp;": " "}
# 이 문구가 든 청크는 상담직원용 안내라 검색 대상이 아니다.
INTERNAL_ONLY = "상담직원용"


class Hooks:
    def edit_chunk(self, text, info, **kwargs):
        """[중간] 청크 한 건. cs_hpp 만 정제하고 내부용 안내는 버린다."""
        if tb.normalize_doc_type(kwargs.get("doc_type")) != "cs_hpp":
            return None                      # 손대지 않는다
        if INTERNAL_ONLY in text:
            return tb.DROP                   # 이 청크를 버린다(순번은 코어가 다시 매긴다)
        cleaned = GLYPHS.sub("", text)
        for src, dst in ENTITIES.items():
            cleaned = cleaned.replace(src, dst)
        return cleaned

    def edit_chunk_row_example(self, text, info, **kwargs):
        """info 는 경로가 달라도 모양이 같다 — 훅 한 벌로 문서·레코드를 함께 다룬다.

        (이 메서드는 설명용이다. 실제로는 위 edit_chunk 하나만 둔다.)
        """
        if info["kind"] == "row" and not info["metadata"].get("USE_YN"):
            return tb.DROP                   # 비활성 레코드는 적재하지 않는다
        if info["kind"] == "docling" and info["headings"]:
            return f"[{info['headings'][0]}] {text}"
        return None
