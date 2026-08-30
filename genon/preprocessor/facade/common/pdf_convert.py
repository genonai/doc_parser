"""비-PDF 입력을 PDF 로 변환하는 공용 진입점.

실제 변환은 `genon.preprocessor.converters.hwp_to_pdf` 의 backend chain 이 한다.
여기에는 facade 가 공유하는 두 가지만 있다.

  1. 입력 확장자와 use_pdf_sdk 로 backend 순서를 정하는 규칙
  2. 이슈 #286 — 변환 backend 가 하나도 없으면 시도 자체가 무의미하므로,
     PDF 직접 입력을 안내하는 warning 을 남기고 None 을 돌려주는 사전 체크

facade 는 이 모듈을 호출만 한다. 5개 facade 에 같은 chain 규칙이 복제돼
드리프트가 쌓였던 자리다(intelligent/chunking 은 #286 사전 체크가 빠져 있었고,
parser 는 backend 모듈이 아니라 soffice 를 직접 호출해 타임아웃이 없었다).
"""

from __future__ import annotations

import logging
import os

from genon.preprocessor.facade.common import file_probe as fp

_log = logging.getLogger(__name__)

_HWP_EXTENSIONS = (".hwp", ".hwpx")


def resolve_backend_order(file_path: str, use_pdf_sdk: bool = True) -> list[str]:
    """입력 확장자와 use_pdf_sdk 로 backend 시도 순서를 정한다.

    chain (HWP/HWPX 입력):
      use_pdf_sdk=True  → pdf_sdk → rhwp → libreoffice
      use_pdf_sdk=False → rhwp → libreoffice
    chain (그 외 입력, 예: docx/pptx):
      use_pdf_sdk=True  → pdf_sdk → libreoffice
      use_pdf_sdk=False → libreoffice

    rhwp 는 HWP/HWPX 전용이라 비-HWP 입력에는 chain 에 들어가지 않는다.
    """
    is_hwp = os.path.splitext(file_path)[1].lower() in _HWP_EXTENSIONS
    if use_pdf_sdk:
        return ["pdf_sdk", "rhwp", "libreoffice"] if is_hwp else ["pdf_sdk", "libreoffice"]
    return ["rhwp", "libreoffice"] if is_hwp else ["libreoffice"]


def convert_to_pdf(
    file_path: str,
    *,
    use_pdf_sdk: bool = True,
    libreoffice_only: bool = False,
) -> str | None:
    """PDF 변환을 시도한다. 실패해도 예외를 던지지 않고 None 을 반환한다.

    libreoffice_only=True 면 backend 순서를 ["libreoffice"] 로 고정하고 가용성도
    LibreOffice 만 따진다. rhwp/pdf_sdk 를 쓰지 않는 facade 용이다.
    """
    from genon.preprocessor.converters.hwp_to_pdf import convert_hwp_to_pdf

    if libreoffice_only:
        order = ["libreoffice"]
        available = fp.is_libreoffice_available()
        label = "LibreOffice"
    else:
        order = resolve_backend_order(file_path, use_pdf_sdk)
        available = fp.has_any_pdf_converter()
        label = "rhwp/LibreOffice/PDF SDK"

    if not available:
        _log.warning(
            f"[convert_to_pdf] PDF 변환기({label})가 설치되어 있지 않습니다 "
            f"(이슈 #286). '{os.path.basename(file_path)}' 변환을 건너뜁니다. PDF 로 변환된 "
            "파일을 입력하거나, 변환기를 포함해 전처리기 이미지를 다시 빌드하세요 (genon/README.md 참고)."
        )
        return None

    return convert_hwp_to_pdf(file_path, order=order)
