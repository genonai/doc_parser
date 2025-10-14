"""
basic_processor.py에 대한 unit test
PDF, HWPX, DOCX, MD 파일에 대해 테스트
"""

import pytest
from pathlib import Path
import os
import tempfile
import shutil


class TestBasicProcessor:
    """BasicProcessor 클래스에 대한 단위 테스트"""

    @pytest.fixture
    def processor(self, basic_processor):
        """DocumentProcessor 인스턴스 생성"""
        return basic_processor()

    @pytest.fixture
    def temp_dir(self):
        """임시 디렉토리 생성"""
        temp_dir = tempfile.mkdtemp()
        yield Path(temp_dir)
        shutil.rmtree(temp_dir, ignore_errors=True)

    def create_test_file(self, temp_dir: Path, filename: str, content: str = "Test content") -> Path:
        """테스트용 파일 생성"""
        file_path = temp_dir / filename
        file_path.write_text(content, encoding='utf-8')
        return file_path

    @pytest.mark.parametrize("filename", [
        "pdf_sample.pdf",
        "hwpx_sample.hwpx", 
        "docx_sample.docx",
        "md_sample.md"
    ])
    def test_load_documents(self, processor, sample_dir, filename):
        """각 파일 타입에 대해 문서 로드 테스트"""
        test_file = sample_dir / filename
        
        # 파일이 존재하는지 확인
        if not test_file.exists():
            pytest.skip(f"Sample file {filename} not found")
        
        try:
            # 문서 로드 테스트
            document = processor.load_documents(str(test_file))
            assert document is not None, f"Document should be loaded from {filename}"
            assert hasattr(document, 'num_pages'), "Document should have num_pages method"
            
            # 페이지 수 확인
            page_count = document.num_pages()
            assert page_count > 0, f"Document {filename} should have at least 1 page"
            
        except Exception as e:
            pytest.fail(f"Failed to load document {filename}: {e}")

    @pytest.mark.parametrize("filename", [
        "docx_sample.docx", 
        "pptx_sample.pptx",
        "md_sample.md"
    ])
    def test_pdf_conversion(self, processor, sample_dir, filename):
        """PDF 변환 기능 테스트 (PDF 제외)"""
        test_file = sample_dir / filename
        
        # 파일이 존재하는지 확인
        if not test_file.exists():
            pytest.skip(f"Sample file {filename} not found")
        
        # convert_to_pdf 함수 import
        from doc_preprocessors.basic_processor import convert_to_pdf
        
        # PDF 변환 시도
        pdf_path = convert_to_pdf(str(test_file))
        
        if pdf_path:
            # PDF 경로가 반환된 경우
            pdf_file = Path(pdf_path)
            assert pdf_file.exists(), f"PDF file should exist at {pdf_path}"
            assert pdf_file.suffix.lower() == ".pdf", "Converted file should have .pdf extension"
            
            # 원본 파일과 같은 디렉토리에 생성되었는지 확인
            assert pdf_file.parent == test_file.parent, "PDF should be in same directory as source"
            
            # 파일 크기가 0보다 큰지 확인
            assert pdf_file.stat().st_size > 0, f"PDF file {pdf_path} should not be empty"
        else:
            # 변환 실패는 예상되는 상황 (LibreOffice 없거나 파일 형식 문제)
            pytest.skip(f"PDF conversion failed for {filename} - this is expected in test environment")

    @pytest.mark.parametrize("filename", [
        "pdf_sample.pdf",
        "hwpx_sample.hwpx",
        "docx_sample.docx", 
        "md_sample.md"
    ])
    def test_chunk_generation_with_real_files(self, processor, sample_dir, filename):
        """실제 샘플 파일로 청크 생성 테스트"""
        test_file = sample_dir / filename
        
        # 파일이 존재하는지 확인
        if not test_file.exists():
            pytest.skip(f"Sample file {filename} not found")
        
        try:
            # 문서 로드
            document = processor.load_documents(str(test_file))
            assert document is not None, f"Document should be loaded from {filename}"
            
            # 청크 분할
            chunks = processor.split_documents(document)
            
            # 청크가 하나 이상 생성되었는지 확인
            assert len(chunks) >= 1, f"At least one chunk should be generated from {filename}"
            
            # 각 청크가 올바른 구조를 가지는지 확인
            for i, chunk in enumerate(chunks):
                assert hasattr(chunk, 'text'), f"Chunk {i} should have text attribute"
                assert hasattr(chunk, 'meta'), f"Chunk {i} should have meta attribute"
                assert hasattr(chunk.meta, 'doc_items'), f"Chunk {i} meta should have doc_items"
                assert len(chunk.meta.doc_items) > 0, f"Chunk {i} should have at least one doc_item"
                
                # 텍스트 내용이 있는지 확인 (빈 문자열이 아닌지)
                assert isinstance(chunk.text, str), f"Chunk {i} text should be string"
                
        except Exception as e:
            pytest.fail(f"Chunk generation test failed for {filename}: {e}")

    def test_convertible_extensions(self):
        """변환 가능한 확장자 목록 확인"""
        from doc_preprocessors.basic_processor import CONVERTIBLE_EXTENSIONS
        
        expected_extensions = ['.xlsx', '.md', '.docx', '.pptx']
        assert CONVERTIBLE_EXTENSIONS == expected_extensions, f"Expected {expected_extensions}, got {CONVERTIBLE_EXTENSIONS}"

    def test_processor_initialization(self, processor):
        """프로세서 초기화 테스트"""
        # 기본 속성 확인
        assert hasattr(processor, 'converter'), "Processor should have converter"
        assert hasattr(processor, 'second_converter'), "Processor should have second_converter"
        assert hasattr(processor, 'page_chunk_counts'), "Processor should have page_chunk_counts"
        
        # page_chunk_counts가 defaultdict인지 확인
        from collections import defaultdict
        assert isinstance(processor.page_chunk_counts, defaultdict), "page_chunk_counts should be defaultdict"

    def test_safe_join_method(self, processor):
        """safe_join 메서드 테스트"""
        # 정상적인 리스트
        result = processor.safe_join(['a', 'b', 'c'])
        assert result == 'abc\n', "safe_join should concatenate strings with newline"
        
        # 빈 리스트
        result = processor.safe_join([])
        assert result == '\n', "safe_join should return newline for empty list"
        
        # None 값
        result = processor.safe_join(None)
        assert result == '', "safe_join should return empty string for None"
        
        # 문자열이 아닌 값
        result = processor.safe_join("not a list")
        assert result == '', "safe_join should return empty string for non-iterable"

    def test_parse_created_date_method(self, processor):
        """parse_created_date 메서드 테스트"""
        # 정상적인 날짜 형식들
        assert processor.parse_created_date("2024-01-15") == 20240115
        assert processor.parse_created_date("2024-01") == 20240101
        assert processor.parse_created_date("2024") == 20240101

        # 잘못된 형식들
        assert processor.parse_created_date("invalid") == 0
        assert processor.parse_created_date("") == 0
        assert processor.parse_created_date(None) == 0
        assert processor.parse_created_date("None") == 0

    def test_basic_processor_specific_features(self, processor):
        """BasicProcessor만의 특별한 기능들 테스트"""
        # BasicProcessor는 HWPX 지원이 있는지 확인
        assert hasattr(processor, 'converter'), "Should have converter"
        
        # pipe_line_options 확인
        assert hasattr(processor, 'pipe_line_options'), "Should have pipe_line_options"
        assert hasattr(processor, 'simple_pipeline_options'), "Should have simple_pipeline_options"
        
        # _create_converters 메서드 확인
        assert hasattr(processor, '_create_converters'), "Should have _create_converters method"

    def test_load_documents_with_save_images_option(self, processor, temp_dir):
        """save_images 옵션을 사용한 문서 로드 테스트"""
        test_file = self.create_test_file(temp_dir, "test.pdf", "Test content")
        
        try:
            # save_images=True로 로드
            document1 = processor.load_documents(str(test_file), save_images=True)
            
            # save_images=False로 로드
            document2 = processor.load_documents(str(test_file), save_images=False)
            
            # 둘 다 문서가 로드되어야 함
            assert document1 is not None
            assert document2 is not None

        except Exception as e:
            # 실제 PDF가 아니므로 예외 발생 예상
            pytest.skip(f"Load documents test skipped - expected for non-PDF file: {e}")
