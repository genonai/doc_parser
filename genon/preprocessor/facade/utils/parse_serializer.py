"""DoclingDocument → parse API 응답 포맷 직렬화 유틸."""

from __future__ import annotations

import json
import logging

from docling_core.types import DoclingDocument
from docling_core.types.doc import PictureItem, TableItem
from docling_core.types.doc.base import CoordOrigin
from docling_core.types.doc.document import ContentLayer

_log = logging.getLogger(__name__)


def normalize_output_format(value) -> str:
    fmt = str(value).strip().lower()
    if fmt not in {"json", "html", "markdown"}:
        _log.warning(f"[DocumentProcessor] Invalid output.format '{value}', fallback to 'json'")
        return "json"
    return fmt


def normalize_table_format(value) -> str:
    fmt = str(value).strip().lower()
    if fmt not in {"html", "markdown"}:
        _log.warning(f"[DocumentProcessor] Invalid output.table_format '{value}', fallback to 'html'")
        return "html"
    return fmt


def get_normalized_coords(bbox, page_w: float, page_h: float) -> list:
    if bbox.coord_origin != CoordOrigin.TOPLEFT:
        bbox = bbox.to_top_left_origin(page_h)
    l = round(bbox.l / page_w, 4)
    t = round(bbox.t / page_h, 4)
    r = round(bbox.r / page_w, 4)
    b = round(bbox.b / page_h, 4)
    return [{"x": l, "y": t}, {"x": r, "y": t}, {"x": r, "y": b}, {"x": l, "y": b}]


def item_to_html(item, element_id: int, doc: DoclingDocument) -> str:
    label_value = item.label.value if hasattr(item.label, "value") else str(item.label)
    if isinstance(item, TableItem):
        return item.export_to_html(doc=doc) or f"<table id='{element_id}'></table>"
    if isinstance(item, PictureItem):
        return f"<figure id='{element_id}'></figure>"
    text = (getattr(item, "text", "") or "").replace("\n", "<br>")
    if label_value == "title":
        return f"<h1 id='{element_id}'>{text}</h1>"
    if label_value == "section_header":
        level = max(1, min(getattr(item, "level", 1), 6))
        return f"<h{level} id='{element_id}'>{text}</h{level}>"
    if label_value == "list_item":
        return f"<p id='{element_id}' data-category='list'>{text}</p>"
    return f"<p id='{element_id}' data-category='{label_value}'>{text}</p>"


def export_table_content(item: TableItem, doc: DoclingDocument, table_format: str = "html") -> str:
    try:
        text = item.export_to_markdown(doc=doc) if table_format == "markdown" else item.export_to_html(doc=doc)
        if text and text.strip():
            return text
    except Exception:
        pass
    try:
        if item.data and item.data.table_cells:
            parts = [str(c.text).strip() for c in item.data.table_cells if (getattr(c, "text", "") or "").strip()]
            if parts:
                return " ".join(parts)
    except Exception:
        pass
    return getattr(item, "text", "") or ""


def docling_to_parse_format(doc: DoclingDocument, table_format: str = "html") -> dict:
    elements = []
    element_id = 0
    try:
        default_page_no = min(doc.pages.keys()) if getattr(doc, "pages", None) else 1
    except Exception:
        default_page_no = 1

    for item, _ in doc.iterate_items(included_content_layers={ContentLayer.BODY, ContentLayer.FURNITURE}):
        prov_list = getattr(item, "prov", None) or []
        prov = prov_list[0] if prov_list else None
        page_no = getattr(prov, "page_no", None)
        if not isinstance(page_no, int) or page_no <= 0:
            page_no = default_page_no

        coordinates = []
        if prov is not None:
            try:
                page_info = doc.pages.get(page_no)
                if page_info is None or page_info.size is None:
                    raise ValueError("no page size")
                coordinates = get_normalized_coords(prov.bbox, page_info.size.width, page_info.size.height)
            except Exception:
                coordinates = []

        label_value = item.label.value if hasattr(item.label, "value") else str(item.label)
        text = export_table_content(item, doc, table_format) if isinstance(item, TableItem) else (getattr(item, "text", "") or "")

        elements.append({"category": label_value, "content": text, "coordinates": coordinates, "id": element_id, "page": page_no})
        element_id += 1

    return {"elements": elements, "usage": {"pages": doc.num_pages()}}


def serialize_docling_document(doc: DoclingDocument) -> dict:
    try:
        return doc.model_dump(mode="json")
    except Exception:
        try:
            return json.loads(doc.model_dump_json())
        except Exception:
            return doc.export_to_dict()


def replace_markdown_tables_with_html(doc: DoclingDocument, markdown_text: str) -> str:
    if not markdown_text:
        return markdown_text
    out = markdown_text
    for item, _ in doc.iterate_items(included_content_layers={ContentLayer.BODY, ContentLayer.FURNITURE}):
        if not isinstance(item, TableItem):
            continue
        try:
            md_table_raw = item.export_to_markdown(doc=doc)
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


def docling_to_content(doc: DoclingDocument, output_format: str = "json", table_format: str = "html") -> str:
    layers = {ContentLayer.BODY, ContentLayer.FURNITURE}
    if output_format == "html":
        return doc.export_to_html(included_content_layers=layers)
    if output_format == "markdown":
        markdown_text = doc.export_to_markdown(included_content_layers=layers)
        if table_format == "html":
            return replace_markdown_tables_with_html(doc, markdown_text)
        return markdown_text
    return ""


def normalize_response(result: dict) -> dict:
    result.setdefault("content", "")
    result.setdefault("elements", [])
    result.setdefault("usage", {"pages": 0})
    return result


def content_response(content: str, pages: int = 0) -> dict:
    return {"elements": [], "usage": {"pages": pages}, "content": content}


def build_docling_response(
    doc: DoclingDocument,
    output_format: str = "json",
    table_format: str = "html",
    clear_coordinates: bool = False,
) -> dict:
    if output_format == "json":
        result = docling_to_parse_format(doc, table_format=table_format)
        if clear_coordinates:
            for element in result.get("elements", []):
                element["coordinates"] = []
        return result
    try:
        pages = max(1, int(doc.num_pages()))
    except Exception:
        pages = 0
    return content_response(docling_to_content(doc, output_format, table_format), pages=pages)


def audio_to_parse_format(text: str) -> dict:
    return {
        "elements": [{"category": "paragraph", "content": text, "coordinates": [], "id": 0, "page": 1}],
        "usage": {"pages": 1},
    }


def sheet_to_html(sheet: dict) -> str:
    data_rows = sheet.get("data_rows", [])
    if not data_rows:
        return "<table></table>"
    cols = list(data_rows[0].keys())
    header = "".join(f"<th>{c}</th>" for c in cols)
    rows_html = "".join("<tr>" + "".join(f"<td>{row.get(c, '')}</td>" for c in cols) + "</tr>" for row in data_rows)
    return f"<table><tr>{header}</tr>{rows_html}</table>"


def tabular_to_parse_format(data_dict: dict) -> dict:
    sheets = data_dict.get("data", [])
    elements = [
        {"category": "table", "content": sheet_to_html(sheet), "coordinates": [], "id": idx, "page": idx + 1}
        for idx, sheet in enumerate(sheets)
    ]
    return {"elements": elements, "usage": {"pages": len(sheets)}}


def langchain_to_parse_format(docs: list) -> dict:
    elements = []
    for idx, doc in enumerate(docs):
        page = doc.metadata.get("page", idx)
        if isinstance(page, int):
            page = page + 1
        elements.append({"category": "paragraph", "content": doc.page_content, "coordinates": [], "id": idx, "page": page})
    return {"elements": elements, "usage": {"pages": max((e["page"] for e in elements), default=0)}}
