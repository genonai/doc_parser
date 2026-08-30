"""yaml → docling 파이프라인 옵션 해석 중 facade 4종이 똑같이 하던 부분.

intelligent / convert / chunking / parser 가 각자 복제해 두었던 OCR 런타임 설정과
PDF 파이프라인 기본값 해석을 한 벌로 모았다. 네 사본의 차이는 로그 접두와 줄바꿈,
그리고 __init__ 안에서의 배치 순서뿐이었고 해석 결과는 같았다.

값 자체(엔드포인트·타임아웃 등)는 여전히 yaml 이 정한다. 이 모듈은 "yaml 을 어떻게
읽을지"만 담는다. 사이트에서 조정하는 지점은 yaml 이지 이 코드가 아니다.

layout(genos_layout) 설정도 여기서 해석한다. 예전에는 네 사본의 기본값과 지원 키가
달랐다(타임아웃 1200 vs 3600, DotsOCR fallback 키를 intelligent 만 읽음). 기본값은
docling 의 GenosLayoutOptions 기본에 맞춰 통일했다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from docling.datamodel.pipeline_options import (
    AcceleratorDevice,
    AcceleratorOptions,
    TableFormerMode,
)

from genon.preprocessor.facade.common.config_parse import (
    as_dict,
    parse_optional_bool,
    parse_optional_float,
    parse_optional_int,
)

_log = logging.getLogger(__name__)

ACCELERATOR_DEVICE_MAP = {
    "auto": AcceleratorDevice.AUTO,
    "cpu": AcceleratorDevice.CPU,
    "cuda": AcceleratorDevice.CUDA,
    "mps": AcceleratorDevice.MPS,
}

TABLE_FORMER_MODE_MAP = {
    "fast": TableFormerMode.FAST,
    "accurate": TableFormerMode.ACCURATE,
}


@dataclass(frozen=True)
class OcrRuntime:
    """OCR 엔드포인트와 재OCR 트리거 관련 런타임 값."""

    endpoint: str
    mode: str                        # "auto" | "force" | "disable"
    table_cell_ocr_timeout: int
    glyph_table_cell_threshold: int
    glyph_document_threshold: int


def resolve_ocr_runtime(cfg: dict, ocr_cfg: dict) -> OcrRuntime:
    """ocr 섹션에서 엔드포인트·모드·타임아웃·글리프 임계값을 해석한다.

    엔드포인트 정식 위치는 ocr.paddle.ocr_endpoint 다.
    구버전 호환으로 ocr.ocr_endpoint(상위)와 최상위 ocr_endpoint 도 폴백으로 인식한다.
    셋 다 없으면 빈 문자열이고 경고를 남긴다. 예전에는 사내 주소가 코드 기본값으로
    박혀 있었다 — 사이트 배포본에 그대로 나가므로 yaml 지정을 정상 경로로 되돌렸다.
    """
    cfg = as_dict(cfg)
    ocr_cfg = as_dict(ocr_cfg)
    paddle_cfg = as_dict(ocr_cfg.get("paddle"))
    endpoint = (
        paddle_cfg.get("ocr_endpoint")
        or ocr_cfg.get("ocr_endpoint")
        or cfg.get("ocr_endpoint", "")
    )

    if not endpoint:
        _log.warning(
            "[DocumentProcessor] ocr.paddle.ocr_endpoint 가 비어 있습니다. "
            "사이트 배포 시 yaml 에 반드시 지정하세요."
        )

    # OCR 수행 모드. "auto"(default)=휴리스틱 기반 재OCR / "force"=무조건 전체 OCR / "disable"=OCR 안 함
    mode = str(ocr_cfg.get("ocr_mode", cfg.get("ocr_mode", "auto"))).lower().strip()
    if mode not in {"auto", "force", "disable"}:
        _log.warning(f"[DocumentProcessor] Unknown ocr_mode '{mode}', fallback to 'auto'")
        mode = "auto"

    # 테이블 셀 재OCR HTTP timeout (ocr_all_table_cells). 잘못된 값은 60 으로 폴백.
    timeout = parse_optional_int(ocr_cfg.get("table_cell_ocr_timeout"), "ocr.table_cell_ocr_timeout")
    timeout = timeout if timeout and timeout > 0 else 60

    # 글리프 기반 auto-OCR 재트리거 임계값.
    glyph_cfg = as_dict(ocr_cfg.get("glyph_detection"))
    cell_th = parse_optional_int(
        glyph_cfg.get("table_cell_threshold"), "ocr.glyph_detection.table_cell_threshold"
    )
    doc_th = parse_optional_int(
        glyph_cfg.get("document_threshold"), "ocr.glyph_detection.document_threshold"
    )
    return OcrRuntime(
        endpoint=endpoint,
        mode=mode,
        table_cell_ocr_timeout=timeout,
        glyph_table_cell_threshold=cell_th if cell_th and cell_th > 0 else 1,
        glyph_document_threshold=doc_th if doc_th and doc_th > 0 else 10,
    )


@dataclass(frozen=True)
class PdfBasics:
    """pdf_pipeline 섹션에서 나오는 PdfPipelineOptions 기본값들."""

    accelerator_options: AcceleratorOptions
    images_scale: int
    generate_page_images: Optional[bool]     # None 이면 호출부 기본값(True) 사용
    generate_picture_images: Optional[bool]  # None 이면 호출부 기본값(True) 사용
    table_structure_mode: TableFormerMode


def resolve_pdf_basics(pdf_cfg: dict) -> PdfBasics:
    """pdf_pipeline 섹션에서 가속기·이미지 배율·표 구조 모드를 해석한다."""
    pdf_cfg = as_dict(pdf_cfg)

    device_str = str(pdf_cfg.get("device", "auto")).lower().strip()
    device = ACCELERATOR_DEVICE_MAP.get(device_str)
    if device is None:
        _log.warning(f"[DocumentProcessor] Unknown pdf_pipeline.device '{device_str}', fallback to 'auto'")
        device = AcceleratorDevice.AUTO

    num_threads = parse_optional_int(pdf_cfg.get("num_threads"), "pdf_pipeline.num_threads")
    if num_threads is None or num_threads <= 0:
        num_threads = 8

    images_scale = parse_optional_int(pdf_cfg.get("images_scale"), "pdf_pipeline.images_scale")
    if images_scale is None or images_scale <= 0:
        images_scale = 2

    table_mode_str = str(pdf_cfg.get("table_structure_mode", "accurate")).lower().strip()
    table_structure_mode = TABLE_FORMER_MODE_MAP.get(table_mode_str)
    if table_structure_mode is None:
        _log.warning(
            f"[DocumentProcessor] Unknown pdf_pipeline.table_structure_mode "
            f"'{table_mode_str}', fallback to 'accurate'"
        )
        table_structure_mode = TableFormerMode.ACCURATE

    return PdfBasics(
        accelerator_options=AcceleratorOptions(num_threads=num_threads, device=device),
        images_scale=images_scale,
        generate_page_images=parse_optional_bool(
            pdf_cfg.get("generate_page_images"), "pdf_pipeline.generate_page_images"),
        generate_picture_images=parse_optional_bool(
            pdf_cfg.get("generate_picture_images"), "pdf_pipeline.generate_picture_images"),
        table_structure_mode=table_structure_mode,
    )


@dataclass(frozen=True)
class LayoutSettings:
    """layout 섹션에서 나오는 값들. genos_layout(DotsOCR) 호출·생성 파라미터 포함.

    기본값은 docling 의 GenosLayoutOptions 기본과 일치시킨다(timeout 1200,
    length_fallback True, fallback_dpi 200, table_fallback True). 예전에는 facade 마다
    달라서 convert/chunking 만 timeout 3600 이었고 fallback 키 3개는 intelligent 만
    읽었다 — yaml 에 써도 나머지 facade 에서는 무시됐다.
    """

    model_type: object          # LayoutModelType
    endpoint: str
    api_key: str
    page_batch_size: int
    max_completion_tokens: int
    model: str
    timeout: int
    retry_count: int
    temperature: float
    top_p: float
    repetition_penalty: float
    length_fallback_enabled: bool
    fallback_dpi: int
    table_fallback_enabled: bool


def resolve_layout_settings(cfg: dict, layout_cfg: dict) -> LayoutSettings:
    """layout 섹션을 해석한다. 엔드포인트/키는 yaml 이 없으면 빈 문자열이고 경고를 남긴다."""
    from docling.datamodel.pipeline_options import LayoutModelType

    cfg = as_dict(cfg)
    layout_cfg = as_dict(layout_cfg)
    genos_cfg = as_dict(layout_cfg.get("genos_layout"))

    # layout 모델 선택. "genos_layout"(default) / "docling_layout". 잘못된 값은 경고 후 폴백.
    model_type_str = str(
        layout_cfg.get("layout_model_type", cfg.get("layout_model_type", "genos_layout"))
    ).lower().strip()
    if model_type_str == LayoutModelType.DOCLING_LAYOUT.value:
        model_type = LayoutModelType.DOCLING_LAYOUT
    else:
        if model_type_str != LayoutModelType.GENOS_LAYOUT.value:
            _log.warning(
                f"[DocumentProcessor] Unknown layout_model_type '{model_type_str}', "
                f"fallback to '{LayoutModelType.GENOS_LAYOUT.value}'"
            )
        model_type = LayoutModelType.GENOS_LAYOUT

    endpoint = genos_cfg.get("endpoint") or cfg.get("layout_endpoint", "")
    api_key = genos_cfg.get("api_key") or cfg.get("layout_api_key", "")
    if model_type == LayoutModelType.GENOS_LAYOUT and not endpoint:
        _log.warning(
            "[DocumentProcessor] layout.genos_layout.endpoint 가 비어 있습니다. "
            "사이트 배포 시 yaml 에 반드시 지정하세요."
        )

    def _int(key: str, default: int, *, allow_zero: bool = False) -> int:
        value = parse_optional_int(genos_cfg.get(key), f"layout.genos_layout.{key}")
        if value is None or (value < 0 if allow_zero else value <= 0):
            return default
        return value

    def _float(key: str, default: float, upper: Optional[float] = None) -> float:
        value = parse_optional_float(genos_cfg.get(key), f"layout.genos_layout.{key}")
        if value is None or value < 0 or (upper is not None and not (0 < value <= upper)):
            return default
        return value

    length_fallback = parse_optional_bool(
        genos_cfg.get("length_fallback_enabled"), "layout.genos_layout.length_fallback_enabled")
    table_fallback = parse_optional_bool(
        genos_cfg.get("table_fallback_enabled"), "layout.genos_layout.table_fallback_enabled")

    return LayoutSettings(
        model_type=model_type,
        endpoint=endpoint,
        api_key=api_key,
        # genos layout 모델은 batch size 를 32 로 둔다.
        page_batch_size=_int("page_batch_size", 32),
        max_completion_tokens=_int("max_completion_tokens", 16384),
        model=genos_cfg.get("model") or "dots-mocr",
        # 이슈 #278: per-page hang 방지(GenosLayoutOptions 기본과 통일)
        timeout=_int("timeout", 1200),
        retry_count=_int("retry_count", 2, allow_zero=True),
        temperature=_float("temperature", 0.1),
        top_p=_float("top_p", 0.9, upper=1.0),
        repetition_penalty=_float("repetition_penalty", 1.15),
        length_fallback_enabled=True if length_fallback is None else length_fallback,
        fallback_dpi=_int("fallback_dpi", 200),
        table_fallback_enabled=True if table_fallback is None else table_fallback,
    )


def apply_layout_settings(pipe_line_options, settings: LayoutSettings) -> None:
    """해석한 layout 값을 PdfPipelineOptions 에 적용한다(page_batch_size 제외)."""
    layout = pipe_line_options.layout_options
    layout.layout_model_type = settings.model_type
    genos = layout.genos_layout_options
    genos.endpoint = settings.endpoint
    genos.api_key = settings.api_key
    genos.max_completion_tokens = settings.max_completion_tokens
    genos.model = settings.model
    genos.timeout = settings.timeout
    genos.retry_count = settings.retry_count
    genos.temperature = settings.temperature
    genos.top_p = settings.top_p
    genos.repetition_penalty = settings.repetition_penalty
    genos.length_fallback_enabled = settings.length_fallback_enabled
    genos.fallback_dpi = settings.fallback_dpi
    genos.table_fallback_enabled = settings.table_fallback_enabled
