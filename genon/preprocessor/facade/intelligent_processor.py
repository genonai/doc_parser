from __future__ import annotations

import json
import os
import logging
import math, bisect
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

from collections import defaultdict
from datetime import datetime
from typing import Optional, Iterable, Any, List, Dict, Tuple

from fastapi import Request

_log = logging.getLogger(__name__)

# Genos 웹 UI 환경은 facade 코드를 단일 파일(preprocessor.py)로 처리하므로
# 다른 facade 파일에서 import 가 깨진다. 따라서 convert_to_pdf 는
# attachment_processor / convert_processor 와 동일하게 자체 정의한다.
import shutil
import subprocess
import tempfile
import unicodedata


def convert_to_pdf(file_path: str, use_pdf_sdk: bool = True) -> str | None:
    """
    PDF 변환을 시도한다. use_pdf_sdk=True면 PDF SDK, False면 LibreOffice.
    실패해도 예외를 던지지 않고 None을 반환한다.
    """
    if use_pdf_sdk:
        return _convert_to_pdf_sdk(file_path)
    return _convert_to_pdf_libreoffice(file_path)


def _convert_to_pdf_sdk(file_path: str) -> str | None:
    sdk_out_dir: Path | None = None
    keep_out_dir = False  # 폴백 경로 리턴 시 임시 디렉토리 보존
    try:
        # 1. PDF_SDK_HOME 환경변수 (도커 환경) → 2. repo_root/pdf_sdk (로컬 실행)
        pdf_sdk_home = os.environ.get(
            "PDF_SDK_HOME",
            str(Path(__file__).resolve().parent.parent.parent.parent / "pdf_sdk"),
        )
        binary = os.path.join(pdf_sdk_home, "pdfConverter")
        fonts_dir = os.path.join(pdf_sdk_home, "fonts")
        moduledata = os.path.join(pdf_sdk_home, "moduledata")
        font_cache = os.path.join(pdf_sdk_home, "font_cache")
        os.makedirs(font_cache, exist_ok=True)

        in_path = Path(file_path).resolve()
        # NFS 쓰기 권한 / 출력 파일명 추측 미스매치 회피를 위해 임시 디렉토리에 출력
        sdk_out_dir = Path(tempfile.mkdtemp(prefix="pdfsdk_out_"))

        _log.info(
            f"[convert_to_pdf:sdk] preflight: "
            f"binary_exists={os.path.exists(binary)} "
            f"binary_x={os.access(binary, os.X_OK)} "
            f"fonts_dir_exists={os.path.exists(fonts_dir)} "
            f"moduledata_exists={os.path.exists(moduledata)} "
            f"input_exists={in_path.exists()} "
            f"input_size={in_path.stat().st_size if in_path.exists() else 'N/A'} "
            f"sdk_out_dir={sdk_out_dir}"
        )

        with tempfile.TemporaryDirectory() as tmp:
            patched_conf = _patch_fontconfig(fonts_dir, font_cache, tmp)

            env = os.environ.copy()
            env["LD_LIBRARY_PATH"] = f"{moduledata}:{env.get('LD_LIBRARY_PATH', '')}"
            env["FONTCONFIG_FILE"] = patched_conf
            env["FONTCONFIG_PATH"] = fonts_dir
            env.setdefault("LANG", "C.UTF-8")
            env.setdefault("LC_ALL", "C.UTF-8")

            cmd = [
                binary,
                "-i", str(in_path),
                "-o", str(sdk_out_dir),
                "-t", tmp,
                "-f", fonts_dir,
                "-e", "-1",
                "-p", "1",
            ]
            _log.info(f"[convert_to_pdf:sdk] cmd: {cmd}")
            proc = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=600)

            produced = sorted(p for p in sdk_out_dir.glob("*") if p.is_file())
            _log.info(
                f"[convert_to_pdf:sdk] returncode={proc.returncode} "
                f"produced_files={[p.name for p in produced]}"
            )
            _log.info(f"[convert_to_pdf:sdk] stdout={proc.stdout!r}")
            _log.info(f"[convert_to_pdf:sdk] stderr={proc.stderr!r}")

            pdf_files = [p for p in produced if p.suffix.lower() == ".pdf"]
            if proc.returncode == 0 and pdf_files:
                produced_pdf = pdf_files[0]
                target_pdf = in_path.with_suffix('.pdf')
                try:
                    shutil.copy2(produced_pdf, target_pdf)
                    _log.info(f"[convert_to_pdf:sdk] success → {target_pdf}")
                    return str(target_pdf)
                except OSError as e:
                    _log.warning(
                        f"[convert_to_pdf:sdk] target copy failed ({e}); "
                        f"using temp path: {produced_pdf}"
                    )
                    keep_out_dir = True
                    return str(produced_pdf)

            _log.warning(
                f"[convert_to_pdf:sdk] FAILED — "
                f"returncode={proc.returncode}, produced={[p.name for p in produced]}"
            )
            return None
    except subprocess.TimeoutExpired as e:
        _log.error(f"[convert_to_pdf:sdk] timeout after {e.timeout}s for {file_path}")
        return None
    except Exception as e:
        _log.error(f"[convert_to_pdf:sdk] error: {e}", exc_info=True)
        return None
    finally:
        if sdk_out_dir is not None and sdk_out_dir.exists() and not keep_out_dir:
            shutil.rmtree(sdk_out_dir, ignore_errors=True)


def _patch_fontconfig(fonts_dir: str, font_cache: str, tmp_dir: str) -> str:
    """원본 fonts_gen.conf의 <dir>/<cachedir> 경로를 현재 SDK 위치 기준으로 치환한 임시 파일 경로 반환."""
    import re
    src = os.path.join(fonts_dir, "fonts_gen.conf")
    dst = os.path.join(tmp_dir, "fonts.conf")
    with open(src, "r", encoding="utf-8") as f:
        conf = f.read()
    conf = re.sub(r"<dir>[^<]*</dir>", f"<dir>{fonts_dir}</dir>", conf, count=1)
    conf = re.sub(r"<cachedir>[^<]*</cachedir>", f"<cachedir>{font_cache}</cachedir>", conf, count=1)
    with open(dst, "w", encoding="utf-8") as f:
        f.write(conf)
    return dst


def _convert_to_pdf_libreoffice(file_path: str) -> str | None:
    try:
        in_path = Path(file_path).resolve()
        out_dir = in_path.parent
        pdf_path = in_path.with_suffix('.pdf')

        env = os.environ.copy()
        env.setdefault("LANG", "C.UTF-8")
        env.setdefault("LC_ALL", "C.UTF-8")

        ext = in_path.suffix.lower()
        if ext in ('.ppt', '.pptx'):
            convert_arg = "pdf:impress_pdf_Export"
        elif ext in ('.doc', '.docx'):
            convert_arg = "pdf:writer_pdf_Export"
        elif ext in ('.xls', '.xlsx', '.csv'):
            convert_arg = "pdf:calc_pdf_Export"
        else:
            convert_arg = "pdf"

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

        for cand in candidates:
            cmd = [
                "soffice", "--headless",
                "--convert-to", convert_arg,
                "--outdir", str(out_dir),
                str(cand)
            ]
            proc = subprocess.run(cmd, env=env, capture_output=True, text=True)
            if proc.returncode == 0 and pdf_path.exists():
                if tmp_dir:
                    shutil.rmtree(tmp_dir, ignore_errors=True)
                return str(pdf_path)
            _log.warning(f"[convert_to_pdf:libreoffice] stderr: {proc.stderr.strip()}")

        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        return None
    except Exception as e:
        _log.error(f"[convert_to_pdf:libreoffice] error: {e}")
        return None


def _is_pdf(file_path: str) -> bool:
    """파일이 PDF 매직 헤더로 시작하는지 확인 (확장자 무관)."""
    try:
        with open(file_path, "rb") as f:
            return f.read(5) == b"%PDF-"
    except Exception:
        return False


def _as_int_flag(value: Any, default: int = 0) -> int:
    """1/0, bool, 문자열 flag를 1 또는 0으로 정규화."""
    if value is None:
        return default
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, (int, float)):
        return 1 if int(value) == 1 else 0
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "y", "yes", "on"}:
            return 1
        if normalized in {"0", "false", "n", "no", "off"}:
            return 0
    return default


def _as_bool_flag(value: Any, default: bool = False) -> bool:
    """1/0, bool, 문자열 flag를 bool로 정규화."""
    return _as_int_flag(value, 1 if default else 0) == 1


def _as_positive_int(value: Any, default: int) -> int:
    """양의 정수 파라미터를 안전하게 정규화."""
    try:
        parsed = int(value)
        return parsed if parsed > 0 else default
    except (TypeError, ValueError):
        return default


def _as_nonnegative_int(value: Any, default: int) -> int:
    """0 이상의 정수 파라미터를 안전하게 정규화."""
    try:
        parsed = int(value)
        return parsed if parsed >= 0 else default
    except (TypeError, ValueError):
        return default


def _first_kwarg(kwargs: dict, *names: str, default: Any = None) -> Any:
    """여러 alias 중 먼저 넘어온 값을 반환."""
    for name in names:
        if name in kwargs:
            return kwargs.get(name)
    return default


def _env_first(*names: str, default: str = "") -> str:
    """여러 환경변수 후보 중 비어 있지 않은 첫 값을 반환."""
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return default


# 운영 환경에서는 OPENROUTER_API_KEY / TABLE_REFINER_BEARER_TOKEN env var 로 주입.
# 기본값은 빈 문자열 (시크릿 노출 방지를 위해 코드에 평문 보관하지 않음).
DEFAULT_OPENROUTER_API_KEY = ""
DEFAULT_TABLE_REFINER_BEARER_TOKEN = ""


# docling imports

from docling.backend.docling_parse_v4_backend import DoclingParseV4DocumentBackend
from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend
from docling.datamodel.base_models import InputFormat
from docling.pipeline.simple_pipeline import SimplePipeline
# from docling.datamodel.document import ConversionStatus
from docling.datamodel.pipeline_options import (
    AcceleratorDevice,
    AcceleratorOptions,
    # OcrEngine,
    # PdfBackend,
    LayoutModelType,
    PdfPipelineOptions,
    TableFormerMode,
    TableStructureModelType,
    PipelineOptions,
    PaddleOcrOptions,
)

from docling.document_converter import (
    DocumentConverter,
    PdfFormatOption,
    FormatOption,
    HwpxFormatOption,
)
from docling.backend.genos_hwp_backend import GenosHwpDocumentBackend
from docling.backend.hwp_backend import HwpDocumentBackend
from docling.backend.xml.hwpx_backend import HwpxDocumentBackend
from docling.datamodel.pipeline_options import DataEnrichmentOptions, PictureDescriptionApiOptions
from docling.utils.document_enrichment import enrich_document, check_document, DocumentEnrichmentUtils
from docling.datamodel.document import ConversionResult
from docling_core.transforms.chunker import (
    BaseChunk,
    BaseChunker,
    DocChunk,
    DocMeta,
)
from docling_core.types import DoclingDocument

from pandas import DataFrame
import asyncio
from docling_core.types import DoclingDocument as DLDocument
from docling_core.types.doc.document import (
    DocumentOrigin,
    LevelNumber,
    ListItem,
    CodeItem,
    ContentLayer,
)
from docling_core.types.doc.labels import DocItemLabel
from docling_core.types.doc import (
    BoundingBox,
    DocItemLabel,
    DoclingDocument,
    DocumentOrigin,
    DocItem,
    PictureItem,
    SectionHeaderItem,
    TableItem,
    TextItem,
    PageItem,
    ProvenanceItem
)
from docling.datamodel.settings import settings

from collections import Counter
import re
import json
import warnings
from typing import Iterable, Iterator, Optional, Union

from pydantic import BaseModel, ConfigDict, PositiveInt, TypeAdapter, model_validator
from typing_extensions import Self
import base64
import fitz
import requests
import httpx

try:
    import semchunk
    from transformers import AutoTokenizer, PreTrainedTokenizerBase
except ImportError:
    raise RuntimeError(
        "Module requires 'chunking' extra; to install, run: "
        "`pip install 'docling-core[chunking]'`"
    )

try:
    from genos_utils import upload_files
except ImportError:
    upload_files = None

# ============================================
#
# Copyright IBM Corp. 2024 - 2024
# SPDX-License-Identifier: MIT
#

"""Chunker implementation leveraging the document structure."""

class TableRefiner:
    def __init__(self):
        serving_id = _as_positive_int(os.getenv("TABLE_REFINER_SERVING_ID"), 729)
        bearer_token = _env_first(
            "TABLE_REFINER_BEARER_TOKEN",
            "GENOS_BEARER_TOKEN",
            default=DEFAULT_TABLE_REFINER_BEARER_TOKEN,
        )
        genos_url = _env_first(
            "TABLE_REFINER_GENOS_URL",
            "GENOS_URL",
            default="https://genos.genon.ai",
        )
        self.llm = llm_serving(
            serving_id=serving_id,
            bearer_token=bearer_token,
            genos_url=genos_url,
        )

    def _debug(self, message: str, *args, level: str = "info"):
        rendered = message % args if args else message
        print(f"[table_refine] {rendered}", flush=True)
        log_func = getattr(_log, level, _log.info)
        log_func("[table_refine] " + message, *args)

    def load_documents(self, file_path):
        self.document = fitz.open(file_path)
        self._debug(
            "loaded document path=%s pages=%s",
            file_path,
            getattr(self.document, "page_count", "unknown"),
        )

    def cropping(self,
                 bbox: dict,
                 i_page: int,
                 clip_margin: float = 0.05,  # 기본적으로 5% 여백 추가 권장
                 zoom: float = 2.0):

        # 1. 페이지 로드
        page = self.document.load_page(i_page)

        # 2. 페이지의 실제 '표시 영역' 좌표 가져오기 (CropBox 기준)
        # page.rect는 (x0, y0, x1, y1)를 반환하며, x0, y0가 0이 아닐 수 있음
        page_rect = page.rect

        # 3. bbox 좌표 추출 (문자열일 경우 대비 float 변환)
        l, t, r, b = float(bbox['l']), float(bbox['t']), float(bbox['r']), float(bbox['b'])

        # 4. 좌표계 변환 (Bottom-Left -> Top-Left)
        # 대부분의 PDF/Vision 모델 좌표계 이슈는 여기서 발생합니다.
        coord_origin = bbox.get('coord_origin', 'BOTTOMLEFT')

        if coord_origin.upper() == 'BOTTOMLEFT':
            # 입력이 수학적 좌표(좌하단 0,0)인 경우 -> 시각적 좌표(좌상단 0,0)로 변환
            # PDF에서 y축은 아래에서 위로 증가하므로, 위아래를 뒤집어야 함
            # t(top)가 수치적으로 더 큼 (예: 0.9), b(bottom)가 작음 (예: 0.1)
            new_t = 1.0 - t  # 1.0 - 0.9 = 0.1 (화면 상단)
            new_b = 1.0 - b  # 1.0 - 0.1 = 0.9 (화면 하단)
            t, b = new_t, new_b

        # 이제 t, b는 모두 Top-Left 기준 (0이 맨 위, 1이 맨 아래)
        # 항상 작은 값이 top, 큰 값이 bottom이 되도록 정렬
        top = min(t, b)
        bottom = max(t, b)
        left = min(l, r)
        right = max(l, r)

        # 5. 여백(Padding) 추가 (잘림 방지 핵심)
        # clip_margin만큼 상하좌우를 넓힘
        if clip_margin > 0:
            width_padding = (right - left) * clip_margin
            height_padding = (bottom - top) * clip_margin

            left -= width_padding
            right += width_padding
            top -= height_padding
            bottom += height_padding

        # 6. 좌표 클램핑 (0.0 ~ 1.0 범위를 벗어나지 않도록)
        left = max(0.0, min(1.0, left))
        right = max(0.0, min(1.0, right))
        top = max(0.0, min(1.0, top))
        bottom = max(0.0, min(1.0, bottom))

        # 7. 실제 픽셀 좌표로 변환 (page_rect의 오프셋 고려)
        # 단순히 width를 곱하는 게 아니라, 시작점(x0, y0)에서 더해야 함
        abs_x0 = page_rect.x0 + (left * page_rect.width)
        abs_y0 = page_rect.y0 + (top * page_rect.height)
        abs_x1 = page_rect.x0 + (right * page_rect.width)
        abs_y1 = page_rect.y0 + (bottom * page_rect.height)

        # 8. Rect 생성
        rect = fitz.Rect(abs_x0, abs_y0, abs_x1, abs_y1)

        # 안전장치: Rect가 페이지 영역을 벗어나지 않도록 교집합 처리
        rect = rect & page_rect

        # 9. 이미지 추출
        mat = fitz.Matrix(zoom, zoom)

        # alpha=False로 해야 흰 배경이 나옴 (투명 배경일 경우 OCR 인식률 저하 가능성)
        pix = page.get_pixmap(matrix=mat, clip=rect, alpha=False)
        png_bytes = pix.tobytes(output="png")

        b64 = base64.b64encode(png_bytes).decode('ascii')
        return b64

    async def _refine_table(self, b64: str = None, original_table_text: str = None):

        system_prompt = """
당신은 'HTML 테이블 복원 전문가(Table Reconstruction Expert)'입니다.
입력으로 제공되는 테이블 이미지를 검색/RAG에 적합한 **읽기 쉬운 HTML fragment**로 변환하는 것이 역할입니다.

### 출력 형식 (절대 규칙)

1. 결과는 반드시 HTML fragment여야 합니다. Markdown table, 코드블록, ```html fence, 설명 문장은 절대 출력하지 않습니다.
2. 사용할 수 있는 태그는 `<p>`, `<h4>`, `<table>`, `<thead>`, `<tbody>`, `<tr>`, `<th>`, `<td>`, `<br>`입니다. 필요한 경우 `<th>`/`<td>`에는 `rowspan`, `colspan` 속성을 사용할 수 있습니다.
3. 표 데이터는 반드시 `<table>`로 출력합니다.
4. 셀 안 줄바꿈은 `<br>`로 표현합니다.
5. 셀 텍스트에 원본 파이프 기호(`|`)가 있으면 HTML에서는 그대로 두지 말고 가운뎃점(`·`)으로 치환합니다.

### 구조 복원 규칙

1. 표가 단순하면 하나의 HTML `<table>`로 출력합니다.
2. 표 안에 표가 들어간 중첩 표, 다단 헤더, 큰 rowspan/colspan, 페이지 이어짐 표라면 **하나의 큰 격자로 억지 변환하지 말고**, 문맥 `<p>` + 여러 개의 작은 `<table>`로 분해합니다.
3. 왼쪽/상단의 큰 병합셀은 반복 컬럼으로 만들지 말고 문맥 `<p>`로 분리합니다.
   - 예: `<p>조건: 대상 / 기간 / 처리방식</p>`
   - 예: `<p>공통 기준: A등급 이상 (단, 예외 기준 참조)</p>`
4. `세부내용`, `구분`처럼 여러 열을 덮는 상위 병합 헤더를 `세부내용`, `세부내용(하위1)`, `세부내용(하위2)`처럼 임의 반복하지 않습니다.
5. 내부 표의 실제 컬럼명이 보이면 그 컬럼명을 사용합니다.
   - 예: `상위구분`, `항목명`, `유형`, `기간`, `처리방식`, `대상`
   - 예: 이미지에 `구분`, `기간`, `방식`, `대상`이 보이면 그 의미를 유지합니다.
6. `[1] 기본형`, `[2] 확장형`처럼 표 내부의 섹션 구분자는 `<h4>`로 분리합니다.
7. 같은 값이 병합셀 때문에 여러 행/열에 반복되더라도 불필요하게 반복하지 않습니다. 반복 적용되는 값은 첫 행에만 쓰거나 문맥 `<p>`로 올립니다.
8. 셀의 텍스트는 생략하거나 요약하지 말고 그대로 재현합니다. 띄어쓰기, 단위, 괄호, 번호를 유지합니다.
9. 내용이 비어 있는 셀이 있다면 빈 `<td></td>` 또는 `<td>-</td>`로 유지하세요.
10. 각 HTML table 내부에서는 사람이 읽을 수 있는 열 구조를 유지합니다.
11. 결과에는 원본에 없는 `하위1`, `하위2`, `구분 1`, `구분 2`, `구분 3` 같은 임시 컬럼명을 만들지 않습니다.

### 빈 양식/체크리스트 표 보존 규칙

1. 날짜별 체크칸, 서명칸, 확인칸처럼 대부분의 셀이 비어 있는 양식 표도 원본 격자를 보존합니다.
2. `개인 위생`, `건강 생활`처럼 세로 병합된 좌측 구분과 각 점검항목은 반드시 HTML table 안에 포함합니다.
3. 검색과 렌더링 안정성을 위해 체크리스트/점검표 양식에서는 `rowspan`, `colspan`을 되도록 쓰지 말고 병합값을 각 행에 반복한 직사각형 표로 만듭니다.
4. 대각선 헤더가 있으면 임의로 대각선을 그리지 말고 `구분`, `점검항목`, 날짜 열들로 분리합니다.
5. 날짜별 빈 체크칸은 `<td></td>`로 보존합니다. 빈 칸이 많다는 이유로 날짜 열이나 체크칸을 생략하지 않습니다.
6. `합계`, `부모님 확인`, `서명`, `총계` 같은 빈 기입 행도 표 안에 별도 행으로 남깁니다.
7. 원본에 보이는 의미 있는 텍스트를 표 밖으로 빼거나 요약하지 않습니다.

### 중첩 표 권장 출력 예시

<p>조건: 대상 / 기간 / 처리방식</p>
<p>공통 기준: A등급 이상 (단, 예외 기준 참조)</p>

<h4>[1] 기본형</h4>
<table>
  <thead>
    <tr><th>상위구분</th><th>항목명</th><th>유형</th><th>기간</th><th>처리방식</th><th>대상</th></tr>
  </thead>
  <tbody>
    <tr><td>기본항목</td><td>항목 A</td><td>일반</td><td>1년</td><td>월별</td><td>전체</td></tr>
  </tbody>
</table>

<h4>[2] 확장형</h4>
<table>
  <thead>
    <tr><th>상위구분</th><th>항목명</th><th>유형</th><th>기간</th><th>처리방식</th><th>대상</th></tr>
  </thead>
  <tbody>
    <tr><td>추가항목</td><td>항목 B</td><td>선택</td><td>2년</td><td>일괄</td><td>일부</td></tr>
  </tbody>
</table>
"""
        system_prompt += """

### 복합 표 보존 추가 규칙

1. 한 셀 안에 짧은 상위 구분명과 긴 항목 목록이 함께 들어간 것처럼 보이면, 이미지의 선/여백/정렬을 기준으로 실제로 분리된 구조인지 판단합니다.
   - 분리된 구조가 명확하면 상위 구분명은 별도 셀로, 항목 목록은 별도 셀로 나누고 필요한 `rowspan`/`colspan`을 사용합니다.
   - 분리 근거가 약하면 임의의 열을 만들지 말고 원본에 가까운 단일 셀 구조를 유지합니다.
2. 헤더가 넓게 병합되어 보이는 경우, 하위 열 이름이 이미지에 보이면 병합 헤더와 하위 열을 함께 보존합니다.
   - 하위 열 이름이 보이지 않으면 `하위1`, `하위2`, `구분1`, `구분2` 같은 임시 열 이름을 만들지 않습니다.
3. 페이지가 이어지는 표에서는 이전 페이지의 반복되지 않은 병합 셀 맥락을 추정할 수 있지만, 보이는 표 구조와 기존 추출 텍스트에 근거가 있을 때만 보완합니다.
4. 항목 목록, 설명 문구, 조건 문구를 오른쪽의 수치/기간/상태 열과 섞지 않습니다. 시각적으로 다른 열이면 HTML에서도 다른 `<td>`로 유지합니다.
5. 페이지 번호, 머리말, 꼬리말, 장식용 구분선은 HTML 결과에 포함하지 않습니다.
6. 특정 조직명, 고유 명칭, 문서 도메인에 특화된 열 이름이나 값을 새로 만들지 않습니다. 원본에 보이는 텍스트와 구조만 사용합니다.

### 다단/분절 표 처리 규칙

1. 원본 이미지에서 하나의 표가 좌우 또는 상하로 분절되어 보이면, 보이는 선/정렬/반복되는 행 키를 근거로 같은 표인지 판단합니다.
2. 서로 독립된 표를 임의로 섞지 않습니다. 독립 표라면 별도 `<table>`로 유지합니다.
3. 분절 경계에서 같은 행 키가 반복되거나 한 행의 설명이 다음 조각에서 이어지는 등 continuation 근거가 명확할 때만 하나의 논리 table로 합칩니다.
4. 합칠 때도 원본의 읽기 순서를 유지하고, 중복된 경계 행은 하나의 완전한 행으로 정리합니다.
"""
        reference_text = ""
        if original_table_text:
            reference_text = f"""

기존 파서가 추출한 표 텍스트가 아래에 있습니다. 이미지가 최우선이지만, 글자 판독이 애매할 때만 참고하세요.
기존 텍스트에 반복/오인식이 있으면 그대로 따르지 말고 이미지 구조를 우선하세요.

<extracted_table_text>
{original_table_text[:12000]}
</extracted_table_text>
"""
        body = {
            'messages': [
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': [
                    {"type": "text", "text": "이 이미지를 위 규칙에 맞춰 검색용 HTML table fragment로 변환해줘." + reference_text},
                    {
                        "type": "image_url",
                        "image_url": {
                            # f-string 사용 시 중괄호 주의. 실제 API 스펙에 따라 구조가 다를 수 있음
                            "url": f"data:image/png;base64,{b64}"
                        }
                    }
                ]}
            ],
            # 필요시 온도(temperature)를 낮춰 일관된 포맷 유도
        }
        result = await self.llm.async_call_with_body(body=body)
        if result and 'choices' in result and result['choices']:
            html = result['choices'][0]['message']['content']
            html = re.sub(r"^```(?:html)?\s*|\s*```$", "", html.strip(), flags=re.IGNORECASE | re.MULTILINE)
            html = self._normalize_sparse_form_table(html)
            self._debug(
                "LLM response accepted raw_chars=%s cleaned_chars=%s",
                len(result['choices'][0]['message'].get('content', '') or ''),
                len(html or ''),
            )
            return html
        else:
            self._debug("Table refinement failed. LLM response=%s", result, level="warning")
            return None

    async def refine_table(self, i_page, bbox, clip_margin: float = 0.0, zoom: float = 2.0, table_line=None,
                           vector_idx=None, original_table_text: str = None):
        self._debug(
            "refine_table start vector=%s page=%s line_range=%s original_chars=%s bbox=%s",
            vector_idx,
            i_page + 1,
            table_line,
            len(original_table_text or ""),
            bbox,
        )
        b64 = self.cropping(bbox=bbox, i_page=i_page, clip_margin=clip_margin, zoom=zoom)
        html = await self._refine_table(b64, original_table_text=original_table_text)
        self._debug(
            "refine_table done vector=%s page=%s line_range=%s html_chars=%s",
            vector_idx,
            i_page + 1,
            table_line,
            len(html or ""),
        )
        if table_line is not None and vector_idx is not None:
            return vector_idx, table_line, html
        else:
            return html

    def detect_table_blocks(self, text: str):
        lines = text.split("\n")
        table_blocks = []

        in_block = False
        start_idx = None
        block_kind = None

        def is_markdown_table_line(line: str):
            line = line.strip()
            # 파이프(|) 기반 테이블 라인
            if line.startswith("|") and "|" in line[1:]:
                return True

            # markdown 구분선
            if set(line.replace("|", "").strip()) <= {"-", ":"} and "-" in line:
                return True

            return False

        def is_html_table_line(line: str):
            line = line.strip()
            # export_to_html 기본값을 유지해도 refine이 동작하도록 HTML table도 감지
            if re.search(r"</?(table|thead|tbody|tr|th|td)\b", line, re.IGNORECASE):
                return True

            return False

        for i, line in enumerate(lines):
            is_html_table = is_html_table_line(line)
            is_markdown_table = is_markdown_table_line(line)

            if is_html_table:
                if in_block and block_kind != "html":
                    table_blocks.append([start_idx, i])
                    in_block = False
                    start_idx = None
                    block_kind = None

                if not in_block:
                    in_block = True
                    start_idx = i
                    block_kind = "html"

                if re.search(r"</table\s*>", line, re.IGNORECASE):
                    table_blocks.append([start_idx, i + 1])
                    in_block = False
                    start_idx = None
                    block_kind = None
            elif is_markdown_table:
                if in_block and block_kind != "markdown":
                    table_blocks.append([start_idx, i])
                    in_block = False
                    start_idx = None
                    block_kind = None

                if not in_block:
                    in_block = True
                    start_idx = i
                    block_kind = "markdown"
            else:
                if in_block:
                    # 테이블 끝: 현재 i 라인 제외
                    table_blocks.append([start_idx, i])
                    in_block = False
                    start_idx = None
                    block_kind = None

        # 마지막 라인까지 테이블인 경우
        if in_block and start_idx is not None:
            table_blocks.append([start_idx, len(lines)])

        return table_blocks

    def _separate_adjacent_html_tables(self, text: str) -> str:
        return re.sub(
            r"(</table\s*>)\s*(?=<table\b)",
            r"\1\n",
            text or "",
            flags=re.IGNORECASE,
        )

    def _refresh_vector_text_stats(self, vector):
        text = vector.text or ""
        vector.n_char = len(text)
        vector.n_word = len(text.split())
        vector.n_line = len(text.splitlines())

    def _expand_table_text_range(self, lines: list[str], start: int, end: int) -> tuple[int, int]:
        """Include adjacent table captions/context when replacing a detected table block."""
        while start > 0:
            prev = lines[start - 1].strip()
            if not prev or re.match(r"</?(p|h[1-6])\b", prev, flags=re.IGNORECASE):
                start -= 1
                continue
            break

        while end < len(lines):
            cur = lines[end].strip()
            if not cur or re.match(r"</?(p|h[1-6])\b", cur, flags=re.IGNORECASE):
                end += 1
                continue
            break

        return start, end

    def _extract_first_column_keys(self, table_text: str) -> list[str]:
        keys = []
        for row in self._parse_html_rows(table_text or ""):
            if not row:
                continue
            key = (row[0] or "").strip()
            if not key or len(key) > 40:
                continue
            keys.append(re.sub(r"\s+", " ", key).strip().casefold())
        return keys

    def _has_table_continuation_evidence(self, left: dict[str, Any], right: dict[str, Any]) -> bool:
        left_keys = self._extract_first_column_keys(left.get("text", ""))
        right_keys = self._extract_first_column_keys(right.get("text", ""))
        if left_keys and right_keys and left_keys[-1] == right_keys[0]:
            return True
        return False

    def _maybe_merge_same_page_column_tables(self, matched_tables: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Merge side-by-side table bboxes only with clear continuation evidence."""
        if len(matched_tables) < 2:
            return matched_tables

        merged = []
        i = 0
        while i < len(matched_tables):
            cur = matched_tables[i]
            group = [cur]
            cur_bbox = cur["bbox"].get("bbox", {})
            cur_page = cur["bbox"].get("page")
            i += 1

            while i < len(matched_tables):
                nxt = matched_tables[i]
                nxt_bbox = nxt["bbox"].get("bbox", {})
                nxt_page = nxt["bbox"].get("page")
                same_page = cur_page == nxt_page
                similar_vertical_span = (
                    abs(float(cur_bbox.get("t", 0)) - float(nxt_bbox.get("t", 0))) <= 0.03
                    and abs(float(cur_bbox.get("b", 0)) - float(nxt_bbox.get("b", 0))) <= 0.03
                )
                side_by_side = (
                    float(cur_bbox.get("r", 0)) <= float(nxt_bbox.get("l", 1))
                    and float(nxt_bbox.get("l", 1)) - float(cur_bbox.get("r", 0)) <= 0.08
                )
                continuation_evidence = self._has_table_continuation_evidence(group[-1], nxt)

                if not (same_page and similar_vertical_span and side_by_side and continuation_evidence):
                    break

                group.append(nxt)
                cur_bbox = {
                    "l": min(float(cur_bbox.get("l", 0)), float(nxt_bbox.get("l", 0))),
                    "t": max(float(cur_bbox.get("t", 0)), float(nxt_bbox.get("t", 0))),
                    "r": max(float(cur_bbox.get("r", 0)), float(nxt_bbox.get("r", 0))),
                    "b": min(float(cur_bbox.get("b", 0)), float(nxt_bbox.get("b", 0))),
                    "coord_origin": cur_bbox.get("coord_origin", nxt_bbox.get("coord_origin", "BOTTOMLEFT")),
                }
                i += 1

            if len(group) == 1:
                merged.append(cur)
                continue

            group_bboxes = [item["bbox"]["bbox"] for item in group]
            first = group[0]["bbox"]
            merged_bbox = {
                **first,
                "bbox": {
                    "l": min(float(bbox.get("l", 0)) for bbox in group_bboxes),
                    "t": max(float(bbox.get("t", 0)) for bbox in group_bboxes),
                    "r": max(float(bbox.get("r", 0)) for bbox in group_bboxes),
                    "b": min(float(bbox.get("b", 0)) for bbox in group_bboxes),
                    "coord_origin": group_bboxes[0].get("coord_origin", "BOTTOMLEFT"),
                },
            }
            merged.append({
                "bbox": merged_bbox,
                "table_line": (
                    min(item["table_line"][0] for item in group),
                    max(item["table_line"][1] for item in group),
                ),
                "merged_count": len(group),
            })

        return merged

    def _parse_markdown_row(self, line: str) -> list[str]:
        line = line.strip()
        if not line.startswith("|"):
            return []
        return [cell.strip() for cell in line.strip("|").split("|")]

    def _strip_html(self, text: str) -> str:
        text = re.sub(r"<br\s*/?>", " ", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)
        return re.sub(r"\s+", " ", text).strip()

    def _parse_html_rows(self, html: str) -> list[list[str]]:
        rows = []
        for tr in re.findall(r"<tr\b[^>]*>.*?</tr>", html or "", flags=re.IGNORECASE | re.DOTALL):
            cells = re.findall(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", tr, flags=re.IGNORECASE | re.DOTALL)
            if cells:
                rows.append([self._strip_html(cell) for cell in cells])
        return rows

    def _parse_html_rows_with_attrs(self, html: str) -> list[list[dict[str, Any]]]:
        rows = []
        for tr in re.findall(r"<tr\b[^>]*>.*?</tr>", html or "", flags=re.IGNORECASE | re.DOTALL):
            row = []
            for match in re.finditer(r"<(t[dh])\b([^>]*)>(.*?)</\1>", tr, flags=re.IGNORECASE | re.DOTALL):
                attrs = match.group(2) or ""
                text = self._strip_html(match.group(3))

                def read_span(name: str) -> int:
                    span_match = re.search(
                        rf"{name}\s*=\s*[\"']?(\d+)",
                        attrs,
                        flags=re.IGNORECASE,
                    )
                    if not span_match:
                        return 1
                    try:
                        return max(int(span_match.group(1)), 1)
                    except ValueError:
                        return 1

                row.append({
                    "tag": match.group(1).lower(),
                    "text": text,
                    "rowspan": read_span("rowspan"),
                    "colspan": read_span("colspan"),
                })
            if row:
                rows.append(row)
        return rows

    def _escape_html_text(self, text: str) -> str:
        text = text or ""
        return (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

    def _normalize_sparse_form_table(self, html: str) -> str:
        """Normalize blank-heavy checklist/form tables into rectangular HTML grids."""
        if not html or not re.search(r"<table\b", html, flags=re.IGNORECASE):
            return html

        total_cells = len(re.findall(r"<t[dh]\b", html, flags=re.IGNORECASE))
        empty_cells = len(
            re.findall(
                r"<t[dh]\b[^>]*>\s*(?:&nbsp;|-)?\s*</t[dh]>",
                html,
                flags=re.IGNORECASE,
            )
        )
        if total_cells < 20 or empty_cells < 10:
            return html

        plain = self._strip_html(html)
        form_markers = ("점검", "체크", "확인", "서명", "날짜", "총계", "○", "X")
        if not any(marker in plain for marker in form_markers):
            return html

        empty_ratio = empty_cells / max(total_cells, 1)
        if empty_ratio < 0.35:
            return html

        rows = self._parse_html_rows_with_attrs(html)
        if len(rows) < 3:
            return html

        date_values = []
        items = []
        current_category = ""

        for cell in rows[0]:
            text = cell.get("text", "").strip()
            if not text:
                continue
            if any(label in text for label in ("점검항목", "날짜", "합계")):
                continue
            if re.search(r"\d|총계", text):
                date_values.append(text)

        if len(date_values) < 3:
            return html

        for row in rows[1:]:
            row_texts = [(cell.get("text", "") or "").strip() for cell in row]
            nonempty = [text for text in row_texts if text and text != "-"]
            if not nonempty:
                continue

            item_idx = None
            item_text = ""
            for i, text in enumerate(row_texts):
                if re.match(r"^\d+\s*[.)]", text):
                    item_idx = i
                    item_text = text
                    break

            if item_text:
                for text in row_texts[:item_idx]:
                    if (
                        text
                        and not re.match(r"^\d+\s*[.)]", text)
                        and not any(marker in text for marker in ("점검항목", "날짜"))
                        and len(text) <= 30
                    ):
                        current_category = text
                items.append((current_category, item_text, [""] * len(date_values)))
                continue

            compact_line = " ".join(nonempty)
            if any(marker in compact_line for marker in ("합", "총계", "확인", "서명")):
                items.append(("기입란", compact_line, [""] * len(date_values)))

        if not items:
            return html

        parts = []
        parts.append("<table>")
        parts.append("  <thead>")
        header_cells = ["구분", "점검항목", *date_values]
        parts.append(
            "    <tr>"
            + "".join(f"<th>{self._escape_html_text(cell)}</th>" for cell in header_cells)
            + "</tr>"
        )
        parts.append("  </thead>")
        parts.append("  <tbody>")
        for category, item_text, values in items:
            parts.append(
                "    <tr>"
                f"<td>{self._escape_html_text(category)}</td>"
                f"<td>{self._escape_html_text(item_text)}</td>"
                + "".join(f"<td>{self._escape_html_text(value)}</td>" for value in values)
                + "</tr>"
            )
        parts.append("  </tbody>")
        parts.append("</table>")

        normalized = "\n".join(parts)
        self._debug(
            "sparse form table normalized total_cells=%s empty_cells=%s empty_ratio=%.2f rows=%s cols=%s before_chars=%s after_chars=%s",
            total_cells,
            empty_cells,
            empty_ratio,
            len(items),
            len(header_cells),
            len(html),
            len(normalized),
        )
        return normalized

    def _looks_like_poor_refined_table(self, refined_table: str) -> tuple[bool, str]:
        if not refined_table:
            return True, "empty"

        if "```" in refined_table:
            return True, "code_fence"

        has_html_table = bool(re.search(r"<table\b", refined_table, flags=re.IGNORECASE))
        if not has_html_table:
            if "|" in refined_table:
                return True, "markdown_output"
            return True, "no_html_table"

        poor_markers = (
            "하위1",
            "하위2",
            "하위3",
            "구분 1",
            "구분 2",
            "구분 3",
            "구분1",
            "구분2",
            "구분3",
        )
        for marker in poor_markers:
            if marker in refined_table:
                return True, f"placeholder_column:{marker}"

        rows = self._parse_html_rows(refined_table)
        data_rows = [
            row for row in rows
            if not all(cell.strip() in {"", "-"} for cell in row)
        ]

        if len(data_rows) < 2:
            return True, "too_few_rows"

        header = data_rows[0]
        nonempty_header = [cell for cell in header if cell]
        if nonempty_header:
            most_common_header, header_count = Counter(nonempty_header).most_common(1)[0]
            if header_count >= 3 and header_count / max(len(nonempty_header), 1) >= 0.4:
                return True, f"repeated_header:{most_common_header}"

        body_rows = data_rows[1:]
        first_col = [row[0] for row in body_rows if row and row[0]]
        if len(first_col) >= 6:
            most_common_first, first_count = Counter(first_col).most_common(1)[0]
            if first_count >= 5 and first_count / len(first_col) >= 0.7:
                return True, f"repeated_first_column:{most_common_first}"

        return False, ""

    async def run_in_batches(self, tasks, batch_size=5):
        results = []
        for i in range(0, len(tasks), batch_size):
            batch = tasks[i:i + batch_size]
            # 현재 배치 실행
            batch_results = await asyncio.gather(*batch, return_exceptions=True)
            results.extend(batch_results)
        return results

    async def refine_vectors(self, vectors):
        replace_tasks = []
        skipped_vectors = 0
        self._debug("begin vectors=%s", len(vectors))

        for idx, vector in enumerate(vectors):
            table_cnt_in_bboxes = 0
            table_bboxes = []
            table_lines = []
            try:
                bboxes = json.loads(vector.chunk_bboxes or "[]")
            except Exception as e:
                self._debug("vector=%s chunk_bboxes 파싱 실패: %s", idx, e, level="warning")
                continue
            original_text = vector.text
            vector.text = self._separate_adjacent_html_tables(vector.text)
            if vector.text != original_text:
                self._debug(
                    "vector=%s separated adjacent HTML tables before detection text_chars=%s",
                    idx,
                    len(vector.text or ""),
                )
            table_lines = self.detect_table_blocks(vector.text)
            table_cnt_in_lines = len(table_lines)
            for bbox in bboxes:
                if bbox.get('type') == 'table':
                    table_cnt_in_bboxes += 1
                    table_bboxes.append(bbox)

            if table_cnt_in_bboxes or table_cnt_in_lines:
                self._debug(
                    "vector=%s table_bboxes=%s table_blocks=%s table_line_ranges=%s bbox_pages=%s",
                    idx,
                    table_cnt_in_bboxes,
                    table_cnt_in_lines,
                    table_lines,
                    [bbox.get('page') for bbox in table_bboxes],
                )

            if table_cnt_in_bboxes == table_cnt_in_lines:
                origin_text_lines = vector.text.split('\n')
                matched_tables = []
                for bbox, table_line in zip(table_bboxes, table_lines):
                    start, end = table_line
                    matched_tables.append({
                        "bbox": bbox,
                        "table_line": table_line,
                        "merged_count": 1,
                        "text": "\n".join(origin_text_lines[start:end]),
                    })
                matched_tables = self._maybe_merge_same_page_column_tables(matched_tables)

                for matched_table in matched_tables:
                    bbox = matched_table["bbox"]
                    table_line = matched_table["table_line"]
                    start, end = table_line
                    original_table_text = "\n".join(origin_text_lines[start:end])
                    self._debug(
                        "vector=%s schedule bbox_page=%s line_range=%s original_chars=%s merged_count=%s",
                        idx,
                        bbox.get('page'),
                        table_line,
                        len(original_table_text or ""),
                        matched_table.get("merged_count", 1),
                    )
                    replace_task = self.refine_table(i_page=bbox['page'] - 1, bbox=bbox['bbox'], table_line=table_line,
                                                     vector_idx=idx, original_table_text=original_table_text)
                    replace_tasks.append(replace_task)
            elif table_cnt_in_bboxes == 1 and table_cnt_in_lines > 1:
                origin_text_lines = vector.text.split('\n')
                start = min(line_range[0] for line_range in table_lines)
                end = max(line_range[1] for line_range in table_lines)
                start, end = self._expand_table_text_range(origin_text_lines, start, end)
                original_table_text = "\n".join(origin_text_lines[start:end])
                bbox = table_bboxes[0]
                self._debug(
                    "vector=%s schedule merged block bbox_page=%s line_range=%s original_chars=%s",
                    idx,
                    bbox.get('page'),
                    (start, end),
                    len(original_table_text or ""),
                )
                replace_task = self.refine_table(
                    i_page=bbox['page'] - 1,
                    bbox=bbox['bbox'],
                    table_line=(start, end),
                    vector_idx=idx,
                    original_table_text=original_table_text,
                )
                replace_tasks.append(replace_task)
            elif table_cnt_in_bboxes or table_cnt_in_lines:
                skipped_vectors += 1
                self._debug(
                    "vector=%s 테이블 bbox/text block 개수가 달라 refine을 건너뜁니다. bboxes=%s blocks=%s line_ranges=%s bbox_pages=%s",
                    idx,
                    table_cnt_in_bboxes,
                    table_cnt_in_lines,
                    table_lines,
                    [bbox.get('page') for bbox in table_bboxes],
                    level="warning",
                )

        self._debug("scheduled_tables=%s skipped_vectors=%s", len(replace_tasks), skipped_vectors)
        results = await self.run_in_batches(replace_tasks, batch_size=5)
        self.results = results
        self.vectors = vectors
        d = defaultdict(list)

        for item in results:
            try:
                if isinstance(item, Exception):
                    self._debug("refine task raised exception=%r", item, level="warning")
                    continue
                (vector_idx, table_line, html) = item
                is_poor, reason = self._looks_like_poor_refined_table(html)
                if is_poor:
                    self._debug(
                        "vector=%s LLM 표 복원 결과가 불안정해서 원본 Docling 표를 유지합니다. reason=%s html_chars=%s",
                        vector_idx,
                        reason,
                        len(html or ""),
                        level="warning",
                    )
                    continue
                d[vector_idx].append([table_line, html])
            except Exception as e:
                self._debug('테이블 감지 중 오류 발생/해당 테이블을 Docling 테이블로 사용합니다: %s', item, level="warning")

        self._debug("refined_vectors=%s refined_tables=%s", len(d), sum(len(v) for v in d.values()))

        for idx in d:
            origin_text_lines = vectors[idx].text.split('\n')
            replacements = sorted(d[idx], key=lambda t: (t[0][0], t[0][1]))
            final_text = []
            cursor = 0

            for (start, end), html in replacements:
                if start < cursor:
                    self._debug(
                        "vector=%s overlapping replacement skipped line_range=%s cursor=%s",
                        idx,
                        (start, end),
                        cursor,
                        level="warning",
                    )
                    continue

                final_text.extend(origin_text_lines[cursor:start])
                final_text.append('')
                final_text.append(html)
                final_text.append('')
                cursor = max(cursor, end)

            final_text.extend(origin_text_lines[cursor:])
            vectors[idx].text = '\n'.join(final_text)
            self._refresh_vector_text_stats(vectors[idx])
            self._debug(
                "vector=%s replacement applied replacements=%s final_chars=%s final_lines=%s",
                idx,
                len(replacements),
                vectors[idx].n_char,
                vectors[idx].n_line,
            )
        return vectors


class llm_serving:
    def __init__(self, serving_id: int = 15, bearer_token: str = None, genos_url: str = 'https://genos.genon.ai'):
        self.serving_id = serving_id
        self.url = f"{genos_url}/api/gateway/rep/serving/{serving_id}"
        # self.url = f"http://llmops-gateway-api-service:8080/rep/serving/{serving_id}"  # 바뀐 대표 리비전 호출 url
        self.headers = {}
        if bearer_token:
            self.headers["Authorization"] = f"Bearer {bearer_token}"
        self.endpoint = f"{self.url}/v1/models"
        if not (serving_id and bearer_token):
            _log.warning("LLM serving id or bearer token is missing. serving_id=%s", serving_id)

    def make_openai_json_schema(self, model: type[BaseModel]) -> dict:
        if not model.model_config:
            model.model_config = ConfigDict(extra='forbid')
        return {
            "type": "json_schema",
            "json_schema": {
                "name": model.__name__,  # 클래스 이름 자동 반영
                "strict": True,
                "schema": model.model_json_schema()
            }
        }

    def call(self, system_prompt: str = '당신은 유용한 어시스턴트입니다.', question: str = '안녕?', output_format: BaseModel = None):
        """
        LLM output의 text만 내보냅니다.
        """
        body = {
            'messages': [
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': question}
            ]
        }

        if output_format:
            body['response_format'] = self.make_openai_json_schema(output_format)

        endpoint = f"{self.url}/v1/chat/completions"
        try:
            response = requests.post(endpoint, headers=self.headers, json=body, stream=True, timeout=180)
            result = response.json()['choices'][0]['message']['content']
        except KeyError as e:
            _log.warning("LLM serving response parse failed: %s response=%s", e, response.json())
            return None
        except requests.exceptions.RequestException as e:
            _log.warning("LLM serving request failed: %s", e)
            return None

        if output_format:
            return json.loads(result)
        else:
            return result

    async def async_call(self, system_prompt: str = '당신은 유용한 어시스턴트입니다.', question: str = '안녕?',
                         output_format: BaseModel = None):
        body = {
            'messages': [
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': question}
            ]
        }

        if output_format:
            body['response_format'] = self.make_openai_json_schema(output_format)

        endpoint = f"{self.url}/v1/chat/completions"

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(180.0)) as client:
                response = await client.post(endpoint, headers=self.headers, json=body)
            result = response.json()['choices'][0]['message']['content']
        except KeyError as e:
            _log.warning("LLM serving response parse failed: %s response=%s", e, response.json())
            return None
        except httpx.RequestError as e:
            _log.warning("LLM serving request failed: %s", e)
            return None

        if output_format:
            return json.loads(result)
        else:
            return result

    def call_all(self, system_prompt: str = '당신은 유용한 어시스턴트입니다.', question: str = '안녕?',
                 output_format: BaseModel = None):
        """
        LLM output의 모든 데이터를 내보냅니다.
        """
        body = {
            'messages': [
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': question}
            ]
        }

        if output_format:
            body['response_format'] = self.make_openai_json_schema(output_format)

        endpoint = f"{self.url}/v1/chat/completions"
        response = requests.post(endpoint, headers=self.headers, json=body)
        result = response.json()
        return result

    def call_with_body(self,
                       body: Optional[dict] = None,
                       output_format: BaseModel = None):
        """
        LLM output의 모든 데이터를 내보냅니다.
        """
        if body is None:
            body = {
                'messages': [
                    {'role': 'system', 'content': '당신은 유용한 어시스턴트입니다.'},
                    {'role': 'user', 'content': '안녕?'}
                ]
            }
        else:
            body = dict(body)

        if output_format:
            body['response_format'] = self.make_openai_json_schema(output_format)

        endpoint = f"{self.url}/v1/chat/completions"
        response = requests.post(endpoint, headers=self.headers, json=body)
        result = response.json()
        return result

    async def async_call_with_body(self,
                                   body: Optional[dict] = None,
                                   output_format: BaseModel = None):
        """
        LLM output의 모든 데이터를 비동기로 요청합니다.
        """
        if body is None:
            body = {
                'messages': [
                    {'role': 'system', 'content': '당신은 유용한 어시스턴트입니다.'},
                    {'role': 'user', 'content': '안녕?'}
                ]
            }
        else:
            body = dict(body)

        if output_format:
            body['response_format'] = self.make_openai_json_schema(output_format)

        endpoint = f"{self.url}/v1/chat/completions"

        async with httpx.AsyncClient(timeout=httpx.Timeout(180.0)) as client:
            response = await client.post(endpoint, headers=self.headers, json=body)
            response.raise_for_status()  # 오류 발생 시 예외 발생
            return response.json()


class GenosBucketChunker(BaseChunker):
    """토큰 제한을 고려하여 섹션별 청크를 분할하고 병합하는 청커 (v2)"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    tokenizer: Union[PreTrainedTokenizerBase, str, Path] = (
            Path("/models/doc_parser_models/sentence-transformers-all-MiniLM-L6-v2")
            if Path("/models/doc_parser_models/sentence-transformers-all-MiniLM-L6-v2").exists()
            else "sentence-transformers/all-MiniLM-L6-v2"
    )
    max_tokens: int = 1024
    max_pages_per_chunk: int = 0
    merge_peers: bool = True

    # _inner_chunker: BaseChunker = None
    _tokenizer: PreTrainedTokenizerBase = None
    merge_list_items: bool = True

    @model_validator(mode="after")
    def _initialize_components(self) -> Self:
        # 토크나이저 초기화
        self._tokenizer = (
            self.tokenizer
            if isinstance(self.tokenizer, PreTrainedTokenizerBase)
            else AutoTokenizer.from_pretrained(self.tokenizer)
        )
        return self

    def _clean_heading_text(self, text: Any) -> str:
        if text is None:
            return ""

        cleaned = str(text).strip()
        cleaned = re.sub(r"\s+", " ", cleaned)

        # 목차 페이지의 dotted leader 항목은 실제 본문 헤더가 아니므로 경로에서 제외
        if re.search(r"[·.]{3,}\s*\d+\s*$", cleaned):
            return ""

        return cleaned

    def preprocess(self, dl_doc: DLDocument, **kwargs: Any) -> Iterator[BaseChunk]:
        """문서의 모든 아이템을 헤더 정보와 함께 청크로 생성

        Args:
            dl_doc: 청킹할 문서

        Yields:
            문서의 모든 아이템을 포함하는 하나의 청크
        """
        # 모든 아이템과 헤더 정보 수집
        all_items = []
        all_header_info = []  # 각 아이템의 헤더 정보
        current_heading_by_level: dict[LevelNumber, str] = {}
        all_header_short_info = []  # 각 아이템의 짧은 헤더 정보
        current_heading_short_by_level: dict[LevelNumber, str] = {}
        list_items: list[TextItem] = []

        # iterate_items()로 수집된 아이템들의 self_ref 추적
        processed_refs = set()

        # 모든 아이템 순회
        for item, level in dl_doc.iterate_items(included_content_layers={ContentLayer.BODY, ContentLayer.FURNITURE}):
            if hasattr(item, 'self_ref'):
                processed_refs.add(item.self_ref)

            if not isinstance(item, DocItem):
                continue

            # 리스트 아이템 병합 처리
            if self.merge_list_items:
                if isinstance(item, ListItem) or (
                    isinstance(item, TextItem) and item.label == DocItemLabel.LIST_ITEM
                ):
                    list_items.append(item)
                    continue
                elif list_items:
                    # 누적된 리스트 아이템들을 추가
                    for list_item in list_items:
                        all_items.append(list_item)
                        # 리스트 아이템의 헤더 정보 저장
                        all_header_info.append({k: v for k, v in current_heading_by_level.items()})
                        all_header_short_info.append({k: v for k, v in current_heading_short_by_level.items()})
                    list_items = []

            # 섹션 헤더 처리
            if isinstance(item, SectionHeaderItem) or (
                isinstance(item, TextItem) and
                item.label in [DocItemLabel.SECTION_HEADER, DocItemLabel.TITLE]
            ):
                # 새로운 헤더 레벨 설정
                header_level = (
                    item.level if isinstance(item, SectionHeaderItem)
                    else (0 if item.label == DocItemLabel.TITLE else 1)
                )
                heading_text = self._clean_heading_text(getattr(item, "text", ""))
                heading_short_text = self._clean_heading_text(getattr(item, "orig", heading_text))
                if not heading_text:
                    all_items.append(item)
                    all_header_info.append({k: v for k, v in current_heading_by_level.items()})
                    all_header_short_info.append({k: v for k, v in current_heading_short_by_level.items()})
                    continue

                current_heading_by_level[header_level] = heading_text
                current_heading_short_by_level[header_level] = heading_short_text or heading_text

                # 더 깊은 레벨의 헤더들 제거
                keys_to_del = [k for k in current_heading_by_level if k > header_level]
                for k in keys_to_del:
                    current_heading_by_level.pop(k, None)
                keys_to_del_short = [k for k in current_heading_short_by_level if k > header_level]
                for k in keys_to_del_short:
                    current_heading_short_by_level.pop(k, None)

                # 헤더 아이템도 추가 (헤더 자체도 아이템임)
                all_items.append(item)
                all_header_info.append({k: v for k, v in current_heading_by_level.items()})
                all_header_short_info.append({k: v for k, v in current_heading_short_by_level.items()})
                continue

            if (isinstance(item, TextItem) or
                isinstance(item, ListItem) or
                isinstance(item, CodeItem) or
                isinstance(item, TableItem) or
                isinstance(item, PictureItem)):
                if getattr(item, "label", None) in [DocItemLabel.PAGE_HEADER, DocItemLabel.PAGE_FOOTER]:
                    continue
                all_items.append(item)
                # 현재 아이템의 헤더 정보 저장
                all_header_info.append({k: v for k, v in current_heading_by_level.items()})
                all_header_short_info.append({k: v for k, v in current_heading_short_by_level.items()})

        # 마지막 리스트 아이템들 처리
        if list_items:
            for list_item in list_items:
                all_items.append(list_item)
                all_header_info.append({k: v for k, v in current_heading_by_level.items()})
                all_header_short_info.append({k: v for k, v in current_heading_short_by_level.items()})

        # iterate_items()에서 누락된 테이블들을 별도로 추가
        missing_tables = []
        for table in dl_doc.tables:
            table_ref = getattr(table, 'self_ref', None)
            if table_ref not in processed_refs:
                missing_tables.append(table)

        # 누락된 테이블들을 문서 앞부분에 추가 (페이지 1의 테이블들일 가능성이 높음)
        if missing_tables:
            for missing_table in missing_tables:
                # 첫 번째 위치에 삽입 (헤더 테이블일 가능성이 높음)
                all_items.insert(0, missing_table)
                all_header_info.insert(0, {})  # 빈 헤더 정보
                all_header_short_info.insert(0, {})  # 빈 짧은 헤더 정보

        # 아이템이 없으면 빈 문서
        if not all_items:
            return

        # 모든 아이템을 하나의 청크로 반환 (HybridChunker에서 분할)
        # headings는 None으로 설정하고, 헤더 정보는 별도로 관리
        chunk = DocChunk(
            text="",  # 텍스트는 HybridChunker에서 생성
            meta=DocMeta(
                doc_items=all_items,
                headings=None,  # DocMeta의 원래 형식 유지
                captions=None,
                origin=dl_doc.origin,
            ),
        )
        # 헤더 정보를 별도 속성으로 저장
        chunk._header_info_list = all_header_info
        chunk._header_short_info_list = all_header_short_info  # 짧은 헤더 정보도 저장
        yield chunk

    def _count_tokens(self, text: str) -> int:
        """텍스트의 토큰 수 계산 (안전한 분할 처리)"""
        if not text:
            return 0

        # 텍스트를 더 작은 단위로 분할하여 계산
        max_chunk_length = 300  # 더 안전한 길이로 설정
        total_tokens = 0

        # 텍스트를 줄 단위로 먼저 분할
        lines = text.split('\n')
        current_chunk = ""

        for line in lines:
            # 현재 청크에 줄을 추가했을 때 길이 확인
            temp_chunk = current_chunk + '\n' + line if current_chunk else line

            if len(temp_chunk) <= max_chunk_length:
                current_chunk = temp_chunk
            else:
                # 현재 청크가 있으면 토큰 계산
                if current_chunk:
                    try:
                        total_tokens += len(self._tokenizer.tokenize(current_chunk))
                    except Exception:
                        total_tokens += int(len(current_chunk.split()) * 1.3)  # 대략적인 계산

                # 새로운 청크 시작
                current_chunk = line

        # 마지막 청크 처리
        if current_chunk:
            try:
                total_tokens += len(self._tokenizer.tokenize(current_chunk))
            except Exception:
                total_tokens += int(len(current_chunk.split()) * 1.3)  # 대략적인 계산

        return total_tokens

    def _generate_text_from_items_with_headers(self, items: list[DocItem],
                                              header_info_list: list[dict],
                                              dl_doc: DoclingDocument,
                                              **kwargs) -> str:
        """DocItem 리스트로부터 헤더 정보를 포함한 텍스트 생성"""
        text_parts = []
        current_section_headers = {}  # 현재 섹션의 헤더 정보

        for i, item in enumerate(items):
            item_headers = header_info_list[i] if i < len(header_info_list) else {}

            # 헤더 정보가 변경된 경우 (새로운 섹션 시작)
            if item_headers != current_section_headers:
                # 변경된 헤더 레벨들만 추가
                headers_to_add = []
                for level in sorted(item_headers.keys()):
                    # 이전 섹션과 다른 헤더만 추가
                    if (level not in current_section_headers or
                        current_section_headers[level] != item_headers[level]):
                        # 해당 레벨까지의 모든 상위 헤더 포함
                        for l in sorted(item_headers.keys()):
                            if l < level:
                                cleaned_header = self._clean_heading_text(item_headers[l])
                                if cleaned_header:
                                    headers_to_add.append(cleaned_header)
                            elif l == level:
                                headers_to_add.append('')

                        break

                # 헤더가 있으면 추가
                if headers_to_add:
                    header_text = ", ".join(headers_to_add)
                    if header_text not in text_parts:
                        text_parts.append(header_text)

                current_section_headers = item_headers.copy()

            # 아이템 텍스트 추가
            if isinstance(item, TableItem):
                table_text = self._extract_table_text(item, dl_doc, **kwargs)
                if table_text:
                    text_parts.append(table_text)
            elif hasattr(item, 'text') and item.text:
                # 타이틀과 섹션 헤더 처리 개선
                # is_section_header = (
                #     isinstance(item, SectionHeaderItem) or
                #     (isinstance(item, TextItem) and
                #      item.label in [DocItemLabel.SECTION_HEADER])  # TITLE은 제외
                # )

                # 타이틀은 항상 포함, 섹션 헤더는 중복 방지를 위해 스킵
                # if not is_section_header:
                # 20250909, shkim, text_parts에 없는 경우만 추가. 섹션헤더가 반복해서 추가되는 것 방지
                item_text = item.text
                if self._is_section_header(item):
                    item_text = self._clean_heading_text(item_text)
                if item_text and item_text not in text_parts:
                    text_parts.append(item_text)
            elif isinstance(item, PictureItem):
                if (_as_int_flag(_first_kwarg(kwargs, 'img_desc', 'image_description_on', default=0), 0) == 1 or
                    _as_int_flag(_first_kwarg(kwargs, 'img_detail', 'enhanced_image_description_on', default=0), 0) == 1):
                    picture_text = self._get_picture_description_text(item)
                    if not picture_text:
                        _log.warning(
                            "[image_description] PictureItem has no annotation text. ref=%s annotations=%s",
                            getattr(item, "self_ref", None),
                            len(getattr(item, "annotations", []) or []),
                        )
                    text_parts.append(picture_text)
                else:
                    text_parts.append("")  # 기본 표준 전처리기 동작 유지

        result_text = self.delim.join(text_parts)
        return result_text

    def _extract_table_text(self, table_item: TableItem, dl_doc: DoclingDocument, **kwargs) -> str:
        """테이블에서 텍스트를 추출하는 일반화된 메서드"""
        try:
            # 먼저 export_to_markdown 시도
            export_to_html = kwargs.get('export_to_html', 1)
            if export_to_html == 1:
                table_text = table_item.export_to_html(dl_doc)
            else:
                table_text = table_item.export_to_markdown(dl_doc)
            if table_text and table_text.strip():
                return table_text
        except Exception:
            pass

        # export_to_markdown 실패 시 테이블 셀 데이터에서 직접 텍스트 추출
        try:
            if hasattr(table_item, 'data') and table_item.data:
                cell_texts = []

                # table_cells에서 텍스트 추출
                if hasattr(table_item.data, 'table_cells'):
                    for cell in table_item.data.table_cells:
                        if hasattr(cell, 'text') and cell.text and cell.text.strip():
                            cell_texts.append(cell.text.strip())

                # grid에서 텍스트 추출 (table_cells가 없는 경우)
                elif hasattr(table_item.data, 'grid') and table_item.data.grid:
                    for row in table_item.data.grid:
                        if isinstance(row, list):
                            for cell in row:
                                if hasattr(cell, 'text') and cell.text and cell.text.strip():
                                    cell_texts.append(cell.text.strip())

                # 추출된 셀 텍스트들을 결합
                if cell_texts:
                    return ' '.join(cell_texts)
        except Exception:
            pass

        # 모든 방법 실패 시 item.text 사용 (있는 경우)
        if hasattr(table_item, 'text') and table_item.text:
            return table_item.text

        return ""

    def _extract_used_headers(self, header_info_list: list[dict]) -> Optional[list[str]]:
        """헤더 정보 리스트에서 실제 사용되는 모든 헤더들을 level 순서대로 추출하고 ', '로 연결"""
        if not header_info_list:
            return None

        all_headers = [] # header 순서대로 추가
        seen_headers = set()  # 중복 방지용

        for header_info in header_info_list:
            if header_info:
                for level in sorted(header_info.keys()):
                    header_text = self._clean_heading_text(header_info[level])
                    if header_text and header_text not in seen_headers:
                        all_headers.append(header_text)
                        seen_headers.add(header_text)

        return all_headers if all_headers else None

    def _split_table_text(self, table_text: str, max_tokens: int) -> list[str]:
        """테이블 텍스트를 토큰 제한에 맞게 분할 (단순 토큰 수 기준)"""
        if not table_text:
            return [table_text]

        # 전체 테이블이 토큰 제한 내인지 확인
        if self._count_tokens(table_text) <= max_tokens:
            return [table_text]

        # 단순히 토큰 수 기준으로 텍스트 분할
        # semchunk 사용하여 토큰 제한에 맞게 분할
        chunker = semchunk.chunkerify(self._tokenizer, chunk_size=max_tokens)
        chunks = chunker(table_text)
        return chunks if chunks else [table_text]

    def _is_section_header(self, item: DocItem) -> bool:
        """아이템이 section header인지 확인"""
        return (isinstance(item, SectionHeaderItem) or
                (isinstance(item, TextItem) and
                 item.label in [DocItemLabel.SECTION_HEADER, DocItemLabel.TITLE]))

    def _get_section_header_level(self, item: DocItem) -> Optional[int]:
        """Section header의 level을 반환"""
        if isinstance(item, SectionHeaderItem):
            return item.level
        elif isinstance(item, TextItem):
            if item.label == DocItemLabel.TITLE:
                return 0
            elif item.label == DocItemLabel.SECTION_HEADER:
                return 1
        return None

    def _get_picture_description_text(self, item: PictureItem) -> str:
        """PictureItem annotation에서 이미지 설명 텍스트를 추출한다."""
        descriptions = []
        for annotation in getattr(item, "annotations", []) or []:
            annotation_text = getattr(annotation, "text", None)
            if annotation_text:
                descriptions.append(str(annotation_text).strip())
        return "\n\n".join(text for text in descriptions if text)

    def _generate_section_text_with_heading(self, section_items: list[DocItem],
                                            section_header_infos: list[dict],
                                            dl_doc: DoclingDocument,
                                            **kwargs) -> str:
        """섹션의 텍스트를 생성하되, 앞에 heading을 붙임"""
        # 첫 번째 item의 header_info에서 heading 추출
        if section_header_infos and section_header_infos[0]:
            merged_headers = {}
            for level, header_text in section_header_infos[0].items():
                cleaned_header = self._clean_heading_text(header_text)
                if cleaned_header:
                    merged_headers[level] = cleaned_header

            # level 순서대로 정렬해서 ', '로 연결
            if merged_headers:
                sorted_levels = sorted(merged_headers.keys())
                headers = [merged_headers[level] for level in sorted_levels]
                heading_text = ', '.join(headers)
            else:
                heading_text = ""
        else:
            heading_text = ""

        # 섹션의 일반 텍스트 생성
        section_text = self._generate_text_from_items_with_headers(
            section_items, section_header_infos, dl_doc, **kwargs
        )

        # heading이 있으면 앞에 붙이기
        if heading_text:
            return heading_text + ", " + section_text
        else:
            return section_text

    def _split_document_by_tokens(self, doc_chunk: DocChunk, dl_doc: DoclingDocument, **kwargs) -> list[DocChunk]:
        """문서를 토큰 제한에 맞게 분할 (v2: 섹션 헤더 기준으로 분할 후 max_tokens로 병합)"""
        items = doc_chunk.meta.doc_items
        header_info_list = getattr(doc_chunk, '_header_info_list', [])
        header_short_info_list = getattr(doc_chunk, '_header_short_info_list', [])

        if not items:
            return []

        # ================================================================
        # 헬퍼 함수들
        # ================================================================

        def get_header_level(header_infos, *, first=False, default=-1):
            """header_infos에서 최종 레벨 계산"""
            if not header_infos:
                return default
            info = header_infos[0] if first else header_infos[-1]
            return max(info.keys(), default=default)

        def get_item_pages(item: DocItem) -> list[int]:
            pages = []
            for prov in (getattr(item, "prov", None) or []):
                page_no = getattr(prov, "page_no", None)
                if isinstance(page_no, int) and page_no > 0:
                    pages.append(page_no)
            return pages

        def get_page_span(items: list[DocItem]) -> int:
            pages = sorted({page for item in items for page in get_item_pages(item)})
            if not pages:
                return 0
            return pages[-1] - pages[0] + 1

        infer_local_section_boundaries = _as_int_flag(
            kwargs.get(
                "infer_local_section_boundaries",
                kwargs.get("soft_section_boundaries_on", 1),
            ),
            1,
        ) == 1

        def get_first_page(item: DocItem) -> Optional[int]:
            pages = get_item_pages(item)
            return pages[0] if pages else None

        def is_visual_or_table_item(item: DocItem) -> bool:
            return isinstance(item, (TableItem, PictureItem))

        def is_local_heading_like_text(item: DocItem) -> bool:
            if not isinstance(item, TextItem):
                return False

            label = getattr(item, "label", None)
            if label in [DocItemLabel.PAGE_HEADER, DocItemLabel.PAGE_FOOTER]:
                return False
            if label not in [
                DocItemLabel.TEXT,
                DocItemLabel.CAPTION,
                DocItemLabel.PARAGRAPH,
            ]:
                return False

            text = self._clean_heading_text(getattr(item, "text", ""))
            if not text:
                return False
            compact = re.sub(r"\s+", "", text)
            if len(compact) < 3 or len(text) > 90:
                return False
            if re.fullmatch(r"-?\s*\d+\s*-?", text):
                return False
            if re.search(r"</?(table|thead|tbody|tr|th|td)\b", text, flags=re.IGNORECASE):
                return False
            if text.count(" ") > 12 or text.count(",") > 3:
                return False
            if compact.endswith(("습니다", "합니다", "됩니다", "있습니다", "없습니다", "십시오")):
                return False
            if re.search(r"[.!?。]$", text):
                return False
            return True

        def is_local_section_boundary(idx: int) -> bool:
            if not infer_local_section_boundaries:
                return False

            item = items[idx]
            if not is_local_heading_like_text(item):
                return False

            item_page = get_first_page(item)
            if item_page is None:
                return False

            if idx > 0:
                prev_item = items[idx - 1]
                if (
                    get_first_page(prev_item) == item_page
                    and is_local_heading_like_text(prev_item)
                ):
                    return False

            for next_item in items[idx + 1: idx + 4]:
                next_page = get_first_page(next_item)
                if next_page is not None and next_page != item_page:
                    break
                if is_visual_or_table_item(next_item):
                    return True

            return False

        def get_current_chunk(doc_chunk: DocChunk, merged_texts: list[str], merged_header_short_infos: list[dict], merged_items: list[DocItem]):
            """현재까지 병합된 내용으로 DocChunk 생성"""
            if not merged_texts:
                return None
            chunk_text = "\n".join(merged_texts)
            used_headers = self._extract_used_headers(merged_header_short_infos)

            return DocChunk(
                    text=chunk_text,
                    meta=DocMeta(
                        doc_items=merged_items,
                        headings=used_headers,
                        captions=None,
                        origin=doc_chunk.meta.origin,
                    )
                )

        def get_text_from_item(item: DocItem) -> str:
            """DocItem에서 텍스트 추출"""
            if isinstance(item, TableItem):
                return self._extract_table_text(item, dl_doc, **kwargs)
            elif hasattr(item, 'text') and item.text:
                return item.text
            elif isinstance(item, PictureItem):
                return self._get_picture_description_text(item)
            return ""

        def split_items_evenly_by_tokens(item_token_counts, max_tokens):
            n = len(item_token_counts)
            total = sum(item_token_counts)
            if n == 0:
                return []
            if total <= max_tokens:
                return [(0, n)]   # ✅ 항상 (a,b)

            k = math.ceil(total / max_tokens)
            target = total / k

            P = [0]
            for c in item_token_counts:
                P.append(P[-1] + c)

            cuts = [0]
            used = {0}
            for t in range(1, k):
                goal = t * target
                j = bisect.bisect_left(P, goal)

                cand = []
                if 0 < j < len(P): cand.append(j)
                if 0 <= j-1 < len(P): cand.append(j-1)

                best = None
                best_dist = float("inf")
                for x in cand:
                    if x in used:
                        continue
                    if x <= cuts[-1]:
                        continue
                    if x >= len(P)-1:  # n
                        continue
                    dist = abs(P[x] - goal)
                    if dist < best_dist:
                        best_dist = dist
                        best = x

                if best is None:
                    best = min(max(cuts[-1] + 1, 1), len(P)-2)

                cuts.append(best)
                used.add(best)

            cuts.append(n)

            return [(a, b) for a, b in zip(cuts[:-1], cuts[1:])]

        def split_items_by_token_and_page_limits(items_group, item_token_counts, max_tokens, max_pages):
            ranges = []
            start = 0
            cur_tokens = 0
            cur_pages = set()

            def page_span(pages: set[int]) -> int:
                if not pages:
                    return 0
                return max(pages) - min(pages) + 1

            for idx, group in enumerate(items_group):
                group_tokens = item_token_counts[idx]
                group_pages = {
                    page
                    for item, _, _ in group
                    for page in get_item_pages(item)
                }
                candidate_pages = cur_pages | group_pages
                token_exceeded = (
                    max_tokens > 0
                    and cur_tokens > 0
                    and cur_tokens + group_tokens > max_tokens
                )
                page_exceeded = (
                    max_pages > 0
                    and cur_pages
                    and group_pages
                    and page_span(candidate_pages) > max_pages
                )

                if (token_exceeded or page_exceeded) and idx > start:
                    ranges.append((start, idx))
                    start = idx
                    cur_tokens = group_tokens
                    cur_pages = set(group_pages)
                else:
                    cur_tokens += group_tokens
                    cur_pages = candidate_pages

            if start < len(items_group):
                ranges.append((start, len(items_group)))
            return ranges

        def adjust_captions(items_group):

            b_modified = False
            for idx, group in enumerate(items_group):
                if group is None:
                    continue
                item = group[0][0]
                ref_idx_list = []
                if hasattr(item, 'captions') and item.captions:
                    for cap in item.captions:
                        cap_ref = cap.cref
                        cap_idx = -1
                        for j, it in enumerate(items_group):
                            if it is None:
                                continue
                            if getattr(it[0][0], 'self_ref', None) == cap_ref:
                                cap_idx = j
                                break
                        if cap_idx != -1:
                            ref_idx_list.append(cap_idx)
                if ref_idx_list:
                    ref_idx_list = sorted(ref_idx_list)

                if not ref_idx_list:
                    continue

                # caption 아이템들을 부모 아이템 바로 뒤로 이동
                for cap_idx in ref_idx_list:
                    for g in items_group[cap_idx]:
                        items_group[idx].append(g)
                    items_group[cap_idx] = None  # 나중에 None 제거
                    b_modified = True

            if b_modified:
                items_group = [it for it in items_group if it is not None]

            return items_group

        def adjust_pictures_in_tables(items_group):
            # picture in table 처리

            b_modified = False
            for idx, group in enumerate(items_group):
                if group is None:
                    continue
                item = group[0][0]
                pic_idx_list = []
                if isinstance(item, TableItem):
                    table_bbox = item.prov[0].bbox
                    table_page_no = item.prov[0].page_no

                    for j in range(len(items_group)):
                        if items_group[j] is None:
                            continue
                        pic_item = items_group[j][0][0]
                        if isinstance(pic_item, PictureItem):
                            # table 안의 picture인지 확인. iou 사용
                            pic_bbox = pic_item.prov[0].bbox
                            pic_page_no = pic_item.prov[0].page_no
                            if pic_page_no != table_page_no:
                                continue
                            ios = pic_bbox.intersection_over_self(table_bbox)
                            if ios > 0.5:  # picture가 50% 이상 table 안에 포함되면 table 안의 picture로 간주
                                pic_idx_list.append(j)
                    if pic_idx_list:
                        pic_idx_list = sorted(pic_idx_list)

                if not pic_idx_list:
                    continue

                for pic_idx in pic_idx_list:
                    for g in items_group[pic_idx]:
                        items_group[idx].append(g)
                    items_group[pic_idx] = None  # 나중에 None 제거
                    b_modified = True

            if b_modified:
                items_group = [it for it in items_group if it is not None]

            return items_group

        # ================================================================
        # 1단계: 섹션 헤더 기준으로 분할
        # ================================================================

        sections = []  # [(items, header_infos, header_short_infos), ...]
        cur_items, cur_h_infos, cur_h_short = [], [], []

        for i, item in enumerate(items):
            h_info = header_info_list[i] if i < len(header_info_list) else {}
            h_short = header_short_info_list[i] if i < len(header_short_info_list) else {}

            is_section_boundary = self._is_section_header(item) or is_local_section_boundary(i)

            # 섹션 헤더 또는 로컬 블록 제목을 만나면
            if is_section_boundary:
                # 이전 섹션이 있으면 저장
                if cur_items:
                    sections.append((cur_items, cur_h_infos, cur_h_short))

                # 새로운 섹션 시작
                cur_items = [item]
                cur_h_infos = [h_info]
                cur_h_short = [h_short]
            else:
                # 섹션 헤더가 아니면 현재 섹션에 추가
                cur_items.append(item)
                cur_h_infos.append(h_info)
                cur_h_short.append(h_short)

        # 마지막 섹션 저장
        if cur_items:
            sections.append((cur_items, cur_h_infos, cur_h_short))

        # ================================================================
        # 2단계: 각 섹션의 텍스트에 heading 붙이기
        # ================================================================

        sections_with_text = []
        for items, header_infos, header_short_infos in sections:
            text = self._generate_section_text_with_heading(
                items, header_short_infos, dl_doc, **kwargs
            )
            sections_with_text.append((
                text,
                items,
                header_infos,
                header_short_infos
            ))

        # ================================================================
        # 2.5단계: 너무 긴 청크 또는 너무 넓은 페이지 범위는 분할
        # ================================================================
        i = 0
        while i < len(sections_with_text):
            text, items, h_infos, h_short = sections_with_text[i]
            token_count = self._count_tokens(text)
            token_split_needed = self.max_tokens > 0 and token_count >= self.max_tokens
            page_split_needed = (
                self.max_pages_per_chunk > 0
                and get_page_span(items) > self.max_pages_per_chunk
            )
            if not token_split_needed and not page_split_needed:
                i += 1
                continue

            # caption 및 table 내 그림은 같은 섹션에 있도록 조정
            items_group=[[(item, info, short)] for item, info, short in zip(items, h_infos, h_short)]
            items_group = adjust_captions(items_group)
            items_group = adjust_pictures_in_tables(items_group)

            # 각 아이템 별 token 수 계산
            item_token_counts = []
            for group in items_group:
                cur_count = 0
                for g in group:
                    cur_count += self._count_tokens(get_text_from_item(g[0]))
                item_token_counts.append(cur_count)

            if page_split_needed:
                split_info = split_items_by_token_and_page_limits(
                    items_group,
                    item_token_counts,
                    self.max_tokens,
                    self.max_pages_per_chunk,
                )
            else:
                split_info = split_items_evenly_by_tokens(item_token_counts, self.max_tokens)

            # item_groups를 섹션으로 다시 구성
            new_sections = []
            for (a, b) in split_info:

                # 각 그룹에서 items, h_infos, h_short로 분리
                group_items = []
                group_h_infos = []
                group_h_short = []
                for idx in range(a, b):
                    for g in items_group[idx]:
                        group_items.append(g[0])
                        group_h_infos.append(g[1])
                        group_h_short.append(g[2])

                new_text = self._generate_section_text_with_heading(
                    group_items, group_h_short, dl_doc, **kwargs
                )
                new_sections.append((new_text, group_items, group_h_infos, group_h_short))

            # 원래 섹션을 새로 분할된 섹션들로 교체
            sections_with_text.pop(i)
            for new_section in reversed(new_sections):
                sections_with_text.insert(i, new_section)
            i += len(new_sections)

        # ================================================================
        # 3단계: 단독 타이틀(1줄만) → 다음 섹션으로 병합
        # ================================================================

        for i in range(len(sections_with_text) - 2, -1, -1):
            text, items, h_infos, h_short = sections_with_text[i]

            # 아이템이 하나인 섹션 헤더만 검사
            if len(items) != 1 or not self._is_section_header(items[0]):
                continue

            # 문단이 이미 구성된 것은 제외 (문자 수가 30자 이상이면 문단을 구성했다고 간주)
            item_text = "".join(getattr(it, "text", "") for it in items)
            if len(item_text) > 30:
                continue

            # 현재 섹션헤더 레벨이 다음 섹션헤더 레벨보다 더 높은 경우에만 병합 (높은 레벨이 더 작은 숫자)
            n_text, n_items, n_h_infos, n_h_short = sections_with_text[i + 1]
            current_level = get_header_level(h_infos, first=False)
            next_level = get_header_level(n_h_infos, first=True)
            if 0 <= next_level < current_level:
                continue
            if (
                self.max_pages_per_chunk > 0
                and get_page_span(items + n_items) > self.max_pages_per_chunk
            ):
                continue

            # 다음 섹션과 병합
            sections_with_text[i] = (text + '\n' + n_text, items + n_items, h_infos + n_h_infos, h_short + n_h_short)
            sections_with_text.pop(i + 1)

        # ================================================================
        # 4단계: 토큰 기준 병합
        # ================================================================

        result_chunks = []
        merged_texts, merged_items = [], []
        merged_header_infos, merged_header_short_infos = [], []

        for text, items, header_infos, header_short_infos in sections_with_text:

            b_new_chunk = False

            #----------------------------------
            # 병합 가능 여부 판단

            # 병합 가능 토큰 수 계산
            test_tokens = self._count_tokens("\n".join(merged_texts + [text]))
            test_page_span = get_page_span(merged_items + items)

            # 현재 섹션헤더 레벨과 병합된 섹션헤더 레벨
            section_level = get_header_level(header_infos, first=True)
            merged_level = get_header_level(merged_header_infos, first=False)

            # 토큰 수 초과 시 새로운 청크 생성
            if self.max_tokens > 0 and test_tokens > self.max_tokens and len(merged_texts) > 0:
                b_new_chunk = True
            # 페이지 범위가 너무 넓어지면 새로운 청크 생성
            elif (
                self.max_pages_per_chunk > 0
                and test_page_span > self.max_pages_per_chunk
                and len(merged_texts) > 0
            ):
                b_new_chunk = True
            # TOC 기반 헤더가 적용된 문서는 섹션 경계를 보존해 과도한 병합을 방지
            elif not self.merge_peers and len(merged_texts) > 0:
                b_new_chunk = True
            # 현재 섹션헤더 레벨이 더 높으면 새로운 청크 생성
            elif 0 <= section_level < merged_level:
                b_new_chunk = True
            #----------------------------------

            # 새로운 청크 생성
            if b_new_chunk:
                cur_chunk = get_current_chunk(doc_chunk, merged_texts, merged_header_short_infos, merged_items)
                if cur_chunk:
                    result_chunks.append(cur_chunk)

                # 새로운 병합 시작
                merged_texts = [text]
                merged_items = items
                merged_header_infos = header_infos
                merged_header_short_infos = header_short_infos
            else:
                # 현재 섹션 병합
                merged_texts.append(text)
                merged_items.extend(items)
                merged_header_infos.extend(header_infos)
                merged_header_short_infos.extend(header_short_infos)

        # 마지막 병합된 items 처리
        cur_chunk = get_current_chunk(doc_chunk, merged_texts, merged_header_short_infos, merged_items)
        if cur_chunk:
            result_chunks.append(cur_chunk)

        return result_chunks

    def chunk(self, dl_doc: DoclingDocument, **kwargs: Any) -> Iterator[BaseChunk]:
        """문서를 청킹하여 반환

        Args:
            dl_doc: 청킹할 문서

        Yields:
            토큰 제한에 맞게 분할된 청크들
        """
        doc_chunks = list(self.preprocess(dl_doc=dl_doc, **kwargs))

        if not doc_chunks:
            return iter([])

        doc_chunk = doc_chunks[0]  # preprocess는 하나의 청크만 반환

        final_chunks = self._split_document_by_tokens(doc_chunk, dl_doc, **kwargs)

        return iter(final_chunks)


class GenOSVectorMeta(BaseModel):
    class Config:
        extra = 'allow'

    text: str = None
    n_char: int = None
    n_word: int = None
    n_line: int = None
    e_page: int = None
    i_page: int = None
    i_chunk_on_page: int = None
    n_chunk_of_page: int = None
    i_chunk_on_doc: int = None
    n_chunk_of_doc: int = None
    n_page: int = None
    reg_date: str = None
    chunk_bboxes: str = None
    media_files: str = None
    title: str = None
    subject: str = None
    category: str = None
    created_date: int = None
    appendix: str = None ## !! appendix feature (2025-09-30, geonhee kim) !!
    file_path: Optional[str] = None
    doc_no : Optional[str] = None
    relate_to : Optional[str] = None


class GenOSVectorMetaBuilder:
    def __init__(self):
        """빌더 초기화"""
        self.text: Optional[str] = None
        self.n_char: Optional[int] = None
        self.n_word: Optional[int] = None
        self.n_line: Optional[int] = None
        self.i_page: Optional[int] = None
        self.e_page: Optional[int] = None
        self.i_chunk_on_page: Optional[int] = None
        self.n_chunk_of_page: Optional[int] = None
        self.i_chunk_on_doc: Optional[int] = None
        self.n_chunk_of_doc: Optional[int] = None
        self.n_page: Optional[int] = None
        self.reg_date: Optional[str] = None
        self.chunk_bboxes: Optional[str] = None
        self.media_files: Optional[str] = None
        self.title: Optional[str] = None
        self.subject: Optional[str] = None
        self.category: Optional[str] = None
        self.created_date: Optional[int] = None
        self.appendix: Optional[str] = None # !! appendix feature (2025-09-30, geonhee kim) !!
        self.file_path: Optional[str] = None
        self.doc_no = None
        self.relate_to = None

    def set_text(self, text: str) -> "GenOSVectorMetaBuilder":
        """텍스트와 관련된 데이터를 설정"""
        self.text = text
        self.n_char = len(text)
        self.n_word = len(text.split())
        self.n_line = len(text.splitlines())
        return self

    def set_page_info(
            self, i_page: int, i_chunk_on_page: int, n_chunk_of_page: int
    ) -> "GenOSVectorMetaBuilder":
        """페이지 정보 설정"""
        self.i_page = i_page
        self.i_chunk_on_page = i_chunk_on_page
        self.n_chunk_of_page = n_chunk_of_page
        return self

    def set_chunk_index(self, i_chunk_on_doc: int) -> "GenOSVectorMetaBuilder":
        """문서 전체의 청크 인덱스 설정"""
        self.i_chunk_on_doc = i_chunk_on_doc
        return self

    def set_global_metadata(self, **global_metadata) -> "GenOSVectorMetaBuilder":
        """글로벌 메타데이터 병합"""
        for key, value in global_metadata.items():
            if hasattr(self, key):
                setattr(self, key, value)
        return self

    def set_chunk_bboxes(self, doc_items: list, document: DoclingDocument) -> "GenOSVectorMetaBuilder":
        chunk_bboxes = []
        for item in doc_items:
            for prov in getattr(item, "prov", []) or []:
                label = item.self_ref
                type_ = item.label
                size = document.pages.get(prov.page_no).size
                page_no = prov.page_no
                bbox = prov.bbox
                bbox_data = {'l': bbox.l / size.width,
                             't': bbox.t / size.height,
                             'r': bbox.r / size.width,
                             'b': bbox.b / size.height,
                             'coord_origin': bbox.coord_origin.value}
                chunk_bboxes.append({'page': page_no, 'bbox': bbox_data, 'type': type_, 'ref': label})
        self.e_page = max([bbox['page'] for bbox in chunk_bboxes]) if chunk_bboxes else 0
        self.chunk_bboxes = json.dumps(chunk_bboxes)
        return self

    def set_media_files(self, doc_items: list) -> "GenOSVectorMetaBuilder":
        temp_list = []
        for item in doc_items:
            if isinstance(item, PictureItem) and item.image:
                path = str(item.image.uri)
                name = path.rsplit("/", 1)[-1]
                temp_list.append({'name': name, 'type': 'image', 'ref': item.self_ref})
        self.media_files = json.dumps(temp_list)
        return self

    def build(self) -> GenOSVectorMeta:
        """설정된 데이터를 사용해 최종적으로 GenOSVectorMeta 객체 생성"""
        return GenOSVectorMeta(
            text=self.text,
            n_char=self.n_char,
            n_word=self.n_word,
            n_line=self.n_line,
            i_page=self.i_page,
            e_page=self.e_page,
            i_chunk_on_page=self.i_chunk_on_page,
            n_chunk_of_page=self.n_chunk_of_page,
            i_chunk_on_doc=self.i_chunk_on_doc,
            n_chunk_of_doc=self.n_chunk_of_doc,
            n_page=self.n_page,
            reg_date=self.reg_date,
            chunk_bboxes=self.chunk_bboxes,
            media_files=self.media_files,
            title=self.title,
            subject=self.subject,
            category=self.category,
            created_date=self.created_date,
            appendix=self.appendix or "", # !! appendix feature (2025-09-30, geonhee kim) !!
            file_path=self.file_path,
        )


class HwpProcessor:
    def __init__(self):
        pass

    def get_paths(self, file_path: str):
        """이미지 등 리소스가 저장될 경로 계산 (기존 로직 유지)"""
        output_path, output_file = os.path.split(file_path)
        filename, _ = os.path.splitext(output_file)
        artifacts_dir = Path(f"{output_path}/{filename}")
        reference_path = None if artifacts_dir.is_absolute() else artifacts_dir.parent
        return artifacts_dir, reference_path

    def safe_join(self, iterable):
        """청크 내 헤딩들을 텍스트로 합침"""
        if not isinstance(iterable, (list, tuple, set)):
            return ''
        return ' '.join(map(str, iterable)) + '\n'

    def load_documents(self, file_path: str, **kwargs: dict) -> DoclingDocument:
        """SDK 백엔드를 통해 문서를 로드"""
        # 요청마다 독립적인 pipeline_options 생성 (공유 상태 변이 방지) --> save_images, dump_sdk_output
        pipeline_options = PipelineOptions()
        pipeline_options.save_images = kwargs.get('save_images', True)

        use_hwp_sdk = kwargs.get('use_hwp_sdk', True)
        pipeline_options.dump_sdk_output = kwargs.get('dump_sdk_output', False) if use_hwp_sdk else False

        if use_hwp_sdk:
            converter = DocumentConverter(
                format_options={
                    InputFormat.HWP: HwpxFormatOption(
                        pipeline_options=pipeline_options,
                        backend=GenosHwpDocumentBackend
                    ),
                    InputFormat.XML_HWPX: HwpxFormatOption(
                        pipeline_options=pipeline_options,
                        backend=GenosHwpDocumentBackend
                    ),
                }
            )
        else:
            converter = DocumentConverter(
                format_options={
                    InputFormat.HWP: HwpxFormatOption(
                        pipeline_options=pipeline_options,
                        backend=HwpDocumentBackend
                    ),
                    InputFormat.XML_HWPX: HwpxFormatOption(
                        pipeline_options=pipeline_options,
                        backend=HwpxDocumentBackend
                    ),
                }
            )

        conv_result: ConversionResult = converter.convert(Path(file_path).resolve(), raises_on_error=True)
        return conv_result.document


# =============================================================================
# 수식 매칭 유틸리티 (merge_04_claude.py 인라인)
# =============================================================================

_FORMULA_MIN_TEXT_LEN: int = 3
_FORMULA_EMBED_RATIO: float = 0.6
_MAX_COMPARE_LEN: int = 2000
_MATCH_THRESHOLD: float = 0.30
_ENRICH_THRESHOLD: float = 0.30

_dp_utils = DocumentEnrichmentUtils(DataEnrichmentOptions())

_LATEX_CMD_RE = re.compile(r"\\[a-zA-Z]+\*?")
_KOR_RE = re.compile(r"[가-힣ㄱ-ㅎㅏ-ㅣ]+")


@dataclass
class _HwpFormula:
    text: str
    norm: str
    page_no: int


def _normalize_text(text: str) -> str:
    """매칭용 텍스트 정규화: GLYPH 토큰 제거, LaTeX 구분자 제거, 소문자, 공백 정리."""
    text = re.sub(r"GLYPH\w*", "", text or "")
    text = re.sub(r"\$\$", "", text)
    return re.sub(r"\s+", " ", text).strip().lower()


def _text_similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a[:_MAX_COMPARE_LEN], b[:_MAX_COMPARE_LEN]).ratio()


def _formula_coverage_score(pdf_text: str, hwp_text: str) -> float:
    if not pdf_text or not hwp_text:
        return 0.0
    sm = SequenceMatcher(None, pdf_text, hwp_text)
    matched = sum(b.size for b in sm.get_matching_blocks())
    return matched / len(hwp_text)


def _normalize_and_map(text: str) -> Tuple[str, List[int]]:
    chars: List[str] = []
    positions: List[int] = []

    i = 0
    while i < len(text):
        glyph_match = re.match(r"GLYPH\w*", text[i:])
        if glyph_match:
            i += glyph_match.end()
            continue
        if text[i : i + 2] == "$$":
            i += 2
            continue
        chars.append(text[i].lower())
        positions.append(i)
        i += 1

    norm_chars: List[str] = []
    norm_positions: List[int] = []
    prev_space = False
    for ch, pos in zip(chars, positions):
        is_space = ch in (" ", "\t", "\n", "\r")
        if is_space:
            if not prev_space:
                norm_chars.append(" ")
                norm_positions.append(pos)
            prev_space = True
        else:
            norm_chars.append(ch)
            norm_positions.append(pos)
            prev_space = False

    while norm_chars and norm_chars[0] == " ":
        norm_chars.pop(0)
        norm_positions.pop(0)
    while norm_chars and norm_chars[-1] == " ":
        norm_chars.pop()
        norm_positions.pop()

    return "".join(norm_chars), norm_positions


def _find_formula_span_in_text(
    pdf_text: str,
    hwp_formula: str,
    min_score: float = 0.5,
) -> Optional[Tuple[int, int]]:
    norm_hwp = _normalize_text(hwp_formula)
    if not norm_hwp or not pdf_text:
        return None

    norm_pdf, pos_map = _normalize_and_map(pdf_text)
    if not norm_pdf or not pos_map:
        return None

    sm = SequenceMatcher(None, norm_pdf, norm_hwp)
    blocks = [b for b in sm.get_matching_blocks() if b.size > 0]
    if not blocks:
        return None

    total_matched = sum(b.size for b in blocks)
    if total_matched / len(norm_hwp) < min_score:
        return None

    norm_start = min(b.a for b in blocks)
    norm_end_inclusive = max(b.a + b.size - 1 for b in blocks)

    if norm_start >= len(pos_map) or norm_end_inclusive >= len(pos_map):
        return None

    orig_start = pos_map[norm_start]
    orig_end = pos_map[norm_end_inclusive] + 1
    return (orig_start, orig_end)


def _strip_latex(text: str) -> str:
    """LaTeX 마크업 제거 → bare content."""
    text = _LATEX_CMD_RE.sub(" ", text)
    text = re.sub(r"[{}]", "", text)
    text = re.sub(r"[_^]", "", text)
    return re.sub(r"\s+", " ", text).strip().lower()


def _latex_coverage_score(pdf_norm: str, hwp_norm: str) -> float:
    hwp_stripped = _strip_latex(hwp_norm)
    if not hwp_stripped:
        return 0.0
    sm = SequenceMatcher(None, pdf_norm, hwp_stripped)
    matched = sum(b.size for b in sm.get_matching_blocks())
    return matched / len(hwp_stripped)


def _korean_coverage_score(pdf_norm: str, hwp_norm: str) -> float:
    hwp_kor = "".join(_KOR_RE.findall(hwp_norm))
    if not hwp_kor:
        return 0.0
    pdf_kor = "".join(_KOR_RE.findall(pdf_norm))
    if not pdf_kor:
        return 0.0
    sm = SequenceMatcher(None, pdf_kor, hwp_kor)
    matched = sum(b.size for b in sm.get_matching_blocks())
    return matched / len(hwp_kor)


def _collect_hwp_formulas(hwp_doc: DoclingDocument) -> List[_HwpFormula]:
    """HWP 문서에서 formula 레이블 아이템을 리딩오더로 수집."""
    formulas: List[_HwpFormula] = []
    for item, _ in hwp_doc.iterate_items(
        included_content_layers={ContentLayer.BODY}
    ):
        if not isinstance(item, TextItem):
            continue
        label = getattr(item, "label", None)
        is_formula = (
            label == DocItemLabel.FORMULA
            or (isinstance(label, str) and label == "formula")
        )
        if not is_formula:
            continue
        text = item.text or ""
        if len(text.strip()) < _FORMULA_MIN_TEXT_LEN:
            continue
        page_no = item.prov[0].page_no if item.prov else 0
        formulas.append(_HwpFormula(
            text=text,
            norm=_normalize_text(text),
            page_no=page_no,
        ))
    return formulas


# =============================================================================
# 벡터 레벨 수식 처리 (신규)
# =============================================================================

def _match_formulas_to_vectors(
    vectors: List[GenOSVectorMeta],
    hwp_formulas: List[_HwpFormula],
    match_threshold: float = _MATCH_THRESHOLD,
) -> List[dict]:
    """DP 순서보존 최적 매칭: 벡터 텍스트 ↔ HWP 수식."""
    candidate_matches = []
    for v_idx, vec in enumerate(vectors):
        norm = _normalize_text(vec.text or "")
        candidates = []
        for h_idx, hwp in enumerate(hwp_formulas):
            if not hwp.norm:
                continue
            ratio     = _text_similarity(norm, hwp.norm)
            cov       = _formula_coverage_score(norm, hwp.norm)
            latex_cov = _latex_coverage_score(norm, hwp.norm)
            kor_cov   = _korean_coverage_score(norm, hwp.norm)
            score = max(ratio, cov * 0.85, latex_cov * 0.80, kor_cov * 0.75)
            if score >= match_threshold:
                candidates.append((h_idx, score, hwp))
        candidate_matches.append((v_idx, candidates))
    return _dp_utils._select_best_toc_text_matching(candidate_matches)


def _enrich_vectors_with_hwp_formulas(
    vectors: List[GenOSVectorMeta],
    hwp_doc: DoclingDocument,
    match_threshold: float = _MATCH_THRESHOLD,
    enrich_threshold: float = _ENRICH_THRESHOLD,
) -> List[GenOSVectorMeta]:
    """벡터 텍스트에 HWP 수식을 반영.

    bbox/페이지 메타를 보존하면서 text/n_char만 갱신.
    HWP 수식이 없으면 vectors를 그대로 반환.
    """
    hwp_formulas = _collect_hwp_formulas(hwp_doc)
    if not hwp_formulas:
        _log.debug("[수식 후처리] HWP 수식 없음, 원본 벡터 반환")
        return vectors

    matches = _match_formulas_to_vectors(vectors, hwp_formulas, match_threshold)
    vectors = list(vectors)  # shallow copy — 변경 전 원본 보존
    applied = 0

    for match in matches:
        if match["score"] < enrich_threshold:
            continue
        v_idx = match["toc_idx"]
        hwp   = hwp_formulas[match["text_idx"]]
        vec   = vectors[v_idx]
        text  = vec.text or ""
        norm  = _normalize_text(text)
        length_ratio = len(hwp.norm) / max(len(norm), 1)

        # 벡터 자체가 이미 수식 블록이면 인라인 임베딩 금지 (블록 교체만)
        is_formula_block = text.strip().startswith("$$")

        new_text = None
        if not is_formula_block and length_ratio < _FORMULA_EMBED_RATIO:
            # 인라인: 스팬 탐색 후 $...$ 삽입 (LaTeX stripped fallback 포함)
            span = _find_formula_span_in_text(text, hwp.text)
            if span is None:
                stripped = _strip_latex(hwp.text)
                if len(stripped) >= 3:
                    span = _find_formula_span_in_text(text, stripped)
            if span:
                s, e = span
                new_text = text[:s] + f"${hwp.text}$" + text[e:]
        elif length_ratio >= _FORMULA_EMBED_RATIO:
            # 블록 전체: $$\n...\n$$ 교체 (수식이 블록 대비 충분히 큰 경우)
            new_text = f"$$\n{hwp.text}\n$$"
        # is_formula_block=True이고 length_ratio < _FORMULA_EMBED_RATIO: 건너뜀
        # (HWP 수식이 너무 짧아서 전체 수식 블록을 대체하는 것은 부적절)

        if new_text is not None:
            vectors[v_idx] = vec.model_copy(update={"text": new_text, "n_char": len(new_text)})
            applied += 1

    _log.debug(f"[수식 후처리] 적용={applied}/{len(matches)} 후보, 벡터={len(vectors)}")
    return vectors


class DocumentProcessor:

    def __init__(self):
        '''
        initialize Document Converter
        '''
        self.ocr_endpoint = os.getenv("OCR_ENDPOINT", "http://192.168.73.172:48080/ocr")
        ocr_lang = [
            lang.strip()
            for lang in os.getenv("OCR_LANG", "korean").split(",")
            if lang.strip()
        ] or ["korean"]
        ocr_options = PaddleOcrOptions(
            force_full_page_ocr=False,
            lang=ocr_lang,
            ocr_endpoint=self.ocr_endpoint,
            text_score=0.3)

        self.page_chunk_counts = defaultdict(int)
        self.toc_on = 1
        self.image_description_on = 0
        self.enhanced_image_description_on = 0
        self.table_refine_on = 0
        self.subject = None
        self.subject_metadata = {"subject": "", "category": "기타", "title": ""}
        device = AcceleratorDevice.AUTO
        num_threads = _as_positive_int(os.getenv("GENOS_NUM_THREADS"), 8)
        accelerator_options = AcceleratorOptions(num_threads=num_threads, device=device)
        # PDF 파이프라인 옵션 설정
        self.pipe_line_options = PdfPipelineOptions()
        self.pipe_line_options.generate_page_images = True
        self.pipe_line_options.generate_picture_images = True
        self.pipe_line_options.do_ocr = False
        self.pipe_line_options.ocr_options = ocr_options
        self.pipe_line_options.images_scale = 2

        # self.pipe_line_options.ocr_options.lang = ["ko", 'en']
        # self.pipe_line_options.ocr_options.model_storage_directory = "./.EasyOCR/model"
        # self.pipe_line_options.ocr_options.force_full_page_ocr = True
        # ocr_options = TesseractOcrOptions()
        # ocr_options.lang = ['kor', 'kor_vert', 'eng', 'jpn', 'jpn_vert']
        # ocr_options.path = './.tesseract/tessdata'
        # self.pipe_line_options.ocr_options = ocr_options
        # self.pipe_line_options.artifacts_path = Path("/models/")

        # layout 모델로 GENOS_LAYOUT 사용
        self.pipe_line_options.layout_options.layout_model_type = LayoutModelType.GENOS_LAYOUT
        # 운영망 T4
        # self.pipe_line_options.layout_options.genos_layout_options.endpoint = "https://genos.genon.ai:3443/api/gateway/rep/serving/733/v1/chat/completions"
        # self.pipe_line_options.layout_options.genos_layout_options.api_key = os.getenv("GENOS_LAYOUT_API_KEY", "")

        # H100 gpu
        self.pipe_line_options.layout_options.genos_layout_options.endpoint = os.getenv(
            "GENOS_LAYOUT_ENDPOINT",
            "http://192.168.75.174:26001/v1/chat/completions",
        )
        self.pipe_line_options.layout_options.genos_layout_options.api_key = os.getenv(
            "GENOS_LAYOUT_API_KEY",
            "",
        )

        # genos layout 모델은 batch size를 32로 설정
        settings.perf.page_batch_size = _as_positive_int(os.getenv("GENOS_PAGE_BATCH_SIZE"), 32)

        self.pipe_line_options.do_table_structure = True
        # VLM 기반 테이블 구조 모델 사용
        # self.pipe_line_options.table_structure_options.table_structure_model_type = TableStructureModelType.VLM
        # self.pipe_line_options.table_structure_options.vlm_table_structure_options.url = "http://localhost:8000/v1/chat/completions"
        # self.pipe_line_options.table_structure_options.vlm_table_structure_options.api_key = ""
        # self.pipe_line_options.table_structure_options.vlm_table_structure_options.model = ""

        self.pipe_line_options.table_structure_options.do_cell_matching = True
        self.pipe_line_options.table_structure_options.mode = TableFormerMode.ACCURATE
        self.pipe_line_options.accelerator_options = accelerator_options

        # Simple 파이프라인 옵션을 인스턴스 변수로 저장
        self.simple_pipeline_options = PipelineOptions()
        self.simple_pipeline_options.save_images = False

        # ocr 파이프라인 옵션
        self.ocr_pipe_line_options = PdfPipelineOptions()
        self.ocr_pipe_line_options = self.pipe_line_options.model_copy(deep=True)
        self.ocr_pipe_line_options.do_ocr = True
        self.ocr_pipe_line_options.ocr_options = ocr_options.model_copy(deep=True)
        self.ocr_pipe_line_options.ocr_options.force_full_page_ocr = True

        # 기본 컨버터들 생성
        self._create_converters()

        toc_api_base_url = os.getenv(
            "TOC_API_BASE_URL",
            "https://openrouter.ai/api/v1/chat/completions",
        )
        metadata_api_base_url = os.getenv(
            "METADATA_API_BASE_URL",
            "https://openrouter.ai/api/v1/chat/completions",
        )
        toc_api_key = _env_first(
            "TOC_API_KEY",
            "OPENROUTER_API_KEY",
            default=DEFAULT_OPENROUTER_API_KEY,
        )
        metadata_api_key = _env_first(
            "METADATA_API_KEY",
            "OPENROUTER_API_KEY",
            default=DEFAULT_OPENROUTER_API_KEY,
        )
        toc_model = os.getenv("TOC_MODEL", "google/gemini-3.1-pro-preview")
        metadata_model = os.getenv("METADATA_MODEL", "google/gemini-3-flash-preview")

        # enrichment 옵션 설정
        self.enrichment_options = DataEnrichmentOptions(
            do_toc_enrichment=True,
            extract_metadata=True,
            toc_api_provider="custom",
            toc_api_base_url=toc_api_base_url,
            metadata_api_base_url=metadata_api_base_url,
            toc_api_key=toc_api_key,
            metadata_api_key=metadata_api_key,
            toc_model=toc_model,
            metadata_model=metadata_model,
            toc_temperature=0.0,
            toc_top_p=0.00001,
            toc_seed=33,
            toc_max_tokens=70000,

            toc_system_prompt=toc_system_prompt,
            toc_user_prompt=toc_user_prompt,
        )

        subject_serving_id = _as_positive_int(os.getenv("SUBJECT_SERVING_ID"), 729)
        subject_bearer_token = _env_first(
            "SUBJECT_BEARER_TOKEN",
            "GENOS_BEARER_TOKEN",
            default=DEFAULT_TABLE_REFINER_BEARER_TOKEN,
        )
        subject_genos_url = _env_first(
            "SUBJECT_GENOS_URL",
            "GENOS_URL",
            default="https://genos.genon.ai",
        )
        self.llm = llm_serving(
            serving_id=subject_serving_id,
            bearer_token=subject_bearer_token,
            genos_url=subject_genos_url,
        )

        image_description_api_base_url = os.getenv(
            "IMAGE_DESCRIPTION_API_BASE_URL",
            "https://openrouter.ai/api/v1/chat/completions",
        )
        image_description_api_key = _env_first(
            "IMAGE_DESCRIPTION_API_KEY",
            "OPENROUTER_API_KEY",
            default=DEFAULT_OPENROUTER_API_KEY,
        )
        image_description_model = os.getenv("IMAGE_DESCRIPTION_MODEL", "google/gemini-3-flash-preview")

        self.pipe_line_options.do_picture_description = False
        self.pipe_line_options.enable_remote_services = False
        self.pipe_line_options.picture_description_options = PictureDescriptionApiOptions(
            url=image_description_api_base_url,
            params=dict(model=image_description_model, max_tokens=5000, temperature=0.1),
            headers={"Authorization": "Bearer " + image_description_api_key},
            prompt="",
            timeout=120,
            scale=4.0,
            picture_area_threshold=0.001,
        )
        self.ocr_pipe_line_options.picture_description_options = (
            self.pipe_line_options.picture_description_options.model_copy(deep=True)
        )

    def _create_converters(self):
        """컨버터들을 생성하는 헬퍼 메서드"""
        self.converter = DocumentConverter(
                format_options={
                    InputFormat.PDF: PdfFormatOption(
                        pipeline_options=self.pipe_line_options,
                        backend=PyPdfiumDocumentBackend
                    ),
                }
            )
        self.second_converter = DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(
                    pipeline_options=self.pipe_line_options,
                    backend=DoclingParseV4DocumentBackend
                ),
            },
        )
        self.ocr_converter = DocumentConverter(
                format_options={
                    InputFormat.PDF: PdfFormatOption(
                        pipeline_options=self.ocr_pipe_line_options,
                        backend=DoclingParseV4DocumentBackend
                    ),
                }
            )
        self.ocr_second_converter = DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(
                    pipeline_options=self.ocr_pipe_line_options,
                    backend=PyPdfiumDocumentBackend
                ),
            },
        )

    def _configure_picture_description(self, image_description_on: int,
                                       enhanced_image_description_on: int,
                                       file_path: str,
                                       **kwargs) -> None:
        """기본/OCR 컨버터가 같은 이미지 설명 옵션을 사용하도록 동기화한다."""
        image_option = image_description_on | enhanced_image_description_on
        enabled = image_option == 1
        prompt = ""
        mode = "disabled"

        if enabled:
            subject = kwargs.get("external_subject") or self.subject or os.path.splitext(os.path.basename(file_path))[0]
            self.subject = subject
            mode = "enhanced" if enhanced_image_description_on == 1 else "basic"
            prompt_template = (
                enhanced_image_description_prompt
                if enhanced_image_description_on == 1
                else image_description_prompt
            )
            prompt = prompt_template.format(subject=subject)
        else:
            self.subject = None

        self.pipe_line_options.do_picture_description = enabled
        self.pipe_line_options.enable_remote_services = enabled
        self.pipe_line_options.picture_description_options.prompt = prompt

        self.ocr_pipe_line_options.do_picture_description = enabled
        self.ocr_pipe_line_options.enable_remote_services = enabled
        self.ocr_pipe_line_options.picture_description_options = (
            self.pipe_line_options.picture_description_options.model_copy(deep=True)
        )

        msg = (
            f"[image_description] mode={mode} enabled={enabled} "
            f"prompt_chars={len(prompt)} primary_do={self.pipe_line_options.do_picture_description} "
            f"ocr_do={self.ocr_pipe_line_options.do_picture_description}"
        )
        print(msg, flush=True)
        _log.info(msg)

    def _log_picture_description_state(self, document: DoclingDocument, stage: str) -> None:
        """문서 내 그림 설명 annotation 상태를 컨테이너 로그에 남긴다."""
        pictures = []
        try:
            for item, _ in document.iterate_items():
                if isinstance(item, PictureItem):
                    pictures.append(item)
        except Exception as e:
            _log.warning("[image_description] state scan failed stage=%s error=%s", stage, e)
            return

        annotated_pictures = 0
        annotation_chars = 0
        annotation_counts = []
        empty_refs = []
        for item in pictures:
            annotations = getattr(item, "annotations", []) or []
            text_chars = sum(len(getattr(annotation, "text", "") or "") for annotation in annotations)
            annotation_counts.append(len(annotations))
            annotation_chars += text_chars
            if text_chars > 0:
                annotated_pictures += 1
            else:
                empty_refs.append(str(getattr(item, "self_ref", "")))

        sample_empty_refs = ",".join(ref for ref in empty_refs[:5] if ref)
        msg = (
            f"[image_description] stage={stage} pictures={len(pictures)} "
            f"annotated_pictures={annotated_pictures} annotation_chars={annotation_chars} "
            f"annotation_counts={annotation_counts[:10]} empty_refs={sample_empty_refs}"
        )
        print(msg, flush=True)
        _log.info(msg)

    def load_documents_with_docling(self, file_path: str, **kwargs: dict) -> DoclingDocument:
        # kwargs에서 save_images 값을 가져와서 옵션 업데이트
        save_images = _as_bool_flag(kwargs.get('save_images', True), True)
        include_wmf = _as_bool_flag(kwargs.get('include_wmf', False), False)

        # save_images 옵션이 현재 설정과 다르면 컨버터 재생성
        if (self.simple_pipeline_options.save_images != save_images or
            getattr(self.simple_pipeline_options, 'include_wmf', False) != include_wmf):
            self.simple_pipeline_options.save_images = save_images
            self.simple_pipeline_options.include_wmf = include_wmf
            self._create_converters()

        try:
            conv_result: ConversionResult = self.converter.convert(file_path, raises_on_error=True)
        except Exception as e:
            _log.warning(
                "[docling] primary PyPdfium conversion failed; retrying with DoclingParseV4: %s",
                e,
            )
            conv_result: ConversionResult = self.second_converter.convert(file_path, raises_on_error=True)
        return conv_result.document

    def load_documents_with_docling_ocr(self, file_path: str, **kwargs: dict) -> DoclingDocument:
        # kwargs에서 save_images 값을 가져와서 옵션 업데이트
        save_images = _as_bool_flag(kwargs.get('save_images', True), True)
        include_wmf = _as_bool_flag(kwargs.get('include_wmf', False), False)

        # save_images 옵션이 현재 설정과 다르면 컨버터 재생성
        if (self.simple_pipeline_options.save_images != save_images or
            getattr(self.simple_pipeline_options, 'include_wmf', False) != include_wmf):
            self.simple_pipeline_options.save_images = save_images
            self.simple_pipeline_options.include_wmf = include_wmf
            self._create_converters()

        try:
            conv_result: ConversionResult = self.ocr_converter.convert(file_path, raises_on_error=True)
        except Exception as e:
            _log.warning(
                "[docling_ocr] primary DoclingParseV4 OCR conversion failed; retrying with PyPdfium: %s",
                e,
            )
            conv_result: ConversionResult = self.ocr_second_converter.convert(file_path, raises_on_error=True)
        return conv_result.document

    def load_documents(self, file_path: str, **kwargs: dict) -> DoclingDocument:
        return self.load_documents_with_docling(file_path, **kwargs)

    def split_documents(self, documents: DoclingDocument, **kwargs: dict) -> List[DocChunk]:
        max_chunk_size = _as_nonnegative_int(
            _first_kwarg(kwargs, 'chunk_size', 'max_chunk_size', 'chunk_max_tokens', default=4000),
            4000,
        )
        max_pages_per_chunk = _as_nonnegative_int(
            kwargs.get(
                'max_pages_per_chunk',
                kwargs.get('max_chunk_pages', kwargs.get('max_chunk_page_span', 0)),
            ),
            0,
        )
        kwargs['max_chunk_size'] = max_chunk_size
        kwargs['chunk_size'] = max_chunk_size
        kwargs['max_pages_per_chunk'] = max_pages_per_chunk
        infer_local_section_boundaries = _as_int_flag(
            kwargs.get(
                'infer_local_section_boundaries',
                kwargs.get('soft_section_boundaries_on', 1),
            ),
            1,
        )
        kwargs['infer_local_section_boundaries'] = infer_local_section_boundaries
        preserve_section_boundaries = _as_int_flag(
            kwargs.get('preserve_section_boundaries', 1),
            1,
        )
        kwargs['preserve_section_boundaries'] = preserve_section_boundaries
        chunker: GenosBucketChunker = GenosBucketChunker(
            max_tokens = max_chunk_size,
            max_pages_per_chunk = max_pages_per_chunk,
            merge_peers = preserve_section_boundaries != 1
        )

        chunks: List[DocChunk] = list(chunker.chunk(dl_doc=documents, **kwargs))
        _log.info(
            "[chunking] chunks=%s max_tokens=%s max_pages_per_chunk=%s preserve_section_boundaries=%s infer_local_section_boundaries=%s",
            len(chunks),
            max_chunk_size,
            max_pages_per_chunk,
            preserve_section_boundaries,
            infer_local_section_boundaries,
        )
        for chunk in chunks:
            if chunk.meta.doc_items[0].prov:
                self.page_chunk_counts[chunk.meta.doc_items[0].prov[0].page_no] += 1
        return chunks

    def safe_join(self, iterable):
        if not isinstance(iterable, (list, tuple, set)):
            return ''
        return ''.join(map(str, iterable)) + '\n'

    def parse_created_date(self, date_text: str) -> Optional[int]:
        """
        작성일 텍스트를 파싱하여 YYYYMMDD 형식의 정수로 변환

        Args:
            date_text: 작성일 텍스트 (YYYY-MM 또는 YYYY-MM-DD 형식)

        Returns:
            YYYYMMDD 형식의 정수, 파싱 실패시 None
        """
        if not date_text or not isinstance(date_text, str) or date_text == "None":
            return 0

        # 공백 제거 및 정리
        date_text = date_text.strip()

        # YYYY-MM-DD 형식 매칭
        match_full = re.match(r'^(\d{4})-(\d{1,2})-(\d{1,2})$', date_text)
        if match_full:
            year, month, day = match_full.groups()
            try:
                # 유효한 날짜인지 검증
                datetime(int(year), int(month), int(day))
                return int(f"{year}{month.zfill(2)}{day.zfill(2)}")
            except ValueError:
                pass

        # YYYY-MM 형식 매칭 (일자는 01로 설정)
        match_month = re.match(r'^(\d{4})-(\d{1,2})$', date_text)
        if match_month:
            year, month = match_month.groups()
            try:
                # 유효한 월인지 검증
                datetime(int(year), int(month), 1)
                return int(f"{year}{month.zfill(2)}01")
            except ValueError:
                pass

        # YYYY 형식 매칭 (월일은 0101로 설정)
        match_year = re.match(r'^(\d{4})$', date_text)
        if match_year:
            year = match_year.group(1)
            try:
                datetime(int(year), 1, 1)
                return int(f"{year}0101")
            except ValueError:
                pass

        return 0

    def _is_non_structural_header_text(self, text: str) -> bool:
        if not text:
            return False

        text = re.sub(r"\s+", " ", text).strip()
        compact_text = re.sub(r"\s+", "", text)

        metadata_patterns = [
            r"^작성\s*기준\s*일\s*[:：]?",
            r"^효력\s*발생\s*일\s*[:：]?",
            r"^작성\s*일\s*[:：]?",
            r"^발행\s*일\s*[:：]?",
            r"^공시\s*일\s*[:：]?",
            r"^시행\s*일\s*[:：]?",
            r"^개정\s*일\s*[:：]?",
            r"^문서\s*번호\s*[:：]?",
            r"^담당\s*부서\s*[:：]?",
            r"^작성\s*자\s*[:：]?",
            r"^발행\s*기관\s*[:：]?",
            r"^버전\s*[:：]?",
            r"^문의\s*처\s*[:：]?",
            r"^판매\s*회사\s*[:：]?",
            r"^모집\s*\(?매출\)?\s*증권",
            r"^모집\s*\(?판매\)?\s*기간",
            r"^열람\s*장소\s*[:：]?",
            r"^홈페이지\s*[:：]?",
            r"^전자\s*문서\s*[:：]",
            r"^서면\s*문서\s*[:：]",
        ]
        if any(re.match(pattern, text) for pattern in metadata_patterns):
            return True

        if re.match(r"^\d+\.\s*[^:：]{1,40}\s*[:：]\s*\S+", text):
            return True

        sentence_endings = (
            "습니다",
            "합니다",
            "됩니다",
            "바랍니다",
            "없습니다",
            "있습니다",
            "아닙니다",
            "십시오",
        )
        if len(text) > 60 and any(compact_text.endswith(ending) for ending in sentence_endings):
            return True

        return False

    def restore_header_labels(self, before_doc: DoclingDocument, after_doc: DoclingDocument) -> DoclingDocument:
        original_header_map = {}

        for item, level in before_doc.iterate_items():
            ref = getattr(item, "self_ref", None)
            label = getattr(item, "label", None)
            text = (getattr(item, "text", None) or "").strip()

            if ref and label in [DocItemLabel.TITLE, DocItemLabel.SECTION_HEADER] and not self._is_non_structural_header_text(text):
                original_header_map[ref] = {
                    "label": label,
                    "level": level,
                    "text": text,
                }

        restored_count = 0

        for item, level in after_doc.iterate_items():
            ref = getattr(item, "self_ref", None)
            if not ref or ref not in original_header_map:
                continue

            orig = original_header_map[ref]

            if getattr(item, "label", None) not in [DocItemLabel.TITLE, DocItemLabel.SECTION_HEADER]:
                try:
                    item.label = orig["label"]
                except Exception:
                    pass

                if hasattr(item, "level"):
                    try:
                        item.level = orig["level"]
                    except Exception:
                        pass

                restored_count += 1

            try:
                setattr(item, "_orig_label", orig["label"])
                setattr(item, "_orig_level", orig["level"])
            except Exception:
                pass

        _log.info("[restore_header_labels] restored_count=%s", restored_count)
        return after_doc

    def enrichment(self, document: DoclingDocument, **kwargs: dict) -> DoclingDocument:
        restore_header_labels_on = _as_int_flag(kwargs.get("restore_header_labels_on", 0), 0)
        if restore_header_labels_on == 1:
            import copy
            origin_document = copy.deepcopy(document)
        else:
            origin_document = None

        # 새로운 enriched result 받기
        document = enrich_document(document, self.enrichment_options, **kwargs)
        if self.toc_on == 1 and restore_header_labels_on == 1 and origin_document is not None:
            document = self.restore_header_labels(origin_document, document)
        return document

    def extract_subject(self, file_path: str, external_subject: str = "") -> dict:
        class MetaOutput(BaseModel):
            subject: str
            category: str
            title: str

        system_prompt = """# 역할
당신은 제공된 문서의 핵심 내용을 정확히 파악하고 지정된 기준에 따라 분류하는 '문서 분석 및 분류 전문가'입니다.

# 목표
입력된 문서를 분석하여, 전체 내용을 포괄하는 요약된 주제(subject)와 문서의 유형(category), 그리고 문서 제목(title)을 JSON 형식으로 추출하십시오.

# 요구사항 (규칙)
1. 주제(subject) 작성 규칙
핵심 내용 추출: 문서 전체를 분석하여 핵심 목적과 주요 내용을 300자 이내의 완성된 문장형으로 작성하십시오.
제작처 명시 (필수): 문구 서두에 해당 문서의 제작 주체(예: 금융사, 보험사명 등)를 반드시 포함하십시오.
작성 패턴: "이 문서는 [제작처]에서 작성된 문서로, [주요 내용]을 설명하고 있습니다."
식별 불가 시 처리: 로고, 하단 저작권 표기, 문맥상 발행처 등을 확인했음에도 제작처를 특정할 수 없는 경우에만 "제작처 미상" 또는 "제작처 확인 불가" 문구를 포함하십시오.

2. 카테고리(category) 분류 규칙
분류 기준: 다음 3가지 중 문서의 성격에 가장 부합하는 값 하나만 정확히 출력하십시오.
산출방법서: 보험료, 책임준비금, 해약환급금 등의 수식, 산출 기초지수, 계산 로직이 포함된 경우.
약관: 보험계약의 권리/의무, 보장 내용, 지급 제한 사유 등 법률적/계약적 조항이 중심인 경우.
기타: 위 두 항목의 특성이 명확하지 않거나 안내서, 상품설명서, 홍보물 등에 해당하는 경우.

3. 문서 제목(title) 작성 규칙
추출 및 정제: 표지나 최상단에서 확인되는 공식 명칭을 추출하되, 줄바꿈/특수문자/불필요한 공백은 제거하십시오.
구체성 확보: "보험약관", "안내서" 같은 일반 명사 대신, 상품명과 버전(무배당, 갱신형 등)을 반드시 포함하여 구체적으로 작성하십시오.
카테고리명 제외 로직 (중요): 앞서 선택한 [category] 값이 제목에 포함되어 있다면 이를 삭제하고 상품명 중심으로 작성하십시오.
예시 (Category가 '약관'인 경우):
수정 전: "판매약관_ThePride신한참좋은치아보험PlusⅢ(무배당, 갱신형) 약관"
수정 후: "ThePride신한참좋은치아보험PlusⅢ(무배당, 갱신형)"
제목 생성: 문서 내 명확한 제목이 없는 경우, 핵심 상품명과 문서 성격을 조합하여 30자 이내의 직관적인 제목을 직접 생성하십시오.

# 출력 형식 (Strict JSON)
- 마크다운 코드 블록 등 다른 설명은 일절 포함하지 말고, 오직 아래의 JSON 객체 포맷만 출력하십시오.

{
  "subject": "제작처가 포함된 300자 이내의 주제 요약 내용",
  "category": "산출방법서" or "약관" or "기타",
  "title" : "추출 또는 생성된 해당 문서의 제목"
}"""
        fallback_title = os.path.splitext(os.path.basename(file_path))[0]
        fallback = {
            "subject": external_subject or fallback_title,
            "category": "기타",
            "title": "",
        }

        full_text = ""
        subject_max_chars = _as_positive_int(os.getenv("SUBJECT_MAX_CHARS", "12000"), 12000)
        try:
            with fitz.open(file_path) as docs:
                for page in docs:
                    try:
                        text = page.get_text("text")
                        if text:
                            full_text += text + "\n"
                            if len(full_text) >= subject_max_chars:
                                full_text = full_text[:subject_max_chars]
                                break
                    except Exception as e:
                        _log.warning("[subject_extract] page text extraction failed: %s", e)
                        continue
        except Exception as e:
            _log.warning("[subject_extract] failed to open document: %s", e)
            return fallback

        if not full_text.strip():
            _log.warning("[subject_extract] no text extracted; using fallback metadata")
            return fallback

        try:
            output = self.llm.call(
                system_prompt=system_prompt,
                question=full_text,
                output_format=MetaOutput,
            )
        except Exception as e:
            _log.warning("[subject_extract] LLM extraction failed: %s", e)
            output = None

        if not isinstance(output, dict):
            return fallback

        subject = str(output.get("subject") or "").strip()
        category = str(output.get("category") or "").strip()
        title = str(output.get("title") or "").strip()

        if category not in {"산출방법서", "약관", "기타"}:
            category = "기타"
        if external_subject:
            subject = external_subject
        if not subject:
            subject = fallback["subject"]

        result = {
            "subject": subject,
            "category": category,
            "title": title,
        }
        _log.info(
            "[subject_extract] subject_chars=%s category=%s title=%s",
            len(subject),
            category,
            title,
        )
        print(
            f"[subject_extract] category={category} title={title!r} subject_chars={len(subject)}",
            flush=True,
        )
        return result

    async def compose_vectors(self, document: DoclingDocument, chunks: List[DocChunk], file_path: str, request: Request, converted_pdf_path: Optional[str] = None, **kwargs: dict) -> \
            list[dict]:
        title = ""
        created_date = 0
        try:
            if (document.key_value_items and
                len(document.key_value_items) > 0 and
                hasattr(document.key_value_items[0], 'graph') and
                hasattr(document.key_value_items[0].graph, 'cells') and
                len(document.key_value_items[0].graph.cells) > 1):

                # 작성일 추출 (cells[1])
                date_text = document.key_value_items[0].graph.cells[1].text
                created_date = self.parse_created_date(date_text)
        except (AttributeError, IndexError) as e:
            pass

        for item, _ in document.iterate_items():
            if hasattr(item, 'label'):
                if item.label == DocItemLabel.TITLE:
                    title = item.text.strip() if item.text else ""
                    break

        subject_metadata = getattr(self, "subject_metadata", {}) or {}
        extracted_subject = str(subject_metadata.get("subject") or "").strip()
        extracted_category = str(subject_metadata.get("category") or "기타").strip() or "기타"
        title = str(subject_metadata.get("title") or "").strip()
        extracted_title = title + "_" + extracted_category

        if extracted_title:
            title = extracted_title

        # kwargs에서 부록 정보 추출 !! appendix feature (2025-09-30, geonhee kim) !!
        appendix_info = kwargs.get('appendix', '')
        appendix_list = []
        if isinstance(appendix_info, str):
            appendix_list = [item.strip() for item in json.loads(appendix_info) if item.strip()] if appendix_info else []
        elif isinstance(appendix_info, list):
            appendix_list = appendix_info
        else:
            appendix_list = []

        global_metadata = dict(
            n_chunk_of_doc=len(chunks),
            n_page=document.num_pages(),
            reg_date=datetime.now().isoformat(timespec='seconds') + 'Z',
            created_date=created_date,
            title=title,
            subject=extracted_subject,
            category=extracted_category,
        )
        # 비-PDF 입력이 변환된 경우 GenOS 문서 뷰어가 변환 PDF를 열 수 있도록 메타에 경로를 남긴다.
        if converted_pdf_path:
            global_metadata['file_path'] = converted_pdf_path

        current_page = None
        chunk_index_on_page = 0
        vectors = []
        upload_tasks = []
        for chunk_idx, chunk in enumerate(chunks):
            chunk_page = chunk.meta.doc_items[0].prov[0].page_no if chunk.meta.doc_items[0].prov else 0
            # header 앞에 헤더 마커 추가 (HEADER: )
            headers_text = "HEADER: " + ", ".join(chunk.meta.headings) + '\n' if chunk.meta.headings else ''
            content = headers_text + chunk.text

            # appendix 추출 !! appendix feature (2025-09-30, geonhee kim) !!
            matched_appendices = self.check_appendix_keywords(content, appendix_list)
            # print(appendix_list, matched_appendices)
            chunk_global_metadata = global_metadata.copy()
            chunk_global_metadata['appendix'] = matched_appendices  # Only matched ones
            ###

            if chunk_page != current_page:
                current_page = chunk_page
                chunk_index_on_page = 0

            vector = (GenOSVectorMetaBuilder()
                      .set_text(content)
                      .set_page_info(chunk_page, chunk_index_on_page, self.page_chunk_counts[chunk_page])
                      .set_chunk_index(chunk_idx)
                      .set_global_metadata(**chunk_global_metadata) #!! appendix feature (2025-09-30, geonhee kim) !!
                      .set_chunk_bboxes(chunk.meta.doc_items, document)
                      .set_media_files(chunk.meta.doc_items)
                      ).build()
            vectors.append(vector)

            chunk_index_on_page += 1
            if upload_files:
                file_list = self.get_media_files(chunk.meta.doc_items)
                upload_tasks.append(asyncio.create_task(
                    upload_files(file_list, request=request)
                ))

        if upload_tasks:
            await asyncio.gather(*upload_tasks)

        doc_no = kwargs.get('doc_no', 0)
        relate_to = kwargs.get('relate_to', 0)

        for v in vectors:
            v.doc_no = f'Doc_{doc_no}' if doc_no != 0 else None
            v.relate_to = f"Doc_{relate_to}" if relate_to != 0 else None


        return vectors

    def get_media_files(self, doc_items: list):
        temp_list = []
        for item in doc_items:
            if isinstance(item, PictureItem) and item.image:
                path = str(item.image.uri)
                name = path.rsplit("/", 1)[-1]
                temp_list.append({'path': path, 'name': name})
        return temp_list

    def check_glyph_text(self, text: str, threshold: int = 1) -> bool:
        """텍스트에 GLYPH 항목이 있는지 확인하는 메서드"""
        if not text:
            return False

        # GLYPH 문자열 또는 PDF 폰트 매핑 실패로 생기는 Private Use Area 문자를 확인
        matches = re.findall(r'GLYPH\w*|[\ue000-\uf8ff]', text)
        if len(matches) >= threshold:
            # print(f"Text has glyphs. len(matches): {len(matches)}. ")
            return True

        return False

    def check_glyphs(self, document: DoclingDocument) -> bool:
        """문서에 글리프가 있는지 확인하는 메서드"""
        total_matches = 0
        for item, level in document.iterate_items():
            if isinstance(item, TextItem) and hasattr(item, 'prov') and item.prov:
                page_no = item.prov[0].page_no
                # page_texts += item.text

                # GLYPH 문자열 또는 PDF 폰트 매핑 실패로 생기는 Private Use Area 문자 확인
                matches = re.findall(r'GLYPH\w*|[\ue000-\uf8ff]', item.text or "")
                total_matches += len(matches)
                if len(matches) > 5 or total_matches > 20:
                    _log.info(
                        "[glyph_check] glyph-like text detected on page=%s item_ref=%s item_matches=%s total_matches=%s",
                        page_no,
                        getattr(item, "self_ref", None),
                        len(matches),
                        total_matches,
                    )
                    return True

        return False

    def check_appendix_keywords(self, content: str, appendix_list: list) -> str: # !! appendix feature (2025-09-30, geonhee kim) !!
        if not content or not appendix_list:
            return ""

        matched_appendices = []

        # 1. Find appendix patterns in content first
        found_patterns = []

        # Complex patterns: 별지/별표/장부 + numbers (with hyphens, Roman numerals)
        # Updated regex to capture full patterns like "별지 제 Ⅰ -1 호 서식" by matching until closing delimiters
        content = re.sub(r"\s+", "", content)
        complex_patterns = re.findall(r'(별지|별표|장부)(?:제)?([^<>()\[\]]+?)(?=(?:호|서식)|[<>\)\]]|$)', content)
        for pattern_type, number in complex_patterns:
            found_patterns.extend([
                f"{pattern_type} {number}",
                f"{pattern_type} 제{number}호",
                f"{pattern_type}{number}",
                f"{pattern_type}제{number}호"
            ])

        # Standalone patterns: (별표), (별지), (장부)
        standalone_patterns = re.findall(r'[\(\[]+(별지|별표|장부)[\)\]]+', content)
        for pattern_type in set(standalone_patterns):
            found_patterns.extend([
                pattern_type,
                f"{pattern_type}",
            ])

        # 2. Check if found patterns match any appendix in the list
        for appendix in appendix_list:
            if not appendix or not isinstance(appendix, str):
                continue

            appendix_clean = appendix.replace('.pdf', '').lower().strip()

            # If any found pattern exists in appendix filename, it's a match
            for pattern in found_patterns:
                if pattern.lower().strip() in appendix_clean:
                    matched_appendices.append(appendix)
                    break  # Prevent duplicates

        return ', '.join(matched_appendices) if matched_appendices else ""

    def ocr_all_table_cells(self, document: DoclingDocument, pdf_path) -> List[Dict[str, Any]]:
        """
        글리프 깨진 텍스트가 있는 테이블에 대해서만 OCR을 수행합니다.
        Args:
            document: DoclingDocument 객체
            pdf_path: PDF 파일 경로
        Returns:
            OCR이 완료된 문서의 DoclingDocument 객체
        """
        import fitz
        import base64
        import requests

        def post_ocr_bytes(img_bytes: bytes, timeout=60) -> dict:
            HEADERS = {"Accept": "application/json", "Content-Type": "application/json"}
            payload = {"file": base64.b64encode(img_bytes).decode("ascii"), "fileType": 1, "visualize": False}
            r = requests.post(self.ocr_endpoint, json=payload, headers=HEADERS, timeout=timeout)
            if not r.ok:
                # 진단에 도움되도록 본문 일부 출력
                raise RuntimeError(f"OCR HTTP {r.status_code}: {r.text[:500]}")
            return r.json()

        def extract_ocr_fields(resp: dict):
            """
            resp: 위와 같은 OCR 응답 JSON(dict)
            return: (rec_texts, rec_scores, rec_boxes) — 모두 list
            """
            if resp is None:
                return [], [], []

            # 최상위 상태 체크
            if resp.get("errorCode") not in (0, None):
                return [], [], []

            ocr_results = (
                resp.get("result", {})
                    .get("ocrResults", [])
            )
            if not ocr_results:
                return [], [], []

            pruned = (
                ocr_results[0]
                .get("prunedResult", {})
            )
            if not pruned:
                return [], [], []

            rec_texts  = pruned.get("rec_texts", [])   # list[str]
            rec_scores = pruned.get("rec_scores", [])  # list[float]
            rec_boxes  = pruned.get("rec_boxes", [])   # list[[x1,y1,x2,y2]]

            # 길이 불일치 방어: 최소 길이에 맞춰 자르기
            n = min(len(rec_texts), len(rec_scores), len(rec_boxes))
            return rec_texts[:n], rec_scores[:n], rec_boxes[:n]

        try:
            doc = fitz.open(pdf_path)

            for table_idx, table_item in enumerate(document.tables):
                if not table_item.data or not table_item.data.table_cells:
                    continue

                b_ocr = False
                for cell_idx, cell in enumerate(table_item.data.table_cells):
                    if self.check_glyph_text(cell.text, threshold=1):
                        b_ocr = True
                        break

                if b_ocr is False:
                    # 글리프 깨진 텍스트가 없는 경우, OCR을 수행하지 않음
                    continue

                for cell_idx, cell in enumerate(table_item.data.table_cells):

                    # Provenance 정보에서 위치 정보 추출
                    if not table_item.prov:
                        continue

                    page_no = table_item.prov[0].page_no - 1
                    bbox = cell.bbox

                    page = doc.load_page(page_no)

                    # 셀의 바운딩 박스를 사용하여 이미지에서 해당 영역을 잘라냄
                    cell_bbox = fitz.Rect(
                        bbox.l, min(bbox.t, bbox.b),
                        bbox.r, max(bbox.t, bbox.b)
                    )

                    # bbox 높이 계산 (PDF 좌표계 단위)
                    bbox_height = cell_bbox.height

                    # 목표 픽셀 높이
                    target_height = 20

                    # zoom factor 계산
                    # (너무 작은 bbox일 경우 0으로 나누는 걸 방지)
                    zoom_factor = target_height / bbox_height if bbox_height > 0 else 1.0
                    zoom_factor = min(zoom_factor, 4.0)  # 최대 확대 비율 제한
                    zoom_factor = max(zoom_factor, 1)  # 최소 확대 비율 제한

                    # 페이지를 이미지로 렌더링
                    mat = fitz.Matrix(zoom_factor, zoom_factor)
                    pix = page.get_pixmap(matrix=mat, clip=cell_bbox)
                    img_data = pix.tobytes("png")

                    result = post_ocr_bytes(img_data, timeout=60)
                    rec_texts, rec_scores, rec_boxes = extract_ocr_fields(result)

                    cell.text = ""
                    for t in rec_texts:
                        if len(cell.text) > 0:
                            cell.text += " "
                        cell.text += t if t else ""
        except Exception as e:
            _log.warning("OCR processing failed: %s", e)

        return document

    def setup_logging(self, level_num: int):
        """
            5"DEBUG", 4"INFO", 3"WARNING", 2"ERROR", 1"CRITICAL", 0"NOLOG" 중 하나를 받아서 로깅 레벨을 설정하는 메서드
        """
        level_num = _as_nonnegative_int(level_num, 4)

        def get_level_name(level_num: int) -> str:
            level_map = {
                5: "DEBUG",
                4: "INFO",
                3: "WARNING",
                2: "ERROR",
                1: "CRITICAL",
                0: "NOLOG"
            }
            return level_map.get(level_num, "INFO")
        level_name = get_level_name(level_num)
        print(f"Setting log level to: {level_name}")

        if level_name == "NOLOG" or not hasattr(logging, level_name):
            logging.disable(logging.CRITICAL)  # 모든 로그 비활성화
            return

        level = getattr(logging, level_name.upper())

        # root logger 설정 (핸들러는 main에서만 설정)
        logging.basicConfig(
            level=level,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            handlers=[logging.StreamHandler()]   # 콘솔 출력
        )

        # root logger level 적용
        logging.getLogger().setLevel(level)

    async def __call__(self, request: Request, file_path: str, **kwargs: dict):
        self.setup_logging(kwargs.get('log_level', 4))
        self.page_chunk_counts = defaultdict(int)

        _log.info(f"file_path: {file_path}")
        _log.info(f"kwargs: {kwargs}")

        source_file_path = file_path
        source_ext = os.path.splitext(source_file_path)[-1].lower()

        export_to_html = _as_int_flag(kwargs.get("export_to_html", 1), 1)
        kwargs["export_to_html"] = export_to_html
        toc_user_prompt_override = kwargs.get('toc_user_prompt', "")
        toc_system_prompt_override = kwargs.get('toc_system_prompt', "")
        self.enrichment_options.toc_user_prompt = toc_user_prompt_override or toc_user_prompt
        self.enrichment_options.toc_system_prompt = toc_system_prompt_override or toc_system_prompt

        toc_on = _as_int_flag(_first_kwarg(kwargs, 'outline', 'toc_on', default=1), 1)
        image_description_on = _as_int_flag(
            _first_kwarg(kwargs, 'img_desc', 'image_description_on', default=0),
            0,
        )
        enhanced_image_description_on = _as_int_flag(
            _first_kwarg(kwargs, 'img_detail', 'enhanced_image_description_on', default=0),
            0,
        )
        table_refine_on = _as_int_flag(
            _first_kwarg(kwargs, 'table_fix', 'table_refine_on', default=0),
            0,
        )
        force_ocr = _as_int_flag(_first_kwarg(kwargs, 'ocr', 'force_ocr', 'ocr_on', default=1), 1)
        # ocr is the public switch:
        # - ocr=1: keep the current vT5_3 behavior and run OCR first.
        # - ocr=0: run the standard-style first pass, then allow glyph/text-quality OCR fallback.
        glyph_ocr_fallback_on = 1
        ocr_fallback_on = 1 if force_ocr == 0 else 0
        metadata_on = _as_int_flag(kwargs.get('metadata_on', kwargs.get('extract_metadata', 1)), 1)
        max_chunk_size = _as_nonnegative_int(
            _first_kwarg(kwargs, 'chunk_size', 'max_chunk_size', 'chunk_max_tokens', default=4000),
            4000,
        )
        max_pages_per_chunk = _as_nonnegative_int(
            kwargs.get(
                'max_pages_per_chunk',
                kwargs.get('max_chunk_pages', kwargs.get('max_chunk_page_span', 0)),
            ),
            0,
        )

        kwargs['toc_on'] = toc_on
        kwargs['outline'] = toc_on
        kwargs['image_description_on'] = image_description_on
        kwargs['img_desc'] = image_description_on
        kwargs['enhanced_image_description_on'] = enhanced_image_description_on
        kwargs['img_detail'] = enhanced_image_description_on
        kwargs['table_refine_on'] = table_refine_on
        kwargs['table_fix'] = table_refine_on
        kwargs['glyph_ocr_fallback_on'] = glyph_ocr_fallback_on
        kwargs['ocr_fallback_on'] = ocr_fallback_on
        kwargs['force_ocr'] = force_ocr
        kwargs['ocr'] = force_ocr
        kwargs['metadata_on'] = metadata_on
        kwargs['max_chunk_size'] = max_chunk_size
        kwargs['chunk_size'] = max_chunk_size
        kwargs['max_pages_per_chunk'] = max_pages_per_chunk
        preserve_section_boundaries = _as_int_flag(
            kwargs.get('preserve_section_boundaries', 1),
            1,
        )
        kwargs['preserve_section_boundaries'] = preserve_section_boundaries
        infer_local_section_boundaries = _as_int_flag(
            kwargs.get(
                'infer_local_section_boundaries',
                kwargs.get('soft_section_boundaries_on', 1),
            ),
            1,
        )
        kwargs['infer_local_section_boundaries'] = infer_local_section_boundaries

        self.toc_on = toc_on
        self.image_description_on = image_description_on
        self.enhanced_image_description_on = enhanced_image_description_on
        self.table_refine_on = table_refine_on
        self.enrichment_options.do_toc_enrichment = toc_on == 1
        self.enrichment_options.extract_metadata = metadata_on == 1
        _log.info(
            "Image Option: %s ToC Option: %s Metadata Option: %s Chunk Max Tokens: %s Chunk Max Pages: %s Section Boundary Option: %s Local Boundary Option: %s Table Refine Option: %s Glyph OCR Fallback Option: %s OCR Fallback Option: %s Force OCR: %s export_to_html: %s",
            image_description_on | enhanced_image_description_on,
            toc_on,
            metadata_on,
            max_chunk_size,
            max_pages_per_chunk,
            preserve_section_boundaries,
            infer_local_section_boundaries,
            table_refine_on,
            glyph_ocr_fallback_on,
            ocr_fallback_on,
            force_ocr,
            export_to_html,
        )

        # 입력이 PDF가 아닐 때 동작:
        # - auto_convert_to_pdf=True (default): PDF SDK/LibreOffice 로 자동 변환 후 진입
        # - auto_convert_to_pdf=False: 변환 없이 그대로 진행 (변경 전 동작; PDF 가정)
        converted_pdf_path: Optional[str] = None
        auto_convert_to_pdf = _as_bool_flag(kwargs.get('auto_convert_to_pdf', True), True)
        kwargs['auto_convert_to_pdf'] = 1 if auto_convert_to_pdf else 0
        if auto_convert_to_pdf and not _is_pdf(file_path):
            _log.info(f"[intelligent] Non-PDF input — auto-converting to PDF: {file_path}")
            use_sdk = _as_bool_flag(kwargs.get('use_pdf_sdk', True), True)
            kwargs['use_pdf_sdk'] = 1 if use_sdk else 0
            converted = convert_to_pdf(file_path, use_pdf_sdk=use_sdk)
            if (not converted or not os.path.exists(converted)) and use_sdk:
                _log.warning(f"[intelligent] SDK conversion failed → fallback to LibreOffice")
                converted = convert_to_pdf(file_path, use_pdf_sdk=False)
            if not converted or not os.path.exists(converted):
                raise GenosServiceException(1, f"PDF 변환 실패: {file_path}")
            file_path = converted
            converted_pdf_path = converted
            _log.info(f"[intelligent] Converted PDF: {file_path}")

        self.subject_metadata = self.extract_subject(
            file_path=file_path,
            external_subject=str(kwargs.get("external_subject") or "").strip(),
        )
        self.subject = self.subject_metadata.get("subject") or os.path.splitext(os.path.basename(file_path))[0]

        image_option = image_description_on | enhanced_image_description_on
        self._configure_picture_description(
            file_path=file_path,
            **kwargs,
        )
        self._create_converters()

        if force_ocr == 1:
            _log.info("[ocr_fallback] force_ocr=1 이므로 일반 Docling 변환을 건너뛰고 OCR 컨버터로 변환합니다.")
            document: DoclingDocument = self.load_documents_with_docling_ocr(file_path, **kwargs)
        else:
            document: DoclingDocument = self.load_documents(file_path, **kwargs)
            if image_option == 1:
                self._log_picture_description_state(document, "after_initial_convert")

            if glyph_ocr_fallback_on == 1 and self.check_glyphs(document):
                _log.info("[ocr_fallback] glyph가 감지되어 OCR 컨버터로 재변환합니다.")
                document: DoclingDocument = self.load_documents_with_docling_ocr(file_path, **kwargs)
            elif ocr_fallback_on == 1:
                needs_ocr = False
                try:
                    needs_ocr = not check_document(document, self.enrichment_options)
                except Exception as e:
                    _log.warning("[ocr_fallback] 문서 품질 검사 실패로 OCR fallback을 건너뜁니다: %s", e)

                if needs_ocr:
                    # OCR이 필요하다고 판단되면 OCR 수행
                    _log.info("[ocr_fallback] 문서 품질 검사 결과 OCR 컨버터로 재변환합니다.")
                    document: DoclingDocument = self.load_documents_with_docling_ocr(file_path, **kwargs)
            else:
                _log.info("[ocr_fallback] 비활성화 상태이므로 최초 Docling 변환 결과를 유지합니다.")
        if image_option == 1:
            self._log_picture_description_state(document, "after_ocr_fallback")

        # 글리프 깨진 텍스트가 있는 테이블에 대해서만 OCR 수행 (청크토큰 8k이상 발생 방지)
        document: DoclingDocument = self.ocr_all_table_cells(document, file_path)
        if image_option == 1:
            self._log_picture_description_state(document, "after_table_cell_ocr")

        output_path, output_file = os.path.split(file_path)
        filename, _ = os.path.splitext(output_file)
        artifacts_dir = Path(f"{output_path}/{filename}")
        if artifacts_dir.is_absolute():
            reference_path = None
        else:
            reference_path = artifacts_dir.parent

        document = document._with_pictures_refs(image_dir=artifacts_dir, page_no=None, reference_path=reference_path)
        if image_option == 1:
            self._log_picture_description_state(document, "after_picture_refs")

        if toc_on == 1 or metadata_on == 1:
            document = self.enrichment(document, **kwargs)
            if image_option == 1:
                self._log_picture_description_state(document, "after_enrichment")
        else:
            _log.info("[enrichment] disabled toc_on=0 metadata_on=0; skipping enrich_document")

        has_text_items = False
        for item, _ in document.iterate_items():
            if (isinstance(item, (TextItem, ListItem, CodeItem, SectionHeaderItem)) and item.text and item.text.strip()) or (isinstance(item, TableItem) and item.data and len(item.data.table_cells) == 0):
                has_text_items = True
                break

        if has_text_items:
            # Extract Chunk from DoclingDocument
            chunks: List[DocChunk] = self.split_documents(document, **kwargs)
        else:
            # text가 있는 item이 없을 때 document에 임의의 text item 추가
            # 첫 번째 페이지의 기본 정보 사용 (1-based indexing)
            page_no = 1

            # ProvenanceItem 생성
            prov = ProvenanceItem(
                page_no=page_no,
                bbox=BoundingBox(l=0, t=0, r=1, b=1),  # 최소 bbox
                charspan=(0, 1)
            )

            # document에 temp text item 추가
            document.add_text(
                label=DocItemLabel.TEXT,
                text=".",
                prov=prov
            )

            # split_documents 호출
            chunks: List[DocChunk] = self.split_documents(document, **kwargs)
        # await assert_cancelled(request)

        vectors = []
        if len(chunks) >= 1:
            vectors: list[dict] = await self.compose_vectors(
                document, chunks, file_path, request,
                converted_pdf_path=converted_pdf_path,
                **kwargs,
            )
        else:
            raise GenosServiceException(1, f"chunk length is 0")

        """
        # 미디어 파일 업로드 방법
        media_files = [
            { 'path': '/tmp/graph.jpg', 'name': 'graph.jpg', 'type': 'image' },
            { 'path': '/result/1/graph.jpg', 'name': '1/graph.jpg', 'type': 'image' },
        ]

        # 업로드 요청 시에는 path, name 필요
        file_list = [{k: v for k, v in file.items() if k != 'type'} for file in media_files]
        await upload_files(file_list, request=request)

        # 메타에 저장시에는 name, type 필요
        meta = [{k: v for k, v in file.items() if k != 'path'} for file in media_files]
        vectors[0].media_files = meta
        """

        if table_refine_on == 1:
            print(f"[table_refine] enabled file_path={file_path} vectors={len(vectors)}", flush=True)
            _log.info("[table_refine] enabled file_path=%s vectors=%s", file_path, len(vectors))
            self.refiner = TableRefiner()
            self.refiner.load_documents(file_path=file_path)
            vectors = await self.refiner.refine_vectors(vectors=vectors)
        else:
            print(f"[table_refine] disabled table_refine_on={table_refine_on}", flush=True)
            _log.info("[table_refine] disabled table_refine_on=%s", table_refine_on)

        # 변환된 PDF 를 minio 에 업로드. object key 는 원본 파일명의 stem + ".pdf".
        # (예: 원본 file_name='sample.hwp' -> minio key='<doc_id>/sample.pdf')
        # upload_files 가 업로드 후 로컬 파일을 os.remove 할 수 있으므로, NFS 상의 변환 PDF
        # (백엔드 서빙용)가 삭제되지 않도록 temp 복사본을 만들어 업로드한다.
        if converted_pdf_path and upload_files:
            original_name = kwargs.get('file_name') or os.path.basename(converted_pdf_path)
            pdf_object_name = os.path.splitext(original_name)[0] + '.pdf'
            fd, temp_upload_path = tempfile.mkstemp(suffix='.pdf')
            os.close(fd)
            try:
                shutil.copy2(converted_pdf_path, temp_upload_path)
                await upload_files(
                    [{'path': temp_upload_path, 'name': pdf_object_name}],
                    request=request,
                )
            finally:
                if os.path.exists(temp_upload_path):
                    try:
                        os.remove(temp_upload_path)
                    except OSError:
                        pass

        if vectors:
            try:
                v0 = vectors[0].model_dump()
                log_meta = {k: v for k, v in v0.items() if k not in ('text', 'chunk_bboxes', 'media_files')}
                _log.info(f"[vector[0] meta] {log_meta}")
            except Exception as e:
                _log.warning(f"[vector[0] meta] dump failed: {e}")

        # 수식 후처리
        postprocess_hwp_formulas = kwargs.get('postprocess_hwp_formulas', True)
        if postprocess_hwp_formulas and source_ext in ('.hwp', '.hwpx'):
            _log.info(f"Processing Korean Document ({source_ext}) with Unified HwpProcessor")
            try:
                hwp_processor = HwpProcessor()
                hwp_doc = hwp_processor.load_documents(source_file_path, **kwargs)
                vectors = _enrich_vectors_with_hwp_formulas(vectors, hwp_doc)
            except Exception as hwp_err:
                _log.warning(f"[HwpProcessor] HWP 직접 파싱 실패 → 수식 후처리 건너뜀: {hwp_err}")
                return vectors

        return vectors


class GenosServiceException(Exception):
    # GenOS 와의 의존성 부분 제거를 위해 추가
    def __init__(self, error_code: str, error_msg: Optional[str] = None, msg_params: Optional[dict] = None) -> None:
        self.code = 1
        self.error_code = error_code
        self.error_msg = error_msg or "GenOS Service Exception"
        self.msg_params = msg_params or {}

    def __repr__(self) -> str:
        class_name = self.__class__.__name__
        return f"{class_name}(code={self.code!r}, errMsg={self.error_msg!r})"


# GenOS 와의 의존성 제거를 위해 추가
async def assert_cancelled(request: Request):
    if await request.is_disconnected():
        raise GenosServiceException(1, f"Cancelled")


#-----------------------------------------------------------------
# enrichment 프롬프트
#-----------------------------------------------------------------

image_description_prompt = """
# [지침] 이미지 핵심 분석 전문가
당신은 이미지의 핵심 정보를 추출하여 RAG 검색에 최적화된 설명을 작성하는 전문가입니다.
당신의 목표는 주어진 문서의 주제를 참고하여 이미지를 보지 못한 사람(혹은 검색 알고리즘)이 이미지의 모든 정보를 완벽하게 이해하고 검색할 수 있도록 구조화된 텍스트를 생성하는 것입니다.

제공된 이미지를 분석하여 아래의 [지침]에 따라 핵심 설명만을 작성해 주세요.

문서의 주제 : {subject}

# [지침]
### [작성 규칙]
1. **한 문장 요약**: 이미지의 종류(도표, 사진, UI 등)와 핵심 주제를 한 줄로 서술.
2. **핵심 요소 (Bullet Point)**: 이미지 내의 주요 객체, 텍스트, 상태 정보를 3~5개 이내로 요약.
3. **원문 보존**: 이미지 안에 보이는 제목, 라벨, 수치, 단위, 범례, 주석은 가능한 한 원문 그대로 적습니다.
4. **불확실성 표시**: 흐리거나 잘린 글자는 추정하지 말고 "판독 불가" 또는 "일부 판독 불가"로 표시합니다.
5. **불필요한 수식어 금지**: "이미지는 ~를 보여줍니다" 같은 문구 없이 팩트 위주로 기술.

### [출력 형식]
# 이미지 설명
- (이미지의 핵심을 담은 한 문장으로 요약)
- (핵심 요소 1)
- (핵심 요소 2)
"""
enhanced_image_description_prompt = """
당신은 RAG(검색 증강 생성) 시스템을 위한 **전문 데이터 분석가이자 이미지 설명 전문가**입니다.
당신의 목표는 주어진 문서의 주제를 참고하여 이미지를 보지 못한 사람(혹은 검색 알고리즘)이 이미지의 모든 정보를 완벽하게 이해하고 검색할 수 있도록 구조화된 텍스트를 생성하는 것입니다.

제공된 이미지를 분석하여 아래의 [지침]에 따라 핵심 설명만을 작성해 주세요.

문서의 주제 : {subject}

# [지침]
### [작성 규칙]

1. **한 문장 요약**: 이미지의 종류(도표, 사진, UI 등)와 핵심 주제를 한 줄로 서술.
2. **핵심 요소 (Bullet Point)**: 이미지 내의 주요 객체, 텍스트, 상태 정보를 3~5개 이내로 요약.
3. **원문 보존**: 이미지 안에 보이는 제목, 축/열 이름, 라벨, 수치, 단위, 범례, 주석은 가능한 한 원문 그대로 적습니다.
4. **불확실성 표시**: 흐리거나 잘린 글자는 추정하지 말고 "판독 불가" 또는 "일부 판독 불가"로 표시합니다.
5. **불필요한 수식어 금지**: "이미지는 ~를 보여줍니다" 같은 문구 없이 팩트 위주로 기술.


6. **차트/테이블 데이터 변환 (Chart to Markdown)**:
   - 이미지에 차트(막대, 선, 파이 등)나 표가 포함되어 있다면, 이를 **반드시 마크다운 테이블(Markdown Table)** 형식으로 변환하세요.
   - 수치가 명시되어 있지 않은 경우에만 축 눈금을 보고 근사치를 추정하고, 비고에 "추정"이라고 표시하세요.
   - 단위, 기준시점, 주석이 보이면 데이터와 함께 보존하세요.
   - 데이터 테이블 작성 후, 확인 가능한 범위에서 추세, 최고/최저점, 주요 차이만 간략히 덧붙이세요.

### [출력 형식]

# 이미지 설명
(이미지 전체 요약 내용)


# 데이터 구조화 (차트/표)
(차트가 있을 경우만 작성, 없을 경우 '해당 없음' 표기)

| 항목 (X축) | 값 (Y축) | 비고 |
| :--- | :--- | :--- |
| 데이터 1 | 100 | ... |
| 데이터 2 | 200 | ... |

"""
toc_system_prompt = "당신은 PDF 문서에서 실제 목차로 사용할 구조적 제목만 선별하는 전문가입니다."
toc_user_prompt = """
다음 문서에서 검색용 HEADER 경로로 사용할 구조적 제목만 계층적으로 추출하세요.

<document>
{raw_text}
</document>

핵심 규칙:
1. 실제 문서의 장, 부, 절, 관, 조, 항목명, 별첨, 붙임처럼 섹션을 여는 제목만 추출한다.
2. 각 문단, 안내문, 설명문, 목록 문장, 표 안의 개별 셀, 페이지 헤더/푸터, 페이지 번호는 목차로 만들지 않는다.
3. 표지/머리말의 메타데이터성 항목은 제외한다. 예: 작성기준일, 효력발생일, 발행기관, 담당부서, 문서번호, 버전, 열람장소, URL.
4. "1.", "2.", "가.", "(1)" 같은 번호형 텍스트는 짧은 명사구 형태의 제목일 때만 포함한다.
5. 번호형 텍스트가 설명문이거나, ':' 뒤에 값이 붙어 있거나, 문장 종결형("습니다", "합니다", "됩니다", "없습니다", "바랍니다" 등)으로 끝나면 제외한다.
6. "제1장 총칙", "Ⅰ. 판매기준 및 판매방법", "MDC 01 Diseases and Disorders of the Nervous System", "제1부 모집 또는 매출에 관한 사항", "가. 회사의 개요"처럼 구조를 여는 제목은 포함한다.
7. "3. 본 문서에 기재된 기준이나 목표가 반드시 실현된다는 보장은 없습니다."처럼 본문 설명 문장은 제외한다.
8. 큰 표, 그림, 목록, 카탈로그 블록 바로 앞에서 그 블록 전체를 여는 독립 캡션/소제목은 구조적 경계로 포함할 수 있다.
   - 단, 표의 행/셀 값이나 반복 페이지명/페이지 번호만으로 된 텍스트는 제외한다.
   - 짧은 명사구이고 뒤따르는 표/그림/목록의 주제를 대표할 때만 포함한다.
9. 문서에 실제로 보이는 제목의 원문 표현을 유지하되, 중복 제목은 제거한다.
10. 문서 순서를 유지한다.
11. 문서 전체 길이에 비해 과도하게 많은 항목을 만들지 않는다. 제목이 아닌 세부 문장까지 모두 뽑아 100개 이상으로 부풀리지 않는다.

출력 형식:
<toc>
TITLE:<문서 제목>
1. <상위 제목>
1.1. <하위 제목>
1.1.1. <하위 제목>
...
</toc>
""".strip()
