"""HWP SDK(convtext) 출력을 판정 로직 없이 그대로 DoclingDocument 로 옮기는 백엔드.

제목 판정·강등·목차 감지·배치용 표 풀기·중복 제거는 하지 않는다.
SDK 단락 하나가 TEXT 하나가 되고, 표는 SDK 가 준 HTML 문자열을 그대로 TEXT 로 넣는다.
"""

import json
import logging
import os
import re
import subprocess
import tempfile
import uuid
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from docling_core.types.doc import (
    BoundingBox,
    CoordOrigin,
    DocItemLabel,
    DoclingDocument,
    DocumentOrigin,
    ImageRef,
    ProvenanceItem,
    Size,
)
from PIL import Image, UnidentifiedImageError
from typing_extensions import override

from docling.backend.abstract_backend import DeclarativeDocumentBackend
from docling.datamodel.base_models import InputFormat
from docling.datamodel.document import InputDocument
from docling.exceptions import HwpConversionError

try:
    from wand.image import Image as WandImage

    WAND_AVAILABLE = True
except ImportError:
    WAND_AVAILABLE = False

_log = logging.getLogger(__name__)

# --- HWP용 MIME 타입 패치 ---
_HWP_MIMETYPES = [
    "application/vnd.hancom.hwp",
    "application/x-hwp",
    "application/vnd.hancom.hwpx",
    "application/hwp+zip",
    "application/vnd.hancom.hwpml",  # hml (hwp_sdk 260713+ 지원, 이슈 #323)
]

for _mime in _HWP_MIMETYPES:
    if _mime not in DocumentOrigin._extra_mimetypes:
        DocumentOrigin._extra_mimetypes.append(_mime)

# --- SDK 경로 및 환경 변수 등록 ---
# HWP_SDK_DIR 환경변수 override 우선(코드서빙처럼 docling 위치가 /app/.venv 밖일 때).
# 미설정 시 이 파일 위치 기준 hwp_sdk 폴더를 쓴다(표준 빌드 하위호환).
_env_sdk = os.environ.get("HWP_SDK_DIR")
SDK_DIR = Path(_env_sdk) if _env_sdk else Path(__file__).resolve().parent.parent.parent / "hwp_sdk"
SDK_PATH_STR = str(SDK_DIR)

if SDK_DIR.exists():
    if SDK_PATH_STR not in os.environ.get("PATH", ""):
        os.environ["PATH"] = f"{SDK_PATH_STR}:{os.environ.get('PATH', '')}"
    if SDK_PATH_STR not in os.environ.get("LD_LIBRARY_PATH", ""):
        os.environ["LD_LIBRARY_PATH"] = f"{SDK_PATH_STR}:{os.environ.get('LD_LIBRARY_PATH', '')}"
else:
    _log.warning(f"HWP SDK 경로를 찾을 수 없습니다: {SDK_PATH_STR}")

# dump_sdk_output=True 시 HWP SDK 중간 산출물이 저장되는 디렉터리
_SDK_DEBUG_OUTPUT_DIR = Path("/tmp/docparser_debug")


def _prov(page_no: int, length: int) -> ProvenanceItem:
    return ProvenanceItem(
        page_no=page_no,
        bbox=BoundingBox(l=0, t=0, r=1, b=1, coord_origin=CoordOrigin.BOTTOMLEFT),
        charspan=(0, length),
    )


class GenosHwpDocumentBackend(DeclarativeDocumentBackend):
    def __init__(self, in_doc: InputDocument, path_or_stream: Union[Path, BytesIO], **kwargs) -> None:
        super().__init__(in_doc, path_or_stream)

        self.include_wmf = kwargs.get("include_wmf", True)
        # include_wmf=True이면 save_images도 자동으로 True
        self.save_images = kwargs.get("save_images", True) or self.include_wmf
        # HWP SDK 중간 산출물(JSON, 이미지 등)을 디버깅 목적으로 보존할지 여부
        self.dump_sdk_output = kwargs.get("dump_sdk_output", False)

        self.valid = False
        self.current_img_dir: Optional[Path] = None

        # 소스 파일 준비
        self.original_path = Path(in_doc.file) if in_doc.file else None
        self.temp_input_path = None
        if isinstance(path_or_stream, BytesIO):
            suffix = self._infer_suffix(path_or_stream, in_doc)
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(path_or_stream.getbuffer())
                self.temp_input_path = Path(tmp.name)
            self.source_path = self.temp_input_path
        else:
            self.source_path = path_or_stream

        if self.source_path and self.source_path.exists():
            self.valid = True

    @staticmethod
    def _infer_suffix(stream: BytesIO, in_doc: InputDocument) -> str:
        # 1순위: in_doc.file에 확장자가 있으면 그대로 사용
        if in_doc.file:
            suffix = Path(in_doc.file).suffix
            if suffix:
                return suffix

        # 2순위: 스트림 매직 바이트로 판별
        header = stream.read(256)
        stream.seek(0)
        if header[:3] == b"\xef\xbb\xbf":  # UTF-8 BOM
            header = header[3:]

        if header[:2] == b"PK":  # ZIP 시그니처 → HWPX
            return ".hwpx"
        if header[:4] == b"\xd0\xcf\x11\xe0":  # OLE 시그니처 → HWP
            return ".hwp"
        if header[:5] == b"<?xml" and b"HWPML" in header:  # HWPML XML → HML
            return ".hml"

        # 3순위: InputFormat으로 폴백
        if getattr(in_doc, "format", None) == InputFormat.XML_HWPX:
            return ".hwpx"

        return ".hwp"

    @override
    def is_valid(self) -> bool:
        return self.valid

    @classmethod
    @override
    def supported_formats(cls) -> set[InputFormat]:
        return {InputFormat.HWP, InputFormat.XML_HWPX}

    @classmethod
    @override
    def supports_pagination(cls) -> bool:
        return True

    def convert(self) -> DoclingDocument:
        if not self.is_valid():
            raise RuntimeError("Invalid HWP/HWPX document")

        # 문서의 origin 정보
        file_ext = self.source_path.suffix.lower()
        if file_ext == ".hwpx":
            mimetype = "application/vnd.hancom.hwpx"
        elif file_ext == ".hwp":
            mimetype = "application/x-hwp"
        elif file_ext == ".hml":
            mimetype = "application/vnd.hancom.hwpml"
        else:
            mimetype = "application/octet-stream"

        try:
            bin_hash = int(self.document_hash, 16) & ((1 << 64) - 1) if hasattr(self, "document_hash") else 0
        except (ValueError, TypeError):
            bin_hash = 0

        origin = DocumentOrigin(
            filename=self.source_path.name or "file",
            mimetype=mimetype,
            binary_hash=bin_hash,
        )
        doc = DoclingDocument(name=self.source_path.stem or "file", origin=origin)

        # 작업 디렉토리 결정 (임시 vs 영구)
        if self.dump_sdk_output:
            base = self.original_path or self.source_path
            # BytesIO 입력은 파일명이 고유하지 않을 수 있으므로 UUID 접미사로 충돌 방지
            if self.temp_input_path is not None:
                subdir_name = f"{base.stem}_{uuid.uuid4().hex[:8]}"
            else:
                subdir_name = base.stem
            work_dir = _SDK_DEBUG_OUTPUT_DIR / subdir_name / "hwp_sdk_result"
            work_dir.mkdir(parents=True, exist_ok=True)
            temp_dir_context = None
        else:
            temp_dir_context = tempfile.TemporaryDirectory()
            work_dir = Path(temp_dir_context.name)

        try:
            json_out = work_dir / "output.json"
            info_out = work_dir / "output.info"
            img_dir = work_dir / "images"
            img_dir.mkdir(exist_ok=True)

            img_path_str = str(img_dir)
            if not img_path_str.endswith("/"):
                img_path_str += "/"

            # SDK 실행
            cmd = [
                "convtext",
                str(self.source_path.resolve()),
                str(json_out.resolve()),
                str(info_out.resolve()),
                img_path_str,
            ]
            try:
                subprocess.run(
                    cmd,
                    capture_output=True,
                    check=True,
                    text=True,
                    cwd=str(SDK_DIR),
                    timeout=600,
                )
            except subprocess.TimeoutExpired as e:
                raise HwpConversionError(f"SDK 실행 타임아웃 (600초 초과): {self.source_path.name}") from e
            except subprocess.CalledProcessError as e:
                raise HwpConversionError(
                    f"SDK 실행 실패 (exit code={e.returncode}): {self.source_path.name}\n" f"stderr: {e.stderr}"
                ) from e

            # .info 로 페이지 정보 설정
            try:
                self._setup_pages(doc, info_out)
            except Exception as e:
                raise HwpConversionError(f"페이지 정보 로드 실패: {self.source_path.name}") from e

            # SDK 결과 읽기. SDK 출력은 두 가지 비정상 케이스가 있어 줄 단위 json.loads 로는 깨진다.
            #   (a) 긴 base64 value 에 줄바꿈이 끼어 한 record 가 여러 물리 줄에 걸침
            #   (b) 표 셀 HTML 안에 <latex value="..."/> 를 임베드하면서 inner " 를 escape 안 함
            # (b)는 정규화로 보정하고, (a)는 raw_decode 기반 stream 파싱으로 처리한다.
            hwp_data: List[List[Dict]] = []
            try:
                with open(json_out, "r", encoding="utf-8") as f:
                    text = f.read()
                text = self._normalize_sdk_json_text(text)
                decoder = json.JSONDecoder(strict=False)
                pos = 0
                n = len(text)
                while pos < n:
                    while pos < n and text[pos] in " \r\n\t":
                        pos += 1
                    if pos >= n:
                        break
                    try:
                        batch, end = decoder.raw_decode(text, pos)
                    except json.JSONDecodeError as e:
                        _log.warning(f"파싱 실패 (스킵): pos={pos} err={e}")
                        next_nl = text.find("\n", pos)
                        if next_nl == -1:
                            break
                        pos = next_nl + 1
                        continue
                    hwp_data.append(batch)
                    pos = end
            except OSError as e:
                raise HwpConversionError(f"SDK 결과 파일 읽기 실패: {json_out}") from e

            self.current_img_dir = img_dir
            self._walk_hwp_data(hwp_data, doc)
            self.current_img_dir = None

        finally:
            if temp_dir_context is not None:
                temp_dir_context.cleanup()

        return doc

    def _walk_hwp_data(self, data: List[List[Dict]], doc: DoclingDocument):
        for paragraph_items in data:
            if not paragraph_items:
                continue
            page_no = next((i.get("page", 1) for i in paragraph_items if "page" in i), 1)
            texts: List[str] = []
            # 자동 번호·글머리표(□, 1. 등)는 SDK 가 단락 속성 bullet 으로 따로 주므로 단락 첫 텍스트 앞에 붙인다.
            para = next((i for i in paragraph_items if str(i.get("item", "")).lower() == "para"), None)
            bullet = [str((para or {}).get("bullet", "") or "").strip()]

            def flush():
                text = "".join(texts).strip()
                texts.clear()
                if text and bullet[0]:
                    text = f"{bullet[0]} {text}"
                    bullet[0] = ""
                if text:
                    doc.add_text(label=DocItemLabel.TEXT, text=text, parent=doc.body, prov=_prov(page_no, len(text)))

            for item in paragraph_items:
                i_type = str(item.get("item", "")).lower()
                i_value = item.get("value", "") or ""
                if i_type == "text":
                    texts.append(i_value)
                elif i_type == "table":
                    flush()
                    if i_value.strip():
                        doc.add_text(
                            label=DocItemLabel.TEXT,
                            text=i_value.strip(),
                            parent=doc.body,
                            prov=_prov(page_no, len(i_value.strip())),
                        )
                elif i_type == "image":
                    flush()
                    self._handle_image(item, doc, page_no, parent=doc.body)
            flush()

    def _handle_image(self, item: Dict, doc: DoclingDocument, page_no: int, parent: Any):
        if not self.save_images:
            return

        # JSON에 적힌 값(이미지 경로)
        img_path = item.get("value", "")
        if not img_path or not os.path.exists(img_path):
            return

        # 매직 넘버 기반 유효성 검사 (XML/가짜 파일 방어)
        def is_really_image(file_path):
            signatures = [
                b"\x89PNG",  # PNG
                b"\xff\xd8\xff",  # JPEG
                b"GIF8",  # GIF
                b"BM",  # BMP
                b"RIFF",  # WebP (RIFF 컨테이너)
                b"\x00\x00\x01\x00",  # ICO
                b"\xd7\xcd\xc6\x9a",  # WMF
                b"\x01\x00\x00\x00",  # EMF
                b"II*\x00",  # TIFF (little-endian)
                b"MM\x00*",  # TIFF (big-endian)
            ]
            try:
                with open(file_path, "rb") as f:
                    header = f.read(4)
                return any(header.startswith(s) for s in signatures)
            except Exception:
                return False

        if not is_really_image(img_path):
            _log.warning(f"유효하지 않은 이미지 데이터(XML 등 가능성): {os.path.basename(img_path)}")
            return

        pil_image = None

        # Pillow -> Wand 단계별 시도 (WMF/EMF 구제)
        try:
            pil_image = Image.open(img_path)
            pil_image.load()  # 강제 로드하여 오류 조기 감지
        except (UnidentifiedImageError, OSError):
            if self.include_wmf and WAND_AVAILABLE:
                try:
                    with WandImage(filename=img_path) as wand_img:
                        wand_img.format = "png"
                        pil_image = Image.open(BytesIO(wand_img.make_blob()))
                except Exception as e:
                    _log.error(f"Wand 변환 실패: {e}")
            else:
                _log.warning(f"Pillow 실패, WMF/EMF 복구 미시도 (include_wmf={self.include_wmf}): {img_path}")

        prov = _prov(page_no, 0)
        if pil_image:
            doc.add_picture(
                parent=parent,
                image=ImageRef.from_pil(image=pil_image, dpi=72),
                caption=None,
                prov=prov,
            )
        else:
            # Pillow + Wand 모두 실패 시 빈 플레이스홀더라도 추가해 문서 구조 보존
            _log.error(f"이미지 로드 완전 실패, 플레이스홀더 추가: {os.path.basename(img_path)}")
            doc.add_picture(parent=parent, caption=None, prov=prov)

    # HWP SDK 의 비정상 출력 보정용.
    # 패턴: <latex value="<base64, 줄바꿈/공백 가능>"/>  (속성값 안의 " 가 escape 없이 등장)
    # JSON 문자열 안에 들어오면 outer string 이 그 시점에서 종료된 것으로 파싱돼 record 가 손실된다.
    # base64 charset(A-Z, a-z, 0-9, +, /, =)과 공백만 허용해 다른 HTML 속성을 오인하지 않는다.
    _LATEX_ATTR_PATTERN = re.compile(
        r'<latex\s+value="([A-Za-z0-9+/=\s]*?)"\s*/?>',
        flags=re.DOTALL,
    )

    @classmethod
    def _normalize_sdk_json_text(cls, text: str) -> str:
        """SDK 결과 JSON 에 임베드된 <latex value="..."/> 속성의 inner " 를 escape 하고
        base64 안의 공백/줄바꿈을 제거해, JSON 파서가 outer 문자열을 끝까지 읽을 수 있게 한다.
        """

        def _repl(m: "re.Match[str]") -> str:
            b64 = re.sub(r"\s+", "", m.group(1))
            return f'<latex value=\\"{b64}\\"/>'

        return cls._LATEX_ATTR_PATTERN.sub(_repl, text)

    def _setup_pages(self, doc: DoclingDocument, info_path: Path):
        """.info 파일을 읽어 DoclingDocument 에 페이지 정보를 설정한다."""

        # 단위 변환 상수: 1cm = 28.3465pt (72 DPI 기준)
        CM_TO_PT = 28.3465

        try:
            with open(info_path, "r", encoding="utf-8") as f:
                info_data = json.load(f)

            page_info_list = info_data.get("page_info", [])

            if page_info_list:
                for p in page_info_list:
                    p_no = p.get("page")
                    w_pt = round(float(p.get("width", 21.00)) * CM_TO_PT)
                    h_pt = round(float(p.get("height", 29.70)) * CM_TO_PT)
                    doc.pages[p_no] = doc.add_page(page_no=p_no, size=Size(width=w_pt, height=h_pt))
            else:
                raise ValueError("page_info is empty")

        except Exception as e:
            # 오류가 나거나 정보가 없는 경우 기본 1페이지(A4)
            _log.warning(f"페이지 정보 로드 실패({e}). 기본 1페이지로 설정합니다.")
            doc.pages[1] = doc.add_page(page_no=1, size=Size(width=595, height=842))

    @override
    def unload(self):
        if self.temp_input_path and self.temp_input_path.exists():
            try:
                os.remove(self.temp_input_path)
            except Exception:
                pass
        self.temp_input_path = None
        super().unload()
