"""HWP SDK(convtext) 출력을 판정 로직 없이 그대로 DoclingDocument 로 옮기는 백엔드.

SDK 실행, 결과 JSON 파싱, 페이지 정보 설정, 이미지·수식 처리는 genos_hwp_backend_new 를 그대로 쓴다.
제목 판정·강등·목차 감지·배치용 표 풀기·중복 제거는 하지 않는다.
SDK 단락 하나가 TEXT 하나가 되고, 표는 SDK 가 준 HTML 문자열을 그대로 TEXT 로 넣는다.
"""

import logging
from typing import Any, Dict, List

from docling_core.types.doc import (
    BoundingBox,
    CoordOrigin,
    DocItemLabel,
    DoclingDocument,
    ProvenanceItem,
)

from docling.backend.genos_hwp_backend_new import GenosHwpDocumentBackend as _GenosHwpNewDocumentBackend

_log = logging.getLogger(__name__)


def _prov(page_no: int, length: int) -> ProvenanceItem:
    return ProvenanceItem(
        page_no=page_no,
        bbox=BoundingBox(l=0, t=0, r=1, b=1, coord_origin=CoordOrigin.BOTTOMLEFT),
        charspan=(0, length),
    )


class GenosHwpDocumentBackend(_GenosHwpNewDocumentBackend):
    def _walk_hwp_data(self, data: List[List[Dict]], doc: DoclingDocument):
        for paragraph_items in data:
            if not paragraph_items:
                continue
            page_no = next((i.get("page", 1) for i in paragraph_items if "page" in i), 1)
            texts: List[str] = []
            # 자동 번호·글머리표(□, 1. 등)는 SDK 가 단락 속성 bullet 으로 따로 주므로 단락 첫 텍스트 앞에 붙인다.
            para = next((i for i in paragraph_items if str(i.get("item", "")).lower() == "para"), None)
            bullet = [str((para or {}).get("bullet", "") or "").strip()]

            def flush():
                text = "".join(texts).strip()
                texts.clear()
                if text and bullet[0]:
                    text = f"{bullet[0]} {text}"
                    bullet[0] = ""
                if text:
                    doc.add_text(label=DocItemLabel.TEXT, text=text, parent=doc.body, prov=_prov(page_no, len(text)))

            for item in paragraph_items:
                i_type = str(item.get("item", "")).lower()
                i_value = item.get("value", "") or ""
                if i_type == "text":
                    texts.append(i_value)
                elif i_type == "table":
                    flush()
                    if i_value.strip():
                        doc.add_text(
                            label=DocItemLabel.TEXT,
                            text=i_value.strip(),
                            parent=doc.body,
                            prov=_prov(page_no, len(i_value.strip())),
                        )
                elif i_type == "image":
                    flush()
                    self._handle_image(item, doc, page_no, parent=doc.body)
                elif i_type == "latex":
                    flush()
                    self._handle_latex(item, doc, page_no, parent=doc.body)
            flush()
