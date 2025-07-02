import json
from pathlib import Path
from typing import Optional, Union, cast
from PIL import Image, UnidentifiedImageError

from docling_core.types.doc import (
    DocItemLabel,
    DoclingDocument,
    DocumentOrigin,
    TableCell,
    TableData,
    ProvenanceItem,
    ImageRef,
    ImageRefMode,
    Size,
    BoundingBox,
    GroupLabel,
)

from docling_core.types.doc.document import ContentLayer, DoclingDocument
from docling.backend.abstract_backend import DeclarativeDocumentBackend
from docling.datamodel.base_models import InputFormat
from docling.datamodel.document import InputDocument


class BOKJsonDocumentBackend(DeclarativeDocumentBackend):
    def __init__(self, in_doc: InputDocument, path_or_stream: Union[str, Path]):
        super().__init__(in_doc, path_or_stream)
        self.json_data = None
        
        # path_or_stream이 Path 객체이거나 string 경로인 경우
        if isinstance(path_or_stream, (Path, str)):
            file_path = Path(path_or_stream) if isinstance(path_or_stream, str) else path_or_stream
            
            try:
                with open(file_path, encoding="utf-8") as f:
                    self.json_data = json.load(f)
                    
            except (FileNotFoundError, json.JSONDecodeError) as e:
                
                self.json_data = None
        # file stream인 경우
        elif hasattr(path_or_stream, 'read'):
            try:
                # 스트림이 이미 읽힌 상태일 수 있으므로 처음으로 되돌림
                if hasattr(path_or_stream, 'seek'):
                    path_or_stream.seek(0)
                
                content = path_or_stream.read()
                
                if not content:
                    self.json_data = None
                    return
                    
                if isinstance(content, bytes):
                    content = content.decode('utf-8')
                self.json_data = json.loads(content)
            except json.JSONDecodeError as e:
                self.json_data = None
        else:
            self.json_data = None

    def is_valid(self) -> bool:
        return self.json_data is not None and "body" in self.json_data

    @classmethod
    def supports_pagination(cls) -> bool:
        return True

    @classmethod
    def supported_formats(cls) -> set[InputFormat]:
        return {InputFormat.JSON_DOCLING}  # ensure enum is extended in your setup

    def unload(self):
        self.path_or_stream = None

    def _process_image(self, image_path: str, doc: DoclingDocument, page_no: int) -> bool:
        """
        이미지를 처리하여 DoclingDocument의 pictures에 추가합니다.
        여러 확장자를 시도하고 경로 처리를 개선합니다.
        """
        if not image_path:
            return False
            
        # 이미지 경로 처리 - 상대/절대 경로 고려
        image_path_obj = Path(image_path)
        
        # 이미지 로드 시도할 경로들 생성
        paths_to_try = [image_path]
        
        # 확장자가 없는 경우 여러 확장자 시도
        if not image_path_obj.suffix:
            base_path = str(image_path_obj)
            for ext in (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".wmf", ".tif"):
                paths_to_try.append(f"{base_path}{ext}")
        
        # 상대 경로인 경우 JSON 파일 디렉토리 기준으로도 시도
        if not image_path_obj.is_absolute() and self.file and self.file.parent:
            json_dir = self.file.parent
            relative_to_json = json_dir / image_path_obj
            paths_to_try.append(str(relative_to_json))
            
            if not image_path_obj.suffix:
                for ext in (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".wmf", ".tif"):
                    paths_to_try.append(str(relative_to_json.with_suffix(ext)))

        # 이미지 로드 시도
        pil_image = None
        successful_path = None
        
        for path in paths_to_try:
            try:
                pil_image = Image.open(path)
                successful_path = path
                break
            except (UnidentifiedImageError, OSError, FileNotFoundError):
                continue
        
        # 이미지 로드 실패 시 처리 중단
        if pil_image is None:
            return False
        
        try:
            img_ref_obj = ImageRef.from_pil(image=pil_image, dpi=72)
            img_ref_obj.mode = ImageRefMode.EMBEDDED
            
            doc.add_picture(
                image=img_ref_obj,
                content_layer=ContentLayer.BODY,
                prov=ProvenanceItem(
                    page_no=page_no,
                    bbox=BoundingBox(l=0, t=0, r=1, b=1),
                    charspan=(0, 0)
                ),
            )
            return True
        except Exception:
            return False

    def convert(self) -> DoclingDocument:
        origin = DocumentOrigin(
            filename=self.file.name if self.file else "from_json",
            mimetype="application/json",
            binary_hash=self.document_hash,
        )

        doc = DoclingDocument(name=self.file.stem if self.file else "json_doc", origin=origin)

        # 중복 방지를 위한 컨텐츠 추적
        processed_content = set()

        for page in self.json_data.get("body", []):
            page_no = page.get("page", 1)  # 기본값을 1로 변경
            
            # 페이지가 없으면 생성
            if page_no not in doc.pages:
                doc.pages[page_no] = doc.add_page(page_no=page_no, size=Size(width=595, height=842))

            # 페이지 내 모든 컨텐츠를 처리
            self._process_page_contents_unique(doc, page_no, page.get("contents", []), processed_content)

        return doc

    def _process_page_contents_unique(self, doc: DoclingDocument, page_no: int, contents: list, processed_content: set):
        """페이지 내 모든 컨텐츠를 중복 없이 처리"""
        for idx, block in enumerate(contents):
            content_type = block.get("type")
            
            # 컨텐츠 고유 식별자 생성
            content_id = self._get_content_id(block, page_no, idx)
            if content_id in processed_content:
                continue
            processed_content.add(content_id)
            
            if content_type == "text":
                text_content = block.get("content", "")
                if text_content is not None:
                    text_content = str(text_content)
                    doc.add_text(
                        label=DocItemLabel.TEXT,
                        text=text_content,
                        content_layer=ContentLayer.BODY,
                        prov=ProvenanceItem(
                            page_no=page_no,
                            bbox=BoundingBox(l=0, t=0, r=1, b=1),
                            charspan=(0, len(text_content))
                        ),
                    )
                    
            elif content_type == "image" or content_type == "picture":
                image_path = block.get("content")
                if image_path:
                    # 이미지 처리
                    self._process_image(image_path, doc, page_no)
                    
            elif content_type == "table":
                table_content = block.get("content", [])
                if table_content:
                    # 중첩 테이블에서 실제 데이터 테이블을 찾아서 처리
                    self._process_table_with_nested_extraction(doc, page_no, table_content, processed_content)

    def _process_table_with_nested_extraction(self, doc: DoclingDocument, page_no: int, table_content: list, processed_content: set):
        """테이블을 처리하되 reading order를 유지하면서 중첩된 실제 데이터 테이블을 올바른 순서로 처리"""
        
        # 1. 먼저 테이블 맵을 생성하여 모든 중첩 테이블의 위치와 정보를 파악
        table_map = self._create_table_map(table_content)
        
        # 2. 테이블이 풀어져야 하는 상황인지 확인 (테이블 맵 정보 활용)
        should_flatten = self._should_flatten_table_with_map(table_content, table_map)
        
        if should_flatten:
            # 테이블을 컬럼별 reading order로 개별 요소들을 순서대로 DoclingDocument에 추가
            # 테이블 맵을 활용하여 중첩된 테이블들도 올바른 순서로 처리
            self._add_table_elements_in_column_order_with_map(doc, page_no, table_content, processed_content, table_map)
        else:
            # 풀어지지 않는 경우 - 현재 테이블 자체가 leaf 테이블
            # 이 경우 전체 테이블을 하나의 테이블로 처리
            if self._is_data_table(table_content):
                table_data = self._convert_to_table_data(table_content)
                if table_data:
                    # 테이블의 고유 ID 생성
                    table_id = self._get_table_fingerprint(table_content)
                    if table_id not in processed_content:
                        processed_content.add(table_id)
                        
                        doc.add_table(
                            data=table_data,
                            content_layer=ContentLayer.BODY,
                            prov=ProvenanceItem(
                                page_no=page_no,
                                bbox=BoundingBox(l=0, t=0, r=1, b=1),
                                charspan=(0, 0)
                            ),
                        )
            else:
                # 데이터 테이블이 아닌 경우 개별 텍스트로 처리
                self._add_table_elements_in_column_order(doc, page_no, table_content, processed_content)
    
    def _create_table_map(self, table_content: list) -> dict:
        """테이블 내의 모든 중첩 테이블의 위치와 정보를 매핑"""
        table_map = {}
        
        for row_idx, row in enumerate(table_content):
            if isinstance(row, dict) and "cells" in row:
                for cell_idx, cell in enumerate(row["cells"]):
                    if isinstance(cell, dict):
                        contents = cell.get("contents", [])
                        for content_idx, content in enumerate(contents):
                            if isinstance(content, dict) and content.get("type") == "table":
                                nested_table_content = content.get("content", [])
                                if nested_table_content:
                                    # 테이블 ID 생성 (원본 JSON의 id 활용 가능)
                                    table_id = content.get("id", f"table_{row_idx}_{cell_idx}_{content_idx}")
                                    
                                    is_data = self._is_data_table(nested_table_content)
                                    
                                    table_map[table_id] = {
                                        'content': nested_table_content,
                                        'position': (row_idx, cell_idx, content_idx),
                                        'is_data_table': is_data,
                                        'original_id': content.get("id"),
                                        'fingerprint': self._get_table_fingerprint(nested_table_content)
                                    }
                                    
                                    # 재귀적으로 더 깊은 중첩 테이블도 확인
                                    deeper_map = self._create_table_map(nested_table_content)
                                    table_map.update(deeper_map)
        
        return table_map
    
    def _should_flatten_table_with_map(self, table_content: list, table_map: dict) -> bool:
        """테이블 맵을 활용하여 테이블을 풀어야 하는지 판단"""
        # 1. 이미지가 있는 경우 - 무조건 풀어야 함
        if self._has_images_in_table(table_content):
            return True
        
        # 2. 중첩 테이블이 있는 경우만 처리
        if len(table_map) > 0:
            # 모든 중첩 테이블이 leaf 테이블인지 확인
            all_nested_are_leaf = True
            for table_info in table_map.values():
                nested_content = table_info['content']
                # 중첩된 테이블이 또 다른 테이블을 포함하거나 데이터 테이블이 아니면
                if self._has_nested_tables(nested_content) or not self._is_data_table(nested_content):
                    all_nested_are_leaf = False
                    break
            
            # 모든 중첩 테이블이 leaf면 외부 테이블만 풀어야 함
            if all_nested_are_leaf:
                return True
            else:
                # 일부 중첩 테이블이 복잡하면 전체를 풀어야 함
                return True
        
        # 3. 중첩 테이블이나 이미지가 없는 단순한 테이블은 보존
        return False
    
    def _add_table_elements_in_column_order_with_map(self, doc: DoclingDocument, page_no: int, table_content: list, processed_content: set, table_map: dict):
        """테이블 맵을 활용하여 컬럼별 reading order로 요소들을 순서대로 DoclingDocument에 추가"""
        if not table_content:
            return

        # 이미지 때문에 테이블을 풀어내는 경우, 먼저 모든 이미지를 Picture로 추가
        if self._has_images_in_table(table_content):
            self._add_pictures_from_table(doc, page_no, table_content)

        cell_matrix = {}
        max_rows = len(table_content)
        max_cols = 0
        
        for row_idx, row in enumerate(table_content):
            if isinstance(row, dict) and "cells" in row:
                current_col = 0
                for cell_idx, cell_data in enumerate(row["cells"]):
                    if isinstance(cell_data, dict):
                        while (row_idx, current_col) in cell_matrix:
                            current_col += 1
                        
                        row_span = cell_data.get("rowSpan", 1)
                        col_span = cell_data.get("colSpan", 1)
                        
                        for r_offset in range(row_span):
                            for c_offset in range(col_span):
                                r, c = row_idx + r_offset, current_col + c_offset
                                if r < max_rows:
                                   cell_matrix[(r, c)] = {
                                        'cell_data': cell_data,
                                        'is_origin': (r_offset == 0 and c_offset == 0),
                                        'origin_pos': (row_idx, current_col)
                                    }
                        current_col += col_span
                        max_cols = max(max_cols, current_col)
        
        added_content_ids_in_current_table_processing = set()

        # 컬럼 순서를 유지하면서 셀별로 개별 그룹 생성
        for col in range(max_cols):
            for row in range(max_rows):
                if (row, col) in cell_matrix:
                    cell_info = cell_matrix[(row, col)]
                    if cell_info['is_origin']:
                        cell_pos = f"r{cell_info['origin_pos'][0]}_c{cell_info['origin_pos'][1]}"
                        
                        # 셀별 개별 그룹 생성
                        cell_group = doc.add_group(
                            name=f"cell_page_{page_no}_{cell_pos}",
                            label=GroupLabel.UNSPECIFIED,
                            content_layer=ContentLayer.BODY
                        )
                        
                        # 셀별 그룹을 parent로 전달
                        self._add_cell_contents_to_doc_with_map(doc, page_no, cell_info['cell_data'], 
                                                       processed_content, added_content_ids_in_current_table_processing,
                                                       cell_pos, cell_group, table_map)
    
    def _extract_nested_data_tables(self, table_content: list):
        """중첩된 테이블 중에서 실제 데이터 테이블만 추출"""
        data_tables = []
        
        for row in table_content:
            if isinstance(row, dict) and "cells" in row:
                for cell in row["cells"]:
                    if isinstance(cell, dict):
                        contents = cell.get("contents", [])
                        for content in contents:
                            if isinstance(content, dict) and content.get("type") == "table":
                                nested_table_content = content.get("content", [])
                                if nested_table_content:

                                    
                                    is_data = self._is_data_table(nested_table_content)
                                    if is_data:

                                        
                                        data_tables.append(nested_table_content)
                                        # 재귀적으로 더 깊은 중첩 테이블도 확인
                                        deeper_tables = self._extract_nested_data_tables(nested_table_content)
                                        data_tables.extend(deeper_tables)
        

        
        return data_tables
    
    def _is_data_table(self, table_content: list):
        """테이블이 실제 데이터 테이블인지 판단 (완화된 기준)"""
        if not table_content:
            return False
        
        # 구조적 특징 확인
        num_rows = len(table_content)
        max_cols = 0
        for row in table_content:
            if isinstance(row, dict) and "cells" in row:
                max_cols = max(max_cols, len(row["cells"]))
        
        # 테이블 태그가 있으면 기본적으로 테이블로 인정
        # 최소 구조 조건: 1행 이상, 1열 이상 (완화된 조건)
        result = num_rows >= 1 and max_cols >= 1
        
        return result
    
    def _process_table_remaining_content_with_order(self, doc: DoclingDocument, page_no: int, table_content: list, processed_content: set, extracted_table_ids: set):
        """테이블에서 중첩 데이터 테이블을 제외한 나머지 콘텐츠를 reading order로 처리"""
        
        # 테이블이 풀어져야 하는 상황인지 확인
        should_flatten = self._should_flatten_table(table_content)
        
        if should_flatten:
            # 테이블을 컬럼별 reading order로 개별 요소들을 순서대로 DoclingDocument에 추가
            self._add_table_elements_in_column_order(doc, page_no, table_content, processed_content)
        else:
            # 기존 방식으로 처리 (개별 텍스트와 이미지로, 단 processed_content를 사용하여 중복 방지)
            for row_idx, row in enumerate(table_content):
                if isinstance(row, dict) and "cells" in row:
                    for cell_idx, cell in enumerate(row["cells"]):
                        if isinstance(cell, dict):
                            contents = cell.get("contents", [])
                            for content_idx, content_block in enumerate(contents):
                                if isinstance(content_block, dict):
                                    # 이 콘텐츠 블록에 대한 고유 ID 생성
                                    # processed_content에 이미 있다면 건너뜀
                                    # 주의: _get_content_id의 인자가 block, page_no, idx 순서임
                                    # 여기서는 content_block, page_no, 그리고 (row_idx, cell_idx, content_idx) 같은 복합 인덱스 필요
                                    # 단순화를 위해 여기서는 중복 처리를 processed_content에 의존하지 않고
                                    # _add_table_elements_in_column_order 에서만 중복을 처리하도록 가정
                                    # 또는 _get_content_id를 사용하지 않고 각 블록을 직접 처리

                                    content_type = content_block.get("type")
                                    
                                    # 여기서 table type은 _extract_nested_data_tables에서 이미 처리되었으므로 건너뛰어야 함
                                    if content_type == "table":
                                        # 중첩 테이블이 이미 추출되었는지 확인
                                        nested_table_content = content_block.get("content", [])
                                        if nested_table_content:
                                            table_id = self._get_table_fingerprint(nested_table_content)
                                            if table_id in extracted_table_ids:
                                                continue  # 이미 처리된 테이블이므로 건너뜀
                                        continue

                                    if content_type == "text":
                                        text = content_block.get("content", "")
                                        if text and str(text).strip():
                                            text_content = str(text)
                                            doc.add_text(
                                                label=DocItemLabel.TEXT,
                                                text=text_content,
                                                content_layer=ContentLayer.BODY,
                                                prov=ProvenanceItem(
                                                    page_no=page_no,
                                                    bbox=BoundingBox(l=0, t=0, r=1, b=1), # Bbox는 예시입니다.
                                                    charspan=(0, len(text_content))
                                                ),
                                            )
                                    
                                    elif content_type == "image" or content_type == "picture":
                                        image_path = content_block.get("content", "")
                                        if image_path:
                                            # hwpx_backend 스타일의 이미지 처리 사용
                                            self._process_image(image_path, doc, page_no)
    
    def _add_table_elements_in_column_order(self, doc: DoclingDocument, page_no: int, table_content: list, processed_content: set):
        """테이블을 컬럼별 reading order로 개별 요소들을 순서대로 DoclingDocument에 추가하고, 중복을 방지한다."""
        if not table_content:
            return
        
        # 이미지 때문에 테이블을 풀어내는 경우, 먼저 모든 이미지를 Picture로 추가
        if self._has_images_in_table(table_content):
            self._add_pictures_from_table(doc, page_no, table_content)
        
        cell_matrix = {}
        max_rows = len(table_content)
        max_cols = 0
        
        for row_idx, row in enumerate(table_content):
            if isinstance(row, dict) and "cells" in row:
                current_col = 0
                for cell_idx, cell_data in enumerate(row["cells"]):
                    if isinstance(cell_data, dict):
                        while (row_idx, current_col) in cell_matrix:
                            current_col += 1
                        
                        row_span = cell_data.get("rowSpan", 1)
                        col_span = cell_data.get("colSpan", 1)
                        
                        # 셀의 각 콘텐츠 블록에 대한 고유 식별자 생성 및 중복 확인을 위해
                        # 셀 데이터와 함께 (row_idx, cell_idx) 같은 위치 정보도 필요할 수 있음.
                        # 여기서는 셀 자체를 저장하고, _add_cell_contents_to_doc에서 처리하도록 함
                        
                        for r_offset in range(row_span):
                            for c_offset in range(col_span):
                                r, c = row_idx + r_offset, current_col + c_offset
                                if r < max_rows : # row_idx + row_span이 max_rows를 넘을 수 있으므로 체크
                                   cell_matrix[(r, c)] = {
                                        'cell_data': cell_data, # 셀 전체 데이터 저장
                                        'is_origin': (r_offset == 0 and c_offset == 0),
                                        'origin_pos': (row_idx, current_col) # 원본 셀의 시작 위치
                                    }
                        current_col += col_span
                        max_cols = max(max_cols, current_col)
        
        added_content_ids_in_current_table_processing = set()

        # 컬럼 순서를 유지하면서 셀별로 개별 그룹 생성
        for col in range(max_cols):
            for row in range(max_rows):
                if (row, col) in cell_matrix:
                    cell_info = cell_matrix[(row, col)]
                    if cell_info['is_origin']:
                        cell_pos = f"r{cell_info['origin_pos'][0]}_c{cell_info['origin_pos'][1]}"
                        
                        # 셀별 개별 그룹 생성
                        cell_group = doc.add_group(
                            name=f"cell_page_{page_no}_{cell_pos}",
                            label=GroupLabel.UNSPECIFIED,
                            content_layer=ContentLayer.BODY
                        )
                        
                        # 셀별 그룹을 parent로 전달
                        self._add_cell_contents_to_doc(doc, page_no, cell_info['cell_data'], 
                                                       processed_content, added_content_ids_in_current_table_processing,
                                                       cell_pos, cell_group)

    def _add_cell_contents_to_doc(self, doc: DoclingDocument, page_no: int, cell_data, 
                                  global_processed_content: set, table_internal_processed_ids: set, cell_base_id: str, parent=None):
        """셀 내용을 원래 순서를 유지하면서 DoclingDocument에 추가 (중복 방지 기능 강화)"""
        contents = cell_data.get("contents", [])
        
        for idx, content_block in enumerate(contents):
            if isinstance(content_block, dict):
                # 각 content_block에 대한 고유 ID 생성
                # _get_content_id를 활용하거나 유사한 방식으로 고유 ID 생성
                # block, page_no, idx(고유한)
                # 여기서는 block, page_no, 그리고 셀 위치와 블록 인덱스를 결합한 고유 ID 사용
                block_unique_id_part = self._get_content_block_fingerprint(content_block) # 텍스트, 이미지 경로 등으로 fingerprint
                content_id = f"pg{page_no}_{cell_base_id}_idx{idx}_{block_unique_id_part}"

                if content_id in global_processed_content or content_id in table_internal_processed_ids:
                    continue 
                
                # 처리된 것으로 기록
                global_processed_content.add(content_id)
                table_internal_processed_ids.add(content_id)

                content_type = content_block.get("type")
                
                if content_type == "text":
                    text = content_block.get("content", "")
                    if text is not None:
                        text_str = str(text).strip()
                        if text_str:

                            
                            doc.add_text(
                                label=DocItemLabel.TEXT,
                                text=text_str,
                                content_layer=ContentLayer.BODY,
                                parent=parent,
                                prov=ProvenanceItem(
                                    page_no=page_no,
                                    bbox=BoundingBox(l=0, t=0, r=1, b=1), # Bbox는 예시
                                    charspan=(0, len(text_str))
                                ),
                            )
                
                elif content_type == "image" or content_type == "picture":
                    image_path = content_block.get("content", "")
                    if image_path:
                        # 이미지 처리
                        self._process_image(image_path, doc, page_no)
                
                elif content_type == "table":
                    nested_table_content = content_block.get("content", [])
                    if nested_table_content:
                        # 테이블 fingerprint 확인하여 이미 추출된 테이블인지 체크
                        table_id = self._get_table_fingerprint(nested_table_content)
                        if table_id in global_processed_content:
                            continue  # 이미 처리된 테이블이므로 건너뜀
                        
                        # 데이터 테이블인지 확인 (중첩 테이블이면서 데이터가 있으면 보존)
                        is_data = self._is_data_table(nested_table_content)
                        has_nested = self._has_nested_tables(nested_table_content)
                        
                        if is_data and not has_nested:
                            # 데이터 테이블이면서 더 이상 중첩이 없는 경우 - 테이블로 보존
                            table_data = self._convert_to_table_data(nested_table_content)
                            if table_data:
                                # 새로 추가하는 테이블도 global_processed_content에 추가
                                global_processed_content.add(table_id)
                                doc.add_table(
                                    data=table_data,
                                    content_layer=ContentLayer.BODY,
                                    parent=parent,
                                    prov=ProvenanceItem(
                                        page_no=page_no,
                                        bbox=BoundingBox(l=0, t=0, r=1, b=1), # Bbox는 예시
                                        charspan=(0, 0)
                                    ),
                                )
                        else:
                            # 데이터 테이블이 아니거나 더 중첩된 테이블이 있는 경우 - 재귀적으로 풀어서 처리
                            # 재귀 호출 시 processed_content 세트를 전달하여 전역 중복 방지
                            self._add_table_elements_in_column_order(doc, page_no, nested_table_content, global_processed_content)
    
    def _add_cell_contents_to_doc_with_map(self, doc: DoclingDocument, page_no: int, cell_data, 
                                  global_processed_content: set, table_internal_processed_ids: set, cell_base_id: str, parent=None, table_map: dict = None):
        """테이블 맵을 활용하여 셀 내용을 원래 순서를 유지하면서 DoclingDocument에 추가 (중복 방지 기능 강화)"""
        contents = cell_data.get("contents", [])
        
        for idx, content_block in enumerate(contents):
            if isinstance(content_block, dict):
                block_id = content_block.get("id")
                
                # 각 content_block에 대한 고유 ID 생성
                block_unique_id_part = self._get_content_block_fingerprint(content_block)
                content_id = f"pg{page_no}_{cell_base_id}_idx{idx}_{block_unique_id_part}"

                # 테이블 맵 기반 처리에서는 테이블 ID를 우선적으로 사용
                if content_block.get("type") == "table" and table_map and block_id in table_map:
                    # 테이블 맵에 있는 테이블은 fingerprint 기반으로 중복 체크
                    table_fingerprint = table_map[block_id]['fingerprint']
                    if table_fingerprint in global_processed_content:
                        continue
                else:
                    # 일반적인 중복 체크
                    if content_id in global_processed_content or content_id in table_internal_processed_ids:
                        continue 
                
                # 처리된 것으로 기록
                global_processed_content.add(content_id)
                table_internal_processed_ids.add(content_id)

                content_type = content_block.get("type")
                
                if content_type == "text":
                    text = content_block.get("content", "")
                    if text is not None:
                        text_str = str(text).strip()
                        if text_str:
                            doc.add_text(
                                label=DocItemLabel.TEXT,
                                text=text_str,
                                content_layer=ContentLayer.BODY,
                                parent=parent,
                                prov=ProvenanceItem(
                                    page_no=page_no,
                                    bbox=BoundingBox(l=0, t=0, r=1, b=1),
                                    charspan=(0, len(text_str))
                                ),
                            )
                
                elif content_type == "image" or content_type == "picture":
                    image_path = content_block.get("content", "")
                    if image_path:
                        # 이미지 처리
                        self._process_image(image_path, doc, page_no)
                                
                elif content_type == "table":
                    nested_table_content = content_block.get("content", [])
                    if nested_table_content:
                        # 테이블 맵에서 해당 테이블 정보 확인
                        table_id = content_block.get("id")
                        table_info = table_map.get(table_id) if table_map and table_id else None
                        
                        # 테이블 fingerprint 확인하여 이미 추출된 테이블인지 체크
                        fingerprint = self._get_table_fingerprint(nested_table_content)
                        if fingerprint in global_processed_content:
                            continue  # 이미 처리된 테이블이므로 건너뜀
                        
                        # 데이터 테이블인지 확인 (중첩 테이블이면서 데이터가 있으면 보존)
                        is_data = self._is_data_table(nested_table_content)
                        has_nested = self._has_nested_tables(nested_table_content)
                        
                        if is_data and not has_nested:
                            # 데이터 테이블이면서 더 이상 중첩이 없는 경우 - 테이블로 보존
                            table_data = self._convert_to_table_data(nested_table_content)
                            if table_data:
                                # 새로 추가하는 테이블도 global_processed_content에 추가
                                global_processed_content.add(fingerprint)
                                doc.add_table(
                                    data=table_data,
                                    content_layer=ContentLayer.BODY,
                                    parent=parent,
                                    prov=ProvenanceItem(
                                        page_no=page_no,
                                        bbox=BoundingBox(l=0, t=0, r=1, b=1),
                                        charspan=(0, 0)
                                    ),
                                )
                        else:
                            # 데이터 테이블이 아니거나 더 중첩된 테이블이 있는 경우 - 재귀적으로 풀어서 처리
                            # 재귀 호출 시 processed_content 세트를 전달하여 전역 중복 방지
                            self._add_table_elements_in_column_order_with_map(doc, page_no, nested_table_content, global_processed_content, table_map)

    def _get_content_block_fingerprint(self, block_data) -> str:
        """블록 데이터로부터 간단한 fingerprint 생성 (해시 충돌 가능성 있음, 예시용)"""
        content_type = block_data.get("type")
        content = block_data.get("content")
        if content_type == "text":
            return f"txt_{hash(str(content)[:50])}" # 첫 50자로 fingerprint
        elif content_type == "image" or content_type == "picture":
            return f"img_{hash(str(content))}"
        elif content_type == "table":
            # 테이블의 경우 내용이 복잡하므로, 간단히 첫 번째 셀의 텍스트나 타입으로 구분
            if content and isinstance(content, list) and len(content) > 0 and \
               isinstance(content[0], dict) and "cells" in content[0] and \
               len(content[0]["cells"]) > 0 and isinstance(content[0]["cells"][0], dict) and \
               "contents" in content[0]["cells"][0] and len(content[0]["cells"][0]["contents"]) > 0 and \
               isinstance(content[0]["cells"][0]["contents"][0], dict) and \
               "content" in content[0]["cells"][0]["contents"][0]:
                return f"tbl_{hash(str(content[0]['cells'][0]['contents'][0]['content'])[:30])}"
            return "tbl_empty"
        return "unknown"

    def _should_flatten_table(self, table_content: list) -> bool:
        """테이블을 풀어야 하는지 판단하는 기준 (원래 로직)"""
        # 1. 이미지가 있는 경우만 풀어냄
        if self._has_images_in_table(table_content):
            return True
        
        # 2. 중첩 테이블이 있고 그 중첩 테이블이 복잡한 경우만 풀어냄
        if self._has_nested_tables(table_content):
            # 중첩 테이블들을 확인하여 데이터 테이블이 아닌 것이 있으면 풀어냄
            for row in table_content:
                if isinstance(row, dict) and "cells" in row:
                    for cell in row["cells"]:
                        if isinstance(cell, dict):
                            contents = cell.get("contents", [])
                            for content_item in contents:
                                if isinstance(content_item, dict) and content_item.get("type") == "table":
                                    nested_content = content_item.get("content", [])
                                    if nested_content and not self._is_data_table(nested_content):
                                        return True
        
        # 3. 단순한 테이블은 보존
        return False
    
    def _has_nested_tables(self, table_content: list) -> bool:
        """테이블에 중첩 테이블(type: table)이 있는지 확인"""
        for row in table_content:
            if isinstance(row, dict) and "cells" in row:
                for cell in row["cells"]:
                    if isinstance(cell, dict):
                        contents = cell.get("contents", [])
                        for content_item in contents:
                            if isinstance(content_item, dict) and content_item.get("type") == "table":
                                return True
        return False

    def _is_leaf_table(self, table_content):
        """테이블이 leaf 테이블인지 확인 (중첩 테이블 없고, 모든 셀이 200자 미만)"""
        for row in table_content:
            if isinstance(row, dict) and "cells" in row:
                for cell in row["cells"]:
                    if isinstance(cell, dict):
                        contents = cell.get("contents", [])
                        
                        # 셀 내 텍스트 길이 체크
                        cell_text_length = 0
                        for content in contents:
                            if isinstance(content, dict):
                                if content.get("type") == "text":
                                    text = content.get("content", "")
                                    if text:
                                        cell_text_length += len(str(text))
                                elif content.get("type") == "table":
                                    # 중첩 테이블 발견
                                    return False
                        
                        # 200자 넘으면 leaf 테이블이 아님
                        if cell_text_length > 200:
                            return False
        return True

    def _convert_to_table_data(self, table_content):
        """테이블 콘텐츠를 TableData로 변환"""
        if not table_content:
            return None
        
        # 행과 열의 수 계산
        num_rows = len(table_content)
        num_cols = 0
        
        # 최대 열 수 계산
        for row in table_content:
            if isinstance(row, dict) and "cells" in row:
                cols_in_row = 0
                for cell in row["cells"]:
                    if isinstance(cell, dict):
                        col_span = cell.get("colSpan", 1)
                        cols_in_row += col_span
                num_cols = max(num_cols, cols_in_row)
        
        if num_rows == 0 or num_cols == 0:
            return None
            
        table_cells = []
        
        for row_idx, row in enumerate(table_content):
            if isinstance(row, dict) and "cells" in row:
                col_idx = 0
                for cell in row["cells"]:
                    if isinstance(cell, dict):
                        # 셀의 내용 추출
                        text_parts = []
                        contents = cell.get("contents", [])
                        for content in contents:
                            if isinstance(content, dict):
                                if content.get("type") == "text":
                                    text = content.get("content", "")
                                    if text is not None:  # None이 아닌 모든 텍스트 포함
                                        text = str(text)
                                        text_parts.append(text)
                                elif content.get("type") == "image" or content.get("type") == "picture":
                                    image_path = content.get("content", "")
                                    if image_path:
                                        text_parts.append(f"[이미지: {image_path}]")
                        
                        cell_text = " ".join(text_parts)
                        
                        # 행과 열 스팬 정보 가져오기
                        row_span = cell.get("rowSpan", 1)
                        col_span = cell.get("colSpan", 1)
                        
                        # TableCell 생성
                        table_cell = TableCell(
                            text=cell_text,
                            row_span=row_span,
                            col_span=col_span,
                            start_row_offset_idx=row_idx,
                            end_row_offset_idx=row_idx + row_span,
                            start_col_offset_idx=col_idx,
                            end_col_offset_idx=col_idx + col_span,
                            column_header=row_idx == 0,  # 첫 번째 행은 헤더로 간주
                            row_header=False,
                        )
                        table_cells.append(table_cell)
                        
                        col_idx += col_span
        
        result = TableData(
            num_rows=num_rows,
            num_cols=num_cols,
            table_cells=table_cells
        )
        

        
        return result

    def _has_images_in_table(self, table_content):
        """테이블에 이미지가 있는지 확인"""
        for row in table_content:
            if isinstance(row, dict) and "cells" in row:
                for cell in row["cells"]:
                    if isinstance(cell, dict):
                        contents = cell.get("contents", [])
                        for content in contents:
                            if isinstance(content, dict):
                                if content.get("type") == "image" or content.get("type") == "picture":
                                    return True
        return False
    
    def _extract_images_from_table(self, table_content):
        """테이블에서 모든 이미지를 추출하여 위치 정보와 함께 반환"""
        images = []
        
        for row_idx, row in enumerate(table_content):
            if isinstance(row, dict) and "cells" in row:
                for cell_idx, cell in enumerate(row["cells"]):
                    if isinstance(cell, dict):
                        contents = cell.get("contents", [])
                        for content_idx, content in enumerate(contents):
                            if isinstance(content, dict) and (content.get("type") == "image" or content.get("type") == "picture"):
                                image_path = content.get("content", "")
                                if image_path:
                                    images.append({
                                        'path': image_path,
                                        'position': (row_idx, cell_idx, content_idx),
                                        'bbox': content.get("bbox", [0, 0, 1, 1])  # 기본 bbox
                                    })
        
        return images
    
    def _add_pictures_from_table(self, doc: DoclingDocument, page_no: int, table_content):
        """테이블 내 모든 이미지를 Picture로 DoclingDocument에 추가"""
        images = self._extract_images_from_table(table_content)
        
        for image_info in images:
            image_path = image_info['path']
            position = image_info['position']
            bbox = image_info['bbox']
            
            # 여러 확장자 시도하여 이미지 로드
            image_path_obj = Path(image_path)
            
            # 이미지 로드 시도할 경로들 생성
            paths_to_try = [image_path]
            
            # 확장자가 없는 경우 여러 확장자 시도
            if not image_path_obj.suffix:
                base_path = str(image_path_obj)
                for ext in (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".wmf", ".tif"):
                    paths_to_try.append(f"{base_path}{ext}")
            
            # 상대 경로인 경우 JSON 파일 디렉토리 기준으로도 시도
            if not image_path_obj.is_absolute() and self.file and self.file.parent:
                json_dir = self.file.parent
                relative_to_json = json_dir / image_path_obj
                paths_to_try.append(str(relative_to_json))
                
                if not image_path_obj.suffix:
                    for ext in (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".wmf", ".tif"):
                        paths_to_try.append(str(relative_to_json.with_suffix(ext)))
            
            # 이미지 로드 시도
            pil_image = None
            for path in paths_to_try:
                try:
                    pil_image = Image.open(path)
                    break
                except (UnidentifiedImageError, OSError, FileNotFoundError):
                    continue
            
            if pil_image is None:
                # 이미지 로드 실패 시 스킵
                continue

            if pil_image:
                img_ref_obj = ImageRef.from_pil(image=pil_image, dpi=72)
                img_ref_obj.mode = ImageRefMode.EMBEDDED
                
                # bbox 설정 (리스트에서 BoundingBox 객체로 변환)
                if isinstance(bbox, list) and len(bbox) >= 4:
                    bbox_obj = BoundingBox(l=bbox[0], t=bbox[1], r=bbox[2], b=bbox[3])
                else:
                    bbox_obj = BoundingBox(l=0, t=0, r=1, b=1)
                
                doc.add_picture(
                    image=img_ref_obj,
                    content_layer=ContentLayer.BODY,
                    prov=ProvenanceItem(
                        page_no=page_no,
                        bbox=bbox_obj,
                        charspan=(0, 0)
                    ),
                )
    
    def _contains_text(self, table_content: list, text: str) -> bool:
        """테이블에 특정 텍스트가 포함되어 있는지 확인"""
        for row in table_content:
            if isinstance(row, dict) and "cells" in row:
                for cell in row["cells"]:
                    if isinstance(cell, dict):
                        contents = cell.get("contents", [])
                        for content in contents:
                            if isinstance(content, dict) and content.get("type") == "text":
                                content_text = content.get("content", "")
                                if text in str(content_text):
                                    return True
        return False

    def _get_content_id(self, block, page_no: int, idx: int):
        """컨텐츠의 고유 식별자 생성 (개선된 버전)"""
        content_type = block.get("type")
        if content_type == "text":
            text = block.get("content", "")[:50]  # 처음 50자
            return f"text_{page_no}_{idx}_{hash(text)}"
        elif content_type == "image" or content_type == "picture":
            image_path = block.get("content", "")
            return f"image_{page_no}_{idx}_{hash(image_path)}"
        elif content_type == "table":
            # 테이블의 경우 page_no와 idx를 활용한 고유 ID 생성
            # fingerprint 방식 대신 위치 기반 고유 ID 사용
            table_content = block.get("content", [])
            table_id = block.get("id", f"table_{idx}")
            return f"table_{page_no}_{idx}_{table_id}_{id(table_content)}"
        else:
            return f"{content_type}_{page_no}_{idx}"

    def _get_table_fingerprint(self, table_content: list) -> str:
        """테이블의 고유 fingerprint 생성 (강화된 버전)"""
        if not table_content:
            return "empty_table"
        
        # 테이블의 전체 구조를 기반으로 fingerprint 생성
        fingerprint_parts = []
        
        # 테이블 크기 정보
        num_rows = len(table_content)
        max_cols = 0
        for row in table_content:
            if isinstance(row, dict) and "cells" in row:
                max_cols = max(max_cols, len(row["cells"]))
        
        fingerprint_parts.append(f"size_{num_rows}x{max_cols}")
        
        # 더 많은 셀 샘플링으로 고유성 확보
        text_samples = []
        
        # 첫 번째 행 전체 샘플링 (헤더 구분용)
        if len(table_content) > 0:
            first_row = table_content[0]
            if isinstance(first_row, dict) and "cells" in first_row:
                for cell_idx, cell in enumerate(first_row["cells"]):
                    if isinstance(cell, dict):
                        contents = cell.get("contents", [])
                        for content in contents:
                            if isinstance(content, dict) and content.get("type") == "text":
                                text = content.get("content", "")
                                if text and str(text).strip():
                                    text_samples.append(f"h_{cell_idx}_{str(text).strip()[:15]}")
                                    break
        
        # 각 행의 첫 번째 셀 샘플링 (행 헤더 구분용)
        for row_idx in range(min(len(table_content), 5)):  # 최대 5행까지
            row = table_content[row_idx]
            if isinstance(row, dict) and "cells" in row and len(row["cells"]) > 0:
                first_cell = row["cells"][0]
                if isinstance(first_cell, dict):
                    contents = first_cell.get("contents", [])
                    for content in contents:
                        if isinstance(content, dict) and content.get("type") == "text":
                            text = content.get("content", "")
                            if text and str(text).strip():
                                text_samples.append(f"r{row_idx}_0_{str(text).strip()[:15]}")
                                break
        
        # 대각선 셀들 추가 샘플링
        diagonal_positions = [(1, 1), (2, 2), (1, 2), (2, 1)]
        for row_idx, col_idx in diagonal_positions:
            if row_idx < len(table_content):
                row = table_content[row_idx]
                if isinstance(row, dict) and "cells" in row and col_idx < len(row["cells"]):
                    cell = row["cells"][col_idx]
                    if isinstance(cell, dict):
                        contents = cell.get("contents", [])
                        for content in contents:
                            if isinstance(content, dict) and content.get("type") == "text":
                                text = content.get("content", "")
                                if text and str(text).strip():
                                    text_samples.append(f"d{row_idx}_{col_idx}_{str(text).strip()[:15]}")
                                    break
        
        # 텍스트 샘플이 있으면 추가
        if text_samples:
            fingerprint_parts.extend(text_samples)
        
        # 메모리 주소를 항상 포함하여 최종 고유성 확보
        fingerprint_parts.append(f"addr_{id(table_content)}")
        
        return f"table_{hash('_'.join(fingerprint_parts))}"
