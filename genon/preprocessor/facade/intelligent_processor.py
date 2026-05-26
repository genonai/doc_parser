import inspect
import json
import logging
from pathlib import Path
from typing import Any, List

import yaml
from fastapi import Request
from docling_core.transforms.chunker import DocChunk
from docling_core.types import DoclingDocument
from docling_core.types.doc import PictureItem, TableItem
from docling_core.types.doc.base import CoordOrigin
from docling_core.types.doc.document import ContentLayer

from genon.preprocessor.facade.loaders import DoclingLoader
from genon.preprocessor.facade.chunkers import CHUNKERS
from genon.preprocessor.facade.enrichment import ENRICHERS
from genon.preprocessor.facade.metadata import GenOSVectorMetaBuilder
from genon.preprocessor.facade.utils.logging_utils import setup_logging

config_filename = "intelligent_config.yaml"

def _resolve_default_parser_config_path(config_filename) -> str:
    base_dir = Path(__file__).resolve().parent
    local_config = (base_dir / f"../resource_dev/{config_filename}").resolve()
    default_config = (base_dir / f"../resource/{config_filename}").resolve()

    if local_config.exists():
        return str(local_config)
    return str(default_config)

config = yaml.safe_load(Path(_resolve_default_parser_config_path(config_filename)).read_text(encoding="utf-8"))

_log = logging.getLogger(__name__)


class DocumentProcessor:
    config: dict = config

    def __init__(self, config: dict) -> None:
        self.config = config
        setup_logging(int(config.get("log_level", 0)))
        self.return_level = config.get("return_level", "vector")

        assert self.return_level in [
            "document",
            "vector",
        ], f"다음 리턴 레벨 중에서 골라주세요: document | vector, 현재 리턴 레벨: {self.return_level}"

        self._ext_loaders = self._build_ext_loaders(config["format_options"], config.get("resource_path"))
        self._chunker = self._build_chunker(config["chunker"])
        self.enrichers = self._build_enrichers(
            config.get("enrichers", []),
            genos_url=config.get("genos_url", ""),
        )
        self.genos_meta_builder = GenOSVectorMetaBuilder()
        output_cfg = config.get("output", {})
        self._output_format = self._normalize_output_format(output_cfg.get("format", "json"))
        self._table_format = self._normalize_table_format(output_cfg.get("table_format", "html"))

    def _build_enrichers(self, enrichers_cfg: list, genos_url: str = "") -> list:
        if not enrichers_cfg:
            return []
        result = []
        for item in enrichers_cfg:
            if isinstance(item, dict):
                name, options = next(iter(item.items()))
                options = {k: v for k, v in (options or {}).items() if v is not None}
            else:
                name, options = item, {}
            cls = ENRICHERS.get(name)
            assert cls is not None, f"지원하지 않는 enricher: {name}. 가능한 값: {list(ENRICHERS.keys())}"
            accepts = inspect.signature(cls.__init__).parameters
            if "genos_url" not in options and genos_url and "genos_url" in accepts:
                options["genos_url"] = genos_url
            result.append(cls(**options))
        return result

    def _build_chunker(self, chunker_cfg: dict | str):
        if isinstance(chunker_cfg, dict):
            name = chunker_cfg["name"]
            options = {k: v for k, v in chunker_cfg.items() if k != "name"}
        else:
            name, options = chunker_cfg, {}
        return CHUNKERS[name](**options)

    def _build_ext_loaders(self, format_options: dict, resource_path: str | None = None) -> dict:
        docling_loader = DoclingLoader(format_options, resource_path=resource_path, genos_url=self.config.get("genos_url", ""))
        return {ext: docling_loader for ext in format_options}

    @staticmethod
    def _normalize_output_format(value) -> str:
        fmt = str(value).strip().lower()
        if fmt not in {"json", "html", "markdown"}:
            _log.warning(f"[DocumentProcessor] Invalid output.format '{value}', fallback to 'json'")
            return "json"
        return fmt

    @staticmethod
    def _normalize_table_format(value) -> str:
        fmt = str(value).strip().lower()
        if fmt not in {"html", "markdown"}:
            _log.warning(f"[DocumentProcessor] Invalid output.table_format '{value}', fallback to 'html'")
            return "html"
        return fmt

    @staticmethod
    def _get_normalized_coords(bbox, page_w: float, page_h: float) -> list:
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

    @staticmethod
    def _item_to_html(item, element_id: int, doc: DoclingDocument) -> str:
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

    @staticmethod
    def _export_table_content(item: TableItem, doc: DoclingDocument, table_format: str = "html") -> str:
        try:
            if table_format == "markdown":
                text = item.export_to_markdown(doc=doc)
            else:
                text = item.export_to_html(doc=doc)
            if text and text.strip():
                return text
        except Exception:
            pass
        try:
            if item.data and item.data.table_cells:
                parts = [
                    str(cell.text).strip()
                    for cell in item.data.table_cells
                    if (getattr(cell, "text", "") or "").strip()
                ]
                if parts:
                    return " ".join(parts)
        except Exception:
            pass
        return getattr(item, "text", "") or ""

    @staticmethod
    def _docling_to_parse_format(doc: DoclingDocument, table_format: str = "html") -> dict:
        elements = []
        element_id = 0
        default_page_no = 1
        try:
            if getattr(doc, "pages", None):
                default_page_no = min(doc.pages.keys())
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
                    coordinates = DocumentProcessor._get_normalized_coords(
                        prov.bbox, page_info.size.width, page_info.size.height
                    )
                except Exception:
                    coordinates = []

            label_value = item.label.value if hasattr(item.label, "value") else str(item.label)
            if isinstance(item, TableItem):
                text = DocumentProcessor._export_table_content(item=item, doc=doc, table_format=table_format)
            else:
                text = getattr(item, "text", "") or ""

            elements.append(
                {
                    "category": label_value,
                    "content": text,
                    "coordinates": coordinates,
                    "id": element_id,
                    "page": page_no,
                }
            )
            element_id += 1

        return {"elements": elements, "usage": {"pages": doc.num_pages()}}

    @staticmethod
    def _serialize_docling_document(doc: DoclingDocument) -> dict:
        try:
            return doc.model_dump(mode="json")
        except Exception:
            try:
                return json.loads(doc.model_dump_json())
            except Exception:
                return doc.export_to_dict()

    @staticmethod
    def _replace_markdown_tables_with_html(doc: DoclingDocument, markdown_text: str) -> str:
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
                out = out[:idx] + html_table + out[idx + len(md_table) :]
            else:
                idx_raw = out.find(md_table_raw)
                if idx_raw >= 0:
                    out = out[:idx_raw] + html_table + out[idx_raw + len(md_table_raw) :]
        return out

    def _docling_to_content(self, doc: DoclingDocument) -> str:
        layers = {ContentLayer.BODY, ContentLayer.FURNITURE}
        if self._output_format == "html":
            return doc.export_to_html(included_content_layers=layers)
        if self._output_format == "markdown":
            markdown_text = doc.export_to_markdown(included_content_layers=layers)
            if self._table_format == "html":
                return self._replace_markdown_tables_with_html(doc, markdown_text)
            return markdown_text
        return ""

    @staticmethod
    def _normalize_response(result: dict) -> dict:
        result.setdefault("content", "")
        result.setdefault("elements", [])
        result.setdefault("usage", {"pages": 0})
        return result

    @staticmethod
    def _content_response(content: str, pages: int = 0) -> dict:
        return {"elements": [], "usage": {"pages": pages}, "content": content}

    def _build_docling_response(self, doc: DoclingDocument, clear_coordinates: bool = False) -> dict:
        if self._output_format == "json":
            result = self._docling_to_parse_format(doc, table_format=self._table_format)
            if clear_coordinates:
                for element in result.get("elements", []):
                    element["coordinates"] = []
            return result
        try:
            pages = max(1, int(doc.num_pages()))
        except Exception:
            pages = 0
        return self._content_response(self._docling_to_content(doc), pages=pages)

    @staticmethod
    def _audio_to_parse_format(text: str) -> dict:
        return {
            "elements": [{"category": "paragraph", "content": text, "coordinates": [], "id": 0, "page": 1}],
            "usage": {"pages": 1},
        }

    @staticmethod
    def _sheet_to_html(sheet: dict) -> str:
        data_rows = sheet.get("data_rows", [])
        if not data_rows:
            return "<table></table>"
        cols = list(data_rows[0].keys())
        header = "".join(f"<th>{c}</th>" for c in cols)
        rows_html = "".join("<tr>" + "".join(f"<td>{row.get(c, '')}</td>" for c in cols) + "</tr>" for row in data_rows)
        return f"<table><tr>{header}</tr>{rows_html}</table>"

    @staticmethod
    def _tabular_to_parse_format(data_dict: dict) -> dict:
        sheets = data_dict.get("data", [])
        elements = [
            {
                "category": "table",
                "content": DocumentProcessor._sheet_to_html(sheet),
                "coordinates": [],
                "id": idx,
                "page": idx + 1,
            }
            for idx, sheet in enumerate(sheets)
        ]
        return {"elements": elements, "usage": {"pages": len(sheets)}}

    @staticmethod
    def _langchain_to_parse_format(docs: list) -> dict:
        elements = []
        for idx, doc in enumerate(docs):
            page = doc.metadata.get("page", idx)
            if isinstance(page, int):
                page = page + 1
            elements.append(
                {"category": "paragraph", "content": doc.page_content, "coordinates": [], "id": idx, "page": page}
            )
        return {"elements": elements, "usage": {"pages": max((e["page"] for e in elements), default=0)}}

    def load_documents(self, file_path: str, **kwargs) -> Any:
        ext = Path(file_path).suffix.lstrip(".").lower()
        loader = self._ext_loaders.get(ext)
        assert loader is not None, (
            f"지원하지 않는 확장자: .{ext}. "
            f"format_options에 추가하세요. 현재 등록된 확장자: {list(self._ext_loaders.keys())}"
        )
        return loader.load(file_path)

    def split_documents(self, documents: DoclingDocument, **kwargs) -> list[DocChunk]:
        return list(self._chunker.chunk(documents, **kwargs))

    def postprocessing(self, chunks: list[DocChunk], documents: DoclingDocument, **kwargs) -> list[DocChunk]:
        # VT5 구현 내용
        return chunks

    async def compose_vectors(
        self, request: Request, file_path: str, document: DoclingDocument, chunks: List[DocChunk], **kwargs
    ) -> list[dict]:
        return await self.genos_meta_builder(document, chunks, file_path, request, **kwargs)

    async def __call__(self, request: Request, file_path: str, **kwargs) -> Any:

        return_level = kwargs.get("return_level", self.return_level)

        documents = self.load_documents(file_path, **kwargs)

        # 청킹 전 enrichment (TOC, 작성일 등 document 레벨)
        for enricher in self.enrichers:
            documents = await enricher.enrich(documents, **kwargs)
        if return_level == "document":
            return self._normalize_response(self._build_docling_response(documents))

        chunks = self.split_documents(documents, **kwargs)
        if return_level == "chunk":
            return chunks

        # 청킹 후 enrichment (테이블 정제, 이미지 설명 등 chunk 레벨)
        chunks = self.postprocessing(chunks, documents, **kwargs)

        vectors = await self.compose_vectors(request, file_path, documents, chunks, **kwargs)
        return vectors
