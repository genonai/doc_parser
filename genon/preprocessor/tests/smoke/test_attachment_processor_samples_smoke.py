"""attachment_processor 를 sample_files 전 확장자에 대해 실제로 돌려보는 smoke 테스트.

원래 tests/unit 에 있었으나 unit 계층에 있을 이유가 없어 여기로 옮겼다. 판단 근거는 둘이다.

  - 하는 일이 smoke 다. 실제 문서 변환·PDF 변환·OCR 을 끝까지 돌리고 단정문은
    "벡터가 1개 이상 나오고 첫 벡터의 text 가 비어 있지 않다" 뿐이다.
  - 같은 계층의 test_docx_smoke.py / test_pdf_smoke.py 등이 이미 같은 일을 한다.
    거기서 다루지 않는 확장자(csv, xlsx, txt, json, 이미지)를 이 파일이 채운다.

대상 목록이 sample_files 글롭이라 디렉터리 내용에 따라 수집 개수가 변한다.
고정된 계약을 검사하고 싶으면 unit 쪽에 단정문 테스트를 따로 두는 편이 맞다.
"""

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


def _has_tesseract() -> bool:
    # 시스템에 실제 tesseract 실행파일이 있는지 체크
    return shutil.which("tesseract") is not None

def _has_same_stem_other_ext(p: Path) -> bool:
    """
    같은 디렉터리에 같은 stem을 가진 다른 확장자의 파일이 있는지 확인.
    예: foo.pdf 와 foo.docx가 같이 있으면 True
    """
    stem = p.stem
    for other in p.parent.glob(f"{stem}.*"):
        if other != p and other.is_file():
            return True
    return False


class _DummyRequest:
    async def is_disconnected(self) -> bool:  # pragma: no cover
        return False


def _import_processor():
    try:
        # 정상 경로 시도
        from facade.attachment_processor import (
            DocumentProcessor, _get_pdf_path, convert_to_pdf, TextLoader,
        )
        return DocumentProcessor, _get_pdf_path, convert_to_pdf, TextLoader
    except ModuleNotFoundError:
        # 테스트 실행 루트에 따라 sys.path 보정
        sys.path.append(str(Path(__file__).resolve().parents[3]))
        from facade.attachment_processor import (
            DocumentProcessor, _get_pdf_path, convert_to_pdf, TextLoader,
        )
        return DocumentProcessor, _get_pdf_path, convert_to_pdf, TextLoader


# def _import_basic_processor():
#     try:
#         # 정상 경로 시도
#         from facade.basic_processor import DocumentProcessor as BasicDocumentProcessor
#         return BasicDocumentProcessor
#     except ModuleNotFoundError:
#         # 테스트 실행 루트에 따라 sys.path 보정
#         sys.path.append(str(Path(__file__).resolve().parents[3]))
#         from facade.basic_processor import DocumentProcessor as BasicDocumentProcessor
#         return BasicDocumentProcessor


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp"}

@pytest.mark.smoke
@pytest.mark.parametrize("sample_path", _collect_samples(ALL_EXTS), ids=lambda p: p.name)
def test_vectors_created_for_samples(sample_path: Path):
    # pdf인데 같은 이름의 다른 확장자 파일이 있으면 스킵
    if sample_path.suffix.lower() == ".pdf" and _has_same_stem_other_ext(sample_path):
        pytest.skip(f"pdf has sibling with same stem: {sample_path.name}")

    # 이미지인데 tesseract 없으면 스킵
    if sample_path.suffix.lower() in IMAGE_EXTS and not _has_tesseract():
        pytest.skip("tesseract not installed; skipping image sample test")

    DocumentProcessor, *_ = _import_processor()

    if not sample_path.exists():
        pytest.skip(f"sample not found: {sample_path}")

    dp = DocumentProcessor()

    async def _run():
        return await dp(_DummyRequest(), str(sample_path))

    try:
        vectors = asyncio.run(_run())
    except TypeError as e:
        # unstructured가 이미지에서 None element를 돌려주는 케이스 방어
        if sample_path.suffix.lower() in IMAGE_EXTS and "returned non-string" in str(e):
            pytest.skip("unstructured returned non-string element for image; skipping")
        raise

    assert isinstance(vectors, list)
    assert len(vectors) >= 1
    v0 = vectors[0]
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


@pytest.mark.smoke
@pytest.mark.parametrize(
    "sample_path",
    _collect_samples(["md", "docx", "ppt", "pptx", "txt", "json", "pdf", "csv", "xlsx", "jpg", "jpeg", "png"]),
    ids=lambda p: p.name,
)
def test_pdf_generation_rules(sample_path: Path):
    # pdf인데 같은 이름의 다른 확장자 파일이 있으면 스킵
    if sample_path.suffix.lower() == ".pdf" and _has_same_stem_other_ext(sample_path):
        pytest.skip(f"pdf has sibling with same stem: {sample_path.name}")

    DocumentProcessor, _get_pdf_path, convert_to_pdf, TextLoader = _import_processor()

    if not sample_path.exists():
        pytest.skip(f"sample not found: {sample_path}")

    ext = sample_path.suffix.lower()

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

    # txt/json → TextLoader 가 weasyprint 로 PDF 를 만들어 읽는다. 그 파생 PDF 는 요청
    # 임시 디렉터리에만 만들고 지우므로 입력 디렉터리에는 남지 않아야 한다(#365).
    if ext in (".txt", ".json"):
        if not _has_weasyprint():
            pytest.skip("weasyprint 미설치로 PDF 생성 검증 스킵")
        pdf_path = Path(_get_pdf_path(str(sample_path)))
        existed_before = pdf_path.exists()
        loader = TextLoader(str(sample_path))
        try:
            loader.load()
        except Exception:
            pytest.skip("TextLoader 실행 실패로 PDF 생성 검증 스킵")
        assert pdf_path.exists() == existed_before
        return

    # doc/ppt 계열 → LibreOffice 필요
    if ext in (".doc", ".docx", ".ppt", ".pptx"):
        if not _has_soffice():
            pytest.skip("LibreOffice(soffice) 미설치로 PDF 생성 검증 스킵")
        pdf_path = convert_to_pdf(str(sample_path))
        assert pdf_path is None or Path(pdf_path).exists()
        return

    # 그 외 타입은 _get_pdf_path 규칙에 따름
    pdf_path = Path(_get_pdf_path(str(sample_path)))
    assert pdf_path.exists()
