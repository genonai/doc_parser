from __future__ import annotations

import json
import os
import warnings
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any
from collections import defaultdict

from fastapi import Request
import asyncio

# Docling imports for image processing
from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend
from docling.backend.docling_parse_v4_backend import DoclingParseV4DocumentBackend
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import (
    AcceleratorDevice,
    AcceleratorOptions,
    PdfPipelineOptions,
    TableFormerMode,
    TesseractOcrOptions,
    PipelineOptions
)
from docling.document_converter import DocumentConverter, PdfFormatOption, FormatOption
from docling.pipeline.standard_pdf_pipeline import StandardPdfPipeline
from docling.datamodel.document import ConversionResult
from docling_core.types.doc.document import DoclingDocument, DocItem, PictureItem, TextItem, ProvenanceItem
from docling_core.types.doc.base import BoundingBox
from docling_core.types.doc.labels import DocItemLabel
from docling_core.transforms.chunker.base import BaseChunk
from docling_core.transforms.chunker.hierarchical_chunker import DocChunk, DocMeta

# Pydantic imports for vector metadata
from pydantic import BaseModel


class GenOSVectorMeta(BaseModel):
    """벡터 메타데이터 모델"""
    class Config:
        extra = 'allow'

    text: Optional[str] = None
    n_char: Optional[int] = None
    n_word: Optional[int] = None
    n_line: Optional[int] = None
    e_page: Optional[int] = None
    i_page: Optional[int] = None
    i_chunk_on_page: Optional[int] = None
    n_chunk_of_page: Optional[int] = None
    i_chunk_on_doc: Optional[int] = None
    n_chunk_of_doc: Optional[int] = None
    n_page: Optional[int] = None
    reg_date: Optional[str] = None
    chunk_bboxes: Optional[str] = None
    media_files: Optional[str] = None


class GenOSVectorMetaBuilder:
    """벡터 메타데이터 빌더"""
    def __init__(self):
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

    def set_text(self, text: str) -> "GenOSVectorMetaBuilder":
        """텍스트와 관련된 데이터를 설정"""
        self.text = text
        self.n_char = len(text)
        self.n_word = len(text.split())
        self.n_line = len(text.splitlines())
        return self

    def set_page_info(self, i_page: int, i_chunk_on_page: int, n_chunk_of_page: int) -> "GenOSVectorMetaBuilder":
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
        """바운딩박스 정보 설정"""
        chunk_bboxes = []
        for item in doc_items:
            for prov in item.prov:
                label = item.self_ref
                type_ = item.label
                page = document.pages.get(prov.page_no)
                if page is None:
                    continue
                size = page.size
                page_no = prov.page_no
                bbox = prov.bbox
                bbox_data = {
                    'l': bbox.l / size.width,
                    't': bbox.t / size.height,
                    'r': bbox.r / size.width,
                    'b': bbox.b / size.height,
                    'coord_origin': bbox.coord_origin.value
                }
                chunk_bboxes.append({'page': page_no, 'bbox': bbox_data, 'type': type_, 'ref': label})
        self.e_page = max([bbox['page'] for bbox in chunk_bboxes]) if chunk_bboxes else None
        self.chunk_bboxes = json.dumps(chunk_bboxes)
        return self

    def set_media_files(self, doc_items: list) -> "GenOSVectorMetaBuilder":
        """미디어 파일 정보 설정"""
        temp_list = []
        for item in doc_items:
            if isinstance(item, PictureItem):
                # image 속성이 None인 경우 처리
                if item.image is not None and item.image.uri is not None:
                    path = str(item.image.uri)
                    name = path.rsplit("/", 1)[-1]
                    temp_list.append({'name': name, 'type': 'image', 'ref': item.self_ref})
                else:
                    # image 정보가 없는 경우 self_ref를 기반으로 처리
                    ref_name = item.self_ref.split('/')[-1] if item.self_ref else 'unknown_image'
                    temp_list.append({'name': f'{ref_name}.png', 'type': 'image', 'ref': item.self_ref})
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
        )


class GenosServiceException(Exception):
    """GenOS 서비스 예외"""
    def __init__(self, error_code: str, error_msg: Optional[str] = None, msg_params: Optional[dict] = None) -> None:
        self.code = 1
        self.error_code = error_code
        self.error_msg = error_msg or "GenOS Service Exception"
        self.msg_params = msg_params or {}

    def __repr__(self) -> str:
        class_name = self.__class__.__name__
        return f"{class_name}(code={self.code!r}, errMsg={self.error_msg!r})"


class DocumentProcessor:
    """이미지 파일 전용 전처리기 - 직접 이미지 처리로 OCR 텍스트 추출 및 벡터 생성"""
    
    def __init__(self):
        """이미지 처리를 위한 DocumentConverter 초기화"""
        self.page_chunk_counts = defaultdict(int)
        
        
        # GPU/CPU 가속 설정
        device = AcceleratorDevice.AUTO
        num_threads = 8
        accelerator_options = AcceleratorOptions(num_threads=num_threads, device=device)
        
        self.pipe_line_options = PdfPipelineOptions()
        self.pipe_line_options.generate_page_images = True
        self.pipe_line_options.generate_picture_images = True
        self.pipe_line_options.do_ocr = True
        self.pipe_line_options.ocr_options.lang = ["ko", 'en']
        self.pipe_line_options.ocr_options.model_storage_directory = "./.EasyOCR/model"
        self.pipe_line_options.ocr_options.force_full_page_ocr = True
        # ocr_options = TesseractOcrOptions()
        # ocr_options.lang = ['kor', 'kor_vert', 'eng', 'jpn', 'jpn_vert']
        # ocr_options.path = './.tesseract/tessdata'
        # self.pipe_line_options.ocr_options = ocr_options
        # self.pipe_line_options.artifacts_path = Path("/nfs-root/models/223/760")  # Path("/nfs-root/aiModel/.cache/huggingface/hub/models--ds4sd--docling-models/snapshots/4659a7d29247f9f7a94102e1f313dad8e8c8f2f6/")
        
        # paddle ocr 옵션 설정
        # ocr_options = PaddleOcrOptions(
        #     force_full_page_ocr=True,
        #     lang=['korean'],
        #     text_score=0.3)
        # self.pipe_line_options.ocr_options = ocr_options
        # ------------------------------
        self.pipe_line_options.do_table_structure = True
        self.pipe_line_options.images_scale = 2
        self.pipe_line_options.table_structure_options.do_cell_matching = True
        self.pipe_line_options.table_structure_options.mode = TableFormerMode.ACCURATE
        self.pipe_line_options.accelerator_options = accelerator_options

        
        # 이미지 직접 처리용 컨버터 생성 
        self.converter = DocumentConverter(
            format_options={
                InputFormat.IMAGE: FormatOption(
                    pipeline_cls=StandardPdfPipeline,
                    pipeline_options=self.pipe_line_options,
                    backend=DoclingParseV4DocumentBackend
                ),
            }
        )
    
    def load_image_document(self, image_path: str, **kwargs) -> DoclingDocument:
        """이미지 파일을 직접 DoclingDocument로 로드 및 OCR 처리"""
        try:
            conv_result: ConversionResult = self.converter.convert(image_path, raises_on_error=True)
            document = conv_result.document
            
            return document
                
        except Exception as e:
            print(f"[ImageProcessor] 이미지 로드 실패: {e}")
            raise Exception(f"이미지 처리 실패: {str(e)}")
    
    def extract_text_from_image_document(self, document: DoclingDocument) -> str:
        """DoclingDocument에서 OCR로 추출된 텍스트 수집"""
        extracted_texts = []
        
        # 모든 텍스트 아이템에서 텍스트 추출
        for item, _ in document.iterate_items():
            if isinstance(item, TextItem) and item.text and item.text.strip():
                # 페이지 헤더/푸터 제외
                if item.label not in [DocItemLabel.PAGE_HEADER, DocItemLabel.PAGE_FOOTER]:
                    extracted_texts.append(item.text.strip())
        
        # 추출된 텍스트가 없으면 기본 텍스트 반환
        if not extracted_texts:
            return "추출된 텍스트가 없습니다."
        
        return "\n".join(extracted_texts)
    
    def create_image_chunks(self, document: DoclingDocument, image_path: str) -> List[DocChunk]:
        """이미지 문서를 청크로 분할"""
        # OCR로 추출된 텍스트 가져오기
        extracted_text = self.extract_text_from_image_document(document)
        
        
        # # 텍스트가 추출되지 않은 경우 기본 텍스트 사용
        # if not extracted_text or extracted_text.strip() == "":
        #     extracted_text = "."
        
        # 첫 번째 페이지의 기본 정보 사용
        page_no = 1
        prov = ProvenanceItem(
            page_no=page_no,
            bbox=BoundingBox(l=0, t=0, r=1, b=1),
            charspan=(0, len(extracted_text))
        )
        
        # 텍스트 아이템 생성
        text_item = document.add_text(
            label=DocItemLabel.TEXT,
            text=extracted_text,
            prov=prov
        )
        
        # 이미지 아이템도 추가 (있는 경우) - DocItem 타입으로 캐스팅
        doc_items: List[DocItem] = [text_item]
        for item, _ in document.iterate_items():
            if isinstance(item, PictureItem):
                # PictureItem을 DocItem으로 처리
                break
        
        # 단일 청크 생성
        chunk = DocChunk(
            text=f"{extracted_text}",
            meta=DocMeta(
                doc_items=doc_items,
                headings=None,
                captions=None,
                origin=document.origin,
            )
        )
        
        # 페이지별 청크 수 업데이트
        self.page_chunk_counts[page_no] = 1
        
        return [chunk]
    
    async def compose_vectors(self, document: DoclingDocument, chunks: List[DocChunk], 
                                  image_path: str, request: Request, **kwargs) -> List[dict]:
        """이미지 처리 결과를 벡터로 변환"""
        
        # 이미지 파일 정보
        image_name = os.path.basename(image_path)
        
        # 글로벌 메타데이터
        global_metadata = dict(
            n_chunk_of_doc=len(chunks),
            n_page=document.num_pages(),
            reg_date=datetime.now().isoformat(timespec='seconds') + 'Z',
        )
        current_page = None
        chunk_index_on_page = 0

        vectors = []
        for chunk_idx, chunk in enumerate(chunks):
            page = chunk.meta.doc_items[0].prov[0].page_no
            text = chunk.text

            if page != current_page:
                current_page = page
                chunk_index_on_page = 0

            vectors.append(GenOSVectorMeta.model_validate({
                'text': text,
                'n_char': len(text),
                'n_word': len(text.split()),
                'n_line': len(text.splitlines()),
                'i_page': page,
                'e_page': page,
                'i_chunk_on_page': chunk_index_on_page,
                'n_chunk_of_page': self.page_chunk_counts[page],
                'i_chunk_on_doc': chunk_idx,
                **global_metadata
            }))
        
        return vectors
    
    async def __call__(self, request: Request, file_path: str, **kwargs: dict):
        try:
            # 지원되는 이미지 확장자 확인
            supported_extensions = ['.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.gif']
            file_ext = Path(file_path).suffix.lower()
            if file_ext not in supported_extensions:
                raise GenosServiceException(1, f"지원되지 않는 이미지 형식: {file_ext}")

            # 1) 이미지 로드 (Docling 파이프라인으로 로드하되 OCR은 pipeline 옵션에 따름)
            document: DoclingDocument = self.load_image_document(file_path, **kwargs)

            # 2) 이미지 문서를 청크로 분할
            chunks: List[DocChunk] = self.create_image_chunks(document, file_path)
            if not chunks:
                raise GenosServiceException(1, "이미지에서 청크를 생성할 수 없습니다")

            # 3) 벡터 생성 (기존 compose_vectors 그대로 사용)
            vectors: list[dict] = await self.compose_vectors(document, chunks, file_path, request, **kwargs)
            if not vectors:
                raise GenosServiceException(1, "이미지 벡터 생성 실패")

            return vectors
        except GenosServiceException:
            raise
        except Exception as e:
            raise GenosServiceException(1, f"이미지 처리 실패: {str(e)}")
    
 


# GenOS 와의 의존성 제거를 위해 추가
async def assert_cancelled(request: Request):
    if await request.is_disconnected():
        raise GenosServiceException(1, f"Cancelled")