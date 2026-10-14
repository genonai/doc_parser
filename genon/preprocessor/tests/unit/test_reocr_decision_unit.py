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
@pytest.mark.parametrize("native,verdict,found,endpoint,failed,expected", [
    # found = (글리프 깨진 페이지, 빈 페이지, 문서에 텍스트가 있는지)
    # 재현 R5: 기본 설정에서 정상 문서가 두 번 변환되던 경우 — 판정 불가면 재변환하지 않는다
    (True, de.OcrVerdict.UNKNOWN, ([], [], True), "", [], (False, False, True)),
    # 판정 LLM 이 OCR 필요라 답하면 문서 전체를 다시 변환한다
    (True, de.OcrVerdict.NEEDS_OCR, ([], [], True), "", [], (False, True, True)),
    # #483: genos_layout 은 판정 LLM 을 부르지 않는다 — LLM 이 YES 였을 문서도 재변환하지 않는다
    (False, de.OcrVerdict.NEEDS_OCR, ([], [], True), "", [], (False, False, False)),
    # #483: 글리프·빈 페이지는 그 페이지만 OCR 하고, 모두 처리되면 전체 재변환은 하지 않는다
    (True, None, ([2], [5], True), "http://ocr", [], (True, False, False)),
    # 처리하지 못한 페이지가 있으면 전체 재변환으로 보낸다
    (True, None, ([2], [], True), "http://ocr", [2], (True, True, False)),
    # #483: 문서 전체에 텍스트가 없으면(스캔본) 판정 LLM 없이 no_text 로 전체 재변환한다
    (True, None, ([], [], False), "http://ocr", [], (False, True, False)),
    # OCR 주소가 없으면 페이지 단위로 할 수 없으므로 전체 재변환한다(예전 동작)
    (True, None, ([2], [], True), "", [], (False, True, False)),
], ids=["unknown-no-reocr", "llm-yes-full", "genos-skips-llm", "pages-fixed", "pages-failed-full",
        "no-text-full", "no-endpoint-full"])
def test_parse_docling_reocr_paths(native, verdict, found, endpoint, failed, expected):
    """_parse_docling 의 auto 경로: 페이지 단위 재OCR 과 문서 전체 재변환 중 무엇을 하는지.
    __init__ 우회 + _intel 가짜 — 기존 test_parser_processor_unit 의 dp 와 같은 꼴."""
    dp = object.__new__(DocumentProcessor)
    dp._intel = MagicMock()
    dp._intel.ocr_mode = "auto"
    dp._intel.ocr_endpoint = endpoint
    dp._intel.find_reocr_pages.return_value = found[:2]
    dp._intel.has_text.return_value = found[2]
    dp._intel.page_text_metrics.return_value = {"glyph_by_page": {}, "chars": 10, "chars_per_page": 10}
    dp._intel.judges_native_text.return_value = native
    dp._intel.ocr_text_items_on_pages.return_value = failed
    dp._intel.ocr_all_table_cells.side_effect = lambda document, path: document
    dp._intel.enrichment_options.do_picture_description = False
    dp._intel.table_image_enabled = False
    dp._intel.enrichment.side_effect = lambda document, **kw: document
    ctx: dict = {}
    with patch.object(core_parser, "judge_document", return_value=verdict) as judge:
        dp._parse_docling("doc.pdf", _enrichment_context=ctx)
    # expected = (페이지 단위 OCR 호출, 문서 전체 재변환, 판정 LLM 호출)
    assert (dp._intel.ocr_text_items_on_pages.called, dp._intel.load_documents_with_docling_ocr.called,
            judge.called) == expected
    # 판정 기록이 응답 metadata.ocr_judge 로 나간다 — 전체 재변환 여부가 실제 동작과 같아야 한다
    judge_log = ctx["metadata"]["ocr_judge"]
    assert (judge_log["full_reconvert"], judge_log["verdict"] == "skipped", judge_log["chars"]) == (
        expected[1], not judge.called, 10)


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
    fp.ocr_endpoint = ""                                   # 페이지 단위 OCR 불가 — 전체 재변환 경로
    fp.find_reocr_pages = MagicMock(return_value=([1], []))  # 글리프 깨짐 — auto 면 재OCR 사유
    with patch.object(sys.modules[facade_cls.__module__], "judge_document", return_value=de.OcrVerdict.UNKNOWN):
        fp._load_document("scan.pdf", **request_kwargs)
    assert fp.load_documents_with_docling_ocr.called is reocr_expected


@pytest.mark.unit
def test_find_reocr_pages_returns_broken_page_numbers():
    """#483: 페이지 단위 재OCR 대상 — 글리프 깨진 페이지와 빈 페이지를 번호로 돌려준다."""
    d = DoclingDocument(name="t")
    pages = {1: "제1조 목적 이 약관은 보험계약의 내용을 정한다.",
             2: " ".join(["GLYPH<c=4,font=/X>"] * 11),   # document_threshold(10) 초과
             3: "",                                       # 박스는 있는데 글자가 없다
             4: "정상 페이지 GLYPH<c=1,font=/X>"}           # 임계값 이하
    for page_no, text in pages.items():
        d.add_page(page_no=page_no, size=Size(width=600, height=800))
        d.add_text(label=DocItemLabel.TEXT, text=text,
                   prov=ProvenanceItem(page_no=page_no, bbox=BoundingBox(l=0, t=0, r=500, b=20),
                                       charspan=(0, len(text))))
    assert dops.find_reocr_pages(d, threshold=10) == ([2], [3])
    # 판정 기록용 지표 — GLYPH 는 토큰이 있는 페이지만, 평균은 텍스트가 있는 페이지 기준
    metrics = dops.page_text_metrics(d)
    assert (metrics["glyph_by_page"], metrics["chars_per_page"]) == ({"2": 11, "4": 1}, round(metrics["chars"] / 3))


@pytest.mark.unit
def test_ocr_text_items_on_pages_replaces_only_target_pages():
    """#483: 대상 페이지의 텍스트 박스만 OCR 결과로 바꾸고, 페이지 이미지가 없는 페이지는 실패로 돌려준다.
    OCR 서버 호출(_post_ocr_bytes)은 패치한다."""
    from PIL import Image
    from docling_core.types.doc import ImageRef

    d = DoclingDocument(name="t")
    for page_no, text, with_image in [(1, "정상", True), (2, "GLYPH<c=4,font=/X>", True), (3, "", False)]:
        d.add_page(page_no=page_no, size=Size(width=600, height=800),
                   image=ImageRef.from_pil(Image.new("RGB", (600, 800), "white"), dpi=72) if with_image else None)
        d.add_text(label=DocItemLabel.TEXT, text=text,
                   prov=ProvenanceItem(page_no=page_no, bbox=BoundingBox(l=10, t=100, r=500, b=80),
                                       charspan=(0, len(text))))
    ocr_resp = {"result": {"ocrResults": [{"prunedResult": {
        "rec_texts": ["제2조", "정의"], "rec_scores": [0.9, 0.9], "rec_boxes": [[0, 0, 1, 1]] * 2}}]}}
    with patch.object(dops, "_post_ocr_bytes", return_value=ocr_resp) as post:
        failed = dops.ocr_text_items_on_pages(d, [2, 3], ocr_endpoint="http://unused.invalid")
    assert ([t.text for t in d.texts], failed, post.call_count) == (["정상", "제2조 정의", ""], [3], 1)


@pytest.mark.unit
def test_text_checking_prompt_formats_with_judge_kwargs():
    """#483: 판정 프롬프트의 자리표시자와 judge_document_text 가 넘기는 인자 이름이 맞는지.
    어긋나면 format_user_prompt 가 KeyError 를 삼키고 None 을 돌려 판정이 조용히 '판정 불가' 가 된다."""
    pm = PromptManager()
    prompt = pm.format_user_prompt("document_checking", "text_checking", content="본문",
                                   text_len=2, non_meaningful_ratio=0.0, space_ratio=0.0)
    assert prompt is not None and "비의미 문자 비율: 0.0" in prompt and "비ASCII" not in prompt
