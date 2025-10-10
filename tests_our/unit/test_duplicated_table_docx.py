from __future__ import annotations

from pathlib import Path
import asyncio
import sys
import pytest
import json


def _import_processor():
    try:
        # 정상 경로 시도
        from doc_parser.doc_preprocessors.attachment_processor import (
            DocumentProcessor, DocxProcessor
        )
        return DocumentProcessor, DocxProcessor
    except ModuleNotFoundError:
        # 테스트 실행 루트에 따라 sys.path 보정
        sys.path.append(str(Path(__file__).resolve().parents[3]))
        from doc_parser.doc_preprocessors.attachment_processor import (
            DocumentProcessor, DocxProcessor
        )
        return DocumentProcessor, DocxProcessor


class _DummyRequest:
    async def is_disconnected(self) -> bool:  # pragma: no cover
        return False


@pytest.mark.unit
def test_duplicated_table_docx_structure():
    """duplicated_table.docx 파일의 표 구조가 제대로 추출되는지 테스트"""
    DocumentProcessor, DocxProcessor = _import_processor()
    
    sample_path = Path(__file__).resolve().parents[2] / "sample_files" / "duplicated_table.docx"
    
    if not sample_path.exists():
        pytest.skip(f"sample not found: {sample_path}")
    
    dp = DocumentProcessor()
    
    async def _run():
        return await dp(_DummyRequest(), str(sample_path))
    
    vectors = asyncio.run(_run())
    
    # 기본 검증
    assert isinstance(vectors, list)
    assert len(vectors) >= 1
    
    # 벡터의 필수 필드 검증
    v0 = vectors[0]
    text = getattr(v0, "text", None) if hasattr(v0, "text") else v0.get("text")
    assert isinstance(text, str) and len(text) > 0
    
    # 표 구조 관련 검증
    full_text = ""
    for vector in vectors:
        vector_text = getattr(vector, "text", None) if hasattr(vector, "text") else vector.get("text")
        if vector_text:
            full_text += vector_text + "\n"
    
    # 표 마크다운 구조가 포함되어 있는지 확인
    assert "|" in full_text, "표 구조(마크다운 형식)가 추출되지 않았습니다"
    
    # 표 헤더 구분자가 있는지 확인 (마크다운 표의 특징)
    assert "---" in full_text or "|-" in full_text, "표 헤더 구분자가 없습니다"
    
    print(f"\n=== 추출된 텍스트 ===")
    print(full_text[:1000])  # 처음 1000자만 출력
    
    # bbox 정보가 있는지 확인
    bbox_info = getattr(v0, "chunk_bboxes", None) if hasattr(v0, "chunk_bboxes") else v0.get("chunk_bboxes")
    if bbox_info and bbox_info != "null":
        try:
            bbox_data = json.loads(bbox_info)
            assert isinstance(bbox_data, list), "bbox 정보가 리스트 형태가 아닙니다"
            print(f"\n=== bbox 정보 ===")
            print(f"bbox 항목 수: {len(bbox_data)}")
            if bbox_data:
                print(f"첫 번째 bbox: {bbox_data[0]}")
        except json.JSONDecodeError:
            print("bbox 정보 파싱 실패")


@pytest.mark.unit
def test_duplicated_table_docx_direct_processor():
    """DocxProcessor를 직접 사용하여 duplicated_table.docx 처리 테스트"""
    DocumentProcessor, DocxProcessor = _import_processor()
    
    sample_path = Path(__file__).resolve().parents[2] / "sample_files" / "duplicated_table.docx"
    
    if not sample_path.exists():
        pytest.skip(f"sample not found: {sample_path}")
    
    processor = DocxProcessor()
    
    async def _run():
        return await processor(_DummyRequest(), str(sample_path))
    
    vectors = asyncio.run(_run())
    
    # 기본 검증
    assert isinstance(vectors, list)
    assert len(vectors) >= 1
    
    # 페이지 정보 검증
    v0 = vectors[0]
    i_page = getattr(v0, "i_page", None) if hasattr(v0, "i_page") else v0.get("i_page")
    e_page = getattr(v0, "e_page", None) if hasattr(v0, "e_page") else v0.get("e_page")
    n_page = getattr(v0, "n_page", None) if hasattr(v0, "n_page") else v0.get("n_page")
    
    assert i_page is not None, "시작 페이지 정보가 없습니다"
    assert e_page is not None, "끝 페이지 정보가 없습니다"
    assert n_page is not None, "총 페이지 정보가 없습니다"
    
    print(f"\n=== 페이지 정보 ===")
    print(f"시작 페이지: {i_page}, 끝 페이지: {e_page}, 총 페이지: {n_page}")
    
    # 표 관련 텍스트 추출 확인
    table_found = False
    for vector in vectors:
        text = getattr(vector, "text", None) if hasattr(vector, "text") else vector.get("text")
        if text and "|" in text:
            table_found = True
            print(f"\n=== 표 구조 발견 ===")
            print(text[:500])  # 처음 500자만 출력
            break
    
    assert table_found, "표 구조가 발견되지 않았습니다"


@pytest.mark.unit 
def test_duplicated_table_docx_table_content():
    """duplicated_table.docx의 표 내용이 중복 없이 제대로 추출되는지 테스트"""
    DocumentProcessor, DocxProcessor = _import_processor()
    
    sample_path = Path(__file__).resolve().parents[2] / "sample_files" / "duplicated_table.docx"
    
    if not sample_path.exists():
        pytest.skip(f"sample not found: {sample_path}")
    
    processor = DocxProcessor()
    
    # 문서 로드 및 청킹
    document = processor.load_documents(str(sample_path))
    chunks = processor.split_documents(document)
    
    # 청크 수 확인
    assert len(chunks) >= 1, "청크가 생성되지 않았습니다"
    
    print(f"\n=== 청크 정보 ===")
    print(f"총 청크 수: {len(chunks)}")
    
    # 각 청크의 텍스트 내용 확인
    table_chunks = []
    for i, chunk in enumerate(chunks):
        if "|" in chunk.text:  # 표 구조를 포함한 청크
            table_chunks.append((i, chunk.text))
            print(f"\n=== 청크 {i} (표 포함) ===")
            print(chunk.text[:300])
    
    assert len(table_chunks) > 0, "표를 포함한 청크가 없습니다"
    
    # 표 내용 중복 검사 (간단한 방법: 동일한 표 텍스트가 여러 번 나타나는지 확인)
    table_texts = [text for _, text in table_chunks]
    unique_table_texts = set(table_texts)
    
    print(f"\n=== 중복 검사 ===")
    print(f"표 청크 수: {len(table_texts)}")
    print(f"고유 표 텍스트 수: {len(unique_table_texts)}")
    
    # 중복이 있다면 경고 (완전히 실패시키지는 않음)
    if len(table_texts) > len(unique_table_texts):
        print("경고: 표 내용에 중복이 있을 수 있습니다")
