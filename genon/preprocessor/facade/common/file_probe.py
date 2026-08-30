"""입력 파일의 포맷·암호화 사전 감지 (이슈 #278/#307).

facade 4종(intelligent/convert/parser/attachment)이 글자 하나 다르지 않게 복제해
두었던 판정 로직의 단일 사본이다. 표준 라이브러리와 선택적 서드파티(pypdf/olefile)
만 쓰고 docling 타입에는 의존하지 않는다.

facade 마다 구현이 갈리는 PDF 변환 계열(`convert_to_pdf`, `_has_any_pdf_converter`,
`_get_pdf_path`)은 여기 넣지 않았다. 그쪽은 facade 별 동작이 실제로 다르다.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Sequence

_log = logging.getLogger(__name__)

# 지원 포맷의 매직 헤더(allowlist). 각 값은 아래 공식 출처로 근거 확인 + 실제 샘플로 검증함.
#   - 정본 매직 DB: file/file(libmagic) magic/Magdir — 실제 본 모듈이 쓰는 python-magic의 DB.
#     (PDF=Magdir/pdf "%PDF-", PNG/GIF=Magdir/images, JPEG=Magdir/jpeg 0xffd8ff, ZIP=Magdir/msooxml "PK\3\4")
#   - 포맷 공식 스펙: PDF=ISO 32000(%PDF-), PNG=W3C PNG/RFC2083(89 50 4E 47 0D 0A 1A 0A),
#     ZIP=PKWARE APPNOTE(local file header 0x04034b50), OLE2/CFB=[MS-CFB] §2.2 Header(D0CF11E0A1B11AE1).
# zip(PK)=docx/xlsx/pptx/hwpx, OLE2(d0cf..)=hwp/doc/ppt/xls(레거시).
KNOWN_MAGIC_PREFIXES = (
    b"%PDF-",                                # pdf
    b"\x89PNG\r\n\x1a\n",                    # png
    b"\xff\xd8\xff",                         # jpeg/jpg
    b"GIF87a", b"GIF89a",                    # gif
    b"BM",                                    # bmp
    b"II*\x00", b"MM\x00*",                  # tiff
    b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08",  # zip 계열(ooxml/hwpx)
    b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",     # OLE2/CFB(hwp5/doc/ppt/xls)
    b"ID3",                                   # mp3(id3v2)
    b"RIFF",                                  # wav/avi/webp
    b"OggS",                                  # ogg
    b"fLaC",                                  # flac
    b"\x1f\x8b",                             # gzip
    b"7z\xbc\xaf\x27\x1c",                  # 7z
    b"Rar!\x1a\x07",                        # rar
    b"<?xml",                                 # xml
)

# 텍스트로 봐줄 수 없는 제어 바이트(탭/개행/CR/FF 제외). 텍스트 파일엔 거의 없음.
TEXT_ALLOWED_CTRL = {0x09, 0x0A, 0x0C, 0x0D}


def is_pdf(file_path: str) -> bool:
    """파일이 PDF 매직 헤더로 시작하는지 확인 (확장자 무관)."""
    try:
        with open(file_path, "rb") as f:
            return f.read(5) == b"%PDF-"
    except Exception:
        return False


def looks_like_text(head: bytes) -> bool:
    """csv/txt/json/md/html 등 매직넘버 없는 텍스트 파일인지 휴리스틱 판정.
    NUL 이 있거나 제어문자 비율이 높으면 바이너리(=텍스트 아님)."""
    if not head:
        return False
    # UTF-16/32 텍스트는 NUL 바이트가 흔하므로 BOM 이면 먼저 텍스트로 인정.
    if head.startswith((b"\xff\xfe", b"\xfe\xff")):  # UTF-16 LE/BE (UTF-32 BOM 도 이 prefix로 시작)
        return True
    if b"\x00" in head:
        return False
    ctrl = sum(
        1 for c in head if (c < 0x20 and c not in TEXT_ALLOWED_CTRL) or c == 0x7F
    )
    return (ctrl / len(head)) < 0.05


def is_encrypted_pdf(file_path: str) -> bool:
    """PDF /Encrypt(비밀번호/DRM 암호화) 여부. ISO 32000 기준, pypdf is_encrypted 사용."""
    try:
        from pypdf import PdfReader

        return bool(PdfReader(file_path).is_encrypted)
    except Exception:
        return False


def is_encrypted_office(file_path: str) -> bool:
    """암호화된 OOXML(docx/xlsx/pptx)은 OLE2 컨테이너의 'EncryptedPackage' 스트림으로
    저장된다(MS-OFFCRYPTO). olefile 로 그 스트림 존재를 확인."""
    try:
        import olefile

        if not olefile.isOleFile(file_path):
            return False
        ole = olefile.OleFileIO(file_path)
        try:
            return ole.exists("EncryptedPackage")
        finally:
            ole.close()
    except Exception:
        return False


def is_protected_hwp(file_path: str) -> bool:
    """암호화/배포용(DRM) HWP 감지. HWP 5.0 'FileHeader' 스트림(OLE2 내, 256B)의
    flags(offset 36, uint32 LE) bit1=password, bit2=distribution(배포용/DRM).
    이런 HWP 는 본문 스트림이 암호화돼 변환기가 정상 처리 못 함. (근거: HWP 5.0 스펙)"""
    try:
        import olefile
        import struct

        if not olefile.isOleFile(file_path):
            return False
        ole = olefile.OleFileIO(file_path)
        try:
            if not ole.exists("FileHeader"):
                return False
            data = ole.openstream("FileHeader").read()
            if len(data) < 40 or data[:17] != b"HWP Document File":
                return False
            flags = struct.unpack("<I", data[36:40])[0]
            return bool(flags & 0x02) or bool(flags & 0x04)  # password or distribution(DRM)
        finally:
            ole.close()
    except Exception:
        return False


def detect_unsupported_file(file_path: str) -> str | None:
    """입력 파일이 정상 처리 가능한지 판정(이슈 #278). 차단 사유 문자열 또는 정상이면 None.

    근거(공식):
    - 포맷 인식: 매직헤더 allowlist (file/file libmagic 정본 DB + 각 포맷 공식 스펙).
      KNOWN_MAGIC_PREFIXES 위 주석에 출처 명시. 확장자와 무관하게 실제 바이트로 본다.
    - 암호화 자체는 바이트 패턴으로 못 본다(암호문=고엔트로피 랜덤). 포맷별 구조로 판정:
      PDF=/Encrypt(pypdf is_encrypted, ISO 32000), Office=OLE2의 EncryptedPackage(MS-OFFCRYPTO),
      HWP=FileHeader flags(HWP 5.0 스펙).
    - Fasoo 등 독점 DRM은 표준 감지법이 없다 → 알려진 매직헤더에 안 맞고 텍스트도 아닌
      바이너리(=고엔트로피 garbage)로 걸러낸다.
    """
    try:
        with open(file_path, "rb") as f:
            head = f.read(512)
    except Exception:
        return None  # 읽기 실패는 여기서 판단 안 함(후속 단계에서 처리)
    if not head:
        return "빈 파일"

    pdf = head.startswith(b"%PDF-")
    is_ole2 = head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")
    # ── Layer 1: 알려진 포맷 매직헤더인가 ──
    known = (
        pdf
        or is_ole2
        or head[4:8] == b"ftyp"  # mp4/mov/m4a (ISO-BMFF, offset 4)
        or (len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0)  # mp3 frame sync
        or any(head.startswith(sig) for sig in KNOWN_MAGIC_PREFIXES)
    )
    if not known:
        if looks_like_text(head):
            return None  # csv/txt/json/md/html 등 텍스트 파일
        return "지원하지 않거나 손상된 파일(DRM 암호화 등)"

    # ── Layer 2: 알려진 포맷이지만 비밀번호/암호화된 경우 ──
    if pdf and is_encrypted_pdf(file_path):
        return "암호화된 PDF 문서"
    if is_ole2 and is_encrypted_office(file_path):
        return "암호화된 Office 문서"
    if is_ole2 and is_protected_hwp(file_path):
        return "암호화/배포용(DRM) HWP 문서"
    return None


def is_libreoffice_available() -> bool:
    """LibreOffice(soffice) 가 가용한지 확인 (이슈 #286).

    backend chain 을 LibreOffice 하나로 고정해 쓰는 호출부는 rhwp/pdf_sdk 와
    무관하게 이것만 따져야 정확하다. 빌드 시 INSTALL_LIBREOFFICE 를 끄면 False.
    가용성 판단 자체가 불가하면(import 실패 등) True 를 반환해 기존 동작을 유지한다.
    """
    try:
        from genon.preprocessor.converters.hwp_to_pdf.availability import (
            libreoffice_available,
        )
        return bool(libreoffice_available())
    except ImportError:
        # facade 단일 파일 실행 등으로 모듈 import 가 안 되는 경우 → 기존 동작 유지(가용 가정)
        return True
    except Exception as exc:
        # 가용성 probe 자체가 예기치 못하게 실패하면 로그만 남기고 파이프라인은 막지 않는다
        _log.warning(f"[is_libreoffice_available] LibreOffice 가용성 확인 실패: {exc}")
        return True


def has_any_pdf_converter() -> bool:
    """PDF 변환 backend(pdf_sdk / rhwp / libreoffice) 가 하나라도 가용한지 확인 (이슈 #286).

    빌드 시 INSTALL_LIBREOFFICE / INSTALL_RHWP 를 끄거나 PDF SDK 미포함(standard)이면
    변환 backend 가 0개가 될 수 있다. 이때 비-PDF 입력을 변환 시도하면 무조건 실패하므로,
    호출부에서 "PDF 로 직접 입력" 안내를 주기 위한 판별 헬퍼.
    가용성 판단 자체가 불가하면(import 실패 등) True 를 반환해 기존 동작을 유지한다.
    """
    try:
        from genon.preprocessor.converters.hwp_to_pdf.availability import (
            libreoffice_available,
            pdf_sdk_available,
            rhwp_available,
        )
        return bool(pdf_sdk_available() or rhwp_available() or libreoffice_available())
    except ImportError:
        # facade 단일 파일 실행 등으로 모듈 import 가 안 되는 경우 → 기존 동작 유지(가용 가정)
        return True
    except Exception as exc:
        # 가용성 probe 자체가 예기치 못하게 실패하면 로그만 남기고 파이프라인은 막지 않는다
        _log.warning(f"[has_any_pdf_converter] PDF 변환기 가용성 확인 실패: {exc}")
        return True


def get_pdf_path(file_path: str, convertible_extensions: Sequence[str]) -> str:
    """변환 가능한 확장자면 PDF 확장자로 바꾼 경로를, 아니면 원본 경로를 돌려준다.

    확장자 부분만 교체한다. 예전 attachment/convert 구현은 경로 문자열 전체에
    str.replace 를 확장자마다 순서대로 돌려서, '.ppt' 가 '.pptx' 보다 먼저 처리되는
    바람에 report.pptx → report.pdfx 가 됐고 디렉터리 이름에 확장자 문자열이 들어간
    경로(/data/a.txt.d/report.hwp)도 망가뜨렸다.
    """
    p = Path(file_path)
    if p.suffix.lower() in {str(e).lower() for e in convertible_extensions}:
        return str(p.with_suffix(".pdf"))
    return file_path
