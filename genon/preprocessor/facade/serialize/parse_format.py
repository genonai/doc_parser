"""DoclingDocument → 파서 응답 포맷.

parser facade 가 "어떤 확장자를 어느 경로로 보내는가" 를 갖는다면, 이 모듈은 그 경로가
만든 문서를 응답으로 굳히는 마지막 단계만 갖는다. 전부 인자만 보고 답하는 함수이고
프로세서 인스턴스를 받지 않는다.

여기 **없는** 것: 응답 조립(`_build_docling_response`). 그것은 설정 4종을 읽고, 문서를
변이하고(strip_enricher_meta), guardrail 로 외부 HTTP 를 부른다 — 순수 변환 모듈에
네트워크 호출이 들어가서는 안 되므로 facade 에 남겼다.

facade 에는 이 함수들을 부르는 얇은 staticmethod 래퍼가 남아 있다. 단위 테스트가
`DocumentProcessor._docling_to_parse_format(...)` 처럼 클래스 경유로 부르고
`patch.object(DocumentProcessor, ...)` 로 갈아끼우기 때문이다.
"""

from __future__ import annotations

import json

from docling_core.types.doc import DoclingDocument, PictureItem, TableItem
from docling_core.types.doc.base import CoordOrigin
from docling_core.types.doc.document import ContentLayer

from genon.preprocessor.facade.chunking import table_shape as ts
from genon.preprocessor.facade.common.markdown_export import export_markdown
from genon.preprocessor.facade.enrichment.image_description import (
    PictureDescriptionExtractor,
)
from genon.preprocessor.facade.enrichment.table_description import (
    TableDescriptionExtractor,
    refined_html_to_format,
)

BODY_LAYERS = {ContentLayer.BODY, ContentLayer.FURNITURE}


def doc_is_html_origin(doc) -> bool:
    """원본이 HTML 계열인가. 표 헤더 행 수 판정 규칙이 여기서 갈린다."""
    origin = getattr(doc, "origin", None)
    mimetype = str(getattr(origin, "mimetype", "") or "").lower()
    filename = str(getattr(origin, "filename", "") or "").lower()
    return mimetype in {"text/html", "application/xhtml+xml"} or filename.endswith(
        (".html", ".htm", ".xhtml"))


def get_normalized_coords(bbox, page_w: float, page_h: float) -> list:
    """BoundingBox → 정규화된 4-코너 좌표 ([top-left, top-right, bottom-right, bottom-left])."""
    if bbox.coord_origin != CoordOrigin.TOPLEFT:
        bbox = bbox.to_top_left_origin(page_h)
    l = round(bbox.l / page_w, 4)
    t = round(bbox.t / page_h, 4)
    r = round(bbox.r / page_w, 4)
    b = round(bbox.b / page_h, 4)
    return [
        {"x": l, "y": t},
        {"x": r, "y": t},
        {"x": r, "y": b},
        {"x": l, "y": b},
    ]


def export_table_content(
    item: TableItem, doc: DoclingDocument, table_format: str = "html",
    compact_tables: bool = True,
) -> str:
    """TableItem을 지정한 포맷으로 변환. auto 면 그 표의 구조를 보고 고른다.

    청커도 같은 analyze_grid/resolve_table_format 을 쓰므로, 같은 표가 파서 출력과
    청크에서 다른 형식으로 나가지 않는다.
    """
    table_format = ts.resolve_table_format(
        table_format, ts.analyze_grid(
            getattr(getattr(item, "data", None), "grid", None),
            getattr(getattr(item, "data", None), "num_cols", 0),
            is_html_origin=doc_is_html_origin(doc),
        ),
    )
    try:
        if table_format == "markdown":
            # compact_tables 는 컬럼 정렬 패딩을 없애 대형 표 markdown 크기를 줄인다.
            text = export_markdown(doc, item=item, compact_tables=compact_tables)
        else:
            text = item.export_to_html(doc=doc)
        if text and text.strip():
            return text
    except Exception:
        pass

    try:
        if item.data and item.data.table_cells:
            parts = []
            for cell in item.data.table_cells:
                value = getattr(cell, "text", "")
                if value and str(value).strip():
                    parts.append(str(value).strip())
            if parts:
                return " ".join(parts)
    except Exception:
        pass

    return getattr(item, "text", "") or ""


def docling_sheet_prefix(item, doc) -> str:
    """xlsx docling 표의 부모 그룹(name='sheet: X')에서 시트명을 뽑아 '시트명: X\\n' 접두 생성.
    시트 그룹이 없으면 '' 반환(비-xlsx 문서엔 실질 미적용)."""
    try:
        parent = item.parent.resolve(doc) if getattr(item, "parent", None) else None
        name = getattr(parent, "name", None)
    except Exception:
        name = None
    if not name:
        return ""
    if name.startswith("sheet: "):
        name = name[len("sheet: "):]
    name = name.strip()
    return f"시트명: {name}\n" if name else ""


def docling_to_parse_format(doc: DoclingDocument, table_format: str = "html",
                            compact_tables: bool = True) -> dict:
    """DoclingDocument → sample_result.json 호환 출력 포맷."""
    elements = []
    element_id = 0
    default_page_no = 1
    try:
        if getattr(doc, "pages", None):
            default_page_no = min(doc.pages.keys())
    except Exception:
        default_page_no = 1

    for item, _ in doc.iterate_items(included_content_layers=BODY_LAYERS):
        prov_list = getattr(item, "prov", None) or []
        prov = prov_list[0] if len(prov_list) > 0 else None

        page_no = getattr(prov, "page_no", None)
        if not isinstance(page_no, int) or page_no <= 0:
            page_no = default_page_no

        coordinates = []
        if prov is not None:
            try:
                page_info = doc.pages.get(page_no)
                if page_info is None or page_info.size is None:
                    raise ValueError("no page size")
                page_w = page_info.size.width
                page_h = page_info.size.height
                coordinates = get_normalized_coords(prov.bbox, page_w, page_h)
            except Exception:
                coordinates = []

        label_value = item.label.value if hasattr(item.label, "value") else str(item.label)

        if isinstance(item, TableItem):
            text = export_table_content(
                item=item,
                doc=doc,
                table_format=table_format,
                compact_tables=compact_tables,
            )
            sheet_prefix = docling_sheet_prefix(item, doc)
            # refine ON 이면 재구성 HTML 로 표 본체 교체, 요약이 있으면 항상 병기.
            refined_html = TableDescriptionExtractor.extract_refined_html(item)
            table_summary = TableDescriptionExtractor.extract_summary(item)
            if refined_html:
                # refine 은 항상 HTML 로 재구성 → output table_format 에 맞춰 변환(markdown 등).
                text = sheet_prefix + refined_html_to_format(refined_html, table_format, compact_tables)
            else:
                # xlsx docling 표면 시트명 접두 추가(비-xlsx 는 "" 라 영향 없음).
                text = sheet_prefix + text
            if table_summary:
                text = text + "\n---\n[표 설명]\n" + table_summary
        else:
            text = getattr(item, "text", "") or ""

        element = {
            "category": label_value,
            "content": text,
            "coordinates": coordinates,
            "id": element_id,
            "page": page_no,
        }
        if isinstance(item, PictureItem):
            image_description = PictureDescriptionExtractor.extract(item)
            if image_description:
                # 최종 소비계층에서 별도 필드 매핑 없이 바로 활용할 수 있도록
                # picture 의 content 를 이미지 설명 텍스트로 채운다.
                element["content"] = image_description

        elements.append(element)
        element_id += 1

    return {
        "elements": elements,
        "usage": {"pages": docling_page_count(doc)},
    }


def serialize_docling_document(doc: DoclingDocument) -> dict:
    """DoclingDocument를 JSON 직렬화 가능한 dict로 변환."""
    try:
        # pydantic v2 호환 방식 (enum/datetime 등 JSON-safe 변환 포함)
        return doc.model_dump(mode="json")
    except Exception:
        try:
            # model_dump가 호환되지 않을 때 문자열 JSON을 다시 dict로 복원
            return json.loads(doc.model_dump_json())
        except Exception:
            # 최후 폴백: docling 기본 export
            return doc.export_to_dict()


def replace_markdown_tables_with_html(doc: DoclingDocument, markdown_text: str) -> str:
    """Markdown 문자열의 테이블 블록을 순차적으로 HTML 테이블로 치환."""
    if not markdown_text:
        return markdown_text

    out = markdown_text
    for item, _ in doc.iterate_items(included_content_layers=BODY_LAYERS):
        if not isinstance(item, TableItem):
            continue

        try:
            md_table_raw = export_markdown(doc, item=item)
            html_table = item.export_to_html(doc=doc)
        except Exception:
            continue

        if not md_table_raw or not html_table:
            continue

        md_table = md_table_raw.strip()
        if not md_table:
            continue

        idx = out.find(md_table)
        if idx >= 0:
            out = out[:idx] + html_table + out[idx + len(md_table):]
        else:
            idx_raw = out.find(md_table_raw)
            if idx_raw >= 0:
                out = out[:idx_raw] + html_table + out[idx_raw + len(md_table_raw):]

    return out


def docling_to_content(doc: DoclingDocument, output_format: str, table_format: str) -> str:
    """DoclingDocument를 output.format에 따라 content 문자열로 변환."""
    if output_format == "html":
        return doc.export_to_html(included_content_layers=BODY_LAYERS)

    if output_format == "markdown":
        markdown_text = export_markdown(doc, included_content_layers=BODY_LAYERS)
        if table_format == "html":
            return replace_markdown_tables_with_html(doc, markdown_text)
        return markdown_text

    return ""


def docling_page_count(doc: DoclingDocument) -> int:
    """DoclingDocument 의 페이지 수. 페이지 개념이 없는 백엔드는 1 로 센다.

    docling HTML 백엔드는 브라우저 렌더링을 켠 경우에만 doc.pages 를 채우므로 평소엔 0 이다.
    raw HTML 블록이 섞인 md 도 md_backend 가 HTML 백엔드로 위임하면서(page 1 스텁이 버려진다)
    같은 상태가 된다. 내용이 있는 문서를 0페이지로 내보내면 소비계층의 페이지 기반 계산이
    전부 무너지므로 1 로 올린다. 진짜 빈 문서는 0 을 유지한다.
    """
    try:
        pages = int(doc.num_pages())
    except Exception:
        pages = 0
    if pages >= 1:
        return pages
    try:
        has_content = next(doc.iterate_items(), None) is not None
    except Exception:
        has_content = False
    return 1 if has_content else 0


def normalize_response(result: dict) -> dict:
    """응답에 content / elements / usage 키가 항상 존재하도록 보장."""
    result.setdefault("content", "")
    result.setdefault("elements", [])
    result.setdefault("usage", {"pages": 0})
    return result


def content_response(content: str, pages: int = 0) -> dict:
    """content 전용 출력 포맷."""
    return {
        "elements": [],
        "usage": {"pages": pages},
        "content": content,
    }


def audio_to_parse_format(text: str) -> dict:
    """전사 텍스트 → parse format."""
    return {
        "elements": [
            {
                "category": "paragraph",
                "content": text,
                "coordinates": [],
                "id": 0,
                "page": 1,
            }
        ],
        "usage": {"pages": 1},
    }


def tabular_to_parse_format(data_dict: dict) -> dict:
    """tabular data_dict(converters.xlsx_processor 산출) → 행별 parse format."""
    from genon.preprocessor.converters.xlsx_processor import tabular_data_to_parse_format

    return tabular_data_to_parse_format(data_dict)


def langchain_to_parse_format(docs: list) -> dict:
    """LangChain Document 목록 → parse format."""
    elements = []
    for idx, doc in enumerate(docs):
        page = doc.metadata.get("page", idx)
        if isinstance(page, int):
            page = page + 1  # 0-based → 1-based
        elements.append({
            "category": "paragraph",
            "content": doc.page_content,
            "coordinates": [],
            "id": idx,
            "page": page,
        })
    num_pages = max((e["page"] for e in elements), default=0)
    return {
        "elements": elements,
        "usage": {"pages": num_pages},
    }
