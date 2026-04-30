import re
from collections import defaultdict
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
import json

import asyncio
from fastapi import Request
from langchain_core.documents import Document
from docling_core.types import DoclingDocument
from docling_core.types.doc.labels import DocItemLabel
from docling_core.transforms.chunker import DocChunk
from docling_core.types.doc import PictureItem

try:
    from genos_utils import upload_files
except ImportError:
    upload_files = None


class GenOSVectorMeta(BaseModel):
    class Config:
        extra = "allow"

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
        match_full = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", date_text)
        if match_full:
            year, month, day = match_full.groups()
            try:
                # 유효한 날짜인지 검증
                datetime(int(year), int(month), int(day))
                return int(f"{year}{month.zfill(2)}{day.zfill(2)}")
            except ValueError:
                pass

        # YYYY-MM 형식 매칭 (일자는 01로 설정)
        match_month = re.match(r"^(\d{4})-(\d{1,2})$", date_text)
        if match_month:
            year, month = match_month.groups()
            try:
                # 유효한 월인지 검증
                datetime(int(year), int(month), 1)
                return int(f"{year}{month.zfill(2)}01")
            except ValueError:
                pass

        # YYYY 형식 매칭 (월일은 0101로 설정)
        match_year = re.match(r"^(\d{4})$", date_text)
        if match_year:
            year = match_year.group(1)
            try:
                datetime(int(year), 1, 1)
                return int(f"{year}0101")
            except ValueError:
                pass

        return 0

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
        chunk_bboxes = []
        for item in doc_items:
            for prov in item.prov:
                label = item.self_ref
                type_ = item.label
                size = document.pages.get(prov.page_no).size
                page_no = prov.page_no
                bbox = prov.bbox
                bbox_data = {
                    "l": bbox.l / size.width,
                    "t": bbox.t / size.height,
                    "r": bbox.r / size.width,
                    "b": bbox.b / size.height,
                    "coord_origin": bbox.coord_origin.value,
                }
                chunk_bboxes.append({"page": page_no, "bbox": bbox_data, "type": type_, "ref": label})
        self.e_page = max([bbox["page"] for bbox in chunk_bboxes]) if chunk_bboxes else None
        self.chunk_bboxes = json.dumps(chunk_bboxes)
        return self

    def set_media_files(self, doc_items: list) -> "GenOSVectorMetaBuilder":
        temp_list = []
        for item in doc_items:
            if isinstance(item, PictureItem):
                if item.image is None:
                    print("@@@@ item.image is None: pipeline_options - generate_picture_images False!!")
                    continue
                path = str(item.image.uri)
                name = path.rsplit("/", 1)[-1]
                temp_list.append({"name": name, "type": "image", "ref": item.self_ref})
        self.media_files = json.dumps(temp_list)
        return self

    def get_title(self, document):
        title = ""
        for item, _ in document.iterate_items():
            if hasattr(item, "label"):
                if item.label == DocItemLabel.TITLE:
                    title = item.text.strip() if item.text else ""
                    break

        return title

    def get_created_data(self, document: DoclingDocument):
        created_date = 0
        try:
            if (
                document.key_value_items
                and len(document.key_value_items) > 0
                and hasattr(document.key_value_items[0], "graph")
                and hasattr(document.key_value_items[0].graph, "cells")
                and len(document.key_value_items[0].graph.cells) > 1
            ):
                # 작성일 추출 (cells[1])
                date_text = document.key_value_items[0].graph.cells[1].text
                created_date = self.parse_created_date(date_text)
        except (AttributeError, IndexError) as e:
            pass

        return created_date

    def get_appendix_keywords(
        self, content: str, appendix_list: list
    ) -> str:  # !! appendix feature (2025-09-30, geonhee kim) !!
        if not content or not appendix_list:
            return ""

        matched_appendices = []

        # 1. Find appendix patterns in content first
        found_patterns = []

        # Complex patterns: 별지/별표/장부 + numbers (with hyphens, Roman numerals)
        # Updated regex to capture full patterns like "별지 제 Ⅰ -1 호 서식" by matching until closing delimiters
        content = re.sub(r"\s+", "", content)
        complex_patterns = re.findall(r"(별지|별표|장부)(?:제)?([^<>()\[\]]+?)(?=(?:호|서식)|[<>\)\]]|$)", content)
        for pattern_type, number in complex_patterns:
            found_patterns.extend(
                [
                    f"{pattern_type} {number}",
                    f"{pattern_type} 제{number}호",
                    f"{pattern_type}{number}",
                    f"{pattern_type}제{number}호",
                ]
            )

        # Standalone patterns: (별표), (별지), (장부)
        standalone_patterns = re.findall(r"[\(\[]+(별지|별표|장부)[\)\]]+", content)
        for pattern_type in set(standalone_patterns):
            found_patterns.extend(
                [
                    pattern_type,
                    f"{pattern_type}",
                ]
            )

        # 2. Check if found patterns match any appendix in the list
        for appendix in appendix_list:
            if not appendix or not isinstance(appendix, str):
                continue

            appendix_clean = appendix.replace(".pdf", "").lower().strip()

            # If any found pattern exists in appendix filename, it's a match
            for pattern in found_patterns:
                if pattern.lower().strip() in appendix_clean:
                    matched_appendices.append(appendix)
                    break  # Prevent duplicates

        return ", ".join(matched_appendices) if matched_appendices else ""

    def get_chunk_count(self, chunks: List[DocChunk]):
        page_chunk_counts = defaultdict(int)

        for chunk in chunks:
            page_chunk_counts[chunk.meta.doc_items[0].prov[0].page_no] += 1

        return page_chunk_counts

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
            appendix=self.appendix or "",  # !! appendix feature (2025-09-30, geonhee kim) !!
        )

    async def __call__(
        self, document: DoclingDocument, chunks: List[DocChunk], file_path: str, request: Request, **kwargs: dict
    ):
        title = self.get_title(document)
        created_date = self.get_created_data(document)
        page_chunk_counts = self.get_chunk_count(chunks)

        # kwargs에서 부록 정보 추출 !! appendix feature (2025-09-30, geonhee kim) !!
        appendix_info = kwargs.get("appendix", "")
        appendix_list = []
        if isinstance(appendix_info, str):
            appendix_list = (
                [item.strip() for item in json.loads(appendix_info) if item.strip()] if appendix_info else []
            )
        elif isinstance(appendix_info, list):
            appendix_list = appendix_info
        else:
            appendix_list = []

        global_metadata = dict(
            n_chunk_of_doc=len(chunks),
            n_page=document.num_pages(),
            reg_date=datetime.now().isoformat(timespec="seconds") + "Z",
            created_date=created_date,
            title=title,
        )

        current_page = None
        chunk_index_on_page = 0
        vectors = []
        upload_tasks = []
        for chunk_idx, chunk in enumerate(chunks):
            chunk_page = chunk.meta.doc_items[0].prov[0].page_no
            # header 앞에 헤더 마커 추가 (HEADER: )
            headers_text = "HEADER: " + ", ".join(chunk.meta.headings) + "\n" if chunk.meta.headings else ""
            content = headers_text + chunk.text

            # appendix 추출 !! appendix feature (2025-09-30, geonhee kim) !!
            matched_appendices = self.get_appendix_keywords(content, appendix_list)
            # print(appendix_list, matched_appendices)
            chunk_global_metadata = global_metadata.copy()
            chunk_global_metadata["appendix"] = matched_appendices  # Only matched ones
            ###

            if chunk_page != current_page:
                current_page = chunk_page
                chunk_index_on_page = 0

            vector = (
                GenOSVectorMetaBuilder()
                .set_text(content)
                .set_page_info(chunk_page, chunk_index_on_page, page_chunk_counts[chunk_page])
                .set_chunk_index(chunk_idx)
                .set_global_metadata(**chunk_global_metadata)  #!! appendix feature (2025-09-30, geonhee kim) !!
                .set_chunk_bboxes(chunk.meta.doc_items, document)
                .set_media_files(chunk.meta.doc_items)
            ).build()
            vectors.append(vector)

            chunk_index_on_page += 1
            if upload_files:
                file_list = self.get_media_files(chunk.meta.doc_items)
                upload_tasks.append(asyncio.create_task(upload_files(file_list, request=request)))

        if upload_tasks:
            await asyncio.gather(*upload_tasks)

        return vectors
