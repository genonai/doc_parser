import os
import shutil
import subprocess
import tempfile
import unicodedata
from pathlib import Path

from .base_converter import BaseFormatConverter

# LibreOffice로 변환 가능한 확장자
SUPPORTED_EXTENSIONS = {".hwp", ".hwpx", ".doc", ".docx", ".ppt", ".pptx", ".txt", ".md"}


class LibreOfficeConverter(BaseFormatConverter):
    def convert(self, src: str) -> str:
        in_path = Path(src).resolve()
        ext = in_path.suffix.lower()

        assert ext in SUPPORTED_EXTENSIONS, f"LibreOfficeConverter가 지원하지 않는 확장자: {ext}"

        out_dir = in_path.parent
        pdf_path = in_path.with_suffix(".pdf")

        if ext in (".ppt", ".pptx"):
            convert_arg = "pdf:impress_pdf_Export"
        elif ext in (".doc", ".docx"):
            convert_arg = "pdf:writer_pdf_Export"
        else:
            convert_arg = "pdf"

        env = os.environ.copy()
        env.setdefault("LANG", "C.UTF-8")
        env.setdefault("LC_ALL", "C.UTF-8")

        # 비ASCII 파일명 대비 임시 ASCII 복사본 시도
        try:
            in_path.name.encode("ascii")
            candidates = [in_path]
            tmp_dir = None
        except UnicodeEncodeError:
            tmp_dir = Path(tempfile.mkdtemp())
            ascii_stem = (
                unicodedata.normalize("NFKD", in_path.stem).encode("ascii", "ignore").decode("ascii") or "file"
            )
            ascii_copy = tmp_dir / f"{ascii_stem}{ext}"
            shutil.copy2(in_path, ascii_copy)
            candidates = [ascii_copy, in_path]

        try:
            for cand in candidates:
                cmd = ["soffice", "--headless", "--convert-to", convert_arg, "--outdir", str(out_dir), str(cand)]
                proc = subprocess.run(cmd, env=env, capture_output=True, text=True)
                if proc.returncode == 0 and pdf_path.exists():
                    return str(pdf_path)
        finally:
            if tmp_dir:
                shutil.rmtree(tmp_dir, ignore_errors=True)

        if pdf_path.exists():
            return str(pdf_path)

        raise RuntimeError(f"LibreOffice 변환 실패: {src}")
