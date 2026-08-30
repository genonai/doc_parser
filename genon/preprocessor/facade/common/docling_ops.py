"""docling 파이프라인 배관: OCR 옵션 조립, 컨버터 생성, 표 이미지 저장, 글리프 판정,
표 셀 재OCR.

facade 4종(intelligent/convert/chunking/parser)이 복제해 두었던 로직의 단일 사본이다.
차이는 전부 주석·docstring·로그 접두·로컬 별칭 같은 표기였고 동작은 같았다.

이 모듈은 docling 타입을 직접 다룬다 — 배관 자체가 docling 객체를 만들고 고치는 일이라
duck typing 으로 우회할 수 없다. facade/enrichment 의 기존 모듈들과 같은 취급이다.
프로세서 인스턴스는 받지 않고 필요한 값만 인자로 받는다.
"""

from __future__ import annotations

import base64
import io
import logging
import os
import re
from pathlib import Path
from typing import Optional

import requests
from PIL import Image

from docling.backend.docling_parse_v4_backend import DoclingParseV4DocumentBackend
from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PaddleOcrOptions, UpstageOcrOptions
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling_core.types import DoclingDocument
from docling_core.types.doc import ImageRef, PictureItem, TableItem, TextItem
from docling_core.types.doc.utils import relative_path

from genon.preprocessor.facade.common.config_parse import as_dict

_log = logging.getLogger(__name__)


# ── OCR 옵션 ────────────────────────────────────────────────────────────────

def build_ocr_options(ocr_cfg: dict, paddle_endpoint: str):
    """yaml 의 ocr.engine 키에 따라 OcrOptions 를 만든다.

    PaddleOcrOptions 또는 UpstageOcrOptions 를 돌려준다. 기본 엔진은 "paddle".
    "upstage" 는 api_key 가 비면 UPSTAGE_API_KEY 환경변수로 폴백한다.
    모르는 engine 값은 경고 후 "paddle" 로 폴백한다.
    """
    ocr_cfg = ocr_cfg if isinstance(ocr_cfg, dict) else {}
    ocr_engine = str(ocr_cfg.get("engine", "paddle")).lower().strip()
    if ocr_engine not in {"paddle", "upstage"}:
        _log.warning(f"[DocumentProcessor] Unknown ocr.engine '{ocr_engine}', fallback to 'paddle'")
        ocr_engine = "paddle"

    if ocr_engine == "upstage":
        upstage_cfg = as_dict(ocr_cfg.get("upstage"))
        upstage_api_key = upstage_cfg.get("api_key", "") or os.getenv("UPSTAGE_API_KEY", "")

        # yaml 의 잘못된 값 (예: timeout: "60s") 으로 startup 이 깨지지 않도록
        # 변환 실패 시 default 로 fallback + warning.
        raw_timeout = upstage_cfg.get("timeout", 60)
        try:
            upstage_timeout = int(raw_timeout)
            if upstage_timeout <= 0:
                raise ValueError
        except (TypeError, ValueError):
            _log.warning(f"[DocumentProcessor] Invalid ocr.upstage.timeout '{raw_timeout}', fallback to 60")
            upstage_timeout = 60

        raw_text_score = upstage_cfg.get("text_score", 0.5)
        try:
            upstage_text_score = float(raw_text_score)
        except (TypeError, ValueError):
            _log.warning(f"[DocumentProcessor] Invalid ocr.upstage.text_score '{raw_text_score}', fallback to 0.5")
            upstage_text_score = 0.5

        return UpstageOcrOptions(
            force_full_page_ocr=False,
            lang=upstage_cfg.get("lang", ["ko", "en"]),
            api_endpoint=upstage_cfg.get(
                "api_endpoint",
                "https://api.upstage.ai/v1/document-digitization",
            ),
            api_key=upstage_api_key,
            model=upstage_cfg.get("model", "ocr"),
            timeout=upstage_timeout,
            text_score=upstage_text_score,
        )

    paddle_cfg = as_dict(ocr_cfg.get("paddle"))

    raw_lang = paddle_cfg.get("lang", ["korean"])
    if isinstance(raw_lang, list) and raw_lang:
        paddle_lang = raw_lang
    else:
        if raw_lang not in (None, [], ["korean"]):
            _log.warning(f"[DocumentProcessor] Invalid ocr.paddle.lang '{raw_lang}', fallback to ['korean']")
        paddle_lang = ["korean"]

    raw_text_score = paddle_cfg.get("text_score", 0.3)
    try:
        paddle_text_score = float(raw_text_score)
    except (TypeError, ValueError):
        _log.warning(f"[DocumentProcessor] Invalid ocr.paddle.text_score '{raw_text_score}', fallback to 0.3")
        paddle_text_score = 0.3

    return PaddleOcrOptions(
        force_full_page_ocr=False,
        lang=paddle_lang,
        ocr_endpoint=paddle_endpoint,
        text_score=paddle_text_score,
    )


# ── 컨버터 ──────────────────────────────────────────────────────────────────

def create_converters(pipeline_options, ocr_pipeline_options):
    """PDF 컨버터 4개(기본/폴백, OCR/OCR 폴백)를 만들어 튜플로 돌려준다.

    반환 순서: (converter, second_converter, ocr_converter, ocr_second_converter).
    기본 경로가 실패하면 second 로 재시도하는 구조라 백엔드가 서로 다르다.
    """
    converter = DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(
                pipeline_options=pipeline_options,
                backend=PyPdfiumDocumentBackend,
            ),
        }
    )
    second_converter = DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(
                pipeline_options=pipeline_options,
                backend=PyPdfiumDocumentBackend,
            ),
        },
    )
    ocr_converter = DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(
                pipeline_options=ocr_pipeline_options,
                backend=DoclingParseV4DocumentBackend,
            ),
        }
    )
    ocr_second_converter = DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(
                pipeline_options=ocr_pipeline_options,
                backend=PyPdfiumDocumentBackend,
            ),
        },
    )
    return converter, second_converter, ocr_converter, ocr_second_converter


# ── 표 이미지 / 미디어 파일 ──────────────────────────────────────────────────

def save_table_images(
    document: DoclingDocument,
    image_dir: Path,
    reference_path: Optional[Path] = None,
) -> None:
    """표 영역을 PNG 로 저장하고 TableItem.image.uri 를 설정한다(in-place).

    docling 의 DoclingDocument._with_pictures_refs 가 PictureItem 만 디스크에
    저장하므로, 동일 로직을 TableItem 에 대해 미러링한다. TableItem.get_image 는
    item.image 가 없으면 페이지 이미지에서 prov bbox 로 잘라 반환한다
    (generate_page_images 가 True 여야 함 — 프로세서 __init__ 에서 보장).
    """
    image_dir.mkdir(parents=True, exist_ok=True)
    if not image_dir.is_dir():
        return

    img_count = 0
    for item, _ in document.iterate_items(with_groups=False):
        if not isinstance(item, TableItem):
            continue
        img = item.get_image(doc=document)
        if img is None:
            continue
        hexhash = PictureItem._image_to_hexhash(img)
        if hexhash is None:
            continue
        loc_path = image_dir / f"table_{img_count:06}_{hexhash}.png"
        img.save(loc_path)
        if reference_path is not None:
            obj_path = relative_path(reference_path.resolve(), loc_path.resolve())
        else:
            obj_path = loc_path
        # 파이프라인이 표 이미지를 미리 크롭하지 않으므로(generate_table_images 미사용)
        # item.image 는 보통 None 이다. ImageRef 를 생성하되 uri 는 반드시 저장한
        # PNG 파일 경로로 설정한다(from_pil 의 base64 data URI 가 남지 않도록).
        if item.image is None:
            scale = img.size[0] / item.prov[0].bbox.width
            item.image = ImageRef.from_pil(image=img, dpi=round(72 * scale))
        item.image.uri = Path(obj_path)
        img_count += 1


def get_media_files(doc_items: list, include_tables: bool = False) -> list:
    """청크에 붙일 미디어 파일 목록(path/name)을 만든다."""
    temp_list = []
    for item in doc_items:
        if isinstance(item, PictureItem) and item.image:
            path = str(item.image.uri)
            temp_list.append({"path": path, "name": path.rsplit("/", 1)[-1]})
        elif include_tables and isinstance(item, TableItem) and item.image:
            path = str(item.image.uri)
            temp_list.append({"path": path, "name": path.rsplit("/", 1)[-1]})
    return temp_list


# ── 글리프 판정 ─────────────────────────────────────────────────────────────

def check_glyph_text(text: str, threshold: int = 1) -> bool:
    """텍스트에 GLYPH 항목이 threshold 개 이상 있는지."""
    if not text:
        return False
    return len(re.findall(r"GLYPH\w*", text)) >= threshold


def check_glyphs(document: DoclingDocument, threshold: int) -> bool:
    """문서 안의 어느 TextItem 이든 GLYPH 가 threshold 를 초과하면 True."""
    for item, _level in document.iterate_items():
        if isinstance(item, TextItem) and hasattr(item, "prov") and item.prov:
            if len(re.findall(r"GLYPH\w*", item.text)) > threshold:
                return True
    return False


def check_empty_text(document: DoclingDocument) -> bool:
    """텍스트 클러스터(박스)는 있는데 그 텍스트가 전부 비어 있는 페이지가 있는지 확인.

    length 폴백(layout_only)이나 텍스트레이어 부재 등으로 박스만 있고 텍스트가
    안 채워진 페이지를 잡아 강제 OCR 로 보낸다(이슈 #278 B-2).
    """
    from collections import defaultdict

    page_item_count: dict = defaultdict(int)
    page_text_len: dict = defaultdict(int)
    for item, _level in document.iterate_items():
        if isinstance(item, TextItem) and hasattr(item, "prov") and item.prov:
            page_no = item.prov[0].page_no
            page_item_count[page_no] += 1
            page_text_len[page_no] += len((item.text or "").strip())
    for page_no, n_items in page_item_count.items():
        # 텍스트 아이템이 있는데 그 페이지 텍스트 총량이 0 → 비어있는 페이지
        if n_items > 0 and page_text_len[page_no] == 0:
            _log.info(f"[intelligent] page {page_no} 텍스트가 비어있음 → 강제 OCR 필요")
            return True
    return False


# ── 표 셀 재OCR ─────────────────────────────────────────────────────────────

def _post_ocr_bytes(img_bytes: bytes, ocr_endpoint: str, timeout: int = 60) -> dict:
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    payload = {"file": base64.b64encode(img_bytes).decode("ascii"), "fileType": 1, "visualize": False}
    r = requests.post(ocr_endpoint, json=payload, headers=headers, timeout=timeout)
    if not r.ok:
        # 진단에 도움되도록 본문 일부 출력
        raise RuntimeError(f"OCR HTTP {r.status_code}: {r.text[:500]}")
    return r.json()


def _extract_ocr_fields(resp: dict):
    """OCR 응답 JSON 에서 (rec_texts, rec_scores, rec_boxes) 를 뽑는다. 모두 list."""
    if resp is None:
        return [], [], []

    # 최상위 상태 체크
    if resp.get("errorCode") not in (0, None):
        return [], [], []

    ocr_results = resp.get("result", {}).get("ocrResults", [])
    if not ocr_results:
        return [], [], []

    pruned = ocr_results[0].get("prunedResult", {})
    if not pruned:
        return [], [], []

    rec_texts = pruned.get("rec_texts", [])    # list[str]
    rec_scores = pruned.get("rec_scores", [])  # list[float]
    rec_boxes = pruned.get("rec_boxes", [])    # list[[x1,y1,x2,y2]]

    # 길이 불일치 방어: 최소 길이에 맞춰 자르기
    n = min(len(rec_texts), len(rec_scores), len(rec_boxes))
    return rec_texts[:n], rec_scores[:n], rec_boxes[:n]


def ocr_all_table_cells(
    document: DoclingDocument,
    *,
    ocr_endpoint: str,
    cell_threshold: int = 1,
    timeout: int = 60,
) -> DoclingDocument:
    """글리프 깨진 텍스트가 있는 표에 대해서만 셀 단위 재OCR 을 수행한다(in-place).

    cell_threshold 를 넘는 GLYPH 가 셀에 있으면 그 표 전체를 재OCR 대상으로 본다.
    """
    try:
        for table_idx, table_item in enumerate(document.tables):
            if not table_item.data or not table_item.data.table_cells:
                continue
            if not table_item.prov:
                continue

            b_ocr = False
            for cell in table_item.data.table_cells:
                if check_glyph_text(cell.text, threshold=cell_threshold):
                    b_ocr = True
                    break

            if b_ocr is False:
                # 글리프 깨진 텍스트가 없는 경우, OCR을 수행하지 않음
                continue

            # docling 이 이미 렌더해 둔 페이지 이미지(generate_page_images=True)를
            # 재사용해 셀 영역을 crop 한다. PyMuPDF 재렌더(get_pixmap)는 일부 PDF 에서
            # 네이티브 크래시(SIGSEGV, worker code 139)를 유발하므로 사용하지 않는다.
            page_no = table_item.prov[0].page_no
            page = document.pages.get(page_no)
            if page is None or page.size is None or page.image is None:
                continue
            page_image = page.image.pil_image
            if page_image is None:
                continue
            W, H = page_image.size

            for cell_idx, cell in enumerate(table_item.data.table_cells):
                try:
                    if cell.bbox is None:
                        continue

                    # docling 셀 bbox(BOTTOMLEFT) → 페이지 이미지 픽셀 좌표(TOPLEFT)
                    crop = (
                        cell.bbox
                        .to_top_left_origin(page_height=page.size.height)
                        .scale_to_size(old_size=page.size, new_size=page.image.size)
                    )
                    x0, y0, x1, y1 = crop.as_tuple()
                    # 정규화 + 페이지 경계 클램프 + degenerate skip
                    x0, x1 = sorted((x0, x1))
                    y0, y1 = sorted((y0, y1))
                    x0 = max(0, min(x0, W)); x1 = max(0, min(x1, W))
                    y0 = max(0, min(y0, H)); y1 = max(0, min(y1, H))
                    if (x1 - x0) < 1 or (y1 - y0) < 1:
                        continue

                    cell_img = page_image.crop((x0, y0, x1, y1))

                    # 아주 작은 셀은 OCR 가독성을 위해 확대(기존 target_height=20, ≤4x)
                    ch = y1 - y0
                    zoom = min(max(20.0 / ch, 1.0), 4.0) if ch > 0 else 1.0
                    if zoom > 1.0:
                        cell_img = cell_img.resize(
                            (max(1, round((x1 - x0) * zoom)), max(1, round(ch * zoom))),
                            Image.LANCZOS,
                        )

                    buf = io.BytesIO()
                    cell_img.save(buf, format="PNG")
                    result = _post_ocr_bytes(buf.getvalue(), ocr_endpoint, timeout=timeout)
                    rec_texts, _rec_scores, _rec_boxes = _extract_ocr_fields(result)

                    cell.text = ""
                    for t in rec_texts:
                        if len(cell.text) > 0:
                            cell.text += " "
                        cell.text += t if t else ""
                except Exception as cell_err:
                    # 한 셀 실패가 나머지 셀/표를 막지 않도록 격리
                    print(f"OCR cell processing failed (table={table_idx}, cell={cell_idx}): {cell_err}")
                    continue
    except Exception as e:
        print(f"OCR processing failed: {e}")

    return document
