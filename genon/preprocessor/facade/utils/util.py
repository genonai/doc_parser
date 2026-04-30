import os
from pathlib import Path

import shutil
import unicodedata
import tempfile
import subprocess

from markdown2 import markdown

try:
    from weasyprint import HTML
except ImportError:
    print("Warning: WeasyPrint could not be imported. PDF conversion features will be disabled.")
    HTML = None


def convert_to_pdf(file_path: str) -> str | None:
    """
    LibreOffice로 PDF 변환을 시도한다.
    실패해도 예외를 던지지 않고 None을 반환한다.
    """
    try:
        in_path = Path(file_path).resolve()
        out_dir = in_path.parent
        pdf_path = in_path.with_suffix(".pdf")

        # headless에서 UTF-8 locale 보장
        env = os.environ.copy()
        env.setdefault("LANG", "C.UTF-8")
        env.setdefault("LC_ALL", "C.UTF-8")

        # 확장자에 따라 필터(특히 .ppt는 impress 필터)
        ext = in_path.suffix.lower()
        if ext in (".ppt", ".pptx"):
            convert_arg = "pdf:impress_pdf_Export"
        elif ext in (".doc", ".docx"):
            convert_arg = "pdf:writer_pdf_Export"
        elif ext in (".xls", ".xlsx", ".csv"):
            convert_arg = "pdf:calc_pdf_Export"
        else:
            convert_arg = "pdf"

        # 비ASCII 파일명 이슈 대비 임시 ASCII 파일명 복사본 시도
        try:
            in_path.name.encode("ascii")
            candidates = [in_path]
            tmp_dir = None
        except UnicodeEncodeError:
            tmp_dir = Path(tempfile.mkdtemp())
            ascii_name = unicodedata.normalize("NFKD", in_path.stem).encode("ascii", "ignore").decode("ascii") or "file"
            ascii_copy = tmp_dir / f"{ascii_name}{in_path.suffix}"
            shutil.copy2(in_path, ascii_copy)
            candidates = [ascii_copy, in_path]

        for cand in candidates:
            cmd = [
                "soffice",
                "--headless",
                "--convert-to",
                convert_arg,
                "--outdir",
                str(out_dir),
                str(cand),
            ]
            proc = subprocess.run(cmd, env=env, capture_output=True, text=True)
            if proc.returncode == 0 and pdf_path.exists():
                # 성공
                if tmp_dir:
                    shutil.rmtree(tmp_dir, ignore_errors=True)
                return str(pdf_path)
            # 실패해도 계속 시도 (로그만 찍고 무시)
            print(f"[convert_to_pdf] stderr: {proc.stderr.strip()}")

        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        return None
    except Exception as e:
        # 어떤 에러든 삼키고 None 반환
        print(f"[convert_to_pdf] error: {e}")
        return None


def _get_pdf_path(file_path: str, CONVERTIBLE_EXTENSIONS: list) -> str:
    """
    다양한 파일 확장자를 PDF 확장자로 변경하는 공통 함수

    Args:
        file_path (str): 원본 파일 경로

    Returns:
        str: PDF 확장자로 변경된 파일 경로
    """
    pdf_path = file_path
    for ext in CONVERTIBLE_EXTENSIONS:
        pdf_path = pdf_path.replace(ext, ".pdf")
    return pdf_path


def get_real_file_type(file_path: str) -> str:
    """파일 확장자가 아닌 실제 내용으로 파일 타입 판단"""
    with open(file_path, "rb") as f:
        header = f.read(8)
    if header.startswith(b"%PDF-"):
        return "pdf"
    elif header.startswith(b"\x89PNG"):
        return "png"
    elif header.startswith(b"\xff\xd8\xff"):
        return "jpg"

    # 매직 헤더로 판단할 수 없으면 확장자 사용
    return os.path.splitext(file_path)[-1].lower()


def convert_md_to_pdf(md_path):
    """Markdown 파일을 PDF로 변환"""
    install_packages(["chardet"])
    import chardet

    pdf_path = md_path.replace(".md", ".pdf")
    with open(md_path, "rb") as f:
        raw_file = f.read()
    candidates = ["utf-8", "utf-8-sig"]
    try:
        det = (chardet.detect(raw_file) or {}).get("encoding") or ""
        # chardet가 ascii/unknown이면 무시. 그 외면 후보에 추가
        if det and det.lower() not in ("ascii", "unknown"):
            if det.lower() not in [c.lower() for c in candidates]:
                candidates.append(det)
    except Exception:
        pass
    candidates += ["cp949", "euc-kr", "iso-8859-1", "latin-1"]
    md_content = None
    for enc in candidates:
        try:
            md_content = raw_file.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if md_content is None:
        md_content = raw_file.decode("utf-8", errors="replace")

    html_content = markdown(md_content)
    if HTML:
        HTML(string=html_content).write_pdf(pdf_path)
    return pdf_path
