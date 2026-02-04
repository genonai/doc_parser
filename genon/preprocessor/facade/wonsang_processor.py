from __future__ import annotations

import json
import os
from pathlib import Path

from collections import defaultdict
from datetime import datetime
from typing import Optional, Iterable, Any, List, Dict, Tuple

from fastapi import Request

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
    PdfPipelineOptions,
    TableFormerMode,
    PipelineOptions,
    PaddleOcrOptions,
)

from docling.document_converter import (
    DocumentConverter,
    PdfFormatOption,
    FormatOption
)
from docling.datamodel.pipeline_options import DataEnrichmentOptions, PictureDescriptionApiOptions
from docling.utils.document_enrichment import enrich_document, check_document
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
    PageItem
)
from collections import Counter
import re
import json
import warnings
from typing import Iterable, Iterator, Optional, Union

from pydantic import BaseModel, ConfigDict, PositiveInt, TypeAdapter, model_validator
from typing_extensions import Self

try:
    import semchunk
    from transformers import AutoTokenizer, PreTrainedTokenizerBase
except ImportError:
    raise RuntimeError(
        "Module requires 'chunking' extra; to install, run: "
        "`pip install 'docling-core[chunking]'`"
    )

import requests
import httpx
import uuid
import shutil
import subprocess
import html as html_module

try:
    from langchain_community.document_loaders import PyMuPDFLoader
except ImportError:
    PyMuPDFLoader = None

# from genos_utils import upload_files

# ============================================
#
# Copyright IBM Corp. 2024 - 2024
# SPDX-License-Identifier: MIT
#

"""Chunker implementation leveraging the document structure."""

class HwpLoader:
    def __init__(self, file_path: str):
        self.file_path = file_path
        self.output_dir = os.path.join('/tmp', str(uuid.uuid4()))
        os.makedirs(self.output_dir, exist_ok=True)
        self.pdf_path = None  # PDF 변환 후 경로 저장

    def load(self):
        try:
            # 파일 헤더 확인
            with open(self.file_path, 'rb') as f:
                header = f.read(8)
            
            # HWPML 형식 확인 (<?xml로 시작)
            if header.startswith(b'<?xml'):
                # HWPML 형식: xmllint로 텍스트 추출
                body_txt_path = os.path.join(self.output_dir, 'body.txt')
                
                # xmllint 명령어 실행
                xmllint_cmd = f'xmllint --html --recover "{self.file_path}" 2>/dev/null'
                sed_cmd1 = "sed 's/<[^>]*>//g'"  # HTML 태그 제거
                python_cmd = "python3 -c 'import sys, html; [print(html.unescape(line), end=\"\") for line in sys.stdin]'"
                sed_cmd2 = "sed '/^[[:space:]]*$/d'"  # 빈 줄 제거
                sed_cmd3 = "sed 's/{[^}]*}//g'"  # {태그} 패턴 제거
                sed_cmd4 = "sed '/^[A-Za-z0-9+\\/]*={0,2}$/d'"  # base64 라인 제거 (한 줄이 base64만 있는 경우)
                
                full_cmd = f'{xmllint_cmd} | {sed_cmd1} | {python_cmd} | {sed_cmd2} | {sed_cmd3} | {sed_cmd4} > "{body_txt_path}"'
                
                result = subprocess.run(
                    full_cmd,
                    shell=True,
                    check=True,
                    timeout=600
                )
                
                # Python에서 추가 정리: {태그} 패턴과 base64 데이터 제거
                with open(body_txt_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                # {태그} 패턴 제거
                import re
                content = re.sub(r'\{[^}]*\}', '', content)
                
                # base64 이미지 데이터 제거 (긴 base64 문자열 패턴)
                # base64는 보통 매우 긴 문자열이고, 줄 끝에 = 또는 == 가 있을 수 있음
                lines = content.split('\n')
                cleaned_lines = []
                for line in lines:
                    line = line.strip()
                    # base64 라인 감지: 매우 긴 문자열이고 대부분 영숫자와 +/=로 구성
                    if len(line) > 50 and re.match(r'^[A-Za-z0-9+/=\s]+$', line):
                        # base64일 가능성이 높지만, 실제 텍스트일 수도 있으므로 더 엄격하게 체크
                        # base64는 보통 100자 이상이고, 공백이 거의 없음
                        if len(line) > 100 and line.count(' ') < len(line) * 0.1:
                            continue  # base64 라인으로 판단하여 제거
                    if line:  # 빈 줄이 아니면 추가
                        cleaned_lines.append(line)
                
                # 정리된 내용 다시 저장
                with open(body_txt_path, 'w', encoding='utf-8') as f:
                    f.write('\n'.join(cleaned_lines))
                # fitz로 직접 PDF 변환 (WeasyPrint 대신)
                import fitz
                import platform
                import glob
                
                with open(body_txt_path, 'r', encoding='utf-8') as txt_file:
                    text_content = txt_file.read()
                
                doc = fitz.open()  # 새 PDF 문서 생성
                page = doc.new_page()
                
                # 한글 폰트 파일 찾기
                system = platform.system()
                font_file = None
                font_name = "helv"  # 기본값
                
                if system == "Darwin":  # macOS
                    # macOS 한글 폰트 경로 찾기
                    font_paths = [
                        "/System/Library/Fonts/AppleSDGothicNeo.ttc",
                        "/System/Library/Fonts/Supplemental/AppleSDGothicNeo.ttc",
                        "/Library/Fonts/AppleGothic.ttf",
                        "/System/Library/Fonts/AppleGothic.ttf",
                    ]
                    for path in font_paths:
                        if os.path.exists(path):
                            font_file = path
                            font_name = "korean"
                            break
                elif system == "Windows":
                    font_paths = [
                        "C:/Windows/Fonts/malgun.ttf",
                        "C:/Windows/Fonts/gulim.ttc",
                    ]
                    for path in font_paths:
                        if os.path.exists(path):
                            font_file = path
                            font_name = "korean"
                            break
                else:  # Linux
                    font_paths = [
                        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
                        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
                    ]
                    for path in font_paths:
                        if os.path.exists(path):
                            font_file = path
                            font_name = "korean"
                            break
                
                # 폰트 파일이 있으면 로드
                if font_file:
                    try:
                        # 페이지에 폰트 추가
                        page.insert_font(fontname=font_name, fontfile=font_file)
                    except Exception as e:
                        print(f"폰트 로드 실패: {e}, 기본 폰트 사용")
                        font_name = "helv"
                def _wrap_line_to_width(
                    line: str, max_width: float, fontname: str, fontsize: int
                ) -> list[str]:
                    """페이지 폭(max_width)에 맞게 line을 여러 줄로 wrap.
                    - 공백이 있으면 공백 기준 우선 분할
                    - 공백이 거의 없는 한글 문장은 문자 단위로 분할
                    """
                    line = (line or "").rstrip("\n")
                    if not line:
                        return [""]

                    try:
                        if fitz.get_text_length(line, fontname=fontname, fontsize=fontsize) <= max_width:
                            return [line]
                    except Exception:
                        # 폰트 이름 문제 등으로 get_text_length 실패 시, 문자 길이로 대략 처리
                        if len(line) <= 80:
                            return [line]

                    out: list[str] = []
                    # 공백 분할 우선
                    tokens = line.split(" ")
                    if len(tokens) > 1:
                        cur = ""
                        for tok in tokens:
                            cand = f"{cur} {tok}".strip() if cur else tok
                            try:
                                ok = fitz.get_text_length(cand, fontname=fontname, fontsize=fontsize) <= max_width
                            except Exception:
                                ok = len(cand) <= 80
                            if ok:
                                cur = cand
                            else:
                                if cur:
                                    out.append(cur)
                                cur = tok
                        if cur:
                            out.append(cur)
                        return out

                    # 공백이 거의 없으면 문자 단위 분할
                    cur = ""
                    for ch in line:
                        cand = cur + ch
                        try:
                            ok = fitz.get_text_length(cand, fontname=fontname, fontsize=fontsize) <= max_width
                        except Exception:
                            ok = len(cand) <= 80
                        if ok:
                            cur = cand
                        else:
                            if cur:
                                out.append(cur)
                            cur = ch
                    if cur:
                        out.append(cur)
                    return out

                # 텍스트를 페이지 폭에 맞게 wrap해서 삽입 (겹침 방지)
                fontsize = 11
                margin = 50
                line_height = int(fontsize * 1.4)
                max_width = page.rect.width - margin * 2
                y_position = 50

                for raw_line in text_content.split("\n"):
                    for line in _wrap_line_to_width(raw_line, max_width, font_name, fontsize):
                        if y_position > page.rect.height - 50:
                            page = doc.new_page()
                            # 새 페이지에도 폰트 추가
                            if font_file:
                                try:
                                    page.insert_font(fontname=font_name, fontfile=font_file)
                                except Exception:
                                    pass
                            y_position = 50

                        if line.strip():
                            try:
                                page.insert_text((margin, y_position), line, fontsize=fontsize, fontname=font_name)
                            except Exception:
                                page.insert_text((margin, y_position), line, fontsize=fontsize)
                        y_position += line_height
                
                pdf_save_path = self.file_path.replace('.hwp', '.pdf')
                doc.save(pdf_save_path)
                doc.close()
                self.pdf_path = pdf_save_path
                    
            # OLE 형식 확인 (d0 cf 11 e0 a1 b1 1a e1)
            elif header[:8] == bytes([0xd0, 0xcf, 0x11, 0xe0, 0xa1, 0xb1, 0x1a, 0xe1]):
                # OLE 형식: 기존 hwp5html 방식 사용
                subprocess.run(['hwp5html', self.file_path, '--output', self.output_dir], check=True, timeout=600)
                converted_file_path = os.path.join(self.output_dir, 'index.xhtml')
                
                # XHTML에서 텍스트 추출
                try:
                    from bs4 import BeautifulSoup
                    with open(converted_file_path, 'r', encoding='utf-8') as f:
                        soup = BeautifulSoup(f.read(), 'html.parser')
                    text_content = soup.get_text(separator='\n', strip=True)
                except ImportError:
                    # BeautifulSoup이 없으면 간단한 텍스트 추출
                    import re
                    with open(converted_file_path, 'r', encoding='utf-8') as f:
                        content = f.read()
                    # HTML 태그 제거
                    text_content = re.sub(r'<[^>]+>', '', content)
                
                # fitz로 PDF 생성 (한글 폰트 포함)
                import fitz
                import platform
                
                doc = fitz.open()
                page = doc.new_page()
                
                # 한글 폰트 파일 찾기 (HWPML과 동일한 로직)
                system = platform.system()
                font_file = None
                font_name = "helv"  # 기본값
                
                if system == "Darwin":  # macOS
                    font_paths = [
                        "/System/Library/Fonts/AppleSDGothicNeo.ttc",
                        "/System/Library/Fonts/Supplemental/AppleSDGothicNeo.ttc",
                        "/Library/Fonts/AppleGothic.ttf",
                        "/System/Library/Fonts/AppleGothic.ttf",
                    ]
                    for path in font_paths:
                        if os.path.exists(path):
                            font_file = path
                            font_name = "korean"
                            break
                elif system == "Windows":
                    font_paths = [
                        "C:/Windows/Fonts/malgun.ttf",
                        "C:/Windows/Fonts/gulim.ttc",
                    ]
                    for path in font_paths:
                        if os.path.exists(path):
                            font_file = path
                            font_name = "korean"
                            break
                else:  # Linux
                    font_paths = [
                        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
                        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
                    ]
                    for path in font_paths:
                        if os.path.exists(path):
                            font_file = path
                            font_name = "korean"
                            break
                
                # 폰트 파일이 있으면 로드
                if font_file:
                    try:
                        # 페이지에 폰트 추가
                        page.insert_font(fontname=font_name, fontfile=font_file)
                    except Exception as e:
                        print(f"폰트 로드 실패: {e}, 기본 폰트 사용")
                        font_name = "helv"
                def _wrap_line_to_width(
                    line: str, max_width: float, fontname: str, fontsize: int
                ) -> list[str]:
                    line = (line or "").rstrip("\n")
                    if not line:
                        return [""]

                    try:
                        if fitz.get_text_length(line, fontname=fontname, fontsize=fontsize) <= max_width:
                            return [line]
                    except Exception:
                        if len(line) <= 80:
                            return [line]

                    out: list[str] = []
                    tokens = line.split(" ")
                    if len(tokens) > 1:
                        cur = ""
                        for tok in tokens:
                            cand = f"{cur} {tok}".strip() if cur else tok
                            try:
                                ok = fitz.get_text_length(cand, fontname=fontname, fontsize=fontsize) <= max_width
                            except Exception:
                                ok = len(cand) <= 80
                            if ok:
                                cur = cand
                            else:
                                if cur:
                                    out.append(cur)
                                cur = tok
                        if cur:
                            out.append(cur)
                        return out

                    cur = ""
                    for ch in line:
                        cand = cur + ch
                        try:
                            ok = fitz.get_text_length(cand, fontname=fontname, fontsize=fontsize) <= max_width
                        except Exception:
                            ok = len(cand) <= 80
                        if ok:
                            cur = cand
                        else:
                            if cur:
                                out.append(cur)
                            cur = ch
                    if cur:
                        out.append(cur)
                    return out

                fontsize = 11
                margin = 50
                line_height = int(fontsize * 1.4)
                max_width = page.rect.width - margin * 2
                y_position = 50

                for raw_line in text_content.split("\n"):
                    for line in _wrap_line_to_width(raw_line, max_width, font_name, fontsize):
                        if y_position > page.rect.height - 50:
                            page = doc.new_page()
                            # 새 페이지에도 폰트 추가
                            if font_file:
                                try:
                                    page.insert_font(fontname=font_name, fontfile=font_file)
                                except Exception:
                                    pass
                            y_position = 50

                        if line.strip():
                            try:
                                page.insert_text((margin, y_position), line, fontsize=fontsize, fontname=font_name)
                            except Exception:
                                page.insert_text((margin, y_position), line, fontsize=fontsize)
                        y_position += line_height
                
                # PDF 경로 생성 (.hwp -> .pdf)
                pdf_save_path = self.file_path.replace('.hwp', '.pdf')
                doc.save(pdf_save_path)
                doc.close()
                self.pdf_path = pdf_save_path  # PDF 경로 저장
            else:
                # 알 수 없는 형식
                raise RuntimeError(f"Unknown HWP file format. Header: {header.hex()}")
            
            # PDF 로더로 문서 로드
            if not PyMuPDFLoader:
                raise RuntimeError("PyMuPDFLoader not available")
            loader = PyMuPDFLoader(pdf_save_path)
            return loader.load()
        except Exception as e:
            print(f"Failed to convert {self.file_path} to XHTML")
            raise e
        finally:
            if os.path.exists(self.output_dir):
                shutil.rmtree(self.output_dir)


class llm_serving:
    def __init__(self, serving_id: int = 15, bearer_token: str = None, genos_url: str = 'https://genos.genon.ai:3443'):
        self.serving_id = serving_id
        self.url = f"{genos_url}/api/gateway/rep/serving/{serving_id}"
        # self.url = f"http://llmops-gateway-api-service:8080/rep/serving/{serving_id}"  # 바뀐 대표 리비전 호출 url
        self.headers = dict(Authorization=f"Bearer {bearer_token}")
        self.endpoint = f"{self.url}/v1/models"
        if serving_id and bearer_token:
            pass
        else:
            print('serving id 혹은 인증키가 입력되지 않았습니다.')

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
            response = requests.post(endpoint, headers=self.headers, json=body, stream=True)
            result = response.json()['choices'][0]['message']['content']
        except KeyError as e:
            print(response.json())
            print(f'llm 서빙 호출 중 keyerror 발생: {e}')
            return None
        except requests.exceptions.RequestException as e:
            print(f'llm 서빙 호출 중 오류 발생 : {e}')
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
            async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
                response = await client.post(endpoint, headers=self.headers, json=body)
            result = response.json()['choices'][0]['message']['content']
        except KeyError as e:
            print(response.json())
            print(f'llm 서빙 호출 중 keyerror 발생: {e}')
            return None
        except httpx.RequestError as e:
            print(f'llm 서빙 호출 중 오류 발생 : {e}')
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
                       body={
                           'messages': [
                               {'role': 'system', 'content': '당신은 유용한 어시스턴트입니다.'},
                               {'role': 'user', 'content': '안녕?'}
                           ]
                       },
                       output_format: BaseModel = None):
        """
        LLM output의 모든 데이터를 내보냅니다.
        """
        if output_format:
            body['response_format'] = self.make_openai_json_schema(output_format)

        endpoint = f"{self.url}/v1/chat/completions"
        response = requests.post(endpoint, headers=self.headers, json=body)
        result = response.json()
        return result


class GenosBucketChunker(BaseChunker):
    """토큰 제한을 고려하여 섹션별 청크를 분할하고 병합하는 청커 (v2)"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    tokenizer: Union[PreTrainedTokenizerBase, str] = "sentence-transformers/all-MiniLM-L6-v2"
    max_tokens: int = 1024
    merge_peers: bool = True
    subject: str
    _tokenizer: PreTrainedTokenizerBase = None
    image_option: int
    legal_option: int
    merge_list_items: bool = True
    llm: llm_serving

    @model_validator(mode="after")
    def _initialize_components(self) -> Self:
        # 토크나이저 초기화
        self._tokenizer = (
            self.tokenizer
            if isinstance(self.tokenizer, PreTrainedTokenizerBase)
            else AutoTokenizer.from_pretrained(self.tokenizer)
        )

        return self

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
                current_heading_by_level[header_level] = item.text
                current_heading_short_by_level[header_level] = item.orig  # 첫 단어로 짧은 헤더 정보 설정

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
                # if item.label in [DocItemLabel.PAGE_HEADER, DocItemLabel.PAGE_FOOTER]:
                #     item.text = ""
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
                                               dl_doc: DoclingDocument) -> str:
        """DocItem 리스트로부터 헤더 정보를 포함한 텍스트 생성"""
        text_parts = []
        current_section_headers = {}  # 현재 섹션의 헤더 정보
        system_prompt = f"""다음은 PDF 문서 내에 포함된 HTML 테이블입니다.
        이 테이블에 대한 설명을 한국어로 작성해 주세요.
        분석이나 해석은 하지 말고, 테이블의 핵심 주제만 작성하세요.
        해당 주제는 Multimodal RAG 시스템의 VectorDB에 저장됩니다.
        검색이 잘 될 수 있도록 문서 주제를 반영하여 description을 작성하세요.
        문서 주제 : {self.subject}"""

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
                                headers_to_add.append(item_headers[l])
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
                table_text = self._extract_table_text(item, dl_doc)
                if self.image_option == 1:
                    description = self.llm.call(system_prompt=system_prompt, question=table_text)
                    print('추출된 주제:', description)
                    table_text = f"{description}\n{table_text}"
                    print(table_text)
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
                if item.text not in text_parts:
                    text_parts.append(item.text)
            elif isinstance(item, PictureItem):
                text = ""
                for annotation in item.annotations:
                    if hasattr(annotation, 'text'):
                        text += annotation.text
                text_parts.append(text)

        result_text = self.delim.join(text_parts)
        return result_text

    def _extract_table_text(self, table_item: TableItem, dl_doc: DoclingDocument) -> str:
        """테이블에서 텍스트를 추출하는 일반화된 메서드"""

        try:
            # 먼저 export_to_markdown 시도
            table_text = table_item.export_to_markdown(dl_doc)

            if table_text and table_text.strip():
                return table_text
        except Exception as e:
            print(f'테이블 추출 중 오류 발생:{e}')
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
                    table_text = ' '.join(cell_texts)
                    return table_text
        except Exception as e:
            print(f'테이블 직접 추출 중 오류 발생: {e}')
            pass

        # 모든 방법 실패 시 item.text 사용 (있는 경우)
        if hasattr(table_item, 'text') and table_item.text:
            return table_item.text

        return ""

    def _generate_section_text_with_heading(self, section_items: list[DocItem],
                                            section_header_infos: list[dict],
                                            dl_doc: DoclingDocument) -> str:
        """섹션의 텍스트를 생성하되, 앞에 heading을 붙임"""
        # 첫 번째 item의 header_info에서 heading 추출
        if section_header_infos and section_header_infos[0]:
            merged_headers = {}
            for level, header_text in section_header_infos[0].items():
                if header_text:
                    merged_headers[level] = header_text

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
            section_items, section_header_infos, dl_doc
        )

        # heading이 있으면 앞에 붙이기
        if heading_text:
            return heading_text + ", " + section_text
        else:
            return section_text

    def _extract_used_headers(self, header_info_list: list[dict]) -> Optional[list[str]]:
        """헤더 정보 리스트에서 실제 사용되는 모든 헤더들을 level 순서대로 추출하고 ', '로 연결"""
        if not header_info_list:
            return None

        all_headers = []  # header 순서대로 추가
        seen_headers = set()  # 중복 방지용

        for header_info in header_info_list:
            if header_info:
                for level in sorted(header_info.keys()):
                    header_text = header_info[level]
                    if header_text and header_text not in seen_headers:
                        all_headers.append(header_text)
                        seen_headers.add(header_text)

        return all_headers if all_headers else None

    def _is_section_header(self, item: DocItem) -> bool:
        """아이템이 section header인지 확인"""
        return (isinstance(item, SectionHeaderItem) or
                (isinstance(item, TextItem) and
                 item.label in [DocItemLabel.SECTION_HEADER, DocItemLabel.TITLE]))

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

    # def _split_document_by_tokens(self, doc_chunk: DocChunk, dl_doc: DoclingDocument) -> list[DocChunk]:
    #     """문서를 토큰 제한에 맞게 분할 (v2: 섹션 헤더 기준으로 분할 후 max_tokens로 병합)"""
    #     items = doc_chunk.meta.doc_items
    #     header_info_list = getattr(doc_chunk, '_header_info_list', [])
    #     header_short_info_list = getattr(doc_chunk, '_header_short_info_list', [])

    #     if not items:
    #         return []

    #     # 1단계: 섹션 헤더가 바뀔 때마다 분할
    #     sections = []  # [(items, header_infos, header_short_infos), ...]
    #     current_section_items = []
    #     current_section_header_infos = []
    #     current_section_header_short_infos = []

    #     for i in range(len(items)):
    #         item = items[i]
    #         header_info = header_info_list[i] if i < len(header_info_list) else {}
    #         header_short_info = header_short_info_list[i] if i < len(header_short_info_list) else {}

    #         # 섹션 헤더를 만나면
    #         if self._is_section_header(item):
    #             # 이전 섹션이 있으면 저장
    #             if current_section_items:
    #                 sections.append((
    #                     current_section_items,
    #                     current_section_header_infos,
    #                     current_section_header_short_infos
    #                 ))

    #             # 새로운 섹션 시작
    #             current_section_items = [item]
    #             current_section_header_infos = [header_info]
    #             current_section_header_short_infos = [header_short_info]
    #         else:
    #             # 섹션 헤더가 아니면 현재 섹션에 추가
    #             current_section_items.append(item)
    #             current_section_header_infos.append(header_info)
    #             current_section_header_short_infos.append(header_short_info)

    #     # 마지막 섹션 저장
    #     if current_section_items:
    #         sections.append((
    #             current_section_items,
    #             current_section_header_infos,
    #             current_section_header_short_infos
    #         ))

    #     # 1.5단계: 한줄만으로 구성된 섹션헤더는 다음 섹션과 병합
    #     for i in range(len(sections) - 2, -1, -1):
    #         items, h_infos, h_short = sections[i]

    #         # 아이템이 하나인 섹션만 검사
    #         if len(items) != 1:
    #             continue

    #         # 섹션 헤더인 경우만 검사
    #         if not self._is_section_header(items[0]):
    #             continue

    #         # 문단이 이미 구성된 것은 제외
    #         item_text = "".join(item.text for item in items if hasattr(item, "text"))
    #         if len(item_text) > 30: # 타이틀의 문자 수가 너무 길면 제외
    #             continue

    #         # 다음 섹션과 병합
    #         n_items, n_h_infos, n_h_short = sections[i + 1]
    #         sections[i] = (items + n_items, h_infos + n_h_infos, h_short + n_h_short)
    #         sections.pop(i + 1)

    #     # 2단계: 각 섹션의 텍스트에 heading 붙이기
    #     sections_with_text = []
    #     for section_items, section_header_infos, section_header_short_infos in sections:
    #         section_text = self._generate_section_text_with_heading(
    #             section_items, section_header_short_infos, dl_doc
    #         )
    #         sections_with_text.append((
    #             section_text,
    #             section_items,
    #             section_header_infos,
    #             section_header_short_infos
    #         ))

    #     # 3단계: 섹션들을 max_tokens 기준으로 병합
    #     result_chunks = []
    #     merged_texts = []
    #     merged_items = []
    #     merged_header_infos = []
    #     merged_header_short_infos = []

    #     for section_text, section_items, section_header_infos, section_header_short_infos in sections_with_text:
    #         # 병합 가능한지 테스트
    #         test_text = "\n".join(merged_texts + [section_text])
    #         test_tokens = self._count_tokens(test_text)

    #         # max_tokens 이하이거나 첫 섹션이면 병합
    #         if test_tokens <= self.max_tokens or len(merged_texts) == 0:
    #             merged_texts.append(section_text)
    #             merged_items.extend(section_items)
    #             merged_header_infos.extend(section_header_infos)
    #             merged_header_short_infos.extend(section_header_short_infos)
    #         else:
    #             # max_tokens 초과하면 현재까지 chunk 생성
    #             chunk_text = "\n".join(merged_texts)
    #             used_headers = self._extract_used_headers(merged_header_short_infos)
    #             result_chunks.append(DocChunk(
    #                 text=chunk_text,
    #                 meta=DocMeta(
    #                     doc_items=merged_items,
    #                     headings=used_headers,
    #                     captions=None,
    #                     origin=doc_chunk.meta.origin,
    #                 )
    #             ))

    #             # 새로운 병합 시작
    #             merged_texts = [section_text]
    #             merged_items = section_items
    #             merged_header_infos = section_header_infos
    #             merged_header_short_infos = section_header_short_infos

    #     # 마지막 병합된 items 처리
    #     if merged_texts:
    #         chunk_text = "\n".join(merged_texts)
    #         used_headers = self._extract_used_headers(merged_header_short_infos)
    #         result_chunks.append(DocChunk(
    #             text=chunk_text,
    #             meta=DocMeta(
    #                 doc_items=merged_items,
    #                 headings=used_headers,
    #                 captions=None,
    #                 origin=doc_chunk.meta.origin,
    #             )
    #         ))

    #     return result_chunks

    def _split_document_by_tokens(self, doc_chunk: DocChunk, dl_doc: DoclingDocument) -> list[DocChunk]:
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

        def get_current_chunk(doc_chunk: DocChunk, merged_texts: list[str], merged_header_short_infos: list[dict],
                              merged_items: list[DocItem]):
            """현재까지 병합된 내용으로 DocChunk 생성"""
            if not merged_texts or not merged_items:
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
                return self._extract_table_text(item, dl_doc)
            elif hasattr(item, 'text') and item.text:
                return item.text
            elif isinstance(item, PictureItem):
                text = ""
                for annotation in item.annotations:
                    if hasattr(annotation, 'text'):
                        text += annotation.text
                return text
            return ""

        def split_items_evenly_by_tokens(item_token_counts, max_tokens):
            import math, bisect

            n = len(item_token_counts)
            total = sum(item_token_counts)
            if n == 0:
                return []
            if total <= max_tokens:
                return [(0, n)]  # ✅ 항상 (a,b)

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
                if 0 <= j - 1 < len(P): cand.append(j - 1)

                best = None
                best_dist = float("inf")
                for x in cand:
                    if x in used:
                        continue
                    if x <= cuts[-1]:
                        continue
                    if x >= len(P) - 1:  # n
                        continue
                    dist = abs(P[x] - goal)
                    if dist < best_dist:
                        best_dist = dist
                        best = x

                if best is None:
                    best = min(max(cuts[-1] + 1, 1), len(P) - 2)

                cuts.append(best)
                used.add(best)

            cuts.append(n)

            return [(a, b) for a, b in zip(cuts[:-1], cuts[1:])]

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

            # 섹션 헤더를 만나면
            if self._is_section_header(item):
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
                items, header_short_infos, dl_doc
            )
            sections_with_text.append((
                text,
                items,
                header_infos,
                header_short_infos
            ))

        # ================================================================
        # 2단계: 각 섹션의 텍스트에 heading 붙이기
        # ================================================================

        sections_with_text = []
        for items, header_infos, header_short_infos in sections:
            text = self._generate_section_text_with_heading(
                items, header_short_infos, dl_doc
            )
            sections_with_text.append((
                text,
                items,
                header_infos,
                header_short_infos
            ))

        # print("-"*20)
        # print(f"2단계")
        # for text, items, h_infos, h_short in sections_with_text:
        #     print(f"text: {text}")
        #     print(f"items: {[it.text if hasattr(it, 'text') else str(it) for it in items]}")
        #     print(f"h_infos: {h_infos}")
        #     print(f"h_short: {h_short}")
        #     print("-"*10)

        # ================================================================
        # 2.5단계: 너무 긴 청크는 분할
        # ================================================================
        if self.max_tokens > 0:
            for i in range(len(sections_with_text)):
                text, items, h_infos, h_short = sections_with_text[i]
                token_count = self._count_tokens(text)
                if token_count < self.max_tokens:
                    continue

                # caption 및 table 내 그림은 같은 섹션에 있도록 조정
                items_group = [[(item, info, short)] for item, info, short in zip(items, h_infos, h_short)]
                items_group = adjust_captions(items_group)
                items_group = adjust_pictures_in_tables(items_group)

                # 너무 긴 섹션은 분할
                # 각 아이템 별 token 수 계산
                item_token_counts = []
                for group in items_group:
                    cur_count = 0
                    for g in group:
                        cur_count += self._count_tokens(get_text_from_item(g[0]))
                    item_token_counts.append(cur_count)

                # 아이템 그룹들을 토큰 기준으로 균등 분할
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
                        group_items, group_h_short, dl_doc
                    )
                    new_sections.append((new_text, group_items, group_h_infos, group_h_short))

                # 원래 섹션을 새로 분할된 섹션들로 교체
                sections_with_text.pop(i)
                for new_section in reversed(new_sections):
                    sections_with_text.insert(i, new_section)

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

            # ----------------------------------
            # 병합 가능 여부 판단

            # 병합 가능 토큰 수 계산
            test_tokens = self._count_tokens("\n".join(merged_texts + [text]))

            # 현재 섹션헤더 레벨과 병합된 섹션헤더 레벨
            section_level = get_header_level(header_infos, first=True)
            merged_level = get_header_level(merged_header_infos, first=False)

            # 토큰 수 초과 시 새로운 청크 생성
            if test_tokens > self.max_tokens and len(merged_texts) > 0:
                b_new_chunk = True
            # 현재 섹션헤더 레벨이 더 높으면 새로운 청크 생성
            elif 0 <= section_level < merged_level:
                b_new_chunk = True
            # ----------------------------------

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
        # doc_chunks = list(self._inner_chunker.chunk(dl_doc=dl_doc, **kwargs))
        doc_chunks = list(self.preprocess(dl_doc=dl_doc, **kwargs))

        if not doc_chunks:
            return iter([])

        doc_chunk = doc_chunks[0]  # HierarchicalChunker는 하나의 청크만 반환
        # {image_description_on : 1, toc_on : 0}
        if self.image_option == 1 and self.legal_option != 1:
            final_chunks = self._split_document_by_tokens_image(doc_chunk, dl_doc)

        else:
            final_chunks = self._split_document_by_tokens(doc_chunk, dl_doc)

        return iter(final_chunks)

    def _split_document_by_tokens_image(self, doc_chunk: DocChunk, dl_doc: DoclingDocument) -> list[DocChunk]:
        """문서를 토큰 제한에 맞게 분할 (여러 섹션이 하나의 청크에 포함 가능)"""
        system_prompt = f"""다음은 PDF 문서 내에 포함된 HTML 테이블입니다.
        이 테이블에 대한 설명을 한국어로 작성해 주세요.
        분석이나 해석은 하지 말고, 테이블의 핵심 주제만 작성하세요.
        해당 주제는 Multimodal RAG 시스템의 VectorDB에 저장됩니다.
        검색이 잘 될 수 있도록 문서 주제를 반영하여 description을 작성하세요.
        문서 주제 : {self.subject}"""
        items = doc_chunk.meta.doc_items
        header_info_list = getattr(doc_chunk, '_header_info_list', [])  # 각 아이템의 헤더 정보 리스트
        print('<split_document_by_tokens_image>')
        if not items:
            return []

        result_chunks = []
        current_items = []
        current_header_infos = []

        i = 0
        while i < len(items):
            item = items[i]
            header_info = header_info_list[i] if i < len(header_info_list) else {}

            # 테이블 아이템인 경우 특별 처리
            if isinstance(item, TableItem):
                # 현재까지 누적된 아이템들이 있으면 먼저 청크로 생성
                if current_items:
                    chunk_text = self._generate_text_from_items_with_headers(
                        current_items, current_header_infos, dl_doc
                    )
                    tokens = self._count_tokens(chunk_text)

                    # 실제 사용된 헤더들만 추출
                    used_headers = self._extract_used_headers(current_header_infos)
                    result_chunks.append(DocChunk(
                        text=chunk_text,
                        meta=DocMeta(
                            doc_items=current_items.copy(),
                            headings=used_headers,
                            captions=None,
                            origin=doc_chunk.meta.origin,
                        )
                    ))
                    current_items = []
                    current_header_infos = []

                # 테이블과 앞뒤 아이템을 포함한 청크 생성
                table_items = []
                table_header_infos = []

                # 앞 아이템 추가 (가능한 경우)
                # if i > 0 and len(result_chunks) == 0:  # 첫 번째 테이블이고 앞에 아이템이 있는 경우
                #     table_items.append(items[i-1])
                #     prev_header_info = header_info_list[i-1] if i-1 < len(header_info_list) else {}
                #     table_header_infos.append(prev_header_info)

                # 테이블 추가
                table_items.append(item)

                table_header_infos.append(header_info)

                # 뒤 아이템 추가 (가능한 경우)
                # if i + 1 < len(items):
                #     table_items.append(items[i+1])
                #     next_header_info = header_info_list[i+1] if i+1 < len(header_info_list) else {}
                #     table_header_infos.append(next_header_info)
                #     i += 1  # 다음 아이템은 이미 처리했으므로 스킵

                # 테이블 청크 생성 (토큰 제한 확인)
                table_text = self._generate_text_from_items_with_headers(
                    table_items, table_header_infos, dl_doc
                )
                table_tokens = self._count_tokens(table_text)

                # 테이블이 max_tokens를 초과하는 경우, 테이블을 분할
                if table_tokens > self.max_tokens:

                    # 테이블 텍스트만 추출하여 분할
                    table_only_text = self._extract_table_text(item, dl_doc)
                    # split_tables = self._split_table_text(table_only_text, 4096)
                    split_tables = [table_only_text]

                    # 분할된 각 테이블에 대해 청크 생성
                    for split_table in split_tables:
                        # 기존 _generate_text_from_items_with_headers 함수 활용
                        full_text = self._generate_text_from_items_with_headers(
                            [item], [header_info], dl_doc
                        )
                        # 원본 테이블 텍스트를 분할된 테이블로 교체
                        full_text = full_text.replace(table_only_text, split_table)
                        description = self.llm.call(system_prompt=system_prompt, question=full_text)
                        full_text = f"{description}\n{full_text}"
                        print('<max_tokens> 초과')
                        print('추출된 주제:', description)
                        print(full_text)
                        # 원래 tableitem에 들어갔어야 할 heading 값 유지
                        used_headers = self._extract_used_headers([header_info])
                        result_chunks.append(DocChunk(
                            text=full_text,
                            meta=DocMeta(
                                doc_items=[item],
                                headings=used_headers,
                                captions=None,
                                origin=doc_chunk.meta.origin,
                            )
                        ))
                else:
                    description = self.llm.call(system_prompt=system_prompt, question=table_text)
                    table_text = f"{description}\n{table_text}"
                    print('<max_tokens 미초과>')
                    print('추출된 주제:', description)
                    print(table_text)
                    used_headers = self._extract_used_headers(table_header_infos)
                    result_chunks.append(DocChunk(
                        text=table_text,
                        meta=DocMeta(
                            doc_items=table_items,
                            headings=used_headers,
                            captions=None,
                            origin=doc_chunk.meta.origin,
                        )
                    ))

                i += 1
                continue

            # 일반 아이템 처리 - 토큰 제한 확인
            test_items = current_items + [item]
            test_header_infos = current_header_infos + [header_info]
            test_text = self._generate_text_from_items_with_headers(
                test_items, test_header_infos, dl_doc
            )
            test_tokens = self._count_tokens(test_text)

            if test_tokens <= self.max_tokens:
                current_items.append(item)
                current_header_infos.append(header_info)
            else:
                # 토큰 제한 초과 - 현재까지의 아이템들로 청크 생성
                if current_items:
                    chunk_text = self._generate_text_from_items_with_headers(
                        current_items, current_header_infos, dl_doc
                    )
                    chunk_tokens = self._count_tokens(chunk_text)

                    used_headers = self._extract_used_headers(current_header_infos)
                    result_chunks.append(DocChunk(
                        text=chunk_text,
                        meta=DocMeta(
                            doc_items=current_items.copy(),
                            headings=used_headers,
                            captions=None,
                            origin=doc_chunk.meta.origin,
                        )
                    ))
                    # 새로운 청크 시작
                    current_items = [item]
                    current_header_infos = [header_info]
                else:
                    # 단일 아이템이 토큰 제한을 초과하는 경우
                    single_text = self._generate_text_from_items_with_headers(
                        [item], [header_info], dl_doc
                    )
                    single_tokens = self._count_tokens(single_text)

                    used_headers = self._extract_used_headers([header_info])
                    result_chunks.append(DocChunk(
                        text=single_text,
                        meta=DocMeta(
                            doc_items=[item],
                            headings=used_headers,
                            captions=None,
                            origin=doc_chunk.meta.origin,
                        )
                    ))

            i += 1

        # 마지막 남은 아이템들 처리
        if current_items:
            chunk_text = self._generate_text_from_items_with_headers(
                current_items, current_header_infos, dl_doc
            )
            chunk_tokens = self._count_tokens(chunk_text)

            used_headers = self._extract_used_headers(current_header_infos)
            result_chunks.append(DocChunk(
                text=chunk_text,
                meta=DocMeta(
                    doc_items=current_items,
                    headings=used_headers,
                    captions=None,
                    origin=doc_chunk.meta.origin,
                )
            ))

        # 작은 청크들 병합 처리
        return self._merge_small_chunks(result_chunks, dl_doc)

    def _merge_small_chunks(self, chunks: list[DocChunk], dl_doc: DoclingDocument) -> list[DocChunk]:
        """작은 청크들을 병합하여 토큰 효율성을 높임 (개선된 버전)"""
        if not chunks:
            return chunks

        min_chunk_size = self.max_tokens // 3  # 최소 청크 크기를 더 크게 설정 (2000/3 = 666토큰)
        merged_chunks = []
        current_merge_candidate = None

        for i, chunk in enumerate(chunks):
            chunk_tokens = self._count_tokens(chunk.text)

            # 아주 큰 청크는 분할 필요
            if chunk_tokens > self.max_tokens:
                if current_merge_candidate:
                    merged_chunks.append(current_merge_candidate)
                    current_merge_candidate = None

                # 큰 청크를 분할 (임시로 그대로 추가하되, 경고 표시)
                merged_chunks.append(chunk)
                continue

            # 작은 청크인 경우 병합 대상 (테이블 청크도 포함)
            if chunk_tokens < min_chunk_size:
                if current_merge_candidate is None:
                    current_merge_candidate = chunk
                else:
                    # 병합 시도
                    merged_items = current_merge_candidate.meta.doc_items + chunk.meta.doc_items
                    merged_header_infos = (
                            getattr(current_merge_candidate, '_header_info_list', []) +
                            getattr(chunk, '_header_info_list', [])
                    )

                    merged_text = self._generate_text_from_items_with_headers(
                        merged_items, merged_header_infos, dl_doc
                    )
                    merged_tokens = self._count_tokens(merged_text)

                    if merged_tokens <= self.max_tokens:
                        current_merge_candidate = DocChunk(
                            text=merged_text,
                            meta=DocMeta(
                                doc_items=merged_items,
                                headings=self._extract_used_headers(merged_header_infos),
                                captions=None,
                                origin=chunk.meta.origin,
                            )
                        )
                        current_merge_candidate._header_info_list = merged_header_infos
                    else:
                        merged_chunks.append(current_merge_candidate)
                        current_merge_candidate = chunk
            else:
                if current_merge_candidate:
                    # 이전 병합 후보가 있으면 현재 청크와 병합 시도
                    candidate_tokens = self._count_tokens(current_merge_candidate.text)
                    if candidate_tokens < min_chunk_size:
                        # 현재 청크와 병합 시도
                        merged_items = current_merge_candidate.meta.doc_items + chunk.meta.doc_items
                        merged_header_infos = (
                                getattr(current_merge_candidate, '_header_info_list', []) +
                                getattr(chunk, '_header_info_list', [])
                        )

                        merged_text = self._generate_text_from_items_with_headers(
                            merged_items, merged_header_infos, dl_doc
                        )
                        merged_tokens = self._count_tokens(merged_text)

                        if merged_tokens <= self.max_tokens:
                            merged_chunks.append(DocChunk(
                                text=merged_text,
                                meta=DocMeta(
                                    doc_items=merged_items,
                                    headings=self._extract_used_headers(merged_header_infos),
                                    captions=None,
                                    origin=chunk.meta.origin,
                                )
                            ))
                            current_merge_candidate = None
                            continue

                    # 병합할 수 없으면 후보를 먼저 추가
                    merged_chunks.append(current_merge_candidate)
                    current_merge_candidate = None

                merged_chunks.append(chunk)

        # 마지막 병합 후보 처리
        if current_merge_candidate:
            merged_chunks.append(current_merge_candidate)

        return merged_chunks


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
    created_date: int = None
    appendix: str = None  ## !! appendix feature (2025-09-30, geonhee kim) !!


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
        self.created_date: Optional[int] = None
        self.appendix: Optional[str] = None  # !! appendix feature (2025-09-30, geonhee kim) !!

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
            for prov in item.prov:
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
        self.e_page = max([bbox['page'] for bbox in chunk_bboxes]) if chunk_bboxes else None
        self.chunk_bboxes = json.dumps(chunk_bboxes)
        return self

    def set_media_files(self, doc_items: list) -> "GenOSVectorMetaBuilder":
        temp_list = []
        for item in doc_items:
            if isinstance(item, PictureItem):
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
            created_date=self.created_date,
            appendix=self.appendix or ""  # !! appendix feature (2025-09-30, geonhee kim) !!
        )


class DocumentProcessor:
    def __init__(self):
        '''
        initialize Document Converter
        '''
        self._document_cache = {}  # 문서 캐시를 위한 dict 추가
        self.ocr_endpoint = "http://192.168.73.172:48080/ocr"
        ocr_options = PaddleOcrOptions(
            force_full_page_ocr=False,
            lang=['korean'],
            ocr_endpoint=self.ocr_endpoint,
            text_score=0.3)
        self.page_chunk_counts = defaultdict(int)

        self.page_chunk_counts = defaultdict(int)
        device = AcceleratorDevice.AUTO
        num_threads = 8
        accelerator_options = AcceleratorOptions(num_threads=num_threads, device=device)
        # PDF 파이프라인 옵션 설정
        self.pipe_line_options = PdfPipelineOptions()
        self.pipe_line_options.generate_page_images = True
        self.pipe_line_options.generate_picture_images = True
        self.pipe_line_options.do_ocr = True
        self.pipe_line_options.ocr_options = ocr_options
        # self.pipe_line_options.ocr_options.lang = ["ko", 'en']
        # self.pipe_line_options.ocr_options.model_storage_directory = "./.EasyOCR/model"
        # self.pipe_line_options.ocr_options.force_full_page_ocr = True
        # ocr_options = TesseractOcrOptions()
        # ocr_options.lang = ['kor', 'kor_vert', 'eng', 'jpn', 'jpn_vert']
        # ocr_options.path = './.tesseract/tessdata'
        # self.pipe_line_options.ocr_options = ocr_options
        # self.pipe_line_options.artifacts_path = Path("/models/")
        self.pipe_line_options.do_table_structure = True
        self.pipe_line_options.images_scale = 2
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

        toc_api_base_url = os.getenv("TOC_API_BASE_URL", "https://genos.genon.ai:3443/api/gateway/rep/serving/630/v1/chat/completions")
        metadata_api_base_url = os.getenv("METADATA_API_BASE_URL", "https://openrouter.ai/api/v1/chat/completions")
        toc_api_key = os.getenv("TOC_API_KEY",
                                "dbc2f782d87c4b9c8d44930acd1342fe")
        metadata_api_key = os.getenv("METADATA_API_KEY",
                                     "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")
        toc_model = os.getenv("TOC_MODEL", "google/gemini-3-pro-preview")
        metadata_model = os.getenv("METADATA_MODEL", "google/gemini-3-flash-preview")
        image_description_api_base_url = os.getenv("IMAGE_DESCRIPTION_API_BASE_URL",
                                                   "https://openrouter.ai/api/v1/chat/completions")
        image_description_api_key = os.getenv("IMAGE_DESCRIPTION_API_KEY",
                                              "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")
        image_description_model = os.getenv("IMAGE_DESCRIPTION_MODEL", "openai/gpt-4.1")

        self.llm = llm_serving(serving_id=640, bearer_token="bf9ccee85f024d3180de25da96885a3e")

        # enrichment 옵션 설정
        self.enrichment_options = DataEnrichmentOptions(
            do_toc_enrichment=True,
            toc_doc_type="law",
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

        # Image Description
        self.pipe_line_options.do_picture_description = True
        # 원격 서비스 연결 활성화
        self.pipe_line_options.enable_remote_services = True

        self.pipe_line_options.picture_description_options = PictureDescriptionApiOptions(
            url=image_description_api_base_url,
            params=dict(model=image_description_model, max_tokens=5000, temperature=0.1),  # 원하는 모델 ID
            headers={
                "Authorization": "Bearer " + image_description_api_key
            },
            prompt="",
            timeout=60,
            scale=4.0,
            picture_area_threshold=0.001
        )

    def _create_converters(self):
        """컨버터들을 생성하는 헬퍼 메서드"""
        self.converter = DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(
                    pipeline_options=self.pipe_line_options,
                    backend=PyPdfiumDocumentBackend,
                ),
            }
        )

        self.second_converter = DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(
                    pipeline_options=self.pipe_line_options,
                    backend=DoclingParseV4DocumentBackend,
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

    def set_content_layer_to_body(self, document: DoclingDocument):
        """
        content_layer가 furniture인 아이템들을 body로 변경하는 메서드
        body로 변경하여 화면에 표시되도록 함
        """
        for item, level in document.iterate_items(included_content_layers=["furniture"]):
            if hasattr(item, 'content_layer') and item.content_layer == "furniture":
                item.content_layer = "body"  # body로 변경하여 화면에 표시되도록 함

    def load_documents_with_docling(self, file_path: str, **kwargs: dict) -> DoclingDocument:
        # kwargs에서 save_images 값을 가져와서 옵션 업데이트
        save_images = kwargs.get('save_images', True)
        include_wmf = kwargs.get('include_wmf', False)

        self._create_converters()

        try:
            conv_result: ConversionResult = self.converter.convert(file_path, raises_on_error=True)
            print('PyPdfiumDocumentBackend로 컨버트합니다.')
        except Exception as e:
            print('PyPdfiumDocumentBackend에서 오류가 발생했습니다:', e)
            print('DoclingParseV4DocumentBackend로 재시도합니다.')
            conv_result: ConversionResult = self.second_converter.convert(file_path, raises_on_error=True)
            print('DoclingParseV4DocumentBackend로 컨버트 성공')
        if self.toc_on != 1:
            self.set_content_layer_to_body(conv_result.document)
        return conv_result.document

    def load_documents_with_docling_ocr(self, file_path: str, **kwargs: dict) -> DoclingDocument:
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
            conv_result: ConversionResult = self.ocr_converter.convert(file_path, raises_on_error=True)
        except Exception as e:
            conv_result: ConversionResult = self.ocr_second_converter.convert(file_path, raises_on_error=True)
        if self.toc_on != 1:
            self.set_content_layer_to_body(conv_result.document)
        return conv_result.document

    def load_documents(self, file_path: str, **kwargs) -> DoclingDocument:
        # 파일 확장자가 hwp면 HwpLoader 사용해서 PDF 변환 후 넘김
        if file_path.endswith('.hwp'):
            hwp_loader = HwpLoader(file_path)
            hwp_loader.load()  # PDF 변환 수행
            file_path = hwp_loader.pdf_path  # PDF 경로 사용
        return self.load_documents_with_docling(file_path, **kwargs)

    def split_documents(self, documents: DoclingDocument, subject, legal_option, image_option, **kwargs: dict) -> List[
        DocChunk]:
        chunk_max_tokens = kwargs.get('chunk_max_tokens', 1000)
        chunker: GenosBucketChunker = GenosBucketChunker(
            max_tokens=chunk_max_tokens,
            merge_peers=True,
            subject=subject,
            legal_option=legal_option,
            image_option=image_option,
            llm=self.llm
        )

        chunks: List[DocChunk] = list(chunker.chunk(dl_doc=documents, **kwargs))
        for chunk in chunks:
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

    def enrichment(self, document: DoclingDocument, enrich_on, **kwargs: dict) -> DoclingDocument:
        import copy
        origin_document = copy.deepcopy(document)
        # 새로운 enriched result 받기
        document = enrich_document(document, self.enrichment_options)
        if origin_document == document and enrich_on == 1:
            raise ValueError("Document enrichment failed. No changes detected.")
        return document

    async def compose_vectors(self, document: DoclingDocument, chunks: List[DocChunk], file_path: str, request: Request,
                              **kwargs: dict) -> \
            list[dict]:
        try:
            file_name = os.path.basename(kwargs.get("file_name","")).replace('.pdf', "")
            if '+' in file_name:
                main_name, sub_name = file_name.split('+', 1)  # maxsplit=1로 최대 2개로만 분할
                prefix = f"{main_name} - {sub_name}\n"
            else:
                prefix = f"{file_name}\n"
        except Exception as e:
            print(f'파일명 추출 중 오류 발생:{e}')
            prefix = ""
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

        # kwargs에서 부록 정보 추출 !! appendix feature (2025-09-30, geonhee kim) !!
        appendix_info = kwargs.get('appendix', '')
        appendix_list = []
        if isinstance(appendix_info, str):
            appendix_list = [item.strip() for item in json.loads(appendix_info) if
                             item.strip()] if appendix_info else []
        elif isinstance(appendix_info, list):
            appendix_list = appendix_info
        else:
            appendix_list = []

        global_metadata = dict(
            n_chunk_of_doc=len(chunks),
            n_page=document.num_pages(),
            reg_date=datetime.now().isoformat(timespec='seconds') + 'Z',
            created_date=created_date,
            title=title
        )

        current_page = None
        chunk_index_on_page = 0
        vectors = []
        upload_tasks = []
        for chunk_idx, chunk in enumerate(chunks):
            chunk_page = chunk.meta.doc_items[0].prov[0].page_no
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
                      .set_global_metadata(**chunk_global_metadata)  # !! appendix feature (2025-09-30, geonhee kim) !!
                      .set_chunk_bboxes(chunk.meta.doc_items, document)
                      .set_media_files(chunk.meta.doc_items)
                      ).build()
            vector.text = prefix + vector.text
            vectors.append(vector)

            chunk_index_on_page += 1
            file_list = self.get_media_files(chunk.meta.doc_items)
        #     upload_tasks.append(asyncio.create_task(
        #         upload_files(file_list, request=request)
        #     ))

        # if upload_tasks:
        #     await asyncio.gather(*upload_tasks)

        return vectors

    def get_media_files(self, doc_items: list):
        temp_list = []
        for item in doc_items:
            if isinstance(item, PictureItem):
                path = str(item.image.uri)
                name = path.rsplit("/", 1)[-1]
                temp_list.append({'path': path, 'name': name})
        return temp_list

    def check_glyph_text(self, text: str, threshold: int = 1) -> bool:
        """텍스트에 GLYPH 항목이 있는지 확인하는 메서드"""
        if not text:
            return False

        # GLYPH 항목이 있는지 정규식으로 확인
        matches = re.findall(r'GLYPH\w*', text)
        if len(matches) >= threshold:
            # print(f"Text has glyphs. len(matches): {len(matches)}. ")
            return True

        return False

    def check_glyphs(self, document: DoclingDocument) -> bool:
        """문서에 글리프가 있는지 확인하는 메서드"""
        for item, level in document.iterate_items():
            if isinstance(item, TextItem) and hasattr(item, 'prov') and item.prov:
                page_no = item.prov[0].page_no
                # page_texts += item.text

                # GLYPH 항목이 있는지 확인. 정규식사용
                matches = re.findall(r'GLYPH\w*', item.text)
                if len(matches) > 10:
                    # print(f"Document has glyphs on page {page_no}. len(matches): {len(matches)}. ")
                    return True

        return False

    def check_appendix_keywords(self, content: str,
                                appendix_list: list) -> str:  # !! appendix feature (2025-09-30, geonhee kim) !!
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
        try:
            import fitz
            import grpc
            import docling.models.ocr_pb2 as ocr_pb2
            import docling.models.ocr_pb2_grpc as ocr_pb2_grpc
            import itertools

            grpc_server_count = self.ocr_pipe_line_options.ocr_options.grpc_server_count

            PORTS = [50051 + i for i in range(grpc_server_count)]
            channels = [grpc.insecure_channel(f"localhost:{p}") for p in PORTS]
            stubs = [(ocr_pb2_grpc.OCRServiceStub(ch), p) for ch, p in zip(channels, PORTS)]
            rr = itertools.cycle(stubs)

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

                    # # Provenance 정보에서 위치 정보 추출
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

                    # gRPC 서버와 연결
                    # channel = grpc.insecure_channel('localhost:50051')
                    # stub = ocr_pb2_grpc.OCRServiceStub(channel)

                    # # OCR 요청: 이미지 데이터를 바이너리로 전송
                    # response = stub.PerformOCR(ocr_pb2.OCRRequest(image_data=img_data))

                    req = ocr_pb2.OCRRequest(image_data=img_data)
                    stub, port = next(rr)  # 라운드 로빈 방식으로 스텁 선택
                    response = stub.PerformOCR(req)

                    cell.text = ""
                    for result in response.results:
                        if len(cell.text) > 0:
                            cell.text += " "
                        cell.text += result.text if result else ""
        except grpc.RpcError as e:
            pass

        return document

    def extract_subject(self, file_path):
        import fitz
        system_prompt = '입력한 문서에 주제를 알려주세요. 주제는 300자 이내로 문서의 모든 내용을 포괄하는 주제를 작성하세요. 주제를 작성할 때에는 해당 문서가 어디에서 제작되었는지를 반드시 포함하세요.'
        
        # HWP 파일인 경우 HwpLoader로 PDF 변환 후 경로 사용 (load_documents와 동일한 방식)
        if file_path.endswith('.hwp'):
            try:
                hwp_loader = HwpLoader(file_path)
                hwp_loader.load()  # PDF 변환 수행
                file_path = hwp_loader.pdf_path  # PDF 경로 사용
                if not file_path or not os.path.exists(file_path):
                    raise RuntimeError(f"PDF 변환 실패: {file_path}")
            except Exception as e:
                print(f"HWP 파일 처리 중 오류 발생: {e}")
                return ""
        
        docs = fitz.open(file_path)
        full_text = ""
        for page in docs:
            try:
                text = page.get_text("text")
                if text:
                    full_text += text + "\n"
            except Exception as e:
                print(f"페이지 텍스트 추출 중 오류 발생: {e}")
                continue
        docs.close()

        subject = self.llm.call(system_prompt=system_prompt, question=full_text)
        subject = subject if subject else ""
        return subject

    async def __call__(self, request: Request, file_path: str, **kwargs: dict):
        # kwargs['save_images'] = True    # 이미지 처리
        # kwargs['include_wmf'] = True   # wmf 처리
        # Load_Doc -> Enrich_Doc -> Split_Doc -> Compose_vector


        toc_user_prompt = kwargs.get('toc_user_prompt', "")
        toc_system_prompt = kwargs.get('toc_system_prompt', "")

        if toc_user_prompt:
            self.enrichment_options.toc_user_prompt = toc_user_prompt
        if toc_system_prompt:
            self.enrichment_options.toc_system_prompt = toc_system_prompt

        toc_on = kwargs.get('toc_on', 1)
        self.toc_on = toc_on

        image_description_on = kwargs.get('image_description_on', 0)
        self.image_description_on = image_description_on

        enhanced_image_description_on = kwargs.get('enhanced_image_description_on', 0)
        self.enhanced_image_description_on = enhanced_image_description_on

        chunk_max_tokens = kwargs.get('chunk_max_tokens', 0)
        self.chunk_max_tokens = chunk_max_tokens

        image_option = image_description_on | enhanced_image_description_on
        enrich_on = toc_on | image_description_on | enhanced_image_description_on

        print("Image Option:", image_option, "ToC Option:", toc_on, "Enrich Option:", enrich_on, "Chunk Max Tokens:",
              chunk_max_tokens)

        # ---------------------------------------------------------------------------
        if toc_on == 1:
            self.enrichment_options.do_toc_enrichment = True
            print('toc 기능을 활성화합니다.')
        else:
            self.enrichment_options.do_toc_enrichment = False

        self.subject = self.extract_subject(file_path=file_path)
        self.pipe_line_options.picture_description_options.prompt = self.pipe_line_options.picture_description_options.prompt + f'문서의 주제: {self.subject}'
        print('뽑힌 주제:', self.subject)

        if enhanced_image_description_on == 1:
            self.pipe_line_options.picture_description_options.prompt = enhanced_image_description_prompt + f'문서의 주제: {self.subject}'
            print("enhanced_image_description 기능을 활성화합니다.")
        elif image_description_on == 1:
            self.pipe_line_options.picture_description_options.prompt = image_description_prompt + f'문서의 주제: {self.subject}'
            print("image_description 기능을 활성화합니다.")

        if image_option == 1:
            self.pipe_line_options.do_picture_description = True
            print('이미지 분석 기능을 활성화합니다.')
            self.pipe_line_options.enable_remote_services = True
        else:
            self.pipe_line_options.do_picture_description = False

        # ---------------------------------------------------------------------------

        document: DoclingDocument = self.load_documents(file_path, **kwargs)

        self.test = document

        if image_description_on != 1:

            if not check_document(document, self.enrichment_options) or self.check_glyphs(document):
                # OCR이 필요하다고 판단되면 OCR 수행
                document: DoclingDocument = self.load_documents_with_docling_ocr(file_path, **kwargs)

            # 글리프 깨진 텍스트가 있는 테이블에 대해서만 OCR 수행 (청크토큰 8k이상 발생 방지)
            # document: DoclingDocument = self.ocr_all_table_cells(document, file_path)

        for i, item in enumerate(document.texts):
            if item.label == DocItemLabel.PARAGRAPH:
                document.texts[i].label = DocItemLabel.TEXT

        output_path, output_file = os.path.split(file_path)
        filename, _ = os.path.splitext(output_file)
        artifacts_dir = Path(f"{output_path}/{filename}")
        if artifacts_dir.is_absolute():
            reference_path = None
        else:
            reference_path = artifacts_dir.parent

        document = document._with_pictures_refs(image_dir=artifacts_dir, reference_path=reference_path, page_no=None)

        document = self.enrichment(document, enrich_on, **kwargs)

        has_text_items = False
        for item, _ in document.iterate_items():
            if (isinstance(item,
                           (TextItem, ListItem, CodeItem, SectionHeaderItem)) and item.text and item.text.strip()) or (
                    isinstance(item, TableItem) and item.data and len(item.data.table_cells) == 0):
                has_text_items = True
                break

        if has_text_items:
            # Extract Chunk from DoclingDocument
            chunks: List[DocChunk] = self.split_documents(document, self.subject, toc_on, image_option,
                                                          **kwargs)
        else:
            # text가 있는 item이 없을 때 document에 임의의 text item 추가
            from docling_core.types.doc import ProvenanceItem

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
            chunks: List[DocChunk] = self.split_documents(document, self.subject, toc_on, image_option,
                                                          **kwargs)

        # await assert_cancelled(request)

        vectors = []
        if len(chunks) >= 1:
            vectors: list[dict] = await self.compose_vectors(document, chunks, file_path, request, **kwargs)


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


# 규정용 프롬프트
# toc_system_prompt = "당신은 **목차**를 생성하는 전문가입니다."
# toc_user_prompt = """
# You are an expert at generating table of contents (목차) from Korean documents, particularly regulatory documents, terms of service, contracts, and mixed-format documents that combine both formal regulatory structures and general section headers.
#
# Here is the document you need to analyze:
#
# <document>
# {raw_text}
# </document>
#
# Your task is to extract and organize all structural elements from this document into a hierarchical table of contents. Korean documents often have mixed structures where some chapters follow formal regulatory patterns (장/절/관/조) while others use general section numbering and headers.
#
# ## Extraction Guidelines:
#
# 1. **Document Structure Assessment**: Identify structural patterns present in the document:
#     - Formal regulatory patterns: 제x장 (chapters), 제x절 (sections), 제x관 (subsections), 제x조 (articles)
#     - General section patterns: numbered headers (1., 2., etc.), lettered headers (가., 나., etc.), or other section titles
#     - Mixed patterns where different parts use different formatting
#
# 2. **Title Identification**: Locate the main document title, typically found at the beginning or before the first chapter/section.
#
# 3. **Hierarchical Element Extraction**: Work through the document systematically:
#     - Extract each main chapter/section (regardless of whether they use 제x장 or general numbering)
#     - Include subsections (절, or numbered subsections) under their parent sections
#     - Identify articles/items (조, or lettered/numbered items) under their parent sections
#     - List appendices and attachments (부칙, 별지, 별표, etc.)
#     - Use the exact title text as it appears in the document for each element
#
# 4. **Pattern Recognition**: Apply appropriate extraction methods:
#     - For regulatory patterns: Look for "제x장", "제x절", "제x조" (with or without spaces)
#     - For general patterns: Look for "1.", "가.", section titles, and other standard formatting
#     - Handle both Korean and Arabic numerals
#
# 5. **Verification**: Ensure all elements are captured in document order with no duplicates and logical hierarchy.
#
# ## Output Requirements:
#
# Format your table of contents as follows:
# - First line: `TITLE:<document title>`
# - Subsequent lines: Use hierarchical decimal numbering (1, 1.1, 1.1.1, etc.) followed by the original title text
# - Format: `number. ` + space + original title exactly as it appears in the document
# - Maintain the document's logical hierarchy
# - Include appendices, attachments, and addenda as separate top-level items
#
# ## Important Guidelines:
#
# - Extract titles exactly as they appear in the original text
# - Do not include explanatory content, only the structural titles
# - Handle both formal regulatory structures and general section headers
# - Remove duplicates but ensure no structural elements are missed
# - For mixed documents, adapt the extraction method to match each section's formatting style
# - Include 부칙 (addenda) with their dates when present
# - Process attachments like <별지>, <별표> as separate sections with their own sub-items
# - Output only the final table of contents without analysis or explanation
# """

# analytics 까지 return 하는 prompt
# toc_system_prompt = "You are an expert at generating table of contents (목차) from Korean documents. You specialize in regulatory documents, terms of service, contracts, and mixed-format documents that combine formal regulatory structures with general section headers."
# toc_user_prompt = """
# Here is the Korean document you need to analyze:
#
# <document>
# {raw_text}
# </document>
#
# Your task is to extract and organize all structural elements from this document into a hierarchical table of contents. Korean documents often have mixed structures where some sections follow formal regulatory patterns (제x장/절/관/조) while others use general section numbering and headers.
#
# ## Analysis Process
#
# Before generating the final table of contents, work through the document systematically in `<analysis>` tags. It's OK for this section to be quite long. Follow these steps:
#
# 1. **Document Title Extraction**: Quote the main document title exactly as it appears at the beginning of the document.
#
# 2. **Structural Marker Identification**: Scan through the document and quote all the key structural markers you find, such as:
#    - Formal regulatory patterns: 제x장, 제x절, 제x관, 제x조
#    - General section patterns: numbered headers (1., 2., etc.), lettered headers (가., 나., etc.)
#    - Special sections: 부칙, 별지, 별표, etc.
#
# 3. **Systematic Section Extraction**: Work through the document from beginning to end, extracting each structural element in order:
#    - For each main section, quote the exact title as it appears
#    - For each subsection, quote the exact title and note which main section it belongs under
#    - For each article/item, quote the exact title and note its parent section
#    - Include any appendices, attachments, and addenda
#
# 4. **Hierarchy Building**: For each extracted element, explicitly note:
#    - What level it should be at (main section, subsection, sub-subsection, etc.)
#    - What its parent section is (if any)
#    - What numbering it should receive in the final TOC (1., 1.1., 1.1.1., etc.)
#
# 5. **Structure Verification**: Review your extracted elements to ensure:
#    - All structural elements are captured in document order
#    - The hierarchy makes logical sense
#    - No elements are duplicated or missed
#
# ## Output Requirements
#
# After your analysis, generate the table of contents with this exact format:
#
# ```
# <toc>
# TITLE:<document title>
# 1. <first main section title>
# 1.1. <first subsection title>
# 1.1.1. <first sub-subsection title>
# 1.2. <second subsection title>
# 2. <second main section title>
# 2.1. <subsection under second main section>
# 3. <third main section title>
# </toc>
# ```
#
# ## Formatting Guidelines
#
# - Start with `TITLE:` followed by the document title
# - Use hierarchical decimal numbering (1, 1.1, 1.1.1, etc.)
# - Follow each number with a space and the original title exactly as it appears
# - Maintain the document's logical hierarchy
# - Include appendices, attachments, and addenda as separate top-level items
# - Extract titles exactly as they appear - do not include explanatory content
# - Handle both formal regulatory structures and general section headers
# - Wrap the entire table of contents in `<toc></toc>` tags
# """

# yongjun prompt
# toc_system_prompt = f"""
# 너는 세계 최고의 문서 구조 분석기이다. 아래에 300페이지 분량의 보험 약관 전체 텍스트가 입력된다.
#
# [임무]
# 위 문서의 '전체' 목차를 사용자가 제공한 [출력 예시]와 '완벽하게 동일한' 계층 구조 및 번호 매기기 방식으로 추출하라.
#
# [규칙]
# 1.  문서의 제목은 반드시 첫 줄에 `TITLE:` 접두사를 붙여 작성한다.
# 2.  "약관 이용가이드 북", "약관 요약서", "보험약관", "특약 약관", "부록" 등 모든 섹션을 포함해야 한다.
# 3.  모든 하위 계층(예: "제 1관", "제1조", "1.", "가.", "(1)")을 인식하여, `1.`, `1.1.`, `1.2.`, `1.4.1.`, `1.4.2.`와 같은 계층적 번호(hierarchical numbering)를 생성하여 제목 앞에 붙여야 한다.
# 4.  계층적 번호 뒤에는 한 칸을 띄고, 원문 목차의 제목(예: "01 보험 약관이란?", "1. 보험금 지급사유...")을 그대로 기재한다.
# 5.  부록의 "■ 법률명" 및 그 하위 조항들("제N조")도 모두 포함하여 계층적 번호로 변환해야 한다.
# 6.  법령(예: ○○법, ○○시행령 등) 섹션을 처리할 때는 제N조까지만 목차에 포함하고,조 아래 단계(항, 호, 목 등)는 절대로 추출하지 마라.
# 7.  원문에 없는 내용은 절대 생성하지 마라.
# 8.  누락되는 항목이 단 하나도 없어야 한다.
# 9.  다른 말은 하지 않고 오직 [출력 형식]에 맞춘 목차 전문만을 추출하여 답변한다.
#
# [출력 요구사항 (Output Requirements)]
# - 문서의 최종 목차는 다음 형식에 맞춰 작성해야 한다.
# - 첫 줄: TITLE:<문서 제목>
# - 이후 줄들: 계층형 번호(예: 1., 1.1., 1.1.1. 등)를 사용하고, 그 뒤에 원문에 나온 제목을 그대로 기재한다.
# - 형식: 번호. + 공백 + 원문 제목(원문 그대로)
# - 문서의 논리적 계층 구조를 유지해야 한다.
# - 부록, 별지, 별표, 부칙 등은 각각 독립된 최상위 항목으로 포함해야 한다.
#
# [중요 가이드라인 (Important Guidelines)]
# - 제목은 반드시 원문에 나온 그대로 추출해야 한다.
# - 설명, 해설 등은 포함하지 말고, 구조적 제목만 출력해야 한다.
# - 법령식 구조(장/절/관/조)와 일반 번호 구조(1., 1.1., 가., (1) 등)를 모두 처리해야 한다.
# - 중복된 항목은 제거하되, 문서에 존재하는 구조 요소는 단 하나도 빠뜨려서는 안 된다.
# - 서로 다른 형식이 섞여 있는 문서는 각 형식에 맞춰 적절히 구조를 인식해야 한다.
# - 부칙이 있을 경우 시행일 등 날짜가 포함된 제목도 그대로 포함해야 한다.
# - <별지>, <별표> 등의 첨부 문서도 별도의 섹션으로 처리하고, 그 이하로 섹션을 나누지 않는다. (별지, 별표 등은 하나의 섹션으로 처리)
#
# [출력 예시]
# TITLE:보험약관 [2025년 9월 1일 개정약관]
# 1. 약관 이용가이드 북
# 1.1. 01 보험 약관이란?
# 1.2. 02 한 눈에 보는 약관의 구성
# 1.3. 03 QR 코드를 통한 편리한 정보 이용
# 1.4. 04 약관의 핵심 체크항목 쉽게 찾기 (주계약 약관 기준)
# 1.4.1. 1. 보험금 지급사유 및 지급 제한 사유
# 1.4.2. 2. 청약철회
# 1.4.3. 3. 계약취소
# 1.4.4. 4. 계약무효
# 1.4.5. 5. 계약 전 알릴의무 및 위반효과
# 2. 약관 요약서
# 2.1. (요약서 내용...)
# ...
# """
#
# toc_user_prompt = """
# [문서 전문]
# {raw_text}
# """

image_description_prompt = """
당신은 RAG(검색 증강 생성) 시스템을 위한 **전문 데이터 분석가이자 이미지 설명 전문가**입니다. 
당신의 목표는 주어진 문서의 주제를 참고하여 이미지를 보지 못한 사람(혹은 검색 알고리즘)이 이미지의 모든 정보를 완벽하게 이해하고 검색할 수 있도록 상세하고 구조화된 텍스트를 생성하는 것입니다.

제공된 이미지를 분석하여 아래의 [지침]에 따라 상세한 설명을 작성해 주세요.

### [지침]

1. **전체 요약 (General Summary)**:
   - 이미지의 주제와 핵심 메시지를 설명하세요.
   - 이미지의 종류(예: 스크린샷, 막대 그래프, 사진, 도표 등)를 명시하세요.


### [출력 형식]

# 이미지 제목/주제
(이미지 전체 요약 내용)


"""
enhanced_image_description_prompt = """
당신은 RAG(검색 증강 생성) 시스템을 위한 **전문 데이터 분석가이자 이미지 설명 전문가**입니다. 
당신의 목표는 주어진 문서의 주제를 참고하여 이미지를 보지 못한 사람(혹은 검색 알고리즘)이 이미지의 모든 정보를 완벽하게 이해하고 검색할 수 있도록 상세하고 구조화된 텍스트를 생성하는 것입니다.

제공된 이미지를 분석하여 아래의 [지침]에 따라 상세한 설명을 작성해 주세요.

### [지침]

1. **전체 요약 (General Summary)**:
   - 이미지의 주제와 핵심 메시지를 설명하세요.
   - 이미지의 종류(예: 스크린샷, 막대 그래프, 사진, 도표 등)를 명시하세요.


2. **차트/테이블 데이터 변환 (Chart to Markdown)**: 
   - 이미지에 차트(막대, 선, 파이 등)나 표가 포함되어 있다면, 이를 **반드시 마크다운 테이블(Markdown Table)** 형식으로 변환하세요.
   - 수치가 명시되어 있지 않은 경우, 축의 눈금을 보고 최대한 정확한 근사치를 추정하여 기입하세요.
   - 데이터 테이블 작성 후, 해당 데이터가 의미하는 바(추세, 최고/최저점 등)를 간략히 분석하여 덧붙이세요.

### [출력 형식]

# 이미지 제목/주제
(이미지 전체 요약 내용)


# 데이터 구조화 (차트/표)
(차트가 있을 경우만 작성, 없을 경우 '해당 없음' 표기)

| 항목 (X축) | 값 (Y축) | 비고 |
| :--- | :--- | :--- |
| 데이터 1 | 100 | ... |
| 데이터 2 | 200 | ... |

"""
toc_system_prompt = "당신은 **목차**를 생성하는 전문가입니다."
toc_user_prompt = """
You are an expert at extracting the Table of Contents (TOC) from Korean insurance policy documents.

Here is the document you need to analyze:

<document>
{raw_text}
</document>

Your task is to extract the structural hierarchy of the document. The user explicitly requests to **IGNORE "Section (관)"** levels entirely to simplify the structure.

## Targeted Hierarchy Levels:

1. **Special Term (특약)**: The title of the specific special contract. This is the **Top Level (1, 2, 3...)**.
2. **Part (편)**: Major divisions within a special term (e.g., "제1편"). This is the **Second Level (1.1, 1.2...)**.
3. **Article (조)**: The individual clauses (e.g., "제1조"). This level follows immediately after "Part" or "Special Term".
4. **Attachments (별표/별지)**: Tables or appendices. Treat these as items under the Special Term.

## Extraction Guidelines:

1. **Strict Hierarchy (Skip 'Section/관')**:
    - **RULE**: Do NOT extract "Section (제x관)" as a hierarchy level. Treat it as invisible.
    - **Structure A (With Part)**: Special Term(1) -> Part(1.1) -> Article(1.1.1)
    - **Structure B (No Part)**: Special Term(1) -> Article(1.1)
    - Even if the text says "제1편 -> 제1관 -> 제1조", your output must be **"제1편 -> 제1조"**.

2. **Keyword Identification**:
    - **Level 1 (Special Term)**: Titles ending in "약관" or "특약" that appear as main headings.
    - **Level 2 (Part)**: "제1편", "제2편"... (Optional)
    - **Level 3 (Article)**: "제1조(Title)", "제2조(Title)"...
    - **Attachments**: "별표", "별지"...
    - **IGNORE**: "제1관", "제2관" etc.

3. **Content Exclusion**:
    - Do NOT extract content *inside* the articles (e.g., do not extract "1.", "①", "가.").
    - Stop strictly at the "Article (조)" title level.

## Output Requirements

After your analysis, generate the table of contents with this exact format:

```
<toc>
TITLE:<document title>
1. <first main section title>
1.1. <first subsection title>
1.1.1. <first sub-subsection title>
1.2. <second subsection title>
2. <second main section title>
2.1. <subsection under second main section>
3. <third main section title>
</toc>
```

## Formatting Guidelines

- Start with `TITLE:` followed by the document title
- Use hierarchical decimal numbering (1, 1.1, 1.1.1, etc.)
- Follow each number with a space and the original title exactly as it appears
- Maintain the document's logical hierarchy
- Include appendices, attachments, and addenda as separate top-level items
- Extract titles exactly as they appear - do not include explanatory content
- Handle both formal regulatory structures and general section headers
- Wrap the entire table of contents in `<toc></toc>` tags

**Example Output Format:**
<toc>
TITLE:<Overall Document Title>
1. 무배당 운전자 보험 특별약관
1.1. 제1편 일반조항
1.1.1. 제1조(보험금의 종류 및 지급사유)
1.1.2. 제2조(보험금 지급에 관한 세부규정)
1.2. 별표 1 상해분류표
...
</toc>

## Important Rules:
- **Focus strictly on [특약 -> 편 -> 조]**.
- Ignore general numbering like "1.", "2." or "가.", "나." unless they constitute a "Special Term" title.
- Do not summarize content; extract titles exactly.
- Ensure "별표" (Appendices) are included in the hierarchy.
"""