"""GenOS Vector Metadata Models"""

import json
from datetime import datetime
from pydantic import BaseModel
from docling_core.types.doc import PictureItem
from docling_core.types import DoclingDocument


class GenOSVectorMeta(BaseModel):
    """Vector metadata model for GenOS"""
    class Config:
        extra = 'allow'
    
    text: str | None = None
    n_char: int | None = None  # preprocess.py 기준
    n_word: int | None = None  # preprocess.py 기준
    n_line: int | None = None  # preprocess.py 기준
    i_page: int | None = None
    e_page: int | None = None
    i_chunk_on_page: int | None = None
    n_chunk_of_page: int | None = None  # preprocess.py 기준
    i_chunk_on_doc: int | None = None
    n_chunk_of_doc: int | None = None
    n_page: int | None = None  # preprocess.py 기준
    reg_date: str | None = None
    chunk_bboxes: str | None = None
    media_files: str | None = None


class GenOSVectorMetaBuilder:
    """Builder pattern for GenOSVectorMeta"""
    
    def __init__(self):
        """빌더 초기화 - 기본값 설정"""
        self.text: str = ""  # 빈 문자열 기본값
        self.n_char: int = 0
        self.n_word: int = 0
        self.n_line: int = 0
        self.i_page: int = 0
        self.e_page: int = 0  # 기본값 0
        self.i_chunk_on_page: int = 0
        self.n_chunk_of_page: int = 1  # 최소 1개
        self.i_chunk_on_doc: int = 0
        self.n_chunk_of_doc: int = 1  # 최소 1개
        self.n_page: int = 1  # 최소 1페이지
        self.reg_date: str = datetime.now().isoformat(timespec='seconds') + 'Z'
        self.chunk_bboxes: str = "[]"  # 빈 JSON 배열
        self.media_files: str = "[]"  # 빈 JSON 배열

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
            if hasattr(self, key) and value is not None:  # None이 아닌 경우만 설정
                setattr(self, key, value)
        return self

    def set_chunk_bboxes(self, doc_items: list, document: DoclingDocument) -> "GenOSVectorMetaBuilder":
        chunk_bboxes = []
        for item in doc_items:
            for prov in item.prov:
                label = item.self_ref
                type_ = item.label
                size = document.pages.get(prov.page_no).size
                page_no = prov.page_no
                bbox = prov.bbox
                bbox_data = {
                    'l': bbox.l / size.width,
                    't': bbox.t / size.height,
                    'r': bbox.r / size.width,
                    'b': bbox.b / size.height,
                    'coord_origin': bbox.coord_origin.value
                }
                chunk_bboxes.append({
                    'page': page_no, 
                    'bbox': bbox_data, 
                    'type': type_, 
                    'ref': label
                })
        self.e_page = max([bbox['page'] for bbox in chunk_bboxes]) if chunk_bboxes else self.i_page
        self.chunk_bboxes = json.dumps(chunk_bboxes) if chunk_bboxes else "[]"
        return self

    def set_media_files(self, doc_items: list) -> "GenOSVectorMetaBuilder":
        temp_list = []
        for item in doc_items:
            if isinstance(item, PictureItem):
                path = str(item.image.uri)
                name = path.rsplit("/", 1)[-1]
                temp_list.append({'name': name, 'type': 'image', 'ref': item.self_ref})
        self.media_files = json.dumps(temp_list) if temp_list else "[]"
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
            media_files=self.media_files
        )