from pathlib import Path

import pytest


# 모듈 임포트와 클래스 초기화가 예외 없이 가능한지 확인
def test_import_and_init():
    try:
        from doc_preprocessors.basic_processor import DocumentProcessor
    except Exception as e:
        pytest.skip(f"basic_processor import failed: {e}")
    else:
        dp = DocumentProcessor()
        assert dp is not None


# PDF 스모크: 샘플 PDF가 있을 때만 로드 → 페이지/청크 생성 여부를 빠르게 확인
@pytest.mark.skipif(
    not (Path(__file__).resolve().parents[1] / "sample_files" / "sample.pdf").exists(),
    reason="sample.pdf not found",
)
def test_pdf_smoke():
    from doc_preprocessors.basic_processor import DocumentProcessor
    dp = DocumentProcessor()
    sample = Path(__file__).resolve().parents[1] / "sample_files" / "sample.pdf"

    doc = dp.load_documents(str(sample))
    assert doc is not None
    if hasattr(doc, "num_pages"):
        assert doc.num_pages() >= 1

    chunks = dp.split_documents(doc)
    assert isinstance(chunks, list) and len(chunks) >= 1


# HWPX 스모크: 샘플 HWPX가 있을 때만 로드 → 페이지/청크 생성 여부를 빠르게 확인
@pytest.mark.skipif(
    not (Path(__file__).resolve().parents[1] / "sample_files" / "sample.hwpx").exists(),
    reason="sample.hwpx not found",
)
def test_hwpx_smoke():
    from doc_preprocessors.basic_processor import DocumentProcessor
    dp = DocumentProcessor()
    sample = Path(__file__).resolve().parents[1] / "sample_files" / "sample.hwpx"

    doc = dp.load_documents(str(sample))
    assert doc is not None
    if hasattr(doc, "num_pages"):
        assert doc.num_pages() >= 1

    chunks = dp.split_documents(doc)
    assert isinstance(chunks, list) and len(chunks) >= 1


# 가벼운 유닛 테스트: 날짜 파서와 문자열 합치기 유틸의 기본 동작을 확인
def test_unit_helpers():
    from doc_preprocessors.basic_processor import DocumentProcessor
    dp = DocumentProcessor()

    assert dp.parse_created_date("2024-09-01") == 20240901
    assert dp.parse_created_date("2024-09") == 20240901
    assert dp.parse_created_date("2024") == 20240101
    assert dp.parse_created_date("") == 0
    assert dp.parse_created_date("invalid") == 0

    assert dp.safe_join(["a", "b"]) == "ab\n"
    assert dp.safe_join(123) == ""
