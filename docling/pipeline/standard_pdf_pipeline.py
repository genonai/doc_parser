import logging
import warnings
from pathlib import Path
from typing import Optional, cast

import numpy as np
from docling_core.types.doc import DocItem, ImageRef, PictureItem, TableItem

from docling.backend.abstract_backend import AbstractDocumentBackend
from docling.backend.pdf_backend import PdfDocumentBackend
from docling.datamodel.base_models import AssembledUnit, Page
from docling.datamodel.document import ConversionResult
from docling.datamodel.layout_model_specs import LayoutModelConfig
from docling.datamodel.pipeline_options import PdfPipelineOptions
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

        return conv_res

    @classmethod
    def get_default_options(cls) -> PdfPipelineOptions:
        return PdfPipelineOptions()

    @classmethod
    def is_backend_supported(cls, backend: AbstractDocumentBackend):
        return isinstance(backend, PdfDocumentBackend)

    def extract_document_metadata(self, document_content, model="google/gemma-3-12b-it:free", seed=3):
        """
        문서 내용에서 작성일과 작성자 정보를 추출하는 함수
        
        Args:
            document_content (str): 문서 내용
            model (str): 사용할 모델 이름
            seed (int): 재현성을 위한 시드값
            
        Returns:
            dict: 추출된 메타데이터 딕셔너리 (작성일, 작성자 정보)
        """
        if not self.pipeline_options.data_enrichment:
            return None
        
        # API 키 직접 설정
        api_key = "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"
        
        # OpenAI 클라이언트 초기화
        client = OpenAI(base_url="https://openrouter.ai/api/v1", 
                        api_key=api_key)
        
        # 프롬프트 구성
        messages = [
            {
                "role": "system",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "You are a professional document extraction assistant. "
                            "Your job is to carefully extract metadata from semi-structured or unstructured Korean financial documents. "
                            "Always follow the requested output format exactly."
                        )
                    }
                ]
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "다음은 한국어로 작성된 금융 보고서 문서의 일부입니다. 이 문서에서 아래 정보를 정확히 추출해주세요:\n\n"
                            "1. 최초 작성일 (날짜가 여러 개 있으면 '보고자료' 또는 '회의자료'에 가까운 날짜 사용)\n"
                            "2. 작성자 이름과 소속 또는 직책 (예: 채권시장팀 조용범 과장)\n"
                            "3. 작성자 전화번호 (내선번호 포함 시 함께 출력)\n\n"
                            "문서:\n"
                            "---\n"
                            f"{document_content}\n"
                            "---\n\n"
                            "출력 형식은 반드시 아래와 같이 맞춰주세요 (JSON 형식):\n\n"
                            "{\n"
                            '  "작성일": "YYYY-MM-DD",\n'
                            '  "작성자": [\n'
                            '    {"이름": "조용범", "소속": "채권시장팀", "직책": "과장", "전화": "4751"},\n'
                            '    {"이름": "임인혁", "소속": "주식시장팀", "직책": "과장", "전화": "4672"}\n'
                            "  ]\n"
                            "}"
                        )
                    }
                ]
            }
        ]
        try:
            # API 요청
            completion = client.chat.completions.create(
                model=model,
                messages=messages,
                seed=seed
            )
            # 응답에서 JSON 추출
            response = completion.choices[0].message.content
            
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

    def _enrich_document(self, conv_res: ConversionResult) -> ConversionResult:
        
        # data_enrichment가 활성화된 경우에만 메타데이터 추출
        if self.pipeline_options.data_enrichment and conv_res.document:
            temp_content = ""
            # 페이지 수 확인
            total_pages = len(conv_res.document.pages)
            # 최대 2페이지까지만 처리 (페이지가 부족하면 있는 만큼만)
            for page in range(1, min(3, total_pages+1)):
                print("page", page)
                temp_content += conv_res.document.export_to_markdown(page_no=page)
            metadata = self.extract_document_metadata(temp_content)
            if metadata:
                # 추출된 메타데이터를 결과 객체에 저장
                conv_res.metadata = metadata
        return conv_res