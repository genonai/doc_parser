"""intelligent_processor 를 실제 샘플 파일로 끝까지 돌려보는 smoke 테스트.

원래 tests/unit/test_intelligent_processor_unit.py 의 TestIntelligentProcessor 안에 있었다.
실제 문서 변환을 수행해 unit 전체 실행 시간의 5%(약 28초)를 쓰면서 unit 마커조차 붙어
있지 않아, conftest 의 unit 전용 스텁이 걸리지 않는 상태였다. 계층을 맞춰 여기로 옮긴다.

원래 파일에는 순수 단위 테스트(설정 해석, enricher 배선)가 남는다.
"""

import os
from pathlib import Path

import pytest
from unittest.mock import AsyncMock, Mock


# PDF 는 기본 layout(genos_layout)이 사내 DotsOCR 엔드포인트를 실제로 호출한다.
# 사내망에 닿지 않는 GitHub CI 에서는 연결 타임아웃으로 실패하므로, 다른 PDF smoke 와
# 같은 GENOS_LAYOUT_AVAILABLE 규약으로 게이팅한다.
PDF_SAMPLE = pytest.param(
    "pdf_sample.pdf",
    marks=pytest.mark.skipif(
        not os.environ.get("GENOS_LAYOUT_AVAILABLE"),
        reason="GENOS_LAYOUT_AVAILABLE 미설정 — dotsocr layout 엔드포인트 없음(CI). skip.",
    ),
)


class TestIntelligentProcessorSmoke:
    @pytest.fixture
    def processor(self, intelligent_processor):
        return intelligent_processor()

    @pytest.fixture
    def mock_request(self):
        request = Mock()
        request.is_disconnected = AsyncMock(return_value=False)
        return request

    @pytest.mark.smoke
    @pytest.mark.parametrize("filename", [
        PDF_SAMPLE,
        "hwpx_sample.hwpx",
        "docx_sample.docx",
        "md_sample.md"
    ])
    def test_load_documents(self, processor, sample_dir, filename):
        """각 파일 타입에 대해 문서 로드 테스트"""
        test_file = sample_dir / filename

        if not test_file.exists():
            pytest.skip(f"Sample file {filename} not found")

        document = processor.load_documents(str(test_file))
        assert document is not None, f"Document should be loaded from {filename}"
        assert hasattr(document, 'num_pages'), "Document should have num_pages method"
        assert document.num_pages() > 0, f"Document {filename} should have at least 1 page"

    @pytest.mark.smoke
    @pytest.mark.parametrize("filename", [
        "docx_sample.docx",
        "pptx_sample.pptx",
        "md_sample.md"
    ])
    def test_pdf_conversion(self, processor, sample_dir, filename):
        """PDF 변환 기능 테스트 (PDF 제외)"""
        test_file = sample_dir / filename

        if not test_file.exists():
            pytest.skip(f"Sample file {filename} not found")

        from facade.convert_processor import convert_to_pdf

        pdf_path = convert_to_pdf(str(test_file))

        if not pdf_path:
            # 변환 실패는 예상되는 상황 (LibreOffice 없거나 파일 형식 문제)
            pytest.skip(f"PDF conversion failed for {filename} - this is expected in test environment")

        pdf_file = Path(pdf_path)
        assert pdf_file.exists(), f"PDF file should exist at {pdf_path}"
        assert pdf_file.suffix.lower() == ".pdf", "Converted file should have .pdf extension"
        assert pdf_file.parent == test_file.parent, "PDF should be in same directory as source"
        assert pdf_file.stat().st_size > 0, f"PDF file {pdf_path} should not be empty"

    @pytest.mark.smoke
    @pytest.mark.parametrize("filename", [
        PDF_SAMPLE,
        "hwpx_sample.hwpx",
        "docx_sample.docx",
        "md_sample.md"
    ])
    def test_chunk_generation_with_real_files(self, processor, sample_dir, filename):
        """실제 샘플 파일로 청크 생성 테스트"""
        test_file = sample_dir / filename

        if not test_file.exists():
            pytest.skip(f"Sample file {filename} not found")

        document = processor.load_documents(str(test_file))
        assert document is not None, f"Document should be loaded from {filename}"

        chunks = processor.split_documents(document)
        assert len(chunks) >= 1, f"At least one chunk should be generated from {filename}"

        for i, chunk in enumerate(chunks):
            assert hasattr(chunk, 'text'), f"Chunk {i} should have text attribute"
            assert hasattr(chunk, 'meta'), f"Chunk {i} should have meta attribute"
            assert hasattr(chunk.meta, 'doc_items'), f"Chunk {i} meta should have doc_items"
            assert len(chunk.meta.doc_items) > 0, f"Chunk {i} should have at least one doc_item"
            assert isinstance(chunk.text, str), f"Chunk {i} text should be string"
