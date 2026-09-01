"""rich cell 서브트리를 문서 순회에서 걸러 내기 위한 공용 유틸리티.

docling HTML 백엔드는 ``<td><p>...</p><p>...</p></td>`` 처럼 내용이 여러 조각으로 나뉜 셀을
``RichTableCell`` 로 만들고, 그 내용을 TableItem 의 자식 그룹으로 문서 트리에도 넣는다
(`docling/backend/html_backend.py` 의 `group_cell_elements`). 표를 통째로 직렬화하는 쪽은
그 내용을 이미 담고 있으므로, 문서 순회가 같은 아이템을 또 집으면 셀 값이 표 뒤에 평문으로
한 번 더 붙는다(실측 — 연회비 표의 금액 6개, 적립기준 표의 적립률이 중복).

파서 경로는 docling_core serializer 의 ``visited`` 집합을 채워 이 중복을 막고 있고
(`facade/enrichment/json_records`), 청커는 ``iterate_items`` 를 직접 돌기 때문에 여기서
얻은 ref 집합으로 같은 아이템을 건너뛴다.

이 모듈은 docling 타입을 import 하지 않는다. duck typing 으로만 읽는다.
"""

from __future__ import annotations

from typing import Any


def table_embedded_refs(doc: Any) -> set[str]:
    """표 직렬화 결과에 이미 들어 있는 아이템의 ``self_ref`` 를 모은다.

    rich cell 서브트리와 캡션 아이템 둘 다 문서 트리에 그대로 남아 있어, 청커가 순회하며
    다시 집으면 같은 문장이 표 뒤에 한 번 더 붙는다.
    """
    refs = rich_cell_refs(doc)
    for table in getattr(doc, "tables", None) or []:
        for caption in getattr(table, "captions", None) or []:
            cref = getattr(caption, "cref", None)
            if cref:
                refs.add(cref)
    return refs


def rich_cell_refs(doc: Any) -> set[str]:
    """문서 안 모든 표의 rich cell 서브트리에 속한 ``self_ref`` 를 모은다.

    참조가 끊긴 셀은 조용히 건너뛴다 — 중복 제거는 부가 기능이라 여기서 예외를 올리면
    문서 전체 청킹이 막힌다.
    """
    refs: set[str] = set()
    for table in getattr(doc, "tables", None) or []:
        for row in getattr(getattr(table, "data", None), "grid", None) or []:
            for cell in row:
                ref = getattr(cell, "ref", None)
                if ref is None:
                    continue
                try:
                    collect_subtree_refs(ref.resolve(doc=doc), doc, refs)
                except Exception:
                    continue
    return refs


def collect_subtree_refs(node: Any, doc: Any, refs: set[str]) -> None:
    """``node`` 와 그 하위 전부의 ``self_ref`` 를 ``refs`` 에 넣는다."""
    ref = getattr(node, "self_ref", None)
    if ref is None or ref in refs:
        return
    refs.add(ref)
    for child in getattr(node, "children", None) or []:
        try:
            collect_subtree_refs(child.resolve(doc=doc), doc, refs)
        except Exception:
            continue
