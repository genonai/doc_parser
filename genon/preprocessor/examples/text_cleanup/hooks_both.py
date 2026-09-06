"""설정과 훅을 함께 쓰는 예시. 이 클래스 몸통을 facade/chunking_processor.py 에 붙인다.

전 문서 공통 정제(장식 마커·엔티티)는 yaml 이 이미 했다. 훅은 **그 doc_type 에만 필요한
것**만 한다 — 여기서는 상담 문서의 내부용 안내 청크를 적재에서 뺀다.
"""
from __future__ import annotations

from genon.preprocessor.facade.core import toolbox as tb

# 이 문구가 든 청크는 상담직원용 안내라 검색 대상이 아니다.
INTERNAL_ONLY = "상담직원용"


class Hooks:
    def post_chunk(self, vectors, **kwargs):
        """[후처리] 응답 직전. cs_hpp 의 내부용 안내 청크를 버린다."""
        if kwargs.get("doc_type") != "cs_hpp":
            return vectors
        kept = [v for v in vectors if INTERNAL_ONLY not in (v.text or "")]
        if len(kept) == len(vectors):
            return vectors
        # 청크를 버렸으니 순번(i_chunk_on_doc·n_chunk_of_doc 등)까지 다시 맞춘다.
        return tb.refresh_stats(kept)
