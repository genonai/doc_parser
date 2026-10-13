from __future__ import annotations

import atexit
import logging
import os
import shutil
import subprocess
import tempfile
import threading
import unicodedata
from pathlib import Path
from typing import ClassVar

from .availability import libreoffice_available
from .base import BackendName

_log = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SEC = 600

# soffice 는 사용자 프로필 폴더에 락을 잡는다. 같은 파드의 프로세스들이 기본 프로필을 공유하면 동시 변환이
# 서로를 막거나 실패하므로 프로세스마다 프로필 하나를 둔다(#481 F-G). 호출마다 새로 만들면 매번 프로필
# 초기화(수 초)를 치르므로 프로세스당 한 번만 만든다.
_profile_lock = threading.Lock()
_profile_dir: str | None = None


def _process_profile_dir() -> str:
    global _profile_dir
    with _profile_lock:
        if _profile_dir is None or not os.path.isdir(_profile_dir):
            _profile_dir = tempfile.mkdtemp(prefix=f"soffice_profile_{os.getpid()}_")
            # 프로세스가 정상 종료할 때 지운다. os._exit 로 죽는 워커 재기동은 못 지우므로 이름에
            # pid 를 넣어 두어 잔여 폴더를 운영에서 구분할 수 있게 한다.
            atexit.register(shutil.rmtree, _profile_dir, ignore_errors=True)
        return _profile_dir


def _convert_arg_for(ext: str) -> str:
    if ext in ('.ppt', '.pptx'):
        return "pdf:impress_pdf_Export"
    if ext in ('.doc', '.docx'):
        return "pdf:writer_pdf_Export"
    if ext in ('.xls', '.xlsx', '.csv'):
        return "pdf:calc_pdf_Export"
    return "pdf"


class LibreOfficeConverter:
    name: ClassVar[BackendName] = "libreoffice"

    def is_available(self) -> bool:
        return libreoffice_available()

    def convert(self, file_path: str) -> str | None:
        try:
            in_path = Path(file_path).resolve()
            out_dir = in_path.parent
            pdf_path = in_path.with_suffix('.pdf')

            env = os.environ.copy()
            env.setdefault("LANG", "C.UTF-8")
            env.setdefault("LC_ALL", "C.UTF-8")

            convert_arg = _convert_arg_for(in_path.suffix.lower())

            try:
                in_path.name.encode('ascii')
                candidates = [in_path]
                tmp_dir = None
            except UnicodeEncodeError:
                tmp_dir = Path(tempfile.mkdtemp())
                ascii_name = unicodedata.normalize('NFKD', in_path.stem).encode('ascii', 'ignore').decode('ascii') or "file"
                ascii_copy = tmp_dir / f"{ascii_name}{in_path.suffix}"
                shutil.copy2(in_path, ascii_copy)
                candidates = [ascii_copy, in_path]

            timeout = int(os.environ.get("HWP_TO_PDF_TIMEOUT_SEC", DEFAULT_TIMEOUT_SEC))
            for cand in candidates:
                cmd = [
                    "soffice", "--headless",
                    f"-env:UserInstallation={Path(_process_profile_dir()).as_uri()}",
                    "--convert-to", convert_arg,
                    "--outdir", str(out_dir),
                    str(cand),
                ]
                proc = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=timeout)
                if proc.returncode == 0 and pdf_path.exists():
                    if tmp_dir:
                        shutil.rmtree(tmp_dir, ignore_errors=True)
                    return str(pdf_path)
                _log.warning(f"[hwp_to_pdf:libreoffice] stderr: {proc.stderr.strip()[:500]}")

            if tmp_dir:
                shutil.rmtree(tmp_dir, ignore_errors=True)
            return None
        except subprocess.TimeoutExpired as e:
            _log.error(f"[hwp_to_pdf:libreoffice] timeout after {e.timeout}s for {file_path}")
            return None
        except Exception as e:
            _log.error(f"[hwp_to_pdf:libreoffice] error: {e}")
            return None
