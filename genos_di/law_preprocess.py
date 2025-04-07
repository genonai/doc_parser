# 수정된 법령 문서 전처리기
import json
import re
import os
import fitz
import uuid
from pathlib import Path
from typing import Dict
from collections import defaultdict
from datetime import datetime
from fastapi import Request

from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_core.documents import Document
from langchain.document_loaders import (
    PyMuPDFLoader,
    UnstructuredWordDocumentLoader,
    UnstructuredPowerPointLoader,
    UnstructuredImageLoader,
    TextLoader,
    JSONLoader,
    UnstructuredFileLoader
)

class DocumentProcessor:
    def __init__(self):
        self.page_chunk_counts = defaultdict(int)

    def get_loader(self, file_path: str):
        ext = os.path.splitext(file_path)[-1].lower()
        if ext == '.pdf':
            return PyMuPDFLoader(file_path)
        elif ext == '.json':
            # JSON 파일을 위한 특별 처리
            return JSONLoader(
                file_path=file_path,
                jq_schema=".",
                text_content=False
            )
        elif ext == '.txt':
            return TextLoader(file_path)
        else:
            return UnstructuredFileLoader(file_path)

    def load_documents(self, file_path: str, **kwargs: dict) -> list[Document]:
        loader = self.get_loader(file_path)
        documents = loader.load()
        
        # page 정보가 없는 문서에 대해 page 메타데이터 추가
        for i, doc in enumerate(documents):
            if 'page' not in doc.metadata:
                doc.metadata['page'] = i

            if isinstance(doc.page_content, str):
                try:
                    doc.page_content = bytes(doc.page_content, 'utf-8').decode('unicode_escape')
                except Exception as e:
                    print(f"[WARN] decoding failed: {e}")    
        return documents

    def split_documents(self, documents, **kwargs: dict) -> list[Document]:
        text_splitter = RecursiveCharacterTextSplitter(**kwargs)
        chunks = text_splitter.split_documents(documents)
        chunks = [chunk for chunk in chunks if chunk.page_content]
        if not chunks:
            raise Exception('Empty document')
        
        # page 정보가 없는 청크에 대해 page 메타데이터 추가
        for i, chunk in enumerate(chunks):
            if 'page' not in chunk.metadata:
                chunk.metadata['page'] = 0
                
        for chunk in chunks:
            self.page_chunk_counts[chunk.metadata['page']] += 1
        return chunks

    def compose_vectors(self, chunks: list[Document], file_path: str, **kwargs: dict) -> list[dict]:
        pdf_path = file_path.replace('.hwp', '.pdf').replace('.txt', '.pdf').replace('.json', '.pdf')
        #pdf_path = str(file_path).replace('.hwp', '.pdf').replace('.txt', '.pdf').replace('.json', '.pdf')

        has_pdf = os.path.exists(pdf_path)
        
        if has_pdf:
            doc = fitz.open(pdf_path)
            n_page = doc.page_count
        else:
            # PDF가 없는 경우 페이지 수는 청크에서 추출한 최대 페이지 번호 + 1
            n_page = max([chunk.metadata.get('page', 0) for chunk in chunks]) + 1

        global_metadata = dict(
            n_chunk_of_doc = len(chunks),
            n_page = n_page,
            reg_date = datetime.now().isoformat(timespec='seconds') + 'Z'
        )

        current_page = None
        chunk_index_on_page = 0
        vectors = []
        for chunk_idx, chunk in enumerate(chunks):
            page = chunk.metadata.get('page', 0)  # 페이지 정보가 없으면 0으로 설정
            text = chunk.page_content

            if page != current_page:
                current_page = page
                chunk_index_on_page = 0

            bboxes = None
            if has_pdf and page < n_page:  # PDF가 있고 유효한 페이지 번호인 경우
                try:
                    fitz_page = doc.load_page(page)
                    bboxes = json.dumps([{
                        'p1': { 'x': rect[0]/fitz_page.rect.width, 'y': rect[1]/fitz_page.rect.height },
                        'p2': { 'x': rect[2]/fitz_page.rect.width, 'y': rect[3]/fitz_page.rect.height },
                    } for rect in fitz_page.search_for(text)])
                except Exception as e:
                    print(f"Error processing PDF page {page}: {e}")
                    bboxes = json.dumps([])

            vector_data = {
                'text': text,
                'n_chars': len(text),
                'n_words': len(text.split()),
                'n_lines': len(text.splitlines()),
                'i_page': page,
                'i_chunk_on_page': chunk_index_on_page,
                #'n_chunk_of_page': self.page_chunk_counts[page],
                'i_chunk_on_doc': chunk_idx,
                **doc.metadata,
                **global_metadata
            }
            
            if bboxes:
                vector_data['bboxes'] = bboxes

            vectors.append(vector_data)
            chunk_index_on_page += 1

        return vectors
    
    
    
        # 법률 문서 처리를 위한 메서드들
    def get_effective_status(self, is_effective: int) -> str:
        return {0: "현행", 1: "예정", -1: "연혁"}.get(is_effective, str(is_effective))
        
    def extract_parent_from_text(self, text: str) -> dict:
        parent = {}
        circled_number_map = {
            '①': '1', '②': '2', '③': '3', '④': '4', '⑤': '5',
            '⑥': '6', '⑦': '7', '⑧': '8', '⑨': '9', '⑩': '10',
            '⑪': '11', '⑫': '12', '⑬': '13', '⑭': '14', '⑮': '15',
            '⑯': '16', '⑰': '17', '⑱': '18', '⑲': '19', '⑳': '20',
        }

        lines = text.splitlines()

        for line in lines:
            if "항" not in parent and line and line[0] in circled_number_map:
                parent["항"] = circled_number_map[line[0]]
            elif "호" not in parent and re.match(r"^(\d+)\.\s", line):
                parent["호"] = re.match(r"^(\d+)\.", line).group(1)
            elif "목" not in parent and re.match(r"^[가-힣]\.", line):
                parent["목"] = re.match(r"^([가-힣])\.", line).group(1)
            elif "세목" not in parent and re.match(r"^\d+\)", line):
                parent["세목"] = re.match(r"^(\d+)\)", line).group(1)
            elif "세세목" not in parent and re.match(r"^[가-힣]\)", line):
                parent["세세목"] = re.match(r"^([가-힣])\)", line).group(1)
            elif "세목" not in parent and re.match(r"^\(\d+\)", line):
                parent["세목"] = re.match(r"^\((\d+)\)", line).group(1)

        return parent
        
    def reset_lower_levels(self, parent_memory: dict, new_parent: dict) -> dict:
        LEVELS = ["항", "호", "목", "세목", "세세목"]
        for level in LEVELS:
            if level in new_parent:
                level_idx = LEVELS.index(level)
                # keep levels above or equal to current
                return {k: v for k, v in parent_memory.items() if LEVELS.index(k) < level_idx}
        return parent_memory.copy()
    
    def track_parent_hierarchy(self, previous_memory: dict, new_detected: dict) -> dict:
        LEVELS = ["항", "호", "목", "세목", "세세목"]
        updated_memory = previous_memory.copy()

        for level in LEVELS:
            if level in new_detected:
                level_index = LEVELS.index(level)
                # 해당 레벨보다 낮은 것 모두 제거
                for lower in LEVELS[level_index + 1:]:
                    updated_memory.pop(lower, None)
                updated_memory[level] = new_detected[level]

        return updated_memory
    
    def track_parent_across_chunks(self, chunks, current_chunk_index):
        """청크 간 연속성을 고려하여 parent 계층 구조를 추적합니다."""
        current_chunk = chunks[current_chunk_index]
        first_line = current_chunk["text"].strip().splitlines()[0]
        
        # 현재 청크의 첫 줄에서 계층 정보 추출
        detected_parent = self.extract_parent_from_text(first_line)
        
        # 이전 청크가 없으면 현재 감지된 정보만 반환
        if current_chunk_index == 0:
            return detected_parent
        
        # 이전 청크의 article_id가 같은지 확인
        prev_chunk = chunks[current_chunk_index - 1]
        if prev_chunk["article_id"] != current_chunk["article_id"]:
            return detected_parent
        
        # 이전 청크의 parent 정보 가져오기
        prev_parent = prev_chunk["parent"]
        
        # 계층 순서 정의
        LEVELS = ["항", "호", "목", "세목", "세세목"]
        
        # 현재 감지된 최상위 계층 찾기
        current_highest_level = None
        for level in LEVELS:
            if level in detected_parent:
                current_highest_level = level
                break
        
        if not current_highest_level:
            return prev_parent
        
        # 결과 parent 초기화
        result_parent = {}
        current_level_index = LEVELS.index(current_highest_level)
        
        # 상위 계층 복사
        for level in LEVELS[:current_level_index]:
            if level in prev_parent:
                result_parent[level] = prev_parent[level]
        
        # 현재 계층 추가
        result_parent[current_highest_level] = detected_parent[current_highest_level]
        
        return result_parent

    # self.create_hierarchical_text(law_name, None, None, None, "부칙", split_text)    
    def create_hierarchical_text(self, name: str, chapter_title: str,  
                                    section_title: str, article_title: str, 
                                    content: str, parent: Dict = None) -> str:
            parts = []
            if name:
                parts.append(f"{name}")
            if chapter_title:
                parts.append(f"{chapter_title}")
            if section_title:
                parts.append(f"{section_title}")
            if article_title:
                parts.append(f"{article_title.strip()}")

            if parent:
                for level in ["항", "호", "목", "세목", "세세목"]:
                    if level in parent:
                        parts.append(f"{level} {parent[level]}")
            
            header = " > ".join(parts)
            return f"{header}\n{content}"
    
    def generate_hierarchy_path(self, chapter_num, chapter_title, section_num, 
                               section_title, article_title, parent):
        path_parts = []
        if chapter_num:
            path_parts.append(f"{chapter_num} {chapter_title}".strip())
        if section_num:
            path_parts.append(f"{section_num} {section_title}".strip())
        if article_title:
            path_parts.append(article_title.strip())
        for level in ["항", "호", "목", "세목", "세세목"]:
            if parent and level in parent:
                path_parts.append(f"{level} {parent[level]}")
        return "/".join(path_parts)

        # 법령일 때 
    def process_law_document(self, input_path: str, output_dir: str):
        input_path = Path(input_path)
        output_dir = Path(output_dir)

        with open(input_path, encoding="utf-8") as f:
            data = json.load(f)

        metadata = data["law"]["metadata"]

        if "law_name" in metadata:
            law_name = metadata.get("law_name", "")
            law_id = metadata.get("law_id", "")
            law_short_name = metadata.get("law_short_name", "")
            law_field = metadata.get("law_field", "")
            announce_date = metadata.get("announce_date", "")
            enforce_date = metadata.get("enforce_date", "")
            is_effective = metadata.get("is_effective", 0)
            law_type_code = metadata.get("law_type_code", "")
            chapter = metadata.get("chapter", "")
            con_laws = metadata.get("con_laws", [])
            dept = metadata.get("dept", "")
            enact_date = metadata.get("enact_date", "")

            # hier_laws 구조 정리
            raw_hier_laws = metadata.get("hier_laws", [])
            hier_laws = []
            for law in raw_hier_laws:
                # 구조가 dict인지 확인
                if isinstance(law, dict):
                    hier_laws.append({
                        "law_id": law.get("law_id"),
                        "law_num": law.get("law_num"),
                        "law_code": law.get("law_code"),
                        "law_type": law.get("law_type"),
                        "law_name": law.get("law_name"),
                        "parent_law_id": law.get("parent_law_id"),
                    })
                else:
                    # 이전 리스트 구조 대응 (호환성 유지용)
                    try:
                        law_id, law_num, law_code, law_type, law_name, parent_id = law
                        hier_laws.append({
                            "law_id": law_id,
                            "law_num": law_num,
                            "law_code": law_code,
                            "law_type": law_type,
                            "law_name": law_name,
                            "parent_law_id": parent_id,
                        })
                    except Exception as e:
                        print(f"Invalid hier_laws entry skipped: {law} ({e})")

            chunk = {
                "law_name": law_name,
                "law_id": law_id,
                "law_short_name": law_short_name,
                "law_type_code": law_type_code,
                "law_field": law_field,
                "type": "법령",
                "chapter": chapter,
                "hier_laws": hier_laws,
                "con_laws": con_laws,
                "dept": dept,
                "announce_date": announce_date,
                "enforce_date": enforce_date,
                "enact_date": enact_date,
                "is_effective": self.get_effective_status(is_effective),
            }
        # 행정규칙일 때 
        if "admrule_id" in metadata:
            admrule_id = metadata.get("admrule_id", "")
            admrule_num = metadata.get("admrule_num", "")
            announce_num = metadata.get("announce_num", "")
            announce_date = metadata.get("announce_date", "")
            enforce_date = metadata.get("enforce_date", "")
            rule_name = metadata.get("rule_name", "")
            rule_type = metadata.get("rule_type", "")
            article_form = metadata.get("article_form", False)
            is_effective = metadata.get("is_effective", 0)
            hier_laws = metadata.get("hier_laws", {})
            con_laws = metadata.get("con_laws", [])
            addenda = metadata.get("addenda", [])
            appendices = metadata.get("appendices", [])
            dept = metadata.get("dept", "")
            enact_date = metadata.get("enact_date", "")

            chunk = {
                "rule_name": rule_name,
                "admrule_id": admrule_id,
                "admrule_num": admrule_num,
                "announce_num": announce_num,
                "announce_date": announce_date,
                "enforce_date": enforce_date,
                "type": "행정규칙",
                "rule_type": rule_type,
                "article_form": article_form,
                "is_effective": self.get_effective_status(is_effective),
                "hier_laws": hier_laws,
                "con_laws": con_laws,
                "addenda": addenda,
                "appendices": appendices,
                "dept": dept,
                "enact_date": enact_date,
            }

        documents = []
        documents.append(chunk)

        splitter = RecursiveCharacterTextSplitter(
            chunk_size=500,
            chunk_overlap=50,
            length_function=len,
            separators=["\n\n", "\n", " ", ""]
        )

        current_chapter_number = ""
        current_chapter_title = ""
        current_section_number = ""
        current_section_title = ""

        for article in data.get("article", []):
            meta = article["metadata"]
            raw_content = article.get("content", [])
            if not raw_content:
                continue

            current_parent_memory = {}

            # is_preamble = meta.get("is_preamable", False)
            article_title = meta.get("article_title", "")
            article_num = meta.get("article_num", "")
            article_id = meta.get("article_id", "")

            chapter_info = meta.get("article_chapter", {})
            chapter_num = chapter_info.get("chapter_num")
            section_num = chapter_info.get("section_num")
            chapter_title = chapter_info.get("chapter_title", "")
            section_title = chapter_info.get("section_title", " ")

            content = "\n".join(raw_content)
            split_texts = splitter.split_text(content)

            for i, chunk_text in enumerate(split_texts):
                #detected_parent = self.extract_parent_from_text(chunk_text)
                # first_line = chunk_text.strip().splitlines()[0] # 첫 문장으로만 판단 
                # detected_parent = self.extract_parent_from_text(first_line)
                # current_parent_memory = self.reset_lower_levels(current_parent_memory, detected_parent)
                # current_parent_memory.update(detected_parent)
                first_line = chunk_text.strip().splitlines()[0]
                detected_parent = self.extract_parent_from_text(first_line)
                current_parent_memory = self.track_parent_hierarchy(current_parent_memory, detected_parent)


                chunk = {
                    "law_name": law_name,
                    "article_title": article_title,
                    "text": self.create_hierarchical_text(
                        law_name,
                        chapter_title,
                        section_title,
                        article_title,
                        chunk_text,
                        parent=current_parent_memory
                    ),
                    #"original_text": chunk_text,
                    "hierarchy_path": self.generate_hierarchy_path(
                        current_chapter_number,
                        current_chapter_title,
                        current_section_number,
                        current_section_title,
                        article_title,
                        current_parent_memory
                    ),
                    "parent": current_parent_memory.copy(),
                    "type": "조문",
                    "article_id": article_id,
                    "article_num": article_num,
                    "law_id": law_id,
                    "law_short_name": law_short_name,
                    "announce_date": meta.get("announce_date", announce_date),
                    "enforce_date": meta.get("enforce_date", enforce_date),
                    "i_chunk_on_article": i,
                    "n_chunk_of_article": len(split_texts),
                    "related_appendices": meta.get("appendices", []),
                    "related_addenda": meta.get("addenda", []),
                    "is_effective": self.get_effective_status(meta.get("is_effective", is_effective)),
                    "chapter_number": chapter_num,
                    "section_number": section_num,
                    "chapter_title": current_chapter_title,
                }
                documents.append(chunk)

        # 부칙 처리
        for addendum in data.get("addendum", []):
            raw_content = addendum.get("content", "")
            content = "\n".join(raw_content) if isinstance(raw_content, list) else str(raw_content)

            meta = addendum.get("metadata", {})
            aid = meta.get("addendum_id", "")
            num = meta.get("addendum_num", "")

            split_texts = splitter.split_text(content)
            for i, split_text in enumerate(split_texts):
                doc = {
                    "law_name": law_name,
                    "text": self.create_hierarchical_text(law_name, None, None,"부칙",content=split_text,parent=None),
                    "hierarchy_path": f"부칙/{aid}",
                    "type": "부칙",
                    "law_id": law_id,
                    "law_short_name": law_short_name,
                    "addendum_id": aid,
                    "addendum_num": num,
                    "announce_date": meta.get("announce_date", ""),
                    "related_articles": meta.get("articles", []),
                    "i_chunk_on_addendum": i,
                    "n_chunk_of_addendum": len(split_texts),
                }
                documents.append(doc)

        # 별표 처리
        for appendix in data.get("appendix", []):
            meta = appendix.get("metadata", {})
            aid = meta.get("appendix_id", "")
            num = meta.get("appendix_num", "")
            title = meta.get("appendix_title", "")

            doc = {
                "law_name": law_name,
                "appendix_title": title,
                #"text": self.create_hierarchical_text(law_name, None, None, None, None, f"별표 {num} {title}", ""),
                "text": self.create_hierarchical_text(law_name, None, None, f"별표 {num} {title}",content="",parent=None),
                "hierarchy_path": f"별표/{aid} {title}",
                "type": "별표",
                "law_id": law_id,
                "law_short_name": law_short_name,
                "related_articles": meta.get("articles", []),
                "appendix_id": aid,
                "appendix_num": num,
                "announce_date": meta.get("announce_date", ""),
                "enforce_date": meta.get("enforce_date", ""),
                "file_link": meta.get("appendix_link", ""),
                "is_effective": self.get_effective_status(meta.get("is_effective", "")),
            }
            documents.append(doc)

        output_dir.mkdir(parents=True, exist_ok=True)
        output_file = output_dir / f"{law_id}_r_langchain_chunks.json"
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(documents, f, ensure_ascii=False, indent=2)
        
        return documents