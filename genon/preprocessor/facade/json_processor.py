from datetime import datetime
from typing import Optional, Iterable, Any, List, Dict, Tuple
from collections import defaultdict
from fastapi import Request
from pydantic import BaseModel, ConfigDict
from collections import Counter

import re
import asyncio
import json
import ast
import pdb

import pandas as pd

from docling_core.types.doc import (
    BoundingBox,
    #CoordOrigin,
    DocItemLabel,
    DoclingDocument,
    DocumentOrigin,
    GroupLabel,
    #ImageRef,
    #ProvenanceItem,
    #Size,
    #TableCell,
    #TableData,
    #GroupItem,
    DocItem,
    PictureItem,
    SectionHeaderItem,
    TableItem,
    TextItem,
    PageItem
)

from docling.document_converter import DocumentConverter, PdfFormatOption, HTMLFormatOption
from docling.datamodel.document import ConversionResult, InputDocument
from docling_core.types import DoclingDocument

KV_MAP = {
    "url": ["URL"],
    "ins_date": [
        "입력일", # 경상오더
        "발행일자", # TM
        ],
    "title": [
        "오더제목", # 경상오더
        "고장내용", # 발전정지
        "TM제목", # TM
        ],
    "num": [
        "오더번호", # 경상오더
        "번호", # 발전정지
        "TM번호", # TM
        ],
    "Powersys": ["발전소"], # 발전정지
    "desman": ["설계자"], # 경상오더
    "desdept": ["설계부서"], # 경상오더, TM
    "hogi": ["호기"],
    "des_date": ["설계일"],
    "stopcat": ["정지종별"], # 발전정지
    "stopcat_code": ["정지종별코드"], # 발전정지
    "parcat": ["대분류"], # 발전정지
    "cat": ["분류"], # 발전정지
    "event_date": ["발생일시"], # 발전정지
    "rec_date": ["복구일시"], # 발전정지
    "pubman": ["발행자"],
    "pubdept": ["발행부서"],
    "status": ["진행상태"]
}

class GenOSVectorMeta(BaseModel):
    model_config = ConfigDict(extra="allow")

class GenOSVectorMetaBuilder:
    def __init__(self):
        """빌더 초기화"""
        self.text: Optional[str] = None
        self.n_char: Optional[int] = None
        self.n_word: Optional[int] = None
        self.n_line: Optional[int] = None
        self.i_page: Optional[int] = None
        self.i_chunk_on_page: Optional[int] = None
        self.n_chunk_of_page: Optional[int] = None
        self.i_chunk_on_doc: Optional[int] = None
        self.n_chunk_of_doc: Optional[int] = None
        self.n_page: Optional[int] = None
        self.reg_date: Optional[str] = None
        self.bboxes: Optional[str] = None
        self.url: Optional[str] = None

        self.data = {"text": None, 
                    "n_char": None,
                    "n_line": None,
                    "i_page": None,
                    "i_chunk_on_page": None,
                    "n_chunk_of_page": None,
                    "i_chunk_on_doc": None,
                    "n_chunk_of_doc": None,
                    "n_page": None,
                    "reg_date": None,
                    "bboxes": None,
                    "url": None
                    }

    def set_text(self, text: str) -> "GenOSVectorMetaBuilder":
        """텍스트와 관련된 데이터를 설정"""
        
        self.text = text
        self.n_char = len(text)
        self.n_word = len(text.split())
        self.n_line = len(text.splitlines())

        self.data["text"] = text
        self.data["n_char"] = len(text)
        self.data["n_word"] = len(text.split())
        self.data["n_line"] = len(text.splitlines())

        return self

    def set_page_info(
            self, i_page: int, i_chunk_on_page: int, n_chunk_of_page: int
    ) -> "GenOSVectorMetaBuilder":
        """페이지 정보 설정"""
        self.i_page = i_page
        self.i_chunk_on_page = i_chunk_on_page
        self.n_chunk_of_page = n_chunk_of_page

        self.data["i_page"] = i_page
        self.data["i_chunk_on_page"] = i_chunk_on_page
        self.data["n_chunk_of_page"] = n_chunk_of_page

        return self

    def set_chunk_index(self, i_chunk_on_doc: int) -> "GenOSVectorMetaBuilder":
        """문서 전체의 청크 인덱스 설정"""
        self.i_chunk_on_doc = i_chunk_on_doc

        self.data["i_chunk_on_doc"] = i_chunk_on_doc

        return self

    def set_bboxes(self, bbox: BoundingBox) -> "GenOSVectorMetaBuilder":
        """Bounding Boxes 정보 설정"""
        #         bboxes.append({
        #             'p1': {'x': rect[0] / fitz_page.rect.width, 'y': rect[1] / fitz_page.rect.height},
        #             'p2': {'x': rect[2] / fitz_page.rect.width, 'y': rect[3] / fitz_page.rect.height},
        #         })
        # NOTE: docling은 BOTTOMLEFT인데 해당 좌표 그대로 활용되는지 ?
        conv = []
        conv.append({
            'p1': {'x': 0, 'y': 0},
            'p2': {'x': 0, 'y': 0},
        })
        self.bboxes = json.dumps(conv)

        self.data["bboxes"] = json.dumps(conv)

        return self

    def set_global_metadata(self, **global_metadata) -> "GenOSVectorMetaBuilder":
        """글로벌 메타데이터 병합"""

        for key, value in global_metadata.items():
            setattr(self, key, value)
            self.data[key] = value
            
                
        return self

    def build(self) -> GenOSVectorMeta:
        """설정된 데이터를 사용해 최종적으로 GenOSVectorMeta 객체 생성"""
        return GenOSVectorMeta(text=self.data.pop("text", "ERROR: no text"), **self.data)

class DocumentProcessor:
    def __init__(self):
        '''
        initialize Document Converter
        '''
        self.page_chunk_counts = defaultdict(int)
        # device = AcceleratorDevice.AUTO
        num_threads = 4

    def preprocess_json(self, jsonf):

        metadata_keys = []
        date_keys = []
        
        for jsonf_k, _ in jsonf.items():
            for k, v in KV_MAP.items():
                if jsonf_k in v:
                    metadata_keys.append(jsonf_k)
                    if "date" in k:
                        date_keys.append(jsonf_k)
                    
        # date처리, json확인해보고 빼도됨. 
        for k in date_keys:
            if k in jsonf:
                try: 
                    # jsonf[k] = pdf.to_datetime(jsonf[k], errors='coerce').isoformat()
                    date_value = jsonf[k]
                    if not date_value:
                      date_value = 0
                    
                    if date_value:
                      dt_obj = self._parse_date_string(str(date_value))
                      
                      if dt_obj:
                        jsonf[k] = int(dt_obj.strftime("%Y%m%d"))
                      else:
                        jsonf[k] = ""
                    else:
                      jsonf[k] = ""
                except Exception:
                    pass
        
        # nan 처리
        for key in jsonf.keys():
            try: 
                if not isinstance(jsonf[key], list) and pd.isna(jsonf[key]):
                    jsonf[key] = ""
            except:
                pass

        # 메타데이터 처리
        metadata = {key: jsonf[key] for key in metadata_keys if key in jsonf}

        # formatted text 생성 
        formatted_text = "\n ".join([f"{key} : {str(jsonf[key])}" for key in jsonf if not key.startswith('Unnamed')])

        metadata_key_list = list(metadata.keys())
        for k, v_list in KV_MAP.items():
            for metadata_key in metadata_key_list:
                if metadata_key in v_list:
                    if k in metadata.keys():
                        print(f"@@@@ 이미 있는 키: {metadata_key} ---X-->> {k}")
                        print(f"@@@@ 있는 값: {k} : {metadata[k]}")
                        metadata.pop(metadata_key)
                    else:
                        metadata[k] = metadata.pop(metadata_key)
                    
        chunk = {
            "id": 1,
            "text": formatted_text,
            "metadata": metadata
        }

        return chunk
       

    def load_documents(self, file_path: str):        
        with open(file_path, 'r', encoding='utf-8') as f:
            jsonfile = json.load(f)

        return jsonfile
    
    def _parse_date_string(self, date_str:str)-> Optional[datetime]:
      formats = [
        "%Y-%m-%d",
        "%Y%m%d",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%d %H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%fZ"
        ]
        
      if not date_str or date_str.strip() == "":
        return 0
      for fmt in formats:
        try:
          return datetime.strptime(date_str, fmt)
        except ValueError:
          continue
        
      return 0

    
    # def split_documents(self, documents: dict, **kwargs: dict) -> List[Dict]:
    def split_documents(self, documents: dict, **kwargs: dict) -> Dict:
        chunk_size = 1000
        text = documents.get("text", "error")
        chunks = []
        chunk = ""

        words = text.split(" ")

        for word in words:
            if len(chunk) + len(word) > chunk_size:
                chunks.append(chunk)
                chunk = word
            else:
                chunk += (" " + word) if chunk else word
        
        if chunk:
            chunks.append(chunk)
                
        new_chunks = []
        for chunk in chunks:
            documents['text'] = chunk
            new_chunks.append(documents.copy())

        return new_chunks

    
    def compose_vectors(self, chunks: list[dict], file_path: str) -> \
            list[dict]:
        
        first_chunk = chunks[0]
        
        global_metadata = dict(
            n_chunk_of_doc=int(1),
            n_page=int(1),
            reg_date=datetime.now().isoformat(timespec='seconds') + 'Z',
            **first_chunk['metadata'],
        )

        current_page = 1
        chunk_index_on_page = 0

        vectors = []
        for chunk in chunks:
            vector = (GenOSVectorMetaBuilder()
                        .set_text(chunk["text"])
                        .set_page_info(1, 1, 1)
                        .set_chunk_index(1)
                        .set_global_metadata(**global_metadata)
                        .set_bboxes(None)
                        ).build()
            vectors.append(vector)

        return vectors

    async def __call__(self, request: Request, file_path: str, **kwargs): # request: Request

        file: dict = self.load_documents(file_path)
        
        document: dict = self.preprocess_json(file)
        
        chunks: list[dict] = self.split_documents(document, **kwargs)

        vectors: list[dict] = self.compose_vectors(chunks=chunks, file_path= file_path)

        return vectors