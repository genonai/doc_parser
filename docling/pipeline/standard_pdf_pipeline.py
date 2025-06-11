import logging
import sys
import warnings
import json
import re
import difflib
from pathlib import Path
from typing import Optional, cast, Dict, Any

import numpy as np
from docling_core.types.doc import DocItem, ImageRef, PictureItem, TableItem, TextItem, DocItemLabel, SectionHeaderItem
from docling_core.types.doc.document import GraphData, GraphCell, GraphCellLabel

from docling.backend.abstract_backend import AbstractDocumentBackend
from docling.backend.pdf_backend import PdfDocumentBackend
from docling.datamodel.base_models import AssembledUnit, Page
from docling.datamodel.document import ConversionResult
from docling.datamodel.layout_model_specs import LayoutModelConfig
from docling.datamodel.pipeline_options import PdfPipelineOptions, DataEnrichmentOptions
from docling.datamodel.settings import settings
from docling.models.base_ocr_model import BaseOcrModel
from docling.models.code_formula_model import CodeFormulaModel, CodeFormulaModelOptions
from docling.models.factories import get_ocr_factory
from docling.models.layout_model import LayoutModel
from docling.models.page_assemble_model import PageAssembleModel, PageAssembleOptions
from docling.models.page_preprocessing_model import (
    PagePreprocessingModel,
    PagePreprocessingOptions,
)
from docling.models.readingorder_model import ReadingOrderModel, ReadingOrderOptions
from docling.models.table_structure_model import TableStructureModel
from docling.pipeline.base_pipeline import PaginatedPipeline
from docling.utils.model_downloader import download_models
from docling.utils.profiling import ProfilingScope, TimeRecorder
from docling.prompts import PromptManager

_log = logging.getLogger(__name__)


class StandardPdfPipeline(PaginatedPipeline):
    def __init__(self, pipeline_options: PdfPipelineOptions):
        super().__init__(pipeline_options)
        self.pipeline_options: PdfPipelineOptions

        with warnings.catch_warnings():  # deprecated generate_table_images
            warnings.filterwarnings("ignore", category=DeprecationWarning)
            self.keep_images = (
                self.pipeline_options.generate_page_images
                or self.pipeline_options.generate_picture_images
                or self.pipeline_options.generate_table_images
            )

        self.reading_order_model = ReadingOrderModel(options=ReadingOrderOptions())

        ocr_model = self.get_ocr_model(artifacts_path=self.artifacts_path)

        self.build_pipe = [
            # Pre-processing
            PagePreprocessingModel(
                options=PagePreprocessingOptions(
                    images_scale=pipeline_options.images_scale,
                )
            ),
            # OCR
            ocr_model,
            # Layout model
            LayoutModel(
                artifacts_path=self.artifacts_path,
                accelerator_options=pipeline_options.accelerator_options,
                options=pipeline_options.layout_options,
            ),
            # Table structure model
            TableStructureModel(
                enabled=pipeline_options.do_table_structure,
                artifacts_path=self.artifacts_path,
                options=pipeline_options.table_structure_options,
                accelerator_options=pipeline_options.accelerator_options,
            ),
            # Page assemble
            PageAssembleModel(options=PageAssembleOptions()),
        ]

        self.enrichment_pipe = [
            # Code Formula Enrichment Model
            CodeFormulaModel(
                enabled=pipeline_options.do_code_enrichment
                or pipeline_options.do_formula_enrichment,
                artifacts_path=self.artifacts_path,
                options=CodeFormulaModelOptions(
                    do_code_enrichment=pipeline_options.do_code_enrichment,
                    do_formula_enrichment=pipeline_options.do_formula_enrichment,
                ),
                accelerator_options=pipeline_options.accelerator_options,
            ),
            *self.enrichment_pipe,
        ]

        if (
            self.pipeline_options.do_formula_enrichment
            or self.pipeline_options.do_code_enrichment
            or self.pipeline_options.do_picture_classification
            or self.pipeline_options.do_picture_description
        ):
            self.keep_backend = True

        # 프롬프트 매니저 초기화 (카테고리별 사용자 정의 프롬프트 및 API 설정 지원)
        custom_prompts = self._build_custom_prompts()
        custom_api_configs = self._build_custom_api_configs()
        self.prompt_manager = PromptManager(
            custom_prompts=custom_prompts,
            custom_api_configs=custom_api_configs
        )

    def _build_custom_prompts(self) -> Dict[str, Any]:
        """사용자 정의 프롬프트 딕셔너리 구성"""
        custom_prompts = {}
        
        enrichment_options = self.pipeline_options.data_enrichment_options
        
        # TOC 관련 사용자 정의 프롬프트
        if enrichment_options.toc_system_prompt or enrichment_options.toc_user_prompt:
            if "toc_extraction" not in custom_prompts:
                custom_prompts["toc_extraction"] = {}
            if "korean_document" not in custom_prompts["toc_extraction"]:
                custom_prompts["toc_extraction"]["korean_document"] = {}
            
            if enrichment_options.toc_system_prompt:
                custom_prompts["toc_extraction"]["korean_document"]["system"] = enrichment_options.toc_system_prompt
            
            if enrichment_options.toc_user_prompt:
                custom_prompts["toc_extraction"]["korean_document"]["user"] = enrichment_options.toc_user_prompt
        
        # 메타데이터 관련 사용자 정의 프롬프트
        if enrichment_options.metadata_system_prompt or enrichment_options.metadata_user_prompt:
            if "metadata_extraction" not in custom_prompts:
                custom_prompts["metadata_extraction"] = {}
            if "korean_financial" not in custom_prompts["metadata_extraction"]:
                custom_prompts["metadata_extraction"]["korean_financial"] = {}
            
            if enrichment_options.metadata_system_prompt:
                custom_prompts["metadata_extraction"]["korean_financial"]["system"] = enrichment_options.metadata_system_prompt
            
            if enrichment_options.metadata_user_prompt:
                custom_prompts["metadata_extraction"]["korean_financial"]["user"] = enrichment_options.metadata_user_prompt
        
        return custom_prompts
    
    def _build_custom_api_configs(self) -> Dict[str, Dict[str, Any]]:
        """카테고리별 사용자 정의 API 설정 딕셔너리 구성"""
        custom_api_configs = {}
        
        enrichment_options = self.pipeline_options.data_enrichment_options
        
        # TOC API 설정
        if (enrichment_options.toc_api_provider or 
            enrichment_options.toc_api_key or 
            enrichment_options.toc_api_base_url or 
            enrichment_options.toc_model):
            
            toc_config = {}
            toc_config["provider"] = enrichment_options.toc_api_provider or "openrouter"
            toc_config["api_base_url"] = enrichment_options.toc_api_base_url or "https://openrouter.ai/api/v1"
            toc_config["model"] = enrichment_options.toc_model or "google/gemma-3-27b-it:free"
            
            if enrichment_options.toc_api_key:
                toc_config["api_key"] = enrichment_options.toc_api_key
            
            # TOC 선택적 파라미터들
            if enrichment_options.toc_temperature is not None:
                toc_config["temperature"] = enrichment_options.toc_temperature
            if enrichment_options.toc_top_p is not None:
                toc_config["top_p"] = enrichment_options.toc_top_p
            if enrichment_options.toc_seed is not None:
                toc_config["seed"] = enrichment_options.toc_seed
            if enrichment_options.toc_max_tokens is not None:
                toc_config["max_tokens"] = enrichment_options.toc_max_tokens
            
            custom_api_configs["toc_extraction"] = toc_config
        
        # Metadata API 설정
        if (enrichment_options.metadata_api_provider or 
            enrichment_options.metadata_api_key or 
            enrichment_options.metadata_api_base_url or 
            enrichment_options.metadata_model):
            
            metadata_config = {}
            metadata_config["provider"] = enrichment_options.metadata_api_provider or "openrouter"
            metadata_config["api_base_url"] = enrichment_options.metadata_api_base_url or "https://openrouter.ai/api/v1"
            metadata_config["model"] = enrichment_options.metadata_model or "google/gemma-3-27b-it:free"
            
            if enrichment_options.metadata_api_key:
                metadata_config["api_key"] = enrichment_options.metadata_api_key
            
            # Metadata 선택적 파라미터들
            if enrichment_options.metadata_temperature is not None:
                metadata_config["temperature"] = enrichment_options.metadata_temperature
            if enrichment_options.metadata_top_p is not None:
                metadata_config["top_p"] = enrichment_options.metadata_top_p
            if enrichment_options.metadata_seed is not None:
                metadata_config["seed"] = enrichment_options.metadata_seed
            if enrichment_options.metadata_max_tokens is not None:
                metadata_config["max_tokens"] = enrichment_options.metadata_max_tokens
            
            custom_api_configs["metadata_extraction"] = metadata_config
        
        return custom_api_configs

    @staticmethod
    def download_models_hf(
        local_dir: Optional[Path] = None, force: bool = False
    ) -> Path:
        warnings.warn(
            "The usage of StandardPdfPipeline.download_models_hf() is deprecated "
            "use instead the utility `docling-tools models download`, or "
            "the upstream method docling.utils.models_downloader.download_all()",
            DeprecationWarning,
            stacklevel=3,
        )

        output_dir = download_models(output_dir=local_dir, force=force, progress=False)
        return output_dir

    def get_ocr_model(self, artifacts_path: Optional[Path] = None) -> BaseOcrModel:
        factory = get_ocr_factory(
            allow_external_plugins=self.pipeline_options.allow_external_plugins
        )
        return factory.create_instance(
            options=self.pipeline_options.ocr_options,
            enabled=self.pipeline_options.do_ocr,
            artifacts_path=artifacts_path,
            accelerator_options=self.pipeline_options.accelerator_options,
        )

    def initialize_page(self, conv_res: ConversionResult, page: Page) -> Page:
        with TimeRecorder(conv_res, "page_init"):
            page._backend = conv_res.input._backend.load_page(page.page_no)  # type: ignore
            if page._backend is not None and page._backend.is_valid():
                page.size = page._backend.get_size()

        return page

    def _assemble_document(self, conv_res: ConversionResult) -> ConversionResult:
        all_elements = []
        all_headers = []
        all_body = []

        with TimeRecorder(conv_res, "doc_assemble", scope=ProfilingScope.DOCUMENT):
            for p in conv_res.pages:
                if p.assembled is not None:
                    for el in p.assembled.body:
                        all_body.append(el)
                    for el in p.assembled.headers:
                        all_headers.append(el)
                    for el in p.assembled.elements:
                        all_elements.append(el)

            conv_res.assembled = AssembledUnit(
                elements=all_elements, headers=all_headers, body=all_body
            )

            conv_res.document = self.reading_order_model(conv_res)

            # TOC 추출 및 적용 (data_enrichment_options 사용)
            if self.pipeline_options.data_enrichment_options.do_toc_enrichment and conv_res.document:
                self._apply_toc_enrichment(conv_res)

            # Generate page images in the output
            if self.pipeline_options.generate_page_images:
                for page in conv_res.pages:
                    assert page.image is not None
                    page_no = page.page_no + 1
                    conv_res.document.pages[page_no].image = ImageRef.from_pil(
                        page.image, dpi=int(72 * self.pipeline_options.images_scale)
                    )

            # Generate images of the requested element types
            with warnings.catch_warnings():  # deprecated generate_table_images
                warnings.filterwarnings("ignore", category=DeprecationWarning)
                if (
                    self.pipeline_options.generate_picture_images
                    or self.pipeline_options.generate_table_images
                ):
                    scale = self.pipeline_options.images_scale
                    for element, _level in conv_res.document.iterate_items():
                        if not isinstance(element, DocItem) or len(element.prov) == 0:
                            continue
                        if (
                            isinstance(element, PictureItem)
                            and self.pipeline_options.generate_picture_images
                        ) or (
                            isinstance(element, TableItem)
                            and self.pipeline_options.generate_table_images
                        ):
                            page_ix = element.prov[0].page_no - 1
                            page = next(
                                (p for p in conv_res.pages if p.page_no == page_ix),
                                cast("Page", None),
                            )
                            assert page is not None
                            assert page.size is not None
                            assert page.image is not None

                            crop_bbox = (
                                element.prov[0]
                                .bbox.scaled(scale=scale)
                                .to_top_left_origin(
                                    page_height=page.size.height * scale
                                )
                            )

                            cropped_im = page.image.crop(crop_bbox.as_tuple())
                            element.image = ImageRef.from_pil(
                                cropped_im, dpi=int(72 * scale)
                            )

            # Aggregate confidence values for document:
            if len(conv_res.pages) > 0:
                with warnings.catch_warnings():
                    warnings.filterwarnings(
                        "ignore",
                        category=RuntimeWarning,
                        message="Mean of empty slice|All-NaN slice encountered",
                    )
                    conv_res.confidence.layout_score = float(
                        np.nanmean(
                            [c.layout_score for c in conv_res.confidence.pages.values()]
                        )
                    )
                    conv_res.confidence.parse_score = float(
                        np.nanquantile(
                            [c.parse_score for c in conv_res.confidence.pages.values()],
                            q=0.1,  # parse score should relate to worst 10% of pages.
                        )
                    )
                    conv_res.confidence.table_score = float(
                        np.nanmean(
                            [c.table_score for c in conv_res.confidence.pages.values()]
                        )
                    )
                    conv_res.confidence.ocr_score = float(
                        np.nanmean(
                            [c.ocr_score for c in conv_res.confidence.pages.values()]
                        )
                    )

            # 메타데이터 추출 및 key_value_items에 추가 (data_enrichment_options 사용)
            if self.pipeline_options.data_enrichment_options.extract_metadata and conv_res.document:
                temp_content = ""
                total_pages = len(conv_res.document.pages)
                for page in range(1, min(3, total_pages+1)):
                    temp_content += conv_res.document.export_to_markdown(page_no=page)
                metadata = self.extract_document_metadata(temp_content)
                if metadata:
                    _log.info(f"추출된 메타데이터: {json.dumps(metadata, ensure_ascii=False, indent=2)}")
                    
                    # KeyValueItem 생성을 위한 GraphData 구성
                    graph_cells = []
                    cell_id = 0
                    
                    # 메타데이터 딕셔너리를 그대로 key-value로 변환
                    for key, value in metadata.items():
                        graph_cells.append(GraphCell(
                            label=GraphCellLabel.KEY,
                            cell_id=cell_id,
                            text=key,
                            orig=key
                        ))
                        cell_id += 1
                        
                        graph_cells.append(GraphCell(
                            label=GraphCellLabel.VALUE,
                            cell_id=cell_id,
                            text=json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value),
                            orig=json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
                        ))
                        cell_id += 1
                    
                    # GraphData 생성
                    graph_data = GraphData(cells=graph_cells, links=[])
                    
                    # KeyValueItem을 문서에 추가
                    conv_res.document.add_key_values(
                        graph=graph_data,
                        prov=None,
                        parent=None
                    )

        return conv_res

    def _apply_toc_enrichment(self, conv_res: ConversionResult):
        """TOC 추출 및 SectionHeader 적용"""
        try:
            _log.info("TOC 추출 시작...")
            
            # 모든 SectionHeaderItem을 TextItem으로 변환
            self._convert_section_headers_to_text(conv_res.document)
            
            # 원시 텍스트 추출
            raw_text = self._extract_raw_text_for_toc(conv_res.document)
            
            # 사용자 정의 프롬프트 가져오기
            enrichment_options = self.pipeline_options.data_enrichment_options
            custom_system = enrichment_options.toc_system_prompt
            custom_user = enrichment_options.toc_user_prompt
            
            # AI로 목차 생성 (프롬프트 매니저 사용)
            toc_content = self.prompt_manager.call_ai_model(
                category="toc_extraction",
                prompt_type="korean_document",
                custom_system=custom_system,
                custom_user=custom_user,
                raw_text=raw_text
            )
            
            if toc_content:
                # 목차를 기반으로 SectionHeader 적용
                matched_count = self._apply_toc_to_document(conv_res.document, toc_content)
                _log.info(f"TOC 추출 완료 - {matched_count}개 섹션 헤더 생성")
            else:
                _log.warning("TOC 생성 실패")
            
        except Exception as e:
            _log.error(f"TOC 추출 중 오류 발생: {str(e)}")

    def _convert_section_headers_to_text(self, document):
        """문서의 모든 SectionHeaderItem을 TextItem으로 변환"""
        new_texts = []
        
        for item in document.texts:
            if isinstance(item, SectionHeaderItem):
                new_item = TextItem(
                    self_ref=item.self_ref,
                    parent=item.parent,
                    children=item.children,
                    content_layer=item.content_layer,
                    label=DocItemLabel.TEXT,
                    prov=item.prov,
                    orig=item.orig,
                    text=item.text,
                    formatting=item.formatting,
                    hyperlink=getattr(item, 'hyperlink', None)
                )
                new_texts.append(new_item)
            else:
                new_texts.append(item)
        
        document.texts = new_texts

    def _extract_raw_text_for_toc(self, document):
        """문서에서 원시 텍스트 추출"""
        raw_texts = ""
        for text in document.texts:
            cleaned_text = re.sub(r'\s+', ' ', text.text.strip())
            raw_texts += cleaned_text + "\n"
        return raw_texts

    def _parse_toc_content(self, toc_content: str):
        """목차 내용을 파싱해서 구조화된 데이터로 변환"""
        toc_items = []
        lines = toc_content.split('\n')
        
        for line in lines:
            cleaned_line = line.strip()
            if not cleaned_line:
                continue
            
            # 숫자 패턴들 (레벨별 매칭)
            patterns = [
                r'^(\d+\.\d+\.\d+\.\d+)\.\s*(.+)$',   # 1.1.1.1. 제목 (4단계)
                r'^(\d+\.\d+\.\d+)\.\s*(.+)$',        # 1.1.1. 제목 (3단계)
                r'^(\d+\.\d+)\.\s*(.+)$',              # 1.1. 제목 (2단계)
                r'^(\d+)\.\s*(.+)$',                    # 1. 제목 (1단계)
            ]
            
            matched = False
            for pattern in patterns:
                match = re.match(pattern, cleaned_line)
                if match:
                    number_part = match.group(1)
                    title_part = match.group(2).strip()
                    level = number_part.count('.') + 1
                    
                    toc_items.append({
                        'number': number_part,
                        'title': title_part,
                        'level': level,
                        'full_text': cleaned_line
                    })
                    matched = True
                    break
            
            if not matched and cleaned_line:
                # 패턴에 맞지 않는 줄은 레벨 1로 처리
                toc_items.append({
                    'number': '',
                    'title': cleaned_line,
                    'level': 1,
                    'full_text': cleaned_line
                })
        
        return toc_items

    def _apply_toc_to_document(self, document, toc_content: str, threshold: float = 0.7):
        """생성된 목차를 기반으로 문서에 SectionHeader 적용"""
        # 목차 파싱
        toc_items = self._parse_toc_content(toc_content)
        
        converted_indices = set()
        
        # 텍스트 아이템들 준비
        text_items = [
            (i, item.text.strip()) 
            for i, item in enumerate(document.texts)
            if (isinstance(item, TextItem) and 
                item.label == DocItemLabel.TEXT and 
                len(item.text.strip()) >= 2)
        ]
        
        matched_count = 0
        
        # 각 목차 항목을 문서의 텍스트와 매칭
        for toc_item in toc_items:
            toc_clean = toc_item['title']
            target_level = toc_item['level']
            
            if len(toc_clean) < 2:
                continue
            
            # difflib을 사용한 유사 텍스트 찾기
            text_only = [text for _, text in text_items]
            close_matches = difflib.get_close_matches(
                toc_clean, text_only, n=3, cutoff=0.3
            )
            
            if close_matches:
                best_match_text = close_matches[0]
                best_match_idx = next(
                    (idx for idx, text in text_items if text == best_match_text), 
                    None
                )
                
                if best_match_idx is not None and best_match_idx not in converted_indices:
                    similarity = difflib.SequenceMatcher(
                        None, toc_clean.lower(), best_match_text.lower()
                    ).ratio()
                    
                    if similarity >= threshold:
                        # TextItem을 SectionHeaderItem으로 변환
                        original_item = document.texts[best_match_idx]
                        section_header = SectionHeaderItem(
                            self_ref=original_item.self_ref,
                            parent=original_item.parent,
                            children=original_item.children,
                            content_layer=original_item.content_layer,
                            prov=original_item.prov,
                            orig=original_item.orig,
                            text=original_item.text,
                            formatting=original_item.formatting,
                            hyperlink=getattr(original_item, 'hyperlink', None),
                            level=target_level
                        )
                        document.texts[best_match_idx] = section_header
                        converted_indices.add(best_match_idx)
                        matched_count += 1
        
        return matched_count

    def extract_document_metadata(self, document_content, model=None, seed=None):
        """
        문서 내용에서 작성일과 작성자 정보를 추출하는 함수 (프롬프트 매니저 사용)
        
        Args:
            document_content (str): 문서 내용
            model (str, optional): 사용할 모델 이름 (프롬프트 설정에서 가져옴)
            seed (int, optional): 재현성을 위한 시드값 (프롬프트 설정에서 가져옴)
            
        Returns:
            dict: 추출된 메타데이터 딕셔너리 (작성일, 작성자 정보)
        """
        if not self.pipeline_options.data_enrichment_options.extract_metadata:
            return None
        
        try:
            # 사용자 정의 프롬프트 가져오기
            enrichment_options = self.pipeline_options.data_enrichment_options
            custom_system = enrichment_options.metadata_system_prompt
            custom_user = enrichment_options.metadata_user_prompt
            
            # 프롬프트 매니저를 사용하여 AI 모델 호출
            response = self.prompt_manager.call_ai_model(
                category="metadata_extraction",
                prompt_type="korean_financial",
                custom_system=custom_system,
                custom_user=custom_user,
                document_content=document_content
            )
            
            if not response:
                return {"작성일": None, "작성자": []}
            
            # JSON 찾기
            json_pattern = r'```json\s*([\s\S]*?)\s*```'
            match = re.search(json_pattern, response)
            
            if match:
                try:
                    metadata = json.loads(match.group(1))
                    return metadata
                except:
                    return {"작성일": None, "작성자": []}
            else:
                try:
                    # JSON 블록이 없는 경우 전체 응답을 JSON으로 파싱 시도
                    return json.loads(response)
                except:
                    return {"작성일": None, "작성자": []}
                    
        except Exception as e:
            _log.error(f"메타데이터 추출 중 오류 발생: {str(e)}")
            return {"작성일": None, "작성자": []}

    @classmethod
    def get_default_options(cls) -> PdfPipelineOptions:
        return PdfPipelineOptions()

    @classmethod
    def is_backend_supported(cls, backend: AbstractDocumentBackend):
        return isinstance(backend, PdfDocumentBackend)
