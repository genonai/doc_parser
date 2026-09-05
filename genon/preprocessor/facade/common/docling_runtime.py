"""facade 가 공유하는 docling 런타임 배선.

같은 배관(OCR 옵션 · PDF 파이프라인 옵션 · layout · 컨버터 4개 · enricher 생성)이
parser / intelligent / convert 세 facade 에 복제돼 있었다. 복제의 대가는 이미 치렀다 —
parser 의 사본이 원본을 따라가지 못해 check_empty_text 가 빠진 채로 남았고 크래시로 드러났다.

이 모듈은 그 공통분을 한 벌로 갖는다. facade 는 상속하거나(intelligent/convert)
합성해서(parser) 쓴다.

무엇이 여기 있고 무엇이 없는가
  있다: __init__ 의 docling 배선, 로딩(load_documents*), enrich_* 위임, 글리프·빈 텍스트
        검사, 표 셀 재OCR, 런타임 kwargs 정규화
  없다: enrichment() — 세 facade 의 본문이 시그니처·예외·PPT 처리에서 서로 다르다.
        특히 convert 의 로컬 GenosServiceException 은 stage/error_type 인자를 받지 않아
        base 가 intelligent 판을 담으면 LLM 오류가 났을 때만 TypeError 로 터진다.
        청킹·벡터 조합 헬퍼(safe_join / setup_logging / get_media_files /
        check_appendix_keywords) — docling 런타임이 아니다.

서브클래스가 끼어드는 자리는 훅 3개다.
  _pre_pipeline_setup(cfg)   PdfPipelineOptions 를 만들기 **전**. 파이프라인 옵션에 영향을
                             주는 자기 설정(table_image / page_description 등)을 여기서 읽는다.
  _force_page_images()       generate_page_images 를 강제할지. 위 훅이 세운 값을 본다.
                             컨버터 생성 전에 확정돼야 OCR 컨버터 옵션까지 반영된다.
  _post_runtime_setup(cfg, ec)  컨버터·enricher 생성 뒤. 서브클래스 고유 배선을 둔다.

주의: object.__new__ 로 __init__ 을 우회해 만든 인스턴스를 쓰는 단위 테스트가 있다.
프로세서 속성을 읽는 메서드는 getattr(..., 기본값) 으로 속성 부재를 견뎌야 한다.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from pathlib import Path
from typing import Any, ClassVar, Optional

from docling.datamodel.pipeline_options import (
    DataEnrichmentOptions,
    PdfPipelineOptions,
    PipelineOptions,
    UpstageOcrOptions,
)
from docling.datamodel.settings import settings as docling_settings
from docling_core.types.doc import DoclingDocument

from genon.preprocessor.facade.common import config_parse as cp
from genon.preprocessor.facade.common import docling_ops as dops
from genon.preprocessor.facade.common import format_alias as fa
from genon.preprocessor.facade.common import pipeline_setup as ps
from genon.preprocessor.facade.common import runtime_kwargs as rk
from genon.preprocessor.facade.enrichment.custom_fields_enricher import (
    build_document_custom_fields_enrichers,
)
from genon.preprocessor.facade.enrichment.doc_summary import (
    DocSummaryEnricher,
    DocSummaryOptions,
)
from genon.preprocessor.facade.enrichment.enrichment_config import EnrichmentConfig
from genon.preprocessor.facade.enrichment.image_description import (
    ImageDescriptionEnricher,
    ImageDescriptionOptions,
)
from genon.preprocessor.facade.enrichment.metadata_enricher import MetadataEnricher
from genon.preprocessor.facade.enrichment.table_description import (
    TableDescriptionEnricher,
    TableDescriptionOptions,
)
from genon.preprocessor.facade.enrichment.table_text_description import (
    TableTextDescriptionEnricher,
)

_log = logging.getLogger(__name__)

_as_dict = cp.as_dict
_parse_optional_bool = cp.parse_optional_bool
_parse_optional_int = cp.parse_optional_int


class DoclingRuntimeBase:
    """docling 파싱 런타임. facade 가 상속하거나 합성해서 쓴다."""

    # MetadataEnricher 에 thinking/thinking_dialect 를 넘길지. parser·convert 는 넘기고
    # intelligent 는 넘기지 않는다(드리프트). 동작을 그대로 보존하려고 플래그로 남겼다.
    # 통일은 별도 이슈다 — 여기서 바꾸면 intelligent 의 metadata 추출 동작이 달라진다.
    _metadata_enricher_passes_thinking: ClassVar[bool] = True

    def __init__(self, config: dict | None = None, config_path: str | None = None):
        cfg = _as_dict(config)
        self._config_dir = Path(config_path).resolve().parent if config_path else Path.cwd()
        # 런타임 kwargs 기본값(img_desc/chart_desc/chart_detection/doc_summary) 용도
        self._runtime_cfg = _as_dict(cfg.get("runtime"))
        ocr_cfg = _as_dict(cfg.get("ocr"))
        layout_cfg = _as_dict(cfg.get("layout"))
        pdf_cfg = _as_dict(cfg.get("pdf_pipeline"))
        ec = EnrichmentConfig.from_raw(cfg.get("enrichment"), self._config_dir, parent_cfg=cfg)

        # 포맷별 처리 설정. 파싱 라우팅이 읽는다.
        self._setup_format_config(cfg)

        # 파이프라인 옵션에 영향을 주는 서브클래스 설정(table_image / page_description 등).
        # 반드시 PdfPipelineOptions 생성보다 먼저다 — 뒤에 두면 OCR 컨버터 옵션에 반영되지 않는다.
        self._pre_pipeline_setup(cfg)

        # OCR 엔드포인트는 ocr.paddle.ocr_endpoint 가 정식 위치.
        # 구버전 호환: ocr.ocr_endpoint(상위) / 최상위 ocr_endpoint 도 폴백으로 인식.
        # 해석은 facade/common/pipeline_setup.py 로 모았다(조정 지점은 yaml 의 ocr 섹션).
        _ocr_rt = ps.resolve_ocr_runtime(cfg, ocr_cfg)
        ocr_ep = _ocr_rt.endpoint
        self.ocr_mode = _ocr_rt.mode
        self._table_cell_ocr_timeout = _ocr_rt.table_cell_ocr_timeout
        self._glyph_table_cell_threshold = _ocr_rt.glyph_table_cell_threshold
        self._glyph_document_threshold = _ocr_rt.glyph_document_threshold

        # 해석·적용은 facade/common/pipeline_setup.py 로 모았다(조정 지점은 yaml 의 layout 섹션).
        _layout = ps.resolve_layout_settings(cfg, layout_cfg)

        ocr_options = self._build_ocr_options(ocr_cfg, paddle_endpoint=ocr_ep)
        if isinstance(ocr_options, UpstageOcrOptions):
            self.ocr_endpoint = ocr_options.api_endpoint
        else:
            self.ocr_endpoint = ocr_ep

        self.page_chunk_counts = defaultdict(int)

        # pdf_pipeline 섹션 해석은 facade/common/pipeline_setup.py 로 모았다.
        _pdf = ps.resolve_pdf_basics(pdf_cfg)
        accelerator_options = _pdf.accelerator_options
        images_scale = _pdf.images_scale
        generate_page_images = _pdf.generate_page_images
        generate_picture_images = _pdf.generate_picture_images
        table_structure_mode = _pdf.table_structure_mode

        self.pipe_line_options = PdfPipelineOptions()
        self.pipe_line_options.generate_page_images = (
            True if generate_page_images is None else generate_page_images
        )
        self.pipe_line_options.generate_picture_images = (
            True if generate_picture_images is None else generate_picture_images
        )
        # 표 이미지 크롭이나 페이지 단위 설명은 페이지 렌더 이미지를 소스로 하므로,
        # 그 기능을 켠 facade 는 여기서 generate_page_images 를 강제한다.
        if self._force_page_images():
            self.pipe_line_options.generate_page_images = True
        self.pipe_line_options.do_ocr = False
        self.pipe_line_options.ocr_options = ocr_options
        self.pipe_line_options.images_scale = images_scale

        ps.apply_layout_settings(self.pipe_line_options, _layout)
        docling_settings.perf.page_batch_size = _layout.page_batch_size

        self.pipe_line_options.do_table_structure = True
        self.pipe_line_options.table_structure_options.do_cell_matching = True
        self.pipe_line_options.table_structure_options.mode = table_structure_mode
        self.pipe_line_options.accelerator_options = accelerator_options

        # docling 모델(TableFormer 등) 로컬 경로. config 에 값이 있을 때만 설정하고,
        # 비어있으면 설정하지 않아 docling 기본 캐시 동작을 그대로 유지(backward compat).
        # (아래 ocr_pipe_line_options 는 pipe_line_options 의 deep copy 라 자동 전파됨)
        models_cfg = _as_dict(cfg.get("models"))
        artifacts_path = models_cfg.get("artifacts_path")
        if artifacts_path:
            self.pipe_line_options.artifacts_path = Path(artifacts_path)

        self.simple_pipeline_options = PipelineOptions()
        self.simple_pipeline_options.save_images = False

        # 이미지/차트 description 옵션. chart.enable 이면 변환 단계에서 그림 분류가 필요하므로
        # 컨버터(ocr 포함) 생성 전에 옵션을 결정하고 do_picture_classification 을 켜 둔다.
        # 모델(ds4sd--DocumentFigureClassifier)은 빌드 시 /models 에 포함된다.
        self.image_description_options = ImageDescriptionOptions.from_config(
            image_desc_cfg=ec.image_description_cfg,
            fallback_api_url=ec.api_url,
            fallback_api_key=ec.api_key,
            fallback_model=ec.model,
            config_dir=self._config_dir,
        )
        # 런타임 kwargs 오버라이드의 기준(base) 옵션 보관
        self._base_image_description_options = self.image_description_options
        if self.image_description_options.chart_enabled:
            try:
                self.pipe_line_options.do_picture_classification = True
            except Exception as exc:
                _log.warning(f"[DoclingRuntime] do_picture_classification 설정 실패: {exc}")

        # 표 description 옵션. VLM 이 표 영역을 crop 하려면 페이지 이미지가 필요하므로
        # base 옵션이 켜져 있으면 컨버터 생성 전에 generate_page_images 를 강제한다.
        self.table_description_options = TableDescriptionOptions.from_config(
            table_desc_cfg=ec.table_description_cfg,
            fallback_api_url=ec.api_url,
            fallback_api_key=ec.api_key,
            fallback_model=ec.model,
            config_dir=self._config_dir,
        )
        self._base_table_description_options = self.table_description_options
        if self.table_description_options.enabled:
            try:
                self.pipe_line_options.generate_page_images = True
            except Exception as exc:
                _log.warning(f"[DoclingRuntime] generate_page_images 설정 실패: {exc}")

        # 문서 본문요약(doc_summary) 옵션. image/table 이 공유하는 {{doc_summary}} 를 1회 계산.
        self.doc_summary_options = DocSummaryOptions.from_config(
            doc_summary_cfg=ec.doc_summary_cfg,
            fallback_api_url=ec.api_url,
            fallback_api_key=ec.api_key,
            fallback_model=ec.model,
            config_dir=self._config_dir,
        )
        self._base_doc_summary_options = self.doc_summary_options

        # pipe_line_options 의 layout 설정이 deep copy 에 포함되므로 별도 재설정 불필요
        self.ocr_pipe_line_options = self.pipe_line_options.model_copy(deep=True)
        self.ocr_pipe_line_options.do_ocr = True
        self.ocr_pipe_line_options.ocr_options = ocr_options.model_copy(deep=True)
        self.ocr_pipe_line_options.ocr_options.force_full_page_ocr = True

        self._create_converters()

        self.image_description_enricher = ImageDescriptionEnricher(
            self.image_description_options
        )
        self.table_description_enricher = TableDescriptionEnricher(
            self.table_description_options
        )
        # 텍스트 표 설명. 자체 url/model 이 있으면 custom_fields 의 LLM 사용 여부와 무관하게
        # 이 실행기가 표 설명을 맡는다(table_text_description 모듈 docstring 참고).
        self.table_text_description_enricher = TableTextDescriptionEnricher(
            ec.table_text_description_cfg
        )
        self.doc_summary_enricher = DocSummaryEnricher(self.doc_summary_options)
        # 원본 설정 목록을 남긴다. 파싱 라우팅(json_text / front_matter / json_records 등)이
        # 같은 목록에서 자기 몫을 골라 읽는다.
        self.custom_fields_cfgs = list(ec.custom_fields_cfgs)
        self.custom_fields_enrichers: list = build_document_custom_fields_enrichers(
            self.custom_fields_cfgs
        )

        # 사용자가 커스텀 metadata 신호(prompt/파일/output_fields/parser)를 하나라도 지정한 경우
        # 커스텀 MetadataEnricher를 사용한다. 지정되지 않으면 docling 내장 enricher가 동작한다
        # (하위 호환). built-in default system prompt 가 이 게이트를 흔들지 않도록
        # system_prompt 유무가 아닌 has_custom_metadata 로 판단한다.
        self.metadata_enricher: "Optional[MetadataEnricher]" = (
            self._build_metadata_enricher(ec)
            if ec.metadata.do_metadata and ec.metadata.has_custom_metadata
            else None
        )

        self.enrichment_options = DataEnrichmentOptions(
            do_toc_enrichment=ec.toc.do_toc,
            toc_doc_type=ec.toc.doc_type,
            # 커스텀 MetadataEnricher가 있으면 docling 내장 metadata 추출을 비활성화한다.
            extract_metadata=ec.metadata.do_metadata and self.metadata_enricher is None,
            toc_api_provider="custom",
            metadata_api_provider="custom",
            toc_api_base_url=ec.toc.url,
            metadata_api_base_url=ec.metadata.url,
            toc_api_key=ec.toc.api_key,
            metadata_api_key=ec.metadata.api_key,
            toc_model=ec.toc.model,
            metadata_model=ec.metadata.model,
            toc_temperature=ec.toc.temperature,
            toc_top_p=ec.toc.top_p,
            toc_seed=ec.toc.seed,
            toc_max_tokens=ec.toc.max_tokens,
            toc_repetition_penalty=ec.toc.repetition_penalty,
            toc_precheck_enabled=ec.toc.precheck_enabled,
            toc_max_context_tokens=ec.toc.precheck_max_context_tokens,
            toc_completion_reserved_tokens=ec.toc.precheck_completion_reserved_tokens,
            toc_split_enabled=ec.toc.split_enabled,
            toc_pages_per_chunk=ec.toc.split_pages_per_chunk,
            toc_page_overlap=ec.toc.split_page_overlap,
            toc_carryover_max_tokens=ec.toc.split_carryover_max_tokens,
            metadata_precheck_enabled=ec.metadata.precheck_enabled,
            metadata_max_context_tokens=ec.metadata.precheck_max_context_tokens,
            metadata_completion_reserved_tokens=ec.metadata.precheck_completion_reserved_tokens,
            toc_system_prompt=ec.toc.system_prompt,
            toc_user_prompt=ec.toc.user_prompt,
            toc_thinking=ec.toc.thinking,
            toc_thinking_dialect=ec.toc.thinking_dialect,
            metadata_thinking=ec.metadata.thinking,
            metadata_thinking_dialect=ec.metadata.thinking_dialect,
        )

        self._post_runtime_setup(cfg, ec)

    # ------------------------------------------------------------------
    # 서브클래스 훅
    # ------------------------------------------------------------------

    #: formats.xlsx.processing_mode 기본값. parser 는 tabular, intelligent/convert 는 docling.
    _xlsx_default_mode: ClassVar[str] = "tabular"

    def _pre_pipeline_setup(self, cfg: dict) -> None:
        """PdfPipelineOptions 생성 전에 불린다. 기본은 아무것도 하지 않는다."""

    def _force_page_images(self) -> bool:
        """generate_page_images 를 강제할지. 서브클래스가 자기 설정을 보고 답한다."""
        return False

    def _post_runtime_setup(self, cfg: dict, ec: EnrichmentConfig) -> None:
        """컨버터·enricher 생성 뒤에 불린다. 기본은 아무것도 하지 않는다."""

    def _build_metadata_enricher(self, ec: EnrichmentConfig) -> MetadataEnricher:
        kwargs: dict[str, Any] = dict(
            url=ec.metadata.url,
            api_key=ec.metadata.api_key,
            model=ec.metadata.model,
            system_prompt=ec.metadata.system_prompt,
            user_prompt=ec.metadata.user_prompt,
            output_fields=ec.metadata.output_fields,
            parser=ec.metadata.parser,
            pages=ec.metadata.pages,
            max_tokens=ec.metadata.max_tokens,
            temperature=ec.metadata.temperature,
            timeout=ec.metadata.timeout,
            config_dir=self._config_dir,
            variables=ec.metadata.variables,
            template_mode=ec.metadata.template_mode,
        )
        if self._metadata_enricher_passes_thinking:
            kwargs["thinking"] = ec.metadata.thinking
            kwargs["thinking_dialect"] = ec.metadata.thinking_dialect
        return MetadataEnricher(**kwargs)

    # ------------------------------------------------------------------
    # 포맷별 처리 설정
    # ------------------------------------------------------------------

    def _setup_format_config(self, cfg: dict) -> None:
        """formats 섹션(xlsx / md / extension_aliases) 해석.

        확장자별 분기를 코드에 늘리지 않고 설정 한 줄로 새 원천을 받기 위한 장치다.
        """
        formats_cfg = _as_dict(cfg.get("formats"))

        # xlsx(엑셀) 처리 설정(이슈 #288). formats.xlsx 아래에 둔다.
        #   tabular: openpyxl 로 병합셀 unmerge+forward-fill 후 데이터 행마다 parse element 생성.
        #   docling: docling MsExcel 백엔드로 DoclingDocument 생성 후 parse-JSON 직렬화.
        #   tabular.{header_row, multi_table}: tabular 모드 전용 세부 옵션
        xlsx_cfg = _as_dict(formats_cfg.get("xlsx"))
        tabular_cfg = _as_dict(xlsx_cfg.get("tabular"))
        default_mode = self._xlsx_default_mode
        xlsx_mode = str(xlsx_cfg.get("processing_mode", default_mode)).strip().lower()
        if xlsx_mode not in {"docling", "tabular"}:
            _log.warning(
                f"[DocumentProcessor] Unknown formats.xlsx.processing_mode '{xlsx_mode}', "
                f"fallback to '{default_mode}'."
            )
            xlsx_mode = default_mode
        self._xlsx_cfg = {
            "processing_mode": xlsx_mode,
            "header_row": _parse_optional_int(
                tabular_cfg.get("header_row"), "formats.xlsx.tabular.header_row") or 0,
            "multi_table": bool(_parse_optional_bool(
                tabular_cfg.get("multi_table"), "formats.xlsx.tabular.multi_table")),
        }

        # md(마크다운) 처리 설정. formats.md 아래에 둔다.
        #   docling(기본): MarkdownDocumentBackend 로 파싱 → 헤딩/표 구조 유지 + enrichment 적용.
        #   text: 레거시 TextLoader 경로. 구조가 없고 enrichment 가 걸리지 않는다.
        md_cfg = _as_dict(formats_cfg.get("md"))
        md_mode = str(md_cfg.get("processing_mode", "docling")).strip().lower()
        if md_mode not in {"docling", "text"}:
            _log.warning(
                f"[DocumentProcessor] Unknown formats.md.processing_mode '{md_mode}', "
                f"fallback to 'docling'."
            )
            md_mode = "docling"
        self._md_cfg = {"processing_mode": md_mode}

        # 비표준 확장자 별칭. 예) {".parsed": ".md"} — *.parsed 를 마크다운으로 보고 라우팅한다.
        self._ext_aliases = fa.parse_extension_aliases(formats_cfg)
        if self._ext_aliases:
            _log.info(f"[DocumentProcessor] 확장자 별칭: {self._ext_aliases}")

    # ------------------------------------------------------------------
    # docling 배관
    # ------------------------------------------------------------------

    @staticmethod
    def _build_ocr_options(ocr_cfg: dict, paddle_endpoint: str):
        return dops.build_ocr_options(ocr_cfg, paddle_endpoint)

    def _create_converters(self):
        """컨버터들을 생성하는 헬퍼 메서드"""
        (self.converter, self.second_converter,
         self.ocr_converter, self.ocr_second_converter) = dops.create_converters(
            self.pipe_line_options, self.ocr_pipe_line_options)

    def load_documents_with_docling(self, file_path: str, **kwargs: dict) -> DoclingDocument:
        # kwargs에서 save_images 값을 가져와서 옵션 업데이트
        save_images = kwargs.get('save_images', True)
        include_wmf = kwargs.get('include_wmf', False)

        # save_images 옵션이 현재 설정과 다르면 컨버터 재생성
        if (self.simple_pipeline_options.save_images != save_images or
                getattr(self.simple_pipeline_options, 'include_wmf', False) != include_wmf):
            self.simple_pipeline_options.save_images = save_images
            self.simple_pipeline_options.include_wmf = include_wmf
            self._create_converters()

        try:
            conv_result = self.converter.convert(file_path, raises_on_error=True)
        except Exception:
            conv_result = self.second_converter.convert(file_path, raises_on_error=True)
        return conv_result.document

    def load_documents_with_docling_ocr(self, file_path: str, **kwargs: dict) -> DoclingDocument:
        save_images = kwargs.get('save_images', True)
        include_wmf = kwargs.get('include_wmf', False)

        if (self.simple_pipeline_options.save_images != save_images or
                getattr(self.simple_pipeline_options, 'include_wmf', False) != include_wmf):
            self.simple_pipeline_options.save_images = save_images
            self.simple_pipeline_options.include_wmf = include_wmf
            self._create_converters()

        try:
            conv_result = self.ocr_converter.convert(file_path, raises_on_error=True)
        except Exception:
            conv_result = self.ocr_second_converter.convert(file_path, raises_on_error=True)
        return conv_result.document

    def load_documents(self, file_path: str, **kwargs: dict) -> DoclingDocument:
        return self.load_documents_with_docling(file_path, **kwargs)

    # ------------------------------------------------------------------
    # 런타임 kwargs
    # ------------------------------------------------------------------

    def _normalize_runtime_kwargs(self, kwargs: dict) -> dict:
        return rk.normalize_runtime_kwargs(self, kwargs)

    def _configure_runtime_image_mode(self, kwargs: dict):
        rk.configure_runtime_image_mode(self, kwargs)

    # ------------------------------------------------------------------
    # enrichment 위임 (enrichment() 자체는 facade 가 갖는다 - 모듈 docstring 참조)
    # ------------------------------------------------------------------

    def _get_or_create_image_description_enricher(self) -> ImageDescriptionEnricher:
        enricher = getattr(self, "image_description_enricher", None)
        if enricher is None:
            # 테스트 등에서 __init__ 우회 시 legacy attribute 기반으로 재구성
            legacy_options = ImageDescriptionOptions.from_legacy_processor(self)
            enricher = ImageDescriptionEnricher(legacy_options)
            self.image_description_enricher = enricher
        return enricher

    def enrich_image_descriptions(self, document: DoclingDocument, **kwargs: dict) -> DoclingDocument:
        enricher = self._get_or_create_image_description_enricher()
        if enricher is None:
            return document
        return enricher.enrich(document, **kwargs)

    def _get_or_create_doc_summary_enricher(self) -> DocSummaryEnricher:
        enricher = getattr(self, "doc_summary_enricher", None)
        if enricher is None:
            base = getattr(self, "_base_doc_summary_options", None)
            enricher = DocSummaryEnricher(base or DocSummaryOptions())
            self.doc_summary_enricher = enricher
        return enricher

    def enrich_doc_summary(self, document: DoclingDocument, **kwargs: dict) -> DoclingDocument:
        enricher = self._get_or_create_doc_summary_enricher()
        if enricher is None:
            return document
        return enricher.enrich(document, **kwargs)

    def _get_or_create_table_description_enricher(self) -> TableDescriptionEnricher:
        enricher = getattr(self, "table_description_enricher", None)
        if enricher is None:
            base = getattr(self, "_base_table_description_options", None)
            enricher = TableDescriptionEnricher(base or TableDescriptionOptions())
            self.table_description_enricher = enricher
        return enricher

    def enrich_table_descriptions(self, document: DoclingDocument, **kwargs: dict) -> DoclingDocument:
        enricher = self._get_or_create_table_description_enricher()
        if enricher is None:
            return document
        return enricher.enrich(document, **kwargs)

    async def enrich_metadata(self, document: DoclingDocument, **kwargs: dict) -> DoclingDocument:
        enricher = getattr(self, "metadata_enricher", None)
        if enricher is not None:
            document = await enricher.enrich(document, **kwargs)
        return document

    async def enrich_custom_fields(self, document: DoclingDocument, **kwargs: dict) -> DoclingDocument:
        for enricher in self.custom_fields_enrichers:
            document = await enricher.enrich(document, **kwargs)
        return document

    # ------------------------------------------------------------------
    # 품질 검사 / 재OCR
    # ------------------------------------------------------------------

    def check_glyph_text(self, text: str, threshold: int = 1) -> bool:
        return dops.check_glyph_text(text, threshold)

    def check_glyphs(self, document: DoclingDocument) -> bool:
        return dops.check_glyphs(document, self._glyph_document_threshold)

    def check_empty_text(self, document: DoclingDocument) -> bool:
        return dops.check_empty_text(document)

    def ocr_all_table_cells(self, document: DoclingDocument, pdf_path) -> DoclingDocument:
        """글리프 깨진 텍스트가 있는 표에 대해서만 셀 단위 재OCR 을 수행한다."""
        return dops.ocr_all_table_cells(
            document,
            ocr_endpoint=self.ocr_endpoint,
            cell_threshold=self._glyph_table_cell_threshold,
            timeout=self._table_cell_ocr_timeout,
        )

    def _save_table_images(
        self,
        document: DoclingDocument,
        image_dir: Path,
        reference_path: Optional[Path] = None,
    ) -> None:
        dops.save_table_images(document, image_dir, reference_path)
