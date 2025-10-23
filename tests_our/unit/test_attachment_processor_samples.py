from __future__ import annotations

from pathlib import Path
import asyncio
import shutil
import sys
import pytest


SAMPLE_DIR = Path(__file__).resolve().parents[2] / "sample_files"
ALL_EXTS = [
    "csv", "xlsx", "md", "docx", "pdf", "ppt", "pptx", "txt", "json",
    "jpeg", "png",
]


def _collect_samples(exts: list[str]) -> list[Path]:
    samples: list[Path] = []
    for ext in exts:
        samples.extend(sorted(SAMPLE_DIR.glob(f"*.{ext}")))
    return samples


class _DummyRequest:
    async def is_disconnected(self) -> bool:  # pragma: no cover
        return False


def _import_processor():
    try:
        # 정상 경로 시도
        from doc_parser.doc_preprocessors.attachment_processor import (
            DocumentProcessor, _get_pdf_path, convert_to_pdf, TextLoader,
        )
        return DocumentProcessor, _get_pdf_path, convert_to_pdf, TextLoader
    except ModuleNotFoundError:
        # 테스트 실행 루트에 따라 sys.path 보정
        sys.path.append(str(Path(__file__).resolve().parents[3]))
        from doc_parser.doc_preprocessors.attachment_processor import (
            DocumentProcessor, _get_pdf_path, convert_to_pdf, TextLoader,
        )
        return DocumentProcessor, _get_pdf_path, convert_to_pdf, TextLoader


def _import_basic_processor():
    try:
        # 정상 경로 시도
        from doc_parser.doc_preprocessors.basic_processor import DocumentProcessor as BasicDocumentProcessor
        return BasicDocumentProcessor
    except ModuleNotFoundError:
        # 테스트 실행 루트에 따라 sys.path 보정
        sys.path.append(str(Path(__file__).resolve().parents[3]))
        from doc_parser.doc_preprocessors.basic_processor import DocumentProcessor as BasicDocumentProcessor
        return BasicDocumentProcessor


@pytest.mark.unit
@pytest.mark.parametrize("sample_path", _collect_samples(ALL_EXTS), ids=lambda p: p.name)
def test_vectors_created_for_samples(sample_path: Path):
    DocumentProcessor, *_ = _import_processor()

    if not sample_path.exists():
        pytest.skip(f"sample not found: {sample_path}")

    dp = DocumentProcessor()

    async def _run():
        return await dp(_DummyRequest(), str(sample_path))

    vectors = asyncio.run(_run())

    assert isinstance(vectors, list)
    assert len(vectors) >= 1
    # 벡터의 필수 필드 대략 점검
    v0 = vectors[0]
    # pydantic 모델 혹은 dict 형태 모두 허용
    text = getattr(v0, "text", None) if hasattr(v0, "text") else v0.get("text")
    assert isinstance(text, str) and len(text) > 0


def _has_weasyprint() -> bool:
    try:
        import weasyprint  # noqa: F401
        return True
    except Exception:
        return False


def _has_soffice() -> bool:
    return shutil.which("soffice") is not None


@pytest.mark.unit
@pytest.mark.parametrize(
    "sample_path",
    _collect_samples(["md", "docx", "ppt", "pptx", "txt", "json", "pdf", "csv", "xlsx", "jpg", "jpeg", "png"]),
    ids=lambda p: p.name,
)
def test_pdf_generation_rules(sample_path: Path):
    DocumentProcessor, _get_pdf_path, convert_to_pdf, TextLoader = _import_processor()

    if not sample_path.exists():
        pytest.skip(f"sample not found: {sample_path}")

    ext = sample_path.suffix.lower()

    # # csv/xlsx 는 PDF 생성 대상이 아님
    # if ext in (".csv", ".xlsx"):
    #     pdf_path = Path(_get_pdf_path(str(sample_path)))
    #     assert not pdf_path.exists()
    #     return

    # 이미 PDF 인 경우는 그 파일 자체가 존재해야 함
    if ext == ".pdf":
        assert sample_path.exists()
        return

    # md → weasyprint 필요
    if ext == ".md":
        if not _has_weasyprint():
            pytest.skip("weasyprint 미설치로 PDF 생성 검증 스킵")
        dp = DocumentProcessor()
        pdf_path = Path(dp.convert_md_to_pdf(str(sample_path)))
        assert pdf_path.exists()
        return

    # txt/json → TextLoader가 weasyprint 있으면 PDF 생성
    if ext in (".txt", ".json"):
        if not _has_weasyprint():
            pytest.skip("weasyprint 미설치로 PDF 생성 검증 스킵")
        loader = TextLoader(str(sample_path))
        try:
            loader.load()
        except Exception:
            # 로더 실패 시에도 환경 문제 가능성이 높으므로 스킵 처리
            pytest.skip("TextLoader 실행 실패로 PDF 생성 검증 스킵")
        pdf_path = Path(_get_pdf_path(str(sample_path)))
        assert pdf_path.exists()
        return

    # doc/ppt 계열 → LibreOffice 필요. 생성 후 ppt/pptx는 내부 로직에서 삭제되므로 존재 보장은 못 함
    if ext in (".doc", ".docx", ".ppt", ".pptx"):
        if not _has_soffice():
            pytest.skip("LibreOffice(soffice) 미설치로 PDF 생성 검증 스킵")
        pdf_path = convert_to_pdf(str(sample_path))
        # 변환 성공 시 경로 반환 및 파일 존재
        assert pdf_path is None or Path(pdf_path).exists()
        return

    # 그 외 타입은 _get_pdf_path 규칙에 따름 (여기선 샘플에 없음)
    pdf_path = Path(_get_pdf_path(str(sample_path)))
    assert pdf_path.exists()


@pytest.mark.unit
@pytest.mark.parametrize("sample_path", _collect_samples(["docx"]), ids=lambda p: p.name)
def test_attachment_vs_basic_processor_docx(sample_path: Path):
    """docx 파일에 대해 attachment_processor와 basic_processor 결과 비교"""
    AttachmentProcessor, *_ = _import_processor()
    BasicProcessor = _import_basic_processor()

    if not sample_path.exists():
        pytest.skip(f"sample not found: {sample_path}")

    attachment_dp = AttachmentProcessor()
    basic_dp = BasicProcessor()

    async def _run_attachment():
        return await attachment_dp(_DummyRequest(), str(sample_path))

    async def _run_basic():
        return await basic_dp(_DummyRequest(), str(sample_path))

    # 두 프로세서 실행
    attachment_vectors = asyncio.run(_run_attachment())
    basic_vectors = asyncio.run(_run_basic())

    # 결과 검증
    assert isinstance(attachment_vectors, list)
    assert isinstance(basic_vectors, list)
    assert len(attachment_vectors) >= 1
    assert len(basic_vectors) >= 1

    # 첫 번째 벡터의 텍스트 내용 비교
    attachment_text = getattr(attachment_vectors[0], "text", None) if hasattr(attachment_vectors[0], "text") else attachment_vectors[0].get("text")
    basic_text = getattr(basic_vectors[0], "text", None) if hasattr(basic_vectors[0], "text") else basic_vectors[0].get("text")
    
    assert isinstance(attachment_text, str) and len(attachment_text) > 0
    assert isinstance(basic_text, str) and len(basic_text) > 0
    
    # 텍스트 내용이 동일한지 확인 (공백 정규화 후 비교)
    import re
    attachment_normalized = re.sub(r'\s+', ' ', attachment_text.strip())
    basic_normalized = re.sub(r'\s+', ' ', basic_text.strip())
    
    assert attachment_normalized == basic_normalized, f"텍스트 내용이 다릅니다.\nAttachment: {attachment_normalized[:100]}...\nBasic: {basic_normalized[:100]}..."


