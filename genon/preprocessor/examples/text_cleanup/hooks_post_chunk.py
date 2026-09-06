"""`post_chunk` 로 정제하는 예시. 이 클래스 몸통을 facade/chunking_processor.py 에 붙인다.

설정(`chunking.text_cleanup`)은 프로세서 전체에 걸려 doc_type 을 가릴 수 없다. 특정
doc_type 만 다르게 정제해야 할 때 이 자리를 쓴다.
"""
from __future__ import annotations

import re

from genon.preprocessor.facade.core import toolbox as tb

# 장식 마커. 구조를 나타내던 역할은 마커 승격(`source.pre.marker_headings`)이 이미
# 끝냈으므로 청크 본문에서는 지워도 계층이 사라지지 않는다.
GLYPHS = re.compile(r"[■◈※☎▶●◆▲☞]\s*")
# 파싱이 해독하지 못하고 넘긴 HTML 엔티티.
ENTITIES = {"&gt;": ">", "&lt;": "<", "&amp;": "&", "&nbsp;": " "}


class Hooks:
    def post_chunk(self, vectors, **kwargs):
        """[후처리] 응답 직전. cs_hpp 만 정제한다."""
        if kwargs.get("doc_type") != "cs_hpp":
            return vectors
        for v in vectors:
            text = GLYPHS.sub("", v.text or "")
            for src, dst in ENTITIES.items():
                text = text.replace(src, dst)
            v.text = text
        # 본문을 고쳤으니 파생 필드를 다시 맞춘다. 청크를 버리지 않았으므로 순번은 그대로 둔다.
        return tb.refresh_stats(vectors, reindex=False)
