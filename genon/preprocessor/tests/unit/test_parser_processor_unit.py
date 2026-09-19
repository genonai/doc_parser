"""
Unit tests for facade/parser_processor.py.

Covers static/pure helpers and __call__ routing logic.
All external services and file I/O are mocked; no real documents required.
"""
import asyncio
import json
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from PIL import Image

from docling_core.types.doc import (
    BoundingBox,
    DescriptionAnnotation,
    DocItemLabel,
    PictureItem,
    ProvenanceItem,
)
from docling_core.types.doc.base import CoordOrigin
from langchain_core.documents import Document

from facade.parser_processor import DocumentProcessor, GenosServiceException
# 로더와 docling 런타임은 처리 본체(core)에 있다 — facade 는 얇은 서브클래스다(#363 08-1).
from processing.core.parser import GenericDocumentLoader, IntelligentDocumentProcessor
# check_sql_dtypes 는 공용 로더(processing/common/loaders.py)에 있다. parser 파이프라인은
# tabular 입력을 processing.converters.xlsx_processor 로 처리하므로 parser 쪽 사본은 없다.
from genon.preprocessor.processing.common.loaders import TabularLoaderBase
from docling.prompts.prompt_manager import LLMApiError


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def dp():
    """DocumentProcessor with __init__ bypassed and all sub-processors mocked."""
    proc = object.__new__(DocumentProcessor)
    proc._intel = MagicMock()
    proc._hwp = MagicMock()
    proc._docx = MagicMock()
    proc._generic = MagicMock()
    proc._whisper_url = ""
    proc._whisper_req_data = {}
    proc._whisper_chunk_sec = 29
    proc._log_level = 4  # __call__ 이 setup_logging 에서 참조 (정상 __init__ 우회 보강)
    # __init__ 이 self._intel._xlsx_cfg 를 alias 하는 속성(이슈 #288). 우회 시 tabular 기본값으로 보강.
    proc._xlsx_cfg = {"processing_mode": "tabular", "header_row": 0, "multi_table": False}
    # __call__ 의 tabular custom_fields 라우팅(#75bb9ec1)이 참조. 우회 시 빈 목록으로 보강.
    proc._tabular_custom_fields_mappers = []
    return proc


@pytest.fixture
def intel():
    """IntelligentDocumentProcessor with __init__ bypassed."""
    return object.__new__(IntelligentDocumentProcessor)


# ─── Helpers for _docling_to_parse_format tests ───────────────────────────────

def _make_prov(page_no=1):
    prov = MagicMock()
    prov.page_no = page_no
    prov.bbox = BoundingBox(l=10.0, t=20.0, r=90.0, b=80.0, coord_origin=CoordOrigin.TOPLEFT)
    return prov


def _make_text_item(text="hello", label="paragraph", page_no=1, has_prov=True, level=1):
    item = MagicMock()
    item.prov = [_make_prov(page_no)] if has_prov else None
    item.label = MagicMock()
    item.label.value = label
    item.text = text
    item.level = level  # needed when label == "section_header"
    return item


def _make_picture_item(page_no=1, annotations=None):
    return PictureItem(
        self_ref="#/pictures/0",
        parent=None,
        children=[],
        label=DocItemLabel.PICTURE,
        prov=[
            ProvenanceItem(
                page_no=page_no,
                bbox=BoundingBox(l=10, t=20, r=90, b=80, coord_origin=CoordOrigin.TOPLEFT),
                charspan=(0, 0),
            )
        ],
        annotations=annotations or [],
    )


def _make_mock_doc(items, num_pages=1):
    doc = MagicMock()
    doc.num_pages.return_value = num_pages
    doc.iterate_items.return_value = [(item, None) for item in items]
    page_size = MagicMock()
    page_size.width = 595.0
    page_size.height = 842.0
    doc.pages = {1: MagicMock(size=page_size)}
    return doc


# ─── _get_normalized_coords ───────────────────────────────────────────────────

@pytest.mark.unit
class TestGetNormalizedCoords:
    def test_topleft_origin_produces_four_corners_clockwise(self):
        bbox = BoundingBox(l=10, t=20, r=90, b=80, coord_origin=CoordOrigin.TOPLEFT)
        result = DocumentProcessor._get_normalized_coords(bbox, page_w=100.0, page_h=100.0)

        assert result == [
            {"x": 0.1, "y": 0.2},   # top-left
            {"x": 0.9, "y": 0.2},   # top-right
            {"x": 0.9, "y": 0.8},   # bottom-right
            {"x": 0.1, "y": 0.8},   # bottom-left
        ]

    def test_full_page_bbox_spans_0_to_1(self):
        bbox = BoundingBox(l=0, t=0, r=100, b=100, coord_origin=CoordOrigin.TOPLEFT)
        result = DocumentProcessor._get_normalized_coords(bbox, page_w=100.0, page_h=100.0)

        assert {p["x"] for p in result} == {0.0, 1.0}
        assert {p["y"] for p in result} == {0.0, 1.0}


# ─── _audio_to_parse_format ───────────────────────────────────────────────────

@pytest.mark.unit
def test_audio_to_parse_format_builds_one_paragraph_element():
    """전사 텍스트 1건이 문단 element 하나와 usage.pages=1 로 나온다."""
    result = DocumentProcessor._audio_to_parse_format("transcribed audio text")

    assert result == {
        "elements": [{
            "category": "paragraph",
            "content": "transcribed audio text",
            "coordinates": [],
            "id": 0,
            "page": 1,
        }],
        "usage": {"pages": 1},
    }


# ─── _tabular_to_parse_format ─────────────────────────────────────────────────

@pytest.mark.unit
class TestTabularToParseFormat:
    def _make_data_dict(self, n=1):
        return {
            "data": [
                {"sheet_name": f"Sheet{i+1}", "data_rows": [{"a": i}], "data_types": []}
                for i in range(n)
            ]
        }

    def test_empty_data_returns_empty_elements(self):
        result = DocumentProcessor._tabular_to_parse_format({"data": []})
        assert result["elements"] == []
        assert result["usage"]["pages"] == 0

    def test_each_sheet_row_becomes_one_element_on_its_own_page(self):
        """시트 3장이면 element 3건, 페이지는 시트 순서대로, usage.pages 는 시트 수다."""
        result = DocumentProcessor._tabular_to_parse_format(self._make_data_dict(3))

        assert len(result["elements"]) == 3
        assert [e["category"] for e in result["elements"]] == ["tabular_row"] * 3
        assert [e["page"] for e in result["elements"]] == [1, 2, 3]
        assert result["usage"]["pages"] == 3
        assert set(result["elements"][0]) >= {
            "category", "content", "coordinates", "id", "page", "metadata"}

    def test_each_data_row_produces_one_element(self):
        data = {
            "data": [{
                "sheet_name": "Sheet1",
                "sheet_index": 1,
                "data_rows": [{"name": "Alice"}, {"name": "Bob"}],
            }]
        }
        result = DocumentProcessor._tabular_to_parse_format(data)
        assert len(result["elements"]) == 2
        assert [e["category"] for e in result["elements"]] == ["tabular_row", "tabular_row"]
        assert [e["metadata"]["name"] for e in result["elements"]] == ["Alice", "Bob"]


# ─── _langchain_to_parse_format ───────────────────────────────────────────────

@pytest.mark.unit
class TestLangchainToParseFormat:
    @pytest.mark.parametrize("metadata,expected_page", [
        ({"page": 0}, 1),      # 0-based -> 1-based
        ({"page": 2}, 3),
        ({}, 1),               # page 누락 시 인덱스 + 1
    ])
    def test_page_is_converted_to_1_based(self, metadata, expected_page):
        docs = [Document(page_content="text", metadata=metadata)]
        element = DocumentProcessor._langchain_to_parse_format(docs)["elements"][0]
        assert element["page"] == expected_page

    def test_element_shape_and_usage_pages(self):
        """element 는 문단 카테고리의 고정 5키이고, usage.pages 는 변환된 페이지의 최대값이다."""
        docs = [
            Document(page_content="a", metadata={"page": 0}),
            Document(page_content="b", metadata={"page": 4}),
        ]
        result = DocumentProcessor._langchain_to_parse_format(docs)

        assert result["elements"][0] == {
            "category": "paragraph",
            "content": "a",
            "coordinates": [],
            "id": 0,
            "page": 1,
        }
        assert result["usage"]["pages"] == 5


# ─── _docling_to_parse_format ─────────────────────────────────────────────────

@pytest.mark.unit
class TestDoclingToParseFormat:
    def test_element_shape_is_sequential_id_with_four_point_coords(self):
        """element 는 고정 키를 갖고, id 는 0부터 순번, coordinates 는 네 점이다."""
        doc = _make_mock_doc([_make_text_item() for _ in range(3)])
        result = DocumentProcessor._docling_to_parse_format(doc)

        assert [e["id"] for e in result["elements"]] == [0, 1, 2]
        first = result["elements"][0]
        assert set(first) >= {"category", "content", "coordinates", "id", "page"}
        assert isinstance(first["coordinates"], list) and len(first["coordinates"]) == 4

    def test_item_without_prov_has_empty_coordinates(self):
        items = [_make_text_item(text="keep", has_prov=True), _make_text_item(text="no-prov", has_prov=False)]
        doc = _make_mock_doc(items)
        result = DocumentProcessor._docling_to_parse_format(doc)
        assert len(result["elements"]) == 2
        no_prov = next(e for e in result["elements"] if e["content"] == "no-prov")
        assert no_prov["coordinates"] == []

    def test_usage_pages_comes_from_doc_num_pages(self):
        doc = _make_mock_doc([], num_pages=5)
        assert DocumentProcessor._docling_to_parse_format(doc)["usage"]["pages"] == 5

    def test_category_uses_label_value_directly(self):
        doc = _make_mock_doc([_make_text_item(label="section_header")])
        result = DocumentProcessor._docling_to_parse_format(doc)
        assert result["elements"][0]["category"] == "section_header"

    def test_picture_annotation_is_mapped_to_content(self):
        picture = _make_picture_item(
            annotations=[
                DescriptionAnnotation(
                    text="문맥 기반 이미지 설명",
                    provenance="facade_image_description",
                )
            ]
        )
        doc = _make_mock_doc([picture])
        element = DocumentProcessor._docling_to_parse_format(doc)["elements"][0]
        assert element["category"] == "picture"
        assert element["content"] == "문맥 기반 이미지 설명"


# ─── DocumentProcessor.__call__ routing ──────────────────────────────────────

_MOCK_RESULT = {"elements": [], "usage": {"pages": 1}}


@pytest.mark.unit
@pytest.mark.parametrize("filename,expected_method", [
    ("a.wav",  "_parse_audio"),
    ("a.mp3",  "_parse_audio"),
    ("a.m4a",  "_parse_audio"),
    ("a.csv",  "_parse_tabular"),
    ("a.xlsx", "_parse_tabular"),
    ("a.hwp",  "_parse_hwp_hwpx"),
    ("a.hwpx", "_parse_hwp_hwpx"),
    ("a.docx", "_parse_docx"),
    ("a.pdf",  "_parse_docling"),
    ("a.txt",  "_parse_other"),
    ("a.pptx", "_parse_other"),
])
def test_call_routes_to_correct_method(dp, filename, expected_method):
    """__call__ dispatches each file extension to the right internal method."""
    parse_mocks = {
        "_parse_audio":    MagicMock(return_value="transcript"),
        "_parse_tabular":  MagicMock(return_value={"data": []}),
        "_parse_hwp_hwpx": MagicMock(return_value=MagicMock()),
        "_parse_docx":     MagicMock(return_value=MagicMock()),
        "_parse_docling":  MagicMock(return_value=MagicMock()),
        "_parse_other":    MagicMock(return_value=[]),
    }
    for name, mock in parse_mocks.items():
        setattr(dp, name, mock)

    with patch.object(DocumentProcessor, "_docling_to_parse_format",   return_value=_MOCK_RESULT), \
         patch.object(DocumentProcessor, "_audio_to_parse_format",     return_value=_MOCK_RESULT), \
         patch.object(DocumentProcessor, "_tabular_to_parse_format",   return_value=_MOCK_RESULT), \
         patch.object(DocumentProcessor, "_langchain_to_parse_format", return_value=_MOCK_RESULT):
        result = asyncio.run(dp(None, filename))

    parse_mocks[expected_method].assert_called_once()
    for name, mock in parse_mocks.items():
        if name != expected_method:
            mock.assert_not_called()
    assert "elements" in result
    assert "usage" in result


@pytest.mark.unit
def test_docx_coordinates_cleared_after_parsing(dp):
    """For .docx files, element coordinates are reset to [] after parsing."""
    elements_with_coords = [
        {"category": "paragraph", "content": "text",
         "coordinates": [{"x": 0.1, "y": 0.2}], "id": 0, "page": 1}
    ]
    dp._parse_docx = MagicMock(return_value=MagicMock())
    with patch.object(DocumentProcessor, "_docling_to_parse_format",
                      return_value={"elements": elements_with_coords, "usage": {"pages": 1}}):
        result = asyncio.run(dp(None, "doc.docx"))

    for element in result["elements"]:
        assert element["coordinates"] == []


# ─── IntelligentDocumentProcessor.check_glyph_text ───────────────────────────

@pytest.mark.unit
@pytest.mark.parametrize("text,kwargs,expected", [
    ("GLYPH123", {}, True),                     # 기본 임계값 1 에서 1건이면 검출
    ("GLYPHXYZ", {}, True),                     # 접미 변형도 검출
    ("GLYPH123", {"threshold": 2}, False),      # 임계값에 못 미치면 미검출
    ("GLYPH123 GLYPHABC", {"threshold": 2}, True),
    ("일반 텍스트 내용", {}, False),
    ("", {}, False),
    (None, {}, False),
])
def test_check_glyph_text(intel, text, kwargs, expected):
    assert intel.check_glyph_text(text, **kwargs) is expected


@pytest.mark.unit
class TestEnrichImageDescriptions:
    def _make_intel(self, enabled=True):
        intel = object.__new__(IntelligentDocumentProcessor)
        intel.image_description_enabled = enabled
        intel.image_description_api_url = "http://example.com/v1/chat/completions"
        intel.image_description_api_key = "secret"
        intel.image_description_model = "mock-model"
        intel.image_description_timeout = 10.0
        intel.image_description_concurrency = 1
        intel.image_description_before_items = 2
        intel.image_description_after_items = 2
        intel.image_description_max_context_chars = 1500
        intel.image_description_include_caption = False
        intel.image_description_include_section_header = False
        intel.image_description_same_page_first = True
        intel.image_description_headers = {}
        intel.image_description_params = {}
        intel.image_description_provenance = "facade_image_description"
        intel.image_description_prompt_template = (
            "[앞 문맥]\\n{before_context}\\n[캡션]\\n{caption}\\n[뒤 문맥]\\n{after_context}"
        )
        return intel

    def test_disabled_option_skips_image_description_api_call(self):
        intel = self._make_intel(enabled=False)
        doc = MagicMock()
        doc.iterate_items.return_value = []

        with patch("genon.preprocessor.processing.enrichment.image_request.api_image_request") as mock_api:
            result = intel.enrich_image_descriptions(doc)

        assert result is doc
        mock_api.assert_not_called()

    def test_enrichment_adds_description_annotation_with_context(self):
        intel = self._make_intel(enabled=True)
        before_item = _make_text_item(text="앞 문단 텍스트", page_no=1)
        picture_item = _make_picture_item(page_no=1)
        after_item = _make_text_item(text="뒤 문단 텍스트", page_no=1)
        doc = _make_mock_doc([before_item, picture_item, after_item])

        with patch.object(
            PictureItem,
            "get_image",
            return_value=Image.new("RGB", (8, 8), color="white"),
        ), patch(
            "genon.preprocessor.processing.enrichment.image_request.api_image_request",
            return_value="문맥 기반 설명 결과",
        ) as mock_api:
            result = intel.enrich_image_descriptions(doc)

        assert result is doc
        descriptions = [
            ann
            for ann in picture_item.annotations
            if isinstance(ann, DescriptionAnnotation)
        ]
        assert descriptions
        assert descriptions[0].text == "문맥 기반 설명 결과"
        assert descriptions[0].provenance == "facade_image_description"

        prompt = mock_api.call_args.kwargs["prompt"]
        assert "앞 문단 텍스트" in prompt
        assert "뒤 문단 텍스트" in prompt

    def test_enrichment_vlm_unreachable_is_ignored(self):
        intel = self._make_intel(enabled=True)
        before_item = _make_text_item(text="앞 문단 텍스트", page_no=1)
        picture_item = _make_picture_item(page_no=1)
        after_item = _make_text_item(text="뒤 문단 텍스트", page_no=1)
        doc = _make_mock_doc([before_item, picture_item, after_item])

        with patch.object(
            PictureItem,
            "get_image",
            return_value=Image.new("RGB", (8, 8), color="white"),
        ), patch(
            "genon.preprocessor.processing.enrichment.image_request.api_image_request",
            side_effect=RuntimeError("VLM endpoint is unreachable"),
        ):
            result = intel.enrich_image_descriptions(doc)

        assert result is doc
        descriptions = [
            ann
            for ann in picture_item.annotations
            if isinstance(ann, DescriptionAnnotation)
        ]
        assert descriptions == []


@pytest.mark.unit
def test_enrichment_provider_error_is_rethrown_as_genos_exception(intel):
    raw_error = """{"object":"error","message":"This model's maximum context length is 16384 tokens.","type":"BadRequestError","param":null,"code":400}"""
    intel.enrichment_options = MagicMock()
    dummy_doc = MagicMock()

    with patch(
        "processing.core.parser.enrich_document",
        side_effect=LLMApiError(raw_error, status_code=400),
    ):
        with pytest.raises(GenosServiceException) as exc_info:
            intel.enrichment(dummy_doc)

    assert exc_info.value.error_msg == raw_error


# ─── _normalize_output_format / _normalize_table_format ─────────────────────

@pytest.mark.unit
@pytest.mark.parametrize("raw,expected", [
    ("json", "json"), ("html", "html"), ("markdown", "markdown"),
    ("JSON", "json"), ("HTML", "html"), ("Markdown", "markdown"),   # 대소문자 정규화
    ("  json  ", "json"),                                           # 앞뒤 공백 제거
    ("xml", "json"), ("", "json"),                                  # 미지원 값은 json 폴백
])
def test_normalize_output_format(raw, expected):
    assert DocumentProcessor._normalize_output_format(raw) == expected


@pytest.mark.unit
@pytest.mark.parametrize("raw,expected", [
    ("html", "html"), ("markdown", "markdown"),
    ("HTML", "html"), ("MARKDOWN", "markdown"),                     # 대소문자 정규화
    ("  html  ", "html"),                                           # 앞뒤 공백 제거
    ("text", "html"), ("", "html"),                                 # 미지원 값은 html 폴백
])
def test_normalize_table_format(raw, expected):
    assert DocumentProcessor._normalize_table_format(raw) == expected


# ─── _export_table_content ───────────────────────────────────────────────────

def _make_table_item(export_html="<table></table>", export_markdown="| a |", text="fallback"):
    item = MagicMock()
    item.export_to_html.return_value = export_html
    item.export_to_markdown.return_value = export_markdown
    item.text = text
    item.data = MagicMock()
    item.data.table_cells = []
    return item


def _failing_table_item(text=""):
    """export_to_html 가 예외를 던지고 셀도 비어 있는 표 item."""
    item = MagicMock()
    item.export_to_html.side_effect = RuntimeError("export failed")
    item.data.table_cells = []
    item.text = text
    return item


def _with_cells(item, *texts):
    item.data.table_cells = [MagicMock(text=t) for t in texts]
    return item


@pytest.mark.unit
class TestExportTableContent:
    @pytest.mark.parametrize("kwargs", [{}, {"table_format": "html"}], ids=["default", "explicit"])
    def test_html_is_the_default_and_calls_export_to_html(self, kwargs):
        item = _make_table_item()
        doc = MagicMock()
        result = DocumentProcessor._export_table_content(item, doc, **kwargs)
        item.export_to_html.assert_called_once_with(doc=doc)
        assert result == "<table></table>"

    @pytest.mark.parametrize("kwargs,expected_compact", [
        ({"compact_tables": False}, False),
        ({}, True),                                  # compact_tables 기본값
    ], ids=["explicit-false", "default-true"])
    def test_markdown_format_uses_shared_export_markdown(self, kwargs, expected_compact):
        # 표 markdown 은 공용 관문을 거친다 - 링크 URL 억제가 여기 한 벌로 걸린다.
        item = _make_table_item()
        doc = MagicMock()
        with patch("genon.preprocessor.processing.serialize.parse_format.export_markdown",
                   return_value="| a |") as em:
            result = DocumentProcessor._export_table_content(
                item, doc, table_format="markdown", **kwargs
            )
        em.assert_called_once_with(doc, item=item, compact_tables=expected_compact)
        item.export_to_markdown.assert_not_called()
        assert result == "| a |"

    @pytest.mark.parametrize("build_item,expected", [
        (lambda: _with_cells(_make_table_item(export_html="   "), "cell value"), "cell value"),
        (lambda: _with_cells(_failing_table_item(), "rescued"), "rescued"),
        (lambda: _failing_table_item(text="last resort"), "last resort"),
    ], ids=["empty-export", "export-raises", "no-cells-left"])
    def test_falls_back_when_export_yields_nothing(self, build_item, expected):
        """export 가 비었거나 예외면 셀 텍스트로, 셀도 없으면 item.text 로 떨어진다."""
        result = DocumentProcessor._export_table_content(
            build_item(), MagicMock(), table_format="html"
        )
        assert result == expected


# ─── _docling_to_content ─────────────────────────────────────────────────────

def _make_proc_with_format(output_format: str, table_format: str):
    proc = object.__new__(DocumentProcessor)
    proc._output_format = output_format
    proc._table_format = table_format
    return proc


@pytest.mark.unit
class TestDoclingToContent:
    def test_html_format_calls_export_to_html(self):
        proc = _make_proc_with_format("html", "html")
        doc = MagicMock()
        doc.export_to_html.return_value = "<html>content</html>"
        result = proc._docling_to_content(doc)
        doc.export_to_html.assert_called_once()
        assert result == "<html>content</html>"

    def test_markdown_format_with_markdown_table_uses_shared_export_markdown(self):
        proc = _make_proc_with_format("markdown", "markdown")
        doc = MagicMock()
        with patch("genon.preprocessor.processing.serialize.parse_format.export_markdown",
                   return_value="# heading\n| a | b |") as em:
            result = proc._docling_to_content(doc)
        em.assert_called_once()
        doc.export_to_markdown.assert_not_called()
        assert result == "# heading\n| a | b |"

    def test_markdown_format_with_html_table_uses_replace(self):
        proc = _make_proc_with_format("markdown", "html")
        doc = MagicMock()
        doc.iterate_items.return_value = []
        with patch("genon.preprocessor.processing.serialize.parse_format.export_markdown",
                   return_value="# heading\n| a | b |") as em:
            result = proc._docling_to_content(doc)
        em.assert_called_once()
        assert isinstance(result, str)

    def test_json_format_returns_empty_string(self):
        proc = _make_proc_with_format("json", "html")
        doc = MagicMock()
        result = proc._docling_to_content(doc)
        assert result == ""
        doc.export_to_html.assert_not_called()
        doc.export_to_markdown.assert_not_called()


# ─── _build_docling_response ─────────────────────────────────────────────────

def _make_parse_format_result():
    return {
        "elements": [
            {"category": "paragraph", "content": "text",
             "coordinates": [{"x": 0.1, "y": 0.2}], "id": 0, "page": 1}
        ],
        "usage": {"pages": 1},
    }


@pytest.mark.unit
class TestBuildDoclingResponse:
    def test_json_format_returns_elements_structure(self):
        proc = _make_proc_with_format("json", "html")
        doc = MagicMock()
        with patch.object(DocumentProcessor, "_docling_to_parse_format",
                          side_effect=lambda *a, **kw: _make_parse_format_result()):
            result = proc._build_docling_response(doc)
        assert "elements" in result
        assert "usage" in result
        assert "content" not in result

    @pytest.mark.parametrize("output_format,table_format,content", [
        ("html", "html", "<html/>"),
        ("markdown", "markdown", "# title"),
    ])
    def test_content_formats_return_content_structure(self, output_format, table_format, content):
        """html/markdown 출력은 content 에 본문을 싣고 elements 는 비운다."""
        proc = _make_proc_with_format(output_format, table_format)
        doc = MagicMock()
        doc.num_pages.return_value = 2
        with patch.object(DocumentProcessor, "_docling_to_content", return_value=content):
            result = proc._build_docling_response(doc)
        assert result["content"] == content
        assert result["elements"] == []
        assert result["usage"]["pages"] == 2

    @pytest.mark.parametrize("clear_coordinates", [True, False])
    def test_json_format_clear_coordinates_flag(self, clear_coordinates):
        proc = _make_proc_with_format("json", "html")
        doc = MagicMock()
        with patch.object(DocumentProcessor, "_docling_to_parse_format",
                          side_effect=lambda *a, **kw: _make_parse_format_result()):
            result = proc._build_docling_response(doc, clear_coordinates=clear_coordinates)
        if clear_coordinates:
            assert all(e["coordinates"] == [] for e in result["elements"])
        else:
            assert result["elements"][0]["coordinates"] != []


# ─── TabularLoaderBase.check_sql_dtypes (공용 로더) ───────────────────────────

@pytest.mark.unit
@pytest.mark.parametrize("column,values,matches", [
    ("n", [1, 2, 3], lambda t: "INT" in t),
    ("f", [1.1, 2.2, 3.3], lambda t: t == "FLOAT"),
    ("s", ["hello", "world"], lambda t: "VARCHAR" in t),
], ids=["int", "float", "string"])
def test_check_sql_dtypes_pairs_each_column_with_a_sql_type(column, values, matches):
    loader = object.__new__(TabularLoaderBase)
    _, dtypes = loader.check_sql_dtypes(pd.DataFrame({column: values}))

    assert len(dtypes) == 1
    assert len(dtypes[0]) == 2  # [col_name, sql_type]
    assert dtypes[0][0] == column
    assert matches(dtypes[0][1])


# ─── GenericDocumentLoader.get_real_file_type ─────────────────────────────────

@pytest.mark.unit
@pytest.mark.parametrize("filename,content,expected", [
    ("fake.txt", b"%PDF-1.4 fake content", "pdf"),
    ("fake.txt", b"\x89PNG\r\n\x1a\n fake png", "png"),
    ("fake.txt", b"\xff\xd8\xff fake jpeg", "jpg"),
    ("test.docx", b"PK\x03\x04 zip content", ".docx"),   # 매직바이트로 못 가리면 확장자
], ids=["pdf", "png", "jpg", "unknown-magic"])
def test_get_real_file_type(tmp_path, filename, content, expected):
    loader = object.__new__(GenericDocumentLoader)
    f = tmp_path / filename
    f.write_bytes(content)
    assert loader.get_real_file_type(str(f)) == expected


# ─── IntelligentDocumentProcessor._build_ocr_options (yaml → ocr_options) ────

@pytest.mark.unit
class TestBuildOcrOptions:
    """yaml 의 ocr.engine 키에 따라 PaddleOcrOptions / UpstageOcrOptions 가 선택되는지 검증."""

    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch):
        monkeypatch.delenv("UPSTAGE_API_KEY", raising=False)

    @pytest.mark.parametrize("ocr_cfg", [
        {},                     # engine 미지정
        {"engine": "paddle"},
        {"engine": "bogus"},    # 알 수 없는 engine 은 paddle 로 폴백
    ], ids=["default", "explicit", "unknown-falls-back"])
    def test_paddle_is_the_default_engine(self, ocr_cfg):
        from docling.datamodel.pipeline_options import PaddleOcrOptions
        opts = IntelligentDocumentProcessor._build_ocr_options(
            ocr_cfg, paddle_endpoint="http://paddle.example/ocr"
        )
        assert isinstance(opts, PaddleOcrOptions)
        assert opts.ocr_endpoint == "http://paddle.example/ocr"

    @pytest.mark.parametrize("engine", ["upstage", "UPSTAGE"], ids=["lower", "upper"])
    def test_upstage_engine_uses_yaml_values(self, engine):
        """engine 판정은 대소문자를 가리지 않고, upstage 하위 키가 그대로 옵션에 실린다."""
        from docling.datamodel.pipeline_options import UpstageOcrOptions
        opts = IntelligentDocumentProcessor._build_ocr_options(
            {
                "engine": engine,
                "upstage": {
                    "api_endpoint": "https://custom.upstage.example/ocr",
                    "api_key": "yaml-key",
                    "model": "ocr",
                    "timeout": 30,
                    "text_score": 0.6,
                    "lang": ["ko"],
                },
            },
            paddle_endpoint="",
        )
        assert isinstance(opts, UpstageOcrOptions)
        assert opts.api_endpoint == "https://custom.upstage.example/ocr"
        assert opts.api_key == "yaml-key"
        assert opts.model == "ocr"
        assert opts.timeout == 30
        assert opts.text_score == 0.6
        assert opts.lang == ["ko"]

    @pytest.mark.parametrize("yaml_key,expected", [
        ("", "env-secret"),               # yaml 이 비면 환경변수로 폴백
        ("yaml-secret", "yaml-secret"),   # yaml 값이 환경변수보다 우선
    ], ids=["env-fallback", "yaml-wins"])
    def test_upstage_api_key_resolution(self, monkeypatch, yaml_key, expected):
        monkeypatch.setenv("UPSTAGE_API_KEY", "env-secret")
        opts = IntelligentDocumentProcessor._build_ocr_options(
            {"engine": "upstage", "upstage": {"api_key": yaml_key}},
            paddle_endpoint="",
        )
        assert opts.api_key == expected

    # ── yaml 의 잘못된 값으로 startup 이 깨지지 않는지 (#178 CodeRabbit) ─────

    @pytest.mark.parametrize("upstage_cfg,expected_timeout,expected_text_score", [
        ({"timeout": "not-a-number"}, 60, 0.5),
        ({"timeout": ""}, 60, 0.5),
        ({"timeout": 0}, 60, 0.5),        # 0 / 음수 timeout 도 의미 없으므로 default 로 복구
        ({"text_score": "bad"}, 60, 0.5),
        ({"timeout": "120", "text_score": "0.7"}, 120, 0.7),  # numeric-string 은 정상 변환
    ], ids=["timeout-nan", "timeout-empty", "timeout-zero", "score-nan", "numeric-string"])
    def test_upstage_invalid_numbers_fall_back_to_defaults(
        self, upstage_cfg, expected_timeout, expected_text_score
    ):
        from docling.datamodel.pipeline_options import UpstageOcrOptions
        opts = IntelligentDocumentProcessor._build_ocr_options(
            {"engine": "upstage", "upstage": {"api_key": "k", **upstage_cfg}},
            paddle_endpoint="",
        )
        assert isinstance(opts, UpstageOcrOptions)
        assert opts.timeout == expected_timeout
        assert opts.text_score == expected_text_score


# ─── _apply_llm_fields_document_scope — 문서 1건당 LLM 1회 ────────────────────
#
# json_semantic(llm_fields_scope="document")은 섹션(청크)마다 LLM 을 부르면 카드 1장에
# 10회 넘게 호출된다 — enricher 실제 호출은 이 테스트가 검증하려는 대상이 아니라(엔드포인트
# 테스트 스크립트가 실제 호출을 담당한다), "호출 횟수를 섹션 수와 무관하게 1회로 제어하는
# 로직" 자체를 검증하는 것이 목적이라 스텁을 쓴다.

from processing.enrichment.custom_fields_enricher import LlmFieldSpec  # noqa: E402


class _CountingStubEnricher:
    """is_configured/extract_fields_from_text 만 흉내 낸 최소 스텁 — 실제 LLM 호출 없음."""

    def __init__(self):
        self.is_configured = True
        self.calls = 0

    async def extract_fields_from_text(self, text: str) -> dict:
        self.calls += 1
        return {"SALE_STATUS": "판매중"}


class _StubDocumentScopeMapper:
    """json_semantic 매퍼의 document 스코프 계약(llm_field_specs/document_input_fields)만 흉내."""

    def __init__(self, llm_field_specs):
        self.llm_field_specs = llm_field_specs
        self.resource_path = None

    def document_input_fields(
        self, fields_list: list, input_field_names: list | None = None
    ) -> dict:
        # 파서가 spec.input_fields 를 함께 넘긴다(섹션 이름 입력 지원).
        self.received_input_field_names = input_field_names
        return {"PRODUCT_INFO": "\n\n".join(f.get("SECTION_NM", "") for f in fields_list)}


def _make_llm_field_spec() -> LlmFieldSpec:
    return LlmFieldSpec({
        "output_fields": ["SALE_STATUS"],
        "input_fields": ["PRODUCT_INFO"],
        "url": "http://llm.invalid/v1/chat/completions",
        "model": "model",
    })


def test_apply_llm_fields_document_scope_calls_enricher_once_regardless_of_section_count(dp):
    """섹션이 5건이어도 spec 당 enricher 호출은 1회뿐이고, 결과는 전 섹션에 복사된다."""
    mapper = _StubDocumentScopeMapper(llm_field_specs=[_make_llm_field_spec()])
    stub_enricher = _CountingStubEnricher()
    dp._llm_field_enricher = MagicMock(return_value=stub_enricher)

    fields_list = [{"SECTION_NM": f"섹션{i}"} for i in range(5)]
    result = asyncio.run(dp._apply_llm_fields_document_scope(mapper, fields_list))

    assert stub_enricher.calls == 1
    assert len(result) == 5
    assert all(fields["SALE_STATUS"] == "판매중" for fields in result)


def test_apply_llm_fields_document_scope_fills_null_when_enricher_not_configured(dp):
    """url/model 미설정(is_configured=False)이면 호출 없이 null 로 채우고 나머지는 계속 진행한다."""
    mapper = _StubDocumentScopeMapper(llm_field_specs=[_make_llm_field_spec()])
    stub_enricher = _CountingStubEnricher()
    stub_enricher.is_configured = False
    dp._llm_field_enricher = MagicMock(return_value=stub_enricher)

    fields_list = [{"SECTION_NM": "섹션0"}, {"SECTION_NM": "섹션1"}]
    result = asyncio.run(dp._apply_llm_fields_document_scope(mapper, fields_list))

    assert stub_enricher.calls == 0
    assert all(fields["SALE_STATUS"] is None for fields in result)


def test_apply_llm_fields_document_scope_routes_from_apply_llm_fields(dp):
    """llm_fields_scope='document' 인 매퍼는 _apply_llm_fields 가 document 스코프로 위임한다."""
    mapper = _StubDocumentScopeMapper(llm_field_specs=[_make_llm_field_spec()])
    mapper.llm_fields_scope = "document"
    stub_enricher = _CountingStubEnricher()
    dp._llm_field_enricher = MagicMock(return_value=stub_enricher)

    fields_list = [{"SECTION_NM": f"섹션{i}"} for i in range(3)]
    result = asyncio.run(dp._apply_llm_fields(mapper, fields_list))

    assert stub_enricher.calls == 1
    assert len(result) == 3


# ─── pack 재적용 — llm_fields 산출도 JSON 한 칸에 담긴다 ──────────────────────
#
# 묶기는 "파이프라인 맨 뒤" 라는 계약인데 매퍼의 build_fields 는 llm_fields 보다 먼저
# 끝난다. 재적용하지 않으면 요약 같은 LLM 산출이 JSON 안에서 영구히 null 로 남고, 문서형
# (extractor: llm)에서는 LLM 응답이 파이프라인 입력이라 되던 것이 레코드형에서만 조용히
# 안 되는 비대칭이 된다.


class _StubRecordScopeMapper:
    """레코드 스코프 계약(llm_field_specs)만 흉내 낸 최소 스텁."""

    def __init__(self, llm_field_specs, pack=None):
        self.llm_field_specs = llm_field_specs
        self.resource_path = None
        if pack is not None:
            self.pack = pack


def test_pack_is_reapplied_after_document_scope_llm_fields(dp):
    mapper = _StubDocumentScopeMapper(llm_field_specs=[_make_llm_field_spec()])
    mapper.llm_fields_scope = "document"
    mapper.pack = {"DETAIL_JSON": ["SECTION_NM", "SALE_STATUS"]}
    dp._llm_field_enricher = MagicMock(return_value=_CountingStubEnricher())

    result = asyncio.run(dp._apply_llm_fields(
        mapper, [{"SECTION_NM": "섹션0", "DETAIL_JSON": '{"SECTION_NM": "섹션0", "SALE_STATUS": null}'}]
    ))

    assert json.loads(result[0]["DETAIL_JSON"]) == {
        "SECTION_NM": "섹션0", "SALE_STATUS": "판매중",
    }


def test_pack_is_reapplied_after_record_scope_llm_fields(dp):
    mapper = _StubRecordScopeMapper(
        llm_field_specs=[_make_llm_field_spec()],
        pack={"DETAIL_JSON": ["PRODUCT_INFO", "SALE_STATUS"]},
    )
    dp._llm_field_enricher = MagicMock(return_value=_CountingStubEnricher())

    result = asyncio.run(dp._apply_llm_fields(
        mapper, [{"PRODUCT_INFO": "상품A"}, {"PRODUCT_INFO": "상품B"}]
    ))

    assert [json.loads(r["DETAIL_JSON"]) for r in result] == [
        {"PRODUCT_INFO": "상품A", "SALE_STATUS": "판매중"},
        {"PRODUCT_INFO": "상품B", "SALE_STATUS": "판매중"},
    ]


def test_pack_absent_mapper_is_untouched(dp):
    """pack 속성이 없는 매퍼(object.__new__ 로 만든 인스턴스 포함)에서도 견딘다."""
    mapper = _StubRecordScopeMapper(llm_field_specs=[_make_llm_field_spec()])
    dp._llm_field_enricher = MagicMock(return_value=_CountingStubEnricher())

    result = asyncio.run(dp._apply_llm_fields(mapper, [{"PRODUCT_INFO": "상품A"}]))

    assert result[0]["SALE_STATUS"] == "판매중"
    assert "DETAIL_JSON" not in result[0]
