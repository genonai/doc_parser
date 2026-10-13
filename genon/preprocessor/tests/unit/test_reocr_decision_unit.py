"""#482 — `ocr_mode: auto` 의 두 번째 변환(재OCR) 판정.

결함: 판정 LLM 이 없거나 응답이 없으면 `check_document` 가 거짓이 되고 호출부가
`not check_document(...)` 로 읽어 모든 PDF 를 OCR converter 로 두 번 변환했다.
수정: 판정값을 OcrVerdict(ok / needs_ocr / unknown)로 두고, 재OCR 사유(reocr_reason)는
needs_ocr·글리프·빈 페이지뿐이다. 외부 호출 없음 — 판정 LLM 은 PromptManager.call_ai_model 을 패치한다.
"""
import sys
from unittest.mock import MagicMock, patch

import pytest
from docling_core.types.doc import BoundingBox, DocItemLabel, DoclingDocument, ProvenanceItem, Size

from docling.datamodel.pipeline_options import DataEnrichmentOptions
from docling.prompts.prompt_manager import PromptManager
from docling.utils import document_enrichment as de
from facade.convert_processor import DocumentProcessor as ConvertProcessor
from facade.intelligent_processor import DocumentProcessor as IntelligentProcessor
from facade.parser_processor import DocumentProcessor
from genon.preprocessor.processing.common import docling_ops as dops

# 파사드가 상속하는 core 모듈 — sys.path 에 저장소 루트와 genon/preprocessor 가 둘 다 있어 같은 파일이
# 두 이름(processing.core.parser / genon.preprocessor.processing.core.parser)으로 import 될 수 있으므로,
# 패치는 파사드가 실제로 쓰는 모듈 객체에 건다.
core_parser = sys.modules[DocumentProcessor.__mro__[1].__module__]


def _doc_with_text() -> DoclingDocument:
    d = DoclingDocument(name="t")
    d.add_page(page_no=1, size=Size(width=600, height=800))
    txt = "제1조 목적 이 약관은 보험계약의 내용을 정한다. " * 40
    d.add_text(label=DocItemLabel.TEXT, text=txt,
               prov=ProvenanceItem(page_no=1, bbox=BoundingBox(l=0, t=0, r=500, b=20), charspan=(0, len(txt))))
    return d


_LLM_ON = dict(do_toc_enrichment=True, toc_api_provider="custom", toc_model="m",
               toc_api_base_url="http://unused.invalid/v1/chat/completions")


@pytest.mark.unit
@pytest.mark.parametrize("options,llm_response,verdict,reason,has_text", [
    # 기본 설정(toc·metadata 끔) — 판정 LLM 이 없다 → 판정 불가 → 재OCR 사유 없음. 예전엔 0(거짓) → 재OCR.
    (dict(do_toc_enrichment=False, extract_metadata=False), None, de.OcrVerdict.UNKNOWN, "none", True),
    # 같은 설정의 텍스트 레이어 없는 스캔본 — LLM 없이도 OCR 필요(리뷰 지적: 판정 LLM 확인이 빈 텍스트 검사를 가렸다).
    (dict(do_toc_enrichment=False, extract_metadata=False), None, de.OcrVerdict.NEEDS_OCR, "llm_yes", False),
    # 판정 LLM 이 있는데 응답이 없다(느림·429) → 판정 불가. 예전엔 "YES" → 재OCR.
    (_LLM_ON, None, de.OcrVerdict.UNKNOWN, "none", True),
    # 응답에 <decision> 이 없다 → 판정 불가. 예전엔 "YES".
    (_LLM_ON, "<analysis>…</analysis>", de.OcrVerdict.UNKNOWN, "none", True),
    (_LLM_ON, "<analysis>ok</analysis><decision>NO</decision>", de.OcrVerdict.OK, "none", True),
    (_LLM_ON, "<decision>\n yes \n</decision>", de.OcrVerdict.NEEDS_OCR, "llm_yes", True),
], ids=["judge-absent", "judge-absent-scan", "llm-silent", "llm-malformed", "llm-no", "llm-yes"])
def test_judge_document_verdict_and_reocr_reason(options, llm_response, verdict, reason, has_text):
    """판정값은 OcrVerdict 셋 중 하나이고, 재OCR 사유는 needs_ocr 일 때만 생긴다(휴리스틱 없음 기준).
    옛 호출부용 래퍼 check_document 는 OK 일 때만 참이다."""
    doc = _doc_with_text() if has_text else DoclingDocument(name="scan")
    opts = DataEnrichmentOptions(**options)
    with patch.object(PromptManager, "call_ai_model", return_value=llm_response):
        got = de.judge_document(doc, opts)
        compat = de.check_document(doc, opts)
    assert (got, dops.reocr_reason(got, False, False), compat) == (verdict, reason, verdict is de.OcrVerdict.OK)


@pytest.mark.unit
@pytest.mark.parametrize("verdict,reocr_expected", [
    (de.OcrVerdict.UNKNOWN, False),   # 재현 R5: 기본 설정에서 정상 문서가 두 번 변환되던 경우
    (de.OcrVerdict.NEEDS_OCR, True),
], ids=["unknown-no-reocr", "needs-reocr"])
def test_parse_docling_reocrs_only_on_needs_ocr(verdict, reocr_expected):
    """_parse_docling 의 auto 경로: 판정 불가면 첫 변환 결과를 그대로 쓴다(OCR converter 를 부르지 않는다).
    __init__ 우회 + _intel 가짜(ocr_mode=auto, 휴리스틱 모두 거짓) — 기존 test_parser_processor_unit 의 dp 와 같은 꼴."""
    dp = object.__new__(DocumentProcessor)
    dp._intel = MagicMock()
    dp._intel.ocr_mode = "auto"
    dp._intel.ocr_endpoint = ""                      # 셀 재OCR 경로는 건너뛴다
    dp._intel.check_glyphs.return_value = False
    dp._intel.check_empty_text.return_value = False
    dp._intel.enrichment_options.do_picture_description = False
    dp._intel.table_image_enabled = False
    dp._intel.enrichment.side_effect = lambda document, **kw: document
    with patch.object(core_parser, "judge_document", return_value=verdict):
        dp._parse_docling("doc.pdf")
    assert (dp._intel.load_documents.call_count, dp._intel.load_documents_with_docling_ocr.called) == (1, reocr_expected)


@pytest.mark.unit
@pytest.mark.parametrize("facade_cls", [IntelligentProcessor, ConvertProcessor], ids=["intelligent", "convert"])
@pytest.mark.parametrize("request_kwargs,reocr_expected", [
    ({}, False),                   # yaml 기본 disable — 판정·재OCR 없음
    ({"ocr_mode": "auto"}, True),  # 요청이 켜면 글리프 깨짐이 재OCR 사유가 된다
], ids=["default-disable", "request-auto"])
def test_http_facades_honor_request_ocr_mode(facade_cls, request_kwargs, reocr_expected):
    """intelligent·convert 의 _load_document 도 요청 ocr_mode 가 yaml 을 덮는다(#482 리뷰 지적).
    예전엔 self.ocr_mode 만 봐서 기본 disable 에서는 문서 단위로 켤 길이 없었다."""
    fp = object.__new__(facade_cls)
    fp.ocr_mode = "disable"
    fp.enrichment_options = MagicMock()
    fp.load_documents = MagicMock(return_value=MagicMock())
    fp.load_documents_with_docling_ocr = MagicMock(return_value=MagicMock())
    fp.check_glyphs = MagicMock(return_value=True)      # 글리프 깨짐 — auto 면 재OCR 사유
    fp.check_empty_text = MagicMock(return_value=False)
    with patch.object(sys.modules[facade_cls.__module__], "judge_document", return_value=de.OcrVerdict.UNKNOWN):
        fp._load_document("scan.pdf", **request_kwargs)
    assert fp.load_documents_with_docling_ocr.called is reocr_expected
